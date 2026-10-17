import asyncio
import hashlib
import logging
import os
import re
import signal
import time
from collections import deque

from utils import note_flood, note_group_req

from telegram import MenuButtonWebApp, WebAppInfo
from telegram.error import RetryAfter, TimedOut, NetworkError
from telegram.ext import Application, BaseRateLimiter
from telegram.request import HTTPXRequest

from config import TELEGRAM_TOKEN, ADMIN_ID
from state import FLAGS
from storage import (
    load_db, auto_restore_from_telegram, recover_sessions, backup_to_telegram, backup_now, flush_db, flush_db_sync,
)
from economy import challenge_tick, set_bot
from handlers import system, hub, games, multiplayer, quiz, v2, heist, admin_tools
from webapi import webapi

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

FLAGS.setdefault("maintenance", False)

WEBHOOK_PATH = "webhook"


def _clean_webhook_url(raw: str) -> str:
    """Ripulisce il valore della variabile webhook."""
    url = (raw or "").strip().strip("'\"").strip()
    m = re.search(r"https?://[^\s\[\]()<>\"']+", url)
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
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET") or hashlib.sha256(f"sdrogo:{TELEGRAM_TOKEN}".encode()).hexdigest()[:48]


PERF_LOG = os.environ.get("PERF_LOG", "1") != "0"
SLOW_SECS = 1.5
_perf = {"upd_n": 0, "upd_sum": 0.0, "upd_max": 0.0, "api_n": 0, "api_sum": 0.0, "api_max": 0.0,
         "net_n": 0, "net_sum": 0.0, "net_max": 0.0, "lag_max": 0.0, "loop_max": 0.0}


def _perf_add(prefix: str, dt: float):
    _perf[prefix + "_n"] += 1
    _perf[prefix + "_sum"] += dt
    _perf[prefix + "_max"] = max(_perf[prefix + "_max"], dt)


class TimedRequest(HTTPXRequest):
    """Misura la durata della sola richiesta di rete verso Telegram."""
    async def do_request(self, *args, **kwargs):
        t = time.perf_counter()
        try:
            return await super().do_request(*args, **kwargs)
        finally:
            _perf_add("net", time.perf_counter() - t)


def _install_perf(application):
    from telegram.ext import ExtBot
    orig_post = ExtBot._do_post

    async def timed_post(self, endpoint, *args, **kwargs):
        t = time.perf_counter()
        try:
            return await orig_post(self, endpoint, *args, **kwargs)
        finally:
            dt = time.perf_counter() - t
            _perf_add("api", dt)
            if dt > SLOW_SECS:
                logging.warning(f"🐢 Chiamata a Telegram lenta: {endpoint} ha impiegato {dt:.2f}s (rete + coda del limitatore)")
    ExtBot._do_post = timed_post

    from telegram import Bot
    from telegram.error import RetryAfter
    orig_base_post = Bot._do_post

    async def base_post(self, endpoint, *args, **kwargs):
        try:
            return await orig_base_post(self, endpoint, *args, **kwargs)
        except RetryAfter as e:
            ra = e.retry_after
            secs = int(ra.total_seconds()) if hasattr(ra, "total_seconds") else int(ra)
            data = args[0] if args else kwargs.get("data")
            try:
                cid = int((data or {}).get("chat_id"))
                note_flood(cid, secs)
            except (TypeError, ValueError, AttributeError):
                cid = None
            logging.warning(f"⛔ FLOOD CONTROL: Telegram ha imposto {secs}s di pausa (richiesta {endpoint}, chat {cid}). "
                            "Quella chat resta in pausa (le altre no) e le sue animazioni si riducono per un minuto.")
            raise
    Bot._do_post = base_post

    orig_process = application.process_update

    async def timed_process(update):
        t = time.perf_counter()
        try:
            msg = getattr(update, "effective_message", None)
            if msg is not None and getattr(msg, "date", None) is not None and not update.callback_query:
                delay = time.time() - msg.date.timestamp()
                if 0 <= delay < 3600:
                    _perf["lag_max"] = max(_perf["lag_max"], delay)
            return await orig_process(update)
        finally:
            dt = time.perf_counter() - t
            _perf_add("upd", dt)
            if dt > SLOW_SECS:
                cq = getattr(update, "callback_query", None)
                what = f"tasto {cq.data}" if cq else (getattr(getattr(update, 'effective_message', None), 'text', None) or "update")
                logging.warning(f"🐢 Update lento: {str(what)[:60]} ha impiegato {dt:.2f}s")
    application.process_update = timed_process


async def _loop_probe():
    """Misura quanto l'event loop resta bloccato."""
    while True:
        t = time.perf_counter()
        await asyncio.sleep(0.5)
        lag = time.perf_counter() - t - 0.5
        _perf["loop_max"] = max(_perf["loop_max"], lag)
        if lag > 0.4:
            logging.warning(f"🧊 Il bot è rimasto bloccato per {lag:.2f}s (codice pesante o disco lento)")


async def _perf_report(application):
    while True:
        await asyncio.sleep(60)
        p = _perf
        if p["upd_n"] or p["api_n"]:
            avg = lambda k: (p[k + "_sum"] / p[k + "_n"] * 1000) if p[k + "_n"] else 0
            print(
                f"📊 60s | update {p['upd_n']} (media {avg('upd'):.0f} ms, max {p['upd_max']:.2f}s) | "
                f"api {p['api_n']} (media {avg('api'):.0f} ms, max {p['api_max']:.2f}s) | "
                f"rete {p['net_n']} (media {avg('net'):.0f} ms, max {p['net_max']:.2f}s) | "
                f"ritardo consegna max {p['lag_max']:.1f}s | loop bloccato max {p['loop_max']:.2f}s | "
                f"coda {application.update_queue.qsize()}",
                flush=True)
        for k in p:
            p[k] = 0 if k.endswith("_n") else 0.0


BACKUP_EVERY = int(os.environ.get("BACKUP_EVERY", "900"))
GROUP_GAP = float(os.environ.get("GROUP_GAP", "1.0"))
PRIVATE_GAP = float(os.environ.get("PRIVATE_GAP", "0"))
GROUP_MAX_PER_MIN = int(os.environ.get("GROUP_MAX_PER_MIN", "20"))

_SEND_ENDPOINTS = {"sendMessage", "sendPhoto", "sendDocument", "sendAnimation", "sendSticker", "sendDice",
                    "sendVideo", "sendAudio", "sendVoice", "sendPoll", "sendMediaGroup"}
_THROTTLED = _SEND_ENDPOINTS | {"editMessageText", "editMessageReplyMarkup", "editMessageCaption", "deleteMessage",
                                "pinChatMessage", "unpinChatMessage", "editMessageMedia"}


class ChatRateLimiter(BaseRateLimiter):
    def __init__(self, group_gap=GROUP_GAP, private_gap=PRIVATE_GAP, sends_per_min=GROUP_MAX_PER_MIN, max_retries=3):
        self.group_gap, self.private_gap, self.sends_per_min, self.max_retries = group_gap, private_gap, sends_per_min, max_retries
        self._locks = {}
        self._next_ok = {}
        self._blocked = {}
        self._sends = {}

    async def initialize(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    @staticmethod
    def _chat_of(endpoint, data):
        if endpoint not in _THROTTLED or not isinstance(data, dict):
            return None
        cid = data.get("chat_id")
        if cid is None:
            return None
        try:
            return int(cid)
        except (TypeError, ValueError):
            return str(cid)

    async def _wait_turn(self, chat, endpoint):
        group = isinstance(chat, str) or chat < 0
        lock = self._locks.get(chat)
        if lock is None:
            lock = self._locks[chat] = asyncio.Lock()
        async with lock:
            now = time.monotonic()
            wait = max(self._next_ok.get(chat, 0) - now, self._blocked.get(chat, 0) - now, 0.0)
            is_send = group
            if is_send:
                dq = self._sends.setdefault(chat, deque())
                while dq and now - dq[0] > 60:
                    dq.popleft()
                if self.sends_per_min and len(dq) >= self.sends_per_min:
                    wait = max(wait, dq[0] + 60 - now)
            if wait > 0:
                await asyncio.sleep(wait)
            if group:
                note_group_req(chat)
            t = time.monotonic()
            self._next_ok[chat] = t + (self.group_gap if group else self.private_gap)
            if is_send:
                self._sends.setdefault(chat, deque()).append(t)

    async def process_request(self, callback, args, kwargs, endpoint, data, rate_limit_args=None, **extra):
        try:
            chat = self._chat_of(endpoint, data)
            if chat is not None:
                await self._wait_turn(chat, endpoint)
        except Exception as e:
            logging.warning(f"Limitatore: {e}")
            chat = None
        for attempt in range(self.max_retries + 1):
            try:
                return await callback(*args, **kwargs)
            except RetryAfter as e:
                ra = e.retry_after
                secs = (ra.total_seconds() if hasattr(ra, "total_seconds") else float(ra)) + 0.5
                logging.warning(f"⛔ Flood control su chat {chat} ({'gruppo' if isinstance(chat, int) and chat < 0 else 'privato'}): "
                                f"{endpoint}, pausa {secs:.0f}s")
                if chat is not None:
                    self._blocked[chat] = time.monotonic() + secs
                if attempt >= self.max_retries:
                    raise
                await asyncio.sleep(secs)


def build_application() -> Application:
    request = TimedRequest(
        connection_pool_size=64,
        connect_timeout=10.0,
        read_timeout=15.0,
        write_timeout=10.0,
        pool_timeout=10.0,
    )
    updates_request = HTTPXRequest(connection_pool_size=4, connect_timeout=10.0, read_timeout=40.0,
                                   write_timeout=10.0, pool_timeout=10.0)
    builder = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .request(request)
        .get_updates_request(updates_request)
        .concurrent_updates(32)
    )
    try:
        if os.environ.get("LIMITER", "").lower() == "ptb":
            from telegram.ext import AIORateLimiter
            builder = builder.rate_limiter(AIORateLimiter(max_retries=3))
        else:
            builder = builder.rate_limiter(ChatRateLimiter())
    except (ImportError, RuntimeError):
        print("⚠️ Rate limiter non disponibile: installa python-telegram-bot[job-queue,rate-limiter,webhooks].", flush=True)

    return builder.build()


async def main_async():
    if not TELEGRAM_TOKEN:
        print("TELEGRAM_TOKEN mancante!", flush=True)
        return

    load_db()
    if not WEBHOOK_URL:
        from keep_alive import app, start_flask

        @app.after_request
        def add_cors_headers(response):
            response.headers['Access-Control-Allow-Origin'] = '*'
            response.headers['Access-Control-Allow-Headers'] = 'Content-Type, X-Telegram-Init-Data'
            response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
            return response

        @app.route('/api/<path:dummy>', methods=['OPTIONS'])
        def options_handler(dummy):
            return '', 200

        try:
            app.register_blueprint(webapi)
        except ValueError:
            pass

        start_flask()

    application = build_application()
    system.register(application)
    v2.register(application)
    hub.register(application)
    games.register(application)
    heist.register(application)
    multiplayer.register(application)
    quiz.register(application)
    admin_tools.register(application)
    system.register_text(application)

    if application.job_queue:
        application.job_queue.run_repeating(flush_db, interval=3, first=3)
        application.job_queue.run_repeating(backup_to_telegram, interval=BACKUP_EVERY, first=BACKUP_EVERY)
        application.job_queue.run_repeating(challenge_tick, interval=30, first=15)
    else:
        print("⚠️ JobQueue non disponibile: installa python-telegram-bot[job-queue].", flush=True)

    for attempt in range(1, 9):
        try:
            await application.initialize()
            break
        except (TimedOut, NetworkError) as e:
            print(f"⚠️ Telegram non risponde all'avvio (tentativo {attempt}/8): {e}. Riprovo tra 5 s...", flush=True)
            if attempt == 8:
                raise
            await asyncio.sleep(5)
    await auto_restore_from_telegram(application.bot)
    set_bot(application.bot)
    await recover_sessions(application.bot)
    await application.start()

    # IMPOSTAZIONE MENU BUTTON: VISIBILE SOLO PER L'ADMIN IN CHAT PRIVATA
    if ADMIN_ID:
        try:
            await application.bot.set_chat_menu_button(
                chat_id=int(ADMIN_ID),
                menu_button=MenuButtonWebApp(
                    text="🎮 Apri Arcade",
                    web_app=WebAppInfo(url="https://giaime05-ops.github.io/sdrogobot-arcade/")
                )
            )
            print(f"Menu Button Arcade impostato riservatamente per ADMIN_ID {ADMIN_ID}", flush=True)
        except Exception as e:
            print(f"⚠️ Impossibile impostare menu button admin: {e}", flush=True)

    if PERF_LOG:
        try:
            _install_perf(application)
            asyncio.get_running_loop().create_task(_loop_probe())
            asyncio.get_running_loop().create_task(_perf_report(application))
        except Exception as e:
            print(f"⚠️ Diagnostica prestazioni non attivata: {e}", flush=True)

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
            print(f"⚠️ Webhook rifiutato da Telegram ({e}). Riparto in POLLING.", flush=True)
            try:
                await application.updater.stop()
            except Exception:
                pass
    if not webhook_ok:
        await application.bot.delete_webhook(drop_pending_updates=True)
        await application.updater.start_polling(drop_pending_updates=True)
        print("SdrogoBot v8.0 operativo in POLLING.", flush=True)

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
    flush_db_sync()
    try:
        await asyncio.wait_for(backup_now(application.bot), timeout=20)
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
