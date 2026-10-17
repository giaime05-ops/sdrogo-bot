import hashlib
import hmac
import json
import os
import urllib.parse
from flask import Blueprint, jsonify, request
import config
from economy import get_balance, get_bank_balance, is_follia_active

webapi = Blueprint('webapi', __name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", getattr(config, "TELEGRAM_TOKEN", ""))
ADMIN_ID = getattr(config, "ADMIN_ID", None)

def validate_telegram_data(init_data: str):
    """Verifica l'autenticità dei dati inviati dalla Mini App di Telegram tramite HMAC-SHA256."""
    if not init_data or not TELEGRAM_TOKEN:
        return None
    try:
        parsed_data = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
        if 'hash' not in parsed_data:
            return None
        
        received_hash = parsed_data.pop('hash')
        data_check_string = '\n'.join(f"{k}={v}" for k, v in sorted(parsed_data.items()))
        
        secret_key = hmac.new(b"WebAppData", TELEGRAM_TOKEN.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        
        if hmac.compare_digest(calculated_hash.lower(), received_hash.lower()):
            if 'user' in parsed_data:
                return json.loads(parsed_data['user'])
        return None
    except Exception as e:
        print(f"Errore nella validazione initData: {e}")
        return None

@webapi.route('/api/me', methods=['GET', 'POST'])
def api_me():
    """Endpoint per ottenere il saldo reale e le informazioni del giocatore."""
    init_data = request.headers.get('X-Telegram-Init-Data') or request.args.get('initData')
    
    user_data = validate_telegram_data(init_data)
    if not user_data:
        return jsonify({"success": False, "error": "Autenticazione non valida o sessione scaduta."}), 401
    
    user_id = user_data.get('id')
    
    # Blocco di sicurezza: Accesso riservato all'Admin durante la fase di sviluppo
    if ADMIN_ID and str(user_id) != str(ADMIN_ID):
        return jsonify({
            "success": False, 
            "error": "Sdrogo Arcade è attualmente in fase di manutenzione/sviluppo riservato all'admin."
        }), 403

    balance = get_balance(user_id)
    bank = get_bank_balance()
    follia = is_follia_active()

    return jsonify({
        "success": True,
        "user": {
            "id": user_id,
            "first_name": user_data.get('first_name', 'Giocatore'),
            "username": user_data.get('username', ''),
            "balance": balance
        },
        "economy": {
            "bank_balance": bank,
            "follia_active": follia
        }
    })
