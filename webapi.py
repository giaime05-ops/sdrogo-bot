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
import hashlib
import hmac
import json
import logging
import os
import time
import urllib.parse

from flask import Blueprint, jsonify, request

import config
from state import USER_DATA
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
    profile = USER_DATA.get(uid) or {}
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
