import asyncio
import logging
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters

from config import TELEGRAM_TOKEN, ADMIN_ID
from keep_alive import start_flask
from storage import load_db, auto_restore_from_telegram, get_user_coins, add_user_coins
from handlers import system, hub, games, multiplayer, quiz

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

BOT_MAINTENANCE = False

async def maintenance_command(update, context):
    global BOT_MAINTENANCE
    user = update.effective_user
    
    if ADMIN_ID and str(user.id) != str(ADMIN_ID):
        await update.message.reply_text("⛔ Non sei autorizzato a usare questo comando.")
        return

    BOT_MAINTENANCE = not BOT_MAINTENANCE
    status = "ATTIVA 🛠️" if BOT_MAINTENANCE else "DISATTIVA ✅"
    await update.message.reply_text(f"Stato manutenzione globale: {status}", parse_mode="HTML")

async def ricarica_command(update, context):
    chat = update.effective_chat
    user = update.effective_user
    
    if ADMIN_ID and str(user.id) != str(ADMIN_ID):
        return

    if chat.type == "private":
        add_user_coins(chat.id, user.id, 1000)
        current = get_user_coins(chat.id, user.id)
        await update.message.reply_text(f"💳 <b>Test Mode:</b> +1000 $SDG! Saldo attuale: {current} $SDG", parse_mode="HTML")
    else:
        await update.message.reply_text("⚠️ Questo comando di test rapido funziona solo in chat privata con il bot.")

async def check_maintenance(update, context):
    global BOT_MAINTENANCE
    if not BOT_MAINTENANCE:
        return False
    
    user = update.effective_user
    chat = update.effective_chat
    
    # Se sei l'admin in chat privata, ignora il blocco per i test
    if ADMIN_ID and str(user.id) == str(ADMIN_ID) and chat.type == "private":
        return False
        
    # Se è un click su un bottone (CallbackQuery), bloccalo con un alert popup
    if update.callback_query:
        try:
            await update.callback_query.answer("🛠️ SdrogoBot in Manutenzione! Riprova più tardi.", show_alert=True)
        except Exception:
            pass
        return True
        
    # Se è un messaggio testuale, rispondi con l'avviso
    msg = update.message
    if msg:
        try:
            await msg.reply_text("🛠️ <b>SdrogoBot in Manutenzione!</b> Il bot è temporaneamente bloccato per aggiornamenti.", parse_mode="HTML")
        except Exception:
            pass
        return True
        
    return True

async def main_async():
    if not TELEGRAM_TOKEN:
        print("TELEGRAM_TOKEN mancante!", flush=True)
        return

    load_db()
    start_flask()

    application = Application.builder().token(TELEGRAM_TOKEN).build()
    await auto_restore_from_telegram(application.bot)

    # Middleware di blocco totale (Priorità massima al gruppo -1)
    async def maintenance_middleware(update, context):
        if await check_maintenance(update, context):
            raise Application.StopPropagation

    application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, maintenance_middleware), group=-1)
    application.add_handler(CallbackQueryHandler(maintenance_middleware), group=-1)

    application.add_handler(CommandHandler("manutenzione", maintenance_command))
    application.add_handler(CommandHandler("ricarica", ricarica_command))

    system.register(application)
    hub.register(application)
    games.register(application)
    multiplayer.register(application)
    quiz.register(application)
    system.register_text(application)

    print("SdrogoBot v6.3 con Manutenzione Totale operativo!", flush=True)

    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)
    await asyncio.Event().wait()

if __name__ == '__main__':
    try:
        asyncio.run(main_async())
    except (KeyboardInterrupt, SystemExit):
        pass
