import os
import random
import asyncio
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

# --- CONFIGURAZIONE REAZIONI E BERSAGLI ---
# Target aggiornati:
# - @manueiii -> 🙉
# - @Spoleto17 -> 🤡
REAZIONI_UTENTI = {
    "manueiii": "🙉",
    "spoleto17": "🤡"
}

# Token preso dalle variabili d'ambiente di Render
TELEGRAM_TOKEN = "8718996725:AAE18K0GA5_EWT1XKJGpLBjwUyMar3DrxPo"

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

# --- GESTIONE REAZIONI AUTOMATICHE ---
async def handle_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.from_user:
        return

    user = update.message.from_user
    chat_id = update.message.chat_id
    message_id = update.message.message_id

    # Controlla se l'utente ha un username ed è presente nel dizionario delle vittime
    if user.username and user.username.lower() in REAZIONI_UTENTI:
        username_clean = user.username.lower()
        emoji = REAZIONI_UTENTI[username_clean]

        # Mette la reazione nell'85% dei casi per simulare naturalezza
        if random.random() < 0.85:
            # Attesa di 1-2 secondi per simulare un comportamento umano
            await asyncio.sleep(random.randint(1, 2))

            try:
                await context.bot.set_message_reaction(
                    chat_id=chat_id,
                    message_id=message_id,
                    reaction=emoji
                )
                logging.info(f"Reazione {emoji} inviata a @{user.username}")
            except Exception as e:
                logging.warning(f"Impossibile inviare la reazione: {e}")

def main():
    keep_alive()

    if not TELEGRAM_TOKEN:
        print("ERRORE: Manca il TELEGRAM_TOKEN!")
        return

    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # Ascolta TUTTI i tipi di messaggio inviati nella chat
    application.add_handler(MessageHandler(filters.ALL, handle_reaction))

    print("SdrogoBot avviato...")
    application.run_polling()

if __name__ == '__main__':
    main()
