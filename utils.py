import asyncio
import functools
from datetime import datetime

from config import ADMIN_ID
from state import USER_INVENTORIES, ACTIVE_TITLES


def is_admin(user_id: int) -> bool:
    return str(user_id) == str(ADMIN_ID) if ADMIN_ID else False


async def verify_user_lock(query, owner_id: int) -> bool:
    if query.from_user.id != owner_id:
        await query.answer("🛑 Questo menu appartiene a un altro giocatore! Apri il tuo con /sdrogocomm.", show_alert=True)
        return False
    return True


def get_formatted_name(chat_id: int, user_id: int, default_name: str) -> str:
    key = f"{chat_id}_{user_id}"
    stars_str = ""

    if key in USER_INVENTORIES:
        stars = USER_INVENTORIES[key].get("stars", 0)
        if stars > 0:
            stars_str = " " + ("⭐" * min(stars, 5))

    if key in ACTIVE_TITLES:
        title_data = ACTIVE_TITLES[key]
        if datetime.now() < title_data["expire"]:
            return f"{title_data['title']} {default_name}{stars_str}"
        else:
            del ACTIVE_TITLES[key]

    return f"{default_name}{stars_str}"


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
