import asyncio
import hashlib
import logging
import os
import re
import signal

from telegram.ext import Application
from telegram.request import HTTPXRequest

from config import TELEGRAM_TOKEN
from state import FLAGS
from storage import (
    load_db, auto_restore_from_telegram, recover_sessions, backup_to_telegram, backup_now, flush_db, flush_db_sync,
)
from economy import challenge_tick, set_bot
from handlers import system, hub, games, multiplayer, quiz, v2, heist, admin_tools

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# La manutenzione vive in FLAGS["maintenance"] (state.py) ed è gestita da handlers/system.py
FLAGS.setdefault("maintenance", False)

# --- WEBHOOK (Northflank) ---
# WEBHOOK_URL = indirizzo pubblico HTTPS del servizio, es. https://site--sdrogobot--xxxx.code.run
# Telegram manda gli aggiornamenti a WEBHOOK_URL/webhook; il bot li riceve sulla porta PORT (default 8080).
# Senza WEBHOOK_URL il bot parte in polling (utile in locale) e usa il vecchio keep-alive Flask.
WEBHOOK_PATH = "webhook"


def _clean_webhook_url(raw: str) -> str:
    """Ripulisce il valore della variabile: toglie spazi, virgolette, '/webhook' già incluso e aggiunge https:// se manca.
    Telegram accetta solo indirizzi HTTPS completi, senza spazi."""
    url = (raw or "").strip().strip("'\"").strip()
    m = re.search(r"https?://[^\s\[\]()<>\"']+", url)       # es. "[https://x](https://x)" incollato come link Markdown
    if m:
        url = m.group(0)
    if not url:
        return ""
    if "://" not in url:
        url = "https://" + url
    if url.startswith("http://"):
        url = "https://" + url[len("http://"):]
    url = url.rstrip("/")
    if url.endswith("/" + WEBHOOK_PATH):
        url = url[: -len(WEBHOOK_PATH) - 1].rstrip("/")
    return url


WEBHOOK_URL = _clean_webhook_url(os.environ.get("WEBHOOK_URL"))
PORT = int(os.environ.get("PORT", "8080"))
# Segreto che Telegram allega a ogni richiesta: chi non lo conosce non può mandare finti aggiornamenti al bot
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET") or hashlib.sha256(f"sdrogo:{TELEGRAM_TOKEN}".encode()).hexdigest()[:48]


def build_application() -> Application:
    # Timeout più larghi per le chiamate a Telegram: evita httpx.ReadTimeout / telegram.error.TimedOut nei momenti lenti.
    # Il pool di connessioni deve stare ben sopra concurrent_updates (32).
    request = HTTPXRequest(
        connection_pool_size=64,
        connect_timeout=10.0,
        read_timeout=15.0,
        write_timeout=10.0,
        pool_timeout=10.0,
    )
    # Il long polling ha bisogno di un read timeout più lungo (Telegram tiene aperta la richiesta fino a 30 s)
    updates_request = HTTPXRequest(connection_pool_size=4, connect_timeout=10.0, read_timeout=40.0,
                                   write_timeout=10.0, pool_timeout=10.0)
    builder = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .request(request)
        .get_updates_request(updates_request)
        .concurrent_updates(32)          # fino a 32 update elaborati in parallelo
    )
    try:
        # Mette in coda le richieste e gestisce da solo il flood control di Telegram
        from telegram.ext import AIORateLimiter
        builder = builder.rate_limiter(AIORateLimiter(max_retries=3))
    except (ImportError, RuntimeError):
        print("⚠️ Rate limiter non disponibile: installa python-telegram-bot[job-queue,rate-limiter,webhooks].", flush=True)
    return builder.build()


async def main_async():
    if not TELEGRAM_TOKEN:
        print("TELEGRAM_TOKEN mancante!", flush=True)
        return

    load_db()
    if not WEBHOOK_URL:
        from keep_alive import start_flask      # il keep-alive serve solo in polling: in webhook la porta è del webhook
        start_flask()

    application = build_application()
    # system.register installa error handler (anche per TimedOut) e blocco manutenzione (group=-2, prima di tutto il resto)
    system.register(application)
    v2.register(application)
    hub.register(application)
    games.register(application)
    heist.register(application)          # Sdrogo Heist V3 (callback "hst_" + controllo timer)
    multiplayer.register(application)
    quiz.register(application)
    admin_tools.register(application)    # /annuncio, /dici e /storico (solo Admin, in privato)
    system.register_text(application)

    if application.job_queue:
        # Salvataggio su SQLite ogni 3 secondi (solo le righe cambiate, in un thread)
        application.job_queue.run_repeating(flush_db, interval=3, first=3)
        # Backup automatico ogni ora (fissato nella chat di backup)
        application.job_queue.run_repeating(backup_to_telegram, interval=3600, first=3600)
        # Sfida di gruppo: scadenza, annuncio finale e avvio della successiva (aggiorna il messaggio solo se è cambiato)
        application.job_queue.run_repeating(challenge_tick, interval=30, first=15)
    else:
        print("⚠️ JobQueue non disponibile: installa python-telegram-bot[job-queue]. Senza, il DB non viene salvato!", flush=True)

    await application.initialize()
    await auto_restore_from_telegram(application.bot)   # container nuovo e vuoto: ripristina dal backup fissato
    set_bot(application.bot)                            # serve a economy per avvisare in privato le quest completate
    await recover_sessions(application.bot)             # riprende le partite salvate / rimborsa quelle non riprendibili
    await application.start()

    webhook_ok = False
    if WEBHOOK_URL:
        full_url = f"{WEBHOOK_URL}/{WEBHOOK_PATH}"
        print(f"Imposto il webhook su: {full_url}", flush=True)
        try:
            await application.updater.start_webhook(
                listen="0.0.0.0",
                port=PORT,
                url_path=WEBHOOK_PATH,
                webhook_url=full_url,
                secret_token=WEBHOOK_SECRET,
                drop_pending_updates=True,
                bootstrap_retries=3,
            )
            webhook_ok = True
            print(f"SdrogoBot v8.0 operativo in WEBHOOK su {full_url} (porta {PORT}).", flush=True)
        except Exception as e:
            # Il bot non deve restare spento: se Telegram rifiuta l'indirizzo, riparte in polling e lo scrive nel log
            print(f"⚠️ Webhook rifiutato da Telegram ({e}) per l'indirizzo '{full_url}'. "
                  "Controlla la variabile WEBHOOK_URL. Riparto in POLLING.", flush=True)
            try:
                await application.updater.stop()
            except Exception:
                pass
    if not webhook_ok:
        await application.bot.delete_webhook(drop_pending_updates=True)
        await application.updater.start_polling(drop_pending_updates=True)
        print("SdrogoBot v8.0 operativo in POLLING.", flush=True)

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
    flush_db_sync()                                      # stato e partite in corso al sicuro su disco
    try:
        await asyncio.wait_for(backup_now(application.bot), timeout=20)   # copia nella chat di backup anche allo spegnimento
    except Exception as e:
        logging.warning(f"Backup di arresto non inviato: {e}")
    await application.shutdown()
    flush_db_sync()
    print("SdrogoBot arrestato, database salvato.", flush=True)


if __name__ == '__main__':
    try:
        asyncio.run(main_async())
    except (KeyboardInterrupt, SystemExit):
        pass
