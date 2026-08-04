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

# Mappa delle vittime con relative emoji (username in minuscolo)
TARGET_MAP = {
    "manueiii": "🙉",
    "spoleto17": "🤡",
    "artemesio": "💩"
}

# Legge il token in modo sicuro dalle variabili d'ambiente di Render
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")

IS_TROLLING_ACTIVE = True

# Frase segreta di penitenza per lo sconfitto
FRASE_PENITENZA = "sono un perdente"

# Memoria sfide attive
ACTIVE_DUELS = {}

# Memoria penitenze attive: {user_id: conteggio_messaggi_rimasti}
PENITENZE_ATTIVE = {}

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

# --- COMANDO TOGGLE (ON / OFF AUTO-TROLL) ---
async def toggle_troll(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global IS_TROLLING_ACTIVE
    IS_TROLLING_ACTIVE = not IS_TROLLING_ACTIVE
    stato = "ATTIVATA 🙉🤡💩" if IS_TROLLING_ACTIVE else "DISATTIVATA 🛑"
    await update.message.reply_text(f"Modalità Auto-Troll: {stato}")

# --- ROULETTE RUSSA 1v1 CON BOTTONI ---

async def avvia_roulette(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    sfidante = update.message.from_user

    if chat_id in ACTIVE_DUELS:
        await update.message.reply_text("⚠️ C'è già una sfida in corso in questa chat!")
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
        await query.answer("⚠️ Questa sfida non è più attiva.", show_alert=True)
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
        if duel["stato"] != "IN_CORSO":
            await query.answer("La sfida non è in corso.", show_alert=True)
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

        if is_bullet:
            # BAM! PROIETTILE ESPLOSO
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
                except Exception as e:
                    print(f"--> Impossibile impostare Custom Title: {e}", flush=True)

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
            del ACTIVE_DUELS[chat_id]

        else:
            # CLICK! CAMERA VUOTA
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

# --- CONTROLLO PENITENZE E AUTO-TROLL ---
async def handle_reaction_and_penitenza(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.from_user:
        return

    user = update.message.from_user
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

async def main_async():
    if not TELEGRAM_TOKEN:
        logging.error("ERRORE CRITICO: La variabile d'ambiente TELEGRAM_TOKEN non è impostata!")
        return

    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()

    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    application.add_handler(CommandHandler("toggle", toggle_troll))
    application.add_handler(CommandHandler("roulette", avvia_roulette))
    application.add_handler(CallbackQueryHandler(gestione_bottoni_roulette, pattern="^roulette_"))
    application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_reaction_and_penitenza))

    print("SdrogoBot protetto e attivo con Token da Environment Variables...", flush=True)

    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)
    await asyncio.Event().wait()

if __name__ == '__main__':
    try:
        asyncio.run(main_async())
    except (KeyboardInterrupt, SystemExit):
        pass
