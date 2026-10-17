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
import hashlib
import hmac
import json
import logging
import math
import os
import re
import time
import urllib.parse

from flask import Blueprint, jsonify, request

import config
from state import USER_DATA, AVIATOR_GAMES, game_enabled
from storage import ukey
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
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Telegram-Init-Data, Authorization"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Max-Age"] = "600"
    response.headers["Cache-Control"] = "no-store"
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


def require_admin():
    """Restituisce (utente, None) se la richiesta è dell'admin; altrimenti (None, risposta di errore)."""
    user = validate_init_data(_init_data_from_request())
    if not user:
        return None, (jsonify({"success": False, "error": "Autenticazione non valida o scaduta."}), 401)
    if not ADMIN_ID or str(user["id"]) != str(ADMIN_ID):
        return None, (jsonify({"success": False, "error": "Sdrogo Arcade è ancora in sviluppo: per ora è riservata all'admin."}), 403)
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
    profile = USER_DATA.get(ukey(user["id"])) or {}
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
    })


# ----------------------------------------------------------------------------------------------
# Ponte verso il loop del bot: Flask gira in un altro thread, ma saldo, Banco e statistiche si toccano SOLO
# dal loop del bot (un solo thread = nessuna corsa tra due richieste o tra mini app e chat).
# ----------------------------------------------------------------------------------------------
LOOP = None


def set_runtime(loop):
    """Chiamata da main.py quando il bot è partito."""
    global LOOP
    LOOP = loop


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
    _, notes = await G._settle(None, uid, uid, "aviator", game["bet"], 0)
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
        _, notes = await G._settle(None, uid, uid, "aviator", bet, 0)
        res = {"gid": game["gid"], "result": "crash", "crash": game["crash"], "bet": bet, "paid": 0, "net": -bet,
               "late": True, "notes": _clean(notes), "balance": _balance(uid)}
    else:
        cur = G._avi_mult(el)
        paid, notes = await G._settle(None, uid, uid, "aviator", bet, int(bet * cur))
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
        if out.get("view"):
            body["state"] = out["view"]
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
