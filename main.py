import os
import logging
from threading import Thread
from flask import Flask
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

# --- CONFIGURAZIONE LOGGING ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Token preso dalle variabili d'ambiente di Render (o il tuo di backup)
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8718996725:AAE18K0GA5_EWT1XKJGpLBjwUyMar3DrxPo")

# --- KEEP ALIVE SERVER (Per Render H24) ---
app = Flask('')

@app.route('/')
def home():
    return "SdrogoBot è Attivo e operativo H24!"

def run_flask():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_flask)
    t.start()

# --- TEST DI PROVA FORZATO ---
async def handle_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.from_user:
        return

    chat_id = update.message.chat_id
    message_id = update.message.message_id
    user = update.message.from_user

    # Scrive nei log di Render chi ha mandato il messaggio
    logging.info(f"Messaggio ricevuto da: {user.first_name} (@{user.username})")

    # Prova a mettere la reazione 🤡 a CHIUNQUE scriva
    try:
        await context.bot.set_message_reaction(
            chat_id=chat_id,
            message_id=message_id,
            reaction="🤡"
        )
        logging.info("Reazione 🤡 inviata con successo!")
    except Exception as e:
        # Se c'è un errore di permessi o altro, viene stampato in rosso nei log di Render
        logging.error(f"ERRORE TELEGRAM: {e}")

def main():
    keep_alive()

    if not TELEGRAM_TOKEN:
        print("ERRORE: Manca il TELEGRAM_TOKEN!")
        return

    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # Ascolta TUTTI i messaggi (testi, foto, sticker, vocali, ecc.)
    application.add_handler(MessageHandler(filters.ALL, handle_reaction))

    print("SdrogoBot avviato in modalità TEST...")
    application.run_polling()

if __name__ == '__main__':
    main()
