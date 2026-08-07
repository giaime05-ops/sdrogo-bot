import os
import json
import random
import asyncio
import logging
from threading import Thread
from datetime import datetime, date, timedelta
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions
from telegram.ext import (
    Application, 
    CommandHandler, 
    MessageHandler, 
    CallbackQueryHandler, 
    filters, 
    ContextTypes
)

# --- CONFIGURAZIONE LOGGING ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# --- CONFIGURAZIONE AMBIENTE ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ADMIN_ID = os.environ.get("ADMIN_ID")
GROUP_CHAT_ID = os.environ.get("GROUP_CHAT_ID")
BACKUP_CHAT_ID = os.environ.get("BACKUP_CHAT_ID")

DB_FILE = "database.json"
USER_DATA = {}

# STATI GLOBALI GIOCHI
ACTIVE_DUELS = {}
BLACKJACK_GAMES = {}
WORDLE_GAMES = {}
REFLEX_GAMES = {}
PENITENZE_ATTIVE = {}

TARGET_MAP = {
    "manueiii": "🙉", "spoleto17": "🤡", "artemesio": "💩",
    "marco_palestra": "🖕", "albe960": "🥱", "alessioaynonnt": "🐳"
}

IS_TROLLING_ACTIVE = True
FRASE_PENITENZA = "sono un perdente"

# --- FLASK KEEP ALIVE ---
app = Flask(__name__)

@app.route('/')
def home():
    return "SdrogoBot attivo H24!"

def run_flask():
    port = int(os.environ.get('PORT', 8080))
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    app.run(host='0.0.0.0', port=port)

# --- GESTIONE DATABASE & BACKUP ---
def load_db():
    global USER_DATA
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r") as f:
                USER_DATA = json.load(f)
        except Exception as e:
            logging.error(f"Errore caricamento DB locale: {e}")
            USER_DATA = {}

def save_db():
    try:
        with open(DB_FILE, "w") as f:
            json.dump(USER_DATA, f, indent=2)
    except Exception as e:
        logging.error(f"Errore salvataggio DB locale: {e}")

async def backup_to_telegram(context: ContextTypes.DEFAULT_TYPE):
    if BACKUP_CHAT_ID and os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "rb") as f:
                await context.bot.send_document(
                    chat_id=int(BACKUP_CHAT_ID),
                    document=f,
                    caption=f"💾 Backup automatico SdrogoBot - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                )
        except Exception as e:
            logging.error(f"Errore invio backup Telegram: {e}")

def get_user_coins(user_id: int) -> int:
    uid = str(user_id)
    if uid not in USER_DATA:
        USER_DATA[uid] = {"coins": 100, "last_daily": ""}
        save_db()
    return USER_DATA[uid].get("coins", 100)

def add_user_coins(user_id: int, amount: int):
    uid = str(user_id)
    if uid not in USER_DATA:
        USER_DATA[uid] = {"coins": 100, "last_daily": ""}
    USER_DATA[uid]["coins"] = max(0, USER_DATA[uid].get("coins", 100) + amount)
    save_db()

# --- HELPER PERMETTI ADMIN ---
def is_admin(user_id: int) -> bool:
    return str(user_id) == str(ADMIN_ID) if ADMIN_ID else False

# --- SDROGOBOT HUB (/sdrogocomm) ---
async def show_hub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    coins = get_user_coins(user_id)
    
    text = (
        f"🤖 <b>SDROGOBOT HUB - MENU PRINCIPALE</b> 🎮\n"
        f"─────────────────────────────\n"
        f"💰 <b>Tuo Saldo Attuale:</b> <code>{coins} $SDG</code>\n\n"
        f"Seleziona una categoria qui sotto per accedere ai giochi e ai comandi:"
    )
    
    keyboard = [
        [InlineKeyboardButton("👤 Single Player", callback_data="hub_single"), InlineKeyboardButton("👥 Multiplayer", callback_data="hub_multi")],
        [InlineKeyboardButton("💳 Portafoglio / Daily", callback_data="hub_wallet")]
    ]
    
    if update.message:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    elif update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

async def hub_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id
    coins = get_user_coins(user_id)

    if data == "hub_main":
        await show_hub(update, context)
        return

    back_button = [InlineKeyboardButton("🔙 Torna all'HUB", callback_data="hub_main")]

    if data == "hub_single":
        text = (
            "👤 <b>GIOCHI SINGLE PLAYER</b>\n\n"
            "🃏 <b>Blackjack 21</b> (Costo: 5 $SDG)\nSfida il Banco a carte. Se vinci raddoppi!\n\n"
            "🔠 <b>Wordle Express</b> (Costo: 5 $SDG)\nIndovina la parola di 5 lettere in 5 tentativi."
        )
        keyboard = [
            [InlineKeyboardButton("🃏 Gioca Blackjack (5 $SDG)", callback_data="start_bj")],
            [InlineKeyboardButton("🔠 Gioca Wordle (5 $SDG)", callback_data="start_wordle")],
            back_button
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif data == "hub_multi":
        text = (
            "👥 <b>GIOCHI MULTIPLAYER & GRUPPO</b>\n\n"
            "🎯 <b>Roulette Russa 1v1</b> (Costo: 10 $SDG)\nSfida un membro del gruppo a duello russa!\n\n"
            "⚡ <b>Sdrogo Reflex</b> (Costo: 5 $SDG)\nGara di riflessi a tempo sul bottone."
        )
        keyboard = [
            [InlineKeyboardButton("🎯 Avvia Roulette 1v1", callback_data="start_roulette_prep")],
            [InlineKeyboardButton("⚡ Avvia Sdrogo Reflex", callback_data="start_reflex")],
            back_button
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif data == "hub_wallet":
        text = (
            f"💳 <b>PORTAFOGLIO & ECONOMIA</b>\n\n"
            f"👤 Utente: <b>{query.from_user.first_name}</b>\n"
            f"💰 Saldo attuale: <code>{coins} $SDG</code>\n\n"
            f"🎁 <b>Bonus Giornaliero:</b> Puoi riscuotere 100 $SDG ogni 24 ore."
        )
        keyboard = [
            [InlineKeyboardButton("🎁 Riscuoti Daily (100 $SDG)", callback_data="claim_daily")],
            back_button
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

# --- COMANDO DAILY ---
async def claim_daily_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = str(query.from_user.id)
    today = str(date.today())

    if user_id not in USER_DATA:
        USER_DATA[user_id] = {"coins": 100, "last_daily": ""}

    if USER_DATA[user_id].get("last_daily") == today:
        await query.answer("❌ Hai già riscosso il tuo bonus giornaliero oggi! Torna domani.", show_alert=True)
    else:
        USER_DATA[user_id]["last_daily"] = today
        add_user_coins(query.from_user.id, 100)
        await query.answer("🎉 Hai riscosso 100 $SDG!", show_alert=True)
        await backup_to_telegram(context)
        await hub_callback(update, context)

# --- BLOCCO COMANDI DIRETTI ---
async def block_direct_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚫 <b>I giochi si avviano solo dall'HUB!</b>\n"
        "Usa il comando /sdrogocomm per aprire il menu e scegliere a cosa giocare.",
        parse_mode="HTML"
    )

# --- GAME: BLACKJACK ---
async def start_bj_from_hub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    chat_id = str(query.message.chat_id)

    if get_user_coins(user.id) < 5:
        await query.answer("❌ Non hai abbastanza SdrogoCoin (servono 5 $SDG)!", show_alert=True)
        return

    add_user_coins(user.id, -5)

    cards = [2, 3, 4, 5, 6, 7, 8, 9, 10, 10, 10, 10, 11]
    player_hand = [random.choice(cards), random.choice(cards)]
    dealer_hand = [random.choice(cards)]

    BLACKJACK_GAMES[chat_id] = {
        "player_id": user.id, "player_name": user.first_name,
        "player_hand": player_hand, "dealer_hand": dealer_hand
    }

    keyboard = [[
        InlineKeyboardButton("🎴 Carta", callback_data="bj_hit"),
        InlineKeyboardButton("✋ Stai", callback_data="bj_stand")
    ]]

    await query.edit_message_text(
        f"🃏 <b>BLACKJACK 21</b> (Puntata: 5 $SDG)\n\n"
        f"👤 Giocatore: <b>{user.first_name}</b>\n"
        f"... Carte: {player_hand} (Totale: <b>{sum(player_hand)}</b>)\n"
        f"🤖 Banco: [{dealer_hand[0]}, ?]\n\nCosa fai?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML'
    )

async def handle_bj_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = str(query.message.chat.id)
    user_id = query.from_user.id

    if chat_id not in BLACKJACK_GAMES:
        await query.edit_message_text("❌ Partita terminata.")
        return

    game = BLACKJACK_GAMES[chat_id]
    if user_id != game["player_id"]:
        return

    cards = [2, 3, 4, 5, 6, 7, 8, 9, 10, 10, 10, 10, 11]

    if query.data == "bj_hit":
        game["player_hand"].append(random.choice(cards))
        score = sum(game["player_hand"])

        if score > 21:
            del BLACKJACK_GAMES[chat_id]
            await query.edit_message_text(f"💥 <b>SBALLATO!</b> ({score})\nHai perso i 5 $SDG di puntata!", parse_mode='HTML')
        else:
            keyboard = [[InlineKeyboardButton("🎴 Carta", callback_data="bj_hit"), InlineKeyboardButton("✋ Stai", callback_data="bj_stand")]]
            await query.edit_message_text(f"🃏 <b>BLACKJACK 21</b>\n\nCarte: {game['player_hand']} ({score})\nBanco: [{game['dealer_hand'][0]}, ?]", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

    elif query.data == "bj_stand":
        player_score = sum(game["player_hand"])
        dealer_hand = game["dealer_hand"]
        while sum(dealer_hand) < 17:
            dealer_hand.append(random.choice(cards))
        dealer_score = sum(dealer_hand)
        del BLACKJACK_GAMES[chat_id]

        if dealer_score > 21 or player_score > dealer_score:
            add_user_coins(user_id, 10)
            await query.edit_message_text(f"🏆 <b>VITTORIA!</b> Punti tuoi: {player_score} | Banco: {dealer_score}\nHai vinto <b>10 $SDG</b>!", parse_mode='HTML')
        elif player_score < dealer_score:
            await query.edit_message_text(f"❌ <b>SCONFITTA!</b> Punti tuoi: {player_score} | Banco: {dealer_score}\nHai perso la puntata.", parse_mode='HTML')
        else:
            add_user_coins(user_id, 5)
            await query.edit_message_text(f"⚖️ <b>PAREGGIO!</b> Punti: {player_score}\nPuntata di 5 $SDG restituita.", parse_mode='HTML')

# --- GAME: WORDLE EXPRESS ---
WORDS = ["PLATO", "CERVO", "SDROG", "CARTA", "SASSI", "FIORE", "GATTO", "TRENO", "FUEGO", "MONDO"]

async def start_wordle_from_hub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    chat_id = str(query.message.chat_id)

    if get_user_coins(user.id) < 5:
        await query.answer("❌ Saldo insufficiente (servono 5 $SDG)!", show_alert=True)
        return

    add_user_coins(user.id, -5)
    secret_word = random.choice(WORDS)

    WORDLE_GAMES[chat_id] = {
        "player_id": user.id,
        "secret": secret_word,
        "attempts": 0,
        "history": []
    }

    await query.edit_message_text(
        f"🔠 <b>WORDLE EXPRESS</b> (Puntata: 5 $SDG)\n\n"
        f"Ho scelto una parola di <b>5 lettere</b>!\n"
        f"Scrivi la parola direttamente in chat per tentare.\n"
        f"Hai 5 tentativi a disposizione!",
        parse_mode="HTML"
    )

# --- GAME: SDROGO REFLEX ---
async def start_reflex_from_hub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat_id
    
    await query.edit_message_text("⚡ <b>SDROGO REFLEX IN ARRIVO...</b>\n\nPronti sul bottone tra pochissimi secondi!", parse_mode="HTML")
    await asyncio.sleep(random.uniform(2.5, 5.0))

    REFLEX_GAMES[chat_id] = {"active": True}
    keyboard = [[InlineKeyboardButton("🎯 PREMI ORA!", callback_data="reflex_click")]]
    
    await context.bot.send_message(
        chat_id=chat_id,
        text="🔥 <b>ORA! PREMI IL BOTTONE!</b> 🔥",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )

async def reflex_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat_id
    user = query.from_user

    if chat_id in REFLEX_GAMES and REFLEX_GAMES[chat_id].get("active"):
        del REFLEX_GAMES[chat_id]
        add_user_coins(user.id, 15)
        await query.answer("🎯 RIFLESSI D'ACCIAIO!")
        await query.edit_message_text(f"🏆 <b>{user.first_name}</b> è stato il più veloce di tutti e vince <b>15 $SDG</b>!", parse_mode="HTML")
    else:
        await query.answer("❌ Arrivato tardi!", show_alert=True)

# --- GAME: ROULETTE RUSSA ---
async def start_roulette_prep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.edit_message_text(
        "🎯 <b>ROULETTE RUSSA 1v1</b>\n\n"
        "Per lanciare la sfida in chat, usa la sintassi del duello specificando lo sfidato con:\n"
        "<code>/roulette @username</code> dall'HUB!",
        parse_mode="HTML"
    )

# --- UTILITIES & ADMIN COMMANDS ---
async def toggle_troll(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global IS_TROLLING_ACTIVE
    if not is_admin(update.effective_user.id):
        return
    IS_TROLLING_ACTIVE = not IS_TROLLING_ACTIVE
    stato = "ATTIVATA 🙉" if IS_TROLLING_ACTIVE else "DISATTIVATA 🛑"
    await update.message.reply_text(f"Modalità Auto-Troll: {stato}")

async def clear_penalties(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    PENITENZE_ATTIVE.clear()
    await update.message.reply_text("🧹 Penitenze rimosse!")

async def reset_duello(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ACTIVE_DUELS.clear()
    BLACKJACK_GAMES.clear()
    WORDLE_GAMES.clear()
    REFLEX_GAMES.clear()
    await update.message.reply_text("🛠️ Tutti i giochi bloccati sono stati resettati.")

# --- HANDLER MESSAGGI GENERICI (PENITENZE & WORDLE) ---
async def handle_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.from_user:
        return
    
    user = update.message.from_user
    chat_id = str(update.message.chat_id)
    text = (update.message.text or "").strip()

    # Gestione Penitenza (RIDOTTA A 1 VOLTA)
    if user.id in PENITENZE_ATTIVE and PENITENZE_ATTIVE[user.id] > 0:
        if text.lower() != FRASE_PENITENZA:
            try:
                await update.message.delete()
                user_tag = f"@{user.username}" if user.username else user.first_name
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"🚫 Messaggio di {user_tag} eliminato! Devi scrivere esattamente: `{FRASE_PENITENZA}` per sbloccarti.",
                    parse_mode="Markdown"
                )
                return
            except Exception:
                pass
        else:
            # Penitenza completata alla prima volta
            del PENITENZE_ATTIVE[user.id]
            try:
                await context.bot.set_chat_administrator_custom_title(chat_id=chat_id, user_id=user.id, custom_title="")
            except Exception:
                pass
            await update.message.reply_text(f"✅ Penitenza completata! @{user.username} ha ammesso di essere un perdente ed è stato riabilitato!")
            return

    # Gestione Wordle
    text_upper = text.upper()
    if chat_id in WORDLE_GAMES and WORDLE_GAMES[chat_id]["player_id"] == user.id:
        game = WORDLE_GAMES[chat_id]
        if len(text_upper) == 5:
            game["attempts"] += 1
            secret = game["secret"]
            result = ""
            for i in range(5):
                if text_upper[i] == secret[i]:
                    result += "🟩"
                elif text_upper[i] in secret:
                    result += "🟨"
                else:
                    result += "⬛"

            game["history"].append(f"{text_upper} -> {result}")
            res_text = "\n".join(game["history"])

            if text_upper == secret:
                del WORDLE_GAMES[chat_id]
                add_user_coins(user.id, 15)
                await update.message.reply_text(f"🎉 <b>ESATTO!</b> La parola era <b>{secret}</b>!\nHai vinto <b>15 $SDG</b>!\n\n{res_text}", parse_mode="HTML")
            elif game["attempts"] >= 5:
                del WORDLE_GAMES[chat_id]
                await update.message.reply_text(f"💥 <b>GAME OVER!</b> Hai finito i tentativi. La parola era <b>{secret}</b>.\n\n{res_text}", parse_mode="HTML")
            else:
                await update.message.reply_text(f"🔠 <b>Tentativo {game['attempts']}/5</b>\n\n{res_text}", parse_mode="HTML")
            return

    # Auto-Troll reazioni
    if IS_TROLLING_ACTIVE and user.username and user.username.lower() in TARGET_MAP:
        if random.random() < 0.85:
            try:
                await context.bot.set_message_reaction(chat_id=chat_id, message_id=update.message.message_id, reaction=TARGET_MAP[user.username.lower()])
            except Exception:
                pass

# --- MAIN ASYNC ---
async def main_async():
    if not TELEGRAM_TOKEN:
        logging.error("ERRORE: TELEGRAM_TOKEN mancante!")
        return

    load_db()

    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()

    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # Registrazione Comandi HUB & Utilities
    application.add_handler(CommandHandler("sdrogocomm", show_hub))
    application.add_handler(CommandHandler("troll", toggle_troll))
    application.add_handler(CommandHandler("pen", clear_penalties))
    application.add_handler(CommandHandler("resetduello", reset_duello))

    # Blocco Comandi Diretti
    for cmd in ["roulette", "blackjack", "slot", "highlow", "wordle", "quiz"]:
        application.add_handler(CommandHandler(cmd, block_direct_command))

    # Callbacks HUB & Giochi
    application.add_handler(CallbackQueryHandler(hub_callback, pattern="^hub_"))
    application.add_handler(CallbackQueryHandler(claim_daily_callback, pattern="^claim_daily$"))
    application.add_handler(CallbackQueryHandler(start_bj_from_hub, pattern="^start_bj$"))
    application.add_handler(CallbackQueryHandler(handle_bj_callback, pattern="^bj_"))
    application.add_handler(CallbackQueryHandler(start_wordle_from_hub, pattern="^start_wordle$"))
    application.add_handler(CallbackQueryHandler(start_reflex_from_hub, pattern="^start_reflex$"))
    application.add_handler(CallbackQueryHandler(reflex_click, pattern="^reflex_click$"))
    application.add_handler(CallbackQueryHandler(start_roulette_prep, pattern="^start_roulette_prep$"))

    # Handler Messaggi di Testo
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_messages))

    print("SdrogoBot v2 pronto all'uso con penitenza singola!", flush=True)

    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)
    await asyncio.Event().wait()

if __name__ == '__main__':
    try:
        asyncio.run(main_async())
    except (KeyboardInterrupt, SystemExit):
        pass
