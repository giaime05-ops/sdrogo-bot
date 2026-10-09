import random
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler, ContextTypes

from state import BLACKJACK_GAMES
from storage import get_user_coins, add_user_coins
from utils import verify_user_lock

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

def register(app):
    app.add_handler(CallbackQueryHandler(start_bj_from_hub, pattern="^start_bj_"))
    app.add_handler(CallbackQueryHandler(handle_bj_callback, pattern="^bj_"))
