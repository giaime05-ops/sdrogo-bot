import asyncio
import logging
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ApplicationHandlerStop

from config import TELEGRAM_TOKEN, ADMIN_ID
from keep_alive import start_flask
from state import FLAGS
from storage import load_db, auto_restore_from_telegram, get_user_coins, add_user_coins, backup_to_telegram
from handlers import system, hub, games, multiplayer, quiz

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Lo stato della manutenzione vive in FLAGS["maintenance"] (state.py), così è condiviso con tutti i moduli.
FLAGS.setdefault("maintenance", False)


def is_admin(user) -> bool:
    """True solo se ADMIN_ID è configurato e coincide con l'utente."""
    return bool(user and ADMIN_ID and str(user.id) == str(ADMIN_ID))


def _command_parts(text: str):
    """Restituisce (nome_comando_minuscolo, bot_destinatario_o_None) per un testo che inizia con '/'."""
    first = text.split()[0] if text.split() else text
    name, _, target = first.partition("@")
    return name.lower(), (target.lower() or None)


async def maintenance_command(update, context):
    user = update.effective_user

    if not is_admin(user):
        await update.message.reply_text("⛔ Non sei autorizzato a usare questo comando.")
        return

    FLAGS["maintenance"] = not FLAGS["maintenance"]
    status = "ATTIVA 🛠️" if FLAGS["maintenance"] else "DISATTIVA ✅"
    extra = "\n<i>Durante la manutenzione solo tu, in chat privata, puoi usare il bot.</i>" if FLAGS["maintenance"] else ""
    await update.message.reply_text(f"Stato manutenzione globale: {status}{extra}", parse_mode="HTML")

async def ricarica_command(update, context):
    chat = update.effective_chat
    user = update.effective_user

    if not is_admin(user):
        return

    if chat.type == "private":
        add_user_coins(chat.id, user.id, 1000)
        current = get_user_coins(chat.id, user.id)
        await update.message.reply_text(f"💳 <b>Test Mode:</b> +1000 $SDG! Saldo attuale: {current} $SDG", parse_mode="HTML")
    else:
        await update.message.reply_text("⚠️ Questo comando di test rapido funziona solo in chat privata con il bot.")

async def check_maintenance(update, context) -> bool:
    """True = l'update va bloccato. Comandi: avviso. Bottoni: popup. Testo normale: silenzio."""
    if not FLAGS.get("maintenance"):
        return False

    user = update.effective_user
    chat = update.effective_chat
    msg = update.message

    # L'admin in chat privata ignora il blocco per i test
    if is_admin(user) and chat and chat.type == "private":
        return False

    # L'admin può sempre usare /manutenzione, anche in un gruppo, per spegnere la modalità
    if is_admin(user) and msg and msg.text and msg.text.startswith("/"):
        name, _ = _command_parts(msg.text)
        if name == "/manutenzione":
            return False

    # Bottoni: popup di avviso
    if update.callback_query:
        try:
            await update.callback_query.answer("🛠️ SdrogoBot in Manutenzione! Riprova più tardi.", show_alert=True)
        except Exception:
            pass
        return True

    # Comandi: avviso (ma solo se destinati a questo bot, non ad altri bot del gruppo)
    if msg and msg.text and msg.text.startswith("/"):
        _, target = _command_parts(msg.text)
        my_username = (context.bot.username or "").lower()
        if target and target != my_username:
            return False   # comando per un altro bot: non è affar nostro
        try:
            await msg.reply_text("🛠️ <b>SdrogoBot in Manutenzione!</b> Il bot è temporaneamente bloccato per aggiornamenti.", parse_mode="HTML")
        except Exception:
            pass
        return True

    # Qualunque altro update (testo normale, messaggi modificati, foto, ecc.): bloccato in silenzio
    return True

async def main_async():
    if not TELEGRAM_TOKEN:
        print("TELEGRAM_TOKEN mancante!", flush=True)
        return

    load_db()
    start_flask()

    application = Application.builder().token(TELEGRAM_TOKEN).build()
    await auto_restore_from_telegram(application.bot)

    # Middleware di blocco totale con ApplicationHandlerStop nativo (group=-1: passa prima di tutti)
    async def maintenance_middleware(update, context):
        if await check_maintenance(update, context):
            raise ApplicationHandlerStop

    # filters.ALL (senza ~COMMAND): intercetta anche i comandi
    application.add_handler(MessageHandler(filters.ALL, maintenance_middleware), group=-1)
    application.add_handler(CallbackQueryHandler(maintenance_middleware), group=-1)

    application.add_handler(CommandHandler("manutenzione", maintenance_command))
    application.add_handler(CommandHandler("ricarica", ricarica_command))

    system.register(application)
    hub.register(application)
    games.register(application)
    multiplayer.register(application)
    quiz.register(application)
    system.register_text(application)

    # Backup automatico del database ogni ora (fissato nella chat di backup)
    if application.job_queue:
        application.job_queue.run_repeating(backup_to_telegram, interval=3600, first=3600)
    else:
        print("⚠️ JobQueue non disponibile: installa python-telegram-bot[job-queue] per i backup orari.", flush=True)

    print("SdrogoBot v6.5 con Manutenzione Totale stabile operativo!", flush=True)

    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)
    await asyncio.Event().wait()

if __name__ == '__main__':
    try:
        asyncio.run(main_async())
    except (KeyboardInterrupt, SystemExit):
        pass
