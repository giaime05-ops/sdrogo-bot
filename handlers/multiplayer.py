"""Giochi 1v1: Roulette Russa, High/Low, Ghigliottina, Duello Quiz."""
import asyncio
import random

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler, ContextTypes

from config import FRASE_PENITENZA
from database_quiz import GHIGLIOTTINA_DB, CATEGORIE_QUIZ
from state import (
    ACTIVE_DUELS, HIGHLOW_DUELS, GHIGLIOTTINA_DUELS, QUIZ_DUELS_1V1,
    PENITENZE_ATTIVE,
)
from storage import add_user_coins
from utils import verify_user_lock

# --- GAME: ROULETTE RUSSA 1v1 ---
async def start_roulette_prep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split("_")
    owner_id = int(parts[2]) if len(parts) > 2 else query.from_user.id

    if not await verify_user_lock(query, owner_id): return

    await query.edit_message_text(
        "🎯 <b>ROULETTE RUSSA 1v1</b>\n\n"
        "Scrivi in chat il nome della tua vittima:\n\n"
        "👉 <code>sfido @username</code>",
        parse_mode="HTML"
    )

async def gestione_bottoni_roulette(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    chat_id = query.message.chat_id
    user = query.from_user

    if chat_id not in ACTIVE_DUELS:
        await query.answer("⚠️ Sfida non attiva.", show_alert=True)
        return

    duel = ACTIVE_DUELS[chat_id]

    if query.data == "roulette_accetta":
        if user.username and user.username.lower() != duel["target_username"]:
            await query.answer("❌ Solo lo sfidato può accettare!", show_alert=True)
            return

        duel["target_id"] = user.id
        duel["target_name"] = user.first_name
        duel["turno_id"] = random.choice([duel["sfidante_id"], user.id])

        keyboard = [[InlineKeyboardButton("🔫 SPARA!", callback_data="roulette_spara")]]
        await query.edit_message_text(
            f"✅ <b>Sfida Accettata!</b> Tamburo caricato (1 proiettile).\n\n"
            f"🎲 Comincia: <b>{user.first_name}</b>!",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )

    elif query.data == "roulette_rifiuta":
        await query.edit_message_text("🐔 Sfida rifiutata!")
        del ACTIVE_DUELS[chat_id]

    elif query.data == "roulette_spara":
        if user.id != duel["turno_id"]:
            await query.answer("✋ Non è il tuo turno!", show_alert=True)
            return

        current_idx = duel["current_chamber"]
        is_bullet = duel["chambers"][current_idx]
        duel["current_chamber"] += 1

        if is_bullet:
            PENITENZE_ATTIVE[user.id] = 1
            await query.edit_message_text(
                f"💥 <b>BAM!</b> 💀 <b>{user.first_name} è morto!</b>\n\n"
                f"⚠️ Per parlare devi scrivere esattamente:\n👉 <code>{FRASE_PENITENZA}</code>",
                parse_mode="HTML"
            )
            del ACTIVE_DUELS[chat_id]
        else:
            prossimo_id = duel["target_id"] if user.id == duel["sfidante_id"] else duel["sfidante_id"]
            duel["turno_id"] = prossimo_id
            keyboard = [[InlineKeyboardButton("🔫 SPARA!", callback_data="roulette_spara")]]
            await query.edit_message_text(
                f"*Click!* 😅 Camera vuota! Si salva <b>{user.first_name}</b>!\n\n👉 Tocca all'altro sfidante!",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )


# --- GAME: HIGHLOW 1v1 ---
async def start_highlow_prep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split("_")
    owner_id = int(parts[2]) if len(parts) > 2 else query.from_user.id

    if not await verify_user_lock(query, owner_id): return

    await query.edit_message_text(
        "🎲 <b>HIGH / LOW 1v1 (DADO DELLA MORTE)</b>\n\n"
        "Scrivi in chat il nome della tua vittima per sfidarla sul dado:\n\n"
        "👉 <code>sfido highlow @username</code>",
        parse_mode="HTML"
    )

async def handle_highlow_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    chat_id = query.message.chat_id
    user = query.from_user

    if chat_id not in HIGHLOW_DUELS:
        await query.answer("⚠️ Sfida High/Low non attiva.", show_alert=True)
        return

    game = HIGHLOW_DUELS[chat_id]

    if query.data == "hl_accetta":
        if user.username and user.username.lower() != game["target_username"]:
            await query.answer("❌ Solo lo sfidato può accettare!", show_alert=True)
            return

        game["target_id"] = user.id
        game["target_name"] = user.first_name
        game["turno_id"] = random.choice([game["sfidante_id"], user.id])
        game["val"] = random.randint(2, 11)

        turno_nome = game["sfidante_name"] if game["turno_id"] == game["sfidante_id"] else game["target_name"]

        keyboard = [[
            InlineKeyboardButton("📈 PIÙ ALTO", callback_data="hl_guess_high"),
            InlineKeyboardButton("📉 PIÙ BASSO", callback_data="hl_guess_low")
        ]]

        await query.edit_message_text(
            f"🎲 <b>DADO DELLA MORTE 1v1</b>\n\n"
            f"🎯 Numero estratto: <b>{game['val']}</b> (da 1 a 12)\n\n"
            f"👉 Tocca a <b>{turno_nome}</b>: Il prossimo numero sarà Più Alto o Più Basso?",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )

    elif query.data in ["hl_guess_high", "hl_guess_low"]:
        if user.id != game["turno_id"]:
            await query.answer("✋ Non è il tuo turno!", show_alert=True)
            return

        old_val = game["val"]
        new_val = random.randint(1, 12)
        while new_val == old_val: new_val = random.randint(1, 12)

        choice = query.data
        won = (choice == "hl_guess_high" and new_val > old_val) or (choice == "hl_guess_low" and new_val < old_val)

        if won:
            game["val"] = new_val
            prossimo_id = game["target_id"] if user.id == game["sfidante_id"] else game["sfidante_id"]
            prossimo_nome = game["target_name"] if user.id == game["sfidante_id"] else game["sfidante_name"]
            game["turno_id"] = prossimo_id

            keyboard = [[
                InlineKeyboardButton("📈 PIÙ ALTO", callback_data="hl_guess_high"),
                InlineKeyboardButton("📉 PIÙ BASSO", callback_data="hl_guess_low")
            ]]

            await query.edit_message_text(
                f"✅ <b>GIUSTO! Era {new_val}!</b>\n"
                f"😅 <b>{user.first_name}</b> si salva!\n\n"
                f"🎯 Nuovo numero: <b>{new_val}</b>\n"
                f"👉 Tocca a <b>{prossimo_nome}</b>: Più Alto o Più Basso?",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )
        else:
            PENITENZE_ATTIVE[user.id] = 1
            await query.edit_message_text(
                f"💥 <b>ERRATO! Era {new_val}!</b>\n"
                f"💀 <b>{user.first_name} HA SBAGLIATO E PERDE IL DUELLO!</b>\n\n"
                f"⚠️ Per parlare devi scrivere esattamente:\n👉 <code>{FRASE_PENITENZA}</code>",
                parse_mode="HTML"
            )
            del HIGHLOW_DUELS[chat_id]

    elif query.data == "hl_rifiuta":
        await query.edit_message_text("🐔 Sfida High/Low rifiutata!")
        del HIGHLOW_DUELS[chat_id]


# --- GAME: GHIGLIOTTINA 1v1 ---
async def start_ghigliottina_prep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split("_")
    owner_id = int(parts[3]) if len(parts) > 3 else query.from_user.id

    if not await verify_user_lock(query, owner_id): return

    await query.edit_message_text(
        "🪓 <b>GHIGLIOTTINA EXPRESS 1v1</b>\n\n"
        "Scrivi in chat il nome della tua vittima per sfidarla alla Ghigliottina:\n\n"
        "👉 <code>sfidoghigliottina @username</code>",
        parse_mode="HTML"
    )

async def handle_ghigliottina_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    chat_id = query.message.chat_id
    user = query.from_user

    if chat_id not in GHIGLIOTTINA_DUELS:
        await query.answer("⚠️ Sfida Ghigliottina non attiva.", show_alert=True)
        return

    duel = GHIGLIOTTINA_DUELS[chat_id]

    if query.data == "ghig_accetta":
        if user.username and user.username.lower() != duel["target_username"]:
            await query.answer("❌ Solo lo sfidato può accettare!", show_alert=True)
            return

        item = random.choice(GHIGLIOTTINA_DB)
        duel["target_id"] = user.id
        duel["target_name"] = user.first_name
        duel["word"] = item["target"]
        duel["indizi"] = item["indizi"]
        duel["active"] = True

        indizi_formatted = " • ".join([f"<b>{word}</b>" for word in item["indizi"]])

        msg = await query.edit_message_text(
            f"🪓 <b>GHIGLIOTTINA EXPRESS 1v1 INIZIATA!</b>\n\n"
            f"⚔️ <b>{duel['sfidante_name']}</b> vs <b>{duel['target_name']}</b>\n\n"
            f"📌 <b>Le 5 Parole Indizio:</b>\n{indizi_formatted}\n\n"
            f"⏱️ <i>Avete 75 secondi! Il primo dei due che scrive la parola legame in chat vince <b>+💳 40 $SDG</b>!</i>",
            parse_mode="HTML"
        )
        asyncio.create_task(run_ghigliottina_timeout(context.bot, chat_id, msg.message_id))

    elif query.data == "ghig_rifiuta":
        await query.edit_message_text("🐔 Sfida Ghigliottina rifiutata!")
        del GHIGLIOTTINA_DUELS[chat_id]

async def run_ghigliottina_timeout(bot, chat_id: int, msg_id: int):
    await asyncio.sleep(75)
    if chat_id in GHIGLIOTTINA_DUELS and GHIGLIOTTINA_DUELS[chat_id].get("active"):
        target_word = GHIGLIOTTINA_DUELS[chat_id]["word"]
        del GHIGLIOTTINA_DUELS[chat_id]
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=f"⏰ <b>TEMPO SCADUTO ALLA GHIGLIOTTINA!</b>\n\nNessuno dei due sfidanti ha indovinato. La parola legame era: <b>{target_word}</b>!",
                parse_mode="HTML"
            )
        except Exception: pass


# --- GAME: QUIZ 1v1 DUEL ---
async def start_quiz1v1_prep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split("_")
    owner_id = int(parts[3]) if len(parts) > 3 else query.from_user.id

    if not await verify_user_lock(query, owner_id): return

    await query.edit_message_text(
        "⚔️ <b>DUELLO QUIZ 1v1 (A 5 ROUND)</b>\n\n"
        "Scrivi in chat il nome della tua vittima per sfidarla al quiz:\n\n"
        "👉 <code>sfidoquiz @username</code>",
        parse_mode="HTML"
    )

async def handle_quiz1v1_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    chat_id = query.message.chat_id
    user = query.from_user

    if chat_id not in QUIZ_DUELS_1V1:
        await query.answer("⚠️ Duello Quiz non attivo.", show_alert=True)
        return

    duel = QUIZ_DUELS_1V1[chat_id]

    if query.data.startswith("q1v1_cat_"):
        if user.id != duel["sfidante_id"]:
            await query.answer("❌ Solo lo sfidante può scegliere la categoria!", show_alert=True)
            return

        cat_choice = query.data.split("_")[2]
        duel["category"] = cat_choice

        keyboard = [
            [InlineKeyboardButton("🎯 Accetta Duello Quiz", callback_data="q1v1_accetta")],
            [InlineKeyboardButton("🐔 Rifiuta", callback_data="q1v1_rifiuta")]
        ]

        await query.edit_message_text(
            f"⚔️ <b>DUELLO QUIZ 1v1 (A 5 ROUND)</b>\n\n"
            f"<b>{duel['sfidante_name']}</b> ha sfidato <b>@{duel['target_username']}</b> in Categoria <b>{cat_choice}</b>!\n\n"
            f"@{duel['target_username']}, accetti la sfida?",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )

    elif query.data == "q1v1_accetta":
        if user.username and user.username.lower() != duel["target_username"]:
            await query.answer("❌ Solo lo sfidato può accettare!", show_alert=True)
            return

        duel["target_id"] = user.id
        duel["target_name"] = user.first_name
        duel["round"] = 1
        duel["p1_score"] = 0
        duel["p2_score"] = 0
        duel["active"] = True

        await launch_quiz1v1_round(context.bot, chat_id)

    elif query.data == "q1v1_rifiuta":
        await query.edit_message_text("🐔 Duello Quiz rifiutato!")
        del QUIZ_DUELS_1V1[chat_id]

async def launch_quiz1v1_round(bot, chat_id: int):
    if chat_id not in QUIZ_DUELS_1V1: return
    duel = QUIZ_DUELS_1V1[chat_id]

    cat = duel["category"]
    selected_db = CATEGORIE_QUIZ.get(cat, CATEGORIE_QUIZ["CALCIO"])[1]
    item = random.choice(selected_db)

    duel["current_target"] = item["target"]
    duel["current_indizi"] = item["indizi"]

    msg = await bot.send_message(
        chat_id=chat_id,
        text=f"⚔️ <b>DUELLO QUIZ 1v1 — ROUND {duel['round']}/5</b>\n"
             f"👤 <b>{duel['sfidante_name']}</b> ({duel['p1_score']}) vs <b>{duel['target_name']}</b> ({duel['p2_score']})\n\n"
             f"🏷️ <b>CATEGORIA: {cat}</b>\n\n"
             f"<b>1° Indizio:</b> {item['indizi'][0]}\n\n"
             f"⏱️ <i>Avete 75 secondi per rispondere prima del prossimo round!</i>",
        parse_mode="HTML"
    )
    duel["msg_id"] = msg.message_id
    asyncio.create_task(run_quiz1v1_round_timer(bot, chat_id, duel['round'], msg.message_id, item["indizi"], item["target"]))

async def run_quiz1v1_round_timer(bot, chat_id: int, round_num: int, msg_id: int, indizi: list, target: str):
    await asyncio.sleep(25)
    if chat_id in QUIZ_DUELS_1V1 and QUIZ_DUELS_1V1[chat_id].get("round") == round_num and QUIZ_DUELS_1V1[chat_id].get("msg_id") == msg_id:
        duel = QUIZ_DUELS_1V1[chat_id]
        hints_text = f"• <b>Indizio 1:</b> {indizi[0]}\n• <b>Indizio 2:</b> {indizi[1]}"
        try:
            await bot.edit_message_text(
                chat_id=chat_id, message_id=msg_id,
                text=f"⚔️ <b>DUELLO QUIZ 1v1 — ROUND {round_num}/5</b>\n"
                     f"👤 <b>{duel['sfidante_name']}</b> ({duel['p1_score']}) vs <b>{duel['target_name']}</b> ({duel['p2_score']})\n\n"
                     f"{hints_text}",
                parse_mode="HTML"
            )
        except Exception: pass

    await asyncio.sleep(25)
    if chat_id in QUIZ_DUELS_1V1 and QUIZ_DUELS_1V1[chat_id].get("round") == round_num and QUIZ_DUELS_1V1[chat_id].get("msg_id") == msg_id:
        duel = QUIZ_DUELS_1V1[chat_id]
        hints_text = f"• <b>Indizio 1:</b> {indizi[0]}\n• <b>Indizio 2:</b> {indizi[1]}\n• <b>Indizio 3:</b> {indizi[2]}"
        try:
            await bot.edit_message_text(
                chat_id=chat_id, message_id=msg_id,
                text=f"⚔️ <b>DUELLO QUIZ 1v1 — ROUND {round_num}/5</b>\n"
                     f"👤 <b>{duel['sfidante_name']}</b> ({duel['p1_score']}) vs <b>{duel['target_name']}</b> ({duel['p2_score']})\n\n"
                     f"{hints_text}",
                parse_mode="HTML"
            )
        except Exception: pass

    await asyncio.sleep(25)
    if chat_id in QUIZ_DUELS_1V1 and QUIZ_DUELS_1V1[chat_id].get("round") == round_num and QUIZ_DUELS_1V1[chat_id].get("msg_id") == msg_id:
        duel = QUIZ_DUELS_1V1[chat_id]
        await bot.send_message(
            chat_id=chat_id,
            text=f"⏰ <b>ROUND {round_num} SCADUTO!</b> Nessuno dei due ha indovinato. La risposta era: <b>{target}</b>.",
            parse_mode="HTML"
        )
        if duel["round"] < 5 and duel["p1_score"] < 3 and duel["p2_score"] < 3:
            duel["round"] += 1
            await launch_quiz1v1_round(bot, chat_id)
        else:
            await conclude_quiz1v1_duel(bot, chat_id)

async def conclude_quiz1v1_duel(bot, chat_id: int):
    if chat_id not in QUIZ_DUELS_1V1: return
    duel = QUIZ_DUELS_1V1[chat_id]

    p1_s = duel["p1_score"]
    p2_s = duel["p2_score"]

    if p1_s > p2_s:
        winner_name = duel["sfidante_name"]
        winner_id = duel["sfidante_id"]
    elif p2_s > p1_s:
        winner_name = duel["target_name"]
        winner_id = duel["target_id"]
    else:
        winner_name = None

    if winner_name:
        add_user_coins(chat_id, winner_id, 50)
        text = f"🏆 <b>DUELLO QUIZ CONCLUSO!</b>\n\n🥇 <b>{winner_name}</b> vince il duello ({p1_s} a {p2_s}) e guadagna <b>+💳 50 $SDG</b>!"
    else:
        text = f"⚖️ <b>DUELLO QUIZ FINITO IN PAREGGIO!</b> ({p1_s} a {p2_s}). Nessun premio assegnato."

    del QUIZ_DUELS_1V1[chat_id]
    await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")


# --- REGISTRAZIONE HANDLER ---
def register(app):
    app.add_handler(CallbackQueryHandler(start_roulette_prep, pattern="^start_roulette_"))
    app.add_handler(CallbackQueryHandler(gestione_bottoni_roulette, pattern="^roulette_"))
    app.add_handler(CallbackQueryHandler(start_highlow_prep, pattern="^start_highlow_"))
    app.add_handler(CallbackQueryHandler(handle_highlow_callback, pattern="^hl_"))
    app.add_handler(CallbackQueryHandler(start_ghigliottina_prep, pattern="^start_ghigliottina_prep_"))
    app.add_handler(CallbackQueryHandler(handle_ghigliottina_callback, pattern="^ghig_"))
    app.add_handler(CallbackQueryHandler(start_quiz1v1_prep, pattern="^start_quiz1v1_prep_"))
    app.add_handler(CallbackQueryHandler(handle_quiz1v1_callback, pattern="^q1v1_"))

def register(app):
    app.add_handler(CallbackQueryHandler(start_roulette_prep, pattern="^start_roulette_"))
    app.add_handler(CallbackQueryHandler(gestione_bottoni_roulette, pattern="^roulette_"))
    app.add_handler(CallbackQueryHandler(start_highlow_prep, pattern="^start_highlow_"))
    app.add_handler(CallbackQueryHandler(handle_highlow_callback, pattern="^hl_"))
    app.add_handler(CallbackQueryHandler(start_ghigliottina_prep, pattern="^start_ghigliottina_prep_"))
    app.add_handler(CallbackQueryHandler(handle_ghigliottina_callback, pattern="^ghig_"))
    app.add_handler(CallbackQueryHandler(start_quiz1v1_prep, pattern="^start_quiz1v1_prep_"))
    app.add_handler(CallbackQueryHandler(handle_quiz1v1_callback, pattern="^q1v1_"))
