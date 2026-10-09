import os
import json
import logging
from datetime import datetime
from telegram.ext import ContextTypes

from config import DB_FILE, BACKUP_CHAT_ID
from state import USER_DATA

async def auto_restore_from_telegram(bot):
    global USER_DATA
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

def load_db():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r") as f:
                data = json.load(f)
            USER_DATA.clear()
            USER_DATA.update(data)
        except Exception as e:
            logging.error(f"Errore caricamento DB: {e}")
            USER_DATA.clear()

def save_db():
    try:
        with open(DB_FILE, "w") as f:
            json.dump(USER_DATA, f, indent=2)
    except Exception as e:
        logging.error(f"Errore salvataggio DB: {e}")

async def backup_to_telegram(context: ContextTypes.DEFAULT_TYPE):
    if BACKUP_CHAT_ID and os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "rb") as f:
                await context.bot.send_document(
                    chat_id=int(BACKUP_CHAT_ID),
                    document=f,
                    caption=f"Backup DB SdrogoBot - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                )
        except Exception as e:
            logging.error(f"Errore backup Telegram: {e}")

def get_user_key(chat_id: int, user_id: int) -> str:
    return f"{chat_id}_{user_id}"

def get_user_coins(chat_id: int, user_id: int) -> int:
    key = get_user_key(chat_id, user_id)
    if key not in USER_DATA:
        USER_DATA[key] = {"coins": 50, "last_daily": ""}
        if str(chat_id) != str(BACKUP_CHAT_ID):
            save_db()
    return USER_DATA[key].get("coins", 50)

def add_user_coins(chat_id: int, user_id: int, amount: int):
    if str(chat_id) == str(BACKUP_CHAT_ID):
        return
    key = get_user_key(chat_id, user_id)
    if key not in USER_DATA:
        USER_DATA[key] = {"coins": 50, "last_daily": ""}
    USER_DATA[key]["coins"] = max(0, USER_DATA[key].get("coins", 50) + amount)
    save_db()
