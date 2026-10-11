"""Giochi 1v1 unificati: Roulette, High/Low, Ghigliottina, Quiz 1v1, Dadi e Tris con scommesse."""
import asyncio
import random

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler, ContextTypes

from database_quiz import GHIGLIOTTINA_DB, CATEGORIE_QUIZ
from state import (
    ACTIVE_DUELS, HIGHLOW_DUELS, GHIGLIOTTINA_DUELS, QUIZ_DUELS_1V1,
    TIC_TAC_TOE_GAMES, USER_DATA, DICE_DUELS
)
from storage import get_user_coins, add_user_coins, get_user_key, save_db
from utils import verify_user_lock

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

# --- HELPER ACCETTAZIONE UNIFICATA ---
async def try_accept(query, game, chat_id: int, user) -> bool:
    """Valida l'accettazione e blocca le puntate. True = il duello può partire."""
    if game.get("target_id"):
        await query.answer()
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
    add_user_coins(chat_id, game["sfidante_id"], -bet)
    add_user_coins(chat_id, user.id, -bet)
    game["target_id"] = user.id
    game["target_name"] = user.first_name
    await query.answer()
    return True

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
    )
    # Mostra i round solo per i giochi che li supportano visivamente in questa fase
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
    
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        except Exception:
            pass
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
        if bet < 1:
            await query.answer("❌ Puntata non valida!", show_alert=True)
            return
        if get_user_coins(chat_id, user_id) < bet:
            await query.answer("❌ Non hai abbastanza $SDG per questa puntata!", show_alert=True)
            return

        game = cfg["game"]
        target = cfg["target_username"]
        rounds = cfg["rounds"]
        user = query.from_user
        del PENDING_CHALLENGES[user_id]

        if game == "dice":
            if chat_id in DICE_DUELS:
                await query.answer("❌ C'è già una sfida a Dadi in corso!", show_alert=True)
                return
            DICE_DUELS[chat_id] = {
                "sfidante_id": user.id, "sfidante_name": user.first_name,
                "target_username": target, "bet": bet, "rounds": rounds, "active": False,
                "p1_wins": 0, "p2_wins": 0, "current_round": 1,
                "p1_val": None, "p2_val": None, "turno_id": None
            }
            keyboard = [[InlineKeyboardButton("🎲 Accetta Dadi", callback_data="dice_accetta"), InlineKeyboardButton("🐔 Rifiuta", callback_data="dice_rifiuta")]]
            await query.edit_message_text(f"🎲 <b>SFIDA A DADI 1v1 INTERATTIVA</b>\n\n<b>{user.first_name}</b> sfida <b>@{target}</b>!\n💰 Puntata: <code>💳 {bet} $SDG</code> | 🔄 Partite: <b>{rounds}</b>\n\n@{target}, accetti?", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

        elif game == "ttt":
            if chat_id in TIC_TAC_TOE_GAMES:
                await query.answer("❌ C'è già un Tris in corso!", show_alert=True)
                return
            TIC_TAC_TOE_GAMES[chat_id] = {
                "sfidante_id": user.id, "sfidante_name": user.first_name,
                "target_username": target, "bet": bet, "rounds": rounds, "active": False,
                "board": [" "] * 9, "turn": "X"
            }
            keyboard = [[InlineKeyboardButton("❌ Accetta Tris", callback_data="ttt_accetta"), InlineKeyboardButton("🐔 Rifiuta", callback_data="ttt_rifiuta")]]
            await query.edit_message_text(f"❌⭕ <b>SFIDA TRIS 1v1</b>\n\n<b>{user.first_name}</b> sfida <b>@{target}</b>!\n💰 Puntata: <code>💳 {bet} $SDG</code>\n\n@{target}, accetti?", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

        elif game == "roulette":
            if chat_id in ACTIVE_DUELS:
                await query.answer("❌ C'è già una Roulette in corso!", show_alert=True)
                return
            ACTIVE_DUELS[chat_id] = {
                "sfidante_id": user.id, "sfidante_name": user.first_name,
                "target_username": target, "bet": bet, "rounds": rounds,
                "chambers": [False]*6, "current_chamber": 0
            }
            ACTIVE_DUELS[chat_id]["chambers"][random.randint(0, 5)] = True
            keyboard = [[InlineKeyboardButton("🎯 Accetta Roulette", callback_data="roulette_accetta"), InlineKeyboardButton("🐔 Rifiuta", callback_data="roulette_rifiuta")]]
            await query.edit_message_text(f"🔫 <b>ROULETTE RUSSA 1v1</b>\n\n<b>{user.first_name}</b> sfida <b>@{target}</b>!\n💰 Puntata: <code>💳 {bet} $SDG</code>\n\n@{target}, accetti?", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

        elif game == "highlow":
            if chat_id in HIGHLOW_DUELS:
                await query.answer("❌ C'è già un High/Low in corso!", show_alert=True)
                return
            HIGHLOW_DUELS[chat_id] = {
                "sfidante_id": user.id, "sfidante_name": user.first_name,
                "target_username": target, "bet": bet, "rounds": rounds, "val": 0, "turno_id": None
            }
            keyboard = [[InlineKeyboardButton("🎲 Accetta High/Low", callback_data="hl_accetta"), InlineKeyboardButton("🐔 Rifiuta", callback_data="hl_rifiuta")]]
            await query.edit_message_text(f"🎲 <b>HIGH / LOW 1v1</b>\n\n<b>{user.first_name}</b> sfida <b>@{target}</b>!\n💰 Puntata: <code>💳 {bet} $SDG</code>\n\n@{target}, accetti?", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

        elif game == "ghig":
            if chat_id in GHIGLIOTTINA_DUELS:
                await query.answer("❌ C'è già una Ghigliottina in corso!", show_alert=True)
                return
            GHIGLIOTTINA_DUELS[chat_id] = {
                "sfidante_id": user.id, "sfidante_name": user.first_name,
                "target_username": target, "bet": bet, "rounds": rounds, "active": False
            }
            keyboard = [[InlineKeyboardButton("🪓 Accetta Ghigliottina", callback_data="ghig_accetta"), InlineKeyboardButton("🐔 Rifiuta", callback_data="ghig_rifiuta")]]
            await query.edit_message_text(f"🪓 <b>GHIGLIOTTINA 1v1</b>\n\n<b>{user.first_name}</b> sfida <b>@{target}</b>!\n💰 Puntata: <code>💳 {bet} $SDG</code>\n\n@{target}, accetti?", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

        elif game == "quiz1v1":
            if chat_id in QUIZ_DUELS_1V1:
                await query.answer("❌ C'è già un Quiz 1v1 in corso!", show_alert=True)
                return
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


async def handle_dice_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat_id
    user = query.from_user

    if chat_id not in DICE_DUELS:
        await query.answer("⚠️ Sfida non attiva.", show_alert=True)
        return
    game = DICE_DUELS[chat_id]

    if query.data == "dice_accetta":
        if not await try_accept(query, game, chat_id, user): return
        
        game["active"] = True
        game["turno_id"] = game["sfidante_id"]

        keyboard = [[InlineKeyboardButton("🎲 Lancia il Dado", callback_data="dice_roll")]]
        await query.edit_message_text(
            f"🎲 <b>DUELLO DADI INIZIATO!</b> Montepremi: <code>💳 {game['bet']*2} $SDG</code>\n\n"
            f"📊 Punteggio: {game['sfidante_name']} (0) - {game['target_name']} (0)\n"
            f"Turno di <b>{game['sfidante_name']}</b>:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML"
        )

    elif query.data == "dice_rifiuta":
        await query.answer()
        await query.edit_message_text("🐔 Sfida rifiutata!")
        del DICE_DUELS[chat_id]

    elif query.data == "dice_roll":
        if user.id != game.get("turno_id"):
            await query.answer("⏳ Non è il tuo turno di lanciare!", show_alert=True)
            return
        await query.answer()

        dice_msg = await context.bot.send_dice(chat_id=chat_id, emoji="🎲")
        val = dice_msg.dice.value
        await asyncio.sleep(2)

        if user.id == game["sfidante_id"]:
            game["p1_val"] = val
            game["turno_id"] = game["target_id"]
            keyboard = [[InlineKeyboardButton("🎲 Lancia il Dado", callback_data="dice_roll")]]
            await query.edit_message_text(
                f"🎲 <b>ROUND {game['current_round']} / {game['rounds']}</b>\n\n"
                f"👤 {game['sfidante_name']} ha fatto: <b>{val}</b> 🎲\n"
                f"📊 Punteggio: {game['sfidante_name']} ({game['p1_wins']}) - {game['target_name']} ({game['p2_wins']})\n\n"
                f"Tocca a <b>{game['target_name']}</b> lanciare!", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML"
            )
        else:
            game["p2_val"] = val
            v1 = game["p1_val"]
            v2 = game["p2_val"]
            
            if v1 > v2:
                game["p1_wins"] += 1
                round_msg = f"🏆 <b>Round vinto da {game['sfidante_name']}!</b>"
            elif v2 > v1:
                game["p2_wins"] += 1
                round_msg = f"🏆 <b>Round vinto da {game['target_name']}!</b>"
            else:
                round_msg = "⚖️ <b>Round pari!</b>"

            res_text = f"👤 {game['sfidante_name']}: <b>{v1}</b> | 👤 {game['target_name']}: <b>{v2}</b>\n{round_msg}\n📊 Totale: {game['sfidante_name']} ({game['p1_wins']}) - {game['target_name']} ({game['p2_wins']})"

            wins_needed = (game["rounds"] // 2) + 1
            if game["p1_wins"] >= wins_needed or game["p2_wins"] >= wins_needed or game["current_round"] >= game["rounds"]:
                winner_id = game["sfidante_id"] if game["p1_wins"] > game["p2_wins"] else (game["target_id"] if game["p2_wins"] > game["p1_wins"] else None)
                montepremi = game["bet"] * 2
                
                w_id_btn = winner_id if winner_id else game["sfidante_id"]
                keyboard = [[InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{w_id_btn}")]]
                
                if winner_id:
                    loser_id = game["target_id"] if winner_id == game["sfidante_id"] else game["sfidante_id"]
                    w_name = game["sfidante_name"] if winner_id == game["sfidante_id"] else game["target_name"]
                    record_duel_result(chat_id, winner_id, loser_id, game["bet"])
                    msg = res_text + f"\n\n👑 <b>VITTORIA FINALE! {w_name}</b> vince <b>+💳 {montepremi} $SDG</b>!"
                else:
                    add_user_coins(chat_id, game["sfidante_id"], game["bet"])
                    add_user_coins(chat_id, game["target_id"], game["bet"])
                    msg = res_text + f"\n\n⚖️ <b>PAREGGIO FINALE!</b> Puntate rimborsate."
                
                del DICE_DUELS[chat_id]
                await context.bot.send_message(chat_id=chat_id, text=msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
            else:
                game["current_round"] += 1
                game["turno_id"] = game["sfidante_id"]
                keyboard = [[InlineKeyboardButton("🎲 Lancia il Dado", callback_data="dice_roll")]]
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=res_text + f"\n\n🔄 <b>Inizia il Round {game['current_round']}!</b>\nTocca a <b>{game['sfidante_name']}</b>:",
                    reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML"
                )

async def handle_ttt_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat_id
    user = query.from_user

    if chat_id not in TIC_TAC_TOE_GAMES: return
    game = TIC_TAC_TOE_GAMES[chat_id]

    if query.data == "ttt_accetta":
        if not await try_accept(query, game, chat_id, user): return
        game["active"] = True
        await update_ttt_board(query, game, "Inizia il Tris!")

    elif query.data == "ttt_rifiuta":
        await query.answer()
        await query.edit_message_text("🐔 Rifiutato!")
        del TIC_TAC_TOE_GAMES[chat_id]

    elif query.data.startswith("ttt_cell_"):
        await query.answer()
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
                w_id_btn = w_id
            else:
                add_user_coins(chat_id, game["sfidante_id"], game["bet"])
                add_user_coins(chat_id, game["target_id"], game["bet"])
                end_text = "⚖️ <b>PAREGGIO!</b> Puntate rimborsate."
                w_id_btn = game["sfidante_id"]
            
            del TIC_TAC_TOE_GAMES[chat_id]
            await update_ttt_board(query, game, end_text, finished=True, btn_id=w_id_btn)
        else:
            game["turn"] = "O" if game["turn"] == "X" else "X"
            await update_ttt_board(query, game, f"Turno di {game['turn']}")

def check_ttt_winner(board):
    for a,b,c in [(0,1,2),(3,4,5),(6,7,8),(0,3,6),(1,4,7),(2,5,8),(0,4,8),(2,4,6)]:
        if board[a] == board[b] == board[c] and board[a] != " ": return board[a]
    return None

async def update_ttt_board(query, game, status, finished=False, btn_id=None):
    symbols = {"X": "❌", "O": "⭕", " ": "◻️"}
    keyboard = []
    if not finished:
        for r in range(3):
            keyboard.append([InlineKeyboardButton(symbols[game['board'][r*3+c]], callback_data=f"ttt_cell_{r*3+c}") for c in range(3)])
    elif btn_id:
        keyboard.append([InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{btn_id}")])
    await query.edit_message_text(f"❌⭕ <b>TRIS</b>\n{status}", reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None, parse_mode="HTML")


async def gestione_bottoni_roulette(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat_id
    user = query.from_user
    if chat_id not in ACTIVE_DUELS: return
    duel = ACTIVE_DUELS[chat_id]

    if query.data == "roulette_accetta":
        if not await try_accept(query, duel, chat_id, user): return
        duel["turno_id"] = random.choice([duel["sfidante_id"], user.id])
        first_shooter = duel["sfidante_name"] if duel["turno_id"] == duel["sfidante_id"] else duel["target_name"]
        
        keyboard = [[InlineKeyboardButton("🔫 SPARA!", callback_data="roulette_spara")]]
        await query.edit_message_text(f"🔫 <b>ROULETTE INIZIATA!</b> Montepremi: {duel['bet']*2} $SDG\n\n👉 Inizia a sparare <b>{first_shooter}</b>!", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    
    elif query.data == "roulette_rifiuta":
        await query.answer()
        await query.edit_message_text("🐔 Sfida rifiutata!")
        del ACTIVE_DUELS[chat_id]

    elif query.data == "roulette_spara":
        if user.id != duel["turno_id"]:
            await query.answer("✋ Non è il tuo turno!", show_alert=True)
            return
        await query.answer()

        is_bullet = duel["chambers"][duel["current_chamber"]]
        duel["current_chamber"] += 1
        
        if is_bullet:
            montepremi = duel["bet"] * 2
            winner_id = duel["target_id"] if user.id == duel["sfidante_id"] else duel["sfidante_id"]
            loser_id = user.id
            record_duel_result(chat_id, winner_id, loser_id, duel["bet"])
            del ACTIVE_DUELS[chat_id]
            
            keyboard = [[InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{winner_id}")]]
            await query.edit_message_text(f"💥 <b>BAM! {user.first_name} è morto!</b> L'altro vince {montepremi} $SDG!", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        else:
            duel["turno_id"] = duel["target_id"] if user.id == duel["sfidante_id"] else duel["sfidante_id"]
            next_shooter = duel["target_name"] if duel["turno_id"] == duel["target_id"] else duel["sfidante_name"]
            keyboard = [[InlineKeyboardButton("🔫 SPARA!", callback_data="roulette_spara")]]
            await query.edit_message_text(f"<i>*click!*</i> {user.first_name} è salvo.\n\n👉 Tocca a <b>{next_shooter}</b>!", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")


async def handle_highlow_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat_id
    user = query.from_user
    if chat_id not in HIGHLOW_DUELS: return
    game = HIGHLOW_DUELS[chat_id]

    if query.data == "hl_accetta":
        if not await try_accept(query, game, chat_id, user): return
        game["turno_id"] = random.choice([game["sfidante_id"], user.id])
        game["val"] = random.randint(2, 11)
        first_shooter = game["sfidante_name"] if game["turno_id"] == game["sfidante_id"] else game["target_name"]
        
        keyboard = [[InlineKeyboardButton("📈 PIÙ ALTO", callback_data="hl_guess_high"), InlineKeyboardButton("📉 PIÙ BASSO", callback_data="hl_guess_low")]]
        await query.edit_message_text(f"🎲 Numero di partenza: <b>{game['val']}</b>\n\n👉 Tocca a <b>{first_shooter}</b>: il prossimo sarà più alto o più basso?", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    
    elif query.data == "hl_rifiuta":
        await query.answer()
        await query.edit_message_text("🐔 Rifiutato!")
        del HIGHLOW_DUELS[chat_id]

    elif query.data in ("hl_guess_high", "hl_guess_low"):
        if user.id != game.get("turno_id"):
            await query.answer("✋ Non è il tuo turno!", show_alert=True)
            return
        await query.answer()
        
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
            await query.edit_message_text(
                f"✅ <b>GIUSTO! Era {new_val}!</b> {user.first_name} si salva.\n\n"
                f"🎯 Nuovo numero: <b>{new_val}</b>\n"
                f"👉 Tocca a <b>{names[game['turno_id']]}</b>: più alto o più basso?",
                reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        else:
            winner_id = game["target_id"] if user.id == game["sfidante_id"] else game["sfidante_id"]
            record_duel_result(chat_id, winner_id, user.id, game["bet"])
            del HIGHLOW_DUELS[chat_id]
            keyboard = [[InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{winner_id}")]]
            await query.edit_message_text(
                f"💥 <b>ERRATO! Era {new_val}!</b>\n"
                f"💀 {user.first_name} perde: <b>{names[winner_id]}</b> vince <b>+💳 {game['bet']*2} $SDG</b>!",
                reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")


async def handle_ghigliottina_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat_id
    user = query.from_user
    if chat_id not in GHIGLIOTTINA_DUELS: return
    duel = GHIGLIOTTINA_DUELS[chat_id]

    if query.data == "ghig_accetta":
        if not await try_accept(query, duel, chat_id, user): return
        item = random.choice(GHIGLIOTTINA_DB)
        duel["word"] = item["target"]
        duel["active"] = True
        indizi_formatted = " • ".join(item["indizi"])
        await query.edit_message_text(f"🪓 <b>Indizi:</b>\n{indizi_formatted}", parse_mode="HTML")
        duel["msg_id"] = query.message.message_id
        asyncio.create_task(run_ghigliottina_timeout(context.bot, chat_id, query.message.message_id))
    
    elif query.data == "ghig_rifiuta":
        await query.answer()
        await query.edit_message_text("🐔 Sfida rifiutata!")
        del GHIGLIOTTINA_DUELS[chat_id]

async def run_ghigliottina_timeout(bot, chat_id: int, msg_id: int):
    await asyncio.sleep(75)
    duel = GHIGLIOTTINA_DUELS.get(chat_id)
    if duel and duel.get("active") and duel.get("msg_id") == msg_id:
        del GHIGLIOTTINA_DUELS[chat_id]
        add_user_coins(chat_id, duel["sfidante_id"], duel["bet"])
        add_user_coins(chat_id, duel["target_id"], duel["bet"])
        await bot.send_message(
            chat_id=chat_id,
            text=f"⏰ Tempo scaduto! La parola era <b>{duel['word']}</b>. Puntate rimborsate.",
            parse_mode="HTML")


async def handle_quiz1v1_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat_id
    user = query.from_user
    if chat_id not in QUIZ_DUELS_1V1: return
    duel = QUIZ_DUELS_1V1[chat_id]

    if query.data.startswith("q1v1_cat_"):
        if user.id != duel["sfidante_id"]: 
            await query.answer("Non sei lo sfidante!", show_alert=True)
            return
        await query.answer()
        duel["category"] = query.data.split("_")[2]
        keyboard = [[InlineKeyboardButton("🎯 Accetta", callback_data="q1v1_accetta"), InlineKeyboardButton("🐔 Rifiuta", callback_data="q1v1_rifiuta")]]
        await query.edit_message_text("Accetti il duello quiz?", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    
    elif query.data == "q1v1_accetta":
        if not await try_accept(query, duel, chat_id, user): return
        duel["round"] = 1
        duel["p1_score"], duel["p2_score"] = 0, 0
        duel["active"] = True
        await launch_quiz1v1_round(context.bot, chat_id)
    
    elif query.data == "q1v1_rifiuta":
        await query.answer()
        await query.edit_message_text("🐔 Sfida rifiutata!")
        del QUIZ_DUELS_1V1[chat_id]

async def launch_quiz1v1_round(bot, chat_id: int):
    duel = QUIZ_DUELS_1V1.get(chat_id)
    if not duel:
        return
    item = random.choice(CATEGORIE_QUIZ.get(duel["category"], CATEGORIE_QUIZ["CALCIO"])[1])
    duel["current_target"] = item["target"]
    msg = await bot.send_message(
        chat_id=chat_id,
        text=(f"⚔️ <b>ROUND {duel['round']}/{duel['rounds']}</b>\n"
              f"📊 {duel['sfidante_name']} ({duel['p1_score']}) - {duel['target_name']} ({duel['p2_score']})\n\n"
              f"<b>Indizio:</b> {item['indizi'][0]}\n\n⏱️ <i>60 secondi!</i>"),
        parse_mode="HTML")
    duel["msg_id"] = msg.message_id
    asyncio.create_task(quiz1v1_round_timeout(bot, chat_id, duel["round"], msg.message_id, item["target"]))

async def quiz1v1_round_timeout(bot, chat_id, round_num, msg_id, target):
    await asyncio.sleep(60)
    duel = QUIZ_DUELS_1V1.get(chat_id)
    if duel and duel.get("round") == round_num and duel.get("msg_id") == msg_id:
        await bot.send_message(chat_id=chat_id, parse_mode="HTML",
                               text=f"⏰ Round {round_num} scaduto! Era <b>{target}</b>.")
        await quiz1v1_advance(bot, chat_id)

async def quiz1v1_advance(bot, chat_id: int):
    """Round successivo oppure chiusura. Usala anche dal text router."""
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
    keyboard = [[InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{btn_id}")]]
    await bot.send_message(chat_id=chat_id, text=text,
                           reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

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
