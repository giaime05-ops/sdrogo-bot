import asyncio
import logging

from telegram.ext import Application

from config import TELEGRAM_TOKEN
from keep_alive import start_flask
from state import FLAGS
from storage import load_db, auto_restore_from_telegram, backup_to_telegram
from handlers import system, hub, games, multiplayer, quiz

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# La manutenzione vive in FLAGS["maintenance"] (state.py) ed è gestita da handlers/system.py
# (/manutenzione, maintenance_gate). Qui basta che il flag esista.
FLAGS.setdefault("maintenance", False)


async def main_async():
    if not TELEGRAM_TOKEN:
        print("TELEGRAM_TOKEN mancante!", flush=True)
        return

    load_db()
    start_flask()

    builder = Application.builder().token(TELEGRAM_TOKEN)
    try:
        # Mette in coda le richieste e gestisce da solo il flood control di Telegram
        from telegram.ext import AIORateLimiter
        builder = builder.rate_limiter(AIORateLimiter(max_retries=3))
    except (ImportError, RuntimeError):
        print("⚠️ Rate limiter non disponibile: installa python-telegram-bot[job-queue,rate-limiter].", flush=True)

    application = builder.build()
    await auto_restore_from_telegram(application.bot)

    # system.register installa error handler e blocco manutenzione (group=-2, prima di tutto il resto)
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

    print("SdrogoBot v6.6 stabile operativo!", flush=True)

    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)
    await asyncio.Event().wait()

if __name__ == '__main__':
    try:
        asyncio.run(main_async())
    except (KeyboardInterrupt, SystemExit):
        pass
