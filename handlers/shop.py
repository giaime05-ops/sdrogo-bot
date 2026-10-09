from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler, ContextTypes

from state import USER_DATA, USER_INVENTORIES, ACTIVE_TITLES, ACTIVE_PERSECUTE, HEIST_GAMES
from storage import get_user_coins, add_user_coins
from utils import verify_user_lock

async def shop_buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
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

    if item_type == "title":
        if coins < 100:
            await query.answer("❌ Servono 100 $SDG per comprare il Titolo Umiliante!", show_alert=True)
            return
        add_user_coins(chat_id, user_id, -100)
        USER_INVENTORIES[inv_key]["titles"] += 1
        await query.edit_message_text(
            "✅ <b>TITOLO UMILIANTE ACQUISTATO!</b>\n\n"
            "Per assegnarlo per 24 ORE a una vittima, scrivi in chat:\n"
            "👉 <code>titolo @username</code> (oppure rispondi al suo messaggio con <code>titolo</code>)",
            parse_mode="HTML"
        )

    elif item_type == "persecute":
        if coins < 120:
            await query.answer("❌ Servono 120 $SDG per comprare il Tag Persecutore!", show_alert=True)
            return
        add_user_coins(chat_id, user_id, -120)
        USER_INVENTORIES[inv_key]["persecutes"] += 1
        await query.edit_message_text(
            "✅ <b>TAG PERSECUTORE ACQUISTATO!</b>\n\n"
            "Per perseguitare una vittima per 15 messaggi, scrivi in chat:\n"
            "👉 <code>perseguita @username</code>",
            parse_mode="HTML"
        )

    elif item_type == "heist":
        if coins < 350:
            await query.answer("❌ Servono 350 $SDG per tentare la Rapina Heist!", show_alert=True)
            return
        add_user_coins(chat_id, user_id, -350)
        
        HEIST_GAMES[user_id] = {"level": 1, "chat_id": chat_id}
        
        try:
            keyboard = [[InlineKeyboardButton("🔓 Disattiva Allarme (Livello 1)", callback_data=f"heist_lvl1_{user_id}")]]
            await context.bot.send_message(
                chat_id=user_id,
                text="🏢 <b>SDROGO HEIST - LA RAPINA AL CAVEAU</b> 🕵️‍♂️\n\nBenvenuto al Livello 1! Devi disattivare l'allarme per entrare.",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )
            await query.edit_message_text("🏢 <b>LA RAPINA È INIZIata!</b> Controlla la tua chat PRIVATA con SdrogoBot per giocare!", parse_mode="HTML")
        except Exception:
            add_user_coins(chat_id, user_id, 350)
            await query.edit_message_text("❌ Devi prima avviare il bot in chat PRIVATA per giocare a Sdrogo Heist!", parse_mode="HTML")

async def apply_title_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    inv_key = f"{chat_id}_{user.id}"
    text = (update.message.text or "").strip()

    if inv_key not in USER_INVENTORIES or USER_INVENTORIES[inv_key].get("titles", 0) <= 0:
        await update.message.reply_text("❌ Non possiedi alcun Titolo Umiliante nel tuo inventario dello /shop!", parse_mode="HTML")
        return

    target_username = None
    for part in text.split():
        if part.startswith("@"):
            target_username = part.replace("@", "").lower()
            break

    if not target_username and update.message.reply_to_message and update.message.reply_to_message.from_user:
        target_username = update.message.reply_to_message.from_user.username.lower() if update.message.reply_to_message.from_user.username else None

    if not target_username:
        await update.message.reply_text("❌ Uso: <code>titolo @username</code> oppure rispondi al suo messaggio con <code>titolo</code>!", parse_mode="HTML")
        return

    target_id = None
    prefix = f"{chat_id}_"
    for k in USER_DATA.keys():
        if k.startswith(prefix):
            uid = k.split("_")[1]
            try:
                m = await context.bot.get_chat_member(chat_id, int(uid))
                if m.user.username and m.user.username.lower() == target_username:
                    target_id = int(uid)
                    break
            except Exception: pass

    if not target_id:
        await update.message.reply_text("❌ Utente non trovato nel registro della chat!", parse_mode="HTML")
        return

    USER_INVENTORIES[inv_key]["titles"] -= 1
    expire_time = datetime.now() + timedelta(hours=24)
    ACTIVE_TITLES[f"{chat_id}_{target_id}"] = {"title": "🏳️‍🌈GAY🏳️‍🌈", "expire": expire_time}
    await update.message.reply_text(f"🔥 <b>TITOLO ASSEGNATO!</b> Per 24 ORE @{target_username} sarà chiamato '🏳️‍🌈GAY🏳️‍🌈' dal bot!", parse_mode="HTML")

async def apply_persecute_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    inv_key = f"{chat_id}_{user.id}"
    text = (update.message.text or "").strip()

    if inv_key not in USER_INVENTORIES or USER_INVENTORIES[inv_key].get("persecutes", 0) <= 0:
        await update.message.reply_text("❌ Non possiedi alcun Tag Persecutore nel tuo inventario dello /shop!", parse_mode="HTML")
        return

    target_username = None
    for part in text.split():
        if part.startswith("@"):
            target_username = part.replace("@", "").lower()
            break

    if not target_username:
        await update.message.reply_text("❌ Uso corretto: <code>perseguita @username</code>", parse_mode="HTML")
        return

    USER_INVENTORIES[inv_key]["persecutes"] -= 1
    ACTIVE_PERSECUTE[f"{chat_id}_{target_username}"] = {"count": 15, "phrase": "frocio hah"}
    await update.message.reply_text(f"😈 <b>PERSECUZIONE ATTIVATA!</b> I prossimi 15 messaggi di @{target_username} riceveranno risposta 'frocio hah' dal bot!", parse_mode="HTML")

def register(app):
    app.add_handler(CallbackQueryHandler(shop_buy_callback, pattern="^buy_"))
