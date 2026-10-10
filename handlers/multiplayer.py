"""Giochi 1v1: Roulette Russa, High/Low, Ghigliottina, Duello Quiz, Dadi e Tris."""
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
from storage import get_user_coins, add_user_coins
from utils import verify_user_lock

# --- SUPPORTO PUNTATE E ROUND MULTIPLAYER ---
PENDING_CHALLENGES = {}  # Memoria temporanea per configurare la sfida prima di inviarla

async def setup_challenge_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, game_type: str, target_username: str, chat_id: int, user):
    PENDING_CHALLENGES[user.id] = {
        "game": game_type,
        "target_username": target_username,
        "chat_id": chat_id,
        "bet": 10,
        "rounds": 1
    }
    await show_challenge_config_message(update, context, user.id)

async def show_challenge_config_message(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    cfg = PENDING_CHALLENGES[user_id]
    text = (
        f"⚔️ <b>CONFIGURA SFIDA: {cfg['game'].upper()}</b>\n\n"
        f"🎯 Avversario: <code>@{cfg['target_username']}</code>\n"
        f"💰 Puntata selezionata: <b>💳 {cfg['bet']} $SDG</b>\n"
        f"🔄 Round/Partite: <b>{cfg['rounds']}</b>\n\n"
        "<i>Scegli la puntata e i round, poi lancia la sfida:</i>"
    )
    keyboard = [
        [InlineKeyboardButton("10", callback_data=f"cfg_bet_10_{user_id}"), InlineKeyboardButton("25", callback_data=f"cfg_bet_25_{user_id}"), InlineKeyboardButton("50", callback_data=f"cfg_bet_50_{user_id}"), InlineKeyboardButton("100", callback_data=f"cfg_bet_100_{user_id}"), InlineKeyboardButton("🚀 ALL-IN", callback_data=f"cfg_bet_all_{user_id}")],
        [InlineKeyboardButton("🔄 1 Round", callback_data=f"cfg_rnd_1_{user_id}"), InlineKeyboardButton("🔄 3 Round", callback_data=f"cfg_rnd_3_{user_id}"), InlineKeyboardButton("🔄 5 Round", callback_data=f"cfg_rnd_5_{user_id}"), InlineKeyboardButton("🔄 7 Round", callback_data=f"cfg_rnd_7_{user_id}")],
        [InlineKeyboardButton("🚀 LANCIA LA SFIDA IN CHAT", callback_data=f"cfg_launch_{user_id}")]
    ]
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

async def handle_challenge_config_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split("_")
    action = parts[1]
    user_id = int(parts[-1])

    if query.from_user.id != user_id:
        await query.answer("🛑 Questa configurazione non è tua!", show_alert=True)
        return

    if user_id not in PENDING_CHALLENGES:
        await query.edit_message_text("❌ Sessione scaduta.")
        return

    cfg = PENDING_CHALLENGES[user_id]
    chat_id = cfg["chat_id"]

    if action == "bet":
        val = parts[2]
        if val == "all":
            cfg["bet"] = get_user_coins(chat_id, user_id)
        else:
            cfg["bet"] = int(val)
        await show_challenge_config_message(update, context, user_id)

    elif action == "rnd":
        cfg["rounds"] = int(parts[2])
        await show_challenge_config_message(update, context, user_id)

    elif action == "launch":
        bet = cfg["bet"]
        if get_user_coins(chat_id, user_id) < bet:
            await query.answer("❌ Non hai abbastanza $SDG per questa puntata!", show_alert=True)
            return

        game = cfg["game"]
        target = cfg["target_username"]
        rounds = cfg["rounds"]
        user = query.from_user
        del PENDING_CHALLENGES[user_id]

        if game == "dice":
            HIGHLOW_DUELS[chat_id] = { # Riusiamo il dizionario o ne creiamo uno dedicato
                "sfidante_id": user.id, "sfidante_name": user.first_name,
                "target_username": target, "bet": bet, "rounds": rounds, "active": False,
                "p1_wins": 0, "p2_wins": 0, "current_round": 1
            }
            keyboard = [[InlineKeyboardButton("🎲 Accetta Dadi 1v1", callback_data="dice_accetta"), InlineKeyboardButton("🐔 Rifiuta", callback_data="dice_rifiuta")]]
            await query.edit_message_text(
                f"🎲 <b>SFIDA A DADI 1v1</b>\n\n<b>{user.first_name}</b> sfida <b>@{target}</b>!\n"
                f"💰 Puntata: <code>💳 {bet} $SDG</code> | 🔄 Partite: <b>{rounds}</b>\n\n@{target}, accetti?",
                reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML"
            )

        elif game == "ttt":
            TIC_TAC_TOE_GAMES[chat_id] = {
                "sfidante_id": user.id, "sfidante_name": user.first_name,
                "target_username": target, "bet": bet, "active": False,
                "board": [" "] * 9, "turn": "X"
            }
            keyboard = [[InlineKeyboardButton("❌ Accetta Tris 1v1", callback_data="ttt_accetta"), InlineKeyboardButton("🐔 Rifiuta", callback_data="ttt_rifiuta")]]
            await query.edit_message_text(
                f"❌⭕ <b>SFIDA TRIS 1v1 (TIC-TAC-TOE)</b>\n\n<b>{user.first_name}</b> sfida <b>@{target}</b>!\n"
                f"💰 Puntata: <code>💳 {bet} $SDG</code>\n\n@{target}, accetti?",
                reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML"
            )


# --- GAME: DADI 1v1 ---
async def start_dice_prep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    owner_id = int(query.data.split("_")[-1])
    if not await verify_user_lock(query, owner_id): return
    await query.edit_message_text("🎲 <b>DADI 1v1</b>\n\nScrivi in chat:\n👉 <code>sfidodadi @username</code>", parse_mode="HTML")

async def handle_dice_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    user = query.from_user

    if chat_id not in HIGHLOW_DUELS:
        await query.answer("⚠️ Sfida Dadi non attiva.", show_alert=True)
        return
    game = HIGHLOW_DUELS[chat_id]

    if query.data == "dice_accetta":
        if user.username and user.username.lower() != game["target_username"]:
            await query.answer("❌ Solo lo sfidato può accettare!", show_alert=True)
            return
        if get_user_coins(chat_id, user.id) < game["bet"]:
            await query.answer("❌ Non hai abbastanza $SDG per coprire la puntata!", show_alert=True)
            return

        game["target_id"] = user.id
        game["target_name"] = user.first_name
        game["active"] = True

        add_user_coins(chat_id, game["sfidante_id"], -game["bet"])
        add_user_coins(chat_id, game["target_id"], -game["bet"])

        await query.edit_message_text(f"🎲 <b>DUELLO DADI INIZIATO!</b> Puntata in palio: <code>💳 {game['bet']*2} $SDG</code>.\n\nLancio i dadi...", parse_mode="HTML")
        await play_dice_round(context.bot, chat_id)

    elif query.data == "dice_rifiuta":
        await query.edit_message_text("🐔 Sfida Dadi rifiutata!")
        del HIGHLOW_DUELS[chat_id]

async def play_dice_round(bot, chat_id: int):
    if chat_id not in HIGHLOW_DUELS: return
    game = HIGHLOW_DUELS[chat_id]
    rnd = game["current_round"]

    await bot.send_message(chat_id=chat_id, text=f"🎲 <b>ROUND {rnd} / {game['rounds']}</b>", parse_mode="HTML")
    
    d1 = await bot.send_dice(chat_id=chat_id, emoji="🎲")
    v1 = d1.dice.value
    await asyncio.sleep(3)

    d2 = await bot.send_dice(chat_id=chat_id, emoji="🎲")
    v2 = d2.dice.value
    await asyncio.sleep(3)

    if v1 > v2:
        game["p1_wins"] += 1
        res_text = f"🏆 <b>{game['sfidante_name']}</b> vince il round ({v1} vs {v2})!"
    elif v2 > v1:
        game["p2_wins"] += 1
        res_text = f"🏆 <b>{game['target_name']}</b> vince il round ({v2} vs {v1})!"
    else:
        res_text = f"⚖️ Pareggio in questo round ({v1} a {v1})! Si rigioca il punto."

    await bot.send_message(chat_id=chat_id, text=res_text, parse_mode="HTML")

    wins_needed = (game["rounds"] // 2) + 1
    if game["p1_wins"] >= wins_needed or game["p2_wins"] >= wins_needed or game["current_round"] >= game["rounds"]:
        winner_id = game["sfidante_id"] if game["p1_wins"] > game["p2_wins"] else (game["target_id"] if game["p2_wins"] > game["p1_wins"] else None)
        montepremi = game["bet"] * 2

        if winner_id:
            winner_name = game["sfidante_name"] if winner_id == game["sfidante_id"] else game["target_name"]
            add_user_coins(chat_id, winner_id, montepremi)
            msg = f"👑 <b>VITTORIA FINALE! {winner_name}</b> conquista il duello di dadi e si intasca <b>+💳 {montepremi} $SDG</b>!"
        else:
            add_user_coins(chat_id, game["sfidante_id"], game["bet"])
            add_user_coins(chat_id, game["target_id"], game["bet"])
            msg = f"⚖️ <b>DUELLO TERMINATO IN PARITÀ!</b> Puntate rimborsate."

        del HIGHLOW_DUELS[chat_id]
        await bot.send_message(chat_id=chat_id, text=msg, parse_mode="HTML")
    else:
        game["current_round"] += 1
        await play_dice_round(bot, chat_id)


# --- GAME: TRIS 1v1 (TIC-TAC-TOE) ---
async def start_ttt_prep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    owner_id = int(query.data.split("_")[-1])
    if not await verify_user_lock(query, owner_id): return
    await query.edit_message_text("❌⭕ <b>TRIS 1v1</b>\n\nScrivi in chat:\n👉 <code>sfidotris @username</code>", parse_mode="HTML")

async def handle_ttt_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    user = query.from_user

    if chat_id not in TIC_TAC_TOE_GAMES:
        await query.answer("⚠️ Partita Tris non attiva.", show_alert=True)
        return
    game = TIC_TAC_TOE_GAMES[chat_id]

    if query.data == "ttt_accetta":
        if user.username and user.username.lower() != game["target_username"]:
            await query.answer("❌ Solo lo sfidato può accettare!", show_alert=True)
            return
        if get_user_coins(chat_id, user.id) < game["bet"]:
            await query.answer("❌ Non hai abbastanza $SDG!", show_alert=True)
            return

        game["target_id"] = user.id
        game["target_name"] = user.first_name
        game["active"] = True

        add_user_coins(chat_id, game["sfidante_id"], -game["bet"])
        add_user_coins(chat_id, game["target_id"], -game["bet"])

        await update_ttt_board(query, game, "Inizia il Tris! Turno di ❌ X (" + game["sfidante_name"] + ")")

    elif query.data == "ttt_rifiuta":
        await query.edit_message_text("🐔 Sfida Tris rifiutata!")
        del TIC_TAC_TOE_GAMES[chat_id]

    elif query.data.startswith("ttt_cell_"):
        idx = int(query.data.split("_")[2])
        current_player_id = game["sfidante_id"] if game["turn"] == "X" else game["target_id"]

        if user.id != current_player_id:
            await query.answer("✋ Non è il tuo turno!", show_alert=True)
            return
        if game["board"][idx] != " ":
            await query.answer("🛑 Casella occupata!", show_alert=True)
            return

        game["board"][idx] = "O" if game["turn"] == "O" else "X"
        winner = check_ttt_winner(game["board"])

        if winner or " " not in game["board"]:
            montepremi = game["bet"] * 2
            if winner:
                w_name = game["sfidante_name"] if winner == "X" else game["target_name"]
                w_id = game["sfidante_id"] if winner == "X" else game["target_id"]
                add_user_coins(chat_id, w_id, montepremi)
                end_text = f"🏆 <b>TRIS VINTO! {w_name}</b> si aggiudica <b>+💳 {montepremi} $SDG</b>!"
            else:
                add_user_coins(chat_id, game["sfidante_id"], game["bet"])
                add_user_coins(chat_id, game["target_id"], game["bet"])
                end_text = f"⚖️ <b>TRIS IN PAREGGIO!</b> Puntate rimborsate."

            del TIC_TAC_TOE_GAMES[chat_id]
            await update_ttt_board(query, game, end_text, finished=True)
        else:
            game["turn"] = "X" if game["turn"] == "O" else "O"
            next_name = game["sfidante_name"] if game["turn"] == "X" else game["target_name"]
            await update_ttt_board(query, game, f"Turno di {'⭕ O' if game['turn']=='O' else '❌ X'} ({next_name})")

def check_ttt_winner(board):
    wins = [(0,1,2),(3,4,5),(6,7,8),(0,3,6),(1,4,7),(2,5,8),(0,4,8),(2,4,6)]
    for a,b,c in wins:
        if board[a] == board[b] == board[c] and board[a] != " ":
            return board[a]
    return None

async def update_ttt_board(query, game, status_text, finished=False):
    symbols = {"X": "❌", "O": "⭕", " ": "◻️"}
    keyboard = []
    if not finished:
        for r in range(3):
            row = []
            for c in range(3):
                idx = r * 3 + c
                cell_val = game["board"][idx]
                row.append(InlineKeyboardButton(symbols[cell_val], callback_data=f"ttt_cell_{idx}"))
            keyboard.append(row)
    
    text = f"❌⭕ <b>TRIS 1v1</b>\n👤 ❌ {game['sfidante_name']} vs ⭕ {game['target_name']}\n\n{status_text}"
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None, parse_mode="HTML")


# --- REGISTRAZIONE HANDLER ---
def register(app):
    app.add_handler(CallbackQueryHandler(handle_challenge_config_callback, pattern="^cfg_"))
    app.add_handler(CallbackQueryHandler(start_dice_prep, pattern="^start_dice_"))
    app.add_handler(CallbackQueryHandler(handle_dice_callback, pattern="^dice_"))
    app.add_handler(CallbackQueryHandler(start_ttt_prep, pattern="^start_ttt_"))
    app.add_handler(CallbackQueryHandler(handle_ttt_callback, pattern="^ttt_"))
