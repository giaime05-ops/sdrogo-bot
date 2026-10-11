"""Admin, gestione errori e router dei messaggi di testo."""
import logging
import random
import time
from datetime import date

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes,
    ApplicationHandlerStop, TypeHandler,
)

from config import FRASE_PENITENZA, TARGET_MAP
from state import (
    FLAGS, ACTIVE_DUELS, HIGHLOW_DUELS, BLACKJACK_GAMES, WORDLE_GAMES,
    MASTERMIND_GAMES, QUIZ_GAMES, QUIZ_DUELS_1V1, GHIGLIOTTINA_DUELS,
    HEIST_GAMES, PENITENZE_ATTIVE, ACTIVE_PERSECUTE, TIC_TAC_TOE_GAMES, DAILY_DONATIONS,
)
from storage import add_user_coins, get_user_coins, add_user_stat
from utils import is_admin
from handlers.hub import apply_title_command, apply_persecute_command
from handlers.multiplayer import (
    launch_quiz1v1_round, conclude_quiz1v1_duel, setup_challenge_menu, record_duel_result
)
from handlers.games import record_single_result

MAINT_TEXT = (
    "🛠️ <b>SdrogoBot è in manutenzione</b>\n"
    "HUB e giochi sono sospesi per qualche minuto. Riprova tra poco!"
)
MAINT_ALERT = "🛠️ Bot in manutenzione: i giochi sono temporaneamente sospesi. Riprova tra poco!"
MAINT_COOLDOWN = 20   # secondi tra due avvisi nella stessa chat

# parole in chat (senza "/") che avviano un gioco o un acquisto
GAME_TRIGGERS = {
    "sfido", "sfidodadi", "sfidotris", "sfidoroulette", "sfidohighlow",
    "sfidoghigliottina", "sfidoquiz", "titolo", "perseguita"
}

CHALLENGES = {
    "sfidodadi": "dice", "/sfidodadi": "dice",
    "sfidotris": "ttt", "/sfidotris": "ttt",
    "sfidoroulette": "roulette", "/sfidoroulette": "roulette",
    "sfidohighlow": "highlow", "/sfidohighlow": "highlow",
    "sfidoghigliottina": "ghig", "/sfidoghigliottina": "ghig",
    "sfidoquiz": "quiz1v1", "/sfidoquiz": "quiz1v1",
    "sfido": "roulette", "/sfido": "roulette"
}


# --- UTILITIES / ADMIN ---
async def toggle_maintenance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    FLAGS["maintenance"] = not FLAGS.get("maintenance", False)
    await update.message.reply_text(
        "🛠️ Manutenzione ATTIVATA: HUB e giochi bloccati per tutti tranne te."
        if FLAGS["maintenance"] else
        "✅ Manutenzione DISATTIVATA: il bot è di nuovo operativo."
    )

async def maintenance_gate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not FLAGS.get("maintenance"):
        return
    user = update.effective_user
    if user and is_admin(user.id):
        return                                   # l'admin passa sempre

    # 1) Bottoni inline: solo popup, nessun messaggio in chat
    if update.callback_query:
        await update.callback_query.answer(MAINT_ALERT, show_alert=True)
        raise ApplicationHandlerStop

    msg = update.effective_message
    if not msg or not msg.text:
        return

    text = msg.text.strip().lower()
    first = text.split()[0]

    # 2) Comandi (/sdrogocomm, /topricconi, ...)
    if first.startswith("/"):
        _, _, for_bot = first[1:].partition("@")
        if for_bot and for_bot != (context.bot.username or "").lower():
            return                               # comando indirizzato a un altro bot
    # 3) Parole-trigger dei giochi ("sfidoquiz @x", "titolo @x", ...)
    elif first not in GAME_TRIGGERS:
        return                                   # testo normale: NON fare nulla

    # avviso con cooldown per non spammare
    last = context.bot_data.setdefault("maint_last", {})
    now = time.time()
    if now - last.get(msg.chat_id, 0) > MAINT_COOLDOWN:
        last[msg.chat_id] = now
        await msg.reply_text(MAINT_TEXT, parse_mode="HTML")
    raise ApplicationHandlerStop


async def ricarica_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user, chat = update.effective_user, update.effective_chat
    if not is_admin(user.id):
        return
    if chat.type != "private":
        await update.message.reply_text("🔒 /ricarica funziona solo in chat privata col bot.")
        return
    add_user_coins(chat.id, user.id, 1000)
    await update.message.reply_text(
        f"🧪 Modalità test: +1000 $SDG. Saldo: {get_user_coins(chat.id, user.id)} $SDG"
    )

async def toggle_troll(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    FLAGS["troll"] = not FLAGS["troll"]
    await update.message.reply_text(f"Modalità Auto-Troll: {'ATTIVATA 🙉' if FLAGS['troll'] else 'DISATTIVATA 🛑'}")

async def clear_penalties(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    PENITENZE_ATTIVE.clear()
    await update.message.reply_text("🧹 Penitenze rimosse!")

async def reset_duello(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    ACTIVE_DUELS.clear()
    HIGHLOW_DUELS.clear()
    BLACKJACK_GAMES.clear()
    WORDLE_GAMES.clear()
    MASTERMIND_GAMES.clear()
    QUIZ_GAMES.clear()
    QUIZ_DUELS_1V1.clear()
    GHIGLIOTTINA_DUELS.clear()
    HEIST_GAMES.clear()
    TIC_TAC_TOE_GAMES.clear()
    await update.message.reply_text("🛠️ Tutti i giochi bloccati sono stati resettati.")

# --- COMANDO DONAZIONE CREDITI (/dona) ---
async def donate_coins_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    text = (update.message.text or "").strip()

    target_username = None
    for part in text.split():
        if part.startswith("@"):
            target_username = part.replace("@", "").lower()
            break

    if not target_username:
        await update.message.reply_text("❌ Uso corretto: <code>/dona @username</code> (Invia 5 $SDG)", parse_mode="HTML")
        return

    if target_username == (user.username or "").lower():
        await update.message.reply_text("❌ Non puoi donare crediti a te stesso!", parse_mode="HTML")
        return

    today_str = str(date.today())
    donation_key = f"{user.id}_{target_username}_{today_str}"
    if donation_key in DAILY_DONATIONS:
        await update.message.reply_text("❌ Hai già fatto una donazione a questo utente oggi! Riprova domani.", parse_mode="HTML")
        return

    target_id = None
    prefix = f"{chat_id}_"
    from state import USER_DATA
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
        await update.message.reply_text("❌ Utente destinatario non trovato nel registro della chat!", parse_mode="HTML")
        return

    if get_user_coins(chat_id, user.id) < 5:
        await update.message.reply_text("❌ Non hai abbastanza $SDG (servono almeno 5 crediti)!", parse_mode="HTML")
        return

    add_user_coins(chat_id, user.id, -5)
    add_user_coins(chat_id, target_id, 5)
    DAILY_DONATIONS.add(donation_key)

    await update.message.reply_text(f"🎁 <b>DONAZIONE EFFETTUATA!</b> @{user.username or user.first_name} ha donato <b>💳 5 $SDG</b> a @{target_username}!", parse_mode="HTML")

# --- SISTEMA DI DEBUG ED ERRORI ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logging.error(msg="Eccezione non gestita durante l'update:", exc_info=context.error)
    print(f"🚨 ERRORE FATALE CRASH: {context.error}", flush=True)

async def catch_all_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print(f"🔥 RICEVUTO UN CLICK! Pulsante: {update.callback_query.data} da utente {update.effective_user.first_name}", flush=True)

# --- HANDLER MESSAGGI GENERICI (TEXT ROUTER) ---
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

    # Handling Tag Persecutore
    persecute_key = f"{chat_id}_{username_lower}"
    if persecute_key in ACTIVE_PERSECUTE:
        p_data = ACTIVE_PERSECUTE[persecute_key]
        if p_data["count"] > 0:
            p_data["count"] -= 1
            await update.message.reply_text(p_data["phrase"])
            if p_data["count"] == 0:
                del ACTIVE_PERSECUTE[persecute_key]

    # Handling Comandi Shop Testuali
    if text.lower().startswith("titolo") or text.lower().startswith("/titolo"):
        await apply_title_command(update, context)
        return
    elif text.lower().startswith("perseguita") or text.lower().startswith("/perseguita"):
        await apply_persecute_command(update, context)
        return

    # Handling Comando Dona
    if text.lower().startswith("/dona") or text.lower().startswith("dona "):
        await donate_coins_command(update, context)
        return

    # Handling Sfide Unificate (Claude Refactor)
    first = text.lower().split()[0] if text else ""
    if first in CHALLENGES:
        target = next((p[1:].lower() for p in text.split() if p.startswith("@") and len(p) > 1), None)
        if not target:
            await update.message.reply_text(f"❌ Uso: <code>{first} @username</code>", parse_mode="HTML")
            return
        if target == username_lower:
            await update.message.reply_text("🤡 Non puoi sfidare te stesso!")
            return
        await setup_challenge_menu(update, context, CHALLENGES[first], target, chat_id_int, user)
        return

    text_upper = text.upper()

    # Handling Risposta Ghigliottina 1v1
    if chat_id_int in GHIGLIOTTINA_DUELS and GHIGLIOTTINA_DUELS[chat_id_int].get("active"):
        duel = GHIGLIOTTINA_DUELS[chat_id_int]
        if user.id in [duel["sfidante_id"], duel["target_id"]] and text_upper == duel["word"]:
            loser_id = duel["target_id"] if user.id == duel["sfidante_id"] else duel["sfidante_id"]
            record_duel_result(chat_id_int, user.id, loser_id, duel["bet"])
            del GHIGLIOTTINA_DUELS[chat_id_int]
            montepremi = duel["bet"] * 2
            await update.message.reply_text(f"🏆 <b>GHIGLIOTTINA RISOLTA!</b> <b>{user.first_name}</b> indovina <b>{duel['word']}</b> e vince +💳 {montepremi} $SDG!", parse_mode="HTML")
            return

    # Handling Risposta Duello Quiz 1v1
    if chat_id_int in QUIZ_DUELS_1V1 and QUIZ_DUELS_1V1[chat_id_int].get("active"):
        duel = QUIZ_DUELS_1V1[chat_id_int]
        if user.id in [duel["sfidante_id"], duel["target_id"]] and text_upper == duel["current_target"]:
            if user.id == duel["sfidante_id"]: duel["p1_score"] += 1
            else: duel["p2_score"] += 1
            await update.message.reply_text(f"🎯 <b>RISPOSTA CORRETTA!</b> <b>{user.first_name}</b> prende il punto del Round {duel['round']}!", parse_mode="HTML")
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
            end_keyboard = [[InlineKeyboardButton("🔂 Rigioca", callback_data=f"start_mm_{user.id}"), InlineKeyboardButton("🔙 HUB", callback_data=f"hub_main_{user.id}")] ]
            if c_hits == 3:
                del MASTERMIND_GAMES[mm_key]
                reward = 35 if mm["attempts"] <= 3 else 20
                net_profit = reward - 10
                record_single_result(chat_id_int, user.id, True, net_profit, is_casino=False)
                add_user_coins(chat_id_int, user.id, reward)
                await update.message.reply_text(f"🎉 <b>ESATTO!</b> Codice: <b>{secret}</b>! Vinti +💳 {reward} $SDG!\n\n{res_text}", reply_markup=InlineKeyboardMarkup(end_keyboard), parse_mode="HTML")
            elif mm["attempts"] >= 5:
                del MASTERMIND_GAMES[mm_key]
                record_single_result(chat_id_int, user.id, False, -10, is_casino=False)
                await update.message.reply_text(f"💥 <b>GAME OVER!</b> Il codice era {secret}.\n\n{res_text}", reply_markup=InlineKeyboardMarkup(end_keyboard), parse_mode="HTML")
            else:
                await update.message.reply_text(f"🔐 <b>MASTERMIND ({mm['attempts']}/5)</b>\n\n{res_text}", parse_mode="HTML")
            return

    # Handling Quiz Multiplayer & Single (con statistiche Quizzes Won)
    if chat_id in QUIZ_GAMES and QUIZ_GAMES[chat_id].get("multi") and text_upper == QUIZ_GAMES[chat_id]["target"]:
        q = QUIZ_GAMES[chat_id]
        del QUIZ_GAMES[chat_id]
        add_user_coins(chat_id_int, user.id, 15)
        add_user_stat(chat_id_int, user.id, "quizzes_won", 1)
        await update.message.reply_text(f"🎉 <b>QUIZ MULTI RISOLTO!</b> <b>{user.first_name}</b> indovina <b>{q['target']}</b> e vince +💳 15 $SDG!", parse_mode="HTML")
        return

    quiz_key = f"{chat_id}_{user.id}"
    if quiz_key in QUIZ_GAMES and text_upper == QUIZ_GAMES[quiz_key]["target"]:
        q = QUIZ_GAMES[quiz_key]
        del QUIZ_GAMES[quiz_key]
        reward = 20 if q["step"] == 1 else (10 if q["step"] == 2 else 6)
        add_user_coins(chat_id_int, user.id, reward)
        add_user_stat(chat_id_int, user.id, "quizzes_won", 1)
        await update.message.reply_text(f"🎉 <b>CORRETTO!</b> <b>{user.first_name}</b> indovina <b>{q['target']}</b>! Vinti +💳 {reward} $SDG!", parse_mode="HTML")
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
            end_keyboard = [[InlineKeyboardButton("🔂 Rigioca", callback_data=f"start_wordle_{user.id}"), InlineKeyboardButton("🔙 HUB", callback_data=f"hub_main_{user.id}")] ]
            
            if text_upper == secret:
                del WORDLE_GAMES[game_key]
                record_single_result(chat_id_int, user.id, True, 10, is_casino=False)
                add_user_coins(chat_id_int, user.id, 20)
                await update.message.reply_text(f"🎉 <b>ESATTO!</b> Parola: <b>{secret}</b>! Vinti +💳 20 $SDG!\n\n{res_text}", reply_markup=InlineKeyboardMarkup(end_keyboard), parse_mode="HTML")
            elif game["attempts"] >= 5:
                del WORDLE_GAMES[game_key]
                record_single_result(chat_id_int, user.id, False, -10, is_casino=False)
                await update.message.reply_text(f"💥 <b>GAME OVER!</b> La parola era {secret}.\n\n{res_text}", reply_markup=InlineKeyboardMarkup(end_keyboard), parse_mode="HTML")
            else:
                await update.message.reply_text(f"🔠 <b>WORDLE ({game['attempts']}/5)</b>\n\n{res_text}", parse_mode="HTML")
            return

    # Auto-Troll
    if FLAGS["troll"] and username_lower in TARGET_MAP:
        if random.random() < 0.85:
            try: await context.bot.set_message_reaction(chat_id=chat_id, message_id=update.message.message_id, reaction=TARGET_MAP[username_lower])
            except Exception: pass

# --- REGISTRAZIONE HANDLER ---
def register(app):
    app.add_error_handler(error_handler)
    app.add_handler(TypeHandler(Update, maintenance_gate), group=-2)
    app.add_handler(CallbackQueryHandler(catch_all_callbacks), group=-1)
    app.add_handler(CommandHandler("troll", toggle_troll))
    app.add_handler(CommandHandler("pen", clear_penalties))
    app.add_handler(CommandHandler("resetduello", reset_duello))
    app.add_handler(CommandHandler("dona", donate_coins_command))
    app.add_handler(CommandHandler("manutenzione", toggle_maintenance))
    app.add_handler(CommandHandler("ricarica", ricarica_command))

def register_text(app):
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_messages))
