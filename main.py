import asyncio
import hashlib
import logging
import os
import re
import signal
import time
from collections import deque

from utils import note_flood, note_group_req, PENDING_GROUP

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


# ======================================================================
# DIAGNOSTICA DELLE PRESTAZIONI (PERF_LOG=0 per spegnerla)
# Ogni 60 secondi, se il bot ha lavorato, scrive nel log UNA riga "📊" che dice dove va il tempo:
#   update  = quanto ci mette il bot a rispondere a un tocco/comando (codice + attese di Telegram)
#   api     = durata totale delle chiamate a Telegram, compresa l'eventuale coda del limitatore di velocità
#   rete    = durata della sola richiesta di rete a Telegram (senza la coda)
#   ritardo = quanto Telegram ha impiegato a consegnare il messaggio al bot
#   loop    = quanto è rimasto "congelato" il bot (se alto, il problema è nel codice o nel disco)
# Le singole operazioni lente (oltre 1,5 s) vengono scritte subito con 🐢.
# ======================================================================
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
            if dt > SLOW_SECS and endpoint != "getUpdates":      # getUpdates è l'attesa normale dei messaggi (~10 s): non è lentezza
                logging.warning(f"🐢 Chiamata a Telegram lenta: {endpoint} ha impiegato {dt:.2f}s (rete + coda del limitatore)")
    ExtBot._do_post = timed_post

    # Il limitatore ritenta da solo quando Telegram risponde 429 e, nel frattempo, BLOCCA TUTTE le richieste del bot:
    # qui lo rendiamo visibile nel log.
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
    """Misura quanto l'event loop resta bloccato (se lo è, nessun giocatore riceve risposte)."""
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


# ======================================================================
# LIMITATORE DI VELOCITÀ PER CHAT
# Il limitatore standard di PTB (AIORateLimiter) ha due difetti per questo bot:
#   1) quando Telegram risponde 429 ("flood control") ferma TUTTE le richieste del bot, anche quelle degli altri gruppi,
#      delle chat private e i semplici "answerCallbackQuery" (il cerchietto del tasto): così un solo gruppo congela tutto;
#   2) conta come "messaggi" anche le modifiche e permette raffiche di 20 modifiche di fila, che Telegram punisce.
# Questo limitatore: ogni chat ha la sua fila (le chat non si disturbano a vicenda); nei gruppi tiene un intervallo minimo
# tra una richiesta e l'altra (GROUP_GAP) e un tetto di NUOVI messaggi al minuto (GROUP_MAX_PER_MIN); le richieste senza chat
# (answerCallbackQuery...) passano subito; un 429 mette in pausa SOLO la chat interessata.
# ======================================================================
ARCADE_URL = os.environ.get("ARCADE_URL", "https://giaime05-ops.github.io/sdrogobot-arcade/")   # indirizzo della mini app
BACKUP_EVERY = int(os.environ.get("BACKUP_EVERY", "900"))     # secondi tra due backup su Telegram (default 15 minuti)
GROUP_GAP = float(os.environ.get("GROUP_GAP", "1.0"))              # secondi minimi tra due richieste nello stesso gruppo
PRIVATE_GAP = float(os.environ.get("PRIVATE_GAP", "0"))          # idem in chat privata
GROUP_MAX_PER_MIN = int(os.environ.get("GROUP_MAX_PER_MIN", "20"))  # NUOVI messaggi al minuto per gruppo (Telegram: 20)

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
            return None                       # modifiche di messaggi inline, risposte ai tasti...: nessuna coda
        try:
            return int(cid)
        except (TypeError, ValueError):
            return str(cid)

    async def _wait_turn(self, chat, endpoint):
        group = isinstance(chat, str) or chat < 0
        if group:
            PENDING_GROUP[chat] = PENDING_GROUP.get(chat, 0) + 1
        try:
            await self._wait_turn_inner(chat, endpoint, group)
        finally:
            if group:
                PENDING_GROUP[chat] = max(0, PENDING_GROUP.get(chat, 1) - 1)

    async def _wait_turn_inner(self, chat, endpoint, group):
        lock = self._locks.get(chat)
        if lock is None:
            lock = self._locks[chat] = asyncio.Lock()
        async with lock:                      # la fila è in ordine di arrivo
            now = time.monotonic()
            wait = max(self._next_ok.get(chat, 0) - now, self._blocked.get(chat, 0) - now, 0.0)
            is_send = group                       # nei gruppi OGNI richiesta (anche le modifiche) conta nel tetto al minuto
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
        except Exception as e:                # mai bloccare una richiesta per un errore del limitatore
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
                    self._blocked[chat] = time.monotonic() + secs      # pausa solo per questa chat
                if attempt >= self.max_retries:
                    raise
                await asyncio.sleep(secs)


def build_application() -> Application:
    # Timeout più larghi per le chiamate a Telegram: evita httpx.ReadTimeout / telegram.error.TimedOut nei momenti lenti.
    # Il pool di connessioni deve stare ben sopra concurrent_updates (32).
    request = TimedRequest(
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
        # Prima della v8 il bot NON usava il limitatore globale di PTB (AIORateLimiter): ogni chiamata gestiva da sola il 429.
        # Il limitatore globale, quando Telegram impone una pausa (es. 16 s) a UN gruppo, ferma TUTTE le richieste del bot
        # (anche gli altri giocatori e le chat private): è la causa dei blocchi visti nei log ("FLOOD CONTROL ... fermo per tutti").
        # Il limitatore per chat (default) sospende invece solo la chat colpita. LIMITER=ptb ripristina quello standard.
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
        from keep_alive import app as flask_app, start_flask   # il keep-alive serve solo in polling: in webhook la porta è del webhook
        try:
            from webapi import webapi              # API della mini app (CORS e controllo identità sono dentro webapi.py)
            flask_app.register_blueprint(webapi)
        except ValueError:
            pass                                   # già registrato
        except Exception as e:                     # un problema della mini app non deve mai fermare il bot
            print(f"⚠️ API della mini app non attivata: {e}", flush=True)
        start_flask()
    else:
        print("ℹ️ WEBHOOK_URL impostato: le API della mini app sono attive solo in polling (senza WEBHOOK_URL).", flush=True)

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
        application.job_queue.run_repeating(backup_to_telegram, interval=BACKUP_EVERY, first=BACKUP_EVERY)
        # Sfida di gruppo: scadenza, annuncio finale e avvio della successiva (aggiorna il messaggio solo se è cambiato)
        application.job_queue.run_repeating(challenge_tick, interval=30, first=15)
    else:
        print("⚠️ JobQueue non disponibile: installa python-telegram-bot[job-queue]. Senza, il DB non viene salvato!", flush=True)

    # Avvio robusto: se Telegram non risponde subito (rete lenta, riavvio del container) si riprova invece di spegnersi.
    for attempt in range(1, 9):
        try:
            await application.initialize()
            break
        except (TimedOut, NetworkError) as e:
            print(f"⚠️ Telegram non risponde all'avvio (tentativo {attempt}/8): {e}. Riprovo tra 5 s...", flush=True)
            if attempt == 8:
                raise
            await asyncio.sleep(5)
    await auto_restore_from_telegram(application.bot)   # container nuovo e vuoto: ripristina dal backup fissato
    set_bot(application.bot)                            # serve a economy per avvisare in privato le quest completate
    await recover_sessions(application.bot)             # riprende le partite salvate / rimborsa quelle non riprendibili
    await application.start()

    # Pulsante "Apri Arcade": visibile SOLO nella chat privata dell'admin
    if ADMIN_ID and ARCADE_URL:
        try:
            await application.bot.set_chat_menu_button(
                chat_id=int(ADMIN_ID),
                menu_button=MenuButtonWebApp(text="🎮 Apri Arcade", web_app=WebAppInfo(url=ARCADE_URL)))
            print(f"Pulsante Arcade impostato per l'admin ({ARCADE_URL}).", flush=True)
        except Exception as e:
            print(f"⚠️ Impossibile impostare il pulsante Arcade: {e}", flush=True)

    if PERF_LOG:
        try:
            _install_perf(application)
            asyncio.get_running_loop().create_task(_loop_probe())
            asyncio.get_running_loop().create_task(_perf_report(application))
        except Exception as e:                        # la diagnostica non deve mai fermare il bot
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
