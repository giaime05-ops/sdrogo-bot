"""Minigiochi single player: Blackjack, Slot, Wordle, Mastermind, Heist."""
import asyncio
import random
import time

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler, ContextTypes

from database_quiz import WORDS
from state import (
    BLACKJACK_GAMES, WORDLE_GAMES, MASTERMIND_GAMES,
    HEIST_GAMES, USER_INVENTORIES
)
from storage import get_user_coins, add_user_coins, get_user_data_dict, save_db
from utils import verify_user_lock

# --- HELPER STATISTICHE ---
def record_single_result(chat_id: int, user_id: int, won: bool, profit: int, is_casino: bool = True):
    u = get_user_data_dict(chat_id, user_id)
    u["single_played"] = u.get("single_played", 0) + 1
    u["net_profit"] = u.get("net_profit", 0) + profit

    if won:
        u["single_won"] = u.get("single_won", 0) + 1
        if is_casino:
            u["casino_wins"] = u.get("casino_wins", 0) + 1
    save_db()

# --- HELPER ANTI DOPPIO TAP ---
def recently_started(store: dict, key: str, secs: int = 60) -> bool:
    g = store.get(key)
    return bool(g) and time.time() - g.get("ts", 0) < secs

# --- HELPER BLACKJACK (Calcolo corretto degli assi) ---
def hand_value(hand: list) -> int:
    total, aces = sum(hand), hand.count(11)
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total

# --- HELPER HEIST ---
def _choice_keyboard(stage: str, labels: list, user_id: int, n_win: int = 1):
    """Tastiera con i bottoni vincenti scelti a caso: la risposta giusta non è mai riconoscibile."""
    win_idx = set(random.sample(range(len(labels)), n_win))
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(lbl, callback_data=f"heist_{stage}res_{'win' if i in win_idx else 'fail'}_{user_id}")]
        for i, lbl in enumerate(labels)
    ])

async def _safe_edit(query, text, keyboard=None):
    try:
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="HTML")
    except Exception:
        pass


# =====================================================================
# BLACKJACK CON PUNTATE PERSONALIZZATE E ASSI CORRETTI
# =====================================================================
BJ_CARDS = [2, 3, 4, 5, 6, 7, 8, 9, 10, 10, 10, 10, 11]

async def start_bj_from_hub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    parts = query.data.split("_")
    owner_id = int(parts[2]) if len(parts) > 2 else query.from_user.id

    if not await verify_user_lock(query, owner_id): return
    await query.answer()
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
        if recently_started(BLACKJACK_GAMES, game_key, 120):
            await query.answer("⏳ Hai già una mano in corso!", show_alert=True)
            return
        coins = get_user_coins(chat_id, owner_id)
        if coins < bet:
            await query.answer("❌ Non hai abbastanza $SDG per questa puntata!", show_alert=True)
            return
        await query.answer()

        add_user_coins(chat_id, owner_id, -bet)
        player_hand = [random.choice(BJ_CARDS), random.choice(BJ_CARDS)]
        dealer_hand = [random.choice(BJ_CARDS)]

        BLACKJACK_GAMES[game_key] = {
            "player_id": user_id, "bet": bet, "ts": time.time(),
            "player_hand": player_hand, "dealer_hand": dealer_hand
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
        await query.answer()
        try:
            await query.edit_message_text("❌ Partita terminata.")
        except Exception:
            pass
        return

    await query.answer()
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
SLOT_BUSY = set()

async def start_slot_from_hub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    parts = query.data.split("_")
    owner_id = int(parts[2]) if len(parts) > 2 else query.from_user.id

    if not await verify_user_lock(query, owner_id): return
    user = query.from_user
    chat_id = query.message.chat_id
    slot_key = f"{chat_id}_{user.id}"

    if slot_key in SLOT_BUSY:
        await query.answer("⏳ I rulli stanno ancora girando!", show_alert=True)
        return
    if get_user_coins(chat_id, user.id) < 10:
        await query.answer("❌ Servono 10 $SDG per girare la Slot!", show_alert=True)
        return
    await query.answer()

    SLOT_BUSY.add(slot_key)
    try:
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
            record_single_result(chat_id, user.id, False, 0)  # La doppietta conta come pareggio, non come vittoria
            text += "✨ <b>DOPPIETTA!</b> Recuperi i tuoi 10 $SDG."
        else:
            record_single_result(chat_id, user.id, False, -10)
            text += "💸 <b>NESSUNA COMBINAZIONE!</b> Hai perso 10 $SDG."

        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(end_keyboard), parse_mode="HTML")
    finally:
        SLOT_BUSY.discard(slot_key)


# =====================================================================
# WORDLE EXPRESS E MASTERMIND (Tentativi gestiti nel Text Router in system.py)
# =====================================================================
async def start_wordle_from_hub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    parts = query.data.split("_")
    owner_id = int(parts[2]) if len(parts) > 2 else query.from_user.id

    if not await verify_user_lock(query, owner_id): return
    user = query.from_user
    chat_id = query.message.chat_id
    game_key = f"{chat_id}_{user.id}"

    if recently_started(WORDLE_GAMES, game_key):
        await query.answer("⏳ Hai già una partita a Wordle in corso!", show_alert=True)
        return
    if get_user_coins(chat_id, user.id) < 10:
        await query.answer("❌ Servono 10 $SDG per giocare a Wordle!", show_alert=True)
        return
    await query.answer()

    add_user_coins(chat_id, user.id, -10)
    secret_word = random.choice(WORDS)

    WORDLE_GAMES[game_key] = {
        "player_id": user.id, "secret": secret_word,
        "attempts": 0, "history": [], "ts": time.time()
    }

    await query.edit_message_text(
        "🔠 <b>WORDLE EXPRESS</b> (Puntata: 10 $SDG)\n\n"
        "Ho scelto una parola di <b>5 lettere</b>!\n"
        "Scrivila direttamente in chat per tentare (5 tentativi).",
        parse_mode="HTML"
    )

async def start_mastermind_from_hub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    parts = query.data.split("_")
    owner_id = int(parts[2]) if len(parts) > 2 else query.from_user.id

    if not await verify_user_lock(query, owner_id): return
    user = query.from_user
    chat_id = query.message.chat_id
    game_key = f"{chat_id}_{user.id}"

    if recently_started(MASTERMIND_GAMES, game_key):
        await query.answer("⏳ Hai già una partita a Mastermind in corso!", show_alert=True)
        return
    if get_user_coins(chat_id, user.id) < 10:
        await query.answer("❌ Servono 10 $SDG per giocare a Mastermind!", show_alert=True)
        return
    await query.answer()

    add_user_coins(chat_id, user.id, -10)
    digits = list("0123456789")
    random.shuffle(digits)
    secret_code = "".join(digits[:3])

    MASTERMIND_GAMES[game_key] = {
        "player_id": user.id, "secret": secret_code,
        "attempts": 0, "history": [], "ts": time.time()
    }

    await query.edit_message_text(
        "🔐 <b>MASTERMIND EXPRESS</b> (Puntata: 10 $SDG)\n\n"
        "Ho scelto un codice segreto di <b>3 cifre uniche</b>!\n"
        "Scrivilo direttamente in chat per tentare (5 tentativi).",
        parse_mode="HTML"
    )


# =====================================================================
# SDROGO HEIST (bilanciato: ritorno atteso ~95% con costo, premi e probabilità sotto)
# =====================================================================
# Probabilità di successo: L1 = 2/3, L2 = 62,5%, L3 = 2/3, L4 = 2/3, L5 = 50%.
# I cashout sono calibrati sul valore atteso: qualunque strategia (fermarsi prima o
# arrivare in fondo) rende circa uguale, quindi non esiste un modo di "farmare" il gioco.
# Il costo del pass deve coincidere con quello scalato in hub.py (shop_buy_callback).
HEIST_COST = 150
HEIST_PRIZES = {2: 175, 3: 230, 4: 300, 5: 500}   # cashout disponibile dopo aver superato il livello (chiave = livello successivo)
HEIST_FINAL = 1000

HEIST_CHOICES = {
    1: ("🔓 <b>LIVELLO 1: DISATTIVAZIONE ALLARME</b>\n\nUno solo dei tre cavi fa scattare l'allarme. Quale tagli?",
        ["🔴 Cavo Rosso", "🔵 Cavo Blu", "🟡 Cavo Giallo"], 2),
    3: ("⚡ <b>LIVELLO 3: CAMPO LASER</b>\n\nTre porte davanti a te. Una sola ha i laser attivi!",
        ["🚪 Porta A", "🚪 Porta B", "🚪 Porta C"], 2),
    4: ("🔐 <b>LIVELLO 4: LA CASSAFORTE</b>\n\nUna delle tre combinazioni blocca la cassaforte. Scegli a intuito:",
        ["🔐 Codice 4-8-1", "🔐 Codice 7-7-7", "🔐 Codice 1-2-3"], 2),
    5: ("🚁 <b>LIVELLO 5: LA FUGA FINALE</b>\n\nCome scappi col bottino? Una via è sorvegliata dalla SWAT:",
        ["🚁 Elicottero sul Tetto", "🚗 Fuga in Tunnel"], 1),
}

HEIST_FAIL_TEXT = {
    1: "💥 <b>BZZZ! ALLARME SCATTATO!</b> Le guardie ti hanno preso. Fuga fallita!",
    3: "⚡ <b>ZAAAP! COLPITO DAL LASER!</b> L'allarme è scattato. Fuga fallita!",
    4: "💥 <b>COMBINAZIONE ERRATA!</b> La cassaforte si è sigillata. Fuga fallita!",
    5: "🚔 <b>SCELTA SBAGLIATA!</b> La via era sorvegliata dalla SWAT! Fuga fallita all'ultimo secondo!",
}

HEIST_PASS_TEXT = {
    1: "✅ <b>LIVELLO 1 SUPERATO!</b> (Allarme disattivato)",
    3: "⚡ <b>LIVELLO 3 SUPERATO!</b> (Percorso sicuro)",
    4: "🔐 <b>LIVELLO 4 SUPERATO!</b> (Cassaforte aperta)\nSei ad un passo dalla gloria!",
}

HEIST_NEXT_LABEL = {
    2: "LIVELLO 2 (Guardia)",
    3: "LIVELLO 3 (Laser)",
    4: "LIVELLO 4 (Cassaforte)",
    5: "LIVELLO 5 FINALE",
}


async def _heist_show_cashout(query, user_id: int, passed_level: int, header: str):
    next_level = passed_level + 1
    prize = HEIST_PRIZES[next_level]
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"💰 CASHOUT (Prendi 💳 {prize} $SDG ed esci)", callback_data=f"heist_cashout_{prize}_{user_id}")],
        [InlineKeyboardButton(f"🔥 RISCHIA IL {HEIST_NEXT_LABEL[next_level]}", callback_data=f"heist_lvl{next_level}_{user_id}")]
    ])
    await _safe_edit(
        query,
        f"{header}\nPremio accumulato: <code>💳 {prize} $SDG</code>.\n\nCosa vuoi fare?",
        keyboard
    )


async def handle_heist_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    parts = query.data.split("_")
    stage = parts[1]
    owner_id = int(parts[-1])

    if query.from_user.id != owner_id:
        await query.answer("🛑 Questa non è la tua rapina!", show_alert=True)
        return

    user_id = query.from_user.id
    if user_id not in HEIST_GAMES:
        await query.answer()
        await _safe_edit(query, "❌ Sessione Rapina terminata.")
        return

    await query.answer()

    game = HEIST_GAMES[user_id]
    chat_id = game["chat_id"]
    level = game.get("level", 1)
    pending = game.get("pending")

    # ---- Presentazione di un livello a scelta (1, 3, 4, 5) ----
    if stage in ("lvl1", "lvl3", "lvl4", "lvl5"):
        n = int(stage[3])
        if level != n or pending:
            return   # bottone vecchio o già mostrato: ignora
        text, labels, n_win = HEIST_CHOICES[n]
        game["pending"] = n
        await _safe_edit(query, text, _choice_keyboard(f"lvl{n}", labels, user_id, n_win))

    # ---- Risultato di un livello a scelta ----
    elif stage in ("lvl1res", "lvl3res", "lvl4res", "lvl5res"):
        n = int(stage[3])
        if pending != n:
            return   # risultato già consumato o bottone vecchio
        game["pending"] = None
        outcome = parts[2]

        if outcome == "fail":
            del HEIST_GAMES[user_id]
            record_single_result(chat_id, user_id, False, -HEIST_COST)
            await _safe_edit(query, HEIST_FAIL_TEXT[n])

        elif n == 5:
            del HEIST_GAMES[user_id]
            add_user_coins(chat_id, user_id, HEIST_FINAL)
            record_single_result(chat_id, user_id, True, HEIST_FINAL - HEIST_COST)

            inv_key = f"{chat_id}_{user_id}"
            if inv_key not in USER_INVENTORIES:
                USER_INVENTORIES[inv_key] = {"titles": 0, "persecutes": 0, "stars": 0}
            USER_INVENTORIES[inv_key]["stars"] = USER_INVENTORIES[inv_key].get("stars", 0) + 1
            USER_INVENTORIES[inv_key]["titles"] = USER_INVENTORIES[inv_key].get("titles", 0) + 1
            USER_INVENTORIES[inv_key]["persecutes"] = USER_INVENTORIES[inv_key].get("persecutes", 0) + 1

            await _safe_edit(
                query,
                "🏆 <b>RAPINA PERFETTA COMPLETATA!</b>\n"
                f"Hai vinto +{HEIST_FINAL} $SDG, 1 Titolo e 1 Persecuzione Gratis + 1 STELLA ⭐!"
            )

            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"👑 <b>COLPO DEL SECOLO!</b> 🏢\n\n"
                        f"<b>{query.from_user.first_name}</b> ha svaligiato il Caveau di Sdrogo Heist "
                        f"arrivando al 5° Livello!\n"
                        f"Guadagna <b>💳 {HEIST_FINAL} $SDG</b> e 1 STELLA ⭐ di prestigio in classifica!"
                    ),
                    parse_mode="HTML"
                )
            except Exception:
                pass

        else:
            game["level"] = n + 1
            await _heist_show_cashout(query, user_id, n, HEIST_PASS_TEXT[n])

    # ---- Livello 2: scontro con la guardia (62,5% di successo) ----
    elif stage == "lvl2":
        if level != 2 or pending:
            return
        p_hand = random.randint(15, 21)
        g_hand = random.randint(14, 21)

        if p_hand >= g_hand:
            game["level"] = 3
            await _heist_show_cashout(
                query, user_id, 2,
                f"👮 <b>LIVELLO 2 SUPERATO!</b>\nHai messo KO la guardia ({p_hand} vs {g_hand})!"
            )
        else:
            del HEIST_GAMES[user_id]
            record_single_result(chat_id, user_id, False, -HEIST_COST)
            await _safe_edit(
                query,
                f"👮 <b>LA GUARDIA TI HA VISTO!</b> ({g_hand} vs {p_hand})\nSei stato arrestato! Fuga fallita."
            )

    # ---- Cashout (l'importo viene dal server, non dal bottone) ----
    elif stage == "cashout":
        if level not in HEIST_PRIZES or pending:
            return
        amount = HEIST_PRIZES[level]
        del HEIST_GAMES[user_id]
        add_user_coins(chat_id, user_id, amount)
        record_single_result(chat_id, user_id, amount > HEIST_COST, amount - HEIST_COST)
        await _safe_edit(
            query,
            f"💰 <b>CASHOUT EFFETTUATO!</b> Ti ritiri dalla rapina incassando <b>+💳 {amount} $SDG</b>!"
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
