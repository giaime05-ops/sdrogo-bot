import asyncio
import logging

from telegram.ext import Application, CommandHandler

from config import TELEGRAM_TOKEN
from keep_alive import start_flask
from storage import load_db, auto_restore_from_telegram, get_user_coins, add_user_coins
from handlers import system, hub, games, multiplayer, quiz

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# --- STATO GLOBALE MANUTENZIONE & ADMIN ---
BOT_MAINTENANCE = False
ADMIN_USER_ID = None  # Verrà impostato automaticamente al primo avvio o comando admin

async def maintenance_command(update, context):
    global BOT_MAINTENANCE, ADMIN_USER_ID
    user_id = update.effective_user.id
    
    # Se non è impostato, il primo che usa il comando diventa l'admin principale
    if ADMIN_USER_ID is None:
        ADMIN_USER_ID = user_id

    if user_id != ADMIN_USER_ID:
        await update.message.reply_text("⛔ Non sei autorizzato a usare questo comando.")
        return

    args = context.args
    if not args:
        status = "ATTIVA 🛠️" if BOT_MAINTENANCE else "DISATTIVA ✅"
        await update.message.reply_text(f"Stato manutenzione: {status}\nUsa /manutenzione on oppure /manutenzione off")
        return

    action = args[0].lower()
    if action == "on":
        BOT_MAINTENANCE = True
        await update.message.reply_text("🛠️ <b>Manutenzione attivata!</b> Il bot risponderà solo che è in aggiornamento.", parse_mode="HTML")
    elif action == "off":
        BOT_MAINTENANCE = False
        await update.message.reply_text("✅ <b>Manutenzione disattivata!</b> Il bot è di nuovo operativo.", parse_mode="HTML")

async def ricarica_command(update, context):
    """Comando per dare crediti infiniti/ricarica rapida all'admin in chat privata."""
    chat = update.effective_chat
    user = update.effective_user
    global ADMIN_USER_ID
    if ADMIN_USER_ID is None:
        ADMIN_USER_ID = user.id

    if user.id != ADMIN_USER_ID:
        return

    if chat.type == "private":
        add_user_coins(chat.id, user.id, 1000)
        current = get_user_coins(chat.id, user.id)
        await update.message.reply_text(f"💳 <b>Test Mode:</b> Aggiunti +1000 $SDG! Saldo attuale: {current} $SDG", parse_mode="HTML")
    else:
        await update.message.reply_text("⚠️ Questo comando di test rapido funziona solo in chat privata con il bot.")

async def main_async():
    if not TELEGRAM_TOKEN:
        print("TELEGRAM_TOKEN mancante!", flush=True)
        return

    load_db()
    start_flask()

    application = Application.builder().token(TELEGRAM_TOKEN).build()
    await auto_restore_from_telegram(application.bot)

    # Registrazione comandi admin speciali
    application.add_handler(CommandHandler("manutenzione", maintenance_command))
    application.add_handler(CommandHandler("ricarica", ricarica_command))

    system.register(application)
    hub.register(application)
    games.register(application)
    multiplayer.register(application)
    quiz.register(application)
    system.register_text(application)     # sempre ULTIMO

    print("SdrogoBot v6.0 pronto all'uso!", flush=True)

    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)
    await asyncio.Event().wait()


if __name__ == '__main__':
    try:
        asyncio.run(main_async())
    except (KeyboardInterrupt, SystemExit):
        pass
