"""Giochi 1v1 unificati: Roulette, High/Low, Ghigliottina, Quiz 1v1, Dadi e Tris con scommesse."""
import asyncio
import random

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler, ContextTypes

from config import FRASE_PENITENZA
from database_quiz import GHIGLIOTTINA_DB, CATEGORIE_QUIZ
from state import (
    ACTIVE_DUELS, HIGHLOW_DUELS, GHIGLIOTTINA_DUELS, QUIZ_DUELS_1V1,
    PENITENZE_ATTIVE, TIC_TAC_TOE_GAMES, USER_DATA
)
from storage import get_user_coins, add_user_coins, get_user_key, save_db
from utils import verify_user_lock

# --- FUNZIONE DI SUPPORTO STATISTICHE MULTIPLAYER ---
def record_duel_result(chat_id: int, winner_id: int, loser_id: int, bet_amount: int):
    for uid, won in [(winner_id, True), (loser_id, False)]:
        if not uid:
            continue
        key = get_user_key(chat_id, uid)
        if key not in USER_DATA:
            USER_DATA[key] = {"coins": 50, "last_daily": "", "quizzes_won": 0, "casino_wins": 0, "duels_wins": 0, "net_profit": 0, "duels_played": 0, "duels_won_count": 0}
        
        u_data = USER_DATA[key]
        u_data["duels_played"] = u_data.get("duels_played", 0) + 1
        
        if won:
            u_data["duels_wins"] = u_data.get("duels_wins", 0) + 1
            u_data["duels_won_count"] = u_data.get("duels_won_count", 0) + 1
            u_data["net_profit"] = u_data.get("net_profit", 0) + bet_amount
            add_user_coins(chat_id, uid, bet_amount * 2)
        else:
            u_data["net_profit"] = u_data.get("net_profit", 0) - bet_amount
    save_db()

# --- SUPPORTO PUNTATE E ROUND MULTIPLAYER (UNIFICATO) ---
PENDING_CHALLENGES = {} 

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
    owner_id = user_id
    keyboard = [
        [InlineKeyboardButton("10", callback_data=f"cfg_bet_10_{user_id}"), InlineKeyboardButton("25", callback_data=f"cfg_bet_25_{user_id}"), InlineKeyboardButton("50", callback_data=f"cfg_bet_50_{user_id}"), InlineKeyboardButton("100", callback_data=f"cfg_bet_100_{user_id}"), InlineKeyboardButton("🚀 ALL-IN", callback_data=f"cfg_bet_all_{user_id}")],
        [InlineKeyboardButton("🔄 1 Round", callback_data=f"cfg_rnd_1_{user_id}"), InlineKeyboardButton("🔄 3 Round", callback_data=f"cfg_rnd_3_{user_id}"), InlineKeyboardButton("🔄 5 Round", callback_data=f"cfg_rnd_5_{user_id}"), InlineKeyboardButton("🔄 7 Round", callback_data=f"cfg_rnd_7_{user_id}")],
        [InlineKeyboardButton("🚀 LANCIA LA SFIDA IN CHAT", callback_data=f"cfg_launch_{user_id}")],
        [InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{owner_id}")]
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

        # 1. DADI (Con turno interattivo tramite pulsante)
        if game == "dice":
            HIGHLOW_DUELS[chat_id] = {
                "sfidante_id": user.id, "sfidante_name": user.first_name,
                "target_username": target, "bet": bet, "rounds": rounds, "active": False,
                "p1_wins": 0, "p2_wins": 0, "current_round": 1,
                "p1_val": None, "p2_val": None, "turno_id": None
            }
            keyboard = [[InlineKeyboardButton("🎲 Accetta Dadi", callback_data="dice_accetta"), InlineKeyboardButton("🐔 Rifiuta", callback_data="dice_rifiuta")]]
            await query.edit_message_text(f"🎲 <b>SFIDA A DADI 1v1 INTERATTIVA</b>\n\n<b>{user.first_name}</b> sfida <b>@{target}</b>!\n💰 Puntata: <code>💳 {bet} $SDG</code> | 🔄 Partite: <b>{rounds}</b>\n\n@{target}, accetti?", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

        # 2. TRIS
        elif game == "ttt":
            TIC_TAC_TOE_GAMES[chat_id] = {
                "sfidante_id": user.id, "sfidante_name": user.first_name,
                "target_username": target, "bet": bet, "rounds": rounds, "active": False,
                "board": [" "] * 9, "turn": "X"
            }
            keyboard = [[InlineKeyboardButton("❌ Accetta Tris", callback_data="ttt_accetta"), InlineKeyboardButton("🐔 Rifiuta", callback_data="ttt_rifiuta")]]
            await query.edit_message_text(f"❌⭕ <b>SFIDA TRIS 1v1</b>\n\n<b>{user.first_name}</b> sfida <b>@{target}</b>!\n💰 Puntata: <code>💳 {bet} $SDG</code>\n\n@{target}, accetti?", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

        # 3. ROULETTE
        elif game == "roulette":
            ACTIVE_DUELS[chat_id] = {
                "sfidante_id": user.id, "sfidante_name": user.first_name,
                "target_username": target, "bet": bet, "rounds": rounds,
                "chambers": [False]*6, "current_chamber": 0
            }
            ACTIVE_DUELS[chat_id]["chambers"][random.randint(0, 5)] = True
            keyboard = [[InlineKeyboardButton("🎯 Accetta Roulette", callback_data="roulette_accetta"), InlineKeyboardButton("🐔 Rifiuta", callback_data="roulette_rifiuta")]]
            await query.edit_message_text(f"🔫 <b>ROULETTE RUSSA 1v1</b>\n\n<b>{user.first_name}</b> sfida <b>@{target}</b>!\n💰 Puntata: <code>💳 {bet} $SDG</code>\n\n@{target}, accetti?", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

        # 4. HIGH / LOW
        elif game == "highlow":
            HIGHLOW_DUELS[chat_id] = {
                "sfidante_id": user.id, "sfidante_name": user.first_name,
                "target_username": target, "bet": bet, "rounds": rounds, "val": 0, "turno_id": None
            }
            keyboard = [[InlineKeyboardButton("🎲 Accetta High/Low", callback_data="hl_accetta"), InlineKeyboardButton("🐔 Rifiuta", callback_data="hl_rifiuta")]]
            await query.edit_message_text(f"🎲 <b>HIGH / LOW 1v1</b>\n\n<b>{user.first_name}</b> sfida <b>@{target}</b>!\n💰 Puntata: <code>💳 {bet} $SDG</code>\n\n@{target}, accetti?", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

        # 5. GHIGLIOTTINA
        elif game == "ghig":
            GHIGLIOTTINA_DUELS[chat_id] = {
                "sfidante_id": user.id, "sfidante_name": user.first_name,
                "target_username": target, "bet": bet, "rounds": rounds, "active": False
            }
            keyboard = [[InlineKeyboardButton("🪓 Accetta Ghigliottina", callback_data="ghig_accetta"), InlineKeyboardButton("🐔 Rifiuta", callback_data="ghig_rifiuta")]]
            await query.edit_message_text(f"🪓 <b>GHIGLIOTTINA 1v1</b>\n\n<b>{user.first_name}</b> sfida <b>@{target}</b>!\n💰 Puntata: <code>💳 {bet} $SDG</code>\n\n@{target}, accetti?", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

        # 6. QUIZ 1v1
        elif game == "quiz1v1":
            QUIZ_DUELS_1V1[chat_id] = {
                "sfidante_id": user.id, "sfidante_name": user.first_name,
                "target_username": target, "bet": bet, "rounds": rounds, "active": False
            }
            keyboard = [
                [InlineKeyboardButton("⚽ Calcio", callback_data="q1v1_cat_CALCIO"), InlineKeyboardButton("🏎️ F1", callback_data="q1v1_cat_F1")],
                [InlineKeyboardButton("🦸 Marvel", callback_data="q1v1_cat_MARVEL"), InlineKeyboardButton("🎬 Cinema", callback_data="q1v1_cat_CINEMA")],
                [InlineKeyboardButton("📺 Serie TV", callback_data="q1v1_cat_SERIE"), InlineKeyboardButton("🗺️ Paesi", callback_data="q1v1_cat_PAESI")],
                [InlineKeyboardButton("🏮 Anime", callback_data="q1v1_cat_ANIME"), InlineKeyboardButton("🏷️ Brand", callback_data="q1v1_cat_BRANDS")],
                [InlineKeyboardButton("📜 Personaggi", callback_data="q1v1_cat_PERSONAGGI"), InlineKeyboardButton("🎵 Canzoni", callback_data="q1v1_cat_CANZONI")]
            ]
            await query.edit_message_text(f"⚔️ <b>DUELLO QUIZ 1v1</b>\n\n<b>{user.first_name}</b> sfida <b>@{target}</b>!\n💰 Puntata: <code>💳 {bet} $SDG</code>\n\nSeleziona la categoria:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")


# --- PREPARAZIONE PULSANTI DALL'HUB ---
async def start_dice_prep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    owner_id = int(query.data.split("_")[-1])
    if not await verify_user_lock(query, owner_id): return
    keyboard = [[InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{owner_id}")] ]
    await query.edit_message_text("🎲 <b>DADI 1v1</b>\n\nScrivi in chat il comando:\n👉 <code>sfidodadi @username</code>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

async def start_ttt_prep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    owner_id = int(query.data.split("_")[-1])
    if not await verify_user_lock(query, owner_id): return
    keyboard = [[InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{owner_id}")] ]
    await query.edit_message_text("❌⭕ <b>TRIS 1v1</b>\n\nScrivi in chat il comando:\n👉 <code>sfidotris @username</code>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

async def start_roulette_prep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    owner_id = int(query.data.split("_")[-1])
    if not await verify_user_lock(query, owner_id): return
    keyboard = [[InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{owner_id}")] ]
    await query.edit_message_text("🎯 <b>ROULETTE RUSSA 1v1</b>\n\nScrivi in chat il comando:\n👉 <code>sfidoroulette @username</code>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

async def start_highlow_prep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    owner_id = int(query.data.split("_")[-1])
    if not await verify_user_lock(query, owner_id): return
    keyboard = [[InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{owner_id}")] ]
    await query.edit_message_text("🎲 <b>HIGH / LOW 1v1</b>\n\nScrivi in chat il comando:\n👉 <code>sfidohighlow @username</code>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

async def start_ghigliottina_prep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    owner_id = int(query.data.split("_")[-1])
    if not await verify_user_lock(query, owner_id): return
    keyboard = [[InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{owner_id}")] ]
    await query.edit_message_text("🪓 <b>GHIGLIOTTINA 1v1</b>\n\nScrivi in chat il comando:\n👉 <code>sfidoghigliottina @username</code>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

async def start_quiz1v1_prep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    owner_id = int(query.data.split("_")[-1])
    if not await verify_user_lock(query, owner_id): return
    keyboard = [[InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{owner_id}")] ]
    await query.edit_message_text("⚔️ <b>DUELLO QUIZ 1v1</b>\n\nScrivi in chat il comando:\n👉 <code>sfidoquiz @username</code>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")


# --- CALLBACKS GIOCHI 1v1 (DADI INTERATTIVO) ---
async def handle_dice_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat_id
    user = query.from_user

    if chat_id not in HIGHLOW_DUELS:
        await query.answer("⚠️ Sfida non attiva.", show_alert=True)
        return
    game = HIGHLOW_DUELS[chat_id]

    if query.data == "dice_accetta":
        await query.answer()
        if user.username and user.username.lower() != game["target_username"]:
            await query.answer("❌ Solo lo sfidato può accettare!", show_alert=True)
            return
        if get_user_coins(chat_id, user.id) < game["bet"]:
            await query.answer("❌ Crediti insufficienti!", show_alert=True)
            return

        game["target_id"] = user.id
        game["target_name"] = user.first_name
        game["active"] = True
        game["turno_id"] = game["sfidante_id"] # Inizia lo sfidante

        add_user_coins(chat_id, game["sfidante_id"], -game["bet"])
        add_user_coins(chat_id, game["target_id"], -game["bet"])

        keyboard = [[InlineKeyboardButton("🎲 Lancia il Dado", callback_data="dice_roll")]]
        await query.edit_message_text(
            f"🎲 <b>DUELLO DADI INIZIATO!</b> Montepremi: <code>💳 {game['bet']*2} $SDG</code>\n\n"
            f"Turno di <b>{game['sfidante_name']}</b>:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML"
        )
    elif query.data == "dice_rifiuta":
        await query.answer()
        await query.edit_message_text("🐔 Sfida rifiutata!")
        del HIGHLOW_DUELS[chat_id]

    elif query.data == "dice_roll":
        if user.id != game.get("turno_id"):
            await query.answer("⏳ Non è il tuo turno di lanciare!", show_alert=True)
            return
        await query.answer()

        # Invia il dado nativo di Telegram in chat
        dice_msg = await context.bot.send_dice(chat_id=chat_id, emoji="🎲")
        val = dice_msg.dice.value
        await asyncio.sleep(2)

        if user.id == game["sfidante_id"]:
            game["p1_val"] = val
            game["turno_id"] = game["target_id"]
            keyboard = [[InlineKeyboardButton("🎲 Lancia il Dado", callback_data="dice_roll")]]
            await query.edit_message_text(
                f"🎲 <b>ROUND {game['current_round']} / {game['rounds']}</b>\n\n"
                f"👤 {game['sfidante_name']} ha fatto: <b>{val}</b> 🎲\n\n"
                f"Tocca a <b>{game['target_name']}</b> lanciare!", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML"
            )
        else:
            game["p2_val"] = val
            v1 = game["p1_val"]
            v2 = game["p2_val"]
            
            res_text = f"👤 {game['sfidante_name']}: <b>{v1}</b> | 👤 {game['target_name']}: <b>{v2}</b>\n\n"

            if v1 > v2:
                game["p1_wins"] += 1
                res_text += f"🏆 <b>Round vinto da {game['sfidante_name']}!</b>"
            elif v2 > v1:
                game["p2_wins"] += 1
                res_text += f"🏆 <b>Round vinto da {game['target_name']}!</b>"
            else:
                res_text += "⚖️ <b>Round pari!</b>"

            wins_needed = (game["rounds"] // 2) + 1
            if game["p1_wins"] >= wins_needed or game["p2_wins"] >= wins_needed or game["current_round'] >= game['rounds']:
                winner_id = game["sfidante_id"] if game["p1_wins"] > game["p2_wins"] else (game["target_id"] if game["p2_wins"] > game["p1_wins"] else None)
                montepremi = game["bet"] * 2
                if winner_id:
                    loser_id = game["target_id"] if winner_id == game["sfidante_id"] else game["sfidante_id"]
                    w_name = game["sfidante_name"] if winner_id == game["sfidante_id"] else game["target_name"]
                    record_duel_result(chat_id, winner_id, loser_id, game["bet"])
                    msg = res_text + f"\n\n👑 <b>VITTORIA FINALE! {w_name}</b> vince <b>+💳 {montepremi} $SDG</b>!"
                else:
                    add_user_coins(chat_id, game["sfidante_id"], game["bet"])
                    add_user_coins(chat_id, game["target_id"], game["bet"])
                    msg = res_text + f"\n\n⚖️ <b>PAREGGIO FINALE!</b> Puntate rimborsate."
                del HIGHLOW_DUELS[chat_id]
                await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode="HTML")
            else:
                game["current_round"] += 1
                game["turno_id"] = game["sfidante_id"]
                keyboard = [[InlineKeyboardButton("🎲 Lancia il Dado", callback_data="dice_roll")]]
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=res_text + f"\n🔄 <b>Inizia il Round {game['current_round']}!</b>\nTocca a <b>{game['sfidante_name']}</b>:",
                    reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML"
                )

async def handle_ttt_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    user = query.from_user

    if chat_id not in TIC_TAC_TOE_GAMES: return
    game = TIC_TAC_TOE_GAMES[chat_id]

    if query.data == "ttt_accetta":
        if user.username and user.username.lower() != game["target_username"]: return
        if get_user_coins(chat_id, user.id) < game["bet"]: return
        game["target_id"] = user.id
        game["target_name"] = user.first_name
        game["active"] = True
        add_user_coins(chat_id, game["sfidante_id"], -game["bet"])
        add_user_coins(chat_id, game["target_id"], -game["bet"])
        await update_ttt_board(query, game, "Inizia il Tris!")
    elif query.data == "ttt_rifiuta":
        await query.edit_message_text("🐔 Rifiutato!")
        del TIC_TAC_TOE_GAMES[chat_id]
    elif query.data.startswith("ttt_cell_"):
        idx = int(query.data.split("_")[2])
        if user.id != (game["sfidante_id"] if game["turn"] == "X" else game["target_id"]): return
        if game["board"][idx] != " ": return
        game["board"][idx] = game["turn"]
        winner = check_ttt_winner(game["board"])
        if winner or " " not in game["board"]:
            montepremi = game["bet"] * 2
            if winner:
                w_id = game["sfidante_id"] if winner == "X" else game["target_id"]
                l_id = game["target_id"] if winner == "X" else game["sfidante_id"]
                record_duel_result(chat_id, w_id, l_id, game["bet"])
                end_text = f"🏆 <b>TRIS VINTO!</b> Montepremi: +💳 {montepremi} $SDG"
            else:
                add_user_coins(chat_id, game["sfidante_id"], game["bet"])
                add_user_coins(chat_id, game["target_id"], game["bet"])
                end_text = "⚖️ <b>PAREGGIO!</b>"
            del TIC_TAC_TOE_GAMES[chat_id]
            await update_ttt_board(query, game, end_text, finished=True)
        else:
            game["turn"] = "O" if game["turn"] == "X" else "X"
            await update_ttt_board(query, game, f"Turno di {game['turn']}")

def check_ttt_winner(board):
    for a,b,c in [(0,1,2),(3,4,5),(6,7,8),(0,3,6),(1,4,7),(2,5,8),(0,4,8),(2,4,6)]:
        if board[a] == board[b] == board[c] and board[a] != " ": return board[a]
    return None

async def update_ttt_board(query, game, status, finished=False):
    symbols = {"X": "❌", "O": "⭕", " ": "◻️"}
    keyboard = []
    if not finished:
        for r in range(3):
            keyboard.append([InlineKeyboardButton(symbols[game['board'][r*3+c]], callback_data=f"ttt_cell_{r*3+c}") for c in range(3)])
    await query.edit_message_text(f"❌⭕ <b>TRIS</b>\n{status}", reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None, parse_mode="HTML")

# ROULETTE, HIGHLOW, GHIGLIOTTINA E QUIZ 1v1 CALLBACKS
async def gestione_bottoni_roulette(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    user = query.from_user
    if chat_id not in ACTIVE_DUELS: return
    duel = ACTIVE_DUELS[chat_id]
    if query.data == "roulette_accetta":
        if user.username and user.username.lower() != duel["target_username"]: return
        if get_user_coins(chat_id, user.id) < duel["bet"]: return
        duel["target_id"] = user.id
        duel["target_name"] = user.first_name
        duel["turno_id"] = random.choice([duel["sfidante_id"], user.id])
        add_user_coins(chat_id, duel["sfidante_id"], -duel["bet"])
        add_user_coins(chat_id, duel["target_id"], -duel["bet"])
        keyboard = [[InlineKeyboardButton("🔫 SPARA!", callback_data="roulette_spara")]]
        await query.edit_message_text(f"🔫 <b>ROULETTE INIZIATA!</b> Montepremi: {duel['bet']*2} $SDG", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    elif query.data == "roulette_rifiuta":
        await query.edit_message_text("🐔 Rifiutato!")
        del ACTIVE_DUELS[chat_id]
    elif query.data == "roulette_spara":
        if user.id != duel["turno_id"]: return
        is_bullet = duel["chambers"][duel["current_chamber"]]
        duel["current_chamber"] += 1
        if is_bullet:
            montepremi = duel["bet"] * 2
            winner_id = duel["target_id"] if user.id == duel["sfidante_id"] else duel["sfidante_id"]
            loser_id = duel["sfidante_id"] if user.id == duel["sfidante_id"] else duel["target_id"]
            record_duel_result(chat_id, winner_id, loser_id, duel["bet"])
            await query.edit_message_text(f"💥 <b>BAM! {user.first_name} è morto!</b> L'altro vince {montepremi} $SDG!", parse_mode="HTML")
            del ACTIVE_DUELS[chat_id]
        else:
            duel["turno_id"] = duel["target_id"] if user.id == duel["sfidante_id"] else duel["sfidante_id"]
            keyboard = [[InlineKeyboardButton("🔫 SPARA!", callback_data="roulette_spara")]]
            await query.edit_message_text("click! Salvo.", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

async def handle_highlow_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    user = query.from_user
    if chat_id not in HIGHLOW_DUELS: return
    game = HIGHLOW_DUELS[chat_id]
    if query.data == "hl_accetta":
        if user.username and user.username.lower() != game["target_username"]: return
        if get_user_coins(chat_id, user.id) < game["bet"]: return
        game["target_id"] = user.id
        game["target_name"] = user.first_name
        game["turno_id"] = random.choice([game["sfidante_id"], user.id])
        game["val"] = random.randint(2, 11)
        add_user_coins(chat_id, game["sfidante_id"], -game["bet"])
        add_user_coins(chat_id, game["target_id"], -game["bet"])
        keyboard = [[InlineKeyboardButton("📈 PIÙ ALTO", callback_data="hl_guess_high"), InlineKeyboardButton("📉 PIÙ BASSO", callback_data="hl_guess_low")]]
        await query.edit_message_text(f"🎲 Numero: <b>{game['val']}</b>", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    elif query.data == "hl_rifiuta":
        await query.edit_message_text("🐔 Rifiutato!")
        del HIGHLOW_DUELS[chat_id]

async def handle_ghigliottina_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    user = query.from_user
    if chat_id not in GHIGLIOTTINA_DUELS: return
    duel = GHIGLIOTTINA_DUELS[chat_id]
    if query.data == "ghig_accetta":
        if user.username and user.username.lower() != duel["target_username"]: return
        if get_user_coins(chat_id, user.id) < duel["bet"]: return
        item = random.choice(GHIGLIOTTINA_DB)
        duel["target_id"] = user.id
        duel["word"] = item["target"]
        duel["active"] = True
        add_user_coins(chat_id, duel["sfidante_id"], -duel["bet"])
        add_user_coins(chat_id, user.id, -duel["bet"])
        indizi_formatted = " • ".join(item["indizi"])
        await query.edit_message_text(f"🪓 <b>Indizi:</b>\n{indizi_formatted}", parse_mode="HTML")
        asyncio.create_task(run_ghigliottina_timeout(context.bot, chat_id))
    elif query.data == "ghig_rifiuta":
        del GHIGLIOTTINA_DUELS[chat_id]

async def run_ghigliottina_timeout(bot, chat_id: int):
    await asyncio.sleep(75)
    if chat_id in GHIGLIOTTINA_DUELS and GHIGLIOTTINA_DUELS[chat_id].get("active"):
        del GHIGLIOTTINA_DUELS[chat_id]
        await bot.send_message(chat_id=chat_id, text="⏰ Tempo scaduto per la Ghigliottina!")

async def handle_quiz1v1_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    user = query.from_user
    if chat_id not in QUIZ_DUELS_1V1: return
    duel = QUIZ_DUELS_1V1[chat_id]
    if query.data.startswith("q1v1_cat_"):
        if user.id != duel["sfidante_id"]: return
        duel["category"] = query.data.split("_")[2]
        keyboard = [[InlineKeyboardButton("🎯 Accetta", callback_data="q1v1_accetta"), InlineKeyboardButton("🐔 Rifiuta", callback_data="q1v1_rifiuta")]]
        await query.edit_message_text("Accetti il duello quiz?", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    elif query.data == "q1v1_accetta":
        if user.username and user.username.lower() != duel["target_username"]: return
        if get_user_coins(chat_id, user.id) < duel["bet"]: return
        duel["target_id"] = user.id
        duel["round"] = 1
        duel["p1_score"], duel["p2_score"] = 0, 0
        duel["active"] = True
        add_user_coins(chat_id, duel["sfidante_id"], -duel["bet"])
        add_user_coins(chat_id, user.id, -duel["bet"])
        await launch_quiz1v1_round(context.bot, chat_id)
    elif query.data == "q1v1_rifiuta":
        del QUIZ_DUELS_1V1[chat_id]

async def launch_quiz1v1_round(bot, chat_id: int):
    if chat_id not in QUIZ_DUELS_1V1: return
    duel = QUIZ_DUELS_1V1[chat_id]
    item = random.choice(CATEGORIE_QUIZ.get(duel["category"], CATEGORIE_QUIZ["CALCIO"])[1])
    duel["current_target"] = item["target"]
    msg = await bot.send_message(chat_id=chat_id, text=f"⚔️ <b>Round {duel['round']}</b>\n{item['indizi'][0]}", parse_mode="HTML")
    duel["msg_id"] = msg.message_id

async def conclude_quiz1v1_duel(bot, chat_id: int):
    if chat_id not in QUIZ_DUELS_1V1: return
    duel = QUIZ_DUELS_1V1[chat_id]
    montepremi = duel["bet"] * 2
    w_id = duel["sfidante_id"] if duel["p1_score"] > duel["p2_score"] else duel["target_id"]
    l_id = duel["target_id"] if duel["p1_score"] > duel["p2_score"] else duel["sfidante_id"]
    record_duel_result(chat_id, w_id, l_id, duel["bet"])
    del QUIZ_DUELS_1V1[chat_id]
    await bot.send_message(chat_id=chat_id, text=f"🏆 Quiz 1v1 terminato! +💳 {montepremi} $SDG al vincitore.", parse_mode="HTML")

# --- REGISTRAZIONE HANDLER ---
def register(app):
    app.add_handler(CallbackQueryHandler(handle_challenge_config_callback, pattern="^cfg_"))
    app.add_handler(CallbackQueryHandler(start_dice_prep, pattern="^start_dice_"))
    app.add_handler(CallbackQueryHandler(handle_dice_callback, pattern="^(dice_)"))
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
