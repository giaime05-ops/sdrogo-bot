"""API della mini app Sdrogo Arcade (Flask Blueprint, gira nello stesso processo del bot).

Endpoint:
  GET /api/me   ->  dati del giocatore (nome, saldo $SDG), Banco e stato della Follia

Sicurezza:
  * l'identità arriva dall'initData firmato da Telegram (HMAC-SHA256 col token del bot) e NON da ciò che dice il client;
  * l'initData scade (INITDATA_MAX_AGE);
  * durante lo sviluppo risponde solo all'ADMIN_ID (403 per gli altri; se ADMIN_ID non è impostato nega a tutti);
  * CORS limitato all'indirizzo della mini app (ARCADE_ORIGIN).

Nota sui thread: Flask gira in un thread separato dal bot. Qui si LEGGE soltanto lo stato condiviso (nessuna scrittura).
Le scritture (puntate, cashout) delle fasi successive dovranno essere eseguite sul loop del bot.
"""
import asyncio
import random
import hashlib
import hmac
import json
import logging
import math
import os
import re
import threading
import time
import urllib.parse

from flask import Blueprint, g, jsonify, request

import config
from state import USER_DATA, AVIATOR_GAMES, game_enabled
from state import USER_INVENTORIES
from storage import save_db, mark_seen, ukey, get_user_data_dict, add_user_coins, set_web_sandbox, ensure_web_sandbox, is_web_sandbox
import economy
from economy import today_str, spin_daily, DAILY_WHEEL
from state import SCRATCH_GAMES, SYSTEM_FLAGS, maint_mode
from utils import is_v2
from economy import banco_balance, follia_active, follia_left_text

webapi = Blueprint("webapi", __name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN") or getattr(config, "TELEGRAM_TOKEN", "")
ADMIN_ID = getattr(config, "ADMIN_ID", None)
ARCADE_ORIGIN = os.environ.get("ARCADE_ORIGIN", "https://giaime05-ops.github.io").rstrip("/")
INITDATA_MAX_AGE = int(os.environ.get("INITDATA_MAX_AGE", str(24 * 3600)))   # secondi


# ----------------------------------------------------------------------------------------------
# CORS (solo per l'indirizzo della mini app)
# ----------------------------------------------------------------------------------------------
@webapi.after_request
def _cors(response):
    origin = request.headers.get("Origin", "")
    if origin.rstrip("/") == ARCADE_ORIGIN:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Telegram-Init-Data, Authorization, X-Arcade-Sandbox"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Max-Age"] = "600"
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


# ----------------------------------------------------------------------------------------------
# Verifica dell'initData di Telegram
# ----------------------------------------------------------------------------------------------
def validate_init_data(init_data: str):
    """Restituisce il dict dell'utente Telegram se l'initData è autentico e recente, altrimenti None."""
    if not init_data or not TELEGRAM_TOKEN:
        return None
    try:
        pairs = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
        received = pairs.pop("hash", None)
        if not received:
            return None
        check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
        secret = hmac.new(b"WebAppData", TELEGRAM_TOKEN.encode(), hashlib.sha256).digest()
        calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc, received.lower()):
            return None
        auth_date = int(pairs.get("auth_date", "0") or 0)
        if auth_date <= 0 or time.time() - auth_date > INITDATA_MAX_AGE:
            return None                                       # scaduto
        user = json.loads(pairs.get("user", "null"))
        return user if isinstance(user, dict) and user.get("id") else None
    except Exception as e:                                    # dati malformati: rifiuta, non crollare
        logging.warning(f"initData non valido: {e}")
        return None


def _init_data_from_request() -> str:
    h = request.headers.get("X-Telegram-Init-Data")
    if h:
        return h
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("tma "):
        return auth[4:].strip()
    return request.args.get("initData", "")


# ----------------------------------------------------------------------------------------------
# Chi può entrare: l'admin sempre; gli altri solo se l'admin ha acceso "Mini app aperta" (da /admin) E sono nel gruppo
# GROUP_CHAT_ID (oppure negli ID extra di ARCADE_EXTRA_IDS). Spegnendo l'interruttore gli altri escono subito.
# ----------------------------------------------------------------------------------------------
GROUP_CHAT_ID = os.environ.get("GROUP_CHAT_ID") or getattr(config, "GROUP_CHAT_ID", None)
EXTRA_IDS = {x.strip() for x in (os.environ.get("ARCADE_EXTRA_IDS") or "").split(",") if x.strip()}
MEMBER_TTL_OK, MEMBER_TTL_NO = 600, 60              # secondi: quanto si ricorda "è nel gruppo" / "non è nel gruppo"
_MEMBER_CACHE = {}


def _is_admin_id(uid) -> bool:
    return bool(ADMIN_ID) and str(uid) == str(ADMIN_ID)


async def _member_check(uid: int) -> bool:
    m = await _bot().get_chat_member(int(GROUP_CHAT_ID), int(uid))
    return m.status in ("member", "administrator", "creator") or (m.status == "restricted" and bool(getattr(m, "is_member", False)))


def _is_member(uid) -> bool:
    """True se la persona fa parte del gruppo. Errore di Telegram = NO (si nega, mai si ammette per errore)."""
    if str(uid) in EXTRA_IDS:
        return True
    if not GROUP_CHAT_ID:
        return False
    now = time.time()
    hit = _MEMBER_CACHE.get(str(uid))
    if hit and now < hit[1]:
        return hit[0]
    try:
        ok = bool(_run(_member_check(uid), timeout=6.0))
    except Exception as e:
        logging.warning(f"Controllo membro gruppo fallito per {uid}: {e}")
        return False
    _MEMBER_CACHE[str(uid)] = (ok, now + (MEMBER_TTL_OK if ok else MEMBER_TTL_NO))
    return ok


def arcade_open() -> bool:
    return bool(SYSTEM_FLAGS.get("ARCADE_PUBLIC"))


_NEW_ROUND = {"/api/aviator/start", "/api/slot/spin", "/api/roulette/spin", "/api/roulette/table",
              "/api/scratch/buy", "/api/wheel/spin", "/api/heist/start"}


def _is_new_round(path: str) -> bool:
    """Durante la manutenzione graduale queste richieste (cominciare un giro/una mano) vengono rifiutate."""
    if path in _NEW_ROUND:
        return True
    return path.startswith("/api/g/") and path.rstrip("/").endswith("/start")


def require_admin():
    """(Nome storico: lo usano tutte le rotte.) Restituisce (utente, None) se la richiesta è di chi può entrare;
    altrimenti (None, risposta di errore). L'admin entra sempre; gli altri solo con la mini app aperta e nel gruppo."""
    user = validate_init_data(_init_data_from_request())
    if not user:
        if not _take("bad:" + _ip(), BAD_RATE, BAD_BURST):
            return None, (jsonify({"success": False, "error": "Troppi tentativi, riprova tra poco."}), 429)
        return None, (jsonify({"success": False, "error": "Autenticazione non valida o scaduta."}), 401)
    if not _is_admin_id(user["id"]):
        if not arcade_open():
            return None, (jsonify({"success": False, "error": "Sdrogo Arcade è ancora in sviluppo: per ora è riservata all'admin."}), 403)
        hit = _MEMBER_CACHE.get(str(user["id"]))
        cached = bool(hit and time.time() < hit[1]) or str(user["id"]) in EXTRA_IDS
        # limite stretto solo quando bisogna davvero interrogare Telegram (appartenenza non ancora in memoria):
        # prima valeva a ogni richiesta e bloccava chi giocava ad Aviator (4 richieste/s) dopo pochi secondi
        if not cached and not _take("nm:" + str(user["id"]), 1.0, 10):
            return None, (jsonify({"success": False, "error": "Troppe richieste, rallenta un attimo."}), 429)
        if not _is_member(user["id"]):
            return None, (jsonify({"success": False, "error": "Sdrogo Arcade è riservata ai membri del gruppo."}), 403)
    if not _is_admin_id(user["id"]):
        mm = maint_mode()
        if mm == "hard" or (mm == "soft" and _is_new_round(request.path)):
            return None, (jsonify({"success": False, "maintenance": mm,
                                   "error": "🛠️ Il server è in manutenzione: riprova tra poco."
                                   if mm == "hard" else
                                   "🛠️ Server in manutenzione: puoi finire la partita in corso ma non iniziarne di nuove."}), 503)
    uid = str(user["id"])
    mark_seen(uid)                                    # la mini app conta come uso del bot (Utenti online)
    # "Account admin" della mini app: solo l'admin, solo se la richiesta lo chiede; il valore vale per questa richiesta
    sbx = _is_admin_id(user["id"]) and request.headers.get("X-Arcade-Sandbox") == "1"
    set_web_sandbox(sbx)
    if sbx:
        ensure_web_sandbox(user["id"])
    if not _take("u:" + uid, USER_RATE, USER_BURST):
        return None, (jsonify({"success": False, "error": "Troppe richieste, rallenta un attimo."}), 429)
    if request.method == "POST":                      # una sola mossa alla volta per giocatore: niente doppi tocchi né richieste parallele
        with _B_LOCK:
            if uid in _INFLIGHT:
                return None, (jsonify({"success": False, "error": "Un'altra operazione è ancora in corso."}), 429)
            _INFLIGHT.add(uid)
        g.inflight_uid = uid
    return user, None


# ----------------------------------------------------------------------------------------------
# Endpoint
# ----------------------------------------------------------------------------------------------
@webapi.route("/api/me", methods=["GET", "POST", "OPTIONS"])
def api_me():
    if request.method == "OPTIONS":
        return "", 204
    user, err = require_admin()
    if err:
        return err

    uid = str(user["id"])
    profile = get_user_data_dict(user["id"], user["id"]) if _is_admin_id(user["id"]) and is_web_sandbox() else (USER_DATA.get(ukey(user["id"])) or {})
    coins = int(profile.get("coins", 0)) if profile else 0

    follia = follia_active()
    return jsonify({
        "success": True,
        "user": {
            "id": user["id"],
            "first_name": user.get("first_name") or "Giocatore",
            "username": user.get("username") or "",
            "balance": coins,
        },
        "economy": {
            "bank_balance": banco_balance(),
            "follia_active": follia,
            "follia_left": follia_left_text() if follia else "",
        },
        "daily": {"available": (profile.get("last_daily") or "") != today_str()},
        "admin": _is_admin_id(user["id"]),
        "maintenance": maint_mode(),
        "style": __import__("websocial")._style(USER_DATA.get(str(user["id"])) or {}),
        "sandbox": bool(is_web_sandbox()),
        "badge": (USER_DATA.get(str(user["id"])) or {}).get("badge", ""),
        "stars": int((USER_INVENTORIES.get(ukey(user["id"])) or {}).get("stars", 0) or 0),
    })


# ----------------------------------------------------------------------------------------------
# FASE 6: protezioni (limiti di richieste, una sola mossa alla volta, dimensione massima, errori senza dettagli)
# ----------------------------------------------------------------------------------------------
MAX_BODY = 4096                                  # byte: nessuna richiesta legittima è più grande
IP_RATE, IP_BURST = 20.0, 60                     # per indirizzo: richieste/secondo a regime e raffica massima
USER_RATE, USER_BURST = 8.0, 20                  # per giocatore autenticato
BAD_RATE, BAD_BURST = 0.5, 10                    # tentativi con identità non valida, per indirizzo
_BUCKETS = {}
_B_LOCK = threading.Lock()
_INFLIGHT = set()


def _take(key: str, rate: float, burst: int) -> bool:
    """Secchiello di gettoni: True se la richiesta può passare."""
    now = time.monotonic()
    with _B_LOCK:
        tokens, last = _BUCKETS.get(key, (float(burst), now))
        tokens = min(float(burst), tokens + (now - last) * rate)
        ok = tokens >= 1.0
        _BUCKETS[key] = (tokens - 1.0 if ok else tokens, now)
        if len(_BUCKETS) > 4000:                 # pulizia: via chi non si fa vivo da 5 minuti
            for k in [k for k, (_, t) in _BUCKETS.items() if now - t > 300]:
                _BUCKETS.pop(k, None)
        return ok


def _ip() -> str:
    xff = (request.headers.get("X-Forwarded-For") or "").split(",")[-1].strip()   # l'ultimo lo aggiunge il proxy; il primo lo può scrivere il client
    return xff or request.remote_addr or "?"


@webapi.before_request
def _guard_request():
    if request.method == "OPTIONS":
        return None
    if (request.content_length or 0) > MAX_BODY:
        return _err("Richiesta troppo grande.", 413)
    if not _take("ip:" + _ip(), IP_RATE, IP_BURST):
        return _err("Troppe richieste, rallenta un attimo.", 429)
    return None


@webapi.teardown_request
def _release_inflight(exc=None):
    uid = g.pop("inflight_uid", None) if g else None
    if uid is not None:
        with _B_LOCK:
            _INFLIGHT.discard(uid)


@webapi.errorhandler(Exception)
def _any_error(e):
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return _err(e.description or "Errore.", e.code or 400)
    logging.exception("Errore non gestito nella mini app")      # il dettaglio resta nel log, al client non arriva
    return _err("Errore interno, riprova.", 500)


# ----------------------------------------------------------------------------------------------
# Ponte verso il loop del bot: Flask gira in un altro thread, ma saldo, Banco e statistiche si toccano SOLO
# dal loop del bot (un solo thread = nessuna corsa tra due richieste o tra mini app e chat).
# ----------------------------------------------------------------------------------------------
LOOP = None


def set_runtime(loop):
    """Chiamata da main.py quando il bot è partito."""
    global LOOP
    LOOP = loop


def _bot():
    return economy._BOT        # impostato da main.py (set_bot): serve a banco_check per annunciare la Follia


def _run(coro, timeout: float = 15.0):
    if LOOP is None:
        coro.close()
        raise RuntimeError("bot non ancora pronto")
    return asyncio.run_coroutine_threadsafe(coro, LOOP).result(timeout)


def _err(msg: str, code: int = 400):
    return jsonify({"success": False, "error": msg}), code


def _clean(notes):
    return [re.sub(r"<[^>]+>", "", n).strip() for n in (notes or []) if n]


# ----------------------------------------------------------------------------------------------
# AVIATOR (stesse regole, stessa economia e stessi calcoli del gioco nel bot)
# ----------------------------------------------------------------------------------------------
WEB_AVI_LAST = {}          # uid -> ultimo risultato (serve a chi si è perso il crash e per i doppi tocchi)
AVI_MIN_FLIGHT = 1.3       # durata minima del volo mostrato (anche per un crash immediato)


def _g():
    from handlers import games as G      # import tardivo: evita dipendenze circolari all'avvio
    return G


def _balance(uid: int) -> int:
    return int((USER_DATA.get(ukey(uid)) or {}).get("coins", 0))


def _avi_crash_t(crash: float) -> float:
    return math.log(crash) / _g().AVIATOR_RATE if crash > 1.0 else 0.0


async def _avi_finish_crash(game: dict, key: str, uid: int):
    G = _g()
    game["finished"] = True
    if AVIATOR_GAMES.get(key) is game:
        del AVIATOR_GAMES[key]
    _, notes = await G._settle(_bot(), uid, uid, "aviator", game["bet"], 0)
    res = {"gid": game["gid"], "result": "crash", "crash": game["crash"], "bet": game["bet"],
           "paid": 0, "net": -game["bet"], "notes": _clean(notes), "balance": _balance(uid)}
    WEB_AVI_LAST[uid] = res
    return res


async def _avi_watch(game: dict, key: str, uid: int):
    """Se il giocatore non incassa (o chiude l'app) l'aereo vola via comunque, e la puntata viene liquidata."""
    try:
        G = _g()
        end = game["t0"] + max(_avi_crash_t(game["crash"]) + G.AVIATOR_GRACE, AVI_MIN_FLIGHT)
        await asyncio.sleep(max(0.0, end - time.time()))
        if not game["finished"]:
            await _avi_finish_crash(game, key, uid)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logging.error(f"Errore Aviator (mini app): {e}")


def _avi_view(uid: int) -> dict:
    G = _g()
    game = AVIATOR_GAMES.get(f"{uid}_{uid}")
    out = {"success": True, "balance": _balance(uid), "bets": list(G.SP_BETS), "rate": G.AVIATOR_RATE,
           "enabled": bool(game_enabled("aviator")), "active": False, "last": WEB_AVI_LAST.get(uid)}
    if game and not game["finished"] and game.get("web"):
        out.update(active=True, gid=game["gid"], bet=game["bet"], elapsed=round(time.time() - game["t0"], 3))
    elif game and not game["finished"]:
        out["busy_in_chat"] = True            # volo in corso nella chat del bot
    return out


async def _avi_state(uid: int):
    return _avi_view(uid)


async def _avi_start(uid: int, name: str, bet: int):
    G = _g()
    key = f"{uid}_{uid}"
    if not game_enabled("aviator"):
        return {"error": "Aviator è in manutenzione.", "code": 503}
    if bet not in G.SP_BETS:
        return {"error": "Puntata non valida.", "code": 400}
    existing = AVIATOR_GAMES.get(key)
    if existing and not existing["finished"] and time.time() - existing["ts"] < 300:
        return {"error": "Hai già un volo in corso.", "code": 409, "view": _avi_view(uid)}
    if _balance(uid) < bet:
        return {"error": f"Servono {bet} $SDG per questa puntata.", "code": 400}
    # nessun await tra i controlli e queste righe: due richieste simultanee non possono passare entrambe
    G._charge(uid, uid, bet)
    now = time.time()
    game = {"bet": bet, "crash": G.aviator_crash_point(), "current": 1.0, "name": name, "coins_view": 0,
            "finished": False, "ts": now, "t0": now, "lock": asyncio.Lock(), "web": True, "gid": int(now * 1000)}
    AVIATOR_GAMES[key] = game
    WEB_AVI_LAST.pop(uid, None)
    game["task"] = asyncio.get_running_loop().create_task(_avi_watch(game, key, uid))
    return {"view": _avi_view(uid)}


async def _avi_cashout(uid: int, name: str, t_click: float):
    G = _g()
    key = f"{uid}_{uid}"
    game = AVIATOR_GAMES.get(key)
    if not game or game["finished"] or not game.get("web"):
        last = WEB_AVI_LAST.get(uid)
        if last:                                   # doppio tocco o crash già avvenuto: stessa risposta, nessun doppio pagamento
            return {"result": last, "repeat": True, "view": _avi_view(uid)}
        return {"error": "Nessun volo in corso.", "code": 409}
    game["finished"] = True                        # sincrono: il guardiano del crash vede subito finished
    if AVIATOR_GAMES.get(key) is game:
        del AVIATOR_GAMES[key]
    try:
        game["task"].cancel()
    except Exception:
        pass
    bet = game["bet"]
    el = max(0.0, t_click - game["t0"] - G.AVIATOR_GRACE)
    if el >= _avi_crash_t(game["crash"]):          # il clic è arrivato dopo il crash
        _, notes = await G._settle(_bot(), uid, uid, "aviator", bet, 0)
        res = {"gid": game["gid"], "result": "crash", "crash": game["crash"], "bet": bet, "paid": 0, "net": -bet,
               "late": True, "notes": _clean(notes), "balance": _balance(uid)}
    else:
        cur = G._avi_mult(el)
        paid, notes = await G._settle(_bot(), uid, uid, "aviator", bet, int(bet * cur))
        res = {"gid": game["gid"], "result": "cashout", "mult": cur, "crash": game["crash"], "bet": bet,
               "paid": paid, "net": paid - bet, "notes": _clean(notes), "balance": _balance(uid)}
    WEB_AVI_LAST[uid] = res
    return {"result": res, "view": _avi_view(uid)}


def _avi_call(coro):
    """Esegue sul loop del bot. Restituisce (risposta_di_errore, None) oppure (None, risultato)."""
    try:
        out = _run(coro)
    except Exception as e:
        logging.error(f"Errore API Aviator: {e}")
        return _err("Il bot non risponde, riprova.", 503), None
    if isinstance(out, dict) and out.get("error"):
        body = {"success": False, "error": out["error"]}
        if out.get("view") or out.get("state"):
            body["state"] = out.get("view") or out.get("state")
        return (jsonify(body), out.get("code", 400)), None
    return None, out


@webapi.route("/api/aviator/state", methods=["GET", "OPTIONS"])
def api_avi_state():
    if request.method == "OPTIONS":
        return "", 204
    user, err = require_admin()
    if err:
        return err
    resp, out = _avi_call(_avi_state(int(user["id"])))
    return resp if resp is not None else jsonify(out)


@webapi.route("/api/aviator/start", methods=["POST", "OPTIONS"])
def api_avi_start():
    if request.method == "OPTIONS":
        return "", 204
    user, err = require_admin()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    try:
        bet = int(data.get("bet"))
    except (TypeError, ValueError):
        return _err("Puntata non valida.")
    resp, out = _avi_call(_avi_start(int(user["id"]), user.get("first_name") or "Giocatore", bet))
    return resp if resp is not None else jsonify(out["view"])


@webapi.route("/api/aviator/cashout", methods=["POST", "OPTIONS"])
def api_avi_cashout():
    t_click = time.time()                          # istante di arrivo del clic, prima di qualunque altra attesa
    if request.method == "OPTIONS":
        return "", 204
    user, err = require_admin()
    if err:
        return err
    resp, out = _avi_call(_avi_cashout(int(user["id"]), user.get("first_name") or "Giocatore", t_click))
    if resp is not None:
        return resp
    return jsonify({"success": True, "result": out["result"], "repeat": bool(out.get("repeat")), "state": out["view"]})


# ----------------------------------------------------------------------------------------------
# FASE 3: SLOT, ROULETTE, GRATTA E VINCI, RUOTA DAILY (stesse regole e stessa economia del bot)
# Il risultato è deciso dal server in un colpo solo e pagato subito; l'animazione la fa l'app.
# ----------------------------------------------------------------------------------------------
def _quest_note(notes: list, q):
    if q:
        notes.append(q)
    return _clean(notes)


SLOT_BONUS_P = 1 / 40          # probabilità che un giro normale sia "BONUS" (tre stelle): regala 3 giri gratis alla stessa puntata
SLOT_BONUS_SPINS = 3
SLOT_BONUS_SYM = "⭐"


def _slot_free(uid: int) -> dict:
    return get_user_data_dict(uid, uid).get("slot_free") or {}


async def _slot_state(uid: int):
    f = _slot_free(uid)
    return {"free_left": int(f.get("n", 0)), "free_bet": int(f.get("bet", 0)), "balance": _balance(uid)}


async def _slot_spin(uid: int, bet: int):
    G = _g()
    if not game_enabled("slot"):
        return {"error": "La Slot è in manutenzione.", "code": 503}
    u = get_user_data_dict(uid, uid)
    free = u.get("slot_free") or {}
    is_free = int(free.get("n", 0)) > 0
    if is_free:
        bet = int(free.get("bet", bet))               # giro gratuito: si gioca alla puntata che ha fatto scattare il bonus
    else:
        if bet not in G.SLOT_BETS:
            return {"error": "Puntata non valida.", "code": 400}
        if _balance(uid) < bet:
            return {"error": f"Servono {bet} $SDG per questa puntata.", "code": 400}
    luck = G.has_luck(uid, uid, "slot")
    if is_free:
        free["n"] = int(free["n"]) - 1
        u["slot_free"] = free if free["n"] > 0 else None
        save_db()
    else:
        G._charge(uid, uid, bet)
    # giro BONUS: tre stelle, nessuna vincita subito ma 3 giri gratis
    if not is_free and random.random() < SLOT_BONUS_P:
        u["slot_free"] = {"n": SLOT_BONUS_SPINS, "bet": bet}
        save_db()
        paid, notes = await G._settle(_bot(), uid, uid, "slot", bet, 0)
        G.quest_progress(uid, uid, "slot_play")
        return {"result": {"reels": [SLOT_BONUS_SYM] * 3, "kind": "bonus", "bet": bet, "paid": 0, "net": -bet, "notes": list(notes),
                           "free_left": SLOT_BONUS_SPINS, "free_bet": bet, "balance": _balance(uid)}}
    reels = [random.choice(G.SLOT_SYMBOLS) for _ in range(3)]
    payout, kind = G.slot_payout(reels, bet)
    luck_used = False
    if kind == "none" and luck and random.random() < G.LUCK_REROLL_CHANCE:
        reels = [random.choice(G.SLOT_SYMBOLS) for _ in range(3)]
        payout, kind = G.slot_payout(reels, bet)
        luck_used = True
    paid, notes = await G._settle(_bot(), uid, uid, "slot", 0 if is_free else bet, payout)
    notes = list(notes)
    if luck_used and paid > 0:
        notes.insert(0, "🍀 Boost Fortuna: la fortuna ti ha aiutato!")
    q = G.quest_progress(uid, uid, "slot_play")
    left = int((u.get("slot_free") or {}).get("n", 0))
    return {"result": {"reels": reels, "kind": kind, "bet": bet, "paid": paid, "net": paid - (0 if is_free else bet), "free": is_free,
                       "free_left": left, "free_bet": bet if left else 0,
                       "notes": _quest_note(notes, q), "balance": _balance(uid)}}


async def _roul_spin(uid: int, bet: int, choice: str):
    G = _g()
    if not game_enabled("roulette"):
        return {"error": "La Roulette è in manutenzione.", "code": 503}
    if bet not in G.SP_BETS or choice not in G.ROUL_CHOICES:
        return {"error": "Puntata non valida.", "code": 400}
    if _balance(uid) < bet:
        return {"error": f"Servono {bet} $SDG per questa puntata.", "code": 400}
    luck = G.has_luck(uid, uid, "roulette")
    G._charge(uid, uid, bet)
    n = random.randint(0, 36)
    mult = G.roulette_multiplier(choice, n)
    luck_used = False
    if mult == 0 and luck and random.random() < G.LUCK_REROLL_CHANCE:
        n = random.randint(0, 36)
        mult = G.roulette_multiplier(choice, n)
        luck_used = True
    paid, notes = await G._settle(_bot(), uid, uid, "roulette", bet, bet * mult)
    notes = list(notes)
    if luck_used and paid > 0:
        notes.insert(0, "🍀 Boost Fortuna: la fortuna ti ha aiutato!")
    q = G.quest_progress(uid, uid, "roulette_play")
    return {"result": {"number": n, "color": G.roulette_color(n), "choice": choice, "mult": mult, "bet": bet,
                       "paid": paid, "net": paid - bet, "notes": _quest_note(notes, q), "balance": _balance(uid)}}


TABLE_MAX_BETS, TABLE_MAX_TOTAL = 40, 3000


def _table_mult(t: str, v: int, n: int) -> int:
    """Moltiplicatore TOTALE di una puntata del tavolo completo (0..36 e 37 = doppio zero)."""
    G = _g()
    if t == "s":
        return 36 if n == v else 0
    if n in (0, 37):
        return 0
    if t in ("red", "black"):
        return 2 if G.roulette_color(n) == t else 0
    if t == "even":
        return 2 if n % 2 == 0 else 0
    if t == "odd":
        return 2 if n % 2 == 1 else 0
    if t == "low":
        return 2 if n <= 18 else 0
    if t == "high":
        return 2 if n >= 19 else 0
    if t == "d":
        return 3 if (n - 1) // 12 + 1 == v else 0
    if t == "c":
        return 3 if (n - 1) % 3 + 1 == v else 0
    return 0


async def _roul_table(uid: int, bets):
    """Roulette americana (0 e 00) con più puntate insieme. Aperta a tutti."""
    G = _g()
    if not game_enabled("roulette"):
        return {"error": "La Roulette è in manutenzione.", "code": 503}
    if not isinstance(bets, list) or not bets or len(bets) > TABLE_MAX_BETS:
        return {"error": "Puntata non valida.", "code": 400}
    clean, total = [], 0
    for b in bets:
        try:
            t, a = str(b.get("t")), int(b.get("a"))
            v = int(b.get("v", 0) or 0)
        except (AttributeError, TypeError, ValueError):
            return {"error": "Puntata non valida.", "code": 400}
        if t not in ("s", "red", "black", "even", "odd", "low", "high", "d", "c") or a <= 0 or a % 5 or a > TABLE_MAX_TOTAL:
            return {"error": "Puntata non valida.", "code": 400}
        if (t == "s" and not 0 <= v <= 37) or (t in ("d", "c") and v not in (1, 2, 3)):
            return {"error": "Puntata non valida.", "code": 400}
        clean.append((t, v, a)); total += a
    if total > TABLE_MAX_TOTAL:
        return {"error": f"Massimo {TABLE_MAX_TOTAL} $SDG per giro.", "code": 400}
    if _balance(uid) < total:
        return {"error": f"Servono {total} $SDG per questa puntata.", "code": 400}
    luck = G.has_luck(uid, uid, "roulette")
    G._charge(uid, uid, total)

    def pay(n):
        return sum(a * _table_mult(t, v, n) for t, v, a in clean)
    n = random.randint(0, 37)
    payout, luck_used = pay(n), False
    if payout == 0 and luck and random.random() < G.LUCK_REROLL_CHANCE:
        n = random.randint(0, 37); payout = pay(n); luck_used = True
    paid, notes = await G._settle(_bot(), uid, uid, "roulette", total, payout)
    notes = list(notes)
    if luck_used and paid > 0:
        notes.insert(0, "🍀 Boost Fortuna: la fortuna ti ha aiutato!")
    q = G.quest_progress(uid, uid, "roulette_play")
    color = "green" if n in (0, 37) else G.roulette_color(n)
    return {"result": {"number": n, "color": color, "bet": total, "paid": paid, "net": paid - total,
                       "notes": _quest_note(notes, q), "balance": _balance(uid)}}


def _scr_view(uid: int, game=None) -> dict:
    G = _g()
    game = game if game is not None else SCRATCH_GAMES.get(f"{uid}_{uid}")
    out = {"success": True, "balance": _balance(uid), "cost": G.SCRATCH_COST, "picks": G.SCRATCH_PICKS,
           "free": int(get_user_data_dict(uid, uid).get("scratch_free", 0) or 0),
           "enabled": bool(game_enabled("scratch")), "active": False}
    if game and time.time() - game["ts"] < 600:
        out.update(active=True, picked=[{"i": i, "sym": game["grid"][i]} for i in game["picked"]])
    return out


async def _scr_state(uid: int):
    return _scr_view(uid)


async def _scr_buy(uid: int, name: str):
    G = _g()
    key = f"{uid}_{uid}"
    game = SCRATCH_GAMES.get(key)
    if game and time.time() - game["ts"] < 600:       # biglietto già pagato: lo si riprende, nessun nuovo addebito
        v = _scr_view(uid, game); v["resumed"] = True
        return {"view": v}
    if not game_enabled("scratch"):
        return {"error": "Il Gratta e Vinci è in manutenzione.", "code": 503}
    if _balance(uid) < G.SCRATCH_COST:
        return {"error": f"Servono {G.SCRATCH_COST} $SDG per un biglietto.", "code": 400}
    luck = G.has_luck(uid, uid, "scratch")
    u = get_user_data_dict(uid, uid)
    free = int(u.get("scratch_free", 0) or 0) > 0
    if free:                                         # biglietto gratuito vinto in precedenza
        u["scratch_free"] = int(u["scratch_free"]) - 1
        save_db()
    else:
        G._charge(uid, uid, G.SCRATCH_COST)
    grid = G.scratch_grid(luck)
    grid = [(SCRATCH_RARE if (g == "💀" and random.random() < SCRATCH_RARE_P) else g) for g in grid]
    game = {"grid": grid, "picked": [], "ts": time.time(), "name": name, "free": free}
    SCRATCH_GAMES[key] = game
    return {"view": _scr_view(uid, game)}


SCRATCH_RARE = "🎟️"          # casella rarissima: 10 biglietti gratuiti
SCRATCH_RARE_P = 0.0016
SCRATCH_RARE_TICKETS = 10


async def _scr_pick(uid: int, idx: int):
    G = _g()
    key = f"{uid}_{uid}"
    game = SCRATCH_GAMES.get(key)
    if not game:
        return {"error": "Nessun biglietto attivo: compra un nuovo biglietto.", "code": 409}
    if idx < 0 or idx > 8 or idx in game["picked"]:
        return {"error": "Casella non valida.", "code": 400}
    game["picked"].append(idx)
    game["ts"] = time.time()
    sym = game["grid"][idx]
    if len(game["picked"]) < G.SCRATCH_PICKS:
        return {"view": _scr_view(uid, game), "sym": sym, "idx": idx}
    picked_syms = [game["grid"][i] for i in game["picked"]]
    base = sum(G.SCRATCH_PRIZE.get(x, 0) for x in picked_syms)
    del SCRATCH_GAMES[key]
    cost = 0 if game.get("free") else G.SCRATCH_COST
    paid, notes = await G._settle(_bot(), uid, uid, "scratch", cost, base)
    notes = list(notes)
    u = get_user_data_dict(uid, uid)
    won_free = 0
    if SCRATCH_RARE in picked_syms:
        won_free += SCRATCH_RARE_TICKETS
        notes.insert(0, f"🎟️ Biglietto dorato! {SCRATCH_RARE_TICKETS} gratta e vinci gratis!")
    elif picked_syms.count("💰") == 3:
        won_free += 1
        notes.insert(0, "🎟️ Tre monete uguali: un gratta e vinci in regalo!")
    if won_free:
        u["scratch_free"] = int(u.get("scratch_free", 0) or 0) + won_free
        save_db()
    q = G.quest_progress(uid, uid, "scratch_play")
    res = {"grid": game["grid"], "picked": list(game["picked"]), "cost": cost, "base": base, "paid": paid,
           "net": paid - cost, "won_free": won_free, "free_left": int(u.get("scratch_free", 0) or 0),
           "notes": _quest_note(notes, q), "balance": _balance(uid)}
    return {"view": _scr_view(uid), "sym": sym, "idx": idx, "result": res}


def _wheel_view(uid: int) -> dict:
    prof = USER_DATA.get(ukey(uid)) or {}
    return {"success": True, "available": (prof.get("last_daily") or "") != today_str() or is_web_sandbox(),
            "segments": [p for p, _ in DAILY_WHEEL], "balance": _balance(uid), "admin": _is_admin_id(uid)}


async def _wheel_state(uid: int):
    return _wheel_view(uid)


async def _wheel_spin(uid: int, test: bool = False):
    if test and _is_admin_id(uid):
        # prova per l'admin: estrae un premio come al solito ma NON paga e NON consuma la giocata di oggi
        prize = spin_daily()
        segments = [p for p, _ in DAILY_WHEEL]
        return {"view": {"success": True, "prize": prize, "index": segments.index(prize), "segments": segments, "test": True,
                         "available": (get_user_data_dict(uid, uid).get("last_daily") or "") != today_str(), "balance": _balance(uid)}}
    u = get_user_data_dict(uid, uid)
    today = today_str()
    if u.get("last_daily") == today and not is_web_sandbox():
        return {"error": "Bonus giornaliero già riscosso oggi.", "code": 409, "view": _wheel_view(uid)}
    segments = [p for p, _ in DAILY_WHEEL]
    if is_v2(uid):
        prize = spin_daily()
    else:
        prize = 50                                  # utenti V1: bonus fisso, come nel bot
    if not is_web_sandbox():
        u["last_daily"] = today
    add_user_coins(uid, uid, prize)
    idx = segments.index(prize) if prize in segments else None
    return {"view": {"success": True, "prize": prize, "index": idx, "segments": segments,
                     "available": False, "balance": _balance(uid)}}


def _post_json():
    user, err = require_admin()
    if err:
        return None, None, err
    return user, (request.get_json(silent=True) or {}), None


@webapi.route("/api/slot/state", methods=["GET", "OPTIONS"])
def api_slot_state():
    if request.method == "OPTIONS":
        return "", 204
    user, err = require_admin()
    if err:
        return err
    resp, out = _avi_call(_slot_state(int(user["id"])))
    body = {"success": True}
    body.update(out or {})
    return resp if resp is not None else jsonify(body)


@webapi.route("/api/slot/spin", methods=["POST", "OPTIONS"])
def api_slot_spin():
    if request.method == "OPTIONS":
        return "", 204
    user, data, err = _post_json()
    if err:
        return err
    try:
        bet = int(data.get("bet") or 0)
    except (TypeError, ValueError):
        return _err("Puntata non valida.")
    resp, out = _avi_call(_slot_spin(int(user["id"]), bet))
    return resp if resp is not None else jsonify({"success": True, "result": out["result"]})


@webapi.route("/api/roulette/spin", methods=["POST", "OPTIONS"])
def api_roulette_spin():
    if request.method == "OPTIONS":
        return "", 204
    user, data, err = _post_json()
    if err:
        return err
    try:
        bet = int(data.get("bet"))
    except (TypeError, ValueError):
        return _err("Puntata non valida.")
    resp, out = _avi_call(_roul_spin(int(user["id"]), bet, str(data.get("choice", ""))))
    return resp if resp is not None else jsonify({"success": True, "result": out["result"]})


@webapi.route("/api/roulette/table", methods=["POST", "OPTIONS"])
def api_roulette_table():
    if request.method == "OPTIONS":
        return "", 204
    user, data, err = _post_json()
    if err:
        return err
    resp, out = _avi_call(_roul_table(int(user["id"]), data.get("bets")))
    return resp if resp is not None else jsonify({"success": True, "result": out["result"]})


@webapi.route("/api/heist/<what>", methods=["GET", "POST", "OPTIONS"])
def api_heist(what):
    if request.method == "OPTIONS":
        return "", 204
    user, err = require_admin()
    if err:
        return err
    if not is_web_sandbox():
        return _err("Disponibile solo nell'account di prova.", 403)
    import webheist
    uid = int(user["id"])
    data = request.get_json(silent=True) or {}
    if what == "state":
        coro = _social_call(webheist.state, uid)
    elif what == "start":
        coro = _social_call(webheist.start, uid, data.get("lvl", 1))
    elif what == "act":
        coro = _social_call(webheist.act, uid, str(data.get("a", ""))[:6], data)
    else:
        return _err("Azione sconosciuta.", 404)
    resp, out = _avi_call(coro)
    if resp is not None:
        return resp
    body = {"success": True}
    body.update(out)
    return jsonify(body)


@webapi.route("/api/scratch/state", methods=["GET", "OPTIONS"])
def api_scratch_state():
    if request.method == "OPTIONS":
        return "", 204
    user, err = require_admin()
    if err:
        return err
    resp, out = _avi_call(_scr_state(int(user["id"])))
    return resp if resp is not None else jsonify(out)


@webapi.route("/api/scratch/buy", methods=["POST", "OPTIONS"])
def api_scratch_buy():
    if request.method == "OPTIONS":
        return "", 204
    user, _, err = _post_json()
    if err:
        return err
    resp, out = _avi_call(_scr_buy(int(user["id"]), user.get("first_name") or "Giocatore"))
    return resp if resp is not None else jsonify(out["view"])


@webapi.route("/api/scratch/pick", methods=["POST", "OPTIONS"])
def api_scratch_pick():
    if request.method == "OPTIONS":
        return "", 204
    user, data, err = _post_json()
    if err:
        return err
    try:
        idx = int(data.get("idx"))
    except (TypeError, ValueError):
        return _err("Casella non valida.")
    resp, out = _avi_call(_scr_pick(int(user["id"]), idx))
    if resp is not None:
        return resp
    body = {"success": True, "state": out["view"], "idx": out["idx"], "sym": out["sym"]}
    if out.get("result"):
        body["result"] = out["result"]
    return jsonify(body)


@webapi.route("/api/wheel/state", methods=["GET", "OPTIONS"])
def api_wheel_state():
    if request.method == "OPTIONS":
        return "", 204
    user, err = require_admin()
    if err:
        return err
    resp, out = _avi_call(_wheel_state(int(user["id"])))
    return resp if resp is not None else jsonify(out)


@webapi.route("/api/wheel/spin", methods=["POST", "OPTIONS"])
def api_wheel_spin():
    if request.method == "OPTIONS":
        return "", 204
    user, data, err = _post_json()
    if err:
        return err
    resp, out = _avi_call(_wheel_spin(int(user["id"]), bool(data.get("test"))))
    return resp if resp is not None else jsonify(out["view"])


# ----------------------------------------------------------------------------------------------
# FASE 4: Blackjack, Wordle, Mastermind, Poker  ->  /api/g/<bj|wd|mm|pk>/<azione>   (logica in webgames.py)
# ----------------------------------------------------------------------------------------------
@webapi.route("/api/g/<game>/<action>", methods=["GET", "POST", "OPTIONS"])
def api_multi(game, action):
    if request.method == "OPTIONS":
        return "", 204
    import webgames
    fn = webgames.ACTIONS.get(game, {}).get(action)
    if fn is None:
        return _err("Azione sconosciuta.", 404)
    user, err = require_admin()
    if err:
        return err
    if (action == "state") != (request.method == "GET"):
        return _err("Metodo non consentito.", 405)
    data = request.get_json(silent=True) or {}
    resp, out = _avi_call(fn(int(user["id"]), user.get("first_name") or "Giocatore", data))
    if resp is not None:
        return resp
    body = {"success": True}
    body.update(out)
    return jsonify(body)


# ----------------------------------------------------------------------------------------------
# FASE 5: classifiche e diario (sola lettura)  ->  /api/social/leaderboard?cat=...   /api/social/diary
# ----------------------------------------------------------------------------------------------
async def _social_call(fn, *args):
    return fn(*args)


@webapi.route("/api/social/<what>", methods=["GET", "OPTIONS"])
def api_social(what):
    if request.method == "OPTIONS":
        return "", 204
    user, err = require_admin()
    if err:
        return err
    import websocial
    uid = int(user["id"])
    if what == "leaderboard":
        coro = _social_call(websocial.leaderboard, uid, request.args.get("cat", "coins"))
    elif what == "profile":
        coro = _social_call(websocial.profile, uid, str(request.args.get("pid", ""))[:24])
    elif what == "diary":
        coro = _social_call(websocial.diary, uid)
    elif what == "quests":
        coro = _social_call(websocial.quests, uid)
    elif what == "work":
        import webextra
        coro = _social_call(webextra.work_state, uid)
    elif what == "donors":
        import webextra
        coro = _social_call(webextra.donors, uid)
    elif what == "wallet":
        import webshop
        coro = _social_call(webshop.wallet, uid)
    elif what == "shop":
        import webshop
        coro = _social_call(webshop.shop, uid)
    elif what == "inventory":
        import webshop
        coro = _social_call(webshop.inventory, uid)
    else:
        return _err("Sezione sconosciuta.", 404)
    resp, out = _avi_call(coro)
    if resp is not None:
        return resp
    body = {"success": True}
    body.update(out)
    return jsonify(body)


@webapi.route("/api/social/style", methods=["POST", "OPTIONS"])
def api_social_style():
    if request.method == "OPTIONS":
        return "", 204
    user, data, err = _post_json()
    if err:
        return err
    if not _is_admin_id(user["id"]):
        return _err("Personalizzazione non ancora disponibile.", 403)
    import websocial
    resp, out = _avi_call(_social_call(websocial.set_style, int(user["id"]), data))
    if resp is not None:
        return resp
    return jsonify({"success": True, **out})


@webapi.route("/api/social/claim", methods=["POST", "OPTIONS"])
def api_quest_claim():
    if request.method == "OPTIONS":
        return "", 204
    user, data, err = _post_json()
    if err:
        return err
    import websocial
    try:
        idx = int(data.get("idx"))
    except (TypeError, ValueError):
        return _err("Missione non valida.")
    resp, out = _avi_call(_social_call(websocial.quest_claim_web, int(user["id"]), idx))
    if resp is not None:
        return resp
    body = {"success": True}
    body.update(out)
    return jsonify(body)


@webapi.route("/api/shop/<action>", methods=["POST", "OPTIONS"])
def api_shop(action):
    if request.method == "OPTIONS":
        return "", 204
    user, data, err = _post_json()
    if err:
        return err
    import webshop
    uid = int(user["id"])
    key = str(data.get("key", ""))[:32]
    if action == "buy":
        coro = _social_call(webshop.shop_buy, uid, key)
    elif action == "activate":
        coro = _social_call(webshop.inv_activate, uid, key)
    elif action == "admin":
        if not _is_admin_id(uid):
            return _err("Funzione riservata all'admin.", 403)
        coro = _social_call(webshop.admin_tool, uid, key, data.get("value"))
    else:
        return _err("Azione sconosciuta.", 404)
    resp, out = _avi_call(coro)
    if resp is not None:
        return resp
    body = {"success": True}
    body.update(out)
    return jsonify(body)


@webapi.route("/api/extra/<action>", methods=["POST", "OPTIONS"])
def api_extra(action):
    if request.method == "OPTIONS":
        return "", 204
    user, data, err = _post_json()
    if err:
        return err
    import webextra
    uid = int(user["id"])
    if action == "work_start":
        coro = _social_call(webextra.work_start, uid)
    elif action == "work_answer":
        try:
            idx = int(data.get("idx"))
        except (TypeError, ValueError):
            return _err("Risposta non valida.")
        coro = _social_call(webextra.work_answer, uid, idx)
    elif action == "donate":
        coro = _social_call(webextra.donate, uid, str(data.get("target", ""))[:32], data.get("amount"))
    else:
        return _err("Azione sconosciuta.", 404)
    resp, out = _avi_call(coro)
    if resp is not None:
        return resp
    body = {"success": True}
    body.update(out)
    return jsonify(body)
