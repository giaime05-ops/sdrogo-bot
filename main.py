import asyncio
import logging

from telegram.ext import Application

from config import TELEGRAM_TOKEN
from keep_alive import start_flask
from storage import load_db, auto_restore_from_telegram
from handlers import system, hub, games, multiplayer, quiz

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)


async def main_async():
    if not TELEGRAM_TOKEN:
        print("TELEGRAM_TOKEN mancante!", flush=True)
        return

    load_db()
    start_flask()

    application = Application.builder().token(TELEGRAM_TOKEN).build()
    await auto_restore_from_telegram(application.bot)

    system.register(application)
    hub.register(application)
    games.register(application)
    multiplayer.register(application)
    quiz.register(application)
    system.register_text(application)     # sempre ULTIMO

    print("SdrogoBot v5.2 pronto all'uso!", flush=True)

    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)
    await asyncio.Event().wait()


if __name__ == '__main__':
    try:
        asyncio.run(main_async())
    except (KeyboardInterrupt, SystemExit):
        pass
