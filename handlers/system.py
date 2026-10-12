"""Admin, gestione errori e router dei messaggi di testo."""
import html
import logging
import random
import time
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import RetryAfter, BadRequest
from telegram.ext import (
    CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes,
    ApplicationHandlerStop, TypeHandler,
)

try:
    from zoneinfo import ZoneInfo
    _TZ = ZoneInfo("Europe/Rome")
except Exception:
    _TZ = None

from config import FRASE_PENITENZA, TARGET_MAP
from state import (
    FLAGS, ACTIVE_DUELS, HIGHLOW_DUELS, DICE_DUELS, BLACKJACK_GAMES, WORDLE_GAMES,
    MASTERMIND_GAMES, QUIZ_GAMES, QUIZ_DUELS_1V1, GHIGLIOTTINA_DUELS,
    HEIST_GAMES, PENITENZE_ATTIVE, ACTIVE_PERSECUTE, TIC_TAC_TOE_GAMES,
    DAILY_DONATIONS, USER_DATA, GAME_STATUS, game_enabled, MAINT_GAME_TEXT,
)
from storage import add_user_coins, get_user_coins, add_user_stat
from utils import is_admin
from handlers.hub import apply_title_command, apply_persecute_command
from handlers.multiplayer import (
    setup_challenge_menu, record_duel_result, quiz1v1_advance, rematch_row, STATUS_KEY,
)
from handlers.games import (
    record_single_result, HEIST_COST, WORDLE_REWARDS, MASTERMIND_REWARDS,
    WORD_SET, norm_word,
)
from handlers.quiz import quiz_reward

MAINT_TEXT = (
    "🛠️ <b>SdrogoBot è in manutenzione</b>\n"
    "HUB e giochi sono sospesi per qualche minuto. Riprova tra poco!"
)
MAINT_ALERT = "🛠️ Bot in manutenzione: i giochi sono temporaneamente sospesi. Riprova tra poco!"
MAINT_COOLDOWN = 20   # secondi tra due avvisi nella stessa chat

# parole in chat (senza "/") che avviano un gioco o un acquisto
GAME_TRIGGERS = {
    "sfido", "sfidodadi", "sfidotris", "sfidoroulette", "sfidohighlow",
    "sfidoghigliottina", "sfidoquiz", "titolo", "perseguita", "dona"
}

# Il MessageHandler esclude i comandi (~filters.COMMAND), quindi qui servono solo le parole senza "/"
CHALLENGES = {
    "sfidodadi": "dice",
    "sfidotris": "ttt",
    "sfidoroulette": "roulette",
    "sfidohighlow": "highlow",
    "sfidoghigliottina": "ghig",
    "sfidoquiz": "quiz1v1",
    "sfido": "roulette",
}

# Dashboard /admin: (chiave in GAME_STATUS, etichetta)
ADMIN_GAMES = [
    ("wordle", "🔠 Wordle"),
    ("blackjack", "🃏 Blackjack"),
    ("slot", "🎰 Slot 777"),
    ("mastermind", "🔐 Mastermind"),
    ("heist", "🏢 Sdrogo Heist"),
    ("dice", "🎲 Dadi 1v1"),
    ("ttt", "❌ Tris 1v1"),
    ("roulette1v1", "🎯 Roulette 1v1"),
    ("highlow", "📈 High/Low 1v1"),
    ("ghig", "🪓 Ghigliottina 1v1"),
    ("quiz1v1", "⚔️ Quiz 1v1"),
    ("quizmulti", "🌐 Quiz Multi"),
]


def _today() -> str:
    return str(datetime.now(_TZ).date())


# --- DASHBOARD ADMIN (/admin): accendi/spegni i singoli giochi ---
def _admin_allowed(user_id: int, chat_type: str) -> bool:
    return is_admin(user_id) and chat_type == "private"


def _admin_view():
    text = (
        "🛠️ <b>DASHBOARD ADMIN</b>\n\n"
        "🟢 = attivo | 🔴 = in manutenzione\n"
        "Tocca un gioco per accenderlo o spegnerlo. "
        "<i>Le partite già in corso possono sempre finire.</i>"
    )
    rows, row = [], []
    for key, label in ADMIN_GAMES:
        icon = "🟢" if GAME_STATUS.get(key, True) else "🔴"
        row.append(InlineKeyboardButton(f"{icon} {label}", callback_data=f"adm_toggle_{key}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([
        InlineKeyboardButton("🟢 Accendi tutti", callback_data="adm_all_1"),
        InlineKeyboardButton("🔴 Spegni tutti", callback_data="adm_all_0"),
    ])
    rows.append([InlineKeyboardButton("✖️ Chiudi", callback_data="adm_close")])
    return text, InlineKeyboardMarkup(rows)


async def admin_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user, chat = update.effective_user, update.effective_chat
    if not user or not is_admin(user.id):
        return                                    # i non-admin non ricevono nessuna risposta
    if chat.type != "private":
        await update.message.reply_text("🔒 /admin funziona solo in chat privata col bot.")
        return
    text, markup = _admin_view()
    await update.message.reply_text(text, reply_markup=markup, parse_mode="HTML")


async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _admin_allowed(query.from_user.id, query.message.chat.type):
        await query.answer("🔒 Area riservata all'admin in chat privata.", show_alert=True)
        return

    parts = query.data.split("_")        # adm_toggle_wordle | adm_all_1 | adm_close
    action = parts[1]

    if action == "close":
        await query.answer()
        try:
            await query.message.delete()
        except Exception:
            pass
        return

    if action == "toggle":
        key = parts[2] if len(parts) > 2 else ""
        if key not in GAME_STATUS:
            await query.answer("Gioco sconosciuto.", show_alert=True)
            return
        GAME_STATUS[key] = not GAME_STATUS[key]
        await query.answer(f"{key}: {'🟢 acceso' if GAME_STATUS[key] else '🔴 spento'}")
    elif action == "all":
        value = parts[2] == "1" if len(parts) > 2 else True
        for key in GAME_STATUS:
            GAME_STATUS[key] = value
        await query.answer("Tutti i giochi " + ("🟢 accesi" if value else "🔴 spenti"))
    else:
        await query.answer()
        return

    text, markup = _admin_view()
    try:
        await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
    except Exception:
        pass


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
        try:
            await update.callback_query.answer(MAINT_ALERT, show_alert=True)
        except Exception:
            pass
        raise ApplicationHandlerStop

    msg = update.effective_message
    if not msg or not msg.text:
        return

    text = msg.text.strip().lower()
    if not text:
        return
    first = text.split()[0]

    # 2) Comandi: solo /sdrogocomm riceve l'avviso, tutti gli altri vengono ignorati in silenzio
    if first.startswith("/"):
        name, _, for_bot = first[1:].partition("@")
        if for_bot and for_bot != (context.bot.username or "").lower():
            return                               # comando indirizzato a un altro bot
        if name != "sdrogocomm":
            raise ApplicationHandlerStop         # nessuna risposta
    # 3) Parole-trigger dei giochi ("sfidoquiz @x", "titolo @x", ...)
    elif first not in GAME_TRIGGERS:
        return                                   # testo normale: NON fare nulla

    # avviso con cooldown per non spammare
    last = context.bot_data.setdefault("maint_last", {})
    now = time.time()
    if now - last.get(msg.chat_id, 0) > MAINT_COOLDOWN:
        last[msg.chat_id] = now
        try:
            await msg.reply_text(MAINT_TEXT, parse_mode="HTML")
        except Exception:
            pass
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
    if not is_admin(update.effective_user.id):
        return
    for d in (HIGHLOW_DUELS, DICE_DUELS, ACTIVE_DUELS, TIC_TAC_TOE_GAMES,
              GHIGLIOTTINA_DUELS, QUIZ_DUELS_1V1):
        for cid, g in list(d.items()):
            if g.get("target_id") and g.get("bet"):      # accettato = puntate già scalate
                add_user_coins(cid, g["sfidante_id"], g["bet"])
                add_user_coins(cid, g["target_id"], g["bet"])
        d.clear()
    for key, g in list(BLACKJACK_GAMES.items()):
        cid, uid = key.rsplit("_", 1)
        add_user_coins(int(cid), int(uid), g["bet"])
    for store in (WORDLE_GAMES, MASTERMIND_GAMES):
        for key in list(store.keys()):
            cid, uid = key.rsplit("_", 1)
            add_user_coins(int(cid), int(uid), 10)
    for key, g in list(QUIZ_GAMES.items()):
        if not g.get("multi") and "_" in str(key):
            cid, uid = str(key).rsplit("_", 1)
            add_user_coins(int(cid), int(uid), 5)
    for uid, g in list(HEIST_GAMES.items()):
        add_user_coins(g["chat_id"], uid, HEIST_COST)
    for d in (BLACKJACK_GAMES, WORDLE_GAMES, MASTERMIND_GAMES, QUIZ_GAMES, HEIST_GAMES):
        d.clear()
    await update.message.reply_text("🛠️ Giochi resettati. Tutte le puntate in corso sono state rimborsate.")

# --- COMANDO DONAZIONE CREDITI (/dona) ---
async def donate_coins_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    text = (update.message.text or "").strip()

    target_username = None
    target_id = None
    target_label = None
    for part in text.split():
        if part.startswith("@") and len(part) > 1:
            target_username = part[1:].lower()
            break

    reply = update.message.reply_to_message
    if not target_username and reply and reply.from_user:
        if reply.from_user.is_bot:
            await update.message.reply_text("❌ Non puoi donare crediti a un bot!", parse_mode="HTML")
            return
        target_id = reply.from_user.id
        target_label = html.escape(reply.from_user.first_name or "utente")

    if not target_username and not target_id:
        await update.message.reply_text("❌ Uso corretto: <code>/dona @username</code> (Invia 5 $SDG)", parse_mode="HTML")
        return

    if target_username and target_username == (user.username or "").lower():
        await update.message.reply_text("❌ Non puoi donare crediti a te stesso!", parse_mode="HTML")
        return

    if target_username:
        target_label = f"@{html.escape(target_username)}"
        # 1) cache degli username già visti in chat, 2) scansione del registro come ripiego
        target_id = context.bot_data.get("usernames", {}).get(f"{chat_id}_{target_username}")
        if not target_id:
            prefix = f"{chat_id}_"
            for k in list(USER_DATA.keys()):
                if k.startswith(prefix):
                    uid = k.rsplit("_", 1)[1]
                    try:
                        m = await context.bot.get_chat_member(chat_id, int(uid))
                        if m.user.username and m.user.username.lower() == target_username:
                            target_id = int(uid)
                            break
                    except Exception:
                        pass

    if not target_id:
        await update.message.reply_text("❌ Utente destinatario non trovato nel registro della chat!", parse_mode="HTML")
        return
    if target_id == user.id:
        await update.message.reply_text("❌ Non puoi donare crediti a te stesso!", parse_mode="HTML")
        return

    today_str = _today()
    DAILY_DONATIONS.difference_update({k for k in DAILY_DONATIONS if not k.endswith(today_str)})
    donation_key = f"{chat_id}_{user.id}_{target_id}_{today_str}"
    if donation_key in DAILY_DONATIONS:
        await update.message.reply_text("❌ Hai già fatto una donazione a questo utente oggi! Riprova domani.", parse_mode="HTML")
        return

    if get_user_coins(chat_id, user.id) < 5:
        await update.message.reply_text("❌ Non hai abbastanza $SDG (servono almeno 5 crediti)!", parse_mode="HTML")
        return

    add_user_coins(chat_id, user.id, -5)
    add_user_coins(chat_id, target_id, 5)
    DAILY_DONATIONS.add(donation_key)

    donor = f"@{html.escape(user.username)}" if user.username else html.escape(user.first_name or "Utente")
    await update.message.reply_text(f"🎁 <b>DONAZIONE EFFETTUATA!</b> {donor} ha donato <b>💳 5 $SDG</b> a {target_label}!", parse_mode="HTML")

# --- SISTEMA DI DEBUG ED ERRORI ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    err = context.error
    if isinstance(err, RetryAfter):
        ra = err.retry_after
        secs = int(ra.total_seconds()) if hasattr(ra, "total_seconds") else int(ra)
        logging.warning(f"Flood control: Telegram chiede di attendere {secs}s")
        cq = getattr(update, "callback_query", None)
        if cq:
            try:
                await cq.answer(f"⏳ Troppe richieste, riprova tra {secs + 1} secondi.", show_alert=True)
            except Exception:
                pass
        return
    if isinstance(err, BadRequest) and "not modified" in str(err).lower():
        return
    logging.error(msg="Eccezione non gestita durante l'update:", exc_info=err)
    print(f"🚨 ERRORE: {err}", flush=True)

async def catch_all_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cq = update.callback_query
    name = update.effective_user.first_name if update.effective_user else "?"
    logging.debug(f"Click ricevuto: {cq.data if cq else None} da {name}")

# --- HANDLER MESSAGGI GENERICI (TEXT ROUTER) ---
async def handle_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.from_user: return

    user = update.message.from_user
    chat_id_int = update.message.chat_id
    chat_id = str(chat_id_int)
    text = (update.message.text or "").strip()
    username_lower = user.username.lower() if user.username else ""
    uname = html.escape(user.first_name or "Giocatore")

    # Cache username -> id, usata da /dona per non scorrere tutto il registro
    if username_lower:
        context.bot_data.setdefault("usernames", {})[f"{chat_id}_{username_lower}"] = user.id

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

    # Prima parola del messaggio: i trigger testuali scattano solo se è la prima parola E c'è un bersaglio
    first = text.lower().split()[0] if text else ""
    has_mention = "@" in text
    has_reply = update.message.reply_to_message is not None

    # Handling Comandi Shop Testuali e Dona
    if first == "titolo" and (has_mention or has_reply):
        await apply_title_command(update, context)
        return
    if first == "perseguita" and (has_mention or has_reply):
        await apply_persecute_command(update, context)
        return
    if first == "dona" and (has_mention or has_reply):
        await donate_coins_command(update, context)
        return

    # Handling Sfide Unificate
    if first in CHALLENGES and (first != "sfido" or has_mention):
        if not game_enabled(STATUS_KEY[CHALLENGES[first]]):
            await update.message.reply_text(MAINT_GAME_TEXT)
            return
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
            del GHIGLIOTTINA_DUELS[chat_id_int]
            record_duel_result(chat_id_int, user.id, loser_id, duel["bet"])
            montepremi = duel["bet"] * 2
            end_keyboard = [
                rematch_row("ghig", duel),
                [InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{user.id}")]
            ]
            await update.message.reply_text(
                f"🏆 <b>GHIGLIOTTINA RISOLTA!</b> <b>{uname}</b> indovina <b>{duel['word']}</b> e vince +💳 {montepremi} $SDG!",
                reply_markup=InlineKeyboardMarkup(end_keyboard), parse_mode="HTML")
            return

    # Handling Risposta Duello Quiz 1v1
    if chat_id_int in QUIZ_DUELS_1V1 and QUIZ_DUELS_1V1[chat_id_int].get("active"):
        duel = QUIZ_DUELS_1V1[chat_id_int]
        if user.id in [duel["sfidante_id"], duel["target_id"]] and text_upper == duel.get("current_target"):
            duel["msg_id"] = None          # invalida il timer del round appena vinto
            duel["current_target"] = None  # nessun secondo punto per la stessa domanda
            if user.id == duel["sfidante_id"]: duel["p1_score"] += 1
            else: duel["p2_score"] += 1
            await update.message.reply_text(f"🎯 <b>RISPOSTA CORRETTA!</b> <b>{uname}</b> prende il punto del Round {duel['round']}!", parse_mode="HTML")
            await quiz1v1_advance(context.bot, chat_id_int)
            return

    # Handling Mastermind Express
    mm_key = f"{chat_id}_{user.id}"
    mm = MASTERMIND_GAMES.get(mm_key)
    if mm and len(text) == 3 and text.isdigit():
        if len(set(text)) < 3:
            await update.message.reply_text("❌ Il codice ha <b>3 cifre tutte diverse</b>: questo tentativo non conta.", parse_mode="HTML")
            return
        mm["last"] = time.time()
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
            reward = MASTERMIND_REWARDS.get(mm["attempts"], MASTERMIND_REWARDS[5])
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
        await update.message.reply_text(f"🎉 <b>QUIZ MULTI RISOLTO!</b> <b>{uname}</b> indovina <b>{q['target']}</b> e vince +💳 15 $SDG!", parse_mode="HTML")
        return

    quiz_key = f"{chat_id}_{user.id}"
    if quiz_key in QUIZ_GAMES and text_upper == QUIZ_GAMES[quiz_key]["target"]:
        q = QUIZ_GAMES[quiz_key]
        del QUIZ_GAMES[quiz_key]
        reward = quiz_reward(q["step"])
        add_user_coins(chat_id_int, user.id, reward)
        add_user_stat(chat_id_int, user.id, "quizzes_won", 1)
        await update.message.reply_text(f"🎉 <b>CORRETTO!</b> <b>{uname}</b> indovina <b>{q['target']}</b>! Vinti +💳 {reward} $SDG!", parse_mode="HTML")
        return

    # Handling Wordle
    game_key = f"{chat_id}_{user.id}"
    game = WORDLE_GAMES.get(game_key)
    if game and len(text_upper) == 5 and text_upper.isalpha():
        guess = norm_word(text_upper)          # "CITTÀ" -> "CITTA"
        if len(guess) != 5:
            return
        if guess not in WORD_SET:
            await update.message.reply_text("❌ Questa parola non è nel dizionario italiano: il tentativo non conta.", parse_mode="HTML")
            return
        game["last"] = time.time()
        game["attempts"] += 1
        secret = game["secret"]
        secret_letters = list(secret)
        res_colors = ["⬛"] * 5
        for i in range(5):
            if guess[i] == secret[i]:
                res_colors[i] = "🟩"
                secret_letters[i] = None
        for i in range(5):
            if res_colors[i] != "🟩" and guess[i] in secret_letters:
                res_colors[i] = "🟨"
                secret_letters[secret_letters.index(guess[i])] = None
        letters_row = "  ".join(list(guess))
        colors_row = " ".join(res_colors)
        game["history"].append(f"<code>{letters_row}</code>\n{colors_row}")
        res_text = "\n\n".join(game["history"])
        end_keyboard = [[InlineKeyboardButton("🔂 Rigioca", callback_data=f"start_wordle_{user.id}"), InlineKeyboardButton("🔙 HUB", callback_data=f"hub_main_{user.id}")] ]

        if guess == secret:
            del WORDLE_GAMES[game_key]
            reward = WORDLE_REWARDS.get(game["attempts"], WORDLE_REWARDS[5])
            record_single_result(chat_id_int, user.id, True, reward - 10, is_casino=False)
            add_user_coins(chat_id_int, user.id, reward)
            await update.message.reply_text(f"🎉 <b>ESATTO!</b> Parola: <b>{secret}</b>! Vinti +💳 {reward} $SDG!\n\n{res_text}", reply_markup=InlineKeyboardMarkup(end_keyboard), parse_mode="HTML")
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
            try:
                await context.bot.set_message_reaction(chat_id=chat_id, message_id=update.message.message_id, reaction=TARGET_MAP[username_lower])
            except Exception as e:
                logging.warning(f"Auto-troll: reazione non riuscita: {e}")

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
    app.add_handler(CommandHandler("admin", admin_dashboard))
    app.add_handler(CallbackQueryHandler(admin_callback, pattern="^adm_"))

def register_text(app):
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_messages))
