from datetime import date
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CommandHandler, CallbackQueryHandler, ContextTypes

from state import USER_DATA, USER_INVENTORIES
from storage import get_user_key, get_user_coins, add_user_coins, backup_to_telegram
from utils import verify_user_lock, get_formatted_name

async def show_hub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    coins = get_user_coins(chat_id, user.id)
    display_name = get_formatted_name(chat_id, user.id, user.first_name)
    
    text = (
        "🎰 <b>SDROGOBOT ARCADE HUB</b> 🎮\n\n"
        f"👤 <b>Player:</b> {display_name}\n"
        f"💰 <b>Saldo:</b> <code>💳 {coins} $SDG</code>\n\n"
        "<i>Seleziona una categoria per iniziare:</i>"
    )
    
    keyboard = [
        [InlineKeyboardButton("🕹️ Single Player", callback_data=f"hub_single_{user.id}"), InlineKeyboardButton("⚔️ Multiplayer", callback_data=f"hub_multi_{user.id}")],
        [InlineKeyboardButton("🧠 Quiz Show", callback_data=f"hub_quiz_{user.id}"), InlineKeyboardButton("🛒 SdrogoShop", callback_data=f"hub_shop_{user.id}")],
        [InlineKeyboardButton("💳 Portafoglio", callback_data=f"hub_wallet_{user.id}"), InlineKeyboardButton("🏆 Classifica", callback_data=f"hub_lead_{user.id}")]
    ]
    
    if update.message:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    elif update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

async def hub_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    parts = data.split("_")
    action = parts[1]
    owner_id = int(parts[2]) if len(parts) > 2 else query.from_user.id

    if not await verify_user_lock(query, owner_id):
        return

    chat_id = query.message.chat_id
    user_id = query.from_user.id
    coins = get_user_coins(chat_id, user_id)

    back_button = [InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{owner_id}")]

    if action == "main":
        await show_hub(update, context)

    elif action == "single":
        text = (
            "🕹️ <b>GIOCHI SINGLE PLAYER</b>\n\n"
            "🃏 <b>Blackjack 21</b> — <i>10 $SDG</i>\n"
            "🎰 <b>Slot Machine 777</b> — <i>10 $SDG</i>\n"
            "🔠 <b>Wordle Express</b> — <i>10 $SDG</i>\n"
            "🔐 <b>Mastermind Express</b> — <i>10 $SDG</i>"
        )
        keyboard = [
            [InlineKeyboardButton("🃏 Blackjack", callback_data=f"start_bj_{owner_id}"), InlineKeyboardButton("🎰 Slot 777", callback_data=f"start_slot_{owner_id}")],
            [InlineKeyboardButton("🔠 Wordle", callback_data=f"start_wordle_{owner_id}"), InlineKeyboardButton("🔐 Mastermind", callback_data=f"start_mm_{owner_id}")],
            back_button
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif action == "multi":
        text = (
            "⚔️ <b>GIOCHI MULTIPLAYER</b>\n\n"
            "🎯 <b>Roulette Russa 1v1</b>\n"
            "🎲 <b>High / Low 1v1</b>\n"
            "🪓 <b>Ghigliottina Express 1v1</b> (`sfidoghigliottina @user`)\n"
            "⚔️ <b>Duello Quiz 1v1</b> (`sfidoquiz @user`)\n"
            "🌐 <b>Quiz Multiplayer</b> (Aperto a tutto il gruppo)"
        )
        keyboard = [
            [InlineKeyboardButton("🎯 Roulette 1v1", callback_data=f"start_roulette_{owner_id}"), InlineKeyboardButton("🎲 High/Low 1v1", callback_data=f"start_highlow_{owner_id}")],
            [InlineKeyboardButton("🪓 Ghigliottina 1v1", callback_data=f"start_ghigliottina_prep_{owner_id}"), InlineKeyboardButton("⚔️ Duello Quiz 1v1", callback_data=f"start_quiz1v1_prep_{owner_id}")],
            [InlineKeyboardButton("🌐 Quiz Multi (Scegli Categoria)", callback_data=f"hub_qmulti_{owner_id}")],
            back_button
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

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
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

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
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif action == "shop":
        inv_key = f"{chat_id}_{user_id}"
        inv = USER_INVENTORIES.get(inv_key, {"titles": 0, "persecutes": 0})
        
        text = (
            "🛒 <b>SDROGOSHOP</b>\n\n"
            f"📦 <b>Inventario:</b> {inv.get('titles', 0)} Titoli | {inv.get('persecutes', 0)} Persecuzioni\n\n"
            "🏷️ <b>1. Titolo Umiliante (100 $SDG)</b>\nAssegna '🏳️‍🌈GAY🏳️‍🌈' a una vittima per 24 ore!\n\n"
            "🗣️ <b>2. Tag Persecutore (120 $SDG)</b>\nIl bot risponde 'frocio hah' ai prossimi 15 messaggi!\n\n"
            "🏢 <b>3. Pass SDROGO HEIST (350 $SDG)</b>\nRapina a 5 livelli in PRIVATO col bot per Jackpot + Stelle!"
        )
        keyboard = [
            [InlineKeyboardButton("🏷️ Compra Titolo (100 $SDG)", callback_data=f"buy_title_{owner_id}")],
            [InlineKeyboardButton("🗣️ Compra Tag Persecutore (120 $SDG)", callback_data=f"buy_persecute_{owner_id}")],
            [InlineKeyboardButton("🏢 Avvia SDROGO HEIST (350 $SDG)", callback_data=f"buy_heist_{owner_id}")],
            back_button
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif action == "wallet":
        text = (
            "💳 <b>PORTAFOGLIO</b>\n\n"
            f"👤 Giocatore: <b>{query.from_user.first_name}</b>\n"
            f"💰 Saldo attuale: <code>💳 {coins} $SDG</code>\n\n"
            "🎁 <b>Bonus Daily:</b> Riscuoti 50 $SDG ogni 24 ore."
        )
        keyboard = [
            [InlineKeyboardButton("🎁 Riscuoti Daily (+50 $SDG)", callback_data=f"claim_daily_{owner_id}")],
            back_button
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif action == "lead":
        await show_leaderboard(update, context, owner_id)

async def show_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE, owner_id: int = None):
    query = update.callback_query
    chat_id = query.message.chat_id if query else update.effective_chat.id
    current_user_id = query.from_user.id if query else update.effective_user.id

    if query and owner_id and not await verify_user_lock(query, owner_id): return

    prefix = f"{chat_id}_"
    chat_users = []

    for key, data in USER_DATA.items():
        if key.startswith(prefix):
            uid = key.split("_")[1]
            coins = data.get("coins", 0)
            chat_users.append((uid, coins))

    chat_users.sort(key=lambda x: x[1], reverse=True)
    text = "🏆 <b>CLASSIFICA RICCONI $SDG</b> 💰\n\n"
    medals = ["🥇", "🥈", "🥉"]

    for idx, (uid, coins) in enumerate(chat_users[:10], start=1):
        rank_icon = medals[idx-1] if idx <= 3 else f"{idx}."
        try:
            member = await context.bot.get_chat_member(chat_id, int(uid))
            raw_name = member.user.first_name
            name = get_formatted_name(chat_id, int(uid), raw_name)
        except Exception:
            name = f"Giocatore {uid[-4:]}"

        text += f"{rank_icon} <b>{name}</b> — <code>💳 {coins} $SDG</code>\n"

    keyboard = [[InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{current_user_id}")]]

    if query:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

async def claim_daily_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split("_")
    owner_id = int(parts[2]) if len(parts) > 2 else query.from_user.id

    if not await verify_user_lock(query, owner_id): return

    chat_id = query.message.chat_id
    user_id = query.from_user.id
    key = get_user_key(chat_id, user_id)
    today = str(date.today())

    if key not in USER_DATA: USER_DATA[key] = {"coins": 50, "last_daily": ""}

    if USER_DATA[key].get("last_daily") == today:
        await query.answer("❌ Bonus giornaliero già riscosso oggi!", show_alert=True)
    else:
        USER_DATA[key]["last_daily"] = today
        add_user_coins(chat_id, user_id, 50)
        await query.answer("🎉 Hai riscosso +💳 50 $SDG!", show_alert=True)
        await backup_to_telegram(context)
        await hub_callback(update, context)

async def block_direct_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚫 <b>I giochi si avviano solo dall'HUB!</b>\nUsa /sdrogocomm per accedere.", parse_mode="HTML")

def register(app):
    app.add_handler(CommandHandler("sdrogocomm", show_hub))
    app.add_handler(CommandHandler("topricconi", show_leaderboard))
    for cmd in ["roulette", "blackjack", "slot", "highlow", "wordle", "quiz", "shop", "heist"]:
        app.add_handler(CommandHandler(cmd, block_direct_command))
    app.add_handler(CallbackQueryHandler(hub_callback, pattern="^hub_"))
    app.add_handler(CallbackQueryHandler(claim_daily_callback, pattern="^claim_daily_"))
