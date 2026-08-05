import os
import random
import asyncio
import logging
from threading import Thread
from datetime import datetime, timedelta
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

# --- MAPPA DELLE VITTIME ---
TARGET_MAP = {
    "manueiii": "🙉",
    "spoleto17": "🤡",
    "artemesio": "💩",
    "marco_palestra": "🖕",
    "albe960": "🥱",
    "alessioaynonnt": "🐳"
}

# --- VARIABILI D'AMBIENTE E STATI GLOBALI ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ADMIN_ID = os.environ.get("ADMIN_ID")            # Tuo ID Telegram personale
GROUP_CHAT_ID = os.environ.get("GROUP_CHAT_ID")    # ID del Gruppo Principale

IS_TROLLING_ACTIVE = True
FRASE_PENITENZA = "sono un perdente"

ACTIVE_DUELS = {}
PENITENZE_ATTIVE = {}
BLACKJACK_GAMES = {}
HIGHLOW_GAMES = {}

# --- KEEP ALIVE SERVER (Flask) ---
app = Flask(__name__)

@app.route('/')
def home():
    return "SdrogoBot attivo H24!"

def run_flask():
    port = int(os.environ.get('PORT', 8080))
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    app.run(host='0.0.0.0', port=port)

# --- HELPER PERMETTI ADMIN ---
def is_admin(user_id: int) -> bool:
    if not ADMIN_ID:
        return False
    return str(user_id) == str(ADMIN_ID)

# --- TRACCIAMENTO ID NEI LOG ---
async def track_ids(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.from_user:
        return
    user_id = str(update.message.from_user.id)
    chat_id = str(update.effective_chat.id)
    chat_type = update.effective_chat.type
    print(f"--- [SDROGOBOT LOG] User ID: {user_id} | Chat ID: {chat_id} ({chat_type}) ---", flush=True)

# --- COMANDO /SDROGOCOMM ---
async def show_commands(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await track_ids(update, context)
    user_id = update.effective_user.id
    chat_type = update.effective_chat.type

    if chat_type == "private" and is_admin(user_id):
        text = (
            "👑 <b>PANNELLO ADMIN SDROGOBOT (RISERVATO)</b> 🛠️\n\n"
            "<b>Comandi Amministrativi (funzionano qui in privato):</b>\n"
            "😈 <b>/troll</b> - Attiva/Disattiva la modalità Auto-Troll nel gruppo.\n"
            "🧹 <b>/pen</b> - Rimuove tutte le penitenze attive nel gruppo.\n\n"
            "<b>Comandi Pubblici del Gruppo:</b>\n"
            "🎯 <b>/roulette @username</b> - Sfida un utente alla Roulette Russa 1v1 (con penitenza!).\n"
            "🃏 <b>/blackjack</b> - Gioca a Blackjack 21 contro il bot.\n"
            "🎲 <b>/highlow</b> - Gioca al Dado Bugiardo (Più o Meno).\n"
            "🎰 <b>/slot</b> - Tira la levetta della Slot Machine 777.\n"
            "🔄 <b>/resetduello</b> - Sblocca duelli o giochi incastrati.\n"
            "ℹ️ <b>/sdrogocomm</b> - Mostra questo pannello."
        )
    else:
        text = (
            "🤖 <b>COMANDI DISPONIBILI - SDROGOBOT</b> 🎮\n\n"
            "🎯 <b>/roulette @username</b> - Sfida un utente alla Roulette Russa 1v1!\n"
            "🃏 <b>/blackjack</b> - Avvia una partita a Blackjack 21!\n"
            "🎲 <b>/highlow</b> - Sfida la sorte al Dado Bugiardo!\n"
            "🎰 <b>/slot</b> - Gioca alla Slot Machine 777!\n"
            "🔄 <b>/resetduello</b> - Ripristina i duelli bloccati da bug/errori.\n"
            "ℹ️ <b>/sdrogocomm</b> - Mostra questa lista comandi."
        )
    
    await update.message.reply_text(text, parse_mode='HTML')

# --- COMANDO TROLL ---
async def toggle_troll(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await track_ids(update, context)
    global IS_TROLLING_ACTIVE

    user_id = update.effective_user.id
    if update.effective_chat.type == "private" and not is_admin(user_id):
        await update.message.reply_text("❌ Non hai i permessi per configurare il bot in privato!")
        return

    IS_TROLLING_ACTIVE = not IS_TROLLING_ACTIVE
    stato = "ATTIVATA 🙉🤡💩🖕🥱🐳" if IS_TROLLING_ACTIVE else "DISATTIVATA 🛑"

    if update.effective_chat.type == "private":
        await update.message.reply_text(f"⚙️ Modalità Auto-Troll nel gruppo: <b>{stato}</b>", parse_mode='HTML')
        if GROUP_CHAT_ID:
            try:
                await context.bot.send_message(
                    chat_id=int(GROUP_CHAT_ID),
                    text=f"Modalità Auto-Troll: {stato}"
                )
            except Exception as e:
                logging.error(f"Errore notifica gruppo: {e}")
    else:
        await update.message.reply_text(f"Modalità Auto-Troll: {stato}")

# --- COMANDO PEN (CANCELLA PENITENZE) ---
async def clear_penalties(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await track_ids(update, context)
    user_id = update.effective_user.id

    if not is_admin(user_id):
        await update.message.reply_text("❌ Solo gli admin possono cancellare le penitenze!")
        return

    had_penalties = len(PENITENZE_ATTIVE) > 0
    PENITENZE_ATTIVE.clear()

    if update.effective_chat.type == "private":
        await update.message.reply_text("🧹 Tutte le penitenze attive nel gruppo sono state rimosse!")
        if GROUP_CHAT_ID and had_penalties:
            try:
                await context.bot.send_message(
                    chat_id=int(GROUP_CHAT_ID),
                    text="🕊️ <b>L'Admin ha rimosso tutte le penitenze attive!</b>",
                    parse_mode='HTML'
                )
            except Exception as e:
                logging.error(f"Errore notifica gruppo: {e}")
    else:
        await update.message.reply_text("🧹 Tutte le penitenze attive sono state rimosse!")

# --- RESET DUELLO ---
async def reset_duello(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await track_ids(update, context)
    chat_id = update.message.chat_id
    
    if chat_id in ACTIVE_DUELS:
        del ACTIVE_DUELS[chat_id]

    if str(chat_id) in BLACKJACK_GAMES:
        del BLACKJACK_GAMES[str(chat_id)]

    if str(chat_id) in HIGHLOW_GAMES:
        del HIGHLOW_GAMES[str(chat_id)]

    await update.message.reply_text(
        "🛠️ <b>RESET LOGICA EFFETTUATO!</b>\n"
        "Tutti i duelli e i minigiochi bloccati sono stati cancellati.\n"
        "<i>Nota: Le penitenze attive della Roulette rimangono in vigore (usa /pen se necessario).</i>",
        parse_mode="HTML"
    )

# --- ROULETTE RUSSA 1v1 CON BOTTONI (UNICO GIOCO CON PENITENZA) ---
async def avvia_roulette(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await track_ids(update, context)
    chat_id = update.message.chat_id
    sfidante = update.message.from_user

    if chat_id in ACTIVE_DUELS:
        await update.message.reply_text("⚠️ C'è già una sfida in corso! Se si è buggata, usate `/resetduello`.", parse_mode="Markdown")
        return

    if not context.args or not context.args[0].startswith("@"):
        await update.message.reply_text("❌ Uso corretto: `/roulette @username`", parse_mode="Markdown")
        return

    target_username = context.args[0].replace("@", "").lower()

    if sfidante.username and target_username == sfidante.username.lower():
        await update.message.reply_text("🤡 Non puoi sfidare te stesso!")
        return

    ACTIVE_DUELS[chat_id] = {
        "sfidante_id": sfidante.id,
        "sfidante_name": sfidante.first_name,
        "sfidante_mention": f"@{sfidante.username}" if sfidante.username else sfidante.first_name,
        "target_username": target_username,
        "target_id": None,
        "target_name": f"@{target_username}",
        "target_mention": f"@{target_username}",
        "stato": "IN_ATTESA",
        "turno_id": None,
        "chambers": [False, False, False, False, False, False],
        "current_chamber": 0,
        "processing": False
    }

    bullet_idx = random.randint(0, 5)
    ACTIVE_DUELS[chat_id]["chambers"][bullet_idx] = True

    keyboard = [
        [
            InlineKeyboardButton("🎯 Accetta Sfida", callback_data="roulette_accetta"),
            InlineKeyboardButton("🐔 Rifiuta", callback_data="roulette_rifiuta")
        ]
    ]

    await update.message.reply_text(
        f"🔫 **ROULETTE RUSSA 1v1**\n\n"
        f"{sfidante.first_name} ha sfidato **@{target_username}**!\n"
        f"@{target_username}, usa i bottoni sottostanti per rispondere:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def gestione_bottoni_roulette(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat_id
    user = query.from_user

    if chat_id not in ACTIVE_DUELS:
        await query.answer("⚠️ Questa sfida non è più attiva o è stata resettata.", show_alert=True)
        return

    duel = ACTIVE_DUELS[chat_id]

    if query.data == "roulette_accetta":
        if duel["stato"] != "IN_ATTESA":
            await query.answer("La sfida è già iniziata!", show_alert=True)
            return

        if not (user.username and user.username.lower() == duel["target_username"]):
            await query.answer("❌ Solo la persona sfidata può accettare!", show_alert=True)
            return

        duel["target_id"] = user.id
        duel["target_name"] = user.first_name
        duel["target_mention"] = f"@{user.username}" if user.username else user.first_name
        duel["stato"] = "IN_CORSO"

        primo_id = random.choice([duel["sfidante_id"], duel["target_id"]])
        duel["turno_id"] = primo_id
        primo_nome = duel["sfidante_name"] if primo_id == duel["sfidante_id"] else duel["target_name"]

        await query.answer("Sfida accettata!")
        keyboard = [[InlineKeyboardButton("🔫 SPARA!", callback_data="roulette_spara")]]
        
        await query.edit_message_text(
            f"✅ **Sfida Accettata!** Tamburo caricato (1 proiettile, 6 camere).\n\n"
            f"🎲 Il sorteggio decide che comincia **{primo_nome}**!\n"
            f"Premi il bottone quando sei pronto.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )

    elif query.data == "roulette_rifiuta":
        if duel["stato"] != "IN_ATTESA":
            await query.answer()
            return

        if not (user.username and user.username.lower() == duel["target_username"]):
            await query.answer("❌ Non sei tu lo sfidato!", show_alert=True)
            return

        await query.answer("Sfida rifiutata.")
        await query.edit_message_text(f"🐔 @{user.username} si è tirato indietro! Sfida annullata.")
        del ACTIVE_DUELS[chat_id]

    elif query.data == "roulette_spara":
        if chat_id not in ACTIVE_DUELS or duel["stato"] != "IN_CORSO":
            await query.answer("La sfida non è più attiva.", show_alert=True)
            return

        if user.id != duel["turno_id"]:
            await query.answer("✋ Non è il tuo turno!", show_alert=True)
            return

        if duel["processing"]:
            await query.answer("Elaborazione sparo...", show_alert=False)
            return

        duel["processing"] = True
        await query.answer("Hai premuto il grilletto...")

        current_idx = duel["current_chamber"]
        is_bullet = duel["chambers"][current_idx]
        duel["current_chamber"] += 1

        user_mention = f"@{user.username}" if user.username else user.first_name

        await query.edit_message_text(f"🔫 **{user.first_name}** preme il grilletto...", parse_mode="Markdown")
        await asyncio.sleep(1.8)

        if chat_id not in ACTIVE_DUELS:
            return

        if is_bullet:
            try:
                until_date = datetime.now() + timedelta(seconds=120)
                await context.bot.restrict_chat_member(
                    chat_id=chat_id,
                    user_id=user.id,
                    permissions=ChatPermissions(can_send_messages=False),
                    until_date=until_date
                )
                msg_esito = f"💥 **BAM!** 💀 **{user_mention} è morto!**\n🔇 Mutato per 2 minuti!"
            except Exception:
                PENITENZE_ATTIVE[user.id] = 3
                
                try:
                    await context.bot.set_chat_administrator_custom_title(
                        chat_id=chat_id,
                        user_id=user.id,
                        custom_title="🤡 Giuse 🤡"
                    )
                except Exception:
                    pass

                msg_esito = (
                    f"💥 **BAM!** 💀 **{user_mention} è morto sul colpo!**\n\n"
                    f"🏷️ Ti è stato assegnato il titolo **🤡 Giuse 🤡**!\n"
                    f"⚠️ Per poterti riabilitare e tornare a scrivere, devi inviare esattamente la frase:\n"
                    f"👉 **`{FRASE_PENITENZA}`** (per 3 volte)\n\n"
                    f"*(Ogni altro tuo messaggio verrà eliminato all'istante!)*"
                )

            try:
                await context.bot.set_message_reaction(chat_id=chat_id, message_id=query.message.message_id, reaction="🤡")
            except Exception:
                pass

            await context.bot.send_message(chat_id=chat_id, text=msg_esito, parse_mode="Markdown")
            if chat_id in ACTIVE_DUELS:
                del ACTIVE_DUELS[chat_id]

        else:
            prossimo_id = duel["target_id"] if user.id == duel["sfidante_id"] else duel["sfidante_id"]
            prossimo_nome = duel["target_name"] if user.id == duel["sfidante_id"] else duel["sfidante_name"]
            
            duel["turno_id"] = prossimo_id
            duel["processing"] = False

            keyboard = [[InlineKeyboardButton("🔫 SPARA!", callback_data="roulette_spara")]]
            
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"*Click!* 😅 Camera vuota. **{user.first_name}** si salva!\n\n👉 Tocca a **{prossimo_nome}**!",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )

# --- MINIGIOCO: BLACKJACK EXPRESS 21 (SENZA PENITENZA) ---
def get_card():
    cards = [2, 3, 4, 5, 6, 7, 8, 9, 10, 10, 10, 10, 11]
    return random.choice(cards)

def calculate_score(hand):
    score = sum(hand)
    while score > 21 and 11 in hand:
        hand[hand.index(11)] = 1
        score = sum(hand)
    return score

async def start_blackjack(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await track_ids(update, context)
    chat_id = str(update.effective_chat.id)
    user = update.effective_user

    if chat_id in BLACKJACK_GAMES:
        await update.message.reply_text("⚠️ C'è già una partita di Blackjack in corso in questa chat!")
        return

    player_hand = [get_card(), get_card()]
    dealer_hand = [get_card()]

    BLACKJACK_GAMES[chat_id] = {
        "player_id": user.id,
        "player_name": user.first_name,
        "player_hand": player_hand,
        "dealer_hand": dealer_hand
    }

    keyboard = [
        [
            InlineKeyboardButton("🎴 Carta", callback_data="bj_hit"),
            InlineKeyboardButton("✋ Stai", callback_data="bj_stand")
        ]
    ]

    await update.message.reply_text(
        f"🃏 <b>BLACKJACK 21</b> 🃏\n\n"
        f"👤 Giocatore: <b>{user.first_name}</b>\n"
        f"🎎 Carte tue: {player_hand} (Totale: <b>{calculate_score(player_hand)}</b>)\n"
        f"🤖 Carta Banco: [{dealer_hand[0]}, ?]\n\n"
        f"Cosa vuoi fare?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML'
    )

async def handle_blackjack_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = str(query.message.chat.id)
    user_id = query.from_user.id

    if chat_id not in BLACKJACK_GAMES:
        await query.edit_message_text("❌ Questa partita è terminata.")
        return

    game = BLACKJACK_GAMES[chat_id]

    if user_id != game["player_id"]:
        await query.answer("⚠️ Non è la tua partita!", show_alert=True)
        return

    if query.data == "bj_hit":
        game["player_hand"].append(get_card())
        score = calculate_score(game["player_hand"])

        if score > 21:
            del BLACKJACK_GAMES[chat_id]
            await query.edit_message_text(
                f"💥 <b>SBALLATO!</b>\n\n"
                f"🎎 Le tue carte: {game['player_hand']} (Totale: <b>{score}</b>)\n"
                f"❌ <b>{game['player_name']}</b> ha superato 21 e ha perso la partita!",
                parse_mode='HTML'
            )
        else:
            keyboard = [
                [
                    InlineKeyboardButton("🎴 Carta", callback_data="bj_hit"),
                    InlineKeyboardButton("✋ Stai", callback_data="bj_stand")
                ]
            ]
            await query.edit_message_text(
                f"🃏 <b>BLACKJACK 21</b> 🃏\n\n"
                f"👤 Giocatore: <b>{game['player_name']}</b>\n"
                f"🎎 Carte tue: {game['player_hand']} (Totale: <b>{score}</b>)\n"
                f"🤖 Carta Banco: [{game['dealer_hand'][0]}, ?]\n\n"
                f"Cosa vuoi fare?",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='HTML'
            )

    elif query.data == "bj_stand":
        player_score = calculate_score(game["player_hand"])
        dealer_hand = game["dealer_hand"]
        
        while calculate_score(dealer_hand) < 17:
            dealer_hand.append(get_card())
            
        dealer_score = calculate_score(dealer_hand)
        player_name = game["player_name"]
        del BLACKJACK_GAMES[chat_id]

        text = (
            f"🏁 <b>RISULTATO FINALE BLACKJACK</b> 🏁\n\n"
            f"👤 <b>{player_name}</b>: {game['player_hand']} (Punti: <b>{player_score}</b>)\n"
            f"🤖 <b>Banco</b>: {dealer_hand} (Punti: <b>{dealer_score}</b>)\n\n"
        )

        if dealer_score > 21 or player_score > dealer_score:
            text += f"🏆 <b>VITTORIA! {player_name} ha battuto il Banco!</b> 🎉"
        elif player_score < dealer_score:
            text += f"❌ <b>SCONFITTA!</b> Il Banco vince. Ritenta la fortuna!"
        else:
            text += "⚖️ <b>PAREGGIO!</b> Avete fatto lo stesso punteggio."

        await query.edit_message_text(text, parse_mode='HTML')

# --- MINIGIOCO: DADO BUGIARDO / HIGHLOW (SENZA PENITENZA) ---
async def start_highlow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await track_ids(update, context)
    chat_id = str(update.effective_chat.id)
    user = update.effective_user

    if chat_id in HIGHLOW_GAMES:
        await update.message.reply_text("⚠️ C'è già un gioco di Dado Bugiardo in corso!")
        return

    current_val = random.randint(2, 11)  # Estrae numero tra 2 e 11 per bilanciare il gioco
    HIGHLOW_GAMES[chat_id] = {
        "player_id": user.id,
        "player_name": user.first_name,
        "current_val": current_val,
        "streak": 0
    }

    keyboard = [
        [
            InlineKeyboardButton("📈 PIÙ ALTO", callback_data="hl_high"),
            InlineKeyboardButton("📉 PIÙ BASSO", callback_data="hl_low")
        ]
    ]

    await update.message.reply_text(
        f"🎲 <b>DADO BUGIARDO (HIGH / LOW)</b> 🎲\n\n"
        f"👤 Giocatore: <b>{user.first_name}</b>\n"
        f"🎯 Numero estratto: <b>{current_val}</b> (da 1 a 12)\n\n"
        f"Il prossimo numero sarà <b>Più Alto</b> o <b>Più Basso</b>?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML'
    )

async def handle_highlow_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    chat_id = str(query.message.chat.id)
    user_id = query.from_user.id

    if chat_id not in HIGHLOW_GAMES:
        await query.edit_message_text("❌ Partita terminata.")
        return

    game = HIGHLOW_GAMES[chat_id]

    if user_id != game["player_id"]:
        await query.answer("⚠️ Non è il tuo gioco!", show_alert=True)
        return

    old_val = game["current_val"]
    new_val = random.randint(1, 12)
    while new_val == old_val:  # Evita parità per un gameplay più fluido
        new_val = random.randint(1, 12)

    choice = query.data
    won = (choice == "hl_high" and new_val > old_val) or (choice == "hl_low" and new_val < old_val)

    if won:
        game["streak"] += 1
        game["current_val"] = new_val

        keyboard = [
            [
                InlineKeyboardButton("📈 PIÙ ALTO", callback_data="hl_high"),
                InlineKeyboardButton("📉 PIÙ BASSO", callback_data="hl_low")
            ],
            [
                InlineKeyboardButton("💰 INCASSA E RITIRATI", callback_data="hl_cashout")
            ]
        ]

        await query.edit_message_text(
            f"✅ <b>GIUSTO!</b> Era <b>{new_val}</b>!\n"
            f"🔥 Serie di successi: <b>{game['streak']}</b>\n\n"
            f"🎯 Nuovo numero: <b>{new_val}</b>\n"
            f"Cosa fai adesso?",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='HTML'
        )
    elif choice == "hl_cashout":
        streak = game["streak"]
        del HIGHLOW_GAMES[chat_id]
        await query.edit_message_text(
            f"💰 <b>INCASSO EFFETTUATO!</b>\n"
            f"🏆 <b>{game['player_name']}</b> si ritira da vincitore con una serie di <b>{streak}</b> risposte esatte consecutivi!",
            parse_mode='HTML'
        )
    else:
        del HIGHLOW_GAMES[chat_id]
        await query.edit_message_text(
            f"❌ <b>ERRATO!</b> Era <b>{new_val}</b>!\n"
            f"💀 <b>{game['player_name']}</b> ha sbagliato e ha perso tutto alla serie {game['streak']}!",
            parse_mode='HTML'
        )

# --- MINIGIOCO: SLOT MACHINE 777 (SENZA PENITENZA) ---
async def play_slot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await track_ids(update, context)
    user_name = update.effective_user.first_name

    symbols = ["🍒", "🔔", "🍋", "💎", "🎰", "7️⃣"]
    
    msg = await update.message.reply_text(f"🎰 <b>{user_name}</b> tira la levetta della Slot...\n\n[ ❓ | ❓ | ❓ ]", parse_mode='HTML')
    await asyncio.sleep(1.0)

    # Estrazione casuale simboli
    r1, r2, r3 = random.choice(symbols), random.choice(symbols), random.choice(symbols)

    text = f"🎰 <b>SLOT MACHINE 777</b> 🎰\n👤 Giocatore: <b>{user_name}</b>\n\n[ {r1} | {r2} | {r3} ]\n\n"

    if r1 == r2 == r3:
        if r1 == "7️⃣":
            text += "🔥 <b>JACKPOT SUPREMO 777!</b> 🔥 Hai sbancato tutto!"
        else:
            text += "🎉 <b>TRIPLETTA VINCENTE!</b> Grande vittoria!"
    elif r1 == r2 or r2 == r3 or r1 == r3:
        text += "✨ <b>DOPPIETTA!</b> Hai sfiorato il jackpot!"
    else:
        text += "💸 <b>NESSUNA COMBINAZIONE!</b> Hai perso le tue monete!"

    await msg.edit_text(text, parse_mode='HTML')

# --- CONTROLLO PENITENZE E AUTO-TROLL ---
async def handle_reaction_and_penitenza(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.from_user:
        return

    user = update.message.from_user
    if user.is_bot:
        return

    chat_id = update.message.chat_id
    text = (update.message.text or "").strip().lower()

    if user.id in PENITENZE_ATTIVE and PENITENZE_ATTIVE[user.id] > 0:
        if text != FRASE_PENITENZA:
            try:
                await update.message.delete()
                user_tag = f"@{user.username}" if user.username else user.first_name
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"🚫 Messaggio di {user_tag} eliminato! Devi scrivere esattamente: `{FRASE_PENITENZA}` (Mancano {PENITENZE_ATTIVE[user.id]} volte).",
                    parse_mode="Markdown"
                )
                return
            except Exception:
                pass
        else:
            PENITENZE_ATTIVE[user.id] -= 1
            if PENITENZE_ATTIVE[user.id] == 0:
                del PENITENZE_ATTIVE[user.id]
                
                try:
                    await context.bot.set_chat_administrator_custom_title(chat_id=chat_id, user_id=user.id, custom_title="")
                except Exception:
                    pass

                await update.message.reply_text(f"✅ Penitenza completata! @{user.username} ha ammesso di essere un perdente ed è stato riabilitato!")
                return

    if not IS_TROLLING_ACTIVE:
        return

    username = user.username.lower() if user.username else ""
    if username in TARGET_MAP:
        if random.random() < 0.85:
            emoji = TARGET_MAP[username]
            await asyncio.sleep(random.uniform(1.0, 2.0))
            try:
                await context.bot.set_message_reaction(
                    chat_id=chat_id,
                    message_id=update.message.message_id,
                    reaction=emoji
                )
            except Exception as e:
                print(f"--> ERRORE TELEGRAM: {e}", flush=True)

# --- MAIN ASYNC ---
async def main_async():
    if not TELEGRAM_TOKEN:
        logging.error("ERRORE CRITICO: La variabile d'ambiente TELEGRAM_TOKEN non è impostata!")
        return

    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()

    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Registrazione Comandi
    application.add_handler(CommandHandler("sdrogocomm", show_commands))
    application.add_handler(CommandHandler("troll", toggle_troll))
    application.add_handler(CommandHandler("pen", clear_penalties))
    application.add_handler(CommandHandler("resetduello", reset_duello))
    application.add_handler(CommandHandler("roulette", avvia_roulette))
    application.add_handler(CommandHandler("blackjack", start_blackjack))
    application.add_handler(CommandHandler("highlow", start_highlow))
    application.add_handler(CommandHandler("slot", play_slot))

    # Callbacks dei Bottoni
    application.add_handler(CallbackQueryHandler(gestione_bottoni_roulette, pattern="^roulette_"))
    application.add_handler(CallbackQueryHandler(handle_blackjack_callback, pattern="^bj_"))
    application.add_handler(CallbackQueryHandler(handle_highlow_callback, pattern="^hl_"))

    # Handler Messaggi generici (Auto-troll e Penitenze)
    application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_reaction_and_penitenza))

    print("SdrogoBot attivo e pronto!", flush=True)

    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)
    await asyncio.Event().wait()

if __name__ == '__main__':
    try:
        asyncio.run(main_async())
    except (KeyboardInterrupt, SystemExit):
        pass
