"""Minigiochi single player: Blackjack, Slot, Wordle, Mastermind, Heist + giochi del Laboratorio Segreto."""
import asyncio
import html
import logging
import os
import random
import time
import unicodedata

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import RetryAfter
from telegram.ext import CallbackQueryHandler, ContextTypes

from state import (
    BLACKJACK_GAMES, WORDLE_GAMES, MASTERMIND_GAMES,
    HEIST_GAMES, USER_INVENTORIES,
    AVIATOR_GAMES, SCRATCH_GAMES, LAB_BUSY,
    game_enabled, MAINT_GAME_TEXT,
)
from storage import get_user_coins, add_user_coins, get_user_data_dict, save_db
from utils import verify_user_lock, is_admin, serialized

# --- PREMI WORDLE / MASTERMIND (costo di ingresso: 10 $SDG) ---
# Totale restituito in base ai tentativi usati (netti: +50, +40, +30, +20, +10)
WORDLE_REWARDS = {1: 60, 2: 50, 3: 40, 4: 30, 5: 20}
MASTERMIND_REWARDS = {1: 50, 2: 50, 3: 50, 4: 30, 5: 30}  # 50 entro il 3° tentativo, 30 al 4° o 5°
GAME_IDLE_SECS = 300   # Wordle / Mastermind / Blackjack inattivi da 5 minuti vengono chiusi con rimborso


# --- DIZIONARIO WORDLE (caricato UNA volta all'avvio) ---
# File con una parola per riga, nella root del progetto (accanto a main.py).
WORDLE_DICT_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "parole_italiane.txt"
)


def norm_word(w: str) -> str:
    """Maiuscolo e senza accenti (è -> E), tenendo solo le lettere A-Z."""
    w = unicodedata.normalize("NFD", w.strip().upper())
    return "".join(c for c in w if "A" <= c <= "Z")


def _load_wordle_dictionary() -> set:
    words = set()
    try:
        with open(WORDLE_DICT_FILE, encoding="utf-8-sig") as f:
            for line in f:
                raw = line.strip()
                if not raw or not raw.isalpha():      # scarta apostrofi, spazi, trattini, numeri
                    continue
                w = norm_word(raw)
                if len(w) == 5:
                    words.add(w)
        logging.info(f"Dizionario Wordle caricato: {len(words)} parole da 5 lettere ({WORDLE_DICT_FILE}).")
    except OSError as e:
        logging.error(f"Dizionario Wordle NON trovato ({WORDLE_DICT_FILE}): {e}. Il Wordle resterà non disponibile.")
    return words


WORD_SET = _load_wordle_dictionary()          # validazione tentativi (ricerca O(1))
WORDLE_WORDS = sorted(WORD_SET)               # elenco per estrarre la parola segreta
DICT_LOADED = bool(WORD_SET)


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

async def _safe_edit(query, text, keyboard=None, retries=2):
    """Modifica il messaggio riprovando se Telegram chiede di attendere (flood control)."""
    for _ in range(retries):
        try:
            await query.edit_message_text(text, reply_markup=keyboard, parse_mode="HTML")
            return True
        except RetryAfter as e:
            ra = e.retry_after
            secs = ra.total_seconds() if hasattr(ra, "total_seconds") else float(ra)
            await asyncio.sleep(min(secs, 10) + 0.5)
        except Exception:
            return False
    return False


# =====================================================================
# BLACKJACK CON PUNTATE PERSONALIZZATE E ASSI CORRETTI
# =====================================================================
BJ_CARDS = [2, 3, 4, 5, 6, 7, 8, 9, 10, 10, 10, 10, 11]

async def start_bj_from_hub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    parts = query.data.split("_")
    owner_id = int(parts[2]) if len(parts) > 2 else query.from_user.id

    if not await verify_user_lock(query, owner_id): return
    if not game_enabled("blackjack"):
        await query.answer(MAINT_GAME_TEXT, show_alert=True)
        return
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
    await _safe_edit(query, text, InlineKeyboardMarkup(keyboard))


@serialized("user")
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
        if bet not in (10, 25, 50, 100):
            await query.answer()
            return
        if not game_enabled("blackjack"):
            await query.answer(MAINT_GAME_TEXT, show_alert=True)
            return
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
            "player_id": user_id, "name": query.from_user.first_name or "un giocatore",
            "bet": bet, "ts": time.time(), "last": time.time(),
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
            await _safe_edit(
                query,
                f"🃏 <b>BLACKJACK 21</b>\n👤 Carte: {player_hand} (Totale: <b>21</b>)\n🤖 Banco: {dealer_hand}\n\n"
                f"✨ <b>BLACKJACK NATURALE!</b> Hai vinto <b>+{net_profit} $SDG</b>!",
                InlineKeyboardMarkup(keyboard)
            )
            return

        keyboard = [[
            InlineKeyboardButton("🎴 Carta", callback_data=f"bj_hit_{owner_id}"),
            InlineKeyboardButton("✋ Stai", callback_data=f"bj_stand_{owner_id}")
        ]]
        await _safe_edit(
            query,
            f"🃏 <b>BLACKJACK 21</b> (Puntata: <code>{bet} $SDG</code>)\n\n"
            f"👤 Giocatore: <b>{html.escape(query.from_user.first_name or 'Giocatore')}</b>\n"
            f"🎎 Carte: {player_hand} (Totale: <b>{hand_value(player_hand)}</b>)\n"
            f"🤖 Banco: [{dealer_hand[0]}, ?]\n\nCosa fai?",
            InlineKeyboardMarkup(keyboard)
        )
        return

    if game_key not in BLACKJACK_GAMES:
        await query.answer()
        await _safe_edit(
            query, "❌ Partita terminata.",
            InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{owner_id}")]])
        )
        return

    await query.answer()
    game = BLACKJACK_GAMES[game_key]
    game["last"] = time.time()
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
            await _safe_edit(
                query,
                f"💥 <b>SBALLATO!</b> ({score})\nHai perso <b>-{bet} $SDG</b>!",
                InlineKeyboardMarkup(end_keyboard)
            )
        else:
            keyboard = [[
                InlineKeyboardButton("🎴 Carta", callback_data=f"bj_hit_{owner_id}"),
                InlineKeyboardButton("✋ Stai", callback_data=f"bj_stand_{owner_id}")
            ]]
            await _safe_edit(
                query,
                f"🃏 <b>BLACKJACK 21</b> (Puntata: {bet} $SDG)\n\nCarte: {game['player_hand']} ({score})\n"
                f"Banco: [{game['dealer_hand'][0]}, ?]",
                InlineKeyboardMarkup(keyboard)
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
            await _safe_edit(
                query,
                f"🏆 <b>VITTORIA!</b> Tu: {player_score} | Banco: {dealer_score} ({dealer_hand})\n"
                f"Hai vinto <b>+💳 {net_profit} $SDG</b>!",
                InlineKeyboardMarkup(end_keyboard)
            )
        elif player_score < dealer_score:
            record_single_result(chat_id, user_id, False, -bet)
            await _safe_edit(
                query,
                f"❌ <b>SCONFITTA!</b> Tu: {player_score} | Banco: {dealer_score} ({dealer_hand})\n"
                f"Hai perso <b>-{bet} $SDG</b>.",
                InlineKeyboardMarkup(end_keyboard)
            )
        else:
            add_user_coins(chat_id, user_id, bet)
            record_single_result(chat_id, user_id, False, 0)
            await _safe_edit(
                query,
                f"⚖️ <b>PAREGGIO!</b> Punti: {player_score}\nPuntata di {bet} $SDG restituita.",
                InlineKeyboardMarkup(end_keyboard)
            )


# =====================================================================
# SLOT MACHINE 777 CON PUNTATE PERSONALIZZATE
# =====================================================================
SLOT_BETS = (10, 25, 50, 100)
SLOT_SYMBOLS = ["🍒", "🍋", "🔔", "💎", "7️⃣"]
SLOT_TRIPLE_MULT = {"🍒": 3, "🍋": 4, "🔔": 5, "💎": 6, "7️⃣": 10}
SLOT_PAIR_MULT = {"💎": 2, "7️⃣": 2}      # le altre doppiette valgono x1 (solo rimborso)
SLOT_REEL_DELAY = 0.6                    # secondi tra l'arresto di un rullo e il successivo
SLOT_BUSY = set()


def slot_payout(reels: list, bet: int):
    """Ritorna (importo totale pagato, tipo). Ritorno atteso circa 90%."""
    if reels[0] == reels[1] == reels[2]:
        return bet * SLOT_TRIPLE_MULT[reels[0]], "triple"
    for s in set(reels):
        if reels.count(s) == 2:
            return bet * SLOT_PAIR_MULT.get(s, 1), "pair"
    return 0, "none"


async def start_slot_from_hub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    parts = query.data.split("_")
    owner_id = int(parts[2]) if len(parts) > 2 else query.from_user.id

    if not await verify_user_lock(query, owner_id): return
    if not game_enabled("slot"):
        await query.answer(MAINT_GAME_TEXT, show_alert=True)
        return
    await query.answer()
    coins = get_user_coins(query.message.chat_id, owner_id)

    text = (
        "🎰 <b>SLOT MACHINE 777 - SCEGLI LA PUNTATA</b>\n\n"
        f"💳 Saldo disponibile: <b>{coins} $SDG</b>\n\n"
        "🏆 <b>Premi (moltiplicatore sulla puntata):</b>\n"
        "7️⃣7️⃣7️⃣ ×10 | 💎💎💎 ×6 | 🔔🔔🔔 ×5 | 🍋🍋🍋 ×4 | 🍒🍒🍒 ×3\n"
        "Doppia 7️⃣ o 💎: ×2 | Altre doppie: rimborso"
    )
    keyboard = [
        [InlineKeyboardButton("10 $SDG", callback_data=f"slot_bet_10_{owner_id}"), InlineKeyboardButton("25 $SDG", callback_data=f"slot_bet_25_{owner_id}")],
        [InlineKeyboardButton("50 $SDG", callback_data=f"slot_bet_50_{owner_id}"), InlineKeyboardButton("100 $SDG", callback_data=f"slot_bet_100_{owner_id}")],
        [InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{owner_id}")]
    ]
    await _safe_edit(query, text, InlineKeyboardMarkup(keyboard))


async def handle_slot_bet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    parts = query.data.split("_")            # slot_bet_25_123
    try:
        bet = int(parts[2])
    except (IndexError, ValueError):
        await query.answer()
        return
    owner_id = int(parts[-1])

    if bet not in SLOT_BETS:
        await query.answer()
        return
    if not await verify_user_lock(query, owner_id): return

    user = query.from_user
    chat_id = query.message.chat_id
    slot_key = f"{chat_id}_{user.id}"

    if not game_enabled("slot"):
        await query.answer(MAINT_GAME_TEXT, show_alert=True)
        return
    if slot_key in SLOT_BUSY:
        await query.answer("⏳ I rulli stanno ancora girando!", show_alert=True)
        return
    if get_user_coins(chat_id, user.id) < bet:
        await query.answer(f"❌ Servono {bet} $SDG per questa puntata!", show_alert=True)
        return

    SLOT_BUSY.add(slot_key)                  # subito, senza await in mezzo (l'handler gira con block=False)
    try:
        await query.answer()
        add_user_coins(chat_id, user.id, -bet)

        reels = [random.choice(SLOT_SYMBOLS) for _ in range(3)]
        payout, kind = slot_payout(reels, bet)
        if payout:
            add_user_coins(chat_id, user.id, payout)          # pagato PRIMA dell'animazione
        record_single_result(chat_id, user.id, payout > bet, payout - bet)

        name = html.escape(user.first_name or "Giocatore")
        head = f"🎰 <b>SLOT MACHINE 777</b> 🎰\n👤 Player: <b>{name}</b> | Puntata: <code>{bet} $SDG</code>\n\n"
        spin = "\n\n<i>Giro rulli in corso...</i>"

        # I tre rulli si fermano uno alla volta: all'inizio girano tutti, poi si fermano in sequenza
        await _safe_edit(query, head + "[ 🔄 | 🔄 | 🔄 ]" + spin)
        await asyncio.sleep(SLOT_REEL_DELAY)
        await _safe_edit(query, head + f"[ {reels[0]} | 🔄 | 🔄 ]" + spin)
        await asyncio.sleep(SLOT_REEL_DELAY)
        await _safe_edit(query, head + f"[ {reels[0]} | {reels[1]} | 🔄 ]" + spin)
        await asyncio.sleep(SLOT_REEL_DELAY)

        text = head + f"[ {reels[0]} | {reels[1]} | {reels[2]} ]\n\n"
        net = payout - bet
        if kind == "triple" and reels[0] == "7️⃣":
            text += f"🔥 <b>JACKPOT SUPREMO 777!</b> 🔥 Hai vinto <b>+💳 {net} $SDG</b>!"
        elif kind == "triple":
            text += f"🎉 <b>TRIPLETTA!</b> Hai vinto <b>+💳 {net} $SDG</b>!"
        elif kind == "pair" and net > 0:
            text += f"✨ <b>DOPPIETTA D'ORO!</b> Ti torna la puntata più un bonus: <b>+💳 {net} $SDG</b>!"
        elif kind == "pair":
            text += "✨ <b>DOPPIETTA!</b> Recuperi la tua puntata."
        else:
            text += f"💸 <b>NESSUNA COMBINAZIONE!</b> Hai perso {bet} $SDG."

        end_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"🔂 Rigioca ({bet} $SDG)", callback_data=f"slot_bet_{bet}_{owner_id}")],
            [InlineKeyboardButton("💰 Cambia puntata", callback_data=f"start_slot_{owner_id}")],
            [InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{owner_id}")]
        ])
        await _safe_edit(query, text, end_keyboard)
    finally:
        SLOT_BUSY.discard(slot_key)


# =====================================================================
# WORDLE EXPRESS E MASTERMIND (Tentativi gestiti nel Text Router in system.py)
# =====================================================================
@serialized("user")
async def start_wordle_from_hub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    parts = query.data.split("_")
    owner_id = int(parts[2]) if len(parts) > 2 else query.from_user.id

    if not await verify_user_lock(query, owner_id): return
    user = query.from_user
    chat_id = query.message.chat_id
    game_key = f"{chat_id}_{user.id}"

    if not game_enabled("wordle"):
        await query.answer(MAINT_GAME_TEXT, show_alert=True)
        return
    if not DICT_LOADED:
        await query.answer("📚 Dizionario Wordle non disponibile: avvisa l'admin!", show_alert=True)
        return
    if recently_started(WORDLE_GAMES, game_key):
        await query.answer("⏳ Hai già una partita a Wordle in corso!", show_alert=True)
        return
    if get_user_coins(chat_id, user.id) < 10:
        await query.answer("❌ Servono 10 $SDG per giocare a Wordle!", show_alert=True)
        return
    await query.answer()

    add_user_coins(chat_id, user.id, -10)
    WORDLE_GAMES[game_key] = {
        "player_id": user.id, "name": user.first_name or "un giocatore",
        "secret": random.choice(WORDLE_WORDS),
        "attempts": 0, "history": [], "ts": time.time(), "last": time.time()
    }

    rewards = ", ".join(f"+{WORDLE_REWARDS[i] - 10}" for i in sorted(WORDLE_REWARDS))
    await _safe_edit(
        query,
        "🔠 <b>WORDLE EXPRESS</b> (Puntata: 10 $SDG)\n\n"
        "Ho scelto una parola italiana di <b>5 lettere</b>!\n"
        "Scrivila direttamente in chat per tentare (5 tentativi).\n"
        f"<i>Vincita netta per tentativo (1°→5°): {rewards} $SDG. Se abbandoni per 5 minuti, ti rimborso.</i>"
    )

@serialized("user")
async def start_mastermind_from_hub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    parts = query.data.split("_")
    owner_id = int(parts[2]) if len(parts) > 2 else query.from_user.id

    if not await verify_user_lock(query, owner_id): return
    user = query.from_user
    chat_id = query.message.chat_id
    game_key = f"{chat_id}_{user.id}"

    if not game_enabled("mastermind"):
        await query.answer(MAINT_GAME_TEXT, show_alert=True)
        return
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
    MASTERMIND_GAMES[game_key] = {
        "player_id": user.id, "name": user.first_name or "un giocatore",
        "secret": "".join(digits[:3]),
        "attempts": 0, "history": [], "ts": time.time(), "last": time.time()
    }

    await _safe_edit(
        query,
        "🔐 <b>MASTERMIND EXPRESS</b> (Puntata: 10 $SDG)\n\n"
        "Ho scelto un codice segreto di <b>3 cifre uniche</b>!\n"
        "Scrivilo direttamente in chat per tentare (5 tentativi).\n"
        f"<i>Premio: {MASTERMIND_REWARDS[1]} $SDG se lo trovi entro il 3° tentativo, "
        f"{MASTERMIND_REWARDS[5]} al 4° o 5°. Se abbandoni per 5 minuti, ti rimborso.</i>"
    )


# =====================================================================
# RIMBORSO DELLE PARTITE ABBANDONATE (job periodico)
# =====================================================================
async def expire_abandoned_games(context: ContextTypes.DEFAULT_TYPE):
    now = time.time()
    for store, label, default_refund in (
        (WORDLE_GAMES, "Wordle", 10),
        (MASTERMIND_GAMES, "Mastermind", 10),
        (BLACKJACK_GAMES, "Blackjack", 10),
    ):
        for key, g in list(store.items()):
            if now - g.get("last", g.get("ts", now)) <= GAME_IDLE_SECS:
                continue
            store.pop(key, None)
            chat_id, user_id = key.rsplit("_", 1)
            refund = g.get("bet", default_refund)
            add_user_coins(int(chat_id), int(user_id), refund)
            try:
                await context.bot.send_message(
                    chat_id=int(chat_id), parse_mode="HTML",
                    text=(f"⏰ Partita a {label} di <b>{html.escape(str(g.get('name', 'un giocatore')))}</b> "
                          f"scaduta per inattività: rimborsati {refund} $SDG.")
                )
            except Exception:
                pass


# =====================================================================
# SDROGO HEIST (bilanciato: ritorno atteso ~95% con costo, premi e probabilità sotto)
# =====================================================================
# Probabilità di successo: L1 = 2/3, L2 = 62,5%, L3 = 2/3, L4 = 2/3, L5 = 50%.
# I cashout sono calibrati sul valore atteso: qualunque strategia (fermarsi prima o
# arrivare in fondo) rende circa uguale, quindi non esiste un modo di "farmare" il gioco.
# Il costo del pass deve coincidere con quello scalato in hub.py (shop_buy_callback).
# L'acquisto del pass (e quindi l'avvio) è controllato in hub.py con GAME_STATUS["heist"]:
# le rapine già iniziate possono sempre essere concluse.
HEIST_COST = 150
HEIST_PRIZES = {2: 175, 3: 230, 4: 300, 5: 500}   # cashout disponibile dopo aver superato il livello (chiave = livello successivo)
HEIST_FINAL = 1000
HEIST_NOTE = "\n\n<i>I premi sono nel saldo del gruppo: riapri /sdrogocomm lì per vederli.</i>"

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


@serialized("user")
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
    game["ts"] = time.time()
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
            save_db()

            await _safe_edit(
                query,
                "🏆 <b>RAPINA PERFETTA COMPLETATA!</b>\n"
                f"Hai vinto +{HEIST_FINAL} $SDG, 1 Titolo e 1 Persecuzione Gratis + 1 STELLA ⭐!" + HEIST_NOTE
            )

            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"👑 <b>COLPO DEL SECOLO!</b> 🏢\n\n"
                        f"<b>{html.escape(query.from_user.first_name or 'Un giocatore')}</b> ha svaligiato il Caveau di Sdrogo Heist "
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
            f"💰 <b>CASHOUT EFFETTUATO!</b> Ti ritiri dalla rapina incassando <b>+💳 {amount} $SDG</b>!" + HEIST_NOTE
        )


# =====================================================================
# LABORATORIO SEGRETO: giochi sperimentali (SOLO admin, SOLO chat privata)
# =====================================================================
LAB_BETS = (10, 25, 50, 100)


def _lab_back_row(owner_id: int):
    return [InlineKeyboardButton("🧪 Torna al Laboratorio", callback_data=f"hub_lab_{owner_id}")]


async def _lab_guard(query, owner_id: int) -> bool:
    """Doppio controllo di sicurezza: admin + chat privata + menu del proprietario."""
    if not is_admin(query.from_user.id) or query.message.chat.type != "private":
        await query.answer("🔒 Area riservata all'admin in chat privata.", show_alert=True)
        return False
    return await verify_user_lock(query, owner_id)


def _lab_bet_keyboard(prefix: str, owner_id: int):
    return [
        [InlineKeyboardButton("10 $SDG", callback_data=f"{prefix}_10_{owner_id}"), InlineKeyboardButton("25 $SDG", callback_data=f"{prefix}_25_{owner_id}")],
        [InlineKeyboardButton("50 $SDG", callback_data=f"{prefix}_50_{owner_id}"), InlineKeyboardButton("100 $SDG", callback_data=f"{prefix}_100_{owner_id}")],
        _lab_back_row(owner_id),
    ]


# ---------------------------------------------------------------------
# 1) SDROGO ROULETTE (single player)
# ---------------------------------------------------------------------
RED_NUMBERS = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}
ROUL_CHOICES = {
    "red": "🔴 Rosso", "black": "⚫ Nero",
    "even": "🔢 Pari", "odd": "🔢 Dispari",
    "green": "🟢 Verde (0)",
}


def roulette_color(n: int) -> str:
    if n == 0:
        return "green"
    return "red" if n in RED_NUMBERS else "black"


def roulette_color_emoji(n: int) -> str:
    return {"green": "🟢", "red": "🔴", "black": "⚫"}[roulette_color(n)]


def roulette_multiplier(choice: str, n: int) -> int:
    """Moltiplicatore TOTALE sulla puntata: 2 (1:1), 36 (35:1 sul verde), 0 se perde. Lo zero fa perdere tutto tranne il verde."""
    if n == 0:
        return 36 if choice == "green" else 0
    if choice == "red" and roulette_color(n) == "red":
        return 2
    if choice == "black" and roulette_color(n) == "black":
        return 2
    if choice == "even" and n % 2 == 0:
        return 2
    if choice == "odd" and n % 2 == 1:
        return 2
    return 0


def _wheel_strip() -> str:
    return " ".join(random.choice(["🔴", "⚫", "🔴", "⚫", "🟢"]) for _ in range(7))


async def handle_lab_roulette(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    parts = query.data.split("_")        # lab_roul_OWNER | lab_roulbet_BET_OWNER | lab_roulpick_BET_CHOICE_OWNER
    action = parts[1]
    try:
        owner_id = int(parts[-1])
    except ValueError:
        await query.answer()
        return

    if not await _lab_guard(query, owner_id):
        return

    user = query.from_user
    chat_id = query.message.chat_id
    coins = get_user_coins(chat_id, user.id)

    # Fase 1: scelta della puntata
    if action == "roul":
        await query.answer()
        text = (
            "🎡 <b>SDROGO ROULETTE</b> 🧪\n\n"
            f"💳 Saldo disponibile: <b>{coins} $SDG</b>\n\n"
            "Rosso / Nero / Pari / Dispari pagano 1:1, il Verde (0) paga 35:1.\n"
            "<i>Lo zero fa perdere tutto tranne chi ha puntato sul Verde.</i>\n\n"
            "<b>Fase 1:</b> scegli la puntata."
        )
        await _safe_edit(query, text, InlineKeyboardMarkup(_lab_bet_keyboard("lab_roulbet", owner_id)))
        return

    # Fase 2: su cosa puntare
    if action == "roulbet":
        try:
            bet = int(parts[2])
        except (IndexError, ValueError):
            await query.answer()
            return
        if bet not in LAB_BETS:
            await query.answer()
            return
        if coins < bet:
            await query.answer(f"❌ Servono {bet} $SDG per questa puntata!", show_alert=True)
            return
        await query.answer()
        text = (
            "🎡 <b>SDROGO ROULETTE</b> 🧪\n\n"
            f"💰 Puntata: <code>{bet} $SDG</code>\n\n"
            "<b>Fase 2:</b> su cosa punti?"
        )
        keyboard = [
            [InlineKeyboardButton("🔴 Rosso", callback_data=f"lab_roulpick_{bet}_red_{owner_id}"), InlineKeyboardButton("⚫ Nero", callback_data=f"lab_roulpick_{bet}_black_{owner_id}")],
            [InlineKeyboardButton("🔢 Pari", callback_data=f"lab_roulpick_{bet}_even_{owner_id}"), InlineKeyboardButton("🔢 Dispari", callback_data=f"lab_roulpick_{bet}_odd_{owner_id}")],
            [InlineKeyboardButton("🟢 Verde (0) · 35:1", callback_data=f"lab_roulpick_{bet}_green_{owner_id}")],
            [InlineKeyboardButton("💰 Cambia puntata", callback_data=f"lab_roul_{owner_id}")],
            _lab_back_row(owner_id),
        ]
        await _safe_edit(query, text, InlineKeyboardMarkup(keyboard))
        return

    # Fase 3: giocata con animazione
    if action == "roulpick":
        try:
            bet = int(parts[2])
            choice = parts[3]
        except (IndexError, ValueError):
            await query.answer()
            return
        if bet not in LAB_BETS or choice not in ROUL_CHOICES:
            await query.answer()
            return
        key = f"{chat_id}_{user.id}"
        if key in LAB_BUSY:
            await query.answer("⏳ La ruota sta ancora girando!", show_alert=True)
            return
        if coins < bet:
            await query.answer(f"❌ Servono {bet} $SDG per questa puntata!", show_alert=True)
            return

        LAB_BUSY.add(key)                       # subito, senza await in mezzo
        try:
            await query.answer()
            add_user_coins(chat_id, user.id, -bet)

            n = random.randint(0, 36)
            mult = roulette_multiplier(choice, n)
            payout = bet * mult
            if payout:
                add_user_coins(chat_id, user.id, payout)       # pagato PRIMA dell'animazione
            record_single_result(chat_id, user.id, payout > bet, payout - bet)

            head = (
                "🎡 <b>SDROGO ROULETTE</b> 🧪\n"
                f"💰 Puntata: <code>{bet} $SDG</code> su <b>{ROUL_CHOICES[choice]}</b>\n\n"
            )
            frames = [
                "🎡 <i>La ruota gira...</i>",
                "🌀 <i>La pallina rimbalza...</i>",
                "⏳ <i>Sta rallentando...</i>",
            ]
            for frame in frames:
                await _safe_edit(query, head + f"{frame}\n\n[ {_wheel_strip()} ]")
                await asyncio.sleep(1.5)

            net = payout - bet
            text = head + f"⚪ La pallina si ferma sul <b>{n}</b> {roulette_color_emoji(n)}\n\n"
            if mult == 36:
                text += f"🔥 <b>VERDE! PAGA 35:1!</b> Hai vinto <b>+💳 {net} $SDG</b>!"
            elif mult == 2:
                text += f"🎉 <b>VINTO!</b> Hai vinto <b>+💳 {net} $SDG</b>!"
            else:
                text += f"💸 <b>PERSO!</b> Hai perso {bet} $SDG."

            end_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton(f"🔂 Rigioca ({bet} su {ROUL_CHOICES[choice]})", callback_data=f"lab_roulpick_{bet}_{choice}_{owner_id}")],
                [InlineKeyboardButton("💰 Cambia puntata", callback_data=f"lab_roul_{owner_id}")],
                _lab_back_row(owner_id),
            ])
            await _safe_edit(query, text, end_keyboard)
        finally:
            LAB_BUSY.discard(key)
        return

    await query.answer()


# ---------------------------------------------------------------------
# 2) SDROGO AVIATOR (single player)
# ---------------------------------------------------------------------
AVIATOR_TICK = 2.0          # secondi tra un aggiornamento del moltiplicatore e il successivo
AVIATOR_MAX_CRASH = 100.0


def aviator_crash_point() -> float:
    """Distribuzione con P(crash >= m) = 0,9 / m: 10% di crash immediato a 1.00x, 40% sotto 1.5x,
    ma con una coda lunga fino a 100x. Il ritorno atteso è 90% qualunque sia il momento dell'incasso."""
    r = random.random()
    c = 0.90 / (1.0 - r)
    if c < 1.0:
        return 1.0
    return min(round(c, 2), AVIATOR_MAX_CRASH)


def _avi_text(game: dict, mult: float, status: str) -> str:
    return (
        "✈️ <b>SDROGO AVIATOR</b> 🧪\n\n"
        f"💰 Puntata: <code>{game['bet']} $SDG</code>\n"
        f"🚀 Moltiplicatore: <b>{mult:.2f}x</b>\n"
        f"💵 Incasso attuale: <b>{int(game['bet'] * mult)} $SDG</b>\n\n"
        f"{status}"
    )


def _avi_end_keyboard(bet: int, owner_id: int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🔂 Rivola ({bet} $SDG)", callback_data=f"lab_avibet_{bet}_{owner_id}")],
        [InlineKeyboardButton("💰 Cambia puntata", callback_data=f"lab_avi_{owner_id}")],
        _lab_back_row(owner_id),
    ])


async def _aviator_flight(query, game: dict, key: str, owner_id: int, chat_id: int, user_id: int):
    bet, crash = game["bet"], game["crash"]
    cash_kb = InlineKeyboardMarkup([[InlineKeyboardButton("💰 CASHOUT", callback_data=f"lab_avicash_{owner_id}")]])

    async def finish_crash():
        game["finished"] = True
        if AVIATOR_GAMES.get(key) is game:
            del AVIATOR_GAMES[key]
        record_single_result(chat_id, user_id, False, -bet)
        await _safe_edit(
            query,
            f"💥 <b>L'aereo è volato via (Crash a {crash:.2f}x)!</b>\n\n"
            f"💰 Puntata: <code>{bet} $SDG</code>\n💸 Hai perso {bet} $SDG.",
            _avi_end_keyboard(bet, owner_id)
        )

    async with game["lock"]:
        await _safe_edit(query, _avi_text(game, 1.0, "🛫 <i>Decollo in corso...</i>"), cash_kb)

    if crash <= 1.0:
        await asyncio.sleep(1.5)
        async with game["lock"]:
            if not game["finished"]:
                await finish_crash()
        return

    mult = 1.0
    while True:
        await asyncio.sleep(AVIATOR_TICK)
        async with game["lock"]:
            if game["finished"]:
                return
            nxt = round(mult + max(0.1, mult * random.uniform(0.08, 0.25)), 2)
            if nxt >= crash:
                await finish_crash()
                return
            await _safe_edit(query, _avi_text(game, nxt, "🚀 <i>Sale... premi CASHOUT prima che voli via!</i>"), cash_kb)
            game["current"] = nxt          # aggiornato solo dopo la modifica: si incassa solo ciò che si è visto
            mult = nxt
            if game["finished"]:
                return


async def handle_lab_aviator(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    parts = query.data.split("_")        # lab_avi_OWNER | lab_avibet_BET_OWNER | lab_avicash_OWNER
    action = parts[1]
    try:
        owner_id = int(parts[-1])
    except ValueError:
        await query.answer()
        return

    if not await _lab_guard(query, owner_id):
        return

    user = query.from_user
    chat_id = query.message.chat_id
    key = f"{chat_id}_{user.id}"

    # Scelta puntata
    if action == "avi":
        await query.answer()
        coins = get_user_coins(chat_id, user.id)
        text = (
            "✈️ <b>SDROGO AVIATOR</b> 🧪\n\n"
            f"💳 Saldo disponibile: <b>{coins} $SDG</b>\n\n"
            "Il moltiplicatore sale finché l'aereo non vola via. Premi <b>CASHOUT</b> prima del crash "
            "per incassare puntata × moltiplicatore. Se l'aereo vola via prima, perdi la puntata.\n\n"
            "Scegli la puntata:"
        )
        await _safe_edit(query, text, InlineKeyboardMarkup(_lab_bet_keyboard("lab_avibet", owner_id)))
        return

    # CASHOUT
    if action == "avicash":
        game = AVIATOR_GAMES.get(key)
        if not game or game["finished"]:
            await query.answer("✈️ Il volo è già terminato!", show_alert=True)
            return
        # tutto sincrono, senza await: il ciclo di volo vede subito finished=True
        game["finished"] = True
        cur = game["current"]
        bet = game["bet"]
        payout = int(bet * cur)
        if AVIATOR_GAMES.get(key) is game:
            del AVIATOR_GAMES[key]
        if payout:
            add_user_coins(chat_id, user.id, payout)
        record_single_result(chat_id, user.id, payout > bet, payout - bet)
        await query.answer(f"💰 Incassato a {cur:.2f}x!")
        async with game["lock"]:
            net = payout - bet
            sign = f"+{net}" if net >= 0 else str(net)
            await _safe_edit(
                query,
                "✈️ <b>SDROGO AVIATOR</b> 🧪\n\n"
                f"💰 <b>CASHOUT a {cur:.2f}x!</b>\n"
                f"Puntata: <code>{bet} $SDG</code> → Incasso: <b>{payout} $SDG</b> (<b>{sign}</b>)\n\n"
                f"<i>L'aereo sarebbe volato via a {game['crash']:.2f}x.</i>",
                _avi_end_keyboard(bet, owner_id)
            )
        return

    # Avvio volo
    if action == "avibet":
        try:
            bet = int(parts[2])
        except (IndexError, ValueError):
            await query.answer()
            return
        if bet not in LAB_BETS:
            await query.answer()
            return
        existing = AVIATOR_GAMES.get(key)
        if existing and not existing["finished"] and time.time() - existing["ts"] < 300:
            await query.answer("✈️ Hai già un volo in corso!", show_alert=True)
            return
        if get_user_coins(chat_id, user.id) < bet:
            await query.answer(f"❌ Servono {bet} $SDG per questa puntata!", show_alert=True)
            return

        # nessun await tra i controlli e queste righe: due tap simultanei non possono passare entrambi
        add_user_coins(chat_id, user.id, -bet)
        game = {
            "bet": bet, "crash": aviator_crash_point(), "current": 1.0,
            "finished": False, "ts": time.time(), "lock": asyncio.Lock(),
        }
        AVIATOR_GAMES[key] = game
        await query.answer()
        try:
            await _aviator_flight(query, game, key, owner_id, chat_id, user.id)
        except Exception as e:
            logging.error(f"Errore Aviator: {e}")
        finally:
            if AVIATOR_GAMES.get(key) is game and game["finished"]:
                del AVIATOR_GAMES[key]
        return

    await query.answer()


# ---------------------------------------------------------------------
# 3) GRATTA E VINCI (single player)
# ---------------------------------------------------------------------
SCRATCH_COST = 20
SCRATCH_PICKS = 3
# (simbolo, premio in $SDG, probabilità per casella): ritorno atteso ≈ 91% del costo con 3 caselle
SCRATCH_TABLE = [("💀", 0, 0.62), ("💰", 10, 0.28), ("💎", 25, 0.09), ("👑", 100, 0.01)]
SCRATCH_PRIZE = {sym: prize for sym, prize, _ in SCRATCH_TABLE}


def scratch_grid() -> list:
    syms = [s for s, _, _ in SCRATCH_TABLE]
    weights = [w for _, _, w in SCRATCH_TABLE]
    return random.choices(syms, weights=weights, k=9)


def _scratch_keyboard(game: dict, owner_id: int, finished: bool = False):
    rows = []
    for r in range(3):
        row = []
        for c in range(3):
            i = r * 3 + c
            sym = game["grid"][i]
            if i in game["picked"]:
                row.append(InlineKeyboardButton(f"✅{sym}", callback_data=f"lab_scrnoop_{owner_id}"))
            elif finished:
                row.append(InlineKeyboardButton(sym, callback_data=f"lab_scrnoop_{owner_id}"))
            else:
                row.append(InlineKeyboardButton("🎁", callback_data=f"lab_scrcell_{i}_{owner_id}"))
        rows.append(row)
    if finished:
        rows.append([InlineKeyboardButton(f"🎟️ Nuovo biglietto ({SCRATCH_COST} $SDG)", callback_data=f"lab_scr_{owner_id}")])
        rows.append(_lab_back_row(owner_id))
    return rows


def _scratch_text(game: dict) -> str:
    left = SCRATCH_PICKS - len(game["picked"])
    found = " ".join(game["grid"][i] for i in game["picked"]) or "—"
    return (
        "🎟️ <b>GRATTA E VINCI</b> 🧪\n\n"
        f"Grattate rimaste: <b>{left}</b>\n"
        f"Trovato finora: {found}\n\n"
        "💀 vuoto • 💰 10 • 💎 25 • 👑 100 $SDG"
    )


@serialized("user")
async def handle_lab_scratch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    parts = query.data.split("_")        # lab_scr_OWNER | lab_scrcell_IDX_OWNER | lab_scrnoop_OWNER
    action = parts[1]
    try:
        owner_id = int(parts[-1])
    except ValueError:
        await query.answer()
        return

    if not await _lab_guard(query, owner_id):
        return

    user = query.from_user
    chat_id = query.message.chat_id
    key = f"{chat_id}_{user.id}"

    if action == "scrnoop":
        await query.answer()
        return

    # Acquisto biglietto
    if action == "scr":
        game = SCRATCH_GAMES.get(key)
        if game and time.time() - game["ts"] < 600:
            await query.answer("🎟️ Hai già un biglietto da finire!")
            await _safe_edit(query, _scratch_text(game), InlineKeyboardMarkup(_scratch_keyboard(game, owner_id)))
            return
        if get_user_coins(chat_id, user.id) < SCRATCH_COST:
            await query.answer(f"❌ Servono {SCRATCH_COST} $SDG per un biglietto!", show_alert=True)
            return
        await query.answer()
        add_user_coins(chat_id, user.id, -SCRATCH_COST)
        game = {"grid": scratch_grid(), "picked": [], "ts": time.time()}
        SCRATCH_GAMES[key] = game
        await _safe_edit(query, _scratch_text(game), InlineKeyboardMarkup(_scratch_keyboard(game, owner_id)))
        return

    # Grattata di una casella
    if action == "scrcell":
        game = SCRATCH_GAMES.get(key)
        try:
            idx = int(parts[2])
        except (IndexError, ValueError):
            await query.answer()
            return
        if not game:
            await query.answer("🎟️ Nessun biglietto attivo: compra un nuovo biglietto.", show_alert=True)
            return
        if idx < 0 or idx > 8 or idx in game["picked"]:
            await query.answer()
            return
        await query.answer()
        game["picked"].append(idx)
        game["ts"] = time.time()

        if len(game["picked"]) < SCRATCH_PICKS:
            await _safe_edit(query, _scratch_text(game), InlineKeyboardMarkup(_scratch_keyboard(game, owner_id)))
            return

        # biglietto finito: somma dei premi, accredito e tastiera disabilitata
        payout = sum(SCRATCH_PRIZE[game["grid"][i]] for i in game["picked"])
        del SCRATCH_GAMES[key]
        if payout:
            add_user_coins(chat_id, user.id, payout)
        record_single_result(chat_id, user.id, payout > SCRATCH_COST, payout - SCRATCH_COST)
        found = " ".join(game["grid"][i] for i in game["picked"])
        net = payout - SCRATCH_COST
        sign = f"+{net}" if net >= 0 else str(net)
        if payout == 0:
            verdict = "💸 Nessun premio, ritenta!"
        else:
            verdict = f"🎉 Hai vinto <b>{payout} $SDG</b> (<b>{sign}</b> netti)!"
        text = (
            "🎟️ <b>GRATTA E VINCI</b> 🧪\n\n"
            f"Hai grattato: {found}\n\n{verdict}\n\n"
            "<i>Le caselle senza ✅ sono quelle che non hai scoperto.</i>"
        )
        await _safe_edit(query, text, InlineKeyboardMarkup(_scratch_keyboard(game, owner_id, finished=True)))
        return

    await query.answer()


# =====================================================================
# REGISTRAZIONE
# =====================================================================
def register(app):
    app.add_handler(CallbackQueryHandler(start_bj_from_hub, pattern="^start_bj_"))
    app.add_handler(CallbackQueryHandler(handle_bj_callback, pattern="^bj_"))
    app.add_handler(CallbackQueryHandler(start_slot_from_hub, pattern="^start_slot_"))
    # block=False: la pausa dell'animazione non ferma gli altri giocatori
    app.add_handler(CallbackQueryHandler(handle_slot_bet, pattern="^slot_bet_", block=False))
    app.add_handler(CallbackQueryHandler(start_wordle_from_hub, pattern="^start_wordle_"))
    app.add_handler(CallbackQueryHandler(start_mastermind_from_hub, pattern="^start_mm_"))
    app.add_handler(CallbackQueryHandler(handle_heist_callback, pattern="^heist_"))
    # Laboratorio Segreto (ogni handler ricontrolla admin + chat privata)
    app.add_handler(CallbackQueryHandler(handle_lab_roulette, pattern="^lab_roul", block=False))
    app.add_handler(CallbackQueryHandler(handle_lab_aviator, pattern="^lab_avi", block=False))
    app.add_handler(CallbackQueryHandler(handle_lab_scratch, pattern="^lab_scr"))
    if app.job_queue:
        app.job_queue.run_repeating(expire_abandoned_games, interval=60, first=60)
