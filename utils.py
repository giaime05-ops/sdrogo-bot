import asyncio
import contextvars
import functools
from datetime import datetime

from telegram import CallbackQuery
from telegram.error import BadRequest, RetryAfter

from config import ADMIN_ID
from state import USER_INVENTORIES, ACTIVE_TITLES, SYSTEM_FLAGS


# --- query.answer() A PROVA DI TIMEOUT ---
# Una callback query scaduta ("query is too old") fa lanciare BadRequest a query.answer(). Questa sostituzione,
# attiva per OGNI chiamata del progetto (games, hub, system, multiplayer, quiz, v2, heist...), ignora l'errore:
# l'update non si interrompe e il gioco continua.
_orig_answer = CallbackQuery.answer


async def _safe_answer(self, *args, **kwargs):
    try:
        return await _orig_answer(self, *args, **kwargs)
    except BadRequest:
        return False


CallbackQuery.answer = _safe_answer


def is_admin(user_id: int) -> bool:
    return str(user_id) == str(ADMIN_ID) if ADMIN_ID else False


# --- VISIBILITÀ V2 (dipende dalla chat in cui arriva l'update) ---
# Il tipo di chat dell'update in elaborazione viene registrato da handlers/hub.py (bind_context_and_track,
# group=-3) all'inizio di OGNI update. Le task create dagli handler (animazioni, timer) ereditano il valore.
_CHAT_TYPE = contextvars.ContextVar("sdrogo_chat_type", default=None)


def set_chat_type(chat_type):
    _CHAT_TYPE.set(chat_type)


def is_v2(user_id: int) -> bool:
    """Con UPDATE_V2_PUBLIC attivo la V2 la vedono tutti.
    Altrimenti: in chat privata solo l'admin, nei gruppi nessuno (nemmeno l'admin)."""
    if SYSTEM_FLAGS.get("UPDATE_V2_PUBLIC"):
        return True
    ctype = _CHAT_TYPE.get()
    if ctype is None or ctype == "private":     # DM (o contesto sconosciuto: job in background, mai gruppi)
        return is_admin(user_id)
    return False


# --- RITMO DELLE ANIMAZIONI (privato vs gruppo) ---
# Telegram permette circa 20 messaggi al minuto per gruppo (e 1 al secondo per chat privata): nei gruppi le
# animazioni vanno più diradate per non incappare nel flood control (HTTP 429). Il rate limiter fa il resto.
ANIM_GAP_PRIVATE = 0.9      # secondi tra due modifiche in chat privata (0.8-1.0)
ANIM_GAP_GROUP = 1.7        # secondi tra due modifiche in un gruppo (1.5-2.0)


def anim_gap(chat_id) -> float:
    try:
        return ANIM_GAP_GROUP if int(chat_id) < 0 else ANIM_GAP_PRIVATE
    except (TypeError, ValueError):
        return ANIM_GAP_GROUP


def fmt_num(n) -> str:
    """1224 -> '1.224'."""
    return f"{int(n):,}".replace(",", ".")


async def verify_user_lock(query, owner_id: int) -> bool:
    if query.from_user.id != owner_id:
        await query.answer("🛑 Questo menu appartiene a un altro giocatore! Apri il tuo con /sdrogocomm.", show_alert=True)
        return False
    return True


def get_formatted_name(chat_id: int, user_id: int, default_name: str) -> str:
    key = f"{chat_id}_{user_id}"          # i titoli attivi sono per chat
    stars_str = ""

    from storage import ukey               # import ritardato: storage non importa utils, ma resta più sicuro
    if ukey(user_id) in USER_INVENTORIES:   # le stelle appartengono al profilo unico
        stars = USER_INVENTORIES[ukey(user_id)].get("stars", 0)
        if stars > 0:
            stars_str = " " + ("⭐" * min(stars, 5))

    if key in ACTIVE_TITLES:
        title_data = ACTIVE_TITLES[key]
        if datetime.now() < title_data["expire"]:
            return f"{title_data['title']} {default_name}{stars_str}"
        else:
            del ACTIVE_TITLES[key]

    return f"{default_name}{stars_str}"


# --- EDIT ANTI-SPAM: non chiama Telegram se il contenuto non cambia ---
# TUTTE le modifiche di messaggi del bot devono passare da smart_edit / smart_edit_msg,
# altrimenti la cache potrebbe credere che un messaggio abbia ancora il vecchio testo.
_LAST_EDIT = {}
_LAST_EDIT_MAX = 3000


def _sig(text: str, keyboard) -> int:
    kb = ""
    if keyboard is not None and hasattr(keyboard, "to_json"):
        kb = keyboard.to_json()
    return hash((text, kb))


def _remember(key, sig):
    if len(_LAST_EDIT) >= _LAST_EDIT_MAX:
        for k in list(_LAST_EDIT.keys())[: _LAST_EDIT_MAX // 2]:
            _LAST_EDIT.pop(k, None)
    _LAST_EDIT[key] = sig


def _retry_after_secs(e) -> float:
    ra = e.retry_after
    return ra.total_seconds() if hasattr(ra, "total_seconds") else float(ra)


async def smart_edit(query, text: str, keyboard=None, retries: int = 2) -> bool:
    """Modifica il messaggio del callback. Salta la chiamata se testo e tastiera sono invariati,
    riprova dopo un flood control e ignora 'message is not modified'."""
    msg = query.message
    key = (msg.chat_id, msg.message_id)
    sig = _sig(text, keyboard)
    if _LAST_EDIT.get(key) == sig:
        return True
    for _ in range(retries):
        try:
            await query.edit_message_text(text, reply_markup=keyboard, parse_mode="HTML")
            _remember(key, sig)
            return True
        except RetryAfter as e:
            await asyncio.sleep(min(_retry_after_secs(e), 10) + 0.5)
        except BadRequest as e:
            if "not modified" in str(e).lower():
                _remember(key, sig)
                return True
            return False
        except Exception:
            return False
    return False


async def smart_edit_msg(bot, chat_id: int, message_id: int, text: str, keyboard=None, retries: int = 2) -> bool:
    """Come smart_edit, ma per messaggi modificati dal bot fuori da un callback (timer, job)."""
    key = (chat_id, message_id)
    sig = _sig(text, keyboard)
    if _LAST_EDIT.get(key) == sig:
        return True
    for _ in range(retries):
        try:
            await bot.edit_message_text(
                chat_id=chat_id, message_id=message_id, text=text,
                reply_markup=keyboard, parse_mode="HTML"
            )
            _remember(key, sig)
            return True
        except RetryAfter as e:
            await asyncio.sleep(min(_retry_after_secs(e), 10) + 0.5)
        except BadRequest as e:
            if "not modified" in str(e).lower():
                _remember(key, sig)
                return True
            return False
        except Exception:
            return False
    return False


# --- LOCK ASINCRONI (protezione dalle race condition con gli update concorrenti) ---
_LOCKS = {}


def _get_lock(key):
    lock = _LOCKS.get(key)
    if lock is None:
        lock = _LOCKS[key] = asyncio.Lock()
    return lock


def serialized(scope: str = "user"):
    """Esegue un handler alla volta per utente (scope='user') o per chat (scope='chat'),
    senza bloccare gli altri utenti o le altre chat."""
    def deco(fn):
        @functools.wraps(fn)
        async def wrapper(update, context, *args, **kwargs):
            if scope == "chat":
                ident = update.effective_chat.id if update.effective_chat else 0
            else:
                ident = update.effective_user.id if update.effective_user else 0
            async with _get_lock((scope, ident)):
                return await fn(update, context, *args, **kwargs)
        return wrapper
    return deco
