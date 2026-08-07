import os
import json
import random
import asyncio
import logging
from threading import Thread
from datetime import datetime, date
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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
BOMBA_GAMES = {}
PENITENZE_ATTIVE = {}

TARGET_MAP = {
    "manueiii": "🙉", "spoleto17": "🤡", "artemesio": "💩",
    "marco_palestra": "🖕", "albe960": "🥱", "alessioaynonnt": "🐳"
}

IS_TROLLING_ACTIVE = True
FRASE_PENITENZA = "sono un perdente"

# --- DATABASE WORDLE (50 PAROLE DA 5 LETTERE) ---
WORDS = [
    "PLATO", "CERVO", "SDROG", "CARTA", "SASSI", "FIORE", "GATTO", "TRENO", "FUEGO", "MONDO",
    "ACQUA", "AMICO", "BARCA", "CALCIO", "DADO", "ELICA", "FORNO", "GUSTO", "HOTEL", "ISOLA",
    "LEONE", "MAGIA", "NOTTE", "OMBRA", "PRATO", "QUEST", "RAGGIO", "SOGNO", "TERRA", "UMORE",
    "VERDE", "ZAINO", "ACIDO", "BAMBO", "CAMPO", "DISCO", "EBANO", "FANGO", "GRANO", "LANZA",
    "MANGO", "NOBILE", "PIAZZA", "ROBOT", "SCALA", "TASSA", "VAPORE", "ZERO", "ZINCO", "PIANO"
]

# --- DATABASE QUIZ CALCIO (30 CALCIATORI) ---
QUIZ_CALCIO_DB = [
    {"target": "MESSI", "indizi": ["🇦🇷 Argentina", "👕 Inter Miami", "🏆 8 Palloni d'Oro"]},
    {"target": "RONALDO", "indizi": ["🇵🇹 Portogallo", "👕 Al-Nassr", "⚡ SIUUU"]},
    {"target": "TOTTI", "indizi": ["🇮🇹 Italia", "👕 Roma", "👑 Capitano Storico"]},
    {"target": "IBRAHIMOVIC", "indizi": ["🇸🇪 Svezia", "👕 AC Milan", "🦁 Zlatan"]},
    {"target": "HAALAND", "indizi": ["🇳🇴 Norvegia", "👕 Manchester City", "🤖 Cyborg"]},
    {"target": "MBAPPE", "indizi": ["🇫🇷 Francia", "👕 Real Madrid", "⚡ Tartaruga Ninja"]},
    {"target": "DEL PIERO", "indizi": ["🇮🇹 Italia", "👕 Juventus", "🎯 Pinturicchio"]},
    {"target": "LAUTARO", "indizi": ["🇦🇷 Argentina", "👕 Inter", "🐂 Toro"]},
    {"target": "OSIMHEN", "indizi": ["🇳🇬 Nigeria", "👕 Napoli", "🎭 Maschera"]},
    {"target": "MODRIC", "indizi": ["🇭🇷 Croazia", "👕 Real Madrid", "🪄 Mago Crotone"]},
    {"target": "BAGGIO", "indizi": ["🇮🇹 Italia", "👕 Brescia / Juve", "🧘 Divin Codino"]},
    {"target": "MARADONA", "indizi": ["🇦🇷 Argentina", "👕 Napoli", "🖐️ Mano de Dios"]},
    {"target": "PELÉ", "indizi": ["🇧🇷 Brasile", "👕 Santos", "👑 O Rei"]},
    {"target": "ZIDANE", "indizi": ["🇫🇷 Francia", "👕 Real Madrid", "💥 Testata 2006"]},
    {"target": "PIRLO", "indizi": ["🇮🇹 Italia", "👕 Juventus / Milan", "🎯 No Look / Cucchiai"]},
    {"target": "BUFFON", "indizi": ["🇮🇹 Italia", "👕 Parma / Juve", "🧤 Numero 1"]},
    {"target": "MALDINI", "indizi": ["🇮🇹 Italia", "👕 AC Milan", "🛡️ Difensore Eterno"]},
    {"target": "RONALDINHO", "indizi": ["🇧🇷 Brasile", "👕 Barcellona", "🤙 Joga Bonito"]},
    {"target": "KAKA", "indizi": ["🇧🇷 Brasile", "👕 AC Milan", "⚡ Pallone d'Oro 2007"]},
    {"target": "BENZEMA", "indizi": ["🇫🇷 Francia", "👕 Al-Ittihad / Real", "🥊 Karimm"]},
    {"target": "LEWANDOWSKI", "indizi": ["🇵🇱 Polonia", "👕 Barcellona", "⚽ 5 gol in 9 min"]},
    {"target": "NEUER", "indizi": ["🇩🇪 Germania", "👕 Bayern Monaco", "🧤 Portiere Libero"]},
    {"target": "SALAH", "indizi": ["🇪🇬 Egitto", "👕 Liverpool", "👑 Re d'Egitto"]},
    {"target": "VINICIUS", "indizi": ["🇧🇷 Brasile", "👕 Real Madrid", "⚡ Ballerino"]},
    {"target": "BELINGHAM", "indizi": ["🏴󠁧󠁢󠁥󠁮󠁧󠁿 Inghilterra", "👕 Real Madrid", "🙌 Esultanza Braccia"]},
    {"target": "DYBALA", "indizi": ["🇦🇷 Argentina", "👕 Roma", "🎭 Dybala Mask"]},
    {"target": "KVARATSKHELIA", "indizi": ["🇬🇪 Georgia", "👕 Napoli", "🪄 Kvaradona"]},
    {"target": "Barella", "indizi": ["🇮🇹 Italia", "👕 Inter", "🏃 Polmone Sardo"]},
    {"target": "CHIESA", "indizi": ["🇮🇹 Italia", "👕 Liverpool / Juve", "⚡ Furia Azzurra"]},
    {"target": "DONNARUMMA", "indizi": ["🇮🇹 Italia", "👕 PSG", "🧤 Eroe Euro 2020"]}
]

# --- DATABASE QUIZ CINEMA (30 FILM) ---
QUIZ_CINEMA_DB = [
    {"target": "TITANIC", "indizi": ["🚢 Transatlantico", "🧊 Iceberg", "🏊‍♂️ Naufragio"]},
    {"target": "INCEPTION", "indizi": ["🌀 Trottole", "😴 Sogni", "🕵️‍♂️ Furto Mente"]},
    {"target": "AVATAR", "indizi": ["🪐 Pandora", "💙 Popolo Blu", "🏹 Arcieri Alieni"]},
    {"target": "GLADIATORE", "indizi": ["🏛️ Roma Antica", "⚔️ Arena", "🦁 Massimo Meridio"]},
    {"target": "MATRIX", "indizi": ["🕶️ Occhiali Neri", "🔴 Pillola Rossa", "💻 Codice Verde"]},
    {"target": "HARRY POTTER", "indizi": ["🧙‍♂️ Bacchetta", "⚡ Cicatrice", "🏰 Hogwarts"]},
    {"target": "JOKER", "indizi": ["🤡 Trucco Faccia", "🌆 Gotham", "🕺 Scalinata"]},
    {"target": "PULP FICTION", "indizi": ["💼 Valigetta", "🕺 Ballo Twist", "🔫 Tarantino"]},
    {"target": "INTERSTELLAR", "indizi": ["🚀 Spazio", "🕳️ Buco Nero", "⏳ Tempo Relativo"]},
    {"target": "SHREK", "indizi": ["🟢 Orco Verde", "🫏 Ciuccio Parlante", "🏰 Palude"]},
    {"target": "SPIDERMAN", "indizi": ["🕷️ Ragnatela", "🔴 Tuta Rossa", "🏙️ New York"]},
    {"target": "BATMAN", "indizi": ["🦇 Pipistrello", "🏎️ Batmobile", "🌆 Gotham City"]},
    {"target": "FIGHT CLUB", "indizi": ["🧼 Sapone", "👊 Regola Numero 1", "🧠 Tyler Durden"]},
    {"target": "FORREST GUMP", "indizi": ["🏃 Corsa Infinita", "🍫 Scatola Cioccolatini", "🪶 Piuma"]},
    {"target": "PARASITE", "indizi": ["🇰🇷 Corea", "🏠 Seminterrato", "🍕 Scatole Pizza"]},
    {"target": "OPENHEIMER", "indizi": ["💣 Bomba Atomica", "🧪 Fisica", "💥 Progetto Manhattan"]},
    {"target": "BARBIE", "indizi": ["🩷 Mondo Rosa", "👠 Tacco Alto", "👱‍♂️ Ken"]},
    {"target": "SQUALO", "indizi": ["🦈 Pesce Cane", "🌊 Oceano", "🚤 Barca distrutta"]},
    {"target": "GHOSTBUSTERS", "indizi": ["👻 Fantasmi", "🚫 Logo Divieto", "🔫 Raggio Protonico"]},
    {"target": "JURASSIC PARK", "indizi": ["🦖 Dinosauri", "🧬 Zanzara Ambra", "🏝️ Isola Nublar"]},
    {"target": "STAR WARS", "indizi": ["⚔️ Spada Laser", "🌌 Galassia", "🤖 Darth Vader"]},
    {"target": "PADRINO", "indizi": ["🎩 Mafia", "🇮🇹 Sicilia", "🌹 Don Corleone"]},
    {"target": "SCARFACE", "indizi": ["🇨🇺 Cuba", "💵 Soldi e Potere", "🔫 Tony Montana"]},
    {"target": "COCO", "indizi": ["🇲🇽 Messico", "💀 Chitarra", "🌺 Regno dei Morti"]},
    {"target": "TOY STORY", "indizi": ["🤠 Cowboy Legno", "🚀 Astronauta Plastica", "🧸 Giocattoli"]},
    {"target": "INSIDE OUT", "indizi": ["🧠 Emozioni", "🟡 Gioia", "🔵 Tristezza"]},
    {"target": "MAD MAX", "indizi": ["🏜️ Deserto", "🚗 Auto Armate", "🔥 Chitarra Fiamme"]},
    {"target": "SHINING", "indizi": ["🏨 Hotel Vuoto", "🪓 Ascia", "👯‍♀️ Gemelline"]},
    {"target": "ALIEN", "indizi": ["👽 Mostro Spazio", "🚀 Astronave", "🥚 Uovo Nero"]},
    {"target": "ROCKY", "indizi": ["🥊 Pugilato", "🏃 Scalinata Philadelphia", "🔔 Campana"]}
]

# --- FLASK KEEP ALIVE ---
app = Flask(__name__)

@app.route('/')
def home():
    return "SdrogoBot v3.2 Attivo H24!"

def run_flask():
    port = int(os.environ.get('PORT', 8080))
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    app.run(host='0.0.0.0', port=port)

# --- DATABASE LOCALE ---
def load_db():
    global USER_DATA
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r") as f:
                USER_DATA = json.load(f)
        except Exception as e:
            logging.error(f"Errore caricamento DB: {e}")
            USER_DATA = {}

def save_db():
    try:
        with open(DB_FILE, "w") as f:
            json.dump(USER_DATA, f, indent=2)
    except Exception as e:
        logging.error(f"Errore salvataggio DB: {e}")

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
            logging.error(f"Errore backup Telegram: {e}")

def get_user_key(chat_id: int, user_id: int) -> str:
    return f"{chat_id}_{user_id}"

def get_user_coins(chat_id: int, user_id: int) -> int:
    key = get_user_key(chat_id, user_id)
    if key not in USER_DATA:
        USER_DATA[key] = {"coins": 50, "last_daily": ""}
        if str(chat_id) != str(BACKUP_CHAT_ID):
            save_db()
    return USER_DATA[key].get("coins", 50)

def add_user_coins(chat_id: int, user_id: int, amount: int):
    if str(chat_id) == str(BACKUP_CHAT_ID):
        return
    key = get_user_key(chat_id, user_id)
    if key not in USER_DATA:
        USER_DATA[key] = {"coins": 50, "last_daily": ""}
    USER_DATA[key]["coins"] = max(0, USER_DATA[key].get("coins", 50) + amount)
    save_db()

def is_admin(user_id: int) -> bool:
    return str(user_id) == str(ADMIN_ID) if ADMIN_ID else False

# --- SDROGOBOT HUB (/sdrogocomm) ---
async def show_hub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    coins = get_user_coins(chat_id, user.id)
    
    text = (
        "🎰 <b>━━━━━━━━━━━━━━━━━━</b> 🎰\n"
        "       <b>SDROGOBOT ARCADE HUB</b> 🎮\n"
        "🎰 <b>━━━━━━━━━━━━━━━━━━</b> 🎰\n\n"
        f"👤 <b>Giocatore:</b> {user.first_name}\n"
        f"💰 <b>Saldo Chat:</b> <code>{coins} $SDG</code>\n\n"
        "⚡ <i>Scegli una categoria dal menu per giocare:</i>"
    )
    
    keyboard = [
        [InlineKeyboardButton("🕹️ Single Player", callback_data="hub_single"), InlineKeyboardButton("⚔️ Multiplayer", callback_data="hub_multi")],
        [InlineKeyboardButton("🧠 Quiz Show", callback_data="hub_quiz"), InlineKeyboardButton("💳 Portafoglio", callback_data="hub_wallet")],
        [InlineKeyboardButton("🏆 Classifica Ricconi $SDG", callback_data="hub_leaderboard")]
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
            "🕹️ <b>GIOCHI SINGLE PLAYER</b>\n"
            "─────────────────────────────\n\n"
            "🃏 <b>Blackjack 21</b> (Costo: 10 $SDG)\nSfida il Banco a carte e fai 21!\n\n"
            "🔠 <b>Wordle Express</b> (Costo: 10 $SDG)\nIndovina la parola di 5 lettere in 5 tentativi."
        )
        keyboard = [
            [InlineKeyboardButton("🃏 Blackjack (10 $SDG)", callback_data="start_bj")],
            [InlineKeyboardButton("🔠 Wordle (10 $SDG)", callback_data="start_wordle")],
            back_button
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif data == "hub_multi":
        text = (
            "⚔️ <b>GIOCHI MULTIPLAYER</b>\n"
            "─────────────────────────────\n\n"
            "🎯 <b>Roulette Russa 1v1</b>\nSfida un utente a duello russa dall'HUB!\n\n"
            "💣 <b>Bomba a Parola (TEST)</b>\nInizia la catena della bomba in chat."
        )
        keyboard = [
            [InlineKeyboardButton("🎯 Avvia Roulette 1v1", callback_data="start_roulette_prep")],
            [InlineKeyboardButton("💣 Inizia Bomba a Parola", callback_data="start_bomba_prep")],
            back_button
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif data == "hub_quiz":
        text = (
            "🧠 <b>QUIZ SHOW</b> (Costo: 5 $SDG)\n"
            "─────────────────────────────\n\n"
            "⚽ <b>Quiz Calcio</b>\nIndovina il calciatore segreto dai 3 indizi!\n\n"
            "🎬 <b>Quiz Cinema</b>\nIndovina il film famoso dalle parole chiave/emoji."
        )
        keyboard = [
            [InlineKeyboardButton("⚽ Quiz Calcio (5 $SDG)", callback_data="start_quiz_calcio")],
            [InlineKeyboardButton("🎬 Quiz Cinema (5 $SDG)", callback_data="start_quiz_cinema")],
            back_button
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif data == "hub_wallet":
        text = (
            "💳 <b>PORTAFOGLIO & ECONOMIA</b>\n"
            "─────────────────────────────\n\n"
            f"👤 Giocatore: <b>{query.from_user.first_name}</b>\n"
            f"💰 Saldo attuale: <code>{coins} $SDG</code>\n\n"
            "🎁 <b>Bonus Daily:</b> Riscuoti 50 $SDG ogni 24 ore."
        )
        keyboard = [
            [InlineKeyboardButton("🎁 Riscuoti Daily (+50 $SDG)", callback_data="claim_daily")],
            back_button
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif data == "hub_leaderboard":
        await show_leaderboard(update, context)

# --- CLASSIFICA RICCONI (/topricconi) ---
async def show_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat_id if query else update.effective_chat.id

    prefix = f"{chat_id}_"
    chat_users = []

    for key, data in USER_DATA.items():
        if key.startswith(prefix):
            uid = key.split("_")[1]
            coins = data.get("coins", 0)
            chat_users.append((uid, coins))

    chat_users.sort(key=lambda x: x[1], reverse=True)

    text = "🏆 <b>CLASSIFICA RICCONI $SDG</b> 💰\n─────────────────────────────\n\n"
    medals = ["🥇", "🥈", "🥉"]

    for idx, (uid, coins) in enumerate(chat_users[:10], start=1):
        rank_icon = medals[idx-1] if idx <= 3 else f"{idx}."
        try:
            member = await context.bot.get_chat_member(chat_id, int(uid))
            name = member.user.first_name
        except Exception:
            name = f"Giocatore {uid[-4:]}"

        text += f"{rank_icon} <b>{name}</b> — <code>{coins} $SDG</code>\n"

    if not chat_users:
        text += "<i>Nessun dato presente in classifica.</i>\n"

    keyboard = [[InlineKeyboardButton("🔙 Torna all'HUB", callback_data="hub_main")]]

    if query:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

# --- DAILY ---
async def claim_daily_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat_id
    user_id = query.from_user.id
    key = get_user_key(chat_id, user_id)
    today = str(date.today())

    if key not in USER_DATA:
        USER_DATA[key] = {"coins": 50, "last_daily": ""}

    if USER_DATA[key].get("last_daily") == today:
        await query.answer("❌ Bonus giornaliero già riscosso oggi!", show_alert=True)
    else:
        USER_DATA[key]["last_daily"] = today
        add_user_coins(chat_id, user_id, 50)
        await query.answer("🎉 Hai riscosso +50 $SDG!", show_alert=True)
        await backup_to_telegram(context)
        await hub_callback(update, context)

async def block_direct_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚫 <b>I giochi si avviano solo dall'HUB!</b>\nUsa /sdrogocomm per accedere.", parse_mode="HTML")

# --- GAME: BLACKJACK ---
async def start_bj_from_hub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    chat_id = query.message.chat_id

    if get_user_coins(chat_id, user.id) < 10:
        await query.answer("❌ Servono 10 $SDG per giocare a Blackjack!", show_alert=True)
        return

    add_user_coins(chat_id, user.id, -10)

    cards = [2, 3, 4, 5, 6, 7, 8, 9, 10, 10, 10, 10, 11]
    player_hand = [random.choice(cards), random.choice(cards)]
    dealer_hand = [random.choice(cards)]

    BLACKJACK_GAMES[f"{chat_id}_{user.id}"] = {
        "player_id": user.id, "player_hand": player_hand, "dealer_hand": dealer_hand
    }

    keyboard = [[
        InlineKeyboardButton("🎴 Carta", callback_data="bj_hit"),
        InlineKeyboardButton("✋ Stai", callback_data="bj_stand")
    ]]

    await query.edit_message_text(
        f"🃏 <b>BLACKJACK 21</b> (Puntata: 10 $SDG)\n"
        f"─────────────────────────────\n\n"
        f"👤 Giocatore: <b>{user.first_name}</b>\n"
        f"🎎 Carte: {player_hand} (Totale: <b>{sum(player_hand)}</b>)\n"
        f"🤖 Banco: [{dealer_hand[0]}, ?]\n\nCosa fai?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML'
    )

async def handle_bj_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    user_id = query.from_user.id
    game_key = f"{chat_id}_{user_id}"

    if game_key not in BLACKJACK_GAMES:
        await query.edit_message_text("❌ Partita terminata.")
        return

    game = BLACKJACK_GAMES[game_key]
    cards = [2, 3, 4, 5, 6, 7, 8, 9, 10, 10, 10, 10, 11]
    end_keyboard = [
        [InlineKeyboardButton("🔂 Rigioca (10 $SDG)", callback_data="start_bj")],
        [InlineKeyboardButton("🔙 Torna all'HUB", callback_data="hub_main")]
    ]

    if query.data == "bj_hit":
        game["player_hand"].append(random.choice(cards))
        score = sum(game["player_hand"])

        if score > 21:
            del BLACKJACK_GAMES[game_key]
            await query.edit_message_text(f"💥 <b>SBALLATO!</b> ({score})\nHai perso 10 $SDG!", reply_markup=InlineKeyboardMarkup(end_keyboard), parse_mode='HTML')
        else:
            keyboard = [[InlineKeyboardButton("🎴 Carta", callback_data="bj_hit"), InlineKeyboardButton("✋ Stai", callback_data="bj_stand")]]
            await query.edit_message_text(f"🃏 <b>BLACKJACK 21</b>\n\nCarte: {game['player_hand']} ({score})\nBanco: [{game['dealer_hand'][0]}, ?]", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

    elif query.data == "bj_stand":
        player_score = sum(game["player_hand"])
        dealer_hand = game["dealer_hand"]
        while sum(dealer_hand) < 17:
            dealer_hand.append(random.choice(cards))
        dealer_score = sum(dealer_hand)
        del BLACKJACK_GAMES[game_key]

        if dealer_score > 21 or player_score > dealer_score:
            add_user_coins(chat_id, user_id, 15)
            await query.edit_message_text(f"🏆 <b>VITTORIA!</b> Tu: {player_score} | Banco: {dealer_score}\nHai vinto <b>+15 $SDG</b>!", reply_markup=InlineKeyboardMarkup(end_keyboard), parse_mode='HTML')
        elif player_score < dealer_score:
            await query.edit_message_text(f"❌ <b>SCONFITTA!</b> Tu: {player_score} | Banco: {dealer_score}\nHai perso la puntata.", reply_markup=InlineKeyboardMarkup(end_keyboard), parse_mode='HTML')
        else:
            add_user_coins(chat_id, user_id, 10)
            await query.edit_message_text(f"⚖️ <b>PAREGGIO!</b> Punti: {player_score}\nPuntata di 10 $SDG restituita.", reply_markup=InlineKeyboardMarkup(end_keyboard), parse_mode='HTML')

# --- GAME: WORDLE EXPRESS ---
async def start_wordle_from_hub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    chat_id = query.message.chat_id
    game_key = f"{chat_id}_{user.id}"

    if get_user_coins(chat_id, user.id) < 10:
        await query.answer("❌ Servono 10 $SDG per giocare a Wordle!", show_alert=True)
        return

    add_user_coins(chat_id, user.id, -10)
    secret_word = random.choice(WORDS)

    WORDLE_GAMES[game_key] = {
        "player_id": user.id, "secret": secret_word,
        "attempts": 0, "history": []
    }

    await query.edit_message_text(
        "🔠 <b>WORDLE EXPRESS</b> (Puntata: 10 $SDG)\n"
        "─────────────────────────────\n\n"
        "Ho scelto una parola di <b>5 lettere</b>!\n"
        "Scrivila direttamente in chat per tentare (5 tentativi).",
        parse_mode="HTML"
    )

# --- GAME: QUIZ SHOW ---
async def start_quiz_calcio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat_id
    user_id = query.from_user.id

    if get_user_coins(chat_id, user_id) < 5:
        await query.answer("❌ Servono 5 $SDG per avviare il Quiz Calcio!", show_alert=True)
        return

    add_user_coins(chat_id, user_id, -5)
    item = random.choice(QUIZ_CALCIO_DB)
    
    QUIZ_GAMES[str(chat_id)] = {
        "type": "CALCIO", "target": item["target"],
        "indizi": item["indizi"], "step": 1
    }

    keyboard = [
        [InlineKeyboardButton("💡 Chiedi altro indizio (-$SDG)", callback_data="quiz_more_hint")],
        [InlineKeyboardButton("🔙 Torna all'HUB", callback_data="hub_main")]
    ]

    await query.edit_message_text(
        "⚽ <b>QUIZ CALCIO</b> (Costo: 5 $SDG)\n"
        "─────────────────────────────\n\n"
        "Indovina il calciatore scrivendo il cognome in chat!\n\n"
        f"<b>1° Indizio (Nazionalità):</b> {item['indizi'][0]}",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )

async def start_quiz_cinema(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat_id
    user_id = query.from_user.id

    if get_user_coins(chat_id, user_id) < 5:
        await query.answer("❌ Servono 5 $SDG per avviare il Quiz Cinema!", show_alert=True)
        return

    add_user_coins(chat_id, user_id, -5)
    item = random.choice(QUIZ_CINEMA_DB)

    QUIZ_GAMES[str(chat_id)] = {
        "type": "CINEMA", "target": item["target"],
        "indizi": item["indizi"], "step": 1
    }

    keyboard = [
        [InlineKeyboardButton("💡 Chiedi altro indizio (-$SDG)", callback_data="quiz_more_hint")],
        [InlineKeyboardButton("🔙 Torna all'HUB", callback_data="hub_main")]
    ]

    await query.edit_message_text(
        "🎬 <b>QUIZ CINEMA</b> (Costo: 5 $SDG)\n"
        "─────────────────────────────\n\n"
        "Indovina il film famoso scrivendolo in chat!\n\n"
        f"<b>1° Indizio:</b> {item['indizi'][0]}",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )

async def quiz_more_hint(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = str(query.message.chat_id)

    if chat_id not in QUIZ_GAMES:
        await query.answer("Nessun quiz attivo.", show_alert=True)
        return

    q = QUIZ_GAMES[chat_id]
    if q["step"] < len(q["indizi"]):
        q["step"] += 1
        hints_text = "\n".join([f"• <b>Indizio {i+1}:</b> {q['indizi'][i]}" for i in range(q["step"])])
        
        keyboard = []
        if q["step"] < len(q["indizi"]):
            keyboard.append([InlineKeyboardButton("💡 Chiedi altro indizio (-$SDG)", callback_data="quiz_more_hint")])
        keyboard.append([InlineKeyboardButton("🔙 Torna all'HUB", callback_data="hub_main")])

        title = "⚽ <b>QUIZ CALCIO</b>" if q["type"] == "CALCIO" else "🎬 <b>QUIZ CINEMA</b>"

        await query.edit_message_text(
            f"{title}\n─────────────────────────────\n\nScrivi la risposta in chat!\n\n{hints_text}",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )

# --- GAME: ROULETTE RUSSA 1v1 ---
async def start_roulette_prep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.edit_message_text(
        "🎯 <b>ROULETTE RUSSA 1v1</b>\n"
        "─────────────────────────────\n\n"
        "Scrivi semplicemente in chat il nome della tua vittima:\n\n"
        "👉 <code>sfido @username</code>",
        parse_mode="HTML"
    )

# --- GAME: BOMBA A PAROLA (TEST) ---
LETTERS = ["A", "B", "C", "F", "M", "P", "S", "T"]

async def start_bomba_prep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = str(query.message.chat_id)
    letter = random.choice(LETTERS)

    BOMBA_GAMES[chat_id] = {
        "active": True,
        "letter": letter,
        "holder_name": query.from_user.first_name,
        "holder_id": query.from_user.id
    }

    keyboard = [[InlineKeyboardButton("🔙 Torna all'HUB", callback_data="hub_main")]]

    await query.edit_message_text(
        f"💣 <b>BOMBA A PAROLA (TEST)</b>\n"
        f"─────────────────────────────\n\n"
        f"💣 La bomba è in mano a <b>{query.from_user.first_name}</b>!\n\n"
        f"👉 Per passare la bomba, scrivi in chat una parola che inizia con la lettera: <b>{letter}</b>!",
        reply_markup=InlineKeyboardMarkup(keyboard),
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
    BOMBA_GAMES.clear()
    await update.message.reply_text("🛠️ Tutti i giochi bloccati sono stati resettati.")

# --- HANDLER MESSAGGI GENERICI ---
async def handle_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.from_user: return
    
    user = update.message.from_user
    chat_id_int = update.message.chat_id
    chat_id = str(chat_id_int)
    text = (update.message.text or "").strip()

    # Handling Penitenze
    if user.id in PENITENZE_ATTIVE and PENITENZE_ATTIVE[user.id] > 0:
        if text.lower() != FRASE_PENITENZA:
            try:
                await update.message.delete()
                await context.bot.send_message(chat_id=chat_id, text=f"🚫 Devi scrivere esattamente: `{FRASE_PENITENZA}`", parse_mode="Markdown")
                return
            except Exception: pass
        else:
            del PENITENZE_ATTIVE[user.id]
            await update.message.reply_text(f"✅ {user.first_name} riabilitato!")
            return

    # Handling Sfida Roulette via testo
    if text.lower().startswith("sfido @"):
        target_username = text.split("@")[1].strip().lower()
        
        ACTIVE_DUELS[chat_id_int] = {
            "sfidante_id": user.id, "sfidante_name": user.first_name,
            "target_username": target_username, "chambers": [False]*6, "current_chamber": 0
        }
        ACTIVE_DUELS[chat_id_int]["chambers"][random.randint(0, 5)] = True

        keyboard = [[
            InlineKeyboardButton("🎯 Accetta Sfida", callback_data="roulette_accetta"),
            InlineKeyboardButton("🐔 Rifiuta", callback_data="roulette_rifiuta")
        ]]
        await update.message.reply_text(
            f"🔫 <b>ROULETTE RUSSA 1v1</b>\n\n"
            f"<b>{user.first_name}</b> ha sfidato <b>@{target_username}</b>!\n"
            f"@{target_username}, rispondi coi bottoni:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
        return

    # Handling Bomba a Parola
    if chat_id in BOMBA_GAMES and BOMBA_GAMES[chat_id].get("active"):
        bomba = BOMBA_GAMES[chat_id]
        if user.id == bomba["holder_id"] and text.upper().startswith(bomba["letter"]):
            new_letter = random.choice(LETTERS)
            bomba["holder_id"] = None
            bomba["letter"] = new_letter
            
            await update.message.reply_text(
                f"💣 <b>BOMBA PASSATA!</b>\n"
                f"<b>{user.first_name}</b> ha scritto <i>'{text}'</i> e si è salvato!\n\n"
                f"👉 Ora chiunque in chat può prendere la bomba scrivendo una parola con la lettera: <b>{new_letter}</b>!",
                parse_mode="HTML"
            )
            return

    text_upper = text.upper()

    # Handling Quiz
    if chat_id in QUIZ_GAMES:
        q = QUIZ_GAMES[chat_id]
        if text_upper == q["target"]:
            del QUIZ_GAMES[chat_id]
            steps_used = q["step"]
            reward = 20 if steps_used == 1 else (10 if steps_used == 2 else 6)
            add_user_coins(chat_id_int, user.id, reward)
            
            end_keyboard = [
                [InlineKeyboardButton("🧠 Altro Quiz", callback_data="hub_quiz")],
                [InlineKeyboardButton("🔙 Torna all'HUB", callback_data="hub_main")]
            ]
            await update.message.reply_text(
                f"🎉 <b>CORRETTO!</b> <b>{user.first_name}</b> ha indovinato <b>{q['target']}</b>!\nGuadagni <b>+{reward} $SDG</b>!",
                reply_markup=InlineKeyboardMarkup(end_keyboard),
                parse_mode="HTML"
            )
            return

    # Handling Wordle
    game_key = f"{chat_id}_{user.id}"
    if game_key in WORDLE_GAMES:
        game = WORDLE_GAMES[game_key]
        if len(text_upper) == 5:
            game["attempts"] += 1
            secret = game["secret"]
            
            letters_row = "  ".join(list(text_upper))
            colors_row = " ".join(["🟩" if text_upper[i] == secret[i] else "🟨" if text_upper[i] in secret else "⬛" for i in range(5)])
            
            game["history"].append(f"<code>{letters_row}</code>\n{colors_row}")
            res_text = "\n\n".join(game["history"])

            end_keyboard = [
                [InlineKeyboardButton("🔂 Rigioca (10 $SDG)", callback_data="start_wordle")],
                [InlineKeyboardButton("🔙 Torna all'HUB", callback_data="hub_main")]
            ]

            if text_upper == secret:
                del WORDLE_GAMES[game_key]
                add_user_coins(chat_id_int, user.id, 20)
                await update.message.reply_text(f"🎉 <b>ESATTO!</b> Parola: <b>{secret}</b>!\nVinti <b>+20 $SDG</b>!\n\n{res_text}", reply_markup=InlineKeyboardMarkup(end_keyboard), parse_mode="HTML")
            elif game["attempts"] >= 5:
                del WORDLE_GAMES[game_key]
                await update.message.reply_text(f"💥 <b>GAME OVER!</b> La parola era <b>{secret}</b>.\n\n{res_text}", reply_markup=InlineKeyboardMarkup(end_keyboard), parse_mode="HTML")
            else:
                await update.message.reply_text(f"🔠 <b>WORDLE EXPRESS ({game['attempts']}/5)</b>\n\n{res_text}", parse_mode="HTML")
            return

    # Auto-Troll
    if IS_TROLLING_ACTIVE and user.username and user.username.lower() in TARGET_MAP:
        if random.random() < 0.85:
            try: await context.bot.set_message_reaction(chat_id=chat_id, message_id=update.message.message_id, reaction=TARGET_MAP[user.username.lower()])
            except Exception: pass

# --- CALLBACK ROULETTE ---
async def gestione_bottoni_roulette(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
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

# --- MAIN ASYNC ---
async def main_async():
    if not TELEGRAM_TOKEN: return
    load_db()

    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()

    application = Application.builder().token(TELEGRAM_TOKEN).build()

    application.add_handler(CommandHandler("sdrogocomm", show_hub))
    application.add_handler(CommandHandler("topricconi", show_leaderboard))
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
    application.add_handler(CallbackQueryHandler(start_bomba_prep, pattern="^start_bomba_prep$"))
    application.add_handler(CallbackQueryHandler(start_roulette_prep, pattern="^start_roulette_prep$"))
    application.add_handler(CallbackQueryHandler(gestione_bottoni_roulette, pattern="^roulette_"))

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_messages))

    print("SdrogoBot v3.2 pronto all'uso!", flush=True)

    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)
    await asyncio.Event().wait()

if __name__ == '__main__':
    try: asyncio.run(main_async())
    except (KeyboardInterrupt, SystemExit): pass
