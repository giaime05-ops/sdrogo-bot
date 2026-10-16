"""Funzioni V2: /lavora, Inventario, oggetti troll (silenzia, perseguita, sanguisuga, filtro) ed effetti sui messaggi."""
import asyncio
import html
import logging
import time

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReactionTypeEmoji
from telegram.ext import CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters

from economy import (
    inv, fmt, make_puzzle, work_cooldown_left, work_set_cooldown,
    WORK_REWARD, WORK_TIME, WORK_MAX_BALANCE, MUTE_FILTER_TARGETS,
)
from state import (
    WORK_GAMES, PENDING_INPUT, SILENCED, REACT_TARGETS, MUTE_FILTER, USER_DATA,
    game_enabled, MAINT_GAME_TEXT,
)
from storage import get_user_coins, add_user_coins, save_db, chat_user_ids
from utils import is_v2, verify_user_lock, serialized, smart_edit, smart_edit_msg

WORK_TICK = 5                 # secondi tra due aggiornamenti del conto alla rovescia (la scadenza è controllata dal server)
PENDING_TTL = 300             # secondi di validità di un input privato in sospeso
SILENCE_MESSAGES = 15
SILENCE_DEFAULT_PHRASE = "Chiedo scusa, sono uno sdrogo pentito"
REACT_MINUTES = 15
MUTE_FILTER_MINUTES = 10
MUTE_FILTER_LABEL = "💦 Filtro Silenzio Totale"

REACTIONS = [("🤡", "Pagliaccio"), ("💩", "Cacca"), ("🔥", "Fuoco"), ("🤮", "Vomito"), ("🤬", "Rabbia"), ("❤️", "Cuore")]

# (chiave, campo inventario, prezzo, etichetta, descrizione breve, come si usa)
SHOP_ITEMS = [
    ("title", "titles", 100, "🏷️ Titolo Umiliante", "Titolo umiliante per 24 ore",
     "Per assegnarlo per 24 ORE a una vittima, scrivi in chat:\n👉 <code>titolo @username</code> (oppure rispondi al suo messaggio con <code>titolo</code>)"),
    ("persecute", "persecutes", 120, "🤡 Perseguita", "Il bot reagisce a tutti i messaggi della vittima per 15 minuti",
     "Scrivi in chat <code>perseguita @username</code> (o rispondi al suo messaggio con <code>perseguita</code>): sceglierai tu la reazione."),
    ("silence", "silence", 150, "🤐 Silenzia", "Cancello i suoi prossimi 15 messaggi finché non scrive la frase",
     "Scrivi in chat <code>silenzia @username</code> (o rispondi al suo messaggio): ti scrivo in privato per scegliere durata (1-15 min) e frase d'umiliazione."),
    ("mutefilter", "mutefilter", 200, MUTE_FILTER_LABEL, "Muta per 10 minuti i bersagli configurati",
     "Attivalo dall'Inventario 🎒: per 10 minuti cancello i messaggi dei bersagli configurati."),
    ("boostwin", "boost_win", 150, "🚀 Boost Vincita ×1.5", "+50% sul profitto di 10 partite Single Player",
     "Attivalo dall'Inventario 🎒: vale per le tue prossime 10 partite Single Player."),
    ("boostluck", "boost_luck", 200, "🍀 Boost Fortuna", "Il caso ti aiuta per 15 partite da casinò",
     "Attivalo dall'Inventario 🎒: vale per le prossime 15 partite a Blackjack, Slot, Roulette e Gratta e Vinci."),
    ("sang", "sang", 250, "🧛 Sanguisuga", "Rubi il 25% delle vincite della vittima per 10 partite",
     "Scrivi in chat <code>sanguisuga @username</code> (o rispondi al suo messaggio): per le sue prossime 10 partite Single Player il 25% di ogni vincita va a te."),
]
SHOP_BY_KEY = {s[0]: s for s in SHOP_ITEMS}

_TASKS = set()


def _spawn(coro):
    t = asyncio.create_task(coro)
    _TASKS.add(t)
    t.add_done_callback(_TASKS.discard)
    return t


# =====================================================================
# RISOLUZIONE BERSAGLIO (@username o risposta a un messaggio)
# =====================================================================
async def resolve_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Restituisce (target_id, etichetta_html, errore). In caso di errore target_id è None."""
    msg = update.message
    chat_id = msg.chat_id
    for part in (msg.text or "").split()[1:]:
        if part.startswith("@") and len(part) > 1:
            uname = part[1:].lower()
            label = f"@{html.escape(uname)}"
            uid = context.bot_data.get("usernames", {}).get(f"{chat_id}_{uname}")
            if not uid:
                for cand in chat_user_ids(chat_id):
                    try:
                        m = await context.bot.get_chat_member(chat_id, int(cand))
                        if m.user.username and m.user.username.lower() == uname:
                            uid = int(cand)
                            context.bot_data.setdefault("usernames", {})[f"{chat_id}_{uname}"] = uid
                            break
                    except Exception:
                        pass
            if not uid:
                return None, label, "❌ Utente non trovato nel registro della chat!"
            return int(uid), label, None
    reply = msg.reply_to_message
    if reply and reply.from_user:
        if reply.from_user.is_bot:
            return None, "", "❌ Non puoi usarlo su un bot!"
        return reply.from_user.id, html.escape(reply.from_user.first_name or "utente"), None
    return None, "", "❌ Indica un @username oppure rispondi a un suo messaggio."


# =====================================================================
# /lavora: enigmi a tempo per chi è al verde
# =====================================================================
def _work_text(game: dict, left: int) -> str:
    return f"💼 <b>LAVORETTO</b>\n⏱️ Tempo rimasto: <b>{left}s</b>\n\n{game['puzzle']['text']}"


def _work_kb(puzzle: dict, uid: int) -> InlineKeyboardMarkup:
    rows, row = [], []
    for i, label in enumerate(puzzle["options"]):
        row.append(InlineKeyboardButton(label, callback_data=f"work_ans_{i}_{uid}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)


def _fmt_wait(secs: int) -> str:
    h, rem = divmod(secs, 3600)
    return f"{h}h {rem // 60}m" if h else f"{max(1, rem // 60)}m"


async def lavora_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user, chat, msg = update.effective_user, update.effective_chat, update.message
    if not user or not msg:
        return
    if not is_v2(user.id):
        await msg.reply_text("💼 /lavora non è ancora disponibile!")
        return
    if not game_enabled("lavora"):
        await msg.reply_text(MAINT_GAME_TEXT)
        return
    chat_id = chat.id
    running = WORK_GAMES.get(user.id)
    if running and running.get("state") == "run" and not running.get("done"):
        await msg.reply_text("💼 Hai già un lavoretto in corso!")
        return
    if WORK_MAX_BALANCE is not None and get_user_coins(chat_id, user.id) >= WORK_MAX_BALANCE:
        await msg.reply_text(f"💼 /lavora è riservato a chi ha meno di {WORK_MAX_BALANCE} $SDG. Vai a giocare! 🎰")
        return
    left = work_cooldown_left(chat_id, user.id)
    if left > 0:
        await msg.reply_text(f"⏳ Sei stanco: potrai lavorare di nuovo tra <b>{_fmt_wait(left)}</b>.", parse_mode="HTML")
        return
    sent = await msg.reply_text(
        f"💼 <b>/LAVORA</b>\n\nRisolvi l'enigma entro <b>{WORK_TIME} secondi</b> e guadagni <b>{WORK_REWARD} $SDG</b>.\n"
        "⚠️ Premuto PRONTO parte il cooldown di 2 ore, anche se sbagli o il tempo scade.\n\nSei pronto?",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ PRONTO", callback_data=f"work_go_{user.id}")]]),
        parse_mode="HTML"
    )
    WORK_GAMES[user.id] = {"state": "ready", "chat_id": chat_id, "msg_id": sent.message_id, "ts": time.time(), "done": False}


async def _work_timer(bot, uid: int, game: dict):
    while True:
        await asyncio.sleep(WORK_TICK)
        if WORK_GAMES.get(uid) is not game or game["done"]:
            return
        left = int(game["deadline"] - time.time())
        if left <= 0:
            break
        await smart_edit_msg(bot, game["chat_id"], game["msg_id"], _work_text(game, left), _work_kb(game["puzzle"], uid))
    if WORK_GAMES.get(uid) is game and not game["done"]:
        game["done"] = True
        WORK_GAMES.pop(uid, None)
        await smart_edit_msg(bot, game["chat_id"], game["msg_id"],
                             "⏰ <b>TEMPO SCADUTO!</b>\nL'enigma è sfumato: potrai riprovare tra 2 ore.", None)


@serialized("user")
async def handle_work_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    parts = query.data.split("_")            # work_go_UID | work_ans_IDX_UID
    owner_id = int(parts[-1])
    if not await verify_user_lock(query, owner_id):
        return
    uid = query.from_user.id
    chat_id = query.message.chat_id
    game = WORK_GAMES.get(uid)
    act = parts[1]

    if act == "go":
        if not game or game.get("state") != "ready" or game["msg_id"] != query.message.message_id:
            await query.answer("Sessione scaduta: rilancia /lavora.", show_alert=True)
            return
        if not game_enabled("lavora"):
            await query.answer(MAINT_GAME_TEXT, show_alert=True)
            return
        if WORK_MAX_BALANCE is not None and get_user_coins(chat_id, uid) >= WORK_MAX_BALANCE:
            await query.answer(f"Hai già {WORK_MAX_BALANCE} $SDG o più!", show_alert=True)
            return
        if work_cooldown_left(chat_id, uid) > 0:
            await query.answer("Sei ancora in cooldown.", show_alert=True)
            return
        game["state"] = "run"                 # prima di qualsiasi await: un secondo tap non riparte
        await query.answer()
        work_set_cooldown(chat_id, uid)
        game["puzzle"] = make_puzzle()
        game["deadline"] = time.time() + WORK_TIME
        await smart_edit(query, _work_text(game, WORK_TIME), _work_kb(game["puzzle"], uid))
        _spawn(_work_timer(context.bot, uid, game))
        return

    if act == "ans":
        if not game or game.get("state") != "run" or game["done"]:
            await query.answer("⌛ Sfida già conclusa.", show_alert=True)
            return
        game["done"] = True                   # sincrono: il timer non può più intervenire
        WORK_GAMES.pop(uid, None)
        await query.answer()
        idx = int(parts[2])
        if time.time() > game["deadline"]:
            text = "⏰ <b>TROPPO TARDI!</b> Il tempo era scaduto: niente paga. Riprova tra 2 ore."
        elif idx == game["puzzle"]["ans"]:
            add_user_coins(chat_id, uid, WORK_REWARD)
            text = (f"🎉 <b>ESATTO!</b> Hai guadagnato <b>+{WORK_REWARD} $SDG</b>!\n"
                    f"💳 Saldo Attuale: <b>{fmt(get_user_coins(chat_id, uid))} $SDG</b>")
        else:
            text = "❌ <b>RISPOSTA SBAGLIATA!</b> Nessuna paga: riprova tra 2 ore."
        await smart_edit(query, text, None)
        return

    await query.answer()


# =====================================================================
# INVENTARIO
# =====================================================================
def inventory_view(chat_id: int, user_id: int, owner_id: int):
    d = inv(chat_id, user_id)
    lines = [
        "🎒 <b>INVENTARIO</b>\n",
        f"🏷️ Titoli Umilianti: <b>{d['titles']}</b>",
        f"🤡 Perseguita: <b>{d['persecutes']}</b>",
        f"🤐 Silenzia: <b>{d['silence']}</b>",
        f"{MUTE_FILTER_LABEL}: <b>{d['mutefilter']}</b>",
        f"🚀 Boost Vincita: <b>{d['boost_win']}</b>",
        f"🍀 Boost Fortuna: <b>{d['boost_luck']}</b>",
        f"🧛 Sanguisuga: <b>{d['sang']}</b>",
        f"⭐ Stelle: <b>{d['stars']}</b>",
    ]
    active = []
    if d["boost_win_left"] > 0:
        active.append(f"🚀 Boost Vincita attivo: {d['boost_win_left']} partite")
    if d["boost_luck_left"] > 0:
        active.append(f"🍀 Boost Fortuna attivo: {d['boost_luck_left']} partite")
    s = d.get("sang_on_me")
    if s and s.get("left", 0) > 0:
        active.append(f"🧛 Sanguisuga su di te: {s['left']} partite")
    if active:
        lines.append("\n⚡ <b>Effetti attivi</b>\n" + "\n".join(active))
    lines.append("\n<i>Tocca un oggetto per attivarlo o vedere come si usa.</i>")

    rows = []
    if d["boost_win"] > 0:
        rows.append([InlineKeyboardButton(f"🚀 Attiva Boost Vincita ({d['boost_win']})", callback_data=f"inv_bw_{owner_id}")])
    if d["boost_luck"] > 0:
        rows.append([InlineKeyboardButton(f"🍀 Attiva Boost Fortuna ({d['boost_luck']})", callback_data=f"inv_bl_{owner_id}")])
    if d["mutefilter"] > 0:
        rows.append([InlineKeyboardButton(f"{MUTE_FILTER_LABEL} — Attiva ({d['mutefilter']})", callback_data=f"inv_mf_{owner_id}")])
    helps = []
    for key, field, label in (("silence", "silence", "🤐 Silenzia"), ("persecute", "persecutes", "🤡 Perseguita"),
                              ("sang", "sang", "🧛 Sanguisuga"), ("title", "titles", "🏷️ Titolo")):
        if d[field] > 0:
            helps.append(InlineKeyboardButton(f"{label}: come si usa", callback_data=f"inv_help_{key}_{owner_id}"))
    for i in range(0, len(helps), 2):
        rows.append(helps[i:i + 2])
    rows.append([InlineKeyboardButton("💳 Portafoglio", callback_data=f"hub_wallet_{owner_id}"),
                 InlineKeyboardButton("🔙 HUB", callback_data=f"hub_main_{owner_id}")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


@serialized("user")
async def handle_inv_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    parts = query.data.split("_")            # inv_bw_OWNER | inv_bl_OWNER | inv_mf_OWNER | inv_help_KEY_OWNER
    owner_id = int(parts[-1])
    if not await verify_user_lock(query, owner_id):
        return
    if not is_v2(query.from_user.id):
        await query.answer("🎒 Non ancora disponibile!", show_alert=True)
        return
    chat_id = query.message.chat_id
    uid = query.from_user.id
    d = inv(chat_id, uid)
    act = parts[1]

    if act == "help":
        key = parts[2]
        spec = SHOP_BY_KEY.get(key)
        how = spec[5] if spec else ""
        plain = (how.replace("<code>", "«").replace("</code>", "»").replace("👉 ", "")
                 .replace("<b>", "").replace("</b>", ""))
        await query.answer(plain[:190], show_alert=True)
        return

    if act == "bw":
        if d["boost_win"] <= 0:
            await query.answer("Non hai Boost Vincita.", show_alert=True)
            return
        if d["boost_win_left"] > 0:
            await query.answer(f"Hai già un Boost Vincita attivo ({d['boost_win_left']} partite).", show_alert=True)
            return
        d["boost_win"] -= 1
        d["boost_win_left"] = 10
        save_db()
        await query.answer("🚀 Boost Vincita attivo per 10 partite!")
    elif act == "bl":
        if d["boost_luck"] <= 0:
            await query.answer("Non hai Boost Fortuna.", show_alert=True)
            return
        if d["boost_luck_left"] > 0:
            await query.answer(f"Hai già un Boost Fortuna attivo ({d['boost_luck_left']} partite).", show_alert=True)
            return
        d["boost_luck"] -= 1
        d["boost_luck_left"] = 15
        save_db()
        await query.answer("🍀 Boost Fortuna attivo per 15 partite!")
    elif act == "mf":
        if d["mutefilter"] <= 0:
            await query.answer("Non hai il filtro.", show_alert=True)
            return
        if query.message.chat.type == "private":
            await query.answer("Attivalo nel gruppo!", show_alert=True)
            return
        if not MUTE_FILTER_TARGETS:
            await query.answer("Filtro non configurato (variabile MUTE_FILTER_TARGETS): oggetto non consumato.", show_alert=True)
            return
        d["mutefilter"] -= 1
        until = time.time() + MUTE_FILTER_MINUTES * 60
        for uname in MUTE_FILTER_TARGETS:
            MUTE_FILTER[f"{chat_id}_{uname}"] = until
        save_db()
        await query.answer(f"{MUTE_FILTER_LABEL} attivo per {MUTE_FILTER_MINUTES} minuti!")
        try:
            await context.bot.send_message(
                chat_id=chat_id, parse_mode="HTML",
                text=(f"🔇 <b>{MUTE_FILTER_LABEL}</b> attivato da <b>{html.escape(query.from_user.first_name or 'un giocatore')}</b>: "
                      f"i bersagli configurati sono silenziati per {MUTE_FILTER_MINUTES} minuti."))
        except Exception:
            pass
    else:
        await query.answer()
        return

    text, kb = inventory_view(chat_id, uid, owner_id)
    await smart_edit(query, text, kb)


# =====================================================================
# SILENZIA @user (configurazione in privato)
# =====================================================================
def _silence_time_kb(uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{m} min", callback_data=f"sil_t_{m}_{uid}") for m in (1, 3, 5, 10, 15)],
        [InlineKeyboardButton("✖️ Annulla", callback_data=f"sil_x_{uid}")],
    ])


async def silence_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user, chat, msg = update.effective_user, update.effective_chat, update.message
    if not is_v2(user.id):
        return
    if chat.type == "private":
        await msg.reply_text("🤐 Usa <code>silenzia @username</code> nel gruppo!", parse_mode="HTML")
        return
    if inv(chat.id, user.id)["silence"] <= 0:
        await msg.reply_text("❌ Non possiedi alcun 🤐 Silenzia: compralo nello SdrogoShop (/sdrogocomm)!")
        return
    tid, label, err = await resolve_target(update, context)
    if err:
        await msg.reply_text(err)
        return
    if tid == user.id:
        await msg.reply_text("🤡 Non puoi silenziare te stesso!")
        return
    PENDING_INPUT[user.id] = {"type": "silence", "step": "time", "chat_id": chat.id, "target_id": tid,
                              "label": label, "minutes": 5, "ts": time.time()}
    try:
        await context.bot.send_message(
            chat_id=user.id, parse_mode="HTML",
            text=f"🤐 <b>SILENZIA {label}</b>\n\nPer quanti minuti vuoi silenziarlo?",
            reply_markup=_silence_time_kb(user.id))
    except Exception:
        PENDING_INPUT.pop(user.id, None)
        await msg.reply_text("📩 Non riesco a scriverti in privato: apri il bot, premi START e riprova.")
        return
    await msg.reply_text("📩 Ti ho scritto in privato per configurare il silenzio.")


async def _activate_silence(context, user, pend: dict, phrase: str, query=None):
    chat_id = pend["chat_id"]
    PENDING_INPUT.pop(user.id, None)
    d = inv(chat_id, user.id)
    if d["silence"] <= 0:
        text = "❌ Non hai più l'oggetto Silenzia."
    else:
        d["silence"] -= 1
        SILENCED[f"{chat_id}_{pend['target_id']}"] = {
            "until": time.time() + pend["minutes"] * 60, "left": SILENCE_MESSAGES, "phrase": phrase}
        save_db()
        text = f"✅ Silenzio attivato su {pend['label']} per {pend['minutes']} minuti."
        try:
            await context.bot.send_message(
                chat_id=chat_id, parse_mode="HTML",
                text=(f"🤐 {pend['label']} è stato <b>silenziato</b> da <b>{html.escape(user.first_name or 'un giocatore')}</b> "
                      f"per {pend['minutes']} minuti!\nPer liberarsi deve scrivere: «<b>{html.escape(phrase)}</b>»"))
        except Exception as e:
            logging.warning(f"Annuncio silenzio non inviato: {e}")
    if query:
        await smart_edit(query, text, None)
    else:
        try:
            await context.bot.send_message(chat_id=user.id, text=text)
        except Exception:
            pass


@serialized("user")
async def handle_silence_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    parts = query.data.split("_")            # sil_t_MIN_UID | sil_d_UID | sil_c_UID | sil_x_UID
    owner_id = int(parts[-1])
    if not await verify_user_lock(query, owner_id):
        return
    pend = PENDING_INPUT.get(owner_id)
    if not pend or pend.get("type") != "silence" or time.time() - pend["ts"] > PENDING_TTL:
        PENDING_INPUT.pop(owner_id, None)
        await query.answer("Sessione scaduta: riscrivi il comando nel gruppo.", show_alert=True)
        return
    act = parts[1]
    await query.answer()
    if act == "x":
        PENDING_INPUT.pop(owner_id, None)
        await smart_edit(query, "❌ Operazione annullata, l'oggetto non è stato consumato.", None)
    elif act == "t":
        pend["minutes"] = max(1, min(15, int(parts[2])))
        pend["step"] = "phrase"
        pend["ts"] = time.time()
        await smart_edit(
            query,
            f"🤐 <b>SILENZIA {pend['label']}</b> — {pend['minutes']} minuti\n\nCome si libera?",
            InlineKeyboardMarkup([
                [InlineKeyboardButton("📝 Frase predefinita", callback_data=f"sil_d_{owner_id}")],
                [InlineKeyboardButton("✍️ Scrivi una frase mia", callback_data=f"sil_c_{owner_id}")],
                [InlineKeyboardButton("✖️ Annulla", callback_data=f"sil_x_{owner_id}")],
            ]))
    elif act == "d":
        await _activate_silence(context, query.from_user, pend, SILENCE_DEFAULT_PHRASE, query)
    elif act == "c":
        pend["step"] = "phrase_text"
        pend["ts"] = time.time()
        await smart_edit(
            query,
            "✍️ Scrivimi ora <b>in questo chat privata</b> la frase d'umiliazione (max 80 caratteri).\n"
            "Usa /annulla per interrompere.", None)


async def cancel_pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user and PENDING_INPUT.pop(update.effective_user.id, None):
        await update.message.reply_text("❌ Operazione annullata.")


# =====================================================================
# PERSEGUITA @user (reazione a tutti i messaggi per 15 minuti)
# =====================================================================
async def persecute_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user, chat, msg = update.effective_user, update.effective_chat, update.message
    if chat.type == "private":
        await msg.reply_text("🤡 Usa <code>perseguita @username</code> nel gruppo!", parse_mode="HTML")
        return
    if inv(chat.id, user.id)["persecutes"] <= 0:
        await msg.reply_text("❌ Non possiedi alcun 🤡 Perseguita: compralo nello SdrogoShop (/sdrogocomm)!")
        return
    tid, label, err = await resolve_target(update, context)
    if err:
        await msg.reply_text(err)
        return
    if tid == user.id:
        await msg.reply_text("🤡 Non puoi perseguitare te stesso!")
        return
    rows = []
    for i in range(0, len(REACTIONS), 3):
        rows.append([InlineKeyboardButton(f"{e} {n}", callback_data=f"rx_{j}_{tid}_{user.id}")
                     for j, (e, n) in enumerate(REACTIONS) if i <= j < i + 3])
    rows.append([InlineKeyboardButton("✖️ Annulla", callback_data=f"rx_x_0_{user.id}")])
    await msg.reply_text(
        f"🤡 <b>PERSEGUITA {label}</b>\n\nScegli la reazione che il bot metterà a tutti i suoi messaggi per {REACT_MINUTES} minuti:",
        reply_markup=InlineKeyboardMarkup(rows), parse_mode="HTML")


@serialized("user")
async def handle_reaction_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    parts = query.data.split("_")            # rx_IDX_TARGET_OWNER | rx_x_0_OWNER
    owner_id = int(parts[-1])
    if not await verify_user_lock(query, owner_id):
        return
    chat_id = query.message.chat_id
    if parts[1] == "x":
        await query.answer()
        await smart_edit(query, "❌ Operazione annullata.", None)
        return
    d = inv(chat_id, owner_id)
    if d["persecutes"] <= 0:
        await query.answer("Non hai più l'oggetto.", show_alert=True)
        return
    try:
        emoji = REACTIONS[int(parts[1])][0]
        target_id = int(parts[2])
    except (ValueError, IndexError):
        await query.answer()
        return
    d["persecutes"] -= 1
    REACT_TARGETS[f"{chat_id}_{target_id}"] = {"until": time.time() + REACT_MINUTES * 60, "emoji": emoji}
    save_db()
    await query.answer(f"{emoji} Perseguitazione attiva!")
    await smart_edit(query, f"{emoji} <b>PERSEGUITAZIONE ATTIVA!</b> Per {REACT_MINUTES} minuti il bot reagirà con {emoji} a tutti i messaggi del bersaglio.", None)


# =====================================================================
# SANGUISUGA @user
# =====================================================================
async def sang_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user, chat, msg = update.effective_user, update.effective_chat, update.message
    if not is_v2(user.id):
        return
    if chat.type == "private":
        await msg.reply_text("🧛 Usa <code>sanguisuga @username</code> nel gruppo!", parse_mode="HTML")
        return
    d = inv(chat.id, user.id)
    if d["sang"] <= 0:
        await msg.reply_text("❌ Non possiedi alcuna 🧛 Sanguisuga: compratela nello SdrogoShop (/sdrogocomm)!")
        return
    tid, label, err = await resolve_target(update, context)
    if err:
        await msg.reply_text(err)
        return
    if tid == user.id:
        await msg.reply_text("🤡 Non puoi usarla su te stesso!")
        return
    victim = inv(chat.id, tid)
    cur = victim.get("sang_on_me")
    if cur and cur.get("left", 0) > 0:
        await msg.reply_text("🧛 Quel giocatore ha già una Sanguisuga attiva!")
        return
    d["sang"] -= 1
    victim["sang_on_me"] = {"buyer": user.id, "left": 10}
    save_db()
    await msg.reply_text(
        f"🧛 <b>SANGUISUGA!</b> Per le prossime 10 partite Single Player di {label} il 25% di ogni vincita "
        f"andrà a <b>{html.escape(user.first_name or 'un giocatore')}</b>!", parse_mode="HTML")


# =====================================================================
# EFFETTI SUI MESSAGGI (silenzio, filtro, reazioni, input privato)
# =====================================================================
async def _delete(msg):
    try:
        await msg.delete()
        return True
    except Exception as e:
        logging.debug(f"Impossibile cancellare il messaggio (serve il permesso di eliminare messaggi): {e}")
        return False


async def process_effects(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Chiamata all'inizio del router testuale. True = il messaggio è stato consumato (non va elaborato oltre)."""
    msg = update.message
    user, chat = msg.from_user, msg.chat
    text = (msg.text or "").strip()
    now = time.time()

    # 1) input in privato (frase d'umiliazione): i comandi non passano di qui
    if chat.type == "private":
        pend = PENDING_INPUT.get(user.id)
        if pend:
            if now - pend["ts"] > PENDING_TTL:
                PENDING_INPUT.pop(user.id, None)
            elif pend.get("type") == "silence" and pend.get("step") == "phrase_text":
                phrase = text[:80].strip()
                if phrase:
                    await _activate_silence(context, user, pend, phrase)
                    return True
        return False

    key = f"{chat.id}_{user.id}"

    # 2) silenzio
    s = SILENCED.get(key)
    if s:
        if now > s["until"] or s["left"] <= 0:
            SILENCED.pop(key, None)
        elif text.lower() == s["phrase"].lower():
            SILENCED.pop(key, None)
            await msg.reply_text("🔓 Ti sei umiliato a dovere: sei di nuovo libero di parlare!")
            return True
        else:
            await _delete(msg)
            s["left"] -= 1
            if s["left"] <= 0:
                SILENCED.pop(key, None)
            return True

    # 3) filtro muto (per username)
    uname = (user.username or "").lower()
    if uname:
        mk = f"{chat.id}_{uname}"
        until = MUTE_FILTER.get(mk)
        if until:
            if now < until:
                await _delete(msg)
                return True
            MUTE_FILTER.pop(mk, None)

    # 4) reazione forzata (il messaggio viene comunque elaborato)
    r = REACT_TARGETS.get(key)
    if r:
        if now > r["until"]:
            REACT_TARGETS.pop(key, None)
        else:
            try:
                await context.bot.set_message_reaction(
                    chat_id=chat.id, message_id=msg.message_id, reaction=[ReactionTypeEmoji(r["emoji"])])
            except Exception as e:
                logging.debug(f"Reazione non applicata: {e}")
    return False


async def handle_non_text_effects(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sticker, foto, vocali... di un utente silenziato o filtrato vengono cancellati in silenzio."""
    msg = update.message
    if not msg or not msg.from_user or msg.chat.type == "private":
        return
    now = time.time()
    key = f"{msg.chat.id}_{msg.from_user.id}"
    s = SILENCED.get(key)
    if s and now <= s["until"] and s["left"] > 0:
        await _delete(msg)
        s["left"] -= 1
        if s["left"] <= 0:
            SILENCED.pop(key, None)
        return
    uname = (msg.from_user.username or "").lower()
    if uname and MUTE_FILTER.get(f"{msg.chat.id}_{uname}", 0) > now:
        await _delete(msg)


def register(app):
    app.add_handler(CommandHandler("lavora", lavora_command))
    app.add_handler(CommandHandler("annulla", cancel_pending))
    app.add_handler(CallbackQueryHandler(handle_work_callback, pattern="^work_"))
    app.add_handler(CallbackQueryHandler(handle_inv_callback, pattern="^inv_"))
    app.add_handler(CallbackQueryHandler(handle_silence_flow, pattern="^sil_"))
    app.add_handler(CallbackQueryHandler(handle_reaction_pick, pattern="^rx_"))
    app.add_handler(MessageHandler(
        filters.ALL & ~filters.TEXT & ~filters.COMMAND & ~filters.StatusUpdate.ALL,
        handle_non_text_effects), group=-1)
