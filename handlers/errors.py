import logging
from telegram import Update
from telegram.ext import CallbackQueryHandler, ContextTypes

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logging.error(msg="Eccezione non gestita durante l'update:", exc_info=context.error)
    print(f"🚨 ERRORE FATALE CRASH: {context.error}", flush=True)

async def catch_all_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print(f"🔥 RICEVUTO UN CLICK! Pulsante: {update.callback_query.data} da utente {update.effective_user.first_name}", flush=True)

def register(app):
    app.add_error_handler(error_handler)
    app.add_handler(CallbackQueryHandler(catch_all_callbacks), group=-1)
