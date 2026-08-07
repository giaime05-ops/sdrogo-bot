import os
import json
import random
import asyncio
import logging
from threading import Thread
from datetime import datetime, date
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
QUIZ_GAMES = {}
REFLEX_GAMES = {}
PENITENZE_ATTIVE = {}

TARGET_MAP = {
    "manueiii": "🙉", "spoleto17": "🤡", "artemesio": "💩",
    "marco_palestra": "🖕", "albe960": "🥱", "alessioaynonnt": "🐳"
}

IS_TROLLING_ACTIVE = True
FRASE_PENITENZA = "sono un perdente"

# DATABASE QUIZ HARDCODED
QUIZ_CALCIO_DB = [
    {"target": "MESSI", "indizi": ["🇦🇷 Nazionalità: Argentina", "👕 Maglia numero 10 per anni", "🏆 Ha vinto 8 Palloni d'Oro"]},
    {"target": "RONALDO", "indizi": ["🇵🇹 Nazionalità: Portogallo", "⚡ Famoso per la punizione e il 'SIUUU'", "🏆 Ha vinto la Champions con Man Utd e Real Madrid"]},
    {"target": "TOTTI", "indizi": ["🇮🇹 Nazionalità: Italia", "👑 Soprannominato 'Il Ottavo Re di Roma'", "🎯 Ha giocato solo in una squadra in tutta la carriera"]},
    {"target": "IBRAHIMOVIC", "indizi": ["🇸🇪 Nazionalità: Svezia", "🥋 Pratica Arti Marziali / Taekwondo", "🦁 Ha giocato in Juve, Inter e Milan"]}
]

QUIZ_CINEMA_DB = [
    {"target": "TITANIC", "indizio": "🚢 🧊 🏊‍♂️ (Un film romantico e tragico in mezzo all'oceano)"},
    {"target": "INCEPTION", "indizio": "🌀 😴 🕵️‍♂️ (Un film sui sogni dentro i sogni)"},
    {"target": "AVATAR", "indizio": "🪐 💙 🏹 (Popolo alieno blu sul pianeta Pandora)"},
    {"target": "GLADIATORE", "indizio": "🏛️ ⚔️ 🦁 ('Al mio segnale, scatenate l'inferno!')"}
]

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

# --- GESTIONE DATABASE ISOLATO PER CHAT & BACKUP ---
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
                    caption=f"💾 Backup DB SdrogoBot - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                )
        except Exception as e:
            logging.error(f"Errore invio backup Telegram: {e}")

# CHIAVE ISOLATA: CHAT_ID + USER_ID
def get_user_key(chat_id: int, user_id: int) -> str:
    return f"{chat_id}_{user_id}"

def get_user_coins(chat_id: int, user_id: int) -> int:
    key = get_user_key(chat_id, user_id)
    if key not in USER_DATA:
        USER_DATA[key] = {"coins": 100, "last_daily": ""}
        save_db()
    return USER_DATA[key].get("coins", 100)

def add_user_coins(chat_id: int, user_id: int, amount: int):
    key = get_user_key(chat_id, user_id)
    if key not in USER_DATA:
        USER_DATA[key] = {"coins": 100, "last_daily": ""}
    USER_DATA[key]["coins"] = max(0, USER_DATA[key].get("coins", 100) + amount)
    save_db()

def is_admin(user_id: int) -> bool:
    return str(user_id) == str(ADMIN_ID) if ADMIN_ID else False

# --- SDROGOBOT HUB (/sdrogocomm) ---
async def show_hub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    coins = get_user_coins(chat_id, user_id)
    
    text = (
        f"🤖 <b>SDROGOBOT HUB - MENU PRINCIPALE</b> 🎮\n"
        f"─────────────────────────────\n"
        f"💰 <b>Tuo Saldo in questa chat:</b> <code>{coins} $SDG</code>\n\n"
        f"Seleziona una categoria per giocare:"
    )
    
    keyboard = [
        [InlineKeyboardButton("👤 Single Player", callback_data="hub_single"), InlineKeyboardButton("👥 Multiplayer", callback_data="hub_multi")],
        [InlineKeyboardButton("🧠 Quiz Show", callback_data="hub_quiz"), InlineKeyboardButton("💳 Portafoglio / Daily", callback_data="hub_wallet")]
    ]
    
    if update.message:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    elif update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

async def hub_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = query.message.chat_id
    user_id = query.from_user.id
    coins = get_user_coins(chat_id, user_id)

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
            "🎯 <b>Roulette Russa 1v1</b>\nSfida un membro del gruppo a duello russa!\n\n"
            "⚡ <b>Sdrogo Reflex</b> (Costo: 5 $SDG)\nGara di riflessi a tempo. Richiede altri giocatori!"
        )
        keyboard = [
            [InlineKeyboardButton("🎯 Avvia Roulette 1v1", callback_data="start_roulette_prep")],
            [InlineKeyboardButton("⚡ Avvia Sdrogo Reflex", callback_data="start_reflex")],
            back_button
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif data == "hub_quiz":
        text = (
            "🧠 <b>QUIZ SHOW</b>\n\n"
            "⚽ <b>Quiz Calcio</b> (Gratuito - Premio: fino a 10 $SDG)\nIndovina il calciatore con gli indizi!\n\n"
            "🎬 <b>Quiz Cinema</b> (Gratuito - Premio: 10 $SDG)\nIndovina il film famoso dalle emoji."
        )
        keyboard = [
            [InlineKeyboardButton("⚽ Quiz Calcio", callback_data="start_quiz_calcio")],
            [InlineKeyboardButton("🎬 Quiz Cinema", callback_data="start_quiz_cinema")],
            back_button
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif data == "hub_wallet":
        text = (
            f"💳 <b>PORTAFOGLIO & ECONOMIA</b>\n\n"
            f"👤 Utente: <b>{query.from_user.first_name}</b>\n"
            f"💰 Saldo in questa chat: <code>{coins} $SDG</code>\n\n"
            f"🎁 <b>Bonus Giornaliero:</b> Riscuoti 100 $SDG ogni 24 ore."
        )
        keyboard = [
            [InlineKeyboardButton("🎁 Riscuoti Daily (100 $SDG)", callback_data="claim_daily")],
            back_button
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

# --- DAILY ---
async def claim_daily_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat_id
    user_id = query.from_user.id
    key = get_user_key(chat_id, user_id)
    today = str(date.today())

    if key not in USER_DATA:
        USER_DATA[key] = {"coins": 100, "last_daily": ""}

    if USER_DATA[key].get("last_daily") == today:
        await query.answer("❌ Hai già riscosso il bonus giornaliero oggi in questa chat!", show_alert=True)
    else:
        USER_DATA[key]["last_daily"] = today
        add_user_coins(chat_id, user_id, 100)
        await query.answer("🎉 Hai riscosso 100 $SDG!", show_alert=True)
        await backup_to_telegram(context)
        await hub_callback(update, context)

async def block_direct_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚫 <b>I giochi si avviano solo dall'HUB!</b>\nUsa /sdrogocomm per aprire il menu.", parse_mode="HTML")

# --- GAME: BLACKJACK (CON BOTTONI RIGIOCA/HOME) ---
async def start_bj_from_hub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    chat_id = query.message.chat_id

    if get_user_coins(chat_id, user.id) < 5:
        await query.answer("❌ Non hai abbastanza SdrogoCoin (servono 5 $SDG)!", show_alert=True)
        return

    add_user_coins(chat_id, user.id, -5)

    cards = [2, 3, 4, 5, 6, 7, 8, 9, 10, 10, 10, 10, 11]
    player_hand = [random.choice(cards), random.choice(cards)]
    dealer_hand = [random.choice(cards)]

    BLACKJACK_GAMES[str(chat_id)] = {
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
        f"🎎 Carte: {player_hand} (Totale: <b>{sum(player_hand)}</b>)\n"
        f"🤖 Banco: [{dealer_hand[0]}, ?]\n\nCosa fai?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML'
    )

async def handle_bj_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = str(query.message.chat_id)
    user_id = query.from_user.id

    if chat_id not in BLACKJACK_GAMES:
        await query.edit_message_text("❌ Partita terminata.")
        return

    game = BLACKJACK_GAMES[chat_id]
    if user_id != game["player_id"]:
        return

    cards = [2, 3, 4, 5, 6, 7, 8, 9, 10, 10, 10, 10, 11]
    end_keyboard = [
        [InlineKeyboardButton("🔂 Rigioca (5 $SDG)", callback_data="start_bj")],
        [InlineKeyboardButton("🔙 Torna all'HUB", callback_data="hub_main")]
    ]

    if query.data == "bj_hit":
        game["player_hand"].append(random.choice(cards))
        score = sum(game["player_hand"])

        if score > 21:
            del BLACKJACK_GAMES[chat_id]
            await query.edit_message_text(f"💥 <b>SBALLATO!</b> ({score})\nHai perso 5 $SDG!", reply_markup=InlineKeyboardMarkup(end_keyboard), parse_mode='HTML')
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
            add_user_coins(int(chat_id), user_id, 10)
            await query.edit_message_text(f"🏆 <b>VITTORIA!</b> Punti tuoi: {player_score} | Banco: {dealer_score}\nHai vinto <b>10 $SDG</b>!", reply_markup=InlineKeyboardMarkup(end_keyboard), parse_mode='HTML')
        elif player_score < dealer_score:
            await query.edit_message_text(f"❌ <b>SCONFITTA!</b> Punti tuoi: {player_score} | Banco: {dealer_score}\nHai perso la puntata.", reply_markup=InlineKeyboardMarkup(end_keyboard), parse_mode='HTML')
        else:
            add_user_coins(int(chat_id), user_id, 5)
            await query.edit_message_text(f"⚖️ <b>PAREGGIO!</b> Punti: {player_score}\nPuntata di 5 $SDG restituita.", reply_markup=InlineKeyboardMarkup(end_keyboard), parse_mode='HTML')

# --- GAME: WORDLE EXPRESS ---
WORDS = ["PLATO", "CERVO", "SDROG", "CARTA", "SASSI", "FIORE", "GATTO", "TRENO", "FUEGO", "MONDO"]

async def start_wordle_from_hub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    chat_id = query.message.chat_id

    if get_user_coins(chat_id, user.id) < 5:
        await query.answer("❌ Saldo insufficiente (servono 5 $SDG)!", show_alert=True)
        return

    add_user_coins(chat_id, user.id, -5)
    secret_word = random.choice(WORDS)

    WORDLE_GAMES[str(chat_id)] = {
        "player_id": user.id, "secret": secret_word,
        "attempts": 0, "history": []
    }

    await query.edit_message_text(
        f"🔠 <b>WORDLE EXPRESS</b> (Puntata: 5 $SDG)\n\n"
        f"Parola di <b>5 lettere</b> scelta!\n"
        f"Scrivila direttamente in chat per tentare (5 tentativi).",
        parse_mode="HTML"
    )

# --- GAME: QUIZ CALCIO & CINEMA ---
async def start_quiz_calcio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = str(query.message.chat_id)
    
    item = random.choice(QUIZ_CALCIO_DB)
    QUIZ_GAMES[chat_id] = {
        "type": "CALCIO", "target": item["target"],
        "indizi": item["indizi"], "step": 1
    }

    keyboard = [
        [InlineKeyboardButton("💡 Chiedi altro indizio", callback_data="quiz_more_hint")],
        [InlineKeyboardButton("🔙 Torna all'HUB", callback_data="hub_main")]
    ]

    await query.edit_message_text(
        f"⚽ <b>QUIZ CALCIO</b>\n\n"
        f"Indovina il calciatore scrivendo il cognome in chat!\n\n"
        f"<b>1° Indizio:</b> {item['indizi'][0]}",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )

async def start_quiz_cinema(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = str(query.message.chat_id)
    
    item = random.choice(QUIZ_CINEMA_DB)
    QUIZ_GAMES[chat_id] = {"type": "CINEMA", "target": item["target"]}

    keyboard = [[InlineKeyboardButton("🔙 Torna all'HUB", callback_data="hub_main")]]

    await query.edit_message_text(
        f"🎬 <b>QUIZ CINEMA</b>\n\n"
        f"Indovina il titolo del film scrivendolo in chat!\n\n"
        f"<b>Indizio Emoji:</b> {item['indizio']}",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )

async def quiz_more_hint(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = str(query.message.chat_id)

    if chat_id not in QUIZ_GAMES or QUIZ_GAMES[chat_id]["type"] != "CALCIO":
        await query.answer("Nessun quiz calcio attivo.", show_alert=True)
        return

    q = QUIZ_GAMES[chat_id]
    if q["step"] < len(q["indizi"]):
        q["step"] += 1
        hints_text = "\n".join([f"• {q['indizi'][i]}" for i in range(q["step"])])
        
        keyboard = []
        if q["step"] < len(q["indizi"]):
            keyboard.append([InlineKeyboardButton("💡 Chiedi altro indizio", callback_data="quiz_more_hint")])
        keyboard.append([InlineKeyboardButton("🔙 Torna all'HUB", callback_data="hub_main")])

        await query.edit_message_text(
            f"⚽ <b>QUIZ CALCIO</b>\n\nScrivi il cognome in chat!\n\n<b>Indizi sbloccati:</b>\n{hints_text}",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )

# --- GAME: SDROGO REFLEX (ANTI-BOT & ANTI-SOLO EXPLOIT) ---
async def start_reflex_from_hub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat_id
    user_id = query.from_user.id

    if get_user_coins(chat_id, user_id) < 5:
        await query.answer("❌ Servono 5 $SDG per avviare Reflex!", show_alert=True)
        return

    add_user_coins(chat_id, user_id, -5)

    await query.edit_message_text("⚡ <b>SDROGO REFLEX IN ARRIVO...</b>\n\nPronti sul bottone tra pochissimi secondi!", parse_mode="HTML")
    await asyncio.sleep(random.uniform(2.5, 4.5))

    REFLEX_GAMES[str(chat_id)] = {"active": True, "starter_id": user_id}
    keyboard = [[InlineKeyboardButton("🎯 PREMI ORA!", callback_data="reflex_click")]]
    
    await context.bot.send_message(
        chat_id=chat_id,
        text="🔥 <b>ORA! PREMI IL BOTTONE!</b> 🔥",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )

async def reflex_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = str(query.message.chat_id)
    user = query.from_user

    if chat_id in REFLEX_GAMES and REFLEX_GAMES[chat_id].get("active"):
        starter_id = REFLEX_GAMES[chat_id]["starter_id"]
        
        # BLOCCO EXPLOIT: Se gioca da solo chi ha avviato la partita non ottiene il premio da solo
        if user.id == starter_id:
            await query.answer("⚠️ Un altro utente deve cliccare per convalidare la sfida di riflessi!", show_alert=True)
            return

        del REFLEX_GAMES[chat_id]
        add_user_coins(int(chat_id), user.id, 15)
        
        end_keyboard = [[InlineKeyboardButton("🔙 Torna all'HUB", callback_data="hub_main")]]
        await query.answer("🎯 RIFLESSI D'ACCIAIO!")
        await query.edit_message_text(f"🏆 <b>{user.first_name}</b> è stato il più veloce e vince <b>15 $SDG</b>!", reply_markup=InlineKeyboardMarkup(end_keyboard), parse_mode="HTML")

async def start_roulette_prep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.edit_message_text(
        "🎯 <b>ROULETTE RUSSA 1v1</b>\n\nPer lanciare la sfida in chat, usa il comando:\n<code>/roulette @username</code>",
        parse_mode="HTML"
    )

# --- UTILITIES ---
async def toggle_troll(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global IS_TROLLING_ACTIVE
    if not is_admin(update.effective_user.id): return
    IS_TROLLING_ACTIVE = not IS_TROLLING_ACTIVE
    await update.message.reply_text(f"Modalità Auto-Troll: {'ATTIVATA 🙉' if IS_TROLLING_ACTIVE else 'DISATTIVATA 🛑'}")

async def clear_penalties(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    PENITENZE_ATTIVE.clear()
    await update.message.reply_text("🧹 Penitenze rimosse!")

async def reset_duello(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ACTIVE_DUELS.clear()
    BLACKJACK_GAMES.clear()
    WORDLE_GAMES.clear()
    QUIZ_GAMES.clear()
    REFLEX_GAMES.clear()
    await update.message.reply_text("🛠️ Tutti i giochi bloccati sono stati resettati.")

# --- HANDLER MESSAGGI GENERICI (WORDLE, QUIZ & PENITENZE) ---
async def handle_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.from_user: return
    
    user = update.message.from_user
    chat_id_int = update.message.chat_id
    chat_id = str(chat_id_int)
    text = (update.message.text or "").strip()

    # Penitenze
    if user.id in PENITENZE_ATTIVE and PENITENZE_ATTIVE[user.id] > 0:
        if text.lower() != FRASE_PENITENZA:
            try:
                await update.message.delete()
                await context.bot.send_message(chat_id=chat_id, text=f"🚫 Devi scrivere esattamente: `{FRASE_PENITENZA}`", parse_mode="Markdown")
                return
            except Exception: pass
        else:
            del PENITENZE_ATTIVE[user.id]
            await update.message.reply_text(f"✅ @{user.username} riabilitato!")
            return

    text_upper = text.upper()

    # Handling Quiz
    if chat_id in QUIZ_GAMES:
        q = QUIZ_GAMES[chat_id]
        if text_upper == q["target"]:
            del QUIZ_GAMES[chat_id]
            reward = 10 if q["type"] == "CINEMA" else max(5, 12 - (q["step"] * 3))
            add_user_coins(chat_id_int, user.id, reward)
            
            end_keyboard = [
                [InlineKeyboardButton("🧠 Altro Quiz", callback_data="hub_quiz")],
                [InlineKeyboardButton("🔙 Torna all'HUB", callback_data="hub_main")]
            ]
            await update.message.reply_text(
                f"🎉 <b>CORRETTO!</b> <b>{user.first_name}</b> ha indovinato <b>{q['target']}</b>!\nGuadagni <b>{reward} $SDG</b>!",
                reply_markup=InlineKeyboardMarkup(end_keyboard),
                parse_mode="HTML"
            )
            return

    # Handling Wordle
    if chat_id in WORDLE_GAMES and WORDLE_GAMES[chat_id]["player_id"] == user.id:
        game = WORDLE_GAMES[chat_id]
        if len(text_upper) == 5:
            game["attempts"] += 1
            secret = game["secret"]
            result = "".join(["🟩" if text_upper[i] == secret[i] else "🟨" if text_upper[i] in secret else "⬛" for i in range(5)])

            game["history"].append(f"{text_upper} -> {result}")
            res_text = "\n".join(game["history"])

            end_keyboard = [
                [InlineKeyboardButton("🔠 Rigioca (5 $SDG)", callback_data="start_wordle")],
                [InlineKeyboardButton("🔙 Torna all'HUB", callback_data="hub_main")]
            ]

            if text_upper == secret:
                del WORDLE_GAMES[chat_id]
                add_user_coins(chat_id_int, user.id, 15)
                await update.message.reply_text(f"🎉 <b>ESATTO!</b> Parola: <b>{secret}</b>!\nVinti <b>15 $SDG</b>!\n\n{res_text}", reply_markup=InlineKeyboardMarkup(end_keyboard), parse_mode="HTML")
            elif game["attempts"] >= 5:
                del WORDLE_GAMES[chat_id]
                await update.message.reply_text(f"💥 <b>GAME OVER!</b> La parola era <b>{secret}</b>.\n\n{res_text}", reply_markup=InlineKeyboardMarkup(end_keyboard), parse_mode="HTML")
            else:
                await update.message.reply_text(f"🔠 <b>Tentativo {game['attempts']}/5</b>\n\n{res_text}", parse_mode="HTML")
            return

    # Auto-Troll
    if IS_TROLLING_ACTIVE and user.username and user.username.lower() in TARGET_MAP:
        if random.random() < 0.85:
            try: await context.bot.set_message_reaction(chat_id=chat_id, message_id=update.message.message_id, reaction=TARGET_MAP[user.username.lower()])
            except Exception: pass

# --- MAIN ASYNC ---
async def main_async():
    if not TELEGRAM_TOKEN: return
    load_db()

    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()

    application = Application.builder().token(TELEGRAM_TOKEN).build()

    application.add_handler(CommandHandler("sdrogocomm", show_hub))
    application.add_handler(CommandHandler("troll", toggle_troll))
    application.add_handler(CommandHandler("pen", clear_penalties))
    application.add_handler(CommandHandler("resetduello", reset_duello))

    for cmd in ["roulette", "blackjack", "slot", "highlow", "wordle", "quiz"]:
        application.add_handler(CommandHandler(cmd, block_direct_command))

    application.add_handler(CallbackQueryHandler(hub_callback, pattern="^hub_"))
    application.add_handler(CallbackQueryHandler(claim_daily_callback, pattern="^claim_daily$"))
    application.add_handler(CallbackQueryHandler(start_bj_from_hub, pattern="^start_bj$"))
    application.add_handler(CallbackQueryHandler(handle_bj_callback, pattern="^bj_"))
    application.add_handler(CallbackQueryHandler(start_wordle_from_hub, pattern="^start_wordle$"))
    application.add_handler(CallbackQueryHandler(start_quiz_calcio, pattern="^start_quiz_calcio$"))
    application.add_handler(CallbackQueryHandler(start_quiz_cinema, pattern="^start_quiz_cinema$"))
    application.add_handler(CallbackQueryHandler(quiz_more_hint, pattern="^quiz_more_hint$"))
    application.add_handler(CallbackQueryHandler(start_reflex_from_hub, pattern="^start_reflex$"))
    application.add_handler(CallbackQueryHandler(reflex_click, pattern="^reflex_click$"))
    application.add_handler(CallbackQueryHandler(start_roulette_prep, pattern="^start_roulette_prep$"))

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_messages))

    print("SdrogoBot v2.1 aggiornato e pronto!", flush=True)

    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)
    await asyncio.Event().wait()

if __name__ == '__main__':
    try: asyncio.run(main_async())
    except (KeyboardInterrupt, SystemExit): pass
