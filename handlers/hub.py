from datetime import datetime, timedelta
from html import escape
import asyncio
import logging
import random
import time

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CommandHandler, CallbackQueryHandler, TypeHandler, ContextTypes

from economy import (
    inv, fmt, charge as eco_charge, banco_add, banco_take, banco_balance, follia_active,
    challenge_text, quests_text, spin_daily, wheel_prizes_text, DAILY_WHEEL, today_str,
)
from state import (
    USER_DATA, USER_INVENTORIES, ACTIVE_TITLES, ACTIVE_PERSECUTE, HEIST_GAMES, GROUP_CHALLENGE,
    game_enabled, MAINT_GAME_TEXT,
)
from storage import get_user_coins, add_user_coins, get_user_data_dict, backup_to_telegram
from utils import verify_user_lock, get_formatted_name, serialized, is_v2, smart_edit, set_chat_type
from handlers.games import HEIST_COST
from handlers.heist import heist_new_game, heist_intro_text, heist_start_kb
from handlers import v2

HEIST_STALE_SECS = 1800   # una rapina abbandonata per 30 minuti viene considerata chiusa
WHEEL_TICK = 0.8          # secondi tra un fotogramma e l'altro della Ruota Daily
WHEEL_FRAMES = 6

# --- AUTO-ELIMINAZIONE DEI MESSAGGI HUB ABBANDONATI (solo gruppi) ---
# Registro dei messaggi che sono ANCORA una schermata HUB: (chat_id, message_id) -> {"ts": ultima attività}.
# Un messaggio esce dal registro appena diventa una schermata di gioco; il job cancella solo chi è ancora dentro.
# Non riguarda in nessun modo i timer di gameplay (/lavora, Heist, quiz, Aviator...).
HUB_TTL = 300             # secondi di inattività dopo i quali un HUB nel gruppo viene eliminato
HUB_CLEANUP_EVERY = 30    # frequenza del controllo
GROUP_TYPES = ("group", "supergroup")
HUB_MESSAGES = {}
# Callback che lasciano il messaggio una schermata HUB (menu, shop, portafoglio, istruzioni di sfida a parole).
# Qualunque altro callback (start_bj_, sp_*, slot_*, heist_*, dice_*, cfg_*, ...) avvia un gioco: il messaggio esce dal registro.
HUB_PREFIXES = (
    "hub_", "buy_", "claim_daily_", "inv_",
    "start_dice_", "start_ttt_", "start_roulette_", "start_highlow_",
    "start_ghigliottina_prep_", "start_quiz1v1_prep_",
)


def _track_hub(chat_id: int, message_id: int):
    HUB_MESSAGES[(chat_id, message_id)] = {"ts": time.time()}


async def bind_context_and_track(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Eseguito all'inizio di OGNI update (group=-3):
    1) registra il tipo di chat, da cui dipende la visibilità della V2 (is_v2);
    2) mantiene il registro dei messaggi HUB dei gruppi: li rinnova se restano HUB, li toglie se partono i giochi."""
    chat = update.effective_chat
    set_chat_type(chat.type if chat else None)

    q = update.callback_query
    if not q or not q.message or q.message.chat.type not in GROUP_TYPES:
        return
    key = (q.message.chat_id, q.message.message_id)
    if (q.data or "").startswith(HUB_PREFIXES):
        _track_hub(*key)
    else:
        HUB_MESSAGES.pop(key, None)             # un gioco sta partendo: questo messaggio non va MAI cancellato


async def cleanup_hub_messages(context: ContextTypes.DEFAULT_TYPE):
    """Job periodico: elimina gli HUB abbandonati da più di HUB_TTL secondi, solo se sono ancora schermate HUB."""
    now = time.time()
    for key, info in list(HUB_MESSAGES.items()):
        if now - info["ts"] < HUB_TTL:
            continue
        if HUB_MESSAGES.pop(key, None) is None:     # ricontrollo: se un gioco è partito nel frattempo la voce non c'è più
            continue
        try:
            await context.bot.delete_message(chat_id=key[0], message_id=key[1])
        except Exception as e:
            logging.debug(f"HUB {key} non eliminato: {e}")


async def show_hub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    coins = get_user_coins(chat_id, user.id)
    display_name = escape(get_formatted_name(chat_id, user.id, user.first_name))
    v2u = is_v2(user.id)

    text = (
        "🎰 <b>SDROGOBOT ARCADE HUB</b> 🎮\n\n"
        f"👤 <b>Player:</b> {display_name}\n"
        f"💰 <b>Saldo:</b> <code>💳 {fmt(coins)} $SDG</code>\n"
    )
    if v2u:
        text += f"🏦 <b>Banco Centrale:</b> <code>{fmt(banco_balance())} $SDG</code>\n"
        if follia_active():
            text += "🎪 <b>FOLLIA SDROGO ATTIVA!</b> Vincite potenziate! 🎉\n"
    text += "\n<i>Seleziona una categoria per iniziare:</i>"

    keyboard = [
        [InlineKeyboardButton("🕹️ Single Player", callback_data=f"hub_single_{user.id}"), InlineKeyboardButton("⚔️ Multiplayer", callback_data=f"hub_multi_{user.id}")],
        [InlineKeyboardButton("🧠 Quiz Show", callback_data=f"hub_quiz_{user.id}"), InlineKeyboardButton("🛒 SdrogoShop", callback_data=f"hub_shop_{user.id}")],
        [InlineKeyboardButton("💳 Portafoglio", callback_data=f"hub_wallet_{user.id}"), InlineKeyboardButton("🏆 Classifiche", callback_data=f"hub_lead_{user.id}")]
    ]
    if v2u:
        keyboard.append([InlineKeyboardButton("🎯 SFIDE & MISSIONI", callback_data=f"hub_quests_{user.id}")])

    if update.message:
        sent = await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        if update.effective_chat.type in GROUP_TYPES:
            _track_hub(sent.chat_id, sent.message_id)       # parte il timer di auto-eliminazione
    elif update.callback_query:
        await smart_edit(update.callback_query, text, InlineKeyboardMarkup(keyboard))


async def show_wallet(query, owner_id: int):
    chat_id = query.message.chat_id
    user_id = query.from_user.id
    coins = get_user_coins(chat_id, user_id)
    u_data = get_user_data_dict(chat_id, user_id)
    v2u = is_v2(user_id)

    q_wins = u_data.get("quizzes_won", 0)
    c_wins = u_data.get("casino_wins", 0)
    d_wins = u_data.get("duels_wins", 0)
    net_profit = u_data.get("net_profit", 0)

    single_played = u_data.get("single_played", 0)
    single_won = u_data.get("single_won", c_wins)
    wr_single = min(100, int(single_won / single_played * 100)) if single_played > 0 else 0

    duels_played = u_data.get("duels_played", 0)
    wr_duels = min(100, int(d_wins / duels_played * 100)) if duels_played > 0 else 0

    stars = USER_INVENTORIES.get(f"{chat_id}_{user_id}", {}).get("stars", 0)
    profit_str = f"+{fmt(net_profit)}" if net_profit >= 0 else f"-{fmt(-net_profit)}"

    text = (
        "💳 <b>PORTAFOGLIO & PROFILO</b>\n\n"
        f"👤 Giocatore: <b>{escape(query.from_user.first_name)}</b>\n"
        f"💰 Saldo attuale: <code>💳 {fmt(coins)} $SDG</code>\n"
        f"📈 Profitto Netto: <code>{profit_str} $SDG</code>\n"
        f"⭐ Stelle Heist: <b>{stars}</b>\n\n"
        "📊 <b>STORICO & STATISTICHE:</b>\n"
        f"🧠 Quiz Vinti: <b>{q_wins}</b>\n"
        f"🎰 Vittorie Casinò: <b>{c_wins}</b>\n"
        f"⚔️ Duelli Vinti: <b>{d_wins}</b>\n\n"
        "🎯 <b>PERFORMANCE:</b>\n"
        f"• Win Rate Single Player: <b>{wr_single}%</b>\n"
        f"• Win Rate Duelli: <b>{wr_duels}%</b>\n\n"
    )
    if v2u:
        text += (
            f"🎡 <b>Ruota Daily:</b> gira una volta al giorno! Premi: {wheel_prizes_text()} $SDG\n"
            "💼 <i>Al verde? Usa /lavora per guadagnare 50 $SDG.</i>\n"
        )
        keyboard = [
            [InlineKeyboardButton("🎡 Gira la Ruota Daily", callback_data=f"claim_daily_{owner_id}")],
            [InlineKeyboardButton("🎒 Inventario", callback_data=f"hub_inv_{owner_id}")],
            [InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{owner_id}")]
        ]
    else:
        text += "🎁 <b>Bonus Daily:</b> Riscuoti 50 $SDG una volta al giorno (si azzera a mezzanotte).\n"
        keyboard = [
            [InlineKeyboardButton("🎁 Riscuoti Daily (+50 $SDG)", callback_data=f"claim_daily_{owner_id}")],
            [InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{owner_id}")]
        ]
    text += "💸 <i>Usa /dona @username [importo] per regalare da 1 a 50 $SDG a un amico (5 se non indichi l'importo)!</i>"
    await smart_edit(query, text, InlineKeyboardMarkup(keyboard))


def _shop_view(chat_id: int, user_id: int, owner_id: int, back_button: list):
    v2u = is_v2(user_id)
    d = inv(chat_id, user_id)
    if not v2u:
        text = (
            "🛒 <b>SDROGOSHOP</b>\n\n"
            f"📦 <b>Inventario:</b> {d['titles']} Titoli | {d['persecutes']} Persecuzioni | {d['stars']} ⭐\n\n"
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
        return text, InlineKeyboardMarkup(keyboard)

    text = (
        "🛒 <b>SDROGOSHOP</b>\n\n"
        f"💳 Saldo Attuale: <b>{fmt(get_user_coins(chat_id, user_id))} $SDG</b>\n"
        "🎒 Gli oggetti acquistati finiscono nell'Inventario.\n\n"
    )
    keyboard = []
    for key, _field, price, label, short, _how in v2.SHOP_ITEMS:
        text += f"<b>{label}</b> — {price} $SDG\n<i>{short}</i>\n\n"
        keyboard.append([InlineKeyboardButton(f"{label} ({price})", callback_data=f"buy_{key}_{owner_id}")])
    text += f"🏢 <b>Pass SDROGO HEIST</b> — {HEIST_COST} $SDG\n<i>Escape room a tempo in PRIVATO: svaligia il Banco Centrale!</i>"
    keyboard.append([InlineKeyboardButton(f"🏢 Avvia SDROGO HEIST ({HEIST_COST})", callback_data=f"buy_heist_{owner_id}")])
    keyboard.append([InlineKeyboardButton("🎒 Inventario", callback_data=f"hub_inv_{owner_id}")])
    keyboard.append(back_button)
    return text, InlineKeyboardMarkup(keyboard)


async def hub_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    parts = query.data.split("_")
    action = parts[1]
    extra = None

    if action == "leadcat":
        extra = parts[2]
        owner_id = int(parts[3])
    else:
        owner_id = int(parts[2]) if len(parts) > 2 else query.from_user.id

    # verify_user_lock risponde con il popup d'errore: query.answer() va chiamata solo dopo i controlli
    if not await verify_user_lock(query, owner_id):
        return

    if action == "qmulti" and not game_enabled("quizmulti"):
        await query.answer(MAINT_GAME_TEXT, show_alert=True)
        return

    v2u = is_v2(query.from_user.id)
    if action in ("quests", "inv") and not v2u:
        await query.answer("🎯 Non ancora disponibile!", show_alert=True)
        return

    await query.answer()

    chat_id = query.message.chat_id
    user_id = query.from_user.id
    back_button = [InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{owner_id}")]

    if action == "main":
        await show_hub(update, context)

    elif action == "single":
        if v2u:
            text = (
                "🕹️ <b>GIOCHI SINGLE PLAYER</b>\n\n"
                "🃏 <b>Blackjack 21</b> • 🎰 <b>Slot 777</b>\n"
                "🔠 <b>Wordle</b> (10 $SDG) • 🔐 <b>Mastermind</b> (10 $SDG)\n"
                "✈️ <b>Aviator</b> • 🎡 <b>Sdrogo Roulette</b>\n"
                "🎟️ <b>Gratta e Vinci</b> (20 $SDG) • 🃏 <b>Sdrogo Poker</b> (1v1 contro il Bot)\n"
                "🏢 <b>Sdrogo Heist</b> — pass nello SdrogoShop\n\n"
                "<i>Puntate da 10 a 100 $SDG dove previsto.</i>"
            )
            keyboard = [
                [InlineKeyboardButton("🃏 Blackjack", callback_data=f"start_bj_{owner_id}"), InlineKeyboardButton("🎰 Slot 777", callback_data=f"start_slot_{owner_id}")],
                [InlineKeyboardButton("🔠 Wordle", callback_data=f"start_wordle_{owner_id}"), InlineKeyboardButton("🔐 Mastermind", callback_data=f"start_mm_{owner_id}")],
                [InlineKeyboardButton("✈️ Aviator", callback_data=f"sp_avi_{owner_id}"), InlineKeyboardButton("🎡 Roulette", callback_data=f"sp_roul_{owner_id}")],
                [InlineKeyboardButton("🎟️ Gratta e Vinci", callback_data=f"sp_scr_{owner_id}"), InlineKeyboardButton("🃏 Sdrogo Poker", callback_data=f"sp_pk_{owner_id}")],
                back_button
            ]
        else:
            text = (
                "🕹️ <b>GIOCHI SINGLE PLAYER</b>\n\n"
                "🃏 <b>Blackjack 21</b> — <i>puntata da 10 a 100 $SDG</i>\n"
                "🎰 <b>Slot Machine 777</b> — <i>puntata da 10 a 100 $SDG</i>\n"
                "🔠 <b>Wordle Express</b> — <i>10 $SDG</i>\n"
                "🔐 <b>Mastermind Express</b> — <i>10 $SDG</i>\n"
                "✈️ <b>Sdrogo Aviator</b> — <i>puntata da 10 a 100 $SDG, incassa prima del crash!</i>\n"
                "🎡 <b>Sdrogo Roulette</b> — <i>puntata da 10 a 100 $SDG</i>\n"
                "🎟️ <b>Gratta e Vinci</b> — <i>biglietto da 20 $SDG</i>\n"
                "🏢 <b>Sdrogo Heist</b> — <i>pass acquistabile nello SdrogoShop</i>"
            )
            keyboard = [
                [InlineKeyboardButton("🃏 Blackjack", callback_data=f"start_bj_{owner_id}"), InlineKeyboardButton("🎰 Slot 777", callback_data=f"start_slot_{owner_id}")],
                [InlineKeyboardButton("🔠 Wordle", callback_data=f"start_wordle_{owner_id}"), InlineKeyboardButton("🔐 Mastermind", callback_data=f"start_mm_{owner_id}")],
                [InlineKeyboardButton("✈️ Aviator", callback_data=f"sp_avi_{owner_id}"), InlineKeyboardButton("🎡 Roulette", callback_data=f"sp_roul_{owner_id}")],
                [InlineKeyboardButton("🎟️ Gratta e Vinci", callback_data=f"sp_scr_{owner_id}")],
                back_button
            ]
        await smart_edit(query, text, InlineKeyboardMarkup(keyboard))

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
        await smart_edit(query, text, InlineKeyboardMarkup(keyboard))

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
        await smart_edit(query, text, InlineKeyboardMarkup(keyboard))

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
        await smart_edit(query, text, InlineKeyboardMarkup(keyboard))

    elif action == "shop":
        text, kb = _shop_view(chat_id, user_id, owner_id, back_button)
        await smart_edit(query, text, kb)

    elif action == "inv":
        text, kb = v2.inventory_view(chat_id, user_id, owner_id)
        await smart_edit(query, text, kb)

    elif action == "quests":
        parts_txt = []
        if GROUP_CHALLENGE.get("active"):
            parts_txt.append(challenge_text())
        else:
            parts_txt.append("🎯 <b>SFIDA DI GRUPPO</b>\nNessuna sfida attiva al momento: la prossima partirà presto!")
        parts_txt.append(quests_text(chat_id, user_id))
        await smart_edit(query, "\n\n".join(parts_txt), InlineKeyboardMarkup([back_button]))

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
    await smart_edit(query, text, InlineKeyboardMarkup(keyboard))


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

        text += f"{rank_icon} <b>{name}</b> — <code>{fmt(val)} {unit}</code>\n"

    keyboard = [
        [InlineKeyboardButton("🔙 Scegli altra Classifica", callback_data=f"hub_lead_{current_user_id}")],
        [InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{current_user_id}")]
    ]

    if query:
        await smart_edit(query, text, InlineKeyboardMarkup(keyboard))
    else:
        sent = await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        if update.effective_chat.type in GROUP_TYPES:
            _track_hub(sent.chat_id, sent.message_id)       # anche la classifica aperta da comando è una schermata HUB


@serialized("user")
async def claim_daily_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    owner_id = int(query.data.split("_")[-1])

    if not await verify_user_lock(query, owner_id):
        return

    chat_id, user_id = query.message.chat_id, query.from_user.id
    u_data = get_user_data_dict(chat_id, user_id)
    today = today_str()

    if u_data.get("last_daily") == today:
        await query.answer("❌ Bonus giornaliero già riscosso oggi!", show_alert=True)
        return

    # --- V1: bonus fisso da 50 $SDG ---
    if not is_v2(user_id):
        u_data["last_daily"] = today
        add_user_coins(chat_id, user_id, 50)
        await query.answer("🎉 Hai riscosso +💳 50 $SDG!", show_alert=True)
        await backup_to_telegram(context)
        await show_wallet(query, owner_id)
        return

    # --- V2: Ruota Daily. Premio estratto e pagato PRIMA dell'animazione ---
    u_data["last_daily"] = today
    prize = spin_daily()
    add_user_coins(chat_id, user_id, prize)
    await query.answer()

    name = escape(query.from_user.first_name or "Giocatore")
    head = f"🎡 <b>RUOTA DAILY</b> 🎡\n👤 Player: <b>{name}</b>\n\n"
    prizes = [p for p, _ in DAILY_WHEEL]
    for _ in range(WHEEL_FRAMES):
        reel = [random.choice(prizes) for _ in range(3)]
        await smart_edit(query, head + f"[ {reel[0]} | {reel[1]} | {reel[2]} ]\n\n<i>La ruota gira...</i>")
        await asyncio.sleep(WHEEL_TICK)

    side = [random.choice(prizes), random.choice(prizes)]
    if prize >= 500:
        verdict = f"💥🎉 <b>SUPER JACKPOT!!!</b> Hai vinto <b>+{fmt(prize)} $SDG</b>! 🎉💥"
    else:
        verdict = f"🎉 Hai vinto <b>+{fmt(prize)} $SDG</b>!"
    final = (
        head + f"[ {side[0]} | <b>{prize}</b> | {side[1]} ]\n\n{verdict}\n"
        f"💳 Saldo Attuale: <b>{fmt(get_user_coins(chat_id, user_id))} $SDG</b>\n\n"
        "<i>Torna domani per un nuovo giro!</i>"
    )
    await smart_edit(query, final, InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Portafoglio", callback_data=f"hub_wallet_{owner_id}")],
        [InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{owner_id}")]
    ]))


@serialized("user")
async def shop_buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    parts = query.data.split("_")
    item = parts[1]
    owner_id = int(parts[2]) if len(parts) > 2 else query.from_user.id

    if not await verify_user_lock(query, owner_id):
        return

    chat_id = query.message.chat_id
    user_id = query.from_user.id
    coins = get_user_coins(chat_id, user_id)
    v2u = is_v2(user_id)
    d = inv(chat_id, user_id)

    shop_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🛒 Torna allo Shop", callback_data=f"hub_shop_{owner_id}")],
        [InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{owner_id}")]
    ])

    # ---------------- PASS HEIST ----------------
    if item == "heist":
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
        if v2u:
            # escape room a tempo: il costo del pass alimenta il Banco Centrale (e conta nella sfida di gruppo)
            eco_charge(chat_id, user_id, HEIST_COST)
            banco_add(HEIST_COST)
            HEIST_GAMES[user_id] = heist_new_game(chat_id)
            private_text, private_kb = heist_intro_text(), heist_start_kb(user_id)
        else:
            add_user_coins(chat_id, user_id, -HEIST_COST)
            HEIST_GAMES[user_id] = {"level": 1, "chat_id": chat_id, "pending": None, "ts": time.time()}
            private_text = ("🏢 <b>SDROGO HEIST - LA RAPINA AL CAVEAU</b> 🕵️‍♂️\n\n"
                            "Benvenuto al Livello 1! Devi disattivare l'allarme per entrare.")
            private_kb = InlineKeyboardMarkup([[InlineKeyboardButton(
                "🔓 Disattiva Allarme (Livello 1)", callback_data=f"heist_lvl1_{user_id}")]])

        try:
            await context.bot.send_message(chat_id=user_id, text=private_text, reply_markup=private_kb, parse_mode="HTML")
        except Exception:
            # il giocatore non ha mai avviato il bot in privato: annulla e rimborsa tutto
            HEIST_GAMES.pop(user_id, None)
            add_user_coins(chat_id, user_id, HEIST_COST)
            if v2u:
                banco_take(HEIST_COST)
            await smart_edit(query, "❌ Devi prima avviare il bot in chat PRIVATA per giocare a Sdrogo Heist!", shop_keyboard)
            return

        await smart_edit(query, "🏢 <b>LA RAPINA È INIZIATA!</b> Controlla la tua chat PRIVATA con SdrogoBot!", shop_keyboard)
        return

    # ---------------- OGGETTI DELLO SHOP ----------------
    spec = v2.SHOP_BY_KEY.get(item)
    if not spec:
        await query.answer()
        return
    if not v2u and item not in ("title", "persecute"):
        await query.answer("🛒 Oggetto non ancora disponibile!", show_alert=True)
        return
    _key, field, price, label, _short, how = spec
    if coins < price:
        await query.answer(f"❌ Servono {price} $SDG per comprare {label}!", show_alert=True)
        return

    await query.answer()
    d[field] = d.get(field, 0) + 1
    add_user_coins(chat_id, user_id, -price)       # salva anche l'inventario nel database

    if not v2u and item == "persecute":
        how = ("Per perseguitare una vittima per 15 messaggi, scrivi in chat:\n"
               "👉 <code>perseguita @username</code> (oppure rispondi al suo messaggio con <code>perseguita</code>)")
    await smart_edit(
        query,
        f"✅ <b>{label} ACQUISTATO!</b>\n\n{how}\n\n💳 Saldo Attuale: <b>{fmt(get_user_coins(chat_id, user_id))} $SDG</b>",
        shop_keyboard
    )


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

    # V2: "perseguita" è la reazione forzata per 15 minuti (menu inline per scegliere l'emoji)
    if is_v2(user.id):
        await v2.persecute_command(update, context)
        return

    # V1: comportamento originale (risposta fissa ai prossimi 15 messaggi)
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
    # group=-3: gira per primo su OGNI update (tipo di chat per is_v2 + registro dei messaggi HUB)
    app.add_handler(TypeHandler(Update, bind_context_and_track), group=-3)
    app.add_handler(CommandHandler("sdrogocomm", show_hub))
    app.add_handler(CommandHandler("topricconi", show_leaderboard))
    for cmd in ["roulette", "blackjack", "slot", "highlow", "wordle", "quiz", "shop", "heist", "sfidodadi", "sfidotris"]:
        app.add_handler(CommandHandler(cmd, block_direct_command))
    app.add_handler(CallbackQueryHandler(hub_callback, pattern="^hub_"))
    app.add_handler(CallbackQueryHandler(shop_buy_callback, pattern="^buy_"))
    # block=False: l'animazione della Ruota Daily non ferma gli altri giocatori
    app.add_handler(CallbackQueryHandler(claim_daily_callback, pattern="^claim_daily_", block=False))
    if app.job_queue:
        app.job_queue.run_repeating(cleanup_hub_messages, interval=HUB_CLEANUP_EVERY, first=HUB_CLEANUP_EVERY)
