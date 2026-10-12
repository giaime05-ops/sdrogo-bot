from datetime import datetime, timedelta
from html import escape
import time

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CommandHandler, CallbackQueryHandler, ContextTypes

from state import (
    USER_DATA, USER_INVENTORIES, ACTIVE_TITLES, ACTIVE_PERSECUTE, HEIST_GAMES,
    game_enabled, MAINT_GAME_TEXT,
)
from storage import (
    get_user_key, get_user_coins, add_user_coins, get_user_data_dict, backup_to_telegram,
)
from utils import verify_user_lock, get_formatted_name, is_admin, serialized
from handlers.games import HEIST_COST

try:
    from zoneinfo import ZoneInfo
    _TZ = ZoneInfo("Europe/Rome")
except Exception:
    _TZ = None

HEIST_STALE_SECS = 1800   # una rapina abbandonata per 30 minuti viene considerata chiusa


def _today() -> str:
    """Data odierna (fuso Europa/Roma) per il bonus giornaliero."""
    return str(datetime.now(_TZ).date())


def _lab_allowed(user_id: int, chat_type: str) -> bool:
    """Il Laboratorio Segreto è accessibile solo all'admin e solo in chat privata."""
    return is_admin(user_id) and chat_type == "private"


async def show_hub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    coins = get_user_coins(chat_id, user.id)
    display_name = escape(get_formatted_name(chat_id, user.id, user.first_name))

    text = (
        "🎰 <b>SDROGOBOT ARCADE HUB</b> 🎮\n\n"
        f"👤 <b>Player:</b> {display_name}\n"
        f"💰 <b>Saldo:</b> <code>💳 {coins} $SDG</code>\n\n"
        "<i>Seleziona una categoria per iniziare:</i>"
    )

    keyboard = [
        [InlineKeyboardButton("🕹️ Single Player", callback_data=f"hub_single_{user.id}"), InlineKeyboardButton("⚔️ Multiplayer", callback_data=f"hub_multi_{user.id}")],
        [InlineKeyboardButton("🧠 Quiz Show", callback_data=f"hub_quiz_{user.id}"), InlineKeyboardButton("🛒 SdrogoShop", callback_data=f"hub_shop_{user.id}")],
        [InlineKeyboardButton("💳 Portafoglio", callback_data=f"hub_wallet_{user.id}"), InlineKeyboardButton("🏆 Classifiche", callback_data=f"hub_lead_{user.id}")]
    ]

    # Laboratorio Segreto: solo admin e solo in chat privata
    if _lab_allowed(user.id, update.effective_chat.type):
        keyboard.append([InlineKeyboardButton("🧪 Laboratorio Segreto", callback_data=f"hub_lab_{user.id}")])

    if update.message:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    elif update.callback_query:
        try:
            await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        except Exception:
            pass

async def show_wallet(query, owner_id: int):
    chat_id = query.message.chat_id
    user_id = query.from_user.id
    coins = get_user_coins(chat_id, user_id)
    u_data = get_user_data_dict(chat_id, user_id)

    q_wins = u_data.get("quizzes_won", 0)
    c_wins = u_data.get("casino_wins", 0)
    d_wins = u_data.get("duels_wins", 0)
    net_profit = u_data.get("net_profit", 0)

    single_played = u_data.get("single_played", 0)
    single_won = u_data.get("single_won", c_wins)
    wr_single = min(100, int(single_won / single_played * 100)) if single_played > 0 else 0

    duels_played = u_data.get("duels_played", 0)
    duels_won = d_wins
    wr_duels = min(100, int(duels_won / duels_played * 100)) if duels_played > 0 else 0

    inv = USER_INVENTORIES.get(f"{chat_id}_{user_id}", {})
    stars = inv.get("stars", 0)

    profit_str = f"+{net_profit}" if net_profit >= 0 else str(net_profit)

    text = (
        "💳 <b>PORTAFOGLIO & PROFILO</b>\n\n"
        f"👤 Giocatore: <b>{escape(query.from_user.first_name)}</b>\n"
        f"💰 Saldo attuale: <code>💳 {coins} $SDG</code>\n"
        f"📈 Profitto Netto: <code>{profit_str} $SDG</code>\n"
        f"⭐ Stelle Heist: <b>{stars}</b>\n\n"
        "📊 <b>STORICO & STATISTICHE:</b>\n"
        f"🧠 Quiz Vinti: <b>{q_wins}</b>\n"
        f"🎰 Vittorie Casinò: <b>{c_wins}</b>\n"
        f"⚔️ Duelli Vinti: <b>{d_wins}</b>\n\n"
        "🎯 <b>PERFORMANCE:</b>\n"
        f"• Win Rate Single Player: <b>{wr_single}%</b>\n"
        f"• Win Rate Duelli: <b>{wr_duels}%</b>\n\n"
        "🎁 <b>Bonus Daily:</b> Riscuoti 50 $SDG una volta al giorno (si azzera a mezzanotte).\n"
        "💸 <i>Usa /dona @username [importo] per regalare da 1 a 50 $SDG a un amico (5 se non indichi l'importo)!</i>"
    )
    keyboard = [
        [InlineKeyboardButton("🎁 Riscuoti Daily (+50 $SDG)", callback_data=f"claim_daily_{owner_id}")],
        [InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{owner_id}")]
    ]
    try:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    except Exception:
        pass


async def hub_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    data = query.data
    parts = data.split("_")
    action = parts[1]
    extra = None

    if action in ("leadcat", "labgame"):
        extra = parts[2]
        owner_id = int(parts[3])
    else:
        owner_id = int(parts[2]) if len(parts) > 2 else query.from_user.id

    # Laboratorio Segreto: solo admin in chat privata (controllo prima di qualsiasi altra cosa)
    if action in ("lab", "labgame") and not _lab_allowed(query.from_user.id, query.message.chat.type):
        await query.answer("🔒 Area riservata all'admin in chat privata.", show_alert=True)
        return

    # verify_user_lock risponde con il popup d'errore: query.answer() va chiamata solo dopo i controlli
    if not await verify_user_lock(query, owner_id):
        return

    if action == "qmulti" and not game_enabled("quizmulti"):
        await query.answer(MAINT_GAME_TEXT, show_alert=True)
        return

    if action == "labgame":
        names = {"mines": "💣 Campo Minato"}
        await query.answer(f"🚧 {names.get(extra, 'Gioco')}: Work in progress!", show_alert=True)
        return

    await query.answer()

    chat_id = query.message.chat_id
    user_id = query.from_user.id

    back_button = [InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{owner_id}")]

    if action == "main":
        await show_hub(update, context)

    elif action == "lab":
        text = (
            "🧪 <b>LABORATORIO SEGRETO</b>\n\n"
            "Giochi sperimentali, visibili solo a te in chat privata.\n"
            "✈️ Aviator • 🎡 Sdrogo Roulette • 🎟️ Gratta e Vinci sono giocabili.\n"
            "💣 Campo Minato è ancora in sviluppo."
        )
        keyboard = [
            [InlineKeyboardButton("✈️ Aviator", callback_data=f"lab_avi_{owner_id}"), InlineKeyboardButton("🎡 Sdrogo Roulette", callback_data=f"lab_roul_{owner_id}")],
            [InlineKeyboardButton("💣 Campo Minato", callback_data=f"hub_labgame_mines_{owner_id}"), InlineKeyboardButton("🎟️ Gratta e Vinci", callback_data=f"lab_scr_{owner_id}")],
            back_button
        ]
        try:
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        except Exception:
            pass

    elif action == "single":
        text = (
            "🕹️ <b>GIOCHI SINGLE PLAYER</b>\n\n"
            "🃏 <b>Blackjack 21</b> — <i>puntata da 10 $SDG</i>\n"
            "🎰 <b>Slot Machine 777</b> — <i>puntata da 10 $SDG</i>\n"
            "🔠 <b>Wordle Express</b> — <i>10 $SDG</i>\n"
            "🔐 <b>Mastermind Express</b> — <i>10 $SDG</i>\n"
            "🏢 <b>Sdrogo Heist</b> — <i>pass acquistabile nello SdrogoShop</i>"
        )
        keyboard = [
            [InlineKeyboardButton("🃏 Blackjack", callback_data=f"start_bj_{owner_id}"), InlineKeyboardButton("🎰 Slot 777", callback_data=f"start_slot_{owner_id}")],
            [InlineKeyboardButton("🔠 Wordle", callback_data=f"start_wordle_{owner_id}"), InlineKeyboardButton("🔐 Mastermind", callback_data=f"start_mm_{owner_id}")],
            back_button
        ]
        try:
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        except Exception:
            pass

    elif action == "multi":
        text = (
            "⚔️ <b>GIOCHI MULTIPLAYER E SCOMMESSE</b>\n\n"
            "🎲 <b>Dadi 1v1 con Puntata</b> (<code>sfidodadi @user</code>)\n"
            "❌ <b>Tris 1v1 con Puntata</b> (<code>sfidotris @user</code>)\n"
            "🎯 <b>Roulette Russa 1v1</b> (<code>sfidoroulette @user</code>)\n"
            "🎲 <b>High / Low 1v1</b> (<code>sfidohighlow @user</code>)\n"
            "🪓 <b>Ghigliottina Express 1v1</b> (<code>sfidoghigliottina @user</code>)\n"
            "⚔️ <b>Duello Quiz 1v1</b> (<code>sfidoquiz @user</code>)\n"
            "🌐 <b>Quiz Multiplayer di Gruppo</b>\n\n"
            "<i>A fine partita trovi il tasto 🔁 Rigioca per rifare la sfida con lo stesso avversario.</i>"
        )
        keyboard = [
            [InlineKeyboardButton("🎲 Dadi 1v1", callback_data=f"start_dice_{owner_id}"), InlineKeyboardButton("❌ Tris 1v1", callback_data=f"start_ttt_{owner_id}")],
            [InlineKeyboardButton("🎯 Roulette 1v1", callback_data=f"start_roulette_{owner_id}"), InlineKeyboardButton("🎲 High/Low 1v1", callback_data=f"start_highlow_{owner_id}")],
            [InlineKeyboardButton("🪓 Ghigliottina 1v1", callback_data=f"start_ghigliottina_prep_{owner_id}"), InlineKeyboardButton("⚔️ Duello Quiz 1v1", callback_data=f"start_quiz1v1_prep_{owner_id}")],
            [InlineKeyboardButton("🌐 Quiz Multi (Scegli Categoria)", callback_data=f"hub_qmulti_{owner_id}")],
            back_button
        ]
        try:
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        except Exception:
            pass

    elif action == "qmulti":
        text = "🌐 <b>QUIZ MULTIPLAYER PER CATEGORIA</b>\n\nScegli la categoria da lanciare in chat di gruppo:"
        keyboard = [
            [InlineKeyboardButton("🎲 Casuale", callback_data=f"start_qmulti_ALL_{owner_id}"), InlineKeyboardButton("⚽ Calcio", callback_data=f"start_qmulti_CALCIO_{owner_id}")],
            [InlineKeyboardButton("🏎️ Formula 1", callback_data=f"start_qmulti_F1_{owner_id}"), InlineKeyboardButton("🦸 Marvel & DC", callback_data=f"start_qmulti_MARVEL_{owner_id}")],
            [InlineKeyboardButton("🎬 Cinema", callback_data=f"start_qmulti_CINEMA_{owner_id}"), InlineKeyboardButton("📺 Serie TV", callback_data=f"start_qmulti_SERIE_{owner_id}")],
            [InlineKeyboardButton("🗺️ Paesi", callback_data=f"start_qmulti_PAESI_{owner_id}"), InlineKeyboardButton("🏮 Anime", callback_data=f"start_qmulti_ANIME_{owner_id}")],
            [InlineKeyboardButton("🏷️ Brand", callback_data=f"start_qmulti_BRANDS_{owner_id}"), InlineKeyboardButton("📜 Personaggi", callback_data=f"start_qmulti_PERSONAGGI_{owner_id}")],
            [InlineKeyboardButton("🎵 Canzoni", callback_data=f"start_qmulti_CANZONI_{owner_id}")],
            back_button
        ]
        try:
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        except Exception:
            pass

    elif action == "quiz":
        text = (
            "🧠 <b>QUIZ SHOW SINGLE PLAYER</b> (5 $SDG)\n\n"
            "Scegli una categoria:"
        )
        keyboard = [
            [InlineKeyboardButton("⚽ Calcio", callback_data=f"start_qcalcio_{owner_id}"), InlineKeyboardButton("🎬 Cinema", callback_data=f"start_qcinema_{owner_id}")],
            [InlineKeyboardButton("📺 Serie TV", callback_data=f"start_qserie_{owner_id}"), InlineKeyboardButton("🏎️ Formula 1", callback_data=f"start_qf1_{owner_id}")],
            [InlineKeyboardButton("🦸 Marvel & DC", callback_data=f"start_qmarvel_{owner_id}"), InlineKeyboardButton("🗺️ Paesi", callback_data=f"start_qpaesi_{owner_id}")],
            [InlineKeyboardButton("🏮 Anime", callback_data=f"start_qanime_{owner_id}"), InlineKeyboardButton("🏷️ Brand", callback_data=f"start_qbrands_{owner_id}")],
            [InlineKeyboardButton("📜 Personaggi", callback_data=f"start_qpersonaggi_{owner_id}"), InlineKeyboardButton("🎵 Canzoni", callback_data=f"start_qcanzoni_{owner_id}")],
            back_button
        ]
        try:
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        except Exception:
            pass

    elif action == "shop":
        inv_key = f"{chat_id}_{user_id}"
        inv = USER_INVENTORIES.get(inv_key, {"titles": 0, "persecutes": 0, "stars": 0})

        text = (
            "🛒 <b>SDROGOSHOP</b>\n\n"
            f"📦 <b>Inventario:</b> {inv.get('titles', 0)} Titoli | {inv.get('persecutes', 0)} Persecuzioni | {inv.get('stars', 0)} ⭐\n\n"
            "🏷️ <b>1. Titolo Umiliante (100 $SDG)</b>\nAssegna '🏳️‍🌈GAY🏳️‍🌈' a una vittima per 24 ore!\n\n"
            "🗣️ <b>2. Tag Persecutore (120 $SDG)</b>\nIl bot risponde 'frocio hah' ai prossimi 15 messaggi!\n\n"
            f"🏢 <b>3. Pass SDROGO HEIST ({HEIST_COST} $SDG)</b>\nRapina a 5 livelli in PRIVATO col bot per Jackpot + Stelle!"
        )
        keyboard = [
            [InlineKeyboardButton("🏷️ Compra Titolo (100 $SDG)", callback_data=f"buy_title_{owner_id}")],
            [InlineKeyboardButton("🗣️ Compra Tag Persecutore (120 $SDG)", callback_data=f"buy_persecute_{owner_id}")],
            [InlineKeyboardButton(f"🏢 Avvia SDROGO HEIST ({HEIST_COST} $SDG)", callback_data=f"buy_heist_{owner_id}")],
            back_button
        ]
        try:
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        except Exception:
            pass

    elif action == "wallet":
        await show_wallet(query, owner_id)

    elif action == "lead":
        await show_leaderboard_menu(update, context, owner_id)

    elif action == "leadcat":
        await show_leaderboard(update, context, owner_id, category=extra)


async def show_leaderboard_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, owner_id: int):
    query = update.callback_query
    text = "🏆 <b>CENTRO CLASSIFICHE D'ÉLITE</b> 👑\n\n<i>Scegli quale categoria vuoi consultare:</i>"
    keyboard = [
        [InlineKeyboardButton("🪙 Re dei Ricconi ($SDG)", callback_data=f"hub_leadcat_coins_{owner_id}")],
        [InlineKeyboardButton("🧠 Re dei Quiz", callback_data=f"hub_leadcat_quizzes_{owner_id}")],
        [InlineKeyboardButton("🎰 Re del Casinò", callback_data=f"hub_leadcat_casino_{owner_id}")],
        [InlineKeyboardButton("⚔️ Re dei Duelli", callback_data=f"hub_leadcat_duels_{owner_id}")],
        [InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{owner_id}")]
    ]
    try:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    except Exception:
        pass


async def show_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE, owner_id: int = None, category: str = "coins"):
    query = update.callback_query
    chat_id = query.message.chat_id if query else update.effective_chat.id
    current_user_id = query.from_user.id if query else update.effective_user.id

    if query and owner_id and not await verify_user_lock(query, owner_id): return

    prefix = f"{chat_id}_"

    LB = {
        "coins":   ("🪙 CLASSIFICA RICCONI $SDG", "coins", "$SDG"),
        "quizzes": ("🧠 RE DEI QUIZ", "quizzes_won", "Quiz vinti"),
        "casino":  ("🎰 RE DEL CASINÒ", "casino_wins", "Vittorie"),
        "duels":   ("⚔️ RE DEI DUELLI", "duels_wins", "Duelli vinti"),
    }
    title_name, field, unit = LB.get(category, LB["coins"])

    chat_users = [
        (k.rsplit("_", 1)[1], d.get(field, 0))
        for k, d in USER_DATA.items()
        if k.startswith(prefix) and d.get(field, 0) > 0
    ]
    chat_users.sort(key=lambda x: x[1], reverse=True)

    text = f"🏆 <b>{title_name}</b> 👑\n\n"
    medals = ["🥇", "🥈", "🥉"]

    if not chat_users:
        text += "<i>Nessun dato in questa classifica, per ora.</i>\n"

    for idx, (uid, val) in enumerate(chat_users[:10], start=1):
        rank_icon = medals[idx-1] if idx <= 3 else f"{idx}."
        try:
            member = await context.bot.get_chat_member(chat_id, int(uid))
            raw_name = member.user.first_name
            name = escape(get_formatted_name(chat_id, int(uid), raw_name))
        except Exception:
            name = f"Giocatore {uid[-4:]}"

        text += f"{rank_icon} <b>{name}</b> — <code>{val} {unit}</code>\n"

    keyboard = [
        [InlineKeyboardButton("🔙 Scegli altra Classifica", callback_data=f"hub_lead_{current_user_id}")],
        [InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{current_user_id}")]
    ]

    if query:
        try:
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        except Exception:
            pass
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")


@serialized("user")
async def claim_daily_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    owner_id = int(query.data.split("_")[-1])

    if not await verify_user_lock(query, owner_id):
        return

    chat_id, user_id = query.message.chat_id, query.from_user.id
    u_data = get_user_data_dict(chat_id, user_id)
    today = _today()

    if u_data.get("last_daily") == today:
        await query.answer("❌ Bonus giornaliero già riscosso oggi!", show_alert=True)
        return

    u_data["last_daily"] = today
    add_user_coins(chat_id, user_id, 50)
    await query.answer("🎉 Hai riscosso +💳 50 $SDG!", show_alert=True)
    await backup_to_telegram(context)
    await show_wallet(query, owner_id)


@serialized("user")
async def shop_buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    parts = query.data.split("_")
    item_type = parts[1]
    owner_id = int(parts[2]) if len(parts) > 2 else query.from_user.id

    if not await verify_user_lock(query, owner_id):
        return

    chat_id = query.message.chat_id
    user_id = query.from_user.id
    coins = get_user_coins(chat_id, user_id)
    inv_key = f"{chat_id}_{user_id}"

    if inv_key not in USER_INVENTORIES:
        USER_INVENTORIES[inv_key] = {"titles": 0, "persecutes": 0, "stars": 0}

    shop_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🛒 Torna allo Shop", callback_data=f"hub_shop_{owner_id}")],
        [InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{owner_id}")]
    ])

    if item_type == "title":
        if coins < 100:
            await query.answer("❌ Servono 100 $SDG per comprare il Titolo Umiliante!", show_alert=True)
            return
        await query.answer()
        add_user_coins(chat_id, user_id, -100)
        USER_INVENTORIES[inv_key]["titles"] = USER_INVENTORIES[inv_key].get("titles", 0) + 1
        try:
            await query.edit_message_text(
                "✅ <b>TITOLO UMILIANTE ACQUISTATO!</b>\n\n"
                "Per assegnarlo per 24 ORE a una vittima, scrivi in chat:\n"
                "👉 <code>titolo @username</code> (oppure rispondi al suo messaggio con <code>titolo</code>)",
                reply_markup=shop_keyboard,
                parse_mode="HTML"
            )
        except Exception:
            pass

    elif item_type == "persecute":
        if coins < 120:
            await query.answer("❌ Servono 120 $SDG per comprare il Tag Persecutore!", show_alert=True)
            return
        await query.answer()
        add_user_coins(chat_id, user_id, -120)
        USER_INVENTORIES[inv_key]["persecutes"] = USER_INVENTORIES[inv_key].get("persecutes", 0) + 1
        try:
            await query.edit_message_text(
                "✅ <b>TAG PERSECUTORE ACQUISTATO!</b>\n\n"
                "Per perseguitare una vittima per 15 messaggi, scrivi in chat:\n"
                "👉 <code>perseguita @username</code> (oppure rispondi al suo messaggio con <code>perseguita</code>)",
                reply_markup=shop_keyboard,
                parse_mode="HTML"
            )
        except Exception:
            pass

    elif item_type == "heist":
        if not game_enabled("heist"):
            await query.answer(MAINT_GAME_TEXT, show_alert=True)
            return
        existing = HEIST_GAMES.get(user_id)
        if existing:
            if time.time() - existing.get("ts", 0) < HEIST_STALE_SECS:
                await query.answer("⚠️ Hai già una rapina in corso: controlla la chat privata!", show_alert=True)
                return
            HEIST_GAMES.pop(user_id, None)   # rapina abbandonata: sessione scaduta, nessun rimborso
        if coins < HEIST_COST:
            await query.answer(f"❌ Servono {HEIST_COST} $SDG per tentare la Rapina Heist!", show_alert=True)
            return

        await query.answer()
        add_user_coins(chat_id, user_id, -HEIST_COST)
        HEIST_GAMES[user_id] = {"level": 1, "chat_id": chat_id, "pending": None, "ts": time.time()}

        try:
            await context.bot.send_message(
                chat_id=user_id,
                text="🏢 <b>SDROGO HEIST - LA RAPINA AL CAVEAU</b> 🕵️‍♂️\n\nBenvenuto al Livello 1! Devi disattivare l'allarme per entrare.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔓 Disattiva Allarme (Livello 1)", callback_data=f"heist_lvl1_{user_id}")]]),
                parse_mode="HTML"
            )
        except Exception:
            HEIST_GAMES.pop(user_id, None)
            add_user_coins(chat_id, user_id, HEIST_COST)
            try:
                await query.edit_message_text(
                    "❌ Devi prima avviare il bot in chat PRIVATA per giocare a Sdrogo Heist!",
                    reply_markup=shop_keyboard
                )
            except Exception:
                pass
            return

        try:
            await query.edit_message_text(
                "🏢 <b>LA RAPINA È INIZIATA!</b> Controlla la tua chat PRIVATA con SdrogoBot!",
                reply_markup=shop_keyboard,
                parse_mode="HTML"
            )
        except Exception:
            pass


async def apply_title_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    inv_key = f"{chat_id}_{user.id}"
    text = (update.message.text or "").strip()

    if inv_key not in USER_INVENTORIES or USER_INVENTORIES[inv_key].get("titles", 0) <= 0:
        await update.message.reply_text("❌ Non possiedi alcun Titolo Umiliante: compralo nello SdrogoShop dall'HUB (/sdrogocomm)!", parse_mode="HTML")
        return

    target_username = None
    target_id = None

    for part in text.split():
        if part.startswith("@"):
            target_username = part.replace("@", "").lower()
            break

    # Risposta a un messaggio: l'ID è già noto, niente scansione del registro
    reply = update.message.reply_to_message
    if not target_username and reply and reply.from_user:
        target_id = reply.from_user.id
        target_username = reply.from_user.username.lower() if reply.from_user.username else str(reply.from_user.first_name)
        if reply.from_user.is_bot:
            await update.message.reply_text("❌ Non puoi assegnare un titolo a un bot!", parse_mode="HTML")
            return

    if not target_username:
        await update.message.reply_text("❌ Uso: <code>titolo @username</code> oppure rispondi al suo messaggio con <code>titolo</code>!", parse_mode="HTML")
        return

    if not target_id:
        prefix = f"{chat_id}_"
        for k in list(USER_DATA.keys()):
            if k.startswith(prefix):
                uid = k.rsplit("_", 1)[1]
                try:
                    m = await context.bot.get_chat_member(chat_id, int(uid))
                    if m.user.username and m.user.username.lower() == target_username:
                        target_id = int(uid)
                        break
                except Exception:
                    pass

    if not target_id:
        await update.message.reply_text("❌ Utente non trovato nel registro della chat!", parse_mode="HTML")
        return

    USER_INVENTORIES[inv_key]["titles"] -= 1
    expire_time = datetime.now() + timedelta(hours=24)
    ACTIVE_TITLES[f"{chat_id}_{target_id}"] = {"title": "🏳️‍🌈GAY🏳️‍🌈", "expire": expire_time}
    await update.message.reply_text(f"🔥 <b>TITOLO ASSEGNATO!</b> Per 24 ORE {escape(target_username)} sarà chiamato '🏳️‍🌈GAY🏳️‍🌈' dal bot!", parse_mode="HTML")

async def apply_persecute_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    inv_key = f"{chat_id}_{user.id}"
    text = (update.message.text or "").strip()

    if inv_key not in USER_INVENTORIES or USER_INVENTORIES[inv_key].get("persecutes", 0) <= 0:
        await update.message.reply_text("❌ Non possiedi alcun Tag Persecutore: compralo nello SdrogoShop dall'HUB (/sdrogocomm)!", parse_mode="HTML")
        return

    target_username = None
    for part in text.split():
        if part.startswith("@"):
            target_username = part.replace("@", "").lower()
            break

    reply = update.message.reply_to_message
    if not target_username and reply and reply.from_user and reply.from_user.username:
        target_username = reply.from_user.username.lower()

    if not target_username:
        await update.message.reply_text("❌ Uso corretto: <code>perseguita @username</code>", parse_mode="HTML")
        return

    USER_INVENTORIES[inv_key]["persecutes"] -= 1
    ACTIVE_PERSECUTE[f"{chat_id}_{target_username}"] = {"count": 15, "phrase": "frocio hah"}
    await update.message.reply_text(f"😈 <b>PERSECUZIONE ATTIVATA!</b> I prossimi 15 messaggi di @{escape(target_username)} riceveranno risposta 'frocio hah' dal bot!", parse_mode="HTML")

async def block_direct_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚫 <b>I giochi si avviano solo dall'HUB!</b>\nUsa /sdrogocomm per accedere.", parse_mode="HTML")

def register(app):
    app.add_handler(CommandHandler("sdrogocomm", show_hub))
    app.add_handler(CommandHandler("topricconi", show_leaderboard))
    for cmd in ["roulette", "blackjack", "slot", "highlow", "wordle", "quiz", "shop", "heist", "sfidodadi", "sfidotris"]:
        app.add_handler(CommandHandler(cmd, block_direct_command))
    app.add_handler(CallbackQueryHandler(hub_callback, pattern="^hub_"))
    app.add_handler(CallbackQueryHandler(shop_buy_callback, pattern="^buy_"))
    app.add_handler(CallbackQueryHandler(claim_daily_callback, pattern="^claim_daily_"))
