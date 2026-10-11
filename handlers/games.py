"""Minigiochi single player: Blackjack, Slot, Wordle, Mastermind, Heist."""
import asyncio
import random

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler, ContextTypes

from database_quiz import WORDS
from state import (
    BLACKJACK_GAMES, WORDLE_GAMES, MASTERMIND_GAMES,
    HEIST_GAMES, USER_INVENTORIES, USER_DATA
)
from storage import get_user_coins, add_user_coins, get_user_key, save_db
from utils import verify_user_lock

# --- HELPER STATISTICHE ---
def record_single_result(chat_id: int, user_id: int, won: bool, profit: int, is_casino: bool = True):
    key = get_user_key(chat_id, user_id)
    if key not in USER_DATA:
        USER_DATA[key] = {"coins": 50, "last_daily": "", "quizzes_won": 0, "casino_wins": 0, "duels_wins": 0, "net_profit": 0, "single_played": 0, "single_won": 0}
    
    u = USER_DATA[key]
    u["single_played"] = u.get("single_played", 0) + 1
    u["net_profit"] = u.get("net_profit", 0) + profit
    
    if won:
        u["single_won"] = u.get("single_won", 0) + 1
        if is_casino:
            u["casino_wins"] = u.get("casino_wins", 0) + 1
    save_db()

# --- HELPER BLACKJACK (Calcolo corretto degli assi) ---
def hand_value(hand: list) -> int:
    total, aces = sum(hand), hand.count(11)
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total

# --- HELPER HEIST (Tastiera casuale anti-farming) ---
def _choice_keyboard(stage: str, labels: list, user_id: int):
    win_idx = random.randrange(len(labels))
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(lbl, callback_data=f"heist_{stage}res_{'win' if i == win_idx else 'fail'}_{user_id}")]
        for i, lbl in enumerate(labels)
    ])


# =====================================================================
# BLACKJACK CON PUNTATE PERSONALIZZATE E ASSI CORRETTI
# =====================================================================
BJ_CARDS = [2, 3, 4, 5, 6, 7, 8, 9, 10, 10, 10, 10, 11]

async def start_bj_from_hub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    parts = query.data.split("_")
    owner_id = int(parts[2]) if len(parts) > 2 else query.from_user.id

    if not await verify_user_lock(query, owner_id): return
    chat_id = query.message.chat_id
    coins = get_user_coins(chat_id, owner_id)

    text = (
        "🃏 <b>BLACKJACK 21 - SCEGLI LA PUNTATA</b> 💰\n\n"
        f"💳 Saldo disponibile: <b>{coins} $SDG</b>\n\n"
        "<i>Seleziona quanto vuoi puntare per questa mano:</i>"
    )
    keyboard = [
        [InlineKeyboardButton("10 $SDG", callback_data=f"bj_bet_10_{owner_id}"), InlineKeyboardButton("25 $SDG", callback_data=f"bj_bet_25_{owner_id}")],
        [InlineKeyboardButton("50 $SDG", callback_data=f"bj_bet_50_{owner_id}"), InlineKeyboardButton("100 $SDG", callback_data=f"bj_bet_100_{owner_id}")],
        [InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{owner_id}")]
    ]
    try:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')
    except Exception:
        pass


async def handle_bj_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    parts = data.split("_")
    action = parts[1]
    owner_id = int(parts[-1])

    if not await verify_user_lock(query, owner_id): return
    chat_id = query.message.chat_id
    user_id = query.from_user.id
    game_key = f"{chat_id}_{user_id}"

    if action == "bet":
        bet = int(parts[2])
        coins = get_user_coins(chat_id, owner_id)
        if coins < bet:
            await query.answer("❌ Non hai abbastanza $SDG per questa puntata!", show_alert=True)
            return

        add_user_coins(chat_id, owner_id, -bet)
        player_hand = [random.choice(BJ_CARDS), random.choice(BJ_CARDS)]
        dealer_hand = [random.choice(BJ_CARDS)]

        BLACKJACK_GAMES[game_key] = {
            "player_id": user_id, "bet": bet, "player_hand": player_hand, "dealer_hand": dealer_hand
        }

        # Controllo Blackjack Naturale
        if hand_value(player_hand) == 21:
            win_amount = int(bet * 2.5)
            add_user_coins(chat_id, owner_id, win_amount)
            net_profit = win_amount - bet
            record_single_result(chat_id, owner_id, True, net_profit)
            del BLACKJACK_GAMES[game_key]
            keyboard = [
                [InlineKeyboardButton("🃏 Gioca Ancora", callback_data=f"start_bj_{owner_id}")],
                [InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{owner_id}")]
            ]
            await query.edit_message_text(
                f"🃏 <b>BLACKJACK 21</b>\n👤 Carte: {player_hand} (Totale: <b>21</b>)\n🤖 Banco: {dealer_hand}\n\n"
                f"✨ <b>BLACKJACK NATURALE!</b> Hai vinto <b>+{net_profit} $SDG</b>!",
                reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML'
            )
            return

        keyboard = [[
            InlineKeyboardButton("🎴 Carta", callback_data=f"bj_hit_{owner_id}"),
            InlineKeyboardButton("✋ Stai", callback_data=f"bj_stand_{owner_id}")
        ]]
        await query.edit_message_text(
            f"🃏 <b>BLACKJACK 21</b> (Puntata: <code>{bet} $SDG</code>)\n\n"
            f"👤 Giocatore: <b>{query.from_user.first_name}</b>\n"
            f"🎎 Carte: {player_hand} (Totale: <b>{hand_value(player_hand)}</b>)\n"
            f"🤖 Banco: [{dealer_hand[0]}, ?]\n\nCosa fai?",
            reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML'
        )
        return

    if game_key not in BLACKJACK_GAMES:
        await query.edit_message_text("❌ Partita terminata.")
        return

    game = BLACKJACK_GAMES[game_key]
    bet = game["bet"]
    end_keyboard = [
        [InlineKeyboardButton("🔂 Rigioca", callback_data=f"start_bj_{owner_id}")],
        [InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{owner_id}")]
    ]

    if action == "hit":
        game["player_hand"].append(random.choice(BJ_CARDS))
        score = hand_value(game["player_hand"])

        if score > 21:
            record_single_result(chat_id, user_id, False, -bet)
            del BLACKJACK_GAMES[game_key]
            await query.edit_message_text(
                f"💥 <b>SBALLATO!</b> ({score})\nHai perso <b>-{bet} $SDG</b>!",
                reply_markup=InlineKeyboardMarkup(end_keyboard), parse_mode='HTML'
            )
        else:
            keyboard = [[
                InlineKeyboardButton("🎴 Carta", callback_data=f"bj_hit_{owner_id}"),
                InlineKeyboardButton("✋ Stai", callback_data=f"bj_stand_{owner_id}")
            ]]
            await query.edit_message_text(
                f"🃏 <b>BLACKJACK 21</b> (Puntata: {bet} $SDG)\n\nCarte: {game['player_hand']} ({score})\n"
                f"Banco: [{game['dealer_hand'][0]}, ?]",
                reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML'
            )

    elif action == "stand":
        player_score = hand_value(game["player_hand"])
        dealer_hand = game["dealer_hand"]
        while hand_value(dealer_hand) < 17:
            dealer_hand.append(random.choice(BJ_CARDS))
        dealer_score = hand_value(dealer_hand)
        del BLACKJACK_GAMES[game_key]

        if dealer_score > 21 or player_score > dealer_score:
            win_amount = bet * 2
            add_user_coins(chat_id, user_id, win_amount)
            net_profit = bet
            record_single_result(chat_id, user_id, True, net_profit)
            await query.edit_message_text(
                f"🏆 <b>VITTORIA!</b> Tu: {player_score} | Banco: {dealer_score} ({dealer_hand})\n"
                f"Hai vinto <b>+💳 {net_profit} $SDG</b>!",
                reply_markup=InlineKeyboardMarkup(end_keyboard), parse_mode='HTML'
            )
        elif player_score < dealer_score:
            record_single_result(chat_id, user_id, False, -bet)
            await query.edit_message_text(
                f"❌ <b>SCONFITTA!</b> Tu: {player_score} | Banco: {dealer_score} ({dealer_hand})\n"
                f"Hai perso <b>-{bet} $SDG</b>.",
                reply_markup=InlineKeyboardMarkup(end_keyboard), parse_mode='HTML'
            )
        else:
            add_user_coins(chat_id, user_id, bet)
            record_single_result(chat_id, user_id, False, 0)
            await query.edit_message_text(
                f"⚖️ <b>PAREGGIO!</b> Punti: {player_score}\nPuntata di {bet} $SDG restituita.",
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
    await asyncio.sleep(0.5)

    await query.edit_message_text(
        f"🎰 <b>SLOT MACHINE 777</b> 🎰\n👤 Player: <b>{user.first_name}</b>\n\n"
        f"[ {r1} | {r2} | 🔄 ]\n\n<i>Giro rulli in corso...</i>",
        parse_mode="HTML"
    )
    await asyncio.sleep(0.5)

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
            record_single_result(chat_id, user.id, True, 140)
            text += "🔥 <b>JACKPOT SUPREMO 777!</b> 🔥 Hai vinto <b>+💳 140 $SDG</b>!"
        else:
            add_user_coins(chat_id, user.id, 30)
            record_single_result(chat_id, user.id, True, 20)
            text += "🎉 <b>TRIPLETTA VINCENTE!</b> Hai vinto <b>+💳 20 $SDG</b>!"
    elif r1 == r2 or r2 == r3 or r1 == r3:
        add_user_coins(chat_id, user.id, 10)
        record_single_result(chat_id, user.id, False, 0)  # La doppietta ora conta come pareggio/non-vittoria statistica
        text += "✨ <b>DOPPIETTA!</b> Recuperi i tuoi 10 $SDG."
    else:
        record_single_result(chat_id, user.id, False, -10)
        text += "💸 <b>NESSUNA COMBINAZIONE!</b> Hai perso 10 $SDG."

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(end_keyboard), parse_mode="HTML")


# =====================================================================
# WORDLE EXPRESS E MASTERMIND (Gestiti nel Text Router in system.py)
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
# SDROGO HEIST (Mistero Totale e Anti-Farming)
# =====================================================================
async def handle_heist_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    parts = query.data.split("_")
    stage = parts[1]
    owner_id = int(parts[-1])

    if query.from_user.id != owner_id:
        return

    user_id = query.from_user.id
    if user_id not in HEIST_GAMES:
        await query.edit_message_text("❌ Sessione Rapina terminata.")
        return

    game = HEIST_GAMES[user_id]
    chat_id = game["chat_id"]

    if stage == "lvl1":
        labels = ["🔴 Cavo Rosso", "🔵 Cavo Blu", "🟡 Cavo Giallo"]
        await query.edit_message_text(
            "🔓 <b>LIVELLO 1: DISATTIVAZIONE ALLARME</b>\n\nQuale cavo decidi di tagliare?",
            reply_markup=_choice_keyboard("lvl1", labels, user_id), parse_mode="HTML"
        )

    elif stage == "lvl1res":
        outcome = parts[2]
        if outcome == "fail":
            del HEIST_GAMES[user_id]
            await query.edit_message_text("💥 <b>BZZZ! ALLARME SCATTATO!</b> Le guardie ti hanno preso. Fuga fallita!", parse_mode="HTML")
        else:
            game["level"] = 2
            keyboard = [
                [InlineKeyboardButton("💰 CASHOUT (Prendi 💳 50 $SDG ed esci)", callback_data=f"heist_cashout_50_{user_id}")],
                [InlineKeyboardButton("🔥 RISCHIA IL LIVELLO 2 (Guardia)", callback_data=f"heist_lvl2_{user_id}")]
            ]
            await query.edit_message_text(
                "✅ <b>LIVELLO 1 SUPERATO!</b> (Cavo corretto)\nPremio accumulato: <code>💳 50 $SDG</code>.\n\nCosa vuoi fare?",
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
        labels = ["🚪 Porta A", "🚪 Porta B", "🚪 Porta C"]
        await query.edit_message_text(
            "⚡ <b>LIVELLO 3: CAMPO LASER</b>\n\nTre porte davanti a te. Solo una non ha i laser attivi!",
            reply_markup=_choice_keyboard("lvl3", labels, user_id), parse_mode="HTML"
        )

    elif stage == "lvl3res":
        outcome = parts[2]
        if outcome == "fail":
            del HEIST_GAMES[user_id]
            await query.edit_message_text("⚡ <b>ZAAAP! COLPITO DAL LASER!</b> L'allarme è scattato. Fuga fallita!", parse_mode="HTML")
        else:
            game["level"] = 4
            keyboard = [
                [InlineKeyboardButton("💰 CASHOUT (Prendi 💳 180 $SDG ed esci)", callback_data=f"heist_cashout_180_{user_id}")],
                [InlineKeyboardButton("🔥 RISCHIA IL LIVELLO 4 (Cassaforte)", callback_data=f"heist_lvl4_{user_id}")]
            ]
            await query.edit_message_text(
                "⚡ <b>LIVELLO 3 SUPERATO!</b> (Percorso sicuro)\nPremio accumulato: <code>💳 180 $SDG</code>.\n\nCosa vuoi fare?",
                reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML"
            )

    elif stage == "lvl4":
        labels = ["🔐 Codice 4-8-1", "🔐 Codice 7-7-7", "🔐 Codice 1-2-3"]
        await query.edit_message_text(
            "🔐 <b>LIVELLO 4: LA CASSAFORTE</b>\n\nScegli la combinazione a intuito per sbloccare la porta blindata:",
            reply_markup=_choice_keyboard("lvl4", labels, user_id), parse_mode="HTML"
        )

    elif stage == "lvl4res":
        outcome = parts[2]
        if outcome == "fail":
            del HEIST_GAMES[user_id]
            await query.edit_message_text("💥 <b>COMBINAZIONE ERRATA!</b> La cassaforte si è sigillata. Fuga fallita!", parse_mode="HTML")
        else:
            game["level"] = 5
            keyboard = [
                [InlineKeyboardButton("💰 CASHOUT (Prendi 💳 300 $SDG ed esci)", callback_data=f"heist_cashout_300_{user_id}")],
                [InlineKeyboardButton("🔥 SFIDA IL LIVELLO 5 FINALE!", callback_data=f"heist_lvl5_{user_id}")]
            ]
            await query.edit_message_text(
                "🔐 <b>LIVELLO 4 SUPERATO!</b> (Cassaforte aperta)\nPremio accumulato: <code>💳 300 $SDG</code>.\n\nSei ad un passo dalla gloria!",
                reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML"
            )

    elif stage == "lvl5":
        labels = ["🚁 Elicottero sul Tetto", "🚗 Fuga in Tunnel"]
        await query.edit_message_text(
            "🚁 <b>LIVELLO 5: LA FUGA FINALE</b>\n\nCome scappi col bottino? Scegli il veicolo giusto:",
            reply_markup=_choice_keyboard("lvl5", labels, user_id), parse_mode="HTML"
        )

    elif stage == "lvl5res":
        outcome = parts[2]
        if outcome == "fail":
            del HEIST_GAMES[user_id]
            await query.edit_message_text("🚔 <b>SCELTA SBAGLIATA!</b> La via era sorvegliata dalla SWAT! Fuga fallita all'ultimo secondo!", parse_mode="HTML")
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

    elif stage == "cashout":
        amount = int(parts[2])
        del HEIST_GAMES[user_id]
        add_user_coins(chat_id, user_id, amount)
        await query.edit_message_text(
            f"💰 <b>CASHOUT EFFETTUATO!</b> Ti ritiri dalla rapina incassando <b>+💳 {amount} $SDG</b>!",
            parse_mode="HTML"
        )

def register(app):
    app.add_handler(CallbackQueryHandler(start_bj_from_hub, pattern="^start_bj_"))
    app.add_handler(CallbackQueryHandler(handle_bj_callback, pattern="^bj_"))
    app.add_handler(CallbackQueryHandler(start_slot_from_hub, pattern="^start_slot_"))
    app.add_handler(CallbackQueryHandler(start_wordle_from_hub, pattern="^start_wordle_"))
    app.add_handler(CallbackQueryHandler(start_mastermind_from_hub, pattern="^start_mm_"))
    app.add_handler(CallbackQueryHandler(handle_heist_callback, pattern="^heist_"))
