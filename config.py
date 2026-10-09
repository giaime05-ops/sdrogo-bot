import os

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ADMIN_ID = os.environ.get("ADMIN_ID")
GROUP_CHAT_ID = os.environ.get("GROUP_CHAT_ID")
BACKUP_CHAT_ID = os.environ.get("BACKUP_CHAT_ID")
PORT = int(os.environ.get('PORT', 8080))

DB_FILE = "database.json"
FRASE_PENITENZA = "sono un perdente"
TARGET_MAP = {"ma1col7": "🤡"}
