"""Strumenti economia riservati all'Admin: sfida a numeri liberi, reset dei saldi, interruttore Follia.

/sfida <quota> <giorni>      avvia la sfida di gruppo con numeri liberi (es. /sfida 50000 7)
/resettutti [quota]          TUTTI i giocatori a `quota` (default 2000) + Banco a 1000, con conferma
/resetbanco [quota]          solo il Banco (default 1000)
/resetsoldi @utente [quota]  un solo giocatore (default 2000)
/resetauto                   mostra/annulla il reset automatico programmato
Tutto anche dai pannelli /admin e /com (bottoni "aec_*").
"""
import html
import logging
import time
from types import SimpleNamespace

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters

from economy import fmt, challenge_start, banco_balance, _announce, follia_enabled
from state import USER_DATA, BANCO_STATE, SYSTEM_FLAGS, GROUP_CHALLENGE
from storage import save_db, _touched_users
from utils import is_admin, smart_edit

RESET_PLAYER_DEFAULT = 2000
RESET_BANCO_DEFAULT = 1000
RESET_STALE = 6 * 3600            # se il bot era spento oltre 6 ore dopo l'orario, il reset automatico non parte


def _num(tok: str):
    t = tok.replace(".", "").replace(",", "").replace("'", "")
    return int(t) if t.isdigit() else None


# --------------------------------------------------------------------------- reset
def reset_all_players(amount: int) -> int:
    n = 0
    for k, d in USER_DATA.items():
        if k.startswith("sbx_") or not isinstance(d, dict):
            continue
        d["coins"] = int(amount)
        _touched_users.add(k)
        n += 1
    save_db()
    return n


def reset_banco(amount: int):
    BANCO_STATE["BANCO_BALANCE"] = int(amount)
    BANCO_STATE["FOLLIA_ATTIVA"] = False
    BANCO_STATE["FOLLIA_CONSUMED"] = 0
    BANCO_STATE["FOLLIA_END"] = 0
    save_db()


async def _refund_games(context, admin_id):
    """Prima di azzerare i saldi si chiudono le partite in corso (puntate rimborsate: poi vengono comunque azzerate)."""
    from handlers.system import reset_duello

    class _Msg:
        async def reply_text(self, *a, **k):
            return None
    try:
        await reset_duello(SimpleNamespace(effective_user=SimpleNamespace(id=int(admin_id)), message=_Msg()), context)
    except Exception as e:
        logging.warning(f"reset partite prima del reset saldi: {e}")


async def do_reset_all(context, player_amt: int, banco_amt: int, admin_id) -> str:
    await _refund_games(context, admin_id)
    n = reset_all_players(player_amt)
    reset_banco(banco_amt)
    return f"{n} giocatori a {fmt(player_amt)} $SDG, Banco a {fmt(banco_amt)} $SDG"


async def _run_auto_reset(context: ContextTypes.DEFAULT_TYPE):
    """Job (ogni 30s): reset UNA TANTUM programmato in SYSTEM_FLAGS['RESET_AT'] (timestamp UTC)."""
    r = SYSTEM_FLAGS.get("RESET_AT")
    if not r or time.time() < float(r.get("ts", 0)):
        return
    SYSTEM_FLAGS.pop("RESET_AT", None)            # prima si disarma: non può ripartire due volte
    save_db()
    if time.time() - float(r["ts"]) > RESET_STALE:
        await _notify_admin(context, "⚠️ Il reset automatico era scaduto da troppo tempo (bot spento?): NON eseguito.")
        return
    from config import ADMIN_ID
    desc = await do_reset_all(context, int(r.get("players", RESET_PLAYER_DEFAULT)),
                              int(r.get("banco", RESET_BANCO_DEFAULT)), ADMIN_ID)
    await _notify_admin(context, f"✅ Reset automatico eseguito: {desc}.")
    if r.get("announce", True):
        await _announce(context.bot, "🔄 <b>RESET GENERALE!</b>\nTutti ripartono da "
                        f"<b>{fmt(int(r.get('players', RESET_PLAYER_DEFAULT)))} $SDG</b>. Il Banco torna a "
                        f"{fmt(int(r.get('banco', RESET_BANCO_DEFAULT)))}. Buona fortuna! 🍀")


async def _notify_admin(context, text):
    from config import ADMIN_ID
    try:
        await context.bot.send_message(chat_id=int(ADMIN_ID), text=text)
    except Exception:
        pass


def _auto_text() -> str:
    r = SYSTEM_FLAGS.get("RESET_AT")
    if not r:
        return "nessun reset automatico programmato"
    left = max(0, int(float(r["ts"]) - time.time()))
    return (f"ARMATO: tra {left // 3600}h {left % 3600 // 60:02d}m → tutti a {fmt(int(r.get('players', 2000)))}, "
            f"Banco a {fmt(int(r.get('banco', 1000)))}")


# --------------------------------------------------------------------------- viste
def _reset_view():
    text = (
        "🔄 <b>RESET SALDI</b>\n\n"
        f"⏰ Automatico: <b>{_auto_text()}</b>\n\n"
        "Scegli cosa fare (i comandi liberi: <code>/resettutti 3000</code>, <code>/resetbanco 500</code>, "
        "<code>/resetsoldi @utente 2000</code>).\n"
        "<i>Le statistiche (profitto, vittorie…) restano; cambia solo il saldo.</i>"
    )
    rows = [
        [InlineKeyboardButton(f"👥 Tutti a {fmt(RESET_PLAYER_DEFAULT)} + Banco {fmt(RESET_BANCO_DEFAULT)}", callback_data="aec_ask_all")],
        [InlineKeyboardButton(f"🏦 Solo Banco a {fmt(RESET_BANCO_DEFAULT)}", callback_data="aec_ask_banco")],
        [InlineKeyboardButton("👤 Un giocatore: /resetsoldi @utente [quota]", callback_data="aec_help")],
    ]
    if SYSTEM_FLAGS.get("RESET_AT"):
        rows.append([InlineKeyboardButton("🛑 Disarma reset automatico", callback_data="aec_disarm")])
    rows.append([InlineKeyboardButton("⬅️ Indietro", callback_data="adm_back")])
    return text, InlineKeyboardMarkup(rows)


# --------------------------------------------------------------------------- comandi
async def sfida_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user, msg = update.effective_user, update.effective_message
    if not user or not msg or not is_admin(user.id):
        return
    await _start_free_challenge(context, msg, (msg.text or "").split()[1:])


async def _start_free_challenge(context, msg, args):
    nums = [_num(a) for a in args]
    nums = [n for n in nums if n]
    if len(nums) < 2:
        await msg.reply_text("Scrivi quota e giorni, es. <code>/sfida 50000 7</code> (50.000 $SDG in 7 giorni).",
                             parse_mode="HTML")
        return
    goal, days = nums[0], nums[1]
    if not (100 <= goal <= 100_000_000) or not (1 <= days <= 60):
        await msg.reply_text("Quota tra 100 e 100.000.000, giorni tra 1 e 60.")
        return
    ok, note = await challenge_start(context.bot, goal=goal, days=days)
    await msg.reply_text(("✅ " if ok else "⚠️ ") + f"{note} ({fmt(goal)} $SDG, {days} g)")


async def free_chal_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Dopo il bottone '✏️ Numeri liberi' l'admin scrive '50000 7' in privato."""
    user, msg = update.effective_user, update.effective_message
    if not user or not msg or not is_admin(user.id) or update.effective_chat.type != "private":
        return
    if not context.user_data.get("await_chal"):
        return
    context.user_data.pop("await_chal", None)
    await _start_free_challenge(context, msg, (msg.text or "").replace("/", " ").split())
    from telegram.ext import ApplicationHandlerStop
    raise ApplicationHandlerStop


async def resettutti_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user, msg = update.effective_user, update.effective_message
    if not user or not msg or not is_admin(user.id):
        return
    a = [n for n in (_num(x) for x in (msg.text or "").split()[1:]) if n is not None]
    amt = a[0] if a else RESET_PLAYER_DEFAULT
    banco = a[1] if len(a) > 1 else RESET_BANCO_DEFAULT
    await msg.reply_text(
        f"⚠️ Portare TUTTI i giocatori a <b>{fmt(amt)} $SDG</b> e il Banco a <b>{fmt(banco)}</b>?",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Sì, reset", callback_data=f"aec_all_{amt}_{banco}"),
                                            InlineKeyboardButton("❌ No", callback_data="aec_no")]]))


async def resetbanco_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user, msg = update.effective_user, update.effective_message
    if not user or not msg or not is_admin(user.id):
        return
    a = [n for n in (_num(x) for x in (msg.text or "").split()[1:]) if n is not None]
    amt = a[0] if a else RESET_BANCO_DEFAULT
    reset_banco(amt)
    await msg.reply_text(f"🏦 Banco azzerato a {fmt(amt)} $SDG.")


async def resetsoldi_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user, msg, chat = update.effective_user, update.effective_message, update.effective_chat
    if not user or not msg or not is_admin(user.id):
        return
    from storage import find_user_id
    toks = (msg.text or "").split()[1:]
    amt = RESET_PLAYER_DEFAULT
    target = None
    label = ""
    for t in toks:
        n = _num(t)
        if t.startswith("@"):
            label = t
            try:
                target = find_user_id(t[1:].lower())
            except Exception:
                target = None
            if not target:
                from handlers.system import _find_user_id_by_username
                target = await _find_user_id_by_username(context, chat.id, t[1:].lower()) if chat.type != "private" else None
        elif n is not None:
            if len(t) >= 7 and t.isdigit() and target is None:
                target, label = int(t), t           # ID numerico
            else:
                amt = n
    if msg.reply_to_message and msg.reply_to_message.from_user and not target:
        target, label = msg.reply_to_message.from_user.id, msg.reply_to_message.from_user.first_name
    if not target:
        await msg.reply_text("Uso: <code>/resetsoldi @utente [quota]</code> (o ID numerico, o in risposta a un messaggio).",
                             parse_mode="HTML")
        return
    if str(target) not in USER_DATA:
        await msg.reply_text("Giocatore non trovato nel registro.")
        return
    USER_DATA[str(target)]["coins"] = amt
    _touched_users.add(str(target))
    save_db()
    await msg.reply_text(f"✅ {html.escape(str(label))} riportato a {fmt(amt)} $SDG.")


async def resetauto_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user, msg = update.effective_user, update.effective_message
    if not user or not msg or not is_admin(user.id):
        return
    args = (msg.text or "").split()[1:]
    if args and args[0].lower() in ("off", "annulla", "stop"):
        SYSTEM_FLAGS.pop("RESET_AT", None)
        save_db()
        await msg.reply_text("🛑 Reset automatico disarmato.")
        return
    await msg.reply_text(f"⏰ {_auto_text()}\n(<code>/resetauto off</code> per annullarlo)", parse_mode="HTML")


# --------------------------------------------------------------------------- callback
async def aec_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id) or q.message.chat.type != "private":
        await q.answer("🔒 Riservato all'admin.", show_alert=True)
        return
    p = q.data.split("_")
    act = p[1]
    if act == "menu":
        await q.answer()
        await smart_edit(q, *_reset_view())
    elif act == "ask":
        await q.answer()
        if p[2] == "all":
            data_yes, txt = f"aec_all_{RESET_PLAYER_DEFAULT}_{RESET_BANCO_DEFAULT}", \
                f"⚠️ Tutti i giocatori a <b>{fmt(RESET_PLAYER_DEFAULT)}</b> e Banco a <b>{fmt(RESET_BANCO_DEFAULT)}</b>. Confermi?"
        else:
            data_yes, txt = f"aec_banco_{RESET_BANCO_DEFAULT}", f"⚠️ Banco a <b>{fmt(RESET_BANCO_DEFAULT)}</b>. Confermi?"
        await smart_edit(q, txt, InlineKeyboardMarkup([[InlineKeyboardButton("✅ Sì", callback_data=data_yes),
                                                        InlineKeyboardButton("❌ No", callback_data="aec_menu")]]))
    elif act == "all":
        await q.answer("Reset in corso…")
        desc = await do_reset_all(context, int(p[2]), int(p[3]), q.from_user.id)
        await smart_edit(q, f"✅ Fatto: {desc}.", InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Indietro", callback_data="aec_menu")]]))
    elif act == "banco":
        reset_banco(int(p[2]))
        await q.answer("Banco azzerato")
        await smart_edit(q, *_reset_view())
    elif act == "disarm":
        SYSTEM_FLAGS.pop("RESET_AT", None)
        save_db()
        await q.answer("Reset automatico disarmato")
        await smart_edit(q, *_reset_view())
    elif act == "help":
        await q.answer()
        await q.message.reply_text("Per un solo giocatore: <code>/resetsoldi @utente 2000</code> "
                                   "(o in risposta a un suo messaggio; senza quota usa 2000).", parse_mode="HTML")
    elif act == "free":
        await q.answer()
        context.user_data["await_chal"] = True
        await q.message.reply_text("✏️ Scrivimi quota e giorni, es. <code>50000 7</code>", parse_mode="HTML")
    elif act == "fol":
        SYSTEM_FLAGS["FOLLIA_ENABLED"] = not follia_enabled()
        save_db()
        await q.answer("🎪 Follia " + ("ACCESA" if follia_enabled() else "SPENTA"), show_alert=True)
        from handlers.system import _admin_view
        await smart_edit(q, *_admin_view())
    elif act == "no":
        await q.answer()
        await smart_edit(q, "Annullato.")


def arm_tonight_reset():
    """Arma il reset di mezzanotte (20/10/2026 00:00 ora italiana = 19/10 22:00 UTC) se non già eseguito."""
    ts = 1792447200.0           # 2026-10-19 22:00:00 UTC
    if time.time() < ts and not SYSTEM_FLAGS.get("RESET_DONE_1020") and "RESET_AT" not in SYSTEM_FLAGS:
        SYSTEM_FLAGS["RESET_AT"] = {"ts": ts, "players": RESET_PLAYER_DEFAULT, "banco": RESET_BANCO_DEFAULT, "announce": True}
        SYSTEM_FLAGS["RESET_DONE_1020"] = True
        save_db()


def register(app):
    app.add_handler(CommandHandler("sfida", sfida_command))
    app.add_handler(CommandHandler("resettutti", resettutti_command))
    app.add_handler(CommandHandler("resetbanco", resetbanco_command))
    app.add_handler(CommandHandler("resetsoldi", resetsoldi_command))
    app.add_handler(CommandHandler("resetauto", resetauto_command))
    app.add_handler(CallbackQueryHandler(aec_callback, pattern="^aec_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, free_chal_text), group=-1)
    if app.job_queue:
        app.job_queue.run_repeating(_run_auto_reset, interval=30, first=20)
        app.job_queue.run_once(lambda ctx: arm_tonight_reset(), 5)
