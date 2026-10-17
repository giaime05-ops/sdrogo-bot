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
import time
import urllib.parse

from flask import Blueprint, jsonify, request

import config
from state import USER_DATA, AVIATOR_GAMES, game_enabled
from storage import ukey, get_user_data_dict, add_user_coins
import economy
from economy import today_str, spin_daily, DAILY_WHEEL
from state import SCRATCH_GAMES
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
        "daily": {"available": (profile.get("last_daily") or "") != today_str()},
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


async def _slot_spin(uid: int, bet: int):
    G = _g()
    if not game_enabled("slot"):
        return {"error": "La Slot è in manutenzione.", "code": 503}
    if bet not in G.SLOT_BETS:
        return {"error": "Puntata non valida.", "code": 400}
    if _balance(uid) < bet:
        return {"error": f"Servono {bet} $SDG per questa puntata.", "code": 400}
    luck = G.has_luck(uid, uid, "slot")
    G._charge(uid, uid, bet)
    reels = [random.choice(G.SLOT_SYMBOLS) for _ in range(3)]
    payout, kind = G.slot_payout(reels, bet)
    luck_used = False
    if kind == "none" and luck and random.random() < G.LUCK_REROLL_CHANCE:
        reels = [random.choice(G.SLOT_SYMBOLS) for _ in range(3)]
        payout, kind = G.slot_payout(reels, bet)
        luck_used = True
    paid, notes = await G._settle(_bot(), uid, uid, "slot", bet, payout)
    notes = list(notes)
    if luck_used:
        notes.insert(0, "🍀 Boost Fortuna: giro rilanciato!")
    q = G.quest_progress(uid, uid, "slot_play")
    return {"result": {"reels": reels, "kind": kind, "bet": bet, "paid": paid, "net": paid - bet,
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
    if luck_used:
        notes.insert(0, "🍀 Boost Fortuna: giro rilanciato!")
    q = G.quest_progress(uid, uid, "roulette_play")
    return {"result": {"number": n, "color": G.roulette_color(n), "choice": choice, "mult": mult, "bet": bet,
                       "paid": paid, "net": paid - bet, "notes": _quest_note(notes, q), "balance": _balance(uid)}}


def _scr_view(uid: int, game=None) -> dict:
    G = _g()
    game = game if game is not None else SCRATCH_GAMES.get(f"{uid}_{uid}")
    out = {"success": True, "balance": _balance(uid), "cost": G.SCRATCH_COST, "picks": G.SCRATCH_PICKS,
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
    G._charge(uid, uid, G.SCRATCH_COST)
    game = {"grid": G.scratch_grid(luck), "picked": [], "ts": time.time(), "name": name}
    SCRATCH_GAMES[key] = game
    return {"view": _scr_view(uid, game)}


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
    base = sum(G.SCRATCH_PRIZE[game["grid"][i]] for i in game["picked"])
    del SCRATCH_GAMES[key]
    paid, notes = await G._settle(_bot(), uid, uid, "scratch", G.SCRATCH_COST, base)
    q = G.quest_progress(uid, uid, "scratch_play")
    res = {"grid": game["grid"], "picked": list(game["picked"]), "cost": G.SCRATCH_COST, "base": base, "paid": paid,
           "net": paid - G.SCRATCH_COST, "notes": _quest_note(list(notes), q), "balance": _balance(uid)}
    return {"view": _scr_view(uid), "sym": sym, "idx": idx, "result": res}


def _wheel_view(uid: int) -> dict:
    prof = USER_DATA.get(ukey(uid)) or {}
    return {"success": True, "available": (prof.get("last_daily") or "") != today_str(),
            "segments": [p for p, _ in DAILY_WHEEL], "balance": _balance(uid)}


async def _wheel_state(uid: int):
    return _wheel_view(uid)


async def _wheel_spin(uid: int):
    u = get_user_data_dict(uid, uid)
    today = today_str()
    if u.get("last_daily") == today:
        return {"error": "Bonus giornaliero già riscosso oggi.", "code": 409, "view": _wheel_view(uid)}
    segments = [p for p, _ in DAILY_WHEEL]
    if is_v2(uid):
        prize = spin_daily()
    else:
        prize = 50                                  # utenti V1: bonus fisso, come nel bot
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


@webapi.route("/api/slot/spin", methods=["POST", "OPTIONS"])
def api_slot_spin():
    if request.method == "OPTIONS":
        return "", 204
    user, data, err = _post_json()
    if err:
        return err
    try:
        bet = int(data.get("bet"))
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
    user, _, err = _post_json()
    if err:
        return err
    resp, out = _avi_call(_wheel_spin(int(user["id"])))
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
    elif what == "diary":
        coro = _social_call(websocial.diary, uid)
    else:
        return _err("Sezione sconosciuta.", 404)
    resp, out = _avi_call(coro)
    if resp is not None:
        return resp
    body = {"success": True}
    body.update(out)
    return jsonify(body)
