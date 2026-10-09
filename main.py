import os
import json
import random
import asyncio
import logging
from threading import Thread
from datetime import datetime, date, timedelta
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

# STATI GLOBALI GIOCHI E SHOP
ACTIVE_DUELS = {}
HIGHLOW_DUELS = {}
BLACKJACK_GAMES = {}
WORDLE_GAMES = {}
MASTERMIND_GAMES = {}
QUIZ_GAMES = {}
QUIZ_DUELS_1V1 = {}
GHIGLIOTTINA_DUELS = {}
HEIST_GAMES = {}
PENITENZE_ATTIVE = {}

# SHOP & PERSECUZIONI
ACTIVE_TITLES = {}      # {chat_id_userid: {"title": "🏳️‍🌈GAY🏳️‍🌈", "expire": datetime}}
ACTIVE_PERSECUTE = {}   # {chat_id_username: {"count": 15, "phrase": "frocio hah"}}
USER_INVENTORIES = {}  # {chat_id_userid: {"titles": 0, "persecutes": 0, "stars": 0}}

TARGET_MAP = {
    "ma1col7": "🤡"
}

IS_TROLLING_ACTIVE = False
FRASE_PENITENZA = "sono un perdente"

# --- FLASK KEEP ALIVE ---
app = Flask(__name__)

@app.route('/')
def home():
    return "SdrogoBot v5.2 Attivo H24!"

def run_flask():
    port = int(os.environ.get('PORT', 8080))
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    app.run(host='0.0.0.0', port=port)

# --- AUTO-RESTORE DATABASE DA MESSAGGIO FISSATO IN CHAT BACKUP ---
async def auto_restore_from_telegram(bot):
    global USER_DATA
    if not BACKUP_CHAT_ID: return
    try:
        chat_id = int(BACKUP_CHAT_ID)
        if not os.path.exists(DB_FILE):
            print("📦 Ricerca messaggio fissato per il ripristino...", flush=True)
            chat = await bot.get_chat(chat_id)
            if chat.pinned_message and chat.pinned_message.document:
                file_info = await bot.get_file(chat.pinned_message.document.file_id)
                await file_info.download_to_drive(DB_FILE)
                print("✅ Database ripristinato con successo dal messaggio fissato!", flush=True)
                load_db()
            else:
                print("⚠️ Nessun messaggio fissato con documento trovato nella chat di backup.", flush=True)
    except Exception as e:
        logging.error(f"Errore Auto-Restore da messaggio fissato: {e}")

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
                    caption=f"Backup DB SdrogoBot - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
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

async def verify_user_lock(query, owner_id: int) -> bool:
    if query.from_user.id != owner_id:
        await query.answer("🛑 Questo menu appartiene a un altro giocatore! Apri il tuo con /sdrogocomm.", show_alert=True)
        return False
    return True

def get_formatted_name(chat_id: int, user_id: int, default_name: str) -> str:
    key = f"{chat_id}_{user_id}"
    stars_str = ""
    
    if key in USER_INVENTORIES:
        stars = USER_INVENTORIES[key].get("stars", 0)
        if stars > 0:
            stars_str = " " + ("⭐" * min(stars, 5))

    if key in ACTIVE_TITLES:
        title_data = ACTIVE_TITLES[key]
        if datetime.now() < title_data["expire"]:
            return f"{title_data['title']} {default_name}{stars_str}"
        else:
            del ACTIVE_TITLES[key]
            
    return f"{default_name}{stars_str}"

# --- SDROGOBOT HUB (/sdrogocomm) ---
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

# --- SHOP ACTIONS ---
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
            await query.edit_message_text("🏢 <b>LA RAPINA È INIZIATA!</b> Controlla la tua chat PRIVATA con SdrogoBot per giocare!", parse_mode="HTML")
        except Exception:
            add_user_coins(chat_id, user_id, 350)
            await query.edit_message_text("❌ Devi prima avviare il bot in chat PRIVATA per giocare a Sdrogo Heist!", parse_mode="HTML")

# --- GAME: SDROGO HEIST ---
async def handle_heist_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split("_")
    stage = parts[1]
    owner_id = int(parts[2])

    if query.from_user.id != owner_id:
        return

    user_id = query.from_user.id
    if user_id not in HEIST_GAMES:
        await query.edit_message_text("❌ Sessione Rapina terminata.")
        return

    game = HEIST_GAMES[user_id]
    chat_id = game["chat_id"]

    if stage == "lvl1":
        keyboard = [
            [InlineKeyboardButton("🔴 Cavo Rosso", callback_data=f"heist_lvl1res_fail_{user_id}")],
            [InlineKeyboardButton("🔵 Cavo Blu (Corretto)", callback_data=f"heist_lvl1res_win_{user_id}")],
            [InlineKeyboardButton("🟡 Cavo Giallo", callback_data=f"heist_lvl1res_fail_{user_id}")]
        ]
        random.shuffle(keyboard)
        await query.edit_message_text("🔓 <b>LIVELLO 1: DISATTIVAZIONE ALLARME</b>\n\nQuale cavo tagli per disattivare l'allarme?", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif stage == "lvl1res":
        outcome = parts[2]
        if outcome == "fail":
            del HEIST_GAMES[user_id]
            await query.edit_message_text("💥 <b>ALLARME SCATTATO!</b> Le guardie ti hanno preso. Fuga fallita!")
        else:
            game["level"] = 2
            keyboard = [
                [InlineKeyboardButton("💰 CASHOUT (Prendi 💳 50 $SDG ed esci)", callback_data=f"heist_cashout_50_{user_id}")],
                [InlineKeyboardButton("🔥 RISCHIA IL LIVELLO 2 (Guardia)", callback_data=f"heist_lvl2_{user_id}")]
            ]
            await query.edit_message_text("✅ <b>LIVELLO 1 SUPERATO!</b>\nPremio accumulato: <code>💳 50 $SDG</code>.\n\nCosa vuoi fare?", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif stage == "lvl2":
        p_hand = random.randint(15, 21)
        g_hand = random.randint(14, 21)
        
        if p_hand >= g_hand:
            game["level"] = 3
            keyboard = [
                [InlineKeyboardButton("💰 CASHOUT (Prendi 💳 100 $SDG ed esci)", callback_data=f"heist_cashout_100_{user_id}")],
                [InlineKeyboardButton("🔥 RISCHIA IL LIVELLO 3 (Laser)", callback_data=f"heist_lvl3_{user_id}")]
            ]
            await query.edit_message_text(f"👮 <b>LIVELLO 2 SUPERATO!</b>\nHai messo KO la guardia ({p_hand} vs {g_hand})!\nPremio accumulato: <code>💳 100 $SDG</code>.", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        else:
            del HEIST_GAMES[user_id]
            await query.edit_message_text(f"👮 <b>LA GUARDIA TI HA VISTO!</b> ({g_hand} vs {p_hand})\nSei stato arrestato! Fuga fallita.")

    elif stage == "lvl3":
        keyboard = [
            [InlineKeyboardButton("🚪 Porta A", callback_data=f"heist_lvl3res_fail_{user_id}")],
            [InlineKeyboardButton("🚪 Porta B", callback_data=f"heist_lvl3res_win_{user_id}")],
            [InlineKeyboardButton("🚪 Porta C", callback_data=f"heist_lvl3res_fail_{user_id}")]
        ]
        random.shuffle(keyboard)
        await query.edit_message_text("⚡ <b>LIVELLO 3: CAMPO LASER</b>\n\nTre porte davanti a te. Solo una non ha i laser attivi!", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif stage == "lvl3res":
        outcome = parts[2]
        if outcome == "fail":
            del HEIST_GAMES[user_id]
            await query.edit_message_text("⚡ <b>COLPITO DAL LASER!</b> L'allarme è scattato. Fuga fallita!")
        else:
            game["level"] = 4
            keyboard = [
                [InlineKeyboardButton("💰 CASHOUT (Prendi 💳 180 $SDG ed esci)", callback_data=f"heist_cashout_180_{user_id}")],
                [InlineKeyboardButton("🔥 RISCHIA IL LIVELLO 4 (Cassaforte)", callback_data=f"heist_lvl4_{user_id}")]
            ]
            await query.edit_message_text("⚡ <b>LIVELLO 3 SUPERATO!</b>\nPremio accumulato: <code>💳 180 $SDG</code>.\n\nCosa vuoi fare?", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif stage == "lvl4":
        keyboard = [
            [InlineKeyboardButton("🔐 Codice 4-8-1 (Sbagliato)", callback_data=f"heist_lvl4res_fail_{user_id}")],
            [InlineKeyboardButton("🔐 Codice 7-7-7 (Sbagliato)", callback_data=f"heist_lvl4res_fail_{user_id}")],
            [InlineKeyboardButton("🔐 Codice 1-2-3 (Corretto)", callback_data=f"heist_lvl4res_win_{user_id}")]
        ]
        random.shuffle(keyboard)
        await query.edit_message_text("🔐 <b>LIVELLO 4: LA CASSAFORTE</b>\n\nTrova la combinazione corretta prima che scada il tempo!", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif stage == "lvl4res":
        outcome = parts[2]
        if outcome == "fail":
            del HEIST_GAMES[user_id]
            await query.edit_message_text("💥 <b>COMBINAZIONE ERRATA!</b> La cassaforte si è bloccata. Fuga fallita!")
        else:
            game["level"] = 5
            keyboard = [
                [InlineKeyboardButton("💰 CASHOUT (Prendi 💳 300 $SDG ed esci)", callback_data=f"heist_cashout_300_{user_id}")],
                [InlineKeyboardButton("🔥 SFIDA IL LIVELLO 5 FINALE!", callback_data=f"heist_lvl5_{user_id}")]
            ]
            await query.edit_message_text("🔐 <b>LIVELLO 4 SUPERATO!</b>\nPremio accumulato: <code>💳 300 $SDG</code>.\n\nSei ad un passo dalla gloria!", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif stage == "lvl5":
        keyboard = [
            [InlineKeyboardButton("🚁 Elicottero sul Tetto", callback_data=f"heist_lvl5res_win_{user_id}")],
            [InlineKeyboardButton("🚗 Fuga in Tunnel", callback_data=f"heist_lvl5res_fail_{user_id}")]
        ]
        await query.edit_message_text("🚁 <b>LIVELLO 5: LA FUGA FINALE</b>\n\nCome scappi col bottino?", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif stage == "lvl5res":
        outcome = parts[2]
        if outcome == "fail":
            del HEIST_GAMES[user_id]
            await query.edit_message_text("🚔 <b>LA POLIZIA TI HA CIRCONDATO IN TUNNEL!</b> Fuga fallita all'ultimo secondo!")
        else:
            del HEIST_GAMES[user_id]
            add_user_coins(chat_id, user_id, 600)
            
            inv_key = f"{chat_id}_{user_id}"
            if inv_key not in USER_INVENTORIES: USER_INVENTORIES[inv_key] = {"titles": 0, "persecutes": 0, "stars": 0}
            USER_INVENTORIES[inv_key]["stars"] = USER_INVENTORIES[inv_key].get("stars", 0) + 1
            USER_INVENTORIES[inv_key]["titles"] += 1
            USER_INVENTORIES[inv_key]["persecutes"] += 1
            
            await query.edit_message_text("🏆 <b>RAPINA PERFETTA COMPLETATA!</b>\nHai vinto +600 $SDG, 1 Titolo e 1 Persecuzione Gratis + 1 STELLA ⭐!")
            
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"👑 <b>COLPO DEL SECOLO!</b> 🏢\n\n<b>{query.from_user.first_name}</b> ha svaligiato il Caveau di Sdrogo Heist arrivando al 5° Livello!\nGuadagna <b>💳 600 $SDG</b> e 1 STELLA ⭐ di prestigio in classifica!",
                    parse_mode="HTML"
                )
            except Exception: pass

    elif stage == "cashout":
        amount = int(parts[2])
        del HEIST_GAMES[user_id]
        add_user_coins(chat_id, user_id, amount)
        await query.edit_message_text(f"💰 <b>CASHOUT EFFETTUATO!</b> Ti ritiri dalla rapina incassando <b>+💳 {amount} $SDG</b>!")

# --- COMANDI SHOP ---
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

# --- GAME: SLOT MACHINE 777 ---
async def start_slot_from_hub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split("_")
    owner_id = int(parts[2]) if len(parts) > 2 else query.from_user.id

    if not await verify_user_lock(query, owner_id): return
    user = query.from_user
    chat_id = query.message.chat_id

    if get_user_coins(chat_id, user.id) < 10:
        await query.answer("❌ Servono 10 $SDG per girare la Slot!", show_alert=True)
        return

    add_user_coins(chat_id, user.id, -10)
    symbols = ["🍒", "🍋", "🔔", "💎", "7️⃣"]
    r1, r2, r3 = random.choice(symbols), random.choice(symbols), random.choice(symbols)

    await query.edit_message_text(
        f"🎰 <b>SLOT MACHINE 777</b> 🎰\n👤 Player: <b>{user.first_name}</b>\n\n"
        f"[ {r1} | 🔄 | ❓ ]\n\n<i>Giro rulli in corso...</i>",
        parse_mode="HTML"
    )
    await asyncio.sleep(0.6)

    await query.edit_message_text(
        f"🎰 <b>SLOT MACHINE 777</b> 🎰\n👤 Player: <b>{user.first_name}</b>\n\n"
        f"[ {r1} | {r2} | 🔄 ]\n\n<i>Giro rulli in corso...</i>",
        parse_mode="HTML"
    )
    await asyncio.sleep(0.6)

    text = f"🎰 <b>SLOT MACHINE 777</b> 🎰\n👤 Player: <b>{user.first_name}</b>\n\n[ {r1} | {r2} | {r3} ]\n\n"

    end_keyboard = [
        [InlineKeyboardButton("🔂 Rigioca (10 $SDG)", callback_data=f"start_slot_{owner_id}")],
        [InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{owner_id}")]
    ]

    if r1 == r2 == r3:
        if r1 == "7️⃣":
            add_user_coins(chat_id, user.id, 150)
            text += "🔥 <b>JACKPOT SUPREMO 777!</b> 🔥 Hai vinto <b>+💳 150 $SDG</b>!"
        else:
            add_user_coins(chat_id, user.id, 30)
            text += "🎉 <b>TRIPLETTA VINCENTE!</b> Hai vinto <b>+💳 30 $SDG</b>!"
    elif r1 == r2 or r2 == r3 or r1 == r3:
        add_user_coins(chat_id, user.id, 10)
        text += "✨ <b>DOPPIETTA!</b> Recuperi i tuoi 10 $SDG!"
    else:
        text += "💸 <b>NESSUNA COMBINAZIONE!</b> Hai perso 10 $SDG."

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(end_keyboard), parse_mode="HTML")

# --- GAME: MASTERMIND EXPRESS ---
async def start_mastermind_from_hub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split("_")
    owner_id = int(parts[2]) if len(parts) > 2 else query.from_user.id

    if not await verify_user_lock(query, owner_id): return
    user = query.from_user
    chat_id = query.message.chat_id
    game_key = f"{chat_id}_{user.id}"

    if get_user_coins(chat_id, user.id) < 10:
        await query.answer("❌ Servono 10 $SDG per giocare a Mastermind!", show_alert=True)
        return

    add_user_coins(chat_id, user.id, -10)
    digits = list("0123456789")
    random.shuffle(digits)
    secret_code = "".join(digits[:3])

    MASTERMIND_GAMES[game_key] = {
        "player_id": user.id, "secret": secret_code,
        "attempts": 0, "history": []
    }

    await query.edit_message_text(
        "🔐 <b>MASTERMIND EXPRESS</b> (Puntata: 10 $SDG)\n\n"
        "Ho scelto un codice segreto di <b>3 cifre uniche</b>!\n"
        "Scrivilo direttamente in chat per tentare (5 tentativi).",
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
        asyncio.create_task(run_ghigliottina_timeout(context.bot, chat_id, msg.message_id, item["target"]))

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
    db_map = {
        "CALCIO": QUIZ_CALCIO_DB, "CINEMA": QUIZ_CINEMA_DB,
        "SERIE": QUIZ_SERIE_DB, "F1": QUIZ_FORMULA1_DB,
        "MARVEL": QUIZ_MARVEL_DB, "PAESI": QUIZ_PAESI_DB,
        "ANIME": QUIZ_ANIME_DB, "BRANDS": QUIZ_BRANDS_DB,
        "PERSONAGGI": QUIZ_PERSONAGGI_DB, "CANZONI": QUIZ_CANZONI_DB
    }
    
    selected_db = db_map.get(cat, QUIZ_CALCIO_DB)
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

# --- GAME: QUIZ MULTIPLAYER PER CATEGORIA ---
async def start_quiz_multiplayer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split("_")
    cat_choice = parts[2] if len(parts) > 2 else "ALL"
    owner_id = int(parts[3]) if len(parts) > 3 else query.from_user.id

    if not await verify_user_lock(query, owner_id): return
    chat_id = str(query.message.chat_id)

    db_map = [
        (QUIZ_CALCIO_DB, "⚽ CALCIO", "CALCIO"),
        (QUIZ_CINEMA_DB, "🎬 CINEMA", "CINEMA"),
        (QUIZ_SERIE_DB, "📺 SERIE TV", "SERIE"),
        (QUIZ_FORMULA1_DB, "🏎️ FORMULA 1", "F1"),
        (QUIZ_MARVEL_DB, "🦸 MARVEL & DC", "MARVEL"),
        (QUIZ_PAESI_DB, "🗺️ PAESI DEL MONDO", "PAESI"),
        (QUIZ_ANIME_DB, "🏮 ANIME & MANGA", "ANIME"),
        (QUIZ_BRANDS_DB, "🏷️ BRAND & MARCHI", "BRANDS"),
        (QUIZ_PERSONAGGI_DB, "📜 PERSONAGGI STORICI", "PERSONAGGI"),
        (QUIZ_CANZONI_DB, "🎵 CANZONI", "CANZONI")
    ]
    
    if cat_choice == "ALL":
        selected_db, cat_name, _ = random.choice(db_map)
    else:
        filtered = [item for item in db_map if item[2] == cat_choice]
        selected_db, cat_name, _ = filtered[0] if filtered else db_map[0]

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

# --- CLASSIFICA RICCONI ---
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

# --- DAILY ---
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

# --- GAME: BLACKJACK ---
async def start_bj_from_hub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split("_")
    owner_id = int(parts[2]) if len(parts) > 2 else query.from_user.id

    if not await verify_user_lock(query, owner_id): return
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
        InlineKeyboardButton("🎴 Carta", callback_data=f"bj_hit_{owner_id}"),
        InlineKeyboardButton("✋ Stai", callback_data=f"bj_stand_{owner_id}")
    ]]

    await query.edit_message_text(
        f"🃏 <b>BLACKJACK 21</b> (Puntata: 10 $SDG)\n\n"
        f"👤 Giocatore: <b>{user.first_name}</b>\n"
        f"🎎 Carte: {player_hand} (Totale: <b>{sum(player_hand)}</b>)\n"
        f"🤖 Banco: [{dealer_hand[0]}, ?]\n\nCosa fai?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML'
    )

async def handle_bj_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split("_")
    action = parts[1]
    owner_id = int(parts[2]) if len(parts) > 2 else query.from_user.id

    if not await verify_user_lock(query, owner_id): return
    chat_id = query.message.chat_id
    user_id = query.from_user.id
    game_key = f"{chat_id}_{user_id}"

    if game_key not in BLACKJACK_GAMES:
        await query.edit_message_text("❌ Partita terminata.")
        return

    game = BLACKJACK_GAMES[game_key]
    cards = [2, 3, 4, 5, 6, 7, 8, 9, 10, 10, 10, 10, 11]
    end_keyboard = [
        [InlineKeyboardButton("🔂 Rigioca (10 $SDG)", callback_data=f"start_bj_{owner_id}")],
        [InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{owner_id}")]
    ]

    if action == "hit":
        game["player_hand"].append(random.choice(cards))
        score = sum(game["player_hand"])

        if score > 21:
            del BLACKJACK_GAMES[game_key]
            await query.edit_message_text(f"💥 <b>SBALLATO!</b> ({score})\nHai perso 10 $SDG!", reply_markup=InlineKeyboardMarkup(end_keyboard), parse_mode='HTML')
        else:
            keyboard = [[InlineKeyboardButton("🎴 Carta", callback_data=f"bj_hit_{owner_id}"), InlineKeyboardButton("✋ Stai", callback_data=f"bj_stand_{owner_id}")]]
            await query.edit_message_text(f"🃏 <b>BLACKJACK 21</b>\n\nCarte: {game['player_hand']} ({score})\nBanco: [{game['dealer_hand'][0]}, ?]", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

    elif action == "stand":
        player_score = sum(game["player_hand"])
        dealer_hand = game["dealer_hand"]
        while sum(dealer_hand) < 17: dealer_hand.append(random.choice(cards))
        dealer_score = sum(dealer_hand)
        del BLACKJACK_GAMES[game_key]

        if dealer_score > 21 or player_score > dealer_score:
            add_user_coins(chat_id, user_id, 15)
            await query.edit_message_text(f"🏆 <b>VITTORIA!</b> Tu: {player_score} | Banco: {dealer_score}\nHai vinto <b>+💳 15 $SDG</b>!", reply_markup=InlineKeyboardMarkup(end_keyboard), parse_mode='HTML')
        elif player_score < dealer_score:
            await query.edit_message_text(f"❌ <b>SCONFITTA!</b> Tu: {player_score} | Banco: {dealer_score}\nHai perso la puntata.", reply_markup=InlineKeyboardMarkup(end_keyboard), parse_mode='HTML')
        else:
            add_user_coins(chat_id, user_id, 10)
            await query.edit_message_text(f"⚖️ <b>PAREGGIO!</b> Punti: {player_score}\nPuntata di 10 $SDG restituita.", reply_markup=InlineKeyboardMarkup(end_keyboard), parse_mode='HTML')

# --- GAME: WORDLE EXPRESS ---
async def start_wordle_from_hub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split("_")
    owner_id = int(parts[2]) if len(parts) > 2 else query.from_user.id

    if not await verify_user_lock(query, owner_id): return
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
        "🔠 <b>WORDLE EXPRESS</b> (Puntata: 10 $SDG)\n\n"
        "Ho scelto una parola di <b>5 lettere</b>!\n"
        "Scrivila direttamente in chat per tentare (5 tentativi).",
        parse_mode="HTML"
    )

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
        "Indovina la risposta skrivendola in chat!\n\n"
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
    HIGHLOW_DUELS.clear()
    BLACKJACK_GAMES.clear()
    WORDLE_GAMES.clear()
    MASTERMIND_GAMES.clear()
    QUIZ_GAMES.clear()
    QUIZ_DUELS_1V1.clear()
    GHIGLIOTTINA_DUELS.clear()
    HEIST_GAMES.clear()
    await update.message.reply_text("🛠️ Tutti i giochi bloccati sono stati resettati.")

# --- HANDLER MESSAGGI GENERICI ---
async def handle_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.from_user: return
    
    user = update.message.from_user
    chat_id_int = update.message.chat_id
    chat_id = str(chat_id_int)
    text = (update.message.text or "").strip()
    username_lower = user.username.lower() if user.username else ""

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

    # Handling Tag Persecutore dallo Shop ("frocio hah")
    persecute_key = f"{chat_id}_{username_lower}"
    if persecute_key in ACTIVE_PERSECUTE:
        p_data = ACTIVE_PERSECUTE[persecute_key]
        if p_data["count"] > 0:
            p_data["count"] -= 1
            await update.message.reply_text(p_data["phrase"])
            if p_data["count"] == 0:
                del ACTIVE_PERSECUTE[persecute_key]

    # Handling Comandi Titolo e Perseguita
    if text.lower().startswith("titolo") or text.lower().startswith("/titolo"):
        await apply_title_command(update, context)
        return
    elif text.lower().startswith("perseguita") or text.lower().startswith("/perseguita"):
        await apply_persecute_command(update, context)
        return

    # Handling Sfida Ghigliottina 1v1 ("sfidoghigliottina @username")
    if text.lower().startswith("sfidoghigliottina") or text.lower().startswith("/sfidoghigliottina"):
        parts = text.split()
        target_username = None
        for p in parts:
            if p.startswith("@"): target_username = p.replace("@", "").lower(); break
        
        if not target_username:
            await update.message.reply_text("❌ Uso: <code>sfidoghigliottina @username</code>", parse_mode="HTML")
            return

        GHIGLIOTTINA_DUELS[chat_id_int] = {
            "sfidante_id": user.id, "sfidante_name": user.first_name,
            "target_username": target_username, "active": False
        }

        keyboard = [[
            InlineKeyboardButton("🪓 Accetta Ghigliottina", callback_data="ghig_accetta"),
            InlineKeyboardButton("🐔 Rifiuta", callback_data="ghig_rifiuta")
        ]]
        await update.message.reply_text(
            f"🪓 <b>GHIGLIOTTINA EXPRESS 1v1</b>\n\n"
            f"<b>{user.first_name}</b> ha sfidato <b>@{target_username}</b> alla Ghigliottina!\n"
            f"@{target_username}, rispondi coi bottoni:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
        return

    # Handling Sfida Duello Quiz 1v1 ("sfidoquiz @username")
    if text.lower().startswith("sfidoquiz") or text.lower().startswith("/sfidoquiz"):
        parts = text.split()
        target_username = None
        for p in parts:
            if p.startswith("@"): target_username = p.replace("@", "").lower(); break
        
        if not target_username:
            await update.message.reply_text("❌ Uso: <code>sfidoquiz @username</code>", parse_mode="HTML")
            return

        QUIZ_DUELS_1V1[chat_id_int] = {
            "sfidante_id": user.id, "sfidante_name": user.first_name,
            "target_username": target_username, "active": False
        }

        keyboard = [
            [InlineKeyboardButton("⚽ Calcio", callback_data="q1v1_cat_CALCIO"), InlineKeyboardButton("🏎️ F1", callback_data="q1v1_cat_F1")],
            [InlineKeyboardButton("🦸 Marvel", callback_data="q1v1_cat_MARVEL"), InlineKeyboardButton("🎬 Cinema", callback_data="q1v1_cat_CINEMA")],
            [InlineKeyboardButton("📺 Serie TV", callback_data="q1v1_cat_SERIE"), InlineKeyboardButton("🗺️ Paesi", callback_data="q1v1_cat_PAESI")],
            [InlineKeyboardButton("🏮 Anime", callback_data="q1v1_cat_ANIME"), InlineKeyboardButton("🏷️ Brand", callback_data="q1v1_cat_BRANDS")],
            [InlineKeyboardButton("📜 Personaggi", callback_data="q1v1_cat_PERSONAGGI"), InlineKeyboardButton("🎵 Canzoni", callback_data="q1v1_cat_CANZONI")]
        ]
        await update.message.reply_text(
            f"⚔️ <b>DUELLO QUIZ 1v1 (A 5 ROUND)</b>\n\n"
            f"<b>{user.first_name}</b> vuole sfidare <b>@{target_username}</b>!\n"
            f"<b>{user.first_name}</b>, seleziona prima la categoria del duello:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
        return

    # Handling Sfida Roulette Russa ("sfido @username")
    if text.lower().startswith("sfido @") or text.lower().startswith("/sfido @"):
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

    # Handling Sfida High/Low 1v1 ("sfido highlow @username")
    if text.lower().startswith("sfido highlow @") or text.lower().startswith("/sfido highlow @"):
        target_username = text.split("@")[1].strip().lower()
        HIGHLOW_DUELS[chat_id_int] = {
            "sfidante_id": user.id, "sfidante_name": user.first_name,
            "target_username": target_username, "val": 0, "turno_id": None
        }

        keyboard = [[
            InlineKeyboardButton("🎲 Accetta High/Low", callback_data="hl_accetta"),
            InlineKeyboardButton("🐔 Rifiuta", callback_data="hl_rifiuta")
        ]]
        await update.message.reply_text(
            f"🎲 <b>HIGH / LOW 1v1 (DADO DELLA MORTE)</b>\n\n"
            f"<b>{user.first_name}</b> ha sfidato <b>@{target_username}</b> a duello sul dado!\n"
            f"@{target_username}, accetti la sfida?",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
        return

    text_upper = text.upper()

    # Handling Risposta Ghigliottina 1v1
    if chat_id_int in GHIGLIOTTINA_DUELS and GHIGLIOTTINA_DUELS[chat_id_int].get("active"):
        duel = GHIGLIOTTINA_DUELS[chat_id_int]
        if user.id in [duel["sfidante_id"], duel["target_id"]]:
            if text_upper == duel["word"]:
                del GHIGLIOTTINA_DUELS[chat_id_int]
                add_user_coins(chat_id_int, user.id, 40)
                await update.message.reply_text(
                    f"🏆 <b>GHIGLIOTTINA RISOLTA!</b>\n\n"
                    f"✨ <b>{user.first_name}</b> ha indovinato la parola legame <b>{duel['word']}</b>!\n"
                    f"Vince il duello e guadagna <b>+💳 40 $SDG</b>!",
                    parse_mode="HTML"
                )
                return

    # Handling Risposta Duello Quiz 1v1
    if chat_id_int in QUIZ_DUELS_1V1 and QUIZ_DUELS_1V1[chat_id_int].get("active"):
        duel = QUIZ_DUELS_1V1[chat_id_int]
        if user.id in [duel["sfidante_id"], duel["target_id"]]:
            if text_upper == duel["current_target"]:
                if user.id == duel["sfidante_id"]:
                    duel["p1_score"] += 1
                else:
                    duel["p2_score"] += 1

                await update.message.reply_text(
                    f"🎯 <b>RISPOSTA CORRETTA!</b> <b>{user.first_name}</b> indovina <b>{duel['current_target']}</b> e prende il punto del Round {duel['round']}!",
                    parse_mode="HTML"
                )

                if duel["round"] < 5 and duel["p1_score"] < 3 and duel["p2_score"] < 3:
                    duel["round"] += 1
                    await launch_quiz1v1_round(context.bot, chat_id_int)
                else:
                    await conclude_quiz1v1_duel(context.bot, chat_id_int)
                return

    # Handling Mastermind Express
    mm_key = f"{chat_id}_{user.id}"
    if mm_key in MASTERMIND_GAMES:
        mm = MASTERMIND_GAMES[mm_key]
        if len(text) == 3 and text.isdigit():
            mm["attempts"] += 1
            secret = mm["secret"]
            
            c_hits = sum(1 for i in range(3) if text[i] == secret[i])
            p_hits = sum(1 for i in range(3) if text[i] != secret[i] and text[i] in secret)
            
            res_str = f"🎯 {c_hits} Centrati | 🔄 {p_hits} Presenti | ❌ {3 - (c_hits + p_hits)} Assenti"
            mm["history"].append(f"<code>{text}</code> -> {res_str}")
            res_text = "\n".join(mm["history"])

            end_keyboard = [
                [InlineKeyboardButton("🔂 Rigioca (10 $SDG)", callback_data=f"start_mm_{user.id}")],
                [InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{user.id}")]
            ]

            if c_hits == 3:
                del MASTERMIND_GAMES[mm_key]
                reward = 30 if mm["attempts"] <= 3 else 15
                add_user_coins(chat_id_int, user.id, reward)
                await update.message.reply_text(f"🎉 <b>ESATTO!</b> Codice segreto: <b>{secret}</b>!\nVinti <b>+💳 {reward} $SDG</b>!\n\n{res_text}", reply_markup=InlineKeyboardMarkup(end_keyboard), parse_mode="HTML")
            elif mm["attempts"] >= 5:
                del MASTERMIND_GAMES[mm_key]
                await update.message.reply_text(f"💥 <b>GAME OVER!</b> Il codice era <b>{secret}</b>.\n\n{res_text}", reply_markup=InlineKeyboardMarkup(end_keyboard), parse_mode="HTML")
            else:
                await update.message.reply_text(f"🔐 <b>MASTERMIND ({mm['attempts']}/5)</b>\n\n{res_text}", parse_mode="HTML")
            return

    # Handling Quiz Multiplayer
    if chat_id in QUIZ_GAMES and QUIZ_GAMES[chat_id].get("multi"):
        q = QUIZ_GAMES[chat_id]
        if text_upper == q["target"]:
            del QUIZ_GAMES[chat_id]
            add_user_coins(chat_id_int, user.id, 15)
            end_keyboard = [
                [InlineKeyboardButton("🌐 Altro Quiz Multiplayer", callback_data=f"hub_qmulti_{user.id}")],
                [InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{user.id}")]
            ]
            await update.message.reply_text(
                f"🎉 <b>QUIZ MULTIPLAYER RISOLTO!</b>\n\n"
                f"🏆 <b>{user.first_name}</b> è stato il più veloce ed ha indovinato <b>{q['target']}</b>!\n"
                f"Guadagni <b>+💳 15 $SDG</b>!",
                reply_markup=InlineKeyboardMarkup(end_keyboard),
                parse_mode="HTML"
            )
            return

    # Handling Quiz Single Player
    quiz_key = f"{chat_id}_{user.id}"
    if quiz_key in QUIZ_GAMES:
        q = QUIZ_GAMES[quiz_key]
        if text_upper == q["target"]:
            del QUIZ_GAMES[quiz_key]
            steps_used = q["step"]
            reward = 20 if steps_used == 1 else (10 if steps_used == 2 else 6)
            add_user_coins(chat_id_int, user.id, reward)
            
            end_keyboard = [
                [InlineKeyboardButton("🧠 Altro Quiz", callback_data=f"hub_quiz_{user.id}")],
                [InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{user.id}")]
            ]
            await update.message.reply_text(
                f"🎉 <b>CORRETTO!</b> <b>{user.first_name}</b> ha indovinato <b>{q['target']}</b>!\nGuadagni <b>+💳 {reward} $SDG</b>!",
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
            
            secret_letters = list(secret)
            res_colors = ["⬛"] * 5
            
            for i in range(5):
                if text_upper[i] == secret[i]:
                    res_colors[i] = "🟩"
                    secret_letters[i] = None
                    
            for i in range(5):
                if res_colors[i] != "🟩" and text_upper[i] in secret_letters:
                    res_colors[i] = "🟨"
                    secret_letters[secret_letters.index(text_upper[i])] = None

            letters_row = "  ".join(list(text_upper))
            colors_row = " ".join(res_colors)
            
            game["history"].append(f"<code>{letters_row}</code>\n{colors_row}")
            res_text = "\n\n".join(game["history"])

            end_keyboard = [
                [InlineKeyboardButton("🔂 Rigioca (10 $SDG)", callback_data=f"start_wordle_{user.id}")],
                [InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{user.id}")]
            ]

            if text_upper == secret:
                del WORDLE_GAMES[game_key]
                add_user_coins(chat_id_int, user.id, 20)
                await update.message.reply_text(f"🎉 <b>ESATTO!</b> Parola: <b>{secret}</b>!\nVinti <b>+💳 20 $SDG</b>!\n\n{res_text}", reply_markup=InlineKeyboardMarkup(end_keyboard), parse_mode="HTML")
            elif game["attempts"] >= 5:
                del WORDLE_GAMES[game_key]
                await update.message.reply_text(f"💥 <b>GAME OVER!</b> La parola era <b>{secret}</b>.\n\n{res_text}", reply_markup=InlineKeyboardMarkup(end_keyboard), parse_mode="HTML")
            else:
                await update.message.reply_text(f"🔠 <b>WORDLE EXPRESS ({game['attempts']}/5)</b>\n\n{res_text}", parse_mode="HTML")
            return

    # Auto-Troll
    if IS_TROLLING_ACTIVE and username_lower in TARGET_MAP:
        if random.random() < 0.85:
            try: await context.bot.set_message_reaction(chat_id=chat_id, message_id=update.message.message_id, reaction=TARGET_MAP[username_lower])
            except Exception: pass

# --- CALLBACK ROULETTE ---
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

# --- SISTEMA DI DEBUG ED ERRORI ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logging.error(msg="Eccezione non gestita durante l'update:", exc_info=context.error)
    print(f"🚨 ERRORE FATALE CRASH: {context.error}", flush=True)

async def catch_all_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print(f"🔥 RICEVUTO UN CLICK! Pulsante: {update.callback_query.data} da utente {update.effective_user.first_name}", flush=True)

# --- MAIN ASYNC ---
async def main_async():
    if not TELEGRAM_TOKEN: return
    load_db()

    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()

    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # Registra il gestore degli errori per vedere i crash silenziosi
    application.add_error_handler(error_handler)

    await auto_restore_from_telegram(application.bot)

    if application.job_queue:
        application.job_queue.run_repeating(quiz_timeout_check, interval=60)

    # Registra il logger dei pulsanti nel gruppo -1 (si attiva prima di tutto senza bloccare l'app)
    application.add_handler(CallbackQueryHandler(catch_all_callbacks), group=-1)

    # Registrazione Comandi
    application.add_handler(CommandHandler("sdrogocomm", show_hub))
    application.add_handler(CommandHandler("topricconi", show_leaderboard))
    application.add_handler(CommandHandler("troll", toggle_troll))
    application.add_handler(CommandHandler("pen", clear_penalties))
    application.add_handler(CommandHandler("resetduello", reset_duello))

    for cmd in ["roulette", "blackjack", "slot", "highlow", "wordle", "quiz", "shop", "heist"]:
        application.add_handler(CommandHandler(cmd, block_direct_command))

    # Callbacks HUB, Shop & Games
    application.add_handler(CallbackQueryHandler(hub_callback, pattern="^hub_"))
    application.add_handler(CallbackQueryHandler(shop_buy_callback, pattern="^buy_"))
    application.add_handler(CallbackQueryHandler(handle_heist_callback, pattern="^heist_"))
    application.add_handler(CallbackQueryHandler(claim_daily_callback, pattern="^claim_daily_"))
    application.add_handler(CallbackQueryHandler(start_bj_from_hub, pattern="^start_bj_"))
    application.add_handler(CallbackQueryHandler(handle_bj_callback, pattern="^bj_"))
    application.add_handler(CallbackQueryHandler(start_slot_from_hub, pattern="^start_slot_"))
    application.add_handler(CallbackQueryHandler(start_wordle_from_hub, pattern="^start_wordle_"))
    application.add_handler(CallbackQueryHandler(start_mastermind_from_hub, pattern="^start_mm_"))
    
    # Category Quizzes Single
    application.add_handler(CallbackQueryHandler(lambda u, c: start_quiz_generic(u, c, QUIZ_CALCIO_DB, "CALCIO"), pattern="^start_qcalcio_"))
    application.add_handler(CallbackQueryHandler(lambda u, c: start_quiz_generic(u, c, QUIZ_CINEMA_DB, "CINEMA"), pattern="^start_qcinema_"))
    application.add_handler(CallbackQueryHandler(lambda u, c: start_quiz_generic(u, c, QUIZ_SERIE_DB, "SERIE TV"), pattern="^start_qserie_"))
    application.add_handler(CallbackQueryHandler(lambda u, c: start_quiz_generic(u, c, QUIZ_FORMULA1_DB, "FORMULA 1"), pattern="^start_qf1_"))
    application.add_handler(CallbackQueryHandler(lambda u, c: start_quiz_generic(u, c, QUIZ_MARVEL_DB, "MARVEL & DC"), pattern="^start_qmarvel_"))
    application.add_handler(CallbackQueryHandler(lambda u, c: start_quiz_generic(u, c, QUIZ_PAESI_DB, "PAESI DEL MONDO"), pattern="^start_qpaesi_"))
    application.add_handler(CallbackQueryHandler(lambda u, c: start_quiz_generic(u, c, QUIZ_ANIME_DB, "ANIME & MANGA"), pattern="^start_qanime_"))
    application.add_handler(CallbackQueryHandler(lambda u, c: start_quiz_generic(u, c, QUIZ_BRANDS_DB, "BRAND & MARCHI"), pattern="^start_qbrands_"))
    application.add_handler(CallbackQueryHandler(lambda u, c: start_quiz_generic(u, c, QUIZ_PERSONAGGI_DB, "PERSONAGGI STORICI"), pattern="^start_qpersonaggi_"))
    application.add_handler(CallbackQueryHandler(lambda u, c: start_quiz_generic(u, c, QUIZ_CANZONI_DB, "CANZONI"), pattern="^start_qcanzoni_"))
    
    # Callbacks Multi & Duelli
    application.add_handler(CallbackQueryHandler(start_quiz_multiplayer, pattern="^start_qmulti_"))
    application.add_handler(CallbackQueryHandler(quiz_more_hint, pattern="^quiz_hint_"))
    application.add_handler(CallbackQueryHandler(start_roulette_prep, pattern="^start_roulette_"))
    application.add_handler(CallbackQueryHandler(start_highlow_prep, pattern="^start_highlow_"))
    application.add_handler(CallbackQueryHandler(start_ghigliottina_prep, pattern="^start_ghigliottina_prep_"))
    application.add_handler(CallbackQueryHandler(start_quiz1v1_prep, pattern="^start_quiz1v1_prep_"))
    
    application.add_handler(CallbackQueryHandler(gestione_bottoni_roulette, pattern="^roulette_"))
    application.add_handler(CallbackQueryHandler(handle_highlow_callback, pattern="^hl_"))
    application.add_handler(CallbackQueryHandler(handle_ghigliottina_callback, pattern="^ghig_"))
    application.add_handler(CallbackQueryHandler(handle_quiz1v1_callback, pattern="^q1v1_"))

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_messages))

    print("SdrogoBot v5.2 pronto all'uso!", flush=True)

    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)
    await asyncio.Event().wait()

if __name__ == '__main__':
    try: asyncio.run(main_async())
    except (KeyboardInterrupt, SystemExit): pass
