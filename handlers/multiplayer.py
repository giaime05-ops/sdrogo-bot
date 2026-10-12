"""Giochi 1v1 unificati: Roulette, High/Low, Ghigliottina, Quiz 1v1, Dadi e Tris con scommesse."""
import asyncio
import html
import logging
import random
import time

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import RetryAfter, BadRequest
from telegram.ext import CallbackQueryHandler, ContextTypes

from database_quiz import GHIGLIOTTINA_DB, CATEGORIE_QUIZ
from state import (
    ACTIVE_DUELS, HIGHLOW_DUELS, GHIGLIOTTINA_DUELS, QUIZ_DUELS_1V1,
    TIC_TAC_TOE_GAMES, DICE_DUELS,
    game_enabled, MAINT_GAME_TEXT,
)
from storage import get_user_coins, add_user_coins, get_user_data_dict, save_db
from utils import verify_user_lock

CHALLENGE_TTL = 120   # secondi dopo i quali una sfida mai accettata scade
ACTIVE_TTL = 900      # secondi di inattività dopo i quali una partita accettata viene chiusa con rimborso
CONFIG_TTL = 600      # secondi dopo i quali un menu di configurazione sfida viene scartato
MAX_WAIT = 10         # massima attesa (s) per un RetryAfter prima di passare al ripiego

STORES = {
    "dice": DICE_DUELS, "ttt": TIC_TAC_TOE_GAMES, "roulette": ACTIVE_DUELS,
    "highlow": HIGHLOW_DUELS, "ghig": GHIGLIOTTINA_DUELS, "quiz1v1": QUIZ_DUELS_1V1,
}

# Tipo di sfida (chiave di STORES) -> chiave dell'interruttore in GAME_STATUS
STATUS_KEY = {
    "dice": "dice", "ttt": "ttt", "roulette": "roulette1v1",
    "highlow": "highlow", "ghig": "ghig", "quiz1v1": "quiz1v1",
}

PENDING_CHALLENGES = {}
REMATCH_NAMES = {}   # user_id -> username (minuscolo), per il tasto Rigioca
_TASKS = set()


# --- HELPER ROBUSTI (flood control, messaggi non modificabili) ---
def nm(user) -> str:
    return html.escape(user.first_name or user.username or "Giocatore")


def spawn(coro):
    """create_task con riferimento forte: i timer non vengono raccolti dal garbage collector."""
    t = asyncio.create_task(coro)
    _TASKS.add(t)
    t.add_done_callback(_TASKS.discard)
    return t


def _retry_secs(e) -> int:
    ra = e.retry_after
    return int(ra.total_seconds()) if hasattr(ra, "total_seconds") else int(ra)


async def safe_send(bot, chat_id: int, text: str, keyboard=None):
    """Invia un messaggio riprovando una volta dopo un flood control. Restituisce il Message o None."""
    markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    for attempt in range(2):
        try:
            return await bot.send_message(chat_id=chat_id, text=text, reply_markup=markup, parse_mode="HTML")
        except RetryAfter as e:
            secs = _retry_secs(e)
            if attempt == 0 and secs <= MAX_WAIT:
                await asyncio.sleep(secs + 1)
                continue
            logging.warning(f"safe_send: flood control ({secs}s) in chat {chat_id}")
            return None
        except Exception as e:
            logging.error(f"safe_send: errore in chat {chat_id}: {e}")
            return None
    return None


async def safe_edit(query, text: str, keyboard=None) -> bool:
    """Modifica il messaggio; se non riesce (flood, messaggio troppo vecchio...) ne invia uno nuovo,
    così la partita non resta mai senza pulsanti."""
    markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    try:
        await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
        return True
    except BadRequest as e:
        if "not modified" in str(e).lower():
            return True
    except RetryAfter as e:
        secs = _retry_secs(e)
        if secs <= MAX_WAIT:
            await asyncio.sleep(secs + 1)
            try:
                await query.edit_message_text(text, reply_markup=markup, parse_mode="HTML")
                return True
            except Exception:
                pass
    except Exception as e:
        logging.warning(f"safe_edit: modifica fallita: {e}")
    return (await safe_send(query.get_bot(), query.message.chat_id, text, keyboard)) is not None


# --- TASTO RIGIOCA ---
def rematch_row(game_type: str, g: dict) -> list:
    """Riga di pulsanti 'Rigioca' per la schermata finale di un duello.
    Memorizza gli username dei due giocatori e codifica puntata e round nel callback."""
    if g.get("sfidante_id"):
        REMATCH_NAMES[g["sfidante_id"]] = g.get("sfidante_username", "")
    if g.get("target_id"):
        REMATCH_NAMES[g["target_id"]] = g.get("target_username", "")
    data = f"rem_{game_type}_{g['sfidante_id']}_{g['target_id']}_{g['bet']}_{g.get('rounds', 1)}"
    return [InlineKeyboardButton("🔁 Rigioca", callback_data=data)]


async def handle_rematch_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """rem_{gioco}_{id1}_{id2}_{puntata}_{round}: chi preme diventa lo sfidante, l'altro è lo sfidato."""
    query = update.callback_query
    parts = query.data.split("_")
    try:
        game_type = parts[1]
        id1, id2 = int(parts[2]), int(parts[3])
        bet, rounds = int(parts[4]), int(parts[5])
    except (IndexError, ValueError):
        await query.answer()
        return

    user = query.from_user
    chat_id = query.message.chat_id

    if game_type not in STORES:
        await query.answer()
        return
    if user.id not in (id1, id2):
        await query.answer("🛑 Solo i due giocatori possono chiedere la rivincita!", show_alert=True)
        return
    if not game_enabled(STATUS_KEY[game_type]):
        await query.answer(MAINT_GAME_TEXT, show_alert=True)
        return

    opp_id = id2 if user.id == id1 else id1
    opp_username = REMATCH_NAMES.get(opp_id, "")
    if not opp_username:
        try:
            member = await context.bot.get_chat_member(chat_id, opp_id)
            opp_username = (member.user.username or "").lower()
        except Exception:
            opp_username = ""
    if not opp_username:
        await query.answer("❌ L'avversario non ha un username: riscrivi il comando di sfida in chat.", show_alert=True)
        return
    if is_busy(STORES[game_type], chat_id):
        await query.answer("❌ C'è già una sfida di questo tipo in corso!", show_alert=True)
        return

    await query.answer()
    PENDING_CHALLENGES[user.id] = {
        "game": game_type,
        "target_username": opp_username,
        "chat_id": chat_id,
        "bet": max(1, bet),
        "rounds": rounds,
        "ts": time.time()
    }
    # nuovo messaggio: il risultato resta visibile e anche l'altro giocatore può chiedere la rivincita
    await show_challenge_config_message(update, context, user.id, new_message=True)


# --- GESTIONE SFIDE SCADUTE / ABBANDONATE ---
def _expired(g: dict) -> bool:
    age = time.time() - g.get("ts", 0)
    return age > (ACTIVE_TTL if g.get("target_id") else CHALLENGE_TTL)


def _refund(chat_id: int, g: dict):
    """Rimborsa le puntate di una partita accettata (le puntate vengono scalate solo all'accettazione)."""
    if g.get("target_id") and g.get("bet"):
        add_user_coins(chat_id, g["sfidante_id"], g["bet"])
        add_user_coins(chat_id, g["target_id"], g["bet"])


def is_busy(store: dict, chat_id: int) -> bool:
    g = store.get(chat_id)
    if not g:
        return False
    if _expired(g):
        _refund(chat_id, g)          # se era già stata accettata, le puntate tornano ai giocatori
        del store[chat_id]
        return False
    return True


async def cleanup_stale_games(context: ContextTypes.DEFAULT_TYPE):
    """Job periodico: chiude le sfide scadute e le partite abbandonate (con rimborso)."""
    for store in STORES.values():
        for chat_id, g in list(store.items()):
            if not _expired(g):
                continue
            started = bool(g.get("target_id"))
            _refund(chat_id, g)
            store.pop(chat_id, None)
            if started:
                await safe_send(context.bot, chat_id, "⏰ Partita abbandonata: sfida chiusa per inattività. Puntate rimborsate.")
    now = time.time()
    for uid, cfg in list(PENDING_CHALLENGES.items()):
        if now - cfg.get("ts", now) > CONFIG_TTL:
            del PENDING_CHALLENGES[uid]


def record_duel_result(chat_id: int, winner_id: int, loser_id: int, bet_amount: int):
    for uid, won in [(winner_id, True), (loser_id, False)]:
        if not uid:
            continue
        u_data = get_user_data_dict(chat_id, uid)
        u_data["duels_played"] = u_data.get("duels_played", 0) + 1

        if won:
            u_data["duels_wins"] = u_data.get("duels_wins", 0) + 1
            u_data["duels_won_count"] = u_data.get("duels_won_count", 0) + 1
            u_data["net_profit"] = u_data.get("net_profit", 0) + bet_amount
            add_user_coins(chat_id, uid, bet_amount * 2)
        else:
            u_data["net_profit"] = u_data.get("net_profit", 0) - bet_amount
    save_db()


# --- HELPER ACCETTAZIONE / RIFIUTO UNIFICATI ---
async def try_accept(query, game, chat_id: int, user, game_type: str) -> bool:
    """Valida l'accettazione e blocca le puntate. True = il duello può partire."""
    if game.get("target_id"):
        await query.answer()
        return False
    if not game_enabled(STATUS_KEY[game_type]):
        await query.answer(MAINT_GAME_TEXT, show_alert=True)
        return False
    if (user.username or "").lower() != game["target_username"]:
        await query.answer("❌ Solo lo sfidato può accettare!", show_alert=True)
        return False
    bet = game["bet"]
    if get_user_coins(chat_id, game["sfidante_id"]) < bet:
        await query.answer("❌ Lo sfidante non ha più abbastanza $SDG!", show_alert=True)
        return False
    if get_user_coins(chat_id, user.id) < bet:
        await query.answer("❌ Non hai abbastanza $SDG!", show_alert=True)
        return False
    await query.answer()
    add_user_coins(chat_id, game["sfidante_id"], -bet)
    add_user_coins(chat_id, user.id, -bet)
    game["target_id"] = user.id
    game["target_name"] = nm(user)
    game["ts"] = time.time()
    return True


async def try_refuse(query, game, user) -> bool:
    """Solo lo sfidato o lo sfidante possono rifiutare, e solo prima dell'inizio."""
    if game.get("target_id"):
        await query.answer("⚠️ La partita è già iniziata!", show_alert=True)
        return False
    is_target = (user.username or "").lower() == game["target_username"]
    if not (is_target or user.id == game["sfidante_id"]):
        await query.answer("❌ Non è la tua sfida!", show_alert=True)
        return False
    await query.answer()
    return True


# --- CONFIGURAZIONE SFIDA ---
async def setup_challenge_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, game_type: str, target_username: str, chat_id: int, user):
    PENDING_CHALLENGES[user.id] = {
        "game": game_type,
        "target_username": target_username,
        "chat_id": chat_id,
        "bet": 10,
        "rounds": 1,
        "ts": time.time()
    }
    await show_challenge_config_message(update, context, user.id)


def _config_view(user_id: int):
    cfg = PENDING_CHALLENGES[user_id]
    text = (
        f"⚔️ <b>CONFIGURA SFIDA: {cfg['game'].upper()}</b>\n\n"
        f"🎯 Avversario: <code>@{html.escape(cfg['target_username'])}</code>\n"
        f"💰 Puntata selezionata: <b>💳 {cfg['bet']} $SDG</b>\n"
    )
    # Mostra i round solo per i giochi che li supportano
    if cfg['game'] in ['dice', 'quiz1v1']:
        text += f"🔄 Round/Partite: <b>{cfg['rounds']}</b>\n\n"
    else:
        text += "\n"

    text += "<i>Scegli la puntata, poi lancia la sfida:</i>"

    owner_id = user_id
    keyboard = [
        [InlineKeyboardButton("10", callback_data=f"cfg_bet_10_{user_id}"), InlineKeyboardButton("25", callback_data=f"cfg_bet_25_{user_id}"), InlineKeyboardButton("50", callback_data=f"cfg_bet_50_{user_id}"), InlineKeyboardButton("100", callback_data=f"cfg_bet_100_{user_id}"), InlineKeyboardButton("🚀 ALL-IN", callback_data=f"cfg_bet_all_{user_id}")],
    ]
    if cfg['game'] in ['dice', 'quiz1v1']:
        keyboard.append([InlineKeyboardButton("🔄 1 Round", callback_data=f"cfg_rnd_1_{user_id}"), InlineKeyboardButton("🔄 3 Round", callback_data=f"cfg_rnd_3_{user_id}"), InlineKeyboardButton("🔄 5 Round", callback_data=f"cfg_rnd_5_{user_id}"), InlineKeyboardButton("🔄 7 Round", callback_data=f"cfg_rnd_7_{user_id}")])

    keyboard.extend([
        [InlineKeyboardButton("🚀 LANCIA LA SFIDA IN CHAT", callback_data=f"cfg_launch_{user_id}")],
        [InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{owner_id}")]
    ])
    return text, keyboard


async def show_challenge_config_message(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, new_message: bool = False):
    text, keyboard = _config_view(user_id)

    if new_message:
        await safe_send(context.bot, PENDING_CHALLENGES[user_id]["chat_id"], text, keyboard)
    elif update.callback_query:
        try:
            await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        except Exception:
            pass
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")


async def handle_challenge_config_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    parts = query.data.split("_")
    action = parts[1]
    user_id = int(parts[-1])

    if query.from_user.id != user_id:
        await query.answer("🛑 Questa configurazione non è tua!", show_alert=True)
        return

    if user_id not in PENDING_CHALLENGES:
        await query.answer()
        try:
            await query.edit_message_text("❌ Sessione scaduta.")
        except Exception:
            pass
        return

    cfg = PENDING_CHALLENGES[user_id]
    chat_id = cfg["chat_id"]

    if action == "bet":
        await query.answer()
        val = parts[2]
        if val == "all":
            cfg["bet"] = get_user_coins(chat_id, user_id)
        else:
            cfg["bet"] = int(val)
        cfg["ts"] = time.time()
        await show_challenge_config_message(update, context, user_id)

    elif action == "rnd":
        await query.answer()
        cfg["rounds"] = int(parts[2])
        cfg["ts"] = time.time()
        await show_challenge_config_message(update, context, user_id)

    elif action == "launch":
        bet = cfg["bet"]
        game = cfg["game"]
        if not game_enabled(STATUS_KEY[game]):
            await query.answer(MAINT_GAME_TEXT, show_alert=True)
            return
        if bet < 1:
            await query.answer("❌ Puntata non valida!", show_alert=True)
            return
        if get_user_coins(chat_id, user_id) < bet:
            await query.answer("❌ Non hai abbastanza $SDG per questa puntata!", show_alert=True)
            return
        if is_busy(STORES[game], chat_id):
            await query.answer("❌ C'è già una sfida di questo tipo in corso!", show_alert=True)
            return

        await query.answer()
        target = cfg["target_username"]
        t_esc = html.escape(target)
        rounds = cfg["rounds"]
        user = query.from_user
        uname = nm(user)
        del PENDING_CHALLENGES[user_id]

        base = {
            "sfidante_id": user.id, "sfidante_name": uname,
            "sfidante_username": (user.username or "").lower(),
            "target_username": target, "bet": bet, "rounds": rounds, "ts": time.time()
        }

        if game == "dice":
            DICE_DUELS[chat_id] = {**base, "active": False,
                "p1_wins": 0, "p2_wins": 0, "current_round": 1,
                "p1_val": None, "p2_val": None, "turno_id": None}
            keyboard = [[InlineKeyboardButton("🎲 Accetta Dadi", callback_data="dice_accetta"), InlineKeyboardButton("🐔 Rifiuta", callback_data="dice_rifiuta")]]
            text = f"🎲 <b>SFIDA A DADI 1v1 INTERATTIVA</b>\n\n<b>{uname}</b> sfida <b>@{t_esc}</b>!\n💰 Puntata: <code>💳 {bet} $SDG</code> | 🔄 Partite: <b>{rounds}</b>\n\n@{t_esc}, accetti?"

        elif game == "ttt":
            TIC_TAC_TOE_GAMES[chat_id] = {**base, "active": False, "board": [" "] * 9, "turn": "X"}
            keyboard = [[InlineKeyboardButton("❌ Accetta Tris", callback_data="ttt_accetta"), InlineKeyboardButton("🐔 Rifiuta", callback_data="ttt_rifiuta")]]
            text = f"❌⭕ <b>SFIDA TRIS 1v1</b>\n\n<b>{uname}</b> sfida <b>@{t_esc}</b>!\n💰 Puntata: <code>💳 {bet} $SDG</code>\n\n@{t_esc}, accetti?"

        elif game == "roulette":
            ACTIVE_DUELS[chat_id] = {**base, "chambers": [False]*6, "current_chamber": 0}
            ACTIVE_DUELS[chat_id]["chambers"][random.randint(0, 5)] = True
            keyboard = [[InlineKeyboardButton("🎯 Accetta Roulette", callback_data="roulette_accetta"), InlineKeyboardButton("🐔 Rifiuta", callback_data="roulette_rifiuta")]]
            text = f"🔫 <b>ROULETTE RUSSA 1v1</b>\n\n<b>{uname}</b> sfida <b>@{t_esc}</b>!\n💰 Puntata: <code>💳 {bet} $SDG</code>\n\n@{t_esc}, accetti?"

        elif game == "highlow":
            HIGHLOW_DUELS[chat_id] = {**base, "val": 0, "turno_id": None}
            keyboard = [[InlineKeyboardButton("🎲 Accetta High/Low", callback_data="hl_accetta"), InlineKeyboardButton("🐔 Rifiuta", callback_data="hl_rifiuta")]]
            text = f"🎲 <b>HIGH / LOW 1v1</b>\n\n<b>{uname}</b> sfida <b>@{t_esc}</b>!\n💰 Puntata: <code>💳 {bet} $SDG</code>\n\n@{t_esc}, accetti?"

        elif game == "ghig":
            GHIGLIOTTINA_DUELS[chat_id] = {**base, "active": False}
            keyboard = [[InlineKeyboardButton("🪓 Accetta Ghigliottina", callback_data="ghig_accetta"), InlineKeyboardButton("🐔 Rifiuta", callback_data="ghig_rifiuta")]]
            text = f"🪓 <b>GHIGLIOTTINA 1v1</b>\n\n<b>{uname}</b> sfida <b>@{t_esc}</b>!\n💰 Puntata: <code>💳 {bet} $SDG</code>\n\n@{t_esc}, accetti?"

        else:  # quiz1v1
            QUIZ_DUELS_1V1[chat_id] = {**base, "active": False}
            keyboard = [
                [InlineKeyboardButton("⚽ Calcio", callback_data="q1v1_cat_CALCIO"), InlineKeyboardButton("🏎️ F1", callback_data="q1v1_cat_F1")],
                [InlineKeyboardButton("🦸 Marvel", callback_data="q1v1_cat_MARVEL"), InlineKeyboardButton("🎬 Cinema", callback_data="q1v1_cat_CINEMA")],
                [InlineKeyboardButton("📺 Serie TV", callback_data="q1v1_cat_SERIE"), InlineKeyboardButton("🗺️ Paesi", callback_data="q1v1_cat_PAESI")],
                [InlineKeyboardButton("🏮 Anime", callback_data="q1v1_cat_ANIME"), InlineKeyboardButton("🏷️ Brand", callback_data="q1v1_cat_BRANDS")],
                [InlineKeyboardButton("📜 Personaggi", callback_data="q1v1_cat_PERSONAGGI"), InlineKeyboardButton("🎵 Canzoni", callback_data="q1v1_cat_CANZONI")]
            ]
            text = f"⚔️ <b>DUELLO QUIZ 1v1</b>\n\n<b>{uname}</b> sfida <b>@{t_esc}</b>!\n💰 Puntata: <code>💳 {bet} $SDG</code> | 🔄 Round: <b>{rounds}</b>\n\nSeleziona la categoria:"

        # il timestamp è già nel dizionario: la sfida scade anche se la modifica del messaggio fallisce
        await safe_edit(query, text, keyboard)


# --- SCHERMATE DI PREPARAZIONE (DALL'HUB) ---
async def _prep_screen(update: Update, status_key: str, text: str):
    """Schermata con le istruzioni per lanciare una sfida a parole. Controlla lock e interruttore del gioco."""
    query = update.callback_query
    owner_id = int(query.data.split("_")[-1])
    if not await verify_user_lock(query, owner_id): return
    if not game_enabled(status_key):
        await query.answer(MAINT_GAME_TEXT, show_alert=True)
        return
    await query.answer()
    keyboard = [[InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{owner_id}")]]
    try:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    except Exception:
        pass

async def start_dice_prep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _prep_screen(update, "dice", "🎲 <b>DADI 1v1</b>\n\nScrivi in chat il comando:\n👉 <code>sfidodadi @username</code>")

async def start_ttt_prep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _prep_screen(update, "ttt", "❌⭕ <b>TRIS 1v1</b>\n\nScrivi in chat il comando:\n👉 <code>sfidotris @username</code>")

async def start_roulette_prep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _prep_screen(update, "roulette1v1", "🎯 <b>ROULETTE RUSSA 1v1</b>\n\nScrivi in chat il comando:\n👉 <code>sfidoroulette @username</code>")

async def start_highlow_prep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _prep_screen(update, "highlow", "🎲 <b>HIGH / LOW 1v1</b>\n\nScrivi in chat il comando:\n👉 <code>sfidohighlow @username</code>")

async def start_ghigliottina_prep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _prep_screen(update, "ghig", "🪓 <b>GHIGLIOTTINA 1v1</b>\n\nScrivi in chat il comando:\n👉 <code>sfidoghigliottina @username</code>")

async def start_quiz1v1_prep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _prep_screen(update, "quiz1v1", "⚔️ <b>DUELLO QUIZ 1v1</b>\n\nScrivi in chat il comando:\n👉 <code>sfidoquiz @username</code>")


# --- DADI ---
async def handle_dice_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat_id
    user = query.from_user

    if chat_id not in DICE_DUELS:
        await query.answer("⚠️ Sfida non attiva.", show_alert=True)
        return
    game = DICE_DUELS[chat_id]

    if query.data == "dice_accetta":
        if not await try_accept(query, game, chat_id, user, "dice"): return

        game["active"] = True
        game["turno_id"] = game["sfidante_id"]

        keyboard = [[InlineKeyboardButton("🎲 Lancia il Dado", callback_data="dice_roll")]]
        await safe_edit(
            query,
            f"🎲 <b>DUELLO DADI INIZIATO!</b> Montepremi: <code>💳 {game['bet']*2} $SDG</code>\n\n"
            f"📊 Punteggio: {game['sfidante_name']} (0) - {game['target_name']} (0)\n"
            f"Turno di <b>{game['sfidante_name']}</b>:", keyboard
        )

    elif query.data == "dice_rifiuta":
        if not await try_refuse(query, game, user): return
        del DICE_DUELS[chat_id]
        await safe_edit(query, "🐔 Sfida rifiutata!")

    elif query.data == "dice_roll":
        if user.id != game.get("turno_id"):
            await query.answer("⏳ Non è il tuo turno di lanciare!", show_alert=True)
            return
        # PRIMA di qualsiasi await: blocca i doppi click (l'handler gira con block=False)
        game["turno_id"] = None
        game["ts"] = time.time()
        await query.answer()
        try:
            await query.edit_message_reply_markup(reply_markup=None)   # il vecchio bottone sparisce
        except Exception:
            pass

        roll_kb = [[InlineKeyboardButton("🎲 Lancia il Dado", callback_data="dice_roll")]]

        try:
            dice_msg = await context.bot.send_dice(chat_id=chat_id, emoji="🎲")
            val = dice_msg.dice.value
        except Exception as e:
            logging.error(f"Errore send_dice: {e}")
            game["turno_id"] = user.id     # il lancio non è avvenuto: restituisce il turno
            await safe_send(context.bot, chat_id, f"⚠️ Lancio non riuscito. Riprova, <b>{nm(user)}</b>!", roll_kb)
            return
        await asyncio.sleep(3)             # durata dell'animazione del dado

        if DICE_DUELS.get(chat_id) is not game:
            return   # partita chiusa nel frattempo (reset admin o scadenza)

        if user.id == game["sfidante_id"]:
            game["p1_val"] = val
            game["turno_id"] = game["target_id"]
            await safe_send(
                context.bot, chat_id,
                f"🎲 <b>ROUND {game['current_round']} / {game['rounds']}</b>\n\n"
                f"👤 {game['sfidante_name']} ha fatto: <b>{val}</b> 🎲\n"
                f"📊 Punteggio: {game['sfidante_name']} ({game['p1_wins']}) - {game['target_name']} ({game['p2_wins']})\n\n"
                f"Tocca a <b>{game['target_name']}</b> lanciare!", roll_kb
            )
        else:
            game["p2_val"] = val
            v1 = game["p1_val"]
            v2 = game["p2_val"]

            if v1 > v2:
                game["p1_wins"] += 1
                round_msg = f"🏆 <b>Round {game['current_round']} vinto da {game['sfidante_name']}!</b>"
            elif v2 > v1:
                game["p2_wins"] += 1
                round_msg = f"🏆 <b>Round {game['current_round']} vinto da {game['target_name']}!</b>"
            else:
                round_msg = f"⚖️ <b>Round {game['current_round']} pari!</b>"

            res_text = f"👤 {game['sfidante_name']}: <b>{v1}</b> | 👤 {game['target_name']}: <b>{v2}</b>\n{round_msg}\n📊 Totale: {game['sfidante_name']} ({game['p1_wins']}) - {game['target_name']} ({game['p2_wins']})"

            wins_needed = (game["rounds"] // 2) + 1
            if game["p1_wins"] >= wins_needed or game["p2_wins"] >= wins_needed or game["current_round"] >= game["rounds"]:
                winner_id = game["sfidante_id"] if game["p1_wins"] > game["p2_wins"] else (game["target_id"] if game["p2_wins"] > game["p1_wins"] else None)
                montepremi = game["bet"] * 2

                w_id_btn = winner_id if winner_id else game["sfidante_id"]
                keyboard = [
                    rematch_row("dice", game),
                    [InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{w_id_btn}")]
                ]

                if winner_id:
                    loser_id = game["target_id"] if winner_id == game["sfidante_id"] else game["sfidante_id"]
                    w_name = game["sfidante_name"] if winner_id == game["sfidante_id"] else game["target_name"]
                    record_duel_result(chat_id, winner_id, loser_id, game["bet"])
                    msg = res_text + f"\n\n👑 <b>VITTORIA FINALE! {w_name}</b> vince <b>+💳 {montepremi} $SDG</b>!"
                else:
                    add_user_coins(chat_id, game["sfidante_id"], game["bet"])
                    add_user_coins(chat_id, game["target_id"], game["bet"])
                    msg = res_text + "\n\n⚖️ <b>PAREGGIO FINALE!</b> Puntate rimborsate."

                del DICE_DUELS[chat_id]
                await safe_send(context.bot, chat_id, msg, keyboard)
            else:
                game["current_round"] += 1
                game["turno_id"] = game["sfidante_id"]
                await safe_send(
                    context.bot, chat_id,
                    res_text + f"\n\n🔄 <b>Inizia il Round {game['current_round']}!</b>\nTocca a <b>{game['sfidante_name']}</b>:",
                    roll_kb
                )


# --- TRIS ---
async def handle_ttt_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat_id
    user = query.from_user

    if chat_id not in TIC_TAC_TOE_GAMES:
        await query.answer("⚠️ Sfida non attiva.", show_alert=True)
        return
    game = TIC_TAC_TOE_GAMES[chat_id]

    if query.data == "ttt_accetta":
        if not await try_accept(query, game, chat_id, user, "ttt"): return
        game["active"] = True
        await update_ttt_board(query, game, f"Inizia il Tris! Tocca a <b>{game['sfidante_name']}</b> (❌)")

    elif query.data == "ttt_rifiuta":
        if not await try_refuse(query, game, user): return
        del TIC_TAC_TOE_GAMES[chat_id]
        await safe_edit(query, "🐔 Rifiutato!")

    elif query.data.startswith("ttt_cell_"):
        if not game.get("active"):
            await query.answer()
            return
        idx = int(query.data.split("_")[2])
        if user.id != (game["sfidante_id"] if game["turn"] == "X" else game["target_id"]):
            await query.answer("✋ Non è il tuo turno!", show_alert=True)
            return
        if game["board"][idx] != " ":
            await query.answer("❌ Casella già occupata!", show_alert=True)
            return
        await query.answer()
        game["ts"] = time.time()
        game["board"][idx] = game["turn"]
        winner = check_ttt_winner(game["board"])
        if winner or " " not in game["board"]:
            montepremi = game["bet"] * 2

            if winner:
                w_id = game["sfidante_id"] if winner == "X" else game["target_id"]
                l_id = game["target_id"] if winner == "X" else game["sfidante_id"]
                record_duel_result(chat_id, w_id, l_id, game["bet"])
                end_text = f"🏆 <b>TRIS VINTO!</b> Montepremi: +💳 {montepremi} $SDG"
                w_id_btn = w_id
            else:
                add_user_coins(chat_id, game["sfidante_id"], game["bet"])
                add_user_coins(chat_id, game["target_id"], game["bet"])
                end_text = "⚖️ <b>PAREGGIO!</b> Puntate rimborsate."
                w_id_btn = game["sfidante_id"]

            del TIC_TAC_TOE_GAMES[chat_id]
            await update_ttt_board(query, game, end_text, finished=True, btn_id=w_id_btn, rematch=rematch_row("ttt", game))
        else:
            game["turn"] = "O" if game["turn"] == "X" else "X"
            next_name = game["sfidante_name"] if game["turn"] == "X" else game["target_name"]
            symbol = "❌" if game["turn"] == "X" else "⭕"
            await update_ttt_board(query, game, f"Turno di <b>{next_name}</b> ({symbol})")

def check_ttt_winner(board):
    for a, b, c in [(0,1,2),(3,4,5),(6,7,8),(0,3,6),(1,4,7),(2,5,8),(0,4,8),(2,4,6)]:
        if board[a] == board[b] == board[c] and board[a] != " ": return board[a]
    return None

async def update_ttt_board(query, game, status, finished=False, btn_id=None, rematch=None):
    symbols = {"X": "❌", "O": "⭕", " ": "◻️"}
    keyboard = []
    if not finished:
        for r in range(3):
            keyboard.append([InlineKeyboardButton(symbols[game['board'][r*3+c]], callback_data=f"ttt_cell_{r*3+c}") for c in range(3)])
    else:
        if rematch:
            keyboard.append(rematch)
        if btn_id:
            keyboard.append([InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{btn_id}")])
    await safe_edit(query, f"❌⭕ <b>TRIS</b>\n{status}", keyboard)


# --- ROULETTE ---
async def gestione_bottoni_roulette(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat_id
    user = query.from_user
    if chat_id not in ACTIVE_DUELS:
        await query.answer("⚠️ Sfida non attiva.", show_alert=True)
        return
    duel = ACTIVE_DUELS[chat_id]

    if query.data == "roulette_accetta":
        if not await try_accept(query, duel, chat_id, user, "roulette"): return
        duel["turno_id"] = random.choice([duel["sfidante_id"], user.id])
        first_shooter = duel["sfidante_name"] if duel["turno_id"] == duel["sfidante_id"] else duel["target_name"]

        keyboard = [[InlineKeyboardButton("🔫 SPARA!", callback_data="roulette_spara")]]
        await safe_edit(query, f"🔫 <b>ROULETTE INIZIATA!</b> Montepremi: {duel['bet']*2} $SDG\n\n👉 Inizia a sparare <b>{first_shooter}</b>!", keyboard)

    elif query.data == "roulette_rifiuta":
        if not await try_refuse(query, duel, user): return
        del ACTIVE_DUELS[chat_id]
        await safe_edit(query, "🐔 Sfida rifiutata!")

    elif query.data == "roulette_spara":
        if user.id != duel.get("turno_id"):
            await query.answer("✋ Non è il tuo turno!", show_alert=True)
            return
        await query.answer()
        duel["ts"] = time.time()

        is_bullet = duel["chambers"][duel["current_chamber"]]
        duel["current_chamber"] += 1

        if is_bullet:
            montepremi = duel["bet"] * 2
            winner_id = duel["target_id"] if user.id == duel["sfidante_id"] else duel["sfidante_id"]
            winner_name = duel["target_name"] if user.id == duel["sfidante_id"] else duel["sfidante_name"]
            loser_id = user.id
            record_duel_result(chat_id, winner_id, loser_id, duel["bet"])
            del ACTIVE_DUELS[chat_id]

            keyboard = [
                rematch_row("roulette", duel),
                [InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{winner_id}")]
            ]
            await safe_edit(query, f"💥 <b>BAM! {nm(user)} è morto!</b> <b>{winner_name}</b> vince <b>+💳 {montepremi} $SDG</b>!", keyboard)
        else:
            duel["turno_id"] = duel["target_id"] if user.id == duel["sfidante_id"] else duel["sfidante_id"]
            next_shooter = duel["target_name"] if duel["turno_id"] == duel["target_id"] else duel["sfidante_name"]
            keyboard = [[InlineKeyboardButton("🔫 SPARA!", callback_data="roulette_spara")]]
            await safe_edit(query, f"<i>*click!*</i> {nm(user)} è salvo.\n\n👉 Tocca a <b>{next_shooter}</b>!", keyboard)


# --- HIGH / LOW (morte improvvisa: chi sbaglia perde, quindi non usa i round) ---
async def handle_highlow_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat_id
    user = query.from_user
    if chat_id not in HIGHLOW_DUELS:
        await query.answer("⚠️ Sfida non attiva.", show_alert=True)
        return
    game = HIGHLOW_DUELS[chat_id]

    if query.data == "hl_accetta":
        if not await try_accept(query, game, chat_id, user, "highlow"): return
        game["turno_id"] = random.choice([game["sfidante_id"], user.id])
        game["val"] = random.randint(2, 11)
        first_shooter = game["sfidante_name"] if game["turno_id"] == game["sfidante_id"] else game["target_name"]

        keyboard = [[InlineKeyboardButton("📈 PIÙ ALTO", callback_data="hl_guess_high"), InlineKeyboardButton("📉 PIÙ BASSO", callback_data="hl_guess_low")]]
        await safe_edit(query, f"🎲 Numero di partenza: <b>{game['val']}</b>\n\n👉 Tocca a <b>{first_shooter}</b>: il prossimo sarà più alto o più basso?", keyboard)

    elif query.data == "hl_rifiuta":
        if not await try_refuse(query, game, user): return
        del HIGHLOW_DUELS[chat_id]
        await safe_edit(query, "🐔 Rifiutato!")

    elif query.data in ("hl_guess_high", "hl_guess_low"):
        if user.id != game.get("turno_id"):
            await query.answer("✋ Non è il tuo turno!", show_alert=True)
            return
        await query.answer()
        game["ts"] = time.time()

        old_val = game["val"]
        new_val = random.randint(1, 12)
        while new_val == old_val:
            new_val = random.randint(1, 12)

        won = (query.data == "hl_guess_high" and new_val > old_val) or \
              (query.data == "hl_guess_low" and new_val < old_val)
        names = {game["sfidante_id"]: game["sfidante_name"], game["target_id"]: game["target_name"]}

        if won:
            game["val"] = new_val
            game["turno_id"] = game["target_id"] if user.id == game["sfidante_id"] else game["sfidante_id"]
            keyboard = [[InlineKeyboardButton("📈 PIÙ ALTO", callback_data="hl_guess_high"),
                         InlineKeyboardButton("📉 PIÙ BASSO", callback_data="hl_guess_low")]]
            await safe_edit(
                query,
                f"✅ <b>GIUSTO! Era {new_val}!</b> {nm(user)} si salva.\n\n"
                f"🎯 Nuovo numero: <b>{new_val}</b>\n"
                f"👉 Tocca a <b>{names[game['turno_id']]}</b>: più alto o più basso?", keyboard)
        else:
            winner_id = game["target_id"] if user.id == game["sfidante_id"] else game["sfidante_id"]
            record_duel_result(chat_id, winner_id, user.id, game["bet"])
            del HIGHLOW_DUELS[chat_id]
            keyboard = [
                rematch_row("highlow", game),
                [InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{winner_id}")]
            ]
            await safe_edit(
                query,
                f"💥 <b>ERRATO! Era {new_val}!</b>\n"
                f"💀 {nm(user)} perde: <b>{names[winner_id]}</b> vince <b>+💳 {game['bet']*2} $SDG</b>!", keyboard)


# --- GHIGLIOTTINA ---
async def handle_ghigliottina_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat_id
    user = query.from_user
    if chat_id not in GHIGLIOTTINA_DUELS:
        await query.answer("⚠️ Sfida non attiva.", show_alert=True)
        return
    duel = GHIGLIOTTINA_DUELS[chat_id]

    if query.data == "ghig_accetta":
        if not await try_accept(query, duel, chat_id, user, "ghig"): return
        item = random.choice(GHIGLIOTTINA_DB)
        duel["word"] = item["target"]
        duel["active"] = True
        indizi_formatted = " • ".join(item["indizi"])
        duel["msg_id"] = query.message.message_id
        await safe_edit(
            query,
            f"🪓 <b>GHIGLIOTTINA 1v1</b>\n⚔️ <b>{duel['sfidante_name']}</b> vs <b>{duel['target_name']}</b>\n\n"
            f"🪓 <b>Indizi:</b>\n{indizi_formatted}\n\n"
            f"⏱️ <i>75 secondi! Il primo che scrive la parola legame vince <b>+💳 {duel['bet']*2} $SDG</b>!</i>")
        spawn(run_ghigliottina_timeout(context.bot, chat_id, query.message.message_id))

    elif query.data == "ghig_rifiuta":
        if not await try_refuse(query, duel, user): return
        del GHIGLIOTTINA_DUELS[chat_id]
        await safe_edit(query, "🐔 Sfida rifiutata!")

async def run_ghigliottina_timeout(bot, chat_id: int, msg_id: int):
    await asyncio.sleep(75)
    duel = GHIGLIOTTINA_DUELS.get(chat_id)
    if duel and duel.get("active") and duel.get("msg_id") == msg_id:
        del GHIGLIOTTINA_DUELS[chat_id]
        add_user_coins(chat_id, duel["sfidante_id"], duel["bet"])
        add_user_coins(chat_id, duel["target_id"], duel["bet"])
        await safe_send(
            bot, chat_id,
            f"⏰ Tempo scaduto! La parola era <b>{duel['word']}</b>. Puntate rimborsate.",
            [rematch_row("ghig", duel)])


# --- QUIZ 1v1 ---
async def handle_quiz1v1_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat_id
    user = query.from_user
    if chat_id not in QUIZ_DUELS_1V1:
        await query.answer("⚠️ Sfida non attiva.", show_alert=True)
        return
    duel = QUIZ_DUELS_1V1[chat_id]

    if query.data.startswith("q1v1_cat_"):
        if user.id != duel["sfidante_id"]:
            await query.answer("Non sei lo sfidante!", show_alert=True)
            return
        if duel.get("target_id"):
            await query.answer()
            return
        await query.answer()
        duel["category"] = query.data.split("_")[2]
        keyboard = [[InlineKeyboardButton("🎯 Accetta", callback_data="q1v1_accetta"), InlineKeyboardButton("🐔 Rifiuta", callback_data="q1v1_rifiuta")]]
        await safe_edit(
            query,
            f"⚔️ <b>DUELLO QUIZ 1v1</b> — Categoria <b>{duel['category']}</b>\n\n"
            f"@{html.escape(duel['target_username'])}, accetti il duello quiz di <b>{duel['sfidante_name']}</b>?\n"
            f"💰 Puntata: <code>💳 {duel['bet']} $SDG</code> | 🔄 Round: <b>{duel['rounds']}</b>", keyboard)

    elif query.data == "q1v1_accetta":
        if "category" not in duel:
            await query.answer("⚠️ Lo sfidante deve prima scegliere la categoria!", show_alert=True)
            return
        if not await try_accept(query, duel, chat_id, user, "quiz1v1"): return
        duel["round"] = 1
        duel["p1_score"], duel["p2_score"] = 0, 0
        duel["active"] = True
        # toglie i pulsanti Accetta/Rifiuta dal messaggio della sfida
        await safe_edit(
            query,
            f"⚔️ <b>DUELLO QUIZ 1v1 INIZIATO!</b>\n<b>{duel['sfidante_name']}</b> vs <b>{duel['target_name']}</b> — "
            f"Categoria <b>{duel['category']}</b>\nMontepremi: <code>💳 {duel['bet']*2} $SDG</code>")
        await launch_quiz1v1_round(context.bot, chat_id)

    elif query.data == "q1v1_rifiuta":
        if not await try_refuse(query, duel, user): return
        del QUIZ_DUELS_1V1[chat_id]
        await safe_edit(query, "🐔 Sfida rifiutata!")

async def launch_quiz1v1_round(bot, chat_id: int):
    duel = QUIZ_DUELS_1V1.get(chat_id)
    if not duel:
        return
    item = random.choice(CATEGORIE_QUIZ.get(duel["category"], CATEGORIE_QUIZ["CALCIO"])[1])
    duel["current_target"] = item["target"]
    duel["ts"] = time.time()
    msg = await safe_send(
        bot, chat_id,
        (f"⚔️ <b>ROUND {duel['round']}/{duel['rounds']}</b>\n"
         f"📊 {duel['sfidante_name']} ({duel['p1_score']}) - {duel['target_name']} ({duel['p2_score']})\n\n"
         f"<b>Indizio:</b> {item['indizi'][0]}\n\n⏱️ <i>60 secondi!</i>"))
    if not msg:
        return   # invio fallito: la partita verrà chiusa e rimborsata dal job di pulizia
    duel["msg_id"] = msg.message_id
    spawn(quiz1v1_round_timeout(bot, chat_id, duel["round"], msg.message_id, item["target"]))

async def quiz1v1_round_timeout(bot, chat_id, round_num, msg_id, target):
    await asyncio.sleep(60)
    duel = QUIZ_DUELS_1V1.get(chat_id)
    if duel and duel.get("round") == round_num and duel.get("msg_id") == msg_id:
        duel["msg_id"] = None   # invalida il timer prima di inviare, evita doppio avanzamento
        duel["current_target"] = None
        await safe_send(bot, chat_id, f"⏰ Round {round_num} scaduto! Era <b>{target}</b>.")
        await quiz1v1_advance(bot, chat_id)

async def quiz1v1_advance(bot, chat_id: int):
    """Round successivo oppure chiusura. Usata anche dal text router."""
    duel = QUIZ_DUELS_1V1.get(chat_id)
    if not duel:
        return
    need = duel["rounds"] // 2 + 1
    if duel["round"] >= duel["rounds"] or duel["p1_score"] >= need or duel["p2_score"] >= need:
        await conclude_quiz1v1_duel(bot, chat_id)
    else:
        duel["round"] += 1
        await launch_quiz1v1_round(bot, chat_id)

async def conclude_quiz1v1_duel(bot, chat_id: int):
    duel = QUIZ_DUELS_1V1.pop(chat_id, None)
    if not duel:
        return
    p1, p2, bet = duel["p1_score"], duel["p2_score"], duel["bet"]
    if p1 == p2:
        add_user_coins(chat_id, duel["sfidante_id"], bet)
        add_user_coins(chat_id, duel["target_id"], bet)
        text, btn_id = f"⚖️ <b>Pareggio {p1}-{p2}!</b> Puntate rimborsate.", duel["sfidante_id"]
    else:
        w, l = ((duel["sfidante_id"], duel["target_id"]) if p1 > p2
                else (duel["target_id"], duel["sfidante_id"]))
        wname = duel["sfidante_name"] if w == duel["sfidante_id"] else duel["target_name"]
        record_duel_result(chat_id, w, l, bet)
        text, btn_id = f"🏆 <b>{wname}</b> vince {max(p1, p2)}-{min(p1, p2)} e incassa <b>+💳 {bet*2} $SDG</b>!", w
    keyboard = [
        rematch_row("quiz1v1", duel),
        [InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{btn_id}")]
    ]
    await safe_send(bot, chat_id, text, keyboard)


def register(app):
    app.add_handler(CallbackQueryHandler(handle_challenge_config_callback, pattern="^cfg_"))
    app.add_handler(CallbackQueryHandler(handle_rematch_callback, pattern="^rem_"))
    app.add_handler(CallbackQueryHandler(start_dice_prep, pattern="^start_dice_"))
    # block=False: l'attesa dell'animazione del dado non ferma gli altri giocatori
    app.add_handler(CallbackQueryHandler(handle_dice_callback, pattern="^(dice_)", block=False))
    app.add_handler(CallbackQueryHandler(start_ttt_prep, pattern="^start_ttt_"))
    app.add_handler(CallbackQueryHandler(handle_ttt_callback, pattern="^ttt_"))
    app.add_handler(CallbackQueryHandler(start_roulette_prep, pattern="^start_roulette_"))
    app.add_handler(CallbackQueryHandler(gestione_bottoni_roulette, pattern="^roulette_"))
    app.add_handler(CallbackQueryHandler(start_highlow_prep, pattern="^start_highlow_"))
    app.add_handler(CallbackQueryHandler(handle_highlow_callback, pattern="^hl_"))
    app.add_handler(CallbackQueryHandler(start_ghigliottina_prep, pattern="^start_ghigliottina_prep_"))
    app.add_handler(CallbackQueryHandler(handle_ghigliottina_callback, pattern="^ghig_"))
    app.add_handler(CallbackQueryHandler(start_quiz1v1_prep, pattern="^start_quiz1v1_prep_"))
    app.add_handler(CallbackQueryHandler(handle_quiz1v1_callback, pattern="^q1v1_"))
    if app.job_queue:
        app.job_queue.run_repeating(cleanup_stale_games, interval=60, first=60)
