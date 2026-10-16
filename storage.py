import asyncio
import copy
import os
import json
import logging
import time
from datetime import datetime
from telegram.ext import ContextTypes

from config import DB_FILE, BACKUP_CHAT_ID
from state import (
    USER_DATA, USER_INVENTORIES, SYSTEM_FLAGS, BANCO_STATE, GROUP_CHALLENGE, GAME_STATUS,
    SILENCED, REACT_TARGETS, MUTE_FILTER, DAILY_DONATIONS,
)

_dirty = False

BANCO_INITIAL = 1000      # valore iniziale del Banco Centrale se è a zero o non inizializzato


async def auto_restore_from_telegram(bot):
    if not BACKUP_CHAT_ID: return
    try:
        chat_id = int(BACKUP_CHAT_ID)
        if not os.path.exists(DB_FILE):
            print("📦 Ricerca messaggio fissato per il ripristino...", flush=True)
            chat = await bot.get_chat(chat_id)
            if chat.pinned_message and chat.pinned_message.document:
                file_info = await bot.get_file(chat.pinned_message.document.file_id)
                await file_info.download_to_drive(DB_FILE)
                print("✅ Database ripristinato con successo dal messaggio fissato!", flush=True)
                load_db()
            else:
                print("⚠️ Nessun messaggio fissato con documento trovato nella chat di backup.", flush=True)
    except Exception as e:
        logging.error(f"Errore Auto-Restore da messaggio fissato: {e}")


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
    legacy_users = [k for k in USER_DATA if "_" in k]
    legacy_invs = [k for k in USER_INVENTORIES if "_" in k]
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


def load_db():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r") as f:
                data = json.load(f)
            USER_DATA.clear()
            USER_INVENTORIES.clear()
            if isinstance(data.get("users"), dict):
                # Nuovo formato: utenti + inventari + stato di sistema (V2)
                USER_DATA.update(data["users"])
                USER_INVENTORIES.update(data.get("inventories", {}))
                system = data.get("system", {})
                if isinstance(system, dict):
                    SYSTEM_FLAGS.update(system.get("flags", {}))
                    BANCO_STATE.update(system.get("banco", {}))
                    GROUP_CHALLENGE.update(system.get("challenge", {}))
                    _load_effects(system.get("effects", {}) or {})
                    DAILY_DONATIONS.clear()
                    DAILY_DONATIONS.update(system.get("donations", []) or [])
                    for k, v in (system.get("games") or {}).items():
                        if k in GAME_STATUS:
                            GAME_STATUS[k] = bool(v)
            else:
                # Vecchio formato: il file conteneva solo USER_DATA
                USER_DATA.update(data)
        except Exception as e:
            logging.error(f"Errore caricamento DB: {e}")
            USER_DATA.clear()
            USER_INVENTORIES.clear()
    if _migrate_profiles():
        save_db()
    _init_banco()


def _snapshot() -> str:
    """Serializza tutto lo stato persistente. Va chiamata nel loop (snapshot coerente)."""
    return json.dumps({
        "users": USER_DATA,
        "inventories": USER_INVENTORIES,      # oggetti + cariche residue di boost/malus
        "system": {
            "flags": SYSTEM_FLAGS,
            "banco": BANCO_STATE,
            "challenge": GROUP_CHALLENGE,     # spesa, scadenza, message_id del messaggio fissato
            "effects": {"silenced": SILENCED, "react": REACT_TARGETS, "mute": MUTE_FILTER},
            "games": GAME_STATUS,
            "donations": sorted(DAILY_DONATIONS),   # donazioni di oggi: il limite giornaliero resiste ai riavvii
        },
    })


def save_db():
    """Segna il database come modificato. La scrittura vera avviene in flush_db (ogni 5 secondi)."""
    global _dirty
    _dirty = True


def _write_file(payload: str):
    tmp = DB_FILE + ".tmp"
    with open(tmp, "w") as f:
        f.write(payload)
    os.replace(tmp, DB_FILE)


async def flush_db(context=None):
    """Scrive il DB su disco se è cambiato. Il JSON viene creato nel loop (snapshot coerente),
    la scrittura su file avviene in un thread, così il bot non si ferma."""
    global _dirty
    if not _dirty:
        return
    _dirty = False
    try:
        payload = _snapshot()
        await asyncio.to_thread(_write_file, payload)
    except Exception as e:
        _dirty = True
        logging.error(f"Errore salvataggio DB: {e}")


def flush_db_sync():
    """Scrittura immediata, usata all'arresto del bot."""
    global _dirty
    try:
        _write_file(_snapshot())
        _dirty = False
    except Exception as e:
        logging.error(f"Errore salvataggio DB finale: {e}")


def _rome_stamp() -> str:
    """Data e ora italiane per la didascalia del backup (non quelle del server, che sono in UTC)."""
    try:
        from economy import rome_now       # import ritardato: economy importa questo modulo
        return rome_now().strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


async def backup_to_telegram(context: ContextTypes.DEFAULT_TYPE):
    await flush_db()
    if BACKUP_CHAT_ID and os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "rb") as f:
                msg = await context.bot.send_document(
                    chat_id=int(BACKUP_CHAT_ID),
                    document=f,
                    caption=f"Backup DB SdrogoBot - {_rome_stamp()}"
                )
            try:
                await context.bot.pin_chat_message(
                    chat_id=int(BACKUP_CHAT_ID),
                    message_id=msg.message_id,
                    disable_notification=True
                )
            except Exception as e:
                logging.error(f"Errore pin backup: {e}")
        except Exception as e:
            logging.error(f"Errore backup Telegram: {e}")

def get_user_key(chat_id: int, user_id: int) -> str:
    """Chiave del profilo: solo l'ID utente, uguale in chat privata e in ogni gruppo."""
    return str(user_id)

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
    is_backup = str(chat_id) == str(BACKUP_CHAT_ID)
    if key not in USER_DATA:
        USER_DATA[key] = {
            "coins": 50,
            "last_daily": "",
            "quizzes_won": 0,
            "casino_wins": 0,
            "duels_wins": 0,
            "net_profit": 0,
            "duels_played": 0,
            "single_played": 0,
            "chats": [],
        }
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
        if changed and str(chat_id) != str(BACKUP_CHAT_ID):
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
