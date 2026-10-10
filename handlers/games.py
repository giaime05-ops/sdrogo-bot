"""Minigiochi single player: Blackjack, Slot, Wordle, Mastermind, Heist."""
import asyncio
import random

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler, ContextTypes

from database_quiz import WORDS
from state import (
    BLACKJACK_GAMES, WORDLE_GAMES, MASTERMIND_GAMES,
    HEIST_GAMES, USER_INVENTORIES,
)
from storage import get_user_coins, add_user_coins
from utils import verify_user_lock


# =====================================================================
# BLACKJACK
# =====================================================================
BJ_CARDS = [2, 3, 4, 5, 6, 7, 8, 9, 10, 10, 10, 10, 11]


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
    player_hand = [random.choice(BJ_CARDS), random.choice(BJ_CARDS)]
    dealer_hand = [random.choice(BJ_CARDS)]

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
    end_keyboard = [
        [InlineKeyboardButton("🔂 Rigioca (10 $SDG)", callback_data=f"start_bj_{owner_id}")],
        [InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{owner_id}")]
    ]

    if action == "hit":
        game["player_hand"].append(random.choice(BJ_CARDS))
        score = sum(game["player_hand"])

        if score > 21:
            del BLACKJACK_GAMES[game_key]
            await query.edit_message_text(
                f"💥 <b>SBALLATO!</b> ({score})\nHai perso 10 $SDG!",
                reply_markup=InlineKeyboardMarkup(end_keyboard), parse_mode='HTML'
            )
        else:
            keyboard = [[
                InlineKeyboardButton("🎴 Carta", callback_data=f"bj_hit_{owner_id}"),
                InlineKeyboardButton("✋ Stai", callback_data=f"bj_stand_{owner_id}")
            ]]
            await query.edit_message_text(
                f"🃏 <b>BLACKJACK 21</b>\n\nCarte: {game['player_hand']} ({score})\n"
                f"Banco: [{game['dealer_hand'][0]}, ?]",
                reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML'
            )

    elif action == "stand":
        player_score = sum(game["player_hand"])
        dealer_hand = game["dealer_hand"]
        while sum(dealer_hand) < 17:
            dealer_hand.append(random.choice(BJ_CARDS))
        dealer_score = sum(dealer_hand)
        del BLACKJACK_GAMES[game_key]

        if dealer_score > 21 or player_score > dealer_score:
            add_user_coins(chat_id, user_id, 15)
            await query.edit_message_text(
                f"🏆 <b>VITTORIA!</b> Tu: {player_score} | Banco: {dealer_score}\n"
                f"Hai vinto <b>+💳 15 $SDG</b>!",
                reply_markup=InlineKeyboardMarkup(end_keyboard), parse_mode='HTML'
            )
        elif player_score < dealer_score:
            await query.edit_message_text(
                f"❌ <b>SCONFITTA!</b> Tu: {player_score} | Banco: {dealer_score}\n"
                f"Hai perso la puntata.",
                reply_markup=InlineKeyboardMarkup(end_keyboard), parse_mode='HTML'
            )
        else:
            add_user_coins(chat_id, user_id, 10)
            await query.edit_message_text(
                f"⚖️ <b>PAREGGIO!</b> Punti: {player_score}\nPuntata di 10 $SDG restituita.",
                reply_markup=InlineKeyboardMarkup(end_keyboard), parse_mode='HTML'
            )


# =====================================================================
# SLOT MACHINE 777
# =====================================================================
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

    text = (
        f"🎰 <b>SLOT MACHINE 777</b> 🎰\n👤 Player: <b>{user.first_name}</b>\n\n"
        f"[ {r1} | {r2} | {r3} ]\n\n"
    )

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


# =====================================================================
# WORDLE EXPRESS  (la logica dei tentativi sta in system.py -> text router)
# =====================================================================
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


# =====================================================================
# MASTERMIND EXPRESS  (idem: tentativi gestiti dal text router)
# =====================================================================
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


# =====================================================================
# SDROGO HEIST  (l'acquisto del pass sta in hub.py -> shop_buy_callback)
# =====================================================================
async def handle_heist_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    parts = query.data.split("_")
    stage = parts[1]
    owner_id = int(parts[-1])          # l'ID utente è SEMPRE l'ultimo elemento

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
        await query.edit_message_text(
            "🔓 <b>LIVELLO 1: DISATTIVAZIONE ALLARME</b>\n\nQuale cavo tagli per disattivare l'allarme?",
            reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML"
        )

    elif stage == "lvl1res":
        outcome = parts[2]
        if outcome == "fail":
            del HEIST_GAMES[user_id]
            await query.edit_message_text("💥 <b>ALLARME SCATTATO!</b> Le guardie ti hanno preso. Fuga fallita!", parse_mode="HTML")
        else:
            game["level"] = 2
            keyboard = [
                [InlineKeyboardButton("💰 CASHOUT (Prendi 💳 50 $SDG ed esci)", callback_data=f"heist_cashout_50_{user_id}")],
                [InlineKeyboardButton("🔥 RISCHIA IL LIVELLO 2 (Guardia)", callback_data=f"heist_lvl2_{user_id}")]
            ]
            await query.edit_message_text(
                "✅ <b>LIVELLO 1 SUPERATO!</b>\nPremio accumulato: <code>💳 50 $SDG</code>.\n\nCosa vuoi fare?",
                reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML"
            )

    elif stage == "lvl2":
        p_hand = random.randint(15, 21)
        g_hand = random.randint(14, 21)

        if p_hand >= g_hand:
            game["level"] = 3
            keyboard = [
                [InlineKeyboardButton("💰 CASHOUT (Prendi 💳 100 $SDG ed esci)", callback_data=f"heist_cashout_100_{user_id}")],
                [InlineKeyboardButton("🔥 RISCHIA IL LIVELLO 3 (Laser)", callback_data=f"heist_lvl3_{user_id}")]
            ]
            await query.edit_message_text(
                f"👮 <b>LIVELLO 2 SUPERATO!</b>\nHai messo KO la guardia ({p_hand} vs {g_hand})!\n"
                f"Premio accumulato: <code>💳 100 $SDG</code>.",
                reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML"
            )
        else:
            del HEIST_GAMES[user_id]
            await query.edit_message_text(
                f"👮 <b>LA GUARDIA TI HA VISTO!</b> ({g_hand} vs {p_hand})\nSei stato arrestato! Fuga fallita.",
                parse_mode="HTML"
            )

    elif stage == "lvl3":
        keyboard = [
            [InlineKeyboardButton("🚪 Porta A", callback_data=f"heist_lvl3res_fail_{user_id}")],
            [InlineKeyboardButton("🚪 Porta B", callback_data=f"heist_lvl3res_win_{user_id}")],
            [InlineKeyboardButton("🚪 Porta C", callback_data=f"heist_lvl3res_fail_{user_id}")]
        ]
        random.shuffle(keyboard)
        await query.edit_message_text(
            "⚡ <b>LIVELLO 3: CAMPO LASER</b>\n\nTre porte davanti a te. Solo una non ha i laser attivi!",
            reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML"
        )

    elif stage == "lvl3res":
        outcome = parts[2]
        if outcome == "fail":
            del HEIST_GAMES[user_id]
            await query.edit_message_text("⚡ <b>COLPITO DAL LASER!</b> L'allarme è scattato. Fuga fallita!", parse_mode="HTML")
        else:
            game["level"] = 4
            keyboard = [
                [InlineKeyboardButton("💰 CASHOUT (Prendi 💳 180 $SDG ed esci)", callback_data=f"heist_cashout_180_{user_id}")],
                [InlineKeyboardButton("🔥 RISCHIA IL LIVELLO 4 (Cassaforte)", callback_data=f"heist_lvl4_{user_id}")]
            ]
            await query.edit_message_text(
                "⚡ <b>LIVELLO 3 SUPERATO!</b>\nPremio accumulato: <code>💳 180 $SDG</code>.\n\nCosa vuoi fare?",
                reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML"
            )

    elif stage == "lvl4":
        keyboard = [
            [InlineKeyboardButton("🔐 Codice 4-8-1 (Sbagliato)", callback_data=f"heist_lvl4res_fail_{user_id}")],
            [InlineKeyboardButton("🔐 Codice 7-7-7 (Sbagliato)", callback_data=f"heist_lvl4res_fail_{user_id}")],
            [InlineKeyboardButton("🔐 Codice 1-2-3 (Corretto)", callback_data=f"heist_lvl4res_win_{user_id}")]
        ]
        random.shuffle(keyboard)
        await query.edit_message_text(
            "🔐 <b>LIVELLO 4: LA CASSAFORTE</b>\n\nTrova la combinazione corretta prima che scada il tempo!",
            reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML"
        )

    elif stage == "lvl4res":
        outcome = parts[2]
        if outcome == "fail":
            del HEIST_GAMES[user_id]
            await query.edit_message_text("💥 <b>COMBINAZIONE ERRATA!</b> La cassaforte si è bloccata. Fuga fallita!", parse_mode="HTML")
        else:
            game["level"] = 5
            keyboard = [
                [InlineKeyboardButton("💰 CASHOUT (Prendi 💳 300 $SDG ed esci)", callback_data=f"heist_cashout_300_{user_id}")],
                [InlineKeyboardButton("🔥 SFIDA IL LIVELLO 5 FINALE!", callback_data=f"heist_lvl5_{user_id}")]
            ]
            await query.edit_message_text(
                "🔐 <b>LIVELLO 4 SUPERATO!</b>\nPremio accumulato: <code>💳 300 $SDG</code>.\n\nSei ad un passo dalla gloria!",
                reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML"
            )

    elif stage == "lvl5":
        keyboard = [
            [InlineKeyboardButton("🚁 Elicottero sul Tetto", callback_data=f"heist_lvl5res_win_{user_id}")],
            [InlineKeyboardButton("🚗 Fuga in Tunnel", callback_data=f"heist_lvl5res_fail_{user_id}")]
        ]
        await query.edit_message_text(
            "🚁 <b>LIVELLO 5: LA FUGA FINALE</b>\n\nCome scappi col bottino?",
            reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML"
        )

    elif stage == "lvl5res":
        outcome = parts[2]
        if outcome == "fail":
            del HEIST_GAMES[user_id]
            await query.edit_message_text("🚔 <b>LA POLIZIA TI HA CIRCONDATO IN TUNNEL!</b> Fuga fallita all'ultimo secondo!", parse_mode="HTML")
        else:
            del HEIST_GAMES[user_id]
            add_user_coins(chat_id, user_id, 600)

            inv_key = f"{chat_id}_{user_id}"
            if inv_key not in USER_INVENTORIES:
                USER_INVENTORIES[inv_key] = {"titles": 0, "persecutes": 0, "stars": 0}
            USER_INVENTORIES[inv_key]["stars"] = USER_INVENTORIES[inv_key].get("stars", 0) + 1
            USER_INVENTORIES[inv_key]["titles"] += 1
            USER_INVENTORIES[inv_key]["persecutes"] += 1

            await query.edit_message_text(
                "🏆 <b>RAPINA PERFETTA COMPLETATA!</b>\n"
                "Hai vinto +600 $SDG, 1 Titolo e 1 Persecuzione Gratis + 1 STELLA ⭐!",
                parse_mode="HTML"
            )

            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"👑 <b>COLPO DEL SECOLO!</b> 🏢\n\n"
                        f"<b>{query.from_user.first_name}</b> ha svaligiato il Caveau di Sdrogo Heist "
                        f"arrivando al 5° Livello!\n"
                        f"Guadagna <b>💳 600 $SDG</b> e 1 STELLA ⭐ di prestigio in classifica!"
                    ),
                    parse_mode="HTML"
                )
            except Exception:
                pass

    elif stage == "cashout":
        amount = int(parts[2])
        del HEIST_GAMES[user_id]
        add_user_coins(chat_id, user_id, amount)
        await query.edit_message_text(
            f"💰 <b>CASHOUT EFFETTUATO!</b> Ti ritiri dalla rapina incassando <b>+💳 {amount} $SDG</b>!",
            parse_mode="HTML"
        )


# =====================================================================
# REGISTRAZIONE
# =====================================================================
def register(app):
    app.add_handler(CallbackQueryHandler(start_bj_from_hub, pattern="^start_bj_"))
    app.add_handler(CallbackQueryHandler(handle_bj_callback, pattern="^bj_"))
    app.add_handler(CallbackQueryHandler(start_slot_from_hub, pattern="^start_slot_"))
    app.add_handler(CallbackQueryHandler(start_wordle_from_hub, pattern="^start_wordle_"))
    app.add_handler(CallbackQueryHandler(start_mastermind_from_hub, pattern="^start_mm_"))
    app.add_handler(CallbackQueryHandler(handle_heist_callback, pattern="^heist_"))
