import asyncio
import logging
import signal

from telegram.ext import Application

from config import TELEGRAM_TOKEN
from keep_alive import start_flask
from state import FLAGS
from storage import load_db, auto_restore_from_telegram, backup_to_telegram, flush_db, flush_db_sync
from economy import challenge_tick, set_bot
from handlers import system, hub, games, multiplayer, quiz, v2, heist, admin_tools

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# La manutenzione vive in FLAGS["maintenance"] (state.py) ed è gestita da handlers/system.py
FLAGS.setdefault("maintenance", False)


async def main_async():
    if not TELEGRAM_TOKEN:
        print("TELEGRAM_TOKEN mancante!", flush=True)
        return

    load_db()
    start_flask()

    builder = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .concurrent_updates(32)          # fino a 32 update elaborati in parallelo
        .connection_pool_size(64)        # deve essere ben sopra concurrent_updates
        .pool_timeout(10)
    )
    try:
        # Mette in coda le richieste e gestisce da solo il flood control di Telegram
        from telegram.ext import AIORateLimiter
        builder = builder.rate_limiter(AIORateLimiter(max_retries=3))
    except (ImportError, RuntimeError):
        print("⚠️ Rate limiter non disponibile: installa python-telegram-bot[job-queue,rate-limiter].", flush=True)

    application = builder.build()
    await auto_restore_from_telegram(application.bot)
    set_bot(application.bot)              # serve a economy per avvisare in privato le quest completate

    # system.register installa error handler e blocco manutenzione (group=-2, prima di tutto il resto)
    system.register(application)
    v2.register(application)
    hub.register(application)
    games.register(application)
    heist.register(application)          # Sdrogo Heist V3 (callback "hst_" + controllo timer)
    multiplayer.register(application)
    quiz.register(application)
    admin_tools.register(application)    # /annuncio e /dici (solo Admin, in privato)
    system.register_text(application)

    if application.job_queue:
        # Scrittura del database ogni 5 secondi (solo se è cambiato qualcosa)
        application.job_queue.run_repeating(flush_db, interval=5, first=5)
        # Backup automatico ogni ora (fissato nella chat di backup)
        application.job_queue.run_repeating(backup_to_telegram, interval=3600, first=3600)
        # Sfida di gruppo: scadenza, annuncio finale e avvio della successiva
        application.job_queue.run_repeating(challenge_tick, interval=30, first=15)
    else:
        print("⚠️ JobQueue non disponibile: installa python-telegram-bot[job-queue]. Senza, il DB non viene salvato!", flush=True)

    print("SdrogoBot v7.0 operativo!", flush=True)

    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)

    # Arresto pulito: Northflank manda SIGTERM a ogni deploy, e senza questo si perderebbero gli ultimi salvataggi
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    await stop.wait()

    await application.updater.stop()
    await application.stop()
    await application.shutdown()
    flush_db_sync()
    print("SdrogoBot arrestato, database salvato.", flush=True)


if __name__ == '__main__':
    try:
        asyncio.run(main_async())
    except (KeyboardInterrupt, SystemExit):
        pass
