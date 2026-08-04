import os
import logging
import asyncio
from threading import Thread
from flask import Flask
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

# --- CONFIGURAZIONE LOGGING ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Token preso dalle variabili d'ambiente o backup
TELEGRAM_TOKEN = "8718996725:AAE18K0GA5_EWT1XKJGpLBjwUyMar3DrxPo"

# --- KEEP ALIVE SERVER (Flask) ---
app = Flask(__name__)

@app.route('/')
def home():
    return "SdrogoBot è attivo e in ascolto!"

def run_flask():
    port = int(os.environ.get('PORT', 8080))
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    app.run(host='0.0.0.0', port=port)

# --- HANDLER MESSAGGI (TEST FORZATO 🤡) ---
async def handle_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.from_user:
        return

    chat_id = update.message.chat_id
    message_id = update.message.message_id
    user = update.message.from_user

    print(f"--> MESSAGGIO RICEVUTO DA: {user.first_name} (@{user.username})", flush=True)

    try:
        await context.bot.set_message_reaction(
            chat_id=chat_id,
            message_id=message_id,
            reaction="🤡"
        )
        print("--> REAZIONE 🤡 INVIATA CON SUCCESSO!", flush=True)
    except Exception as e:
        print(f"--> ERRORE REAZIONE TELEGRAM: {e}", flush=True)

async def main_async():
    # Avvia Flask in background
    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print("Server Flask avviato...", flush=True)

    # Inizializza l'applicazione Telegram
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.add_handler(MessageHandler(filters.ALL, handle_reaction))

    print("Inizio ascolto messaggi con Asyncio...", flush=True)

    # Avvio ed esecuzione del polling compatibile con Python 3.14
    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)

    # Mantiene il bot in esecuzione per sempre
    await asyncio.Event().wait()

if __name__ == '__main__':
    try:
        asyncio.run(main_async())
    except (KeyboardInterrupt, SystemExit):
        pass
