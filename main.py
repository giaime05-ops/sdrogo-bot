import os
import random
import asyncio
import logging
from threading import Thread
from flask import Flask
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from telegram.constants import ReactionEmoji

# --- CONFIGURAZIONE LOGGING ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Mappa vittime e relative emoji (in minuscolo)
TARGET_MAP = {
    "manueiii": ReactionEmoji.MONKEY_FACE,  # 🙉
    "spoleto17": ReactionEmoji.CLOWN_FACE    # 🤡
}

TELEGRAM_TOKEN = "8718996725:AAE18K0GA5_EWT1XKJGpLBjwUyMar3DrxPo"

# --- KEEP ALIVE SERVER (Flask) ---
app = Flask(__name__)

@app.route('/')
def home():
    return "SdrogoBot attivo H24!"

def run_flask():
    port = int(os.environ.get('PORT', 8080))
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    app.run(host='0.0.0.0', port=port)

# --- HANDLER REAZIONI ---
async def handle_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.from_user:
        return

    user = update.message.from_user
    username = user.username.lower() if user.username else ""

    # Verifica se l'utente è tra i target
    if username in TARGET_MAP:
        # Probabilità dell'85%
        if random.random() < 0.85:
            emoji = TARGET_MAP[username]
            
            # Delay casuale tra 1 e 2 secondi
            await asyncio.sleep(random.uniform(1.0, 2.0))

            try:
                # Usa il metodo nativo dell'oggetto message per piazzare la reazione
                await update.message.react(emoji)
                print(f"--> Reazione {emoji} inviata a @{user.username}", flush=True)
            except Exception as e:
                print(f"--> Errore durante l'invio della reazione: {e}", flush=True)

async def main_async():
    # Avvia il server Flask in background
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print("Server Flask avviato...", flush=True)

    # Inizializza l'applicazione Telegram
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.add_handler(MessageHandler(filters.ALL, handle_reaction))

    print("SdrogoBot in ascolto per i target...", flush=True)

    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)

    await asyncio.Event().wait()

if __name__ == '__main__':
    try:
        asyncio.run(main_async())
    except (KeyboardInterrupt, SystemExit):
        pass
