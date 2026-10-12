"""Quiz Show single player e Quiz Multiplayer."""
import asyncio
import html
import random
from datetime import datetime
from functools import partial

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler, ContextTypes

from database_quiz import CATEGORIE_QUIZ
from state import QUIZ_GAMES
from storage import get_user_coins, add_user_coins
from utils import verify_user_lock
from state import game_enabled, MAINT_GAME_TEXT
  if not game_enabled("quizmulti"):
      await query.answer(MAINT_GAME_TEXT, show_alert=True)
      return


def quiz_reward(step: int) -> int:
    """Premio del quiz single player in base agli indizi usati."""
    return 20 if step == 1 else (10 if step == 2 else 6)


def _hints_text(indizi: list, n: int) -> str:
    return "\n".join(f"• <b>Indizio {i+1}:</b> {indizi[i]}" for i in range(min(n, len(indizi))))


# --- GAME: QUIZ SHOW SINGLE PLAYER CON CHIUSURA A TEMPO ---
async def start_quiz_generic(update: Update, context: ContextTypes.DEFAULT_TYPE, db_source, title_name: str):
    query = update.callback_query

    parts = query.data.split("_")
    owner_id = int(parts[2]) if len(parts) > 2 else query.from_user.id

    if not await verify_user_lock(query, owner_id): return
    chat_id = query.message.chat_id
    user_id = query.from_user.id
    quiz_key = f"{chat_id}_{user_id}"

    if quiz_key in QUIZ_GAMES:
        await query.answer("⏳ Hai già un quiz in corso!", show_alert=True)
        return
    if get_user_coins(chat_id, user_id) < 5:
        await query.answer("❌ Servono 5 $SDG per avviare il Quiz!", show_alert=True)
        return
    await query.answer()

    add_user_coins(chat_id, user_id, -5)
    item = random.choice(db_source)

    # La partita viene registrata PRIMA di modificare il messaggio: un doppio click trova già il quiz attivo
    QUIZ_GAMES[quiz_key] = {
        "player_id": user_id, "type": title_name,
        "target": item["target"], "indizi": item["indizi"], "step": 1,
        "created_at": datetime.now(), "msg_id": None
    }

    try:
        msg = await query.edit_message_text(
            f"🧠 <b>QUIZ {title_name}</b> (Costo: 5 $SDG)\n\n"
            f"👤 Giocatore: <b>{html.escape(query.from_user.first_name or 'Giocatore')}</b>\n"
            "Indovina la risposta scrivendola in chat!\n\n"
            f"<b>1° Indizio:</b> {item['indizi'][0]}\n\n"
            f"🎁 Premio attuale: <b>{quiz_reward(1)} $SDG</b> (scende a {quiz_reward(2)} e {quiz_reward(3)} con gli indizi)\n"
            f"⏱️ <i>Hai 75 secondi per indovinare!</i>",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💡 Altro indizio (il premio scende)", callback_data=f"quiz_hint_{owner_id}")],
                [InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{owner_id}")]
            ]),
            parse_mode="HTML"
        )
    except Exception:
        # il quiz non è partito davvero: annulla e rimborsa
        QUIZ_GAMES.pop(quiz_key, None)
        add_user_coins(chat_id, user_id, 5)
        return

    QUIZ_GAMES[quiz_key]["msg_id"] = msg.message_id
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

    parts = query.data.split("_")
    owner_id = int(parts[2]) if len(parts) > 2 else query.from_user.id

    if not await verify_user_lock(query, owner_id): return
    chat_id = str(query.message.chat_id)
    game_key = f"{chat_id}_{owner_id}"

    if game_key not in QUIZ_GAMES:
        await query.answer("Nessun quiz attivo.", show_alert=True)
        return

    q = QUIZ_GAMES[game_key]
    if q["step"] >= len(q["indizi"]):
        await query.answer("Non ci sono altri indizi.", show_alert=True)
        return
    await query.answer()

    q["step"] += 1
    hints_text = _hints_text(q["indizi"], q["step"])

    keyboard = []
    if q["step"] < len(q["indizi"]):
        keyboard.append([InlineKeyboardButton("💡 Altro indizio (il premio scende)", callback_data=f"quiz_hint_{owner_id}")])
    keyboard.append([InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{owner_id}")])

    try:
        await query.edit_message_text(
            f"🧠 <b>QUIZ {q['type']}</b>\n\nScrivi la risposta in chat!\n\n{hints_text}\n\n"
            f"🎁 Premio attuale: <b>{quiz_reward(q['step'])} $SDG</b>",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
    except Exception:
        pass


# --- GAME: QUIZ MULTIPLAYER PER CATEGORIA ---
async def start_quiz_multiplayer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    parts = query.data.split("_")
    cat_choice = parts[2] if len(parts) > 2 else "ALL"
    owner_id = int(parts[3]) if len(parts) > 3 else query.from_user.id

    if not await verify_user_lock(query, owner_id): return
    if not game_enabled("quizmulti"):
      await query.answer(MAINT_GAME_TEXT, show_alert=True)
      return
    chat_id = str(query.message.chat_id)

    if chat_id in QUIZ_GAMES:
        await query.answer("⏳ C'è già un quiz multiplayer in corso in questa chat!", show_alert=True)
        return
    await query.answer()

    if cat_choice == "ALL":
        cat_name, selected_db = random.choice(list(CATEGORIE_QUIZ.values()))
    else:
        cat_name, selected_db = CATEGORIE_QUIZ.get(cat_choice, CATEGORIE_QUIZ["CALCIO"])

    item = random.choice(selected_db)

    # Registrato prima della modifica del messaggio: niente quiz sovrapposti con doppio click
    QUIZ_GAMES[chat_id] = {
        "multi": True, "target": item["target"],
        "indizi": item["indizi"], "step": 1,
        "created_at": datetime.now(),
        "msg_id": None,
        "cat_name": cat_name
    }

    try:
        msg = await query.edit_message_text(
            f"🌐 <b>QUIZ MULTIPLAYER APERTO A TUTTI</b>\n"
            f"🏷️ <b>CATEGORIA: {cat_name}</b>\n\n"
            f"Il primo che risponde in chat vince +💳 15 $SDG!\n\n"
            f"<b>1° Indizio:</b> {item['indizi'][0]}\n\n"
            f"⏱️ <i>Avete 75 secondi per indovinare!</i>",
            parse_mode="HTML"
        )
    except Exception:
        QUIZ_GAMES.pop(chat_id, None)
        return

    QUIZ_GAMES[chat_id]["msg_id"] = msg.message_id
    asyncio.create_task(run_quiz_multi_timer(context.bot, chat_id, msg.message_id, item["indizi"], cat_name, item["target"], owner_id))

async def run_quiz_multi_timer(bot, chat_id: str, msg_id: int, indizi: list, cat_name: str, target: str, owner_id: int):
    def still_active() -> bool:
        return chat_id in QUIZ_GAMES and QUIZ_GAMES[chat_id].get("multi") and QUIZ_GAMES[chat_id].get("msg_id") == msg_id

    await asyncio.sleep(25)
    if still_active():
        QUIZ_GAMES[chat_id]["step"] = 2
        try:
            await bot.edit_message_text(
                chat_id=int(chat_id), message_id=msg_id,
                text=f"🌐 <b>QUIZ MULTIPLAYER APERTO A TUTTI</b>\n🏷️ <b>CATEGORIA: {cat_name}</b>\n\nIl primo che risponde in chat vince +💳 15 $SDG!\n\n{_hints_text(indizi, 2)}",
                parse_mode="HTML"
            )
        except Exception: pass

    await asyncio.sleep(25)
    if still_active():
        QUIZ_GAMES[chat_id]["step"] = 3
        try:
            await bot.edit_message_text(
                chat_id=int(chat_id), message_id=msg_id,
                text=f"🌐 <b>QUIZ MULTIPLAYER APERTO A TUTTI</b>\n🏷️ <b>CATEGORIA: {cat_name}</b>\n\nIl primo che risponde in chat vince +💳 15 $SDG!\n\n{_hints_text(indizi, 3)}",
                parse_mode="HTML"
            )
        except Exception: pass

    await asyncio.sleep(25)
    if still_active():
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
