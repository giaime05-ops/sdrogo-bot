import hashlib
import hmac
import json
import os
import urllib.parse
from flask import Blueprint, jsonify, request
import config
import state
import storage
import economy

webapi = Blueprint('webapi', __name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", getattr(config, "TELEGRAM_TOKEN", ""))
ADMIN_ID = getattr(config, "ADMIN_ID", None)

def _get_user_balance(user_id):
    """Trova il saldo dell'utente cercando in modo dinamico in economy, storage o state."""
    # 1. Prova funzioni in economy.py
    for name in ['get_balance', 'get_saldo', 'get_user_balance', 'get_user_saldo']:
        if hasattr(economy, name):
            try:
                return getattr(economy, name)(user_id)
            except Exception:
                pass
    
    # 2. Prova funzioni in storage.py
    for name in ['get_balance', 'get_saldo', 'get_user']:
        if hasattr(storage, name):
            try:
                res = getattr(storage, name)(user_id)
                if isinstance(res, (int, float)):
                    return res
                if isinstance(res, dict):
                    return res.get('balance', res.get('saldo', 0))
            except Exception:
                pass

    # 3. Prova direttamente la cache in state.py (USER_DATA)
    uid_str = str(user_id)
    user_data = getattr(state, 'USER_DATA', {})
    if isinstance(user_data, dict):
        u_info = user_data.get(uid_str) or user_data.get(user_id)
        if isinstance(u_info, dict):
            return u_info.get('balance', u_info.get('saldo', 0))
            
    return 0

def _get_bank_balance():
    """Trova il saldo del Banco."""
    for name in ['get_bank_balance', 'get_banco', 'get_bank']:
        if hasattr(economy, name):
            try:
                return getattr(economy, name)()
            except Exception:
                pass
    if hasattr(state, 'BANCO'):
        return getattr(state, 'BANCO', 0)
    if hasattr(state, 'FLAGS') and isinstance(state.FLAGS, dict):
        return state.FLAGS.get('banco', 0)
    return 0

def _is_follia_active():
    """Verifica lo stato della Follia Sdrogo."""
    for name in ['is_follia_active', 'is_follia', 'check_follia']:
        if hasattr(economy, name):
            try:
                return getattr(economy, name)()
            except Exception:
                pass
    if hasattr(state, 'FLAGS') and isinstance(state.FLAGS, dict):
        return bool(state.FLAGS.get('follia') or state.FLAGS.get('follia_active'))
    return False

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

    balance = _get_user_balance(user_id)
    bank = _get_bank_balance()
    follia = _is_follia_active()

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
