"""Quiz Show single player e Quiz Multiplayer."""
import asyncio
import random
from datetime import datetime
from functools import partial

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler, ContextTypes

from database_quiz import CATEGORIE_QUIZ
from state import QUIZ_GAMES
from storage import get_user_coins, add_user_coins
from utils import verify_user_lock

# --- GAME: QUIZ SHOW SINGLE PLAYER CON CHIUSURA A TEMPO ---
async def start_quiz_generic(update: Update, context: ContextTypes.DEFAULT_TYPE, db_source, title_name: str):
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split("_")
    owner_id = int(parts[2]) if len(parts) > 2 else query.from_user.id

    if not await verify_user_lock(query, owner_id): return
    chat_id = query.message.chat_id
    user_id = query.from_user.id

    if get_user_coins(chat_id, user_id) < 5:
        await query.answer("❌ Servono 5 $SDG per avviare il Quiz!", show_alert=True)
        return

    add_user_coins(chat_id, user_id, -5)
    item = random.choice(db_source)
    
    msg = await query.edit_message_text(
        f"🧠 <b>QUIZ {title_name}</b> (Costo: 5 $SDG)\n\n"
        f"👤 Giocatore: <b>{query.from_user.first_name}</b>\n"
        "Indovina la risposta scrivendola in chat!\n\n"
        f"<b>1° Indizio:</b> {item['indizi'][0]}\n\n"
        f"⏱️ <i>Hai 75 secondi per indovinare!</i>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💡 Chiedi altro indizio (-$SDG)", callback_data=f"quiz_hint_{owner_id}")],
            [InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{owner_id}")]
        ]),
        parse_mode="HTML"
    )

    quiz_key = f"{chat_id}_{user_id}"
    QUIZ_GAMES[quiz_key] = {
        "player_id": user_id, "type": title_name,
        "target": item["target"], "indizi": item["indizi"], "step": 1,
        "created_at": datetime.now(), "msg_id": msg.message_id
    }

    asyncio.create_task(run_quiz_single_timeout(context.bot, chat_id, user_id, msg.message_id, item["target"]))

async def run_quiz_single_timeout(bot, chat_id: int, user_id: int, msg_id: int, target: str):
    await asyncio.sleep(75)
    quiz_key = f"{chat_id}_{user_id}"
    if quiz_key in QUIZ_GAMES and QUIZ_GAMES[quiz_key].get("msg_id") == msg_id:
        del QUIZ_GAMES[quiz_key]
        end_keyboard = [[InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{user_id}")]]
        try:
            await bot.edit_message_text(
                chat_id=chat_id, message_id=msg_id,
                text=f"⏰ <b>TEMPO SCADUTO!</b>\n\nNon hai indovinato in tempo. La risposta corretta era: <b>{target}</b>!",
                reply_markup=InlineKeyboardMarkup(end_keyboard),
                parse_mode="HTML"
            )
        except Exception: pass

async def quiz_more_hint(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split("_")
    owner_id = int(parts[2]) if len(parts) > 2 else query.from_user.id

    if not await verify_user_lock(query, owner_id): return
    chat_id = str(query.message.chat_id)
    game_key = f"{chat_id}_{owner_id}"

    if game_key not in QUIZ_GAMES:
        await query.answer("Nessun quiz attivo.", show_alert=True)
        return

    q = QUIZ_GAMES[game_key]
    if q["step"] < len(q["indizi"]):
        q["step"] += 1
        hints_text = "\n".join([f"• <b>Indizio {i+1}:</b> {q['indizi'][i]}" for i in range(q["step"])])
        
        keyboard = []
        if q["step"] < len(q["indizi"]):
            keyboard.append([InlineKeyboardButton("💡 Chiedi altro indizio (-$SDG)", callback_data=f"quiz_hint_{owner_id}")])
        keyboard.append([InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{owner_id}")])

        await query.edit_message_text(
            f"🧠 <b>QUIZ {q['type']}</b>\n\nScrivi la risposta in chat!\n\n{hints_text}",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )


# --- GAME: QUIZ MULTIPLAYER PER CATEGORIA ---
async def start_quiz_multiplayer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split("_")
    cat_choice = parts[2] if len(parts) > 2 else "ALL"
    owner_id = int(parts[3]) if len(parts) > 3 else query.from_user.id

    if not await verify_user_lock(query, owner_id): return
    chat_id = str(query.message.chat_id)

    if cat_choice == "ALL":
        cat_name, selected_db = random.choice(list(CATEGORIE_QUIZ.values()))
    else:
        cat_name, selected_db = CATEGORIE_QUIZ.get(cat_choice, CATEGORIE_QUIZ["CALCIO"])

    item = random.choice(selected_db)

    msg = await query.edit_message_text(
        f"🌐 <b>QUIZ MULTIPLAYER APERTO A TUTTI</b>\n"
        f"🏷️ <b>CATEGORIA: {cat_name}</b>\n\n"
        f"Il primo che risponde in chat vince +💳 15 $SDG!\n\n"
        f"<b>1° Indizio:</b> {item['indizi'][0]}\n\n"
        f"⏱️ <i>Avete 75 secondi per indovinare!</i>",
        parse_mode="HTML"
    )

    QUIZ_GAMES[chat_id] = {
        "multi": True, "target": item["target"],
        "indizi": item["indizi"], "step": 1,
        "created_at": datetime.now(),
        "msg_id": msg.message_id,
        "cat_name": cat_name
    }

    asyncio.create_task(run_quiz_multi_timer(context.bot, chat_id, msg.message_id, item["indizi"], cat_name, item["target"], owner_id))

async def run_quiz_multi_timer(bot, chat_id: str, msg_id: int, indizi: list, cat_name: str, target: str, owner_id: int):
    await asyncio.sleep(25)
    if chat_id in QUIZ_GAMES and QUIZ_GAMES[chat_id].get("multi") and QUIZ_GAMES[chat_id].get("msg_id") == msg_id:
        QUIZ_GAMES[chat_id]["step"] = 2
        hints_text = f"• <b>Indizio 1:</b> {indizi[0]}\n• <b>Indizio 2:</b> {indizi[1]}"
        try:
            await bot.edit_message_text(
                chat_id=int(chat_id), message_id=msg_id,
                text=f"🌐 <b>QUIZ MULTIPLAYER APERTO A TUTTI</b>\n🏷️ <b>CATEGORIA: {cat_name}</b>\n\nIl primo che risponde in chat vince +💳 15 $SDG!\n\n{hints_text}",
                parse_mode="HTML"
            )
        except Exception: pass

    await asyncio.sleep(25)
    if chat_id in QUIZ_GAMES and QUIZ_GAMES[chat_id].get("multi") and QUIZ_GAMES[chat_id].get("msg_id") == msg_id:
        QUIZ_GAMES[chat_id]["step"] = 3
        hints_text = f"• <b>Indizio 1:</b> {indizi[0]}\n• <b>Indizio 2:</b> {indizi[1]}\n• <b>Indizio 3:</b> {indizi[2]}"
        try:
            await bot.edit_message_text(
                chat_id=int(chat_id), message_id=msg_id,
                text=f"🌐 <b>QUIZ MULTIPLAYER APERTO A TUTTI</b>\n🏷️ <b>CATEGORIA: {cat_name}</b>\n\nIl primo che risponde in chat vince +💳 15 $SDG!\n\n{hints_text}",
                parse_mode="HTML"
            )
        except Exception: pass

    await asyncio.sleep(25)
    if chat_id in QUIZ_GAMES and QUIZ_GAMES[chat_id].get("multi") and QUIZ_GAMES[chat_id].get("msg_id") == msg_id:
        del QUIZ_GAMES[chat_id]
        end_keyboard = [
            [InlineKeyboardButton("🌐 Altro Quiz Multi", callback_data=f"hub_qmulti_{owner_id}")],
            [InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{owner_id}")]
        ]
        try:
            await bot.edit_message_text(
                chat_id=int(chat_id), message_id=msg_id,
                text=f"⏰ <b>TEMPO SCADUTO!</b>\n\nNessuno ha indovinato in tempo. La risposta corretta era: <b>{target}</b>!",
                reply_markup=InlineKeyboardMarkup(end_keyboard),
                parse_mode="HTML"
            )
        except Exception: pass

# --- TIMEOUT QUIZ GENERALI ---
async def quiz_timeout_check(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now()
    to_delete = [k for k, q in QUIZ_GAMES.items() if q.get("created_at") and (now - q["created_at"]).total_seconds() > 180]
    for k in to_delete: del QUIZ_GAMES[k]


# --- REGISTRAZIONE HANDLER ---
def register(app):
    for key, (label, db) in CATEGORIE_QUIZ.items():
        title = label.split(" ", 1)[1]            # toglie l'emoji dall'etichetta
        app.add_handler(CallbackQueryHandler(
            partial(start_quiz_generic, db_source=db, title_name=title),
            pattern=f"^start_q{key.lower()}_"
        ))
    app.add_handler(CallbackQueryHandler(start_quiz_multiplayer, pattern="^start_qmulti_"))
    app.add_handler(CallbackQueryHandler(quiz_more_hint, pattern="^quiz_hint_"))
    if app.job_queue:
        app.job_queue.run_repeating(quiz_timeout_check, interval=60)
