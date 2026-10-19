"""Persistenza di SdrogoBot su SQLite.

Come funziona
-------------
* Il bot lavora sempre sui dizionari in memoria (USER_DATA, USER_INVENTORIES, ...): le letture sono istantanee.
* Ogni 3 secondi `flush_db` confronta lo stato con quello già salvato e scrive SOLO le righe cambiate, in UNA transazione
  atomica, in un thread dedicato: l'event loop di Python non si ferma mai e nessun file viene riscritto in blocco.
* Anche le partite in corso (Poker, Blackjack, Wordle, Mastermind, Gratta e Vinci) sono salvate nella stessa transazione
  del saldo: se il container si riavvia, al boot le mani riprendono da dove erano. Per i giochi che non si possono
  riprendere (Aviator, Heist, Quiz, duelli) viene salvato un "buono di rimborso" e la puntata torna al giocatore.
* Il file del database è il backup: ogni ora (e allo spegnimento) una copia coerente viene inviata e fissata nella
  chat di backup; se il container riparte senza disco (database vuoto) viene ripristinata da lì.
* Migrazione automatica: se esiste il vecchio database.json e il database SQLite è vuoto, viene importato.
"""
import asyncio
import contextvars
import copy
import json
import logging
import os
import sqlite3
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from telegram.ext import ContextTypes

from config import DB_FILE, BACKUP_CHAT_ID, ADMIN_ID
from state import (
    USER_DATA, USER_INVENTORIES, SYSTEM_FLAGS, BANCO_STATE, GROUP_CHALLENGE, GAME_STATUS,
    SILENCED, REACT_TARGETS, MUTE_FILTER, DAILY_DONATIONS, SANDBOX,
    POKER_GAMES, BLACKJACK_GAMES, WORDLE_GAMES, MASTERMIND_GAMES, SCRATCH_GAMES,
    AVIATOR_GAMES, HEIST_GAMES, QUIZ_GAMES,
    ACTIVE_DUELS, HIGHLOW_DUELS, DICE_DUELS, TIC_TAC_TOE_GAMES, GHIGLIOTTINA_DUELS, QUIZ_DUELS_1V1,
)

# Percorso del database: variabile DB_PATH; altrimenti /data/sdrogo.db se esiste un volume Northflank montato su /data;
# altrimenti sdrogo.db nella cartella dell'app.
DB_PATH = os.environ.get("DB_PATH") or (
    "/data/sdrogo.db" if os.path.isdir("/data") and os.access("/data", os.W_OK) else "sdrogo.db"
)

BANCO_INITIAL = 1000        # valore iniziale del Banco Centrale se è a zero o non inizializzato
HISTORY_MAX = 20            # giocate ricordate per utente (cronologia circolare, comando /storico)
SANDBOX_COINS = 10000       # saldo di partenza del profilo di prova dell'admin
HEIST_PASS = 150            # costo del pass Heist (per il rimborso in caso di riavvio)

_dirty = False
_touched_users = set()          # profili usati dopo l'ultimo salvataggio: solo questi vengono riconfrontati ogni 3 secondi
_touched_inv = set()
_last_full = 0.0                # ultima scansione completa di tutti i profili (rete di sicurezza ogni 30 s)
FULL_SCAN_EVERY = 120
_conn = None
_db_lock = threading.Lock()                         # un solo accesso alla connessione alla volta
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sdrogo-db")
_flush_lock = None                                  # asyncio.Lock creato al primo uso (dentro l'event loop)
_boot_sessions = []                                 # sessioni lette dal DB all'avvio, da ripristinare/rimborsare

# copia di ciò che è già su disco: {"users": {id: json}, "inv": {...}, "kv": {...}, "sess": {(kind, key): json}}
_saved = {"users": {}, "inv": {}, "kv": {}, "sess": {}}

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS inventories(id TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(kind TEXT NOT NULL, key TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(kind, key));
"""


# ----------------------------------------------------------------------------------------------
# SANDBOX ADMIN
# ----------------------------------------------------------------------------------------------
_SBX_CTX = contextvars.ContextVar("sdrogo_sandbox", default=False)


# "Account admin" della mini app: sandbox per singola richiesta (header), profilo separato "sbx_w<id>" con 30 milioni
_WEB_SBX = contextvars.ContextVar("sdrogo_web_sandbox", default=False)
WEB_SBX_COINS = 30_000_000


def set_web_sandbox(on: bool):
    """Chiamata dall'API a OGNI richiesta (True solo per l'admin col suo header): il valore non deve restare tra richieste."""
    _WEB_SBX.set(bool(on))


def is_web_sandbox() -> bool:
    return _WEB_SBX.get()


def is_sandbox(user_id) -> bool:
    """True se l'utente è l'admin e la modalità Sandbox (Telegram o account admin della mini app) è accesa."""
    return (bool(SANDBOX.get("on")) or _WEB_SBX.get()) and bool(ADMIN_ID) and str(user_id) == str(ADMIN_ID)


def ukey(user_id) -> str:
    """Chiave del profilo: str(user_id), oppure "sbx_<id>" / "sbx_w<id>" (profilo di prova) per l'admin in Sandbox."""
    if _WEB_SBX.get() and ADMIN_ID and str(user_id) == str(ADMIN_ID):
        return f"sbx_w{user_id}"
    return f"sbx_{user_id}" if is_sandbox(user_id) else str(user_id)


def ensure_web_sandbox(user_id):
    """Crea (una volta) il profilo di prova della mini app: 30M $SDG e un po' di tutti gli oggetti."""
    k = f"sbx_w{user_id}"
    if k not in USER_DATA:
        USER_DATA[k] = _new_profile(WEB_SBX_COINS)
    if k not in USER_INVENTORIES:
        USER_INVENTORIES[k] = {"titles": 5, "persecutes": 5, "stars": 0, "silence": 5, "mutefilter": 5,
                               "boost_win": 5, "boost_luck": 5, "sang": 5}


def set_sandbox_ctx(user_id):
    """Chiamata all'inizio di ogni update: nel contesto di una giocata Sandbox Banco, Follia e Sfida di gruppo
    restano fuori (vedi in_sandbox() in economy.py)."""
    _SBX_CTX.set(is_sandbox(user_id) if user_id is not None else False)


def in_sandbox() -> bool:
    return _SBX_CTX.get() or _WEB_SBX.get()


def sandbox_set(on: bool):
    """Accende (profilo di prova nuovo, con saldo e oggetti) o spegne (il profilo di prova viene buttato)."""
    uid = str(ADMIN_ID)
    USER_DATA.pop(f"sbx_{uid}", None)
    USER_INVENTORIES.pop(f"sbx_{uid}", None)
    SANDBOX["on"] = bool(on)
    if on and ADMIN_ID:
        USER_DATA[f"sbx_{uid}"] = _new_profile(SANDBOX_COINS)
        USER_INVENTORIES[f"sbx_{uid}"] = {
            "titles": 5, "persecutes": 5, "stars": 0, "silence": 3, "mutefilter": 3,
            "boost_win": 3, "boost_luck": 3, "sang": 2,
        }


# ----------------------------------------------------------------------------------------------
# CONNESSIONE E SCHEMA
# ----------------------------------------------------------------------------------------------
def _connect():
    d = os.path.dirname(DB_PATH)
    if d:
        os.makedirs(d, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    return conn


def _close():
    global _conn
    with _db_lock:
        if _conn is not None:
            try:
                _conn.close()
            except Exception:
                pass
            _conn = None


def _json(obj) -> str:
    def default(o):
        if isinstance(o, (set, frozenset)):
            return sorted(o)
        if isinstance(o, datetime):
            return o.isoformat()
        return None                                  # oggetti non salvabili (lock, task...): ignorati
    return json.dumps(obj, separators=(",", ":"), default=default)


def _is_empty() -> bool:
    """True se il database non contiene ancora nessun giocatore (container nuovo, nessun volume)."""
    return not any(not k.startswith("sbx_") for k in USER_DATA)


def _load_effects(eff: dict):
    """Ripristina silenzia, perseguita e filtro: gli effetti già scaduti vengono scartati."""
    now = time.time()
    SILENCED.clear()
    REACT_TARGETS.clear()
    MUTE_FILTER.clear()
    for k, v in (eff.get("silenced") or {}).items():
        if isinstance(v, dict) and v.get("until", 0) > now and v.get("left", 0) > 0:
            SILENCED[k] = v
    for k, v in (eff.get("react") or {}).items():
        if isinstance(v, dict) and v.get("until", 0) > now:
            REACT_TARGETS[k] = v
    for k, until in (eff.get("mute") or {}).items():
        try:
            if float(until) > now:
                MUTE_FILTER[k] = float(until)
        except (TypeError, ValueError):
            pass


def _apply_system(system: dict):
    SYSTEM_FLAGS.update(system.get("flags", {}))
    BANCO_STATE.update(system.get("banco", {}))
    GROUP_CHALLENGE.update(system.get("challenge", {}))
    _load_effects(system.get("effects", {}) or {})
    DAILY_DONATIONS.clear()
    DAILY_DONATIONS.update(system.get("donations", []) or [])
    for k, v in (system.get("games") or {}).items():
        if k in GAME_STATUS:
            GAME_STATUS[k] = bool(v)


def _init_banco():
    """Banco a zero o non inizializzato: parte da BANCO_INITIAL."""
    global _dirty
    try:
        bal = int(BANCO_STATE.get("BANCO_BALANCE", 0) or 0)
    except (TypeError, ValueError):
        bal = 0
    if bal <= 0:
        BANCO_STATE["BANCO_BALANCE"] = BANCO_INITIAL
        _dirty = True


# ----------------------------------------------------------------------------------------------
# PROFILO UNICO: la chiave di ogni giocatore è str(user_id), identica in privato e in tutti i gruppi.
# I vecchi DB usavano "chat_user": al primo avvio i profili vengono uniti.
# ----------------------------------------------------------------------------------------------
_NUM_STATS = ("quizzes_won", "casino_wins", "duels_wins", "net_profit", "duels_played",
              "single_played", "single_won", "duels_won_count")


def _migrate_profiles() -> bool:
    """Unisce i profili vecchio formato ("chat_user") in uno solo per utente.
    Saldo e missioni: del profilo col saldo più alto. Statistiche: sommate. Oggetti e cariche: sommati.
    I saldi degli altri profili restano annotati in 'legacy_coins' (si possono sempre recuperare)."""
    legacy_users = [k for k in USER_DATA if "_" in k and not k.startswith("sbx_")]
    legacy_invs = [k for k in USER_INVENTORIES if "_" in k and not k.startswith("sbx_")]
    if not legacy_users and not legacy_invs:
        return False

    by_user = {}
    for k in legacy_users:
        chat, uid = k.rsplit("_", 1)
        by_user.setdefault(uid, []).append((chat, USER_DATA.pop(k)))
    for uid, cands in by_user.items():
        if uid in USER_DATA:                                  # profilo già unificato: partecipa all'unione
            cands.append((None, USER_DATA.pop(uid)))
        cands.sort(key=lambda c: c[1].get("coins", 0), reverse=True)
        master = copy.deepcopy(cands[0][1])
        for stat in _NUM_STATS:
            total = sum(c[1].get(stat, 0) for c in cands)
            if total or stat in master:
                master[stat] = total
        for field in ("last_daily", "last_work"):
            vals = [c[1].get(field) for c in cands if c[1].get(field)]
            if vals:
                master[field] = max(vals, key=str)
        chats = set(master.get("chats", []))
        for chat, d in cands:
            for c in d.get("chats", []):
                chats.add(c)
            try:
                if chat is not None and str(chat) != str(BACKUP_CHAT_ID):
                    chats.add(int(chat))
            except ValueError:
                pass
        master["chats"] = sorted(chats)
        others = {str(c if c is not None else "unificato"): d.get("coins", 0) for c, d in cands[1:]}
        if others:
            master.setdefault("legacy_coins", {}).update(others)
        USER_DATA[uid] = master

    inv_by_user = {}
    for k in legacy_invs:
        chat, uid = k.rsplit("_", 1)
        inv_by_user.setdefault(uid, []).append(USER_INVENTORIES.pop(k))
    for uid, invs in inv_by_user.items():
        if uid in USER_INVENTORIES:
            invs.append(USER_INVENTORIES.pop(uid))
        merged = {}
        for d in invs:
            for key, val in d.items():
                if isinstance(val, bool) or not isinstance(val, (int, float)):
                    if key not in merged or (isinstance(val, dict) and val.get("left", 0) > merged[key].get("left", 0)):
                        merged[key] = copy.deepcopy(val)
                else:
                    merged[key] = merged.get(key, 0) + val
        USER_INVENTORIES[uid] = merged
    logging.info(f"Profili unificati: {len(by_user)} utenti, {len(inv_by_user)} inventari.")
    return True


def chat_user_ids(chat_id: int) -> list:
    """ID (stringa) dei giocatori visti in quella chat: per classifiche e ricerca degli @username."""
    try:
        cid = int(chat_id)
    except (TypeError, ValueError):
        return []
    return [uid for uid, d in USER_DATA.items() if cid in d.get("chats", ())]


# ----------------------------------------------------------------------------------------------
# CARICAMENTO
# ----------------------------------------------------------------------------------------------
def _read_sqlite():
    """Legge tutto il database nei dizionari in memoria."""
    global _boot_sessions
    with _db_lock:
        c = _conn
        users = list(c.execute("SELECT id, data FROM users"))
        invs = list(c.execute("SELECT id, data FROM inventories"))
        kv = dict(c.execute("SELECT key, data FROM kv"))
        sess = list(c.execute("SELECT kind, key, data FROM sessions"))
    USER_DATA.clear()
    USER_INVENTORIES.clear()
    _saved["users"] = {}
    _saved["inv"] = {}
    for uid, raw in users:
        try:
            USER_DATA[uid] = json.loads(raw)
            _saved["users"][uid] = raw
        except ValueError:
            logging.error(f"Profilo {uid} illeggibile: ignorato.")
    for uid, raw in invs:
        try:
            USER_INVENTORIES[uid] = json.loads(raw)
            _saved["inv"][uid] = raw
        except ValueError:
            pass
    _saved["kv"] = dict(kv)
    system = {}
    for name in ("flags", "banco", "challenge", "effects", "games", "donations"):
        if name in kv:
            try:
                system[name] = json.loads(kv[name])
            except ValueError:
                pass
    _apply_system(system)
    _saved["sess"] = {(k, key): raw for k, key, raw in sess}
    _boot_sessions = sess


def _import_legacy_json() -> bool:
    """Importa il vecchio database.json (una volta sola). True se ha importato qualcosa."""
    global _dirty
    if not DB_FILE or not os.path.exists(DB_FILE):
        return False
    try:
        with open(DB_FILE, "r") as f:
            data = json.load(f)
    except Exception as e:
        logging.error(f"database.json non leggibile, migrazione saltata: {e}")
        return False
    if isinstance(data.get("users"), dict):
        USER_DATA.update(data["users"])
        USER_INVENTORIES.update(data.get("inventories", {}))
        system = data.get("system", {})
        if isinstance(system, dict):
            _apply_system(system)
    else:
        USER_DATA.update(data)                    # formato originale: solo gli utenti
    _dirty = True
    logging.info(f"Migrazione da {DB_FILE}: {len(USER_DATA)} giocatori importati.")
    return True


def load_db():
    """Apre (o crea) il database SQLite e carica lo stato. Va chiamata una volta all'avvio, e dopo un ripristino."""
    global _conn, _dirty
    _close()
    with _db_lock:
        _conn = _connect()
    _read_sqlite()
    imported = False
    if _is_empty() and not _saved["kv"]:
        imported = _import_legacy_json()
    if _migrate_profiles():
        _dirty = True
    _init_banco()
    if imported:
        flush_db_sync()                           # i dati sono al sicuro nel nuovo DB: il vecchio file si mette da parte
        try:
            os.replace(DB_FILE, DB_FILE + ".migrato")
        except OSError:
            pass


# ----------------------------------------------------------------------------------------------
# SESSIONI DI GIOCO (partite in corso)
# ----------------------------------------------------------------------------------------------
_RESTORABLE = {
    "poker": POKER_GAMES, "blackjack": BLACKJACK_GAMES, "wordle": WORDLE_GAMES,
    "mastermind": MASTERMIND_GAMES, "scratch": SCRATCH_GAMES,
}
_DUEL_STORES = (HIGHLOW_DUELS, DICE_DUELS, ACTIVE_DUELS, TIC_TAC_TOE_GAMES, GHIGLIOTTINA_DUELS, QUIZ_DUELS_1V1)


def _split_key(key):
    chat, uid = str(key).rsplit("_", 1)
    return int(chat), int(uid)


def _tickets() -> dict:
    """Buoni di rimborso per le partite che non si possono riprendere dopo un riavvio."""
    out = {}

    def add(kind, ident, chat_id, uid, amount, label, banco=0):
        if amount and amount > 0 and not str(uid).startswith("sbx"):
            out[("refund", f"{kind}:{ident}")] = _json(
                {"chat_id": chat_id, "uid": uid, "amount": int(amount), "label": label, "banco": int(banco)})

    for key, g in list(AVIATOR_GAMES.items()):
        if not g.get("finished"):
            try:
                chat, uid = _split_key(key)
                add("avi", key, chat, uid, g.get("bet", 0), "Aviator")
            except ValueError:
                pass
    for uid, g in list(HEIST_GAMES.items()):
        add("heist", uid, g.get("chat_id", uid), uid, HEIST_PASS, "Sdrogo Heist", HEIST_PASS if g.get("v2") else 0)
    for key, g in list(QUIZ_GAMES.items()):
        if isinstance(key, str) and "_" in key and not g.get("multi"):
            try:
                chat, uid = _split_key(key)
                add("quiz", key, chat, uid, 5, "Quiz")
            except ValueError:
                pass
    for n, store in enumerate(_DUEL_STORES):
        for cid, g in list(store.items()):
            if g.get("target_id") and g.get("bet"):           # accettato = puntate già scalate
                for who in ("sfidante_id", "target_id"):
                    if g.get(who):
                        add("duel", f"{n}:{cid}:{who}", cid, g[who], g["bet"], "Duello")
    return out


def _collect_sessions() -> dict:
    rows = {}
    for kind, store in _RESTORABLE.items():
        for key, g in list(store.items()):
            rows[(kind, str(key))] = _json(g)
    rows.update(_tickets())
    return rows


async def recover_sessions(bot):
    """Da chiamare all'avvio, dopo load_db(): riprende le partite salvate e rimborsa quelle che non si possono riprendere."""
    global _boot_sessions, _dirty
    sessions, _boot_sessions = _boot_sessions, []
    if not sessions:
        return
    now = time.time()
    refunds = {}                                   # chat_id -> [(uid, amount, label)]
    restored = 0
    for kind, key, raw in sessions:
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        if kind in _RESTORABLE:
            if kind == "poker":
                for f in ("hole_p", "hole_b", "board", "deck"):
                    data[f] = [tuple(c) for c in data.get(f, [])]
            data["last"] = now                     # tempo di grazia: l'inattività riparte da adesso
            _RESTORABLE[kind][key] = data
            restored += 1
        elif kind == "refund":
            chat_id, uid, amount = data.get("chat_id"), data.get("uid"), int(data.get("amount", 0))
            if chat_id is None or uid is None or amount <= 0:
                continue
            u = USER_DATA.get(str(uid))
            if u is not None:
                u["coins"] = u.get("coins", 0) + amount
                _touched_users.add(str(uid))
            if data.get("banco"):
                BANCO_STATE["BANCO_BALANCE"] = max(0, int(BANCO_STATE.get("BANCO_BALANCE", 0)) - int(data["banco"]))
            refunds.setdefault(chat_id, []).append((uid, amount, data.get("label", "partita")))
    if refunds or restored:
        _dirty = True
        await flush_db()                           # rimborsi e buoni cancellati nella stessa transazione: mai due volte
    for chat_id, items in refunds.items():
        lines = "\n".join(f"• <a href=\"tg://user?id={u}\">giocatore</a>: +{a} $SDG ({lab})" for u, a, lab in items)
        try:
            await bot.send_message(
                chat_id=int(chat_id), parse_mode="HTML",
                text="♻️ <b>Il bot si è riavviato.</b> Le partite che non si possono riprendere sono state rimborsate:\n" + lines)
        except Exception as e:
            logging.warning(f"Avviso rimborso non inviato a {chat_id}: {e}")
    logging.info(f"Sessioni: {restored} riprese, {sum(len(v) for v in refunds.values())} rimborsate.")


# ----------------------------------------------------------------------------------------------
# SCRITTURA
# ----------------------------------------------------------------------------------------------
def save_db():
    """Segna il database come modificato. La scrittura vera avviene in flush_db (ogni 3 secondi)."""
    global _dirty
    _dirty = True


def _kv_rows() -> dict:
    return {
        "flags": _json(SYSTEM_FLAGS),
        "banco": _json(BANCO_STATE),
        "challenge": _json(GROUP_CHALLENGE),            # spesa, scadenza, message_id del messaggio fissato
        "effects": _json({"silenced": SILENCED, "react": REACT_TARGETS, "mute": MUTE_FILTER}),
        "games": _json(GAME_STATUS),
        "donations": _json(sorted(DAILY_DONATIONS)),     # il limite giornaliero resiste ai riavvii
        "meta": _json({"v": 8}),
    }


def touch_inv(key: str):
    """Segna un inventario come usato (economy.inv lo chiama): sarà riconfrontato al prossimo salvataggio."""
    _touched_inv.add(key)


def _build(full: bool, touched_u=None, touched_i=None):
    """Calcola le righe cambiate. Gira nel loop (fotografia coerente di tutto lo stato).
    full=False: confronta solo i profili toccati di recente (veloce anche con migliaia di giocatori);
    full=True: confronta tutti (ogni 30 secondi e allo spegnimento)."""
    if full:
        uids = [u for u in list(USER_DATA) if not u.startswith("sbx_")]
        iids = [u for u in list(USER_INVENTORIES) if not u.startswith("sbx_")]
    else:
        uids = [u for u in list(touched_u if touched_u is not None else _touched_users) if u in USER_DATA and not u.startswith("sbx_")]
        iids = [u for u in list(touched_i if touched_i is not None else _touched_inv) if u in USER_INVENTORIES and not u.startswith("sbx_")]
    u_new, i_new = {}, {}
    for u in uids:
        j = _json(USER_DATA[u])
        if _saved["users"].get(u) != j:
            u_new[u] = j
    for u in iids:
        j = _json(USER_INVENTORIES[u])
        if _saved["inv"].get(u) != j:
            i_new[u] = j
    u_del = [k for k in _saved["users"] if k not in USER_DATA] if full else []
    i_del = [k for k in _saved["inv"] if k not in USER_INVENTORIES] if full else []
    kv = _kv_rows()
    sess = _collect_sessions()
    cur = {"u_new": u_new, "i_new": i_new, "u_del": u_del, "i_del": i_del, "kv": kv, "sess": sess}
    changes = {
        "users": list(u_new.items()),
        "users_del": [(k,) for k in u_del],
        "inv": list(i_new.items()),
        "inv_del": [(k,) for k in i_del],
        "kv": [(k, v) for k, v in kv.items() if _saved["kv"].get(k) != v],
        "sess": [(k[0], k[1], v) for k, v in sess.items() if _saved["sess"].get(k) != v],
        "sess_del": [k for k in _saved["sess"] if k not in sess],
    }
    return cur, changes


def _has_changes(ch: dict) -> bool:
    return any(ch.values())


def _apply(ch: dict):
    """Scrive le modifiche in una transazione (gira in un thread: non blocca il bot)."""
    with _db_lock:
        c = _conn
        c.execute("BEGIN IMMEDIATE")
        try:
            up = "INSERT INTO {t}({k}, data) VALUES(?, ?) ON CONFLICT({k}) DO UPDATE SET data = excluded.data"
            if ch["users"]:
                c.executemany(up.format(t="users", k="id"), ch["users"])
            if ch["users_del"]:
                c.executemany("DELETE FROM users WHERE id = ?", ch["users_del"])
            if ch["inv"]:
                c.executemany(up.format(t="inventories", k="id"), ch["inv"])
            if ch["inv_del"]:
                c.executemany("DELETE FROM inventories WHERE id = ?", ch["inv_del"])
            if ch["kv"]:
                c.executemany(up.format(t="kv", k="key"), ch["kv"])
            if ch["sess"]:
                c.executemany(
                    "INSERT INTO sessions(kind, key, data) VALUES(?, ?, ?) "
                    "ON CONFLICT(kind, key) DO UPDATE SET data = excluded.data", ch["sess"])
            if ch["sess_del"]:
                c.executemany("DELETE FROM sessions WHERE kind = ? AND key = ?", ch["sess_del"])
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            raise


def _commit_saved(cur: dict):
    _saved["users"].update(cur["u_new"])
    _saved["inv"].update(cur["i_new"])
    for k in cur["u_del"]:
        _saved["users"].pop(k, None)
    for k in cur["i_del"]:
        _saved["inv"].pop(k, None)
    _saved["kv"] = cur["kv"]
    _saved["sess"] = cur["sess"]


async def flush_db(context=None):
    """Salva su SQLite ciò che è cambiato. Profili: solo quelli usati di recente (scansione completa ogni 30 s);
    partite e stato di sistema: sempre. Il lavoro pesante (scrittura) avviene in un thread."""
    global _dirty, _flush_lock, _last_full
    if _conn is None:
        return
    if _flush_lock is None:
        _flush_lock = asyncio.Lock()
    async with _flush_lock:
        was_dirty = _dirty
        _dirty = False
        now = time.time()
        full = was_dirty and now - _last_full >= FULL_SCAN_EVERY
        taken_u, taken_i = set(_touched_users), set(_touched_inv)
        _touched_users.clear()
        _touched_inv.clear()
        try:
            cur, ch = _build(full, taken_u, taken_i)
            if not _has_changes(ch):
                if full:
                    _last_full = now
                return
            await asyncio.get_running_loop().run_in_executor(_executor, _apply, ch)
            _commit_saved(cur)
            if full:
                _last_full = now
        except Exception as e:
            _dirty = True
            _touched_users.update(taken_u)
            _touched_inv.update(taken_i)
            logging.error(f"Errore salvataggio DB: {e}")


def flush_db_sync():
    """Scrittura immediata e bloccante, usata all'arresto del bot (e dopo la migrazione)."""
    global _dirty
    if _conn is None:
        return
    try:
        cur, ch = _build(True)
        if _has_changes(ch):
            _apply(ch)
        _commit_saved(cur)
        _dirty = False
    except Exception as e:
        logging.error(f"Errore salvataggio DB finale: {e}")


# ----------------------------------------------------------------------------------------------
# BACKUP / RIPRISTINO SU TELEGRAM
# ----------------------------------------------------------------------------------------------
def _rome_stamp() -> str:
    """Data e ora italiane per la didascalia del backup (non quelle del server, che sono in UTC)."""
    try:
        from economy import rome_now       # import ritardato: economy importa questo modulo
        return rome_now().strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _copy_db(dst_path: str):
    """Copia coerente del database (API di backup di SQLite, sicura anche mentre il bot scrive)."""
    dst = sqlite3.connect(dst_path)
    try:
        with _db_lock:
            _conn.backup(dst)
    finally:
        dst.close()


async def backup_now(bot):
    """Invia una copia del database nella chat di backup e la fissa."""
    if not BACKUP_CHAT_ID or _conn is None:
        return
    await flush_db()
    tmp = os.path.join(tempfile.gettempdir(), "sdrogo_backup.db")
    try:
        await asyncio.get_running_loop().run_in_executor(_executor, _copy_db, tmp)
        with open(tmp, "rb") as f:
            msg = await bot.send_document(
                chat_id=int(BACKUP_CHAT_ID), document=f, filename="sdrogo_backup.db",
                caption=f"Backup DB SdrogoBot - {_rome_stamp()}")
        try:
            await bot.pin_chat_message(chat_id=int(BACKUP_CHAT_ID), message_id=msg.message_id, disable_notification=True)
        except Exception as e:
            logging.error(f"Errore pin backup: {e}")
    except Exception as e:
        logging.error(f"Errore backup Telegram: {e}")
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


async def backup_to_telegram(context: ContextTypes.DEFAULT_TYPE):
    await backup_now(context.bot)


def _swap_db(tmp_path: str):
    _close()
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(DB_PATH + suffix)
        except OSError:
            pass
    d = os.path.dirname(DB_PATH)
    if d:
        os.makedirs(d, exist_ok=True)
    os.replace(tmp_path, DB_PATH)


async def auto_restore_from_telegram(bot):
    """Container nuovo e senza dati: ripristina dal messaggio fissato nella chat di backup (file .db oppure il vecchio .json)."""
    if not BACKUP_CHAT_ID or not _is_empty():
        return
    try:
        print("📦 Database vuoto: ricerca del backup fissato...", flush=True)
        chat = await bot.get_chat(int(BACKUP_CHAT_ID))
        pm = chat.pinned_message
        if not (pm and pm.document):
            print("⚠️ Nessun messaggio fissato con documento trovato nella chat di backup.", flush=True)
            return
        name = (pm.document.file_name or "").lower()
        f = await bot.get_file(pm.document.file_id)
        if name.endswith(".db"):
            tmp = DB_PATH + ".restore"
            await f.download_to_drive(tmp)
            await asyncio.to_thread(_swap_db, tmp)
        else:
            await f.download_to_drive(DB_FILE)           # vecchio backup JSON: verrà importato da load_db
        load_db()
        print("✅ Database ripristinato dal messaggio fissato!", flush=True)
    except Exception as e:
        logging.error(f"Errore Auto-Restore da messaggio fissato: {e}")


# ----------------------------------------------------------------------------------------------
# PROFILI
# ----------------------------------------------------------------------------------------------
def _new_profile(coins: int = 50) -> dict:
    return {
        "coins": coins, "last_daily": "", "quizzes_won": 0, "casino_wins": 0, "duels_wins": 0,
        "net_profit": 0, "duels_played": 0, "single_played": 0, "chats": [],
    }


def get_user_key(chat_id: int, user_id: int) -> str:
    """Chiave del profilo: solo l'ID utente, uguale in chat privata e in ogni gruppo (profilo di prova in Sandbox)."""
    return ukey(user_id)


def _track_chat(d: dict, chat_id) -> bool:
    """Ricorda in quali chat il giocatore è stato visto (serve a classifiche e ricerca username)."""
    try:
        cid = int(chat_id)
    except (TypeError, ValueError):
        return False
    chats = d.setdefault("chats", [])
    if cid in chats:
        return False
    chats.append(cid)
    return True


def get_user_data_dict(chat_id: int, user_id: int) -> dict:
    key = get_user_key(chat_id, user_id)
    _touched_users.add(key)
    if key.startswith("sbx_"):                       # profilo di prova: niente chat tracciate, mai su disco
        if key not in USER_DATA:
            USER_DATA[key] = _new_profile(WEB_SBX_COINS if key.startswith("sbx_w") else SANDBOX_COINS)
        return USER_DATA[key]
    is_backup = str(chat_id) == str(BACKUP_CHAT_ID)
    if key not in USER_DATA:
        USER_DATA[key] = _new_profile()
        if not is_backup:
            _track_chat(USER_DATA[key], chat_id)
            save_db()
    else:
        changed = False
        if not is_backup and _track_chat(USER_DATA[key], chat_id):
            changed = True
        for stat in ["quizzes_won", "casino_wins", "duels_wins", "net_profit", "duels_played", "single_played"]:
            if stat not in USER_DATA[key]:
                USER_DATA[key][stat] = 0
                changed = True
        if changed and not is_backup:
            save_db()
    return USER_DATA[key]


def get_user_coins(chat_id: int, user_id: int) -> int:
    data = get_user_data_dict(chat_id, user_id)
    return data.get("coins", 50)


def add_user_coins(chat_id: int, user_id: int, amount: int):
    if str(chat_id) == str(BACKUP_CHAT_ID):
        return
    data = get_user_data_dict(chat_id, user_id)
    data["coins"] = max(0, data.get("coins", 50) + amount)
    save_db()


def add_user_stat(chat_id: int, user_id: int, stat_name: str, amount: int = 1):
    if str(chat_id) == str(BACKUP_CHAT_ID):
        return
    data = get_user_data_dict(chat_id, user_id)
    data[stat_name] = data.get(stat_name, 0) + amount
    save_db()


# ----------------------------------------------------------------------------------------------
# CRONOLOGIA (comando admin /storico)
# ----------------------------------------------------------------------------------------------
def log_play(user_id, game: str, bet: int, payout: int, outcome: str = ""):
    """Aggiunge una giocata alla cronologia circolare dell'utente (ultime HISTORY_MAX).
    bet = importo puntato; payout = totale restituito al giocatore (0 se persa). outcome: vinta / persa / pareggio."""
    key = ukey(user_id)
    if key.startswith("sbx_"):
        return                                        # le prove dell'admin non lasciano tracce
    d = USER_DATA.get(key)
    if d is None:
        return
    bet, payout = int(bet), int(payout)
    if not outcome:
        outcome = "vinta" if payout > bet else ("pareggio" if payout == bet else "persa")
    _touched_users.add(key)
    hist = d.setdefault("history", [])
    hist.append({"t": int(time.time()), "g": str(game), "b": bet, "o": outcome, "w": payout})
    if len(hist) > HISTORY_MAX:
        del hist[:-HISTORY_MAX]
    save_db()


LAST_SEEN = {}            # user_id (str) -> istante dell'ultimo messaggio/tasto (solo memoria)
SEEN_SAVE_EVERY = 600     # sul profilo l'ultimo accesso si aggiorna al massimo ogni 10 minuti (per non scrivere a ogni tocco)


def note_username(user):
    """Ricorda @username e nome (per /storico) e l'ultimo momento in cui la persona ha usato il bot (per 'Utenti online')."""
    if not user or user.is_bot:
        return
    now = time.time()
    LAST_SEEN[str(user.id)] = now
    d = USER_DATA.get(str(user.id))
    if d is None:
        return
    uname = (user.username or "").lower()
    name = (user.first_name or "")[:40]
    changed = False
    if d.get("username", "") != uname or d.get("name", "") != name:
        d["username"] = uname
        d["name"] = name
        changed = True
    if now - d.get("seen", 0) > SEEN_SAVE_EVERY:
        d["seen"] = int(now)
        changed = True
    if changed:
        _touched_users.add(str(user.id))
        save_db()


def find_user_id(query: str):
    """Trova l'ID di un giocatore da @username (o dall'ID numerico). None se sconosciuto."""
    q = (query or "").strip().lstrip("@").lower()
    if not q:
        return None
    if q.isdigit() and q in USER_DATA:
        return q
    for uid, d in USER_DATA.items():
        if not uid.startswith("sbx_") and d.get("username") == q:
            return uid
    return None
