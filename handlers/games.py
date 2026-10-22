"""Minigiochi single player V2: Blackjack, Slot, Wordle, Mastermind, Heist (V1 e V2), Aviator, Roulette,
Gratta e Vinci, Sdrogo Poker. Economia V2 (Banco, Follia, boost) in economy.py, attiva solo per chi vede la V2."""
import asyncio
import html
import itertools
import logging
import math
import os
import random
import time
import unicodedata

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler, ContextTypes

from economy import (
    game_header, fmt, charge as eco_charge, settle_single, inv, has_luck, quest_progress,
    banco_balance, banco_take, banco_check, follia_active, record_single_result, _puzzle_wires,
)
from state import (
    BLACKJACK_GAMES, WORDLE_GAMES, MASTERMIND_GAMES, HEIST_GAMES, USER_INVENTORIES,
    AVIATOR_GAMES, SCRATCH_GAMES, LAB_BUSY, POKER_GAMES,
    game_enabled, MAINT_GAME_TEXT,
)
from storage import get_user_coins, add_user_coins, save_db, ukey
from utils import verify_user_lock, serialized, is_v2, smart_edit, anim_gap, frame_ok

# --- PREMI WORDLE / MASTERMIND (costo di ingresso: 10 $SDG) ---
# Totale restituito in base ai tentativi usati (netti: +50, +40, +30, +20, +10)
WORDLE_REWARDS = {1: 60, 2: 50, 3: 40, 4: 30, 5: 20}
MASTERMIND_REWARDS = {1: 80, 2: 80, 3: 80, 4: 50, 5: 35}  # 80 entro il 3° tentativo, 50 al 4°, 35 al 5°
GAME_IDLE_SECS = 300   # Wordle / Mastermind / Blackjack / Poker inattivi da 5 minuti vengono chiusi con rimborso


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


# =====================================================================
# HELPER COMUNI
# =====================================================================
# Tutte le modifiche di messaggio passano da smart_edit: niente chiamate se nulla cambia, retry sui flood control.
_safe_edit = smart_edit


def _charge(chat_id: int, user_id: int, amount: int):
    """Scala una puntata. Per chi vede la V2 conta anche nella sfida di gruppo."""
    if is_v2(user_id):
        eco_charge(chat_id, user_id, amount)
    else:
        add_user_coins(chat_id, user_id, -amount)


async def _settle(bot, chat_id: int, user_id: int, game: str, bet: int, base_payout: int, is_casino: bool = True):
    """Liquida una partita single player. V2: boost, Follia, Sanguisuga e Banco. V1: pagamento semplice.
    Restituisce (importo accreditato, note da mostrare)."""
    if is_v2(user_id):
        return await settle_single(bot, chat_id, user_id, game, bet, base_payout, is_casino=is_casino)
    if base_payout > 0:
        add_user_coins(chat_id, user_id, base_payout)
    record_single_result(chat_id, user_id, base_payout > bet, base_payout - bet, is_casino=is_casino, game=game, bet=bet)
    return base_payout, []


def _notes(notes: list) -> str:
    return ("\n" + "\n".join(notes)) if notes else ""


def _sign(n: int) -> str:
    return f"+{fmt(n)}" if n >= 0 else f"-{fmt(-n)}"


def recently_started(store: dict, key: str, secs: int = 60) -> bool:
    g = store.get(key)
    return bool(g) and time.time() - g.get("ts", 0) < secs


def hand_value(hand: list) -> int:
    total, aces = sum(hand), hand.count(11)
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total


async def _game_on(query, name: str) -> bool:
    """True se il gioco è acceso; altrimenti mostra il popup di manutenzione."""
    if game_enabled(name):
        return True
    await query.answer(MAINT_GAME_TEXT, show_alert=True)
    return False


# =====================================================================
# BLACKJACK
# =====================================================================
# Il banco pesca meno carte da 10 (3 su 12 invece di 4 su 13). Compensazione per il giocatore: "Charlie a 5 carte"
# (5 carte senza sballare = vittoria 1:1). Con Boost Fortuna il banco pesca da un mazzo con ancora meno dieci.
BJ_PLAYER_CARDS = [2, 3, 4, 5, 6, 7, 8, 9, 10, 10, 10, 10, 11]
BJ_DEALER_CARDS = [2, 3, 4, 5, 6, 7, 8, 9, 10, 10, 10, 11]
BJ_DEALER_CARDS_LUCK = [2, 3, 4, 5, 6, 7, 8, 9, 10, 10, 11]
BJ_CHARLIE_CARDS = 5
BJ_BETS = (10, 25, 50, 100)


def _bj_dealer_deck(game: dict) -> list:
    return BJ_DEALER_CARDS_LUCK if game.get("luck") else BJ_DEALER_CARDS


async def start_bj_from_hub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    parts = query.data.split("_")
    owner_id = int(parts[2]) if len(parts) > 2 else query.from_user.id

    if not await verify_user_lock(query, owner_id): return
    if not await _game_on(query, "blackjack"): return
    await query.answer()
    chat_id = query.message.chat_id
    coins = get_user_coins(chat_id, owner_id)

    text = (
        game_header("🃏 <b>BLACKJACK 21</b> 🃏", query.from_user.first_name, coins)
        + "🖐️ <b>Charlie:</b> 5 carte senza sballare = vittoria automatica!\n\n"
        "<i>Seleziona quanto vuoi puntare per questa mano:</i>"
    )
    keyboard = [
        [InlineKeyboardButton("10 $SDG", callback_data=f"bj_bet_10_{owner_id}"), InlineKeyboardButton("25 $SDG", callback_data=f"bj_bet_25_{owner_id}")],
        [InlineKeyboardButton("50 $SDG", callback_data=f"bj_bet_50_{owner_id}"), InlineKeyboardButton("100 $SDG", callback_data=f"bj_bet_100_{owner_id}")],
        [InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{owner_id}")]
    ]
    await _safe_edit(query, text, InlineKeyboardMarkup(keyboard))


def _bj_playing_text(game: dict, name: str, coins: int) -> str:
    return (
        game_header("🃏 <b>BLACKJACK 21</b> 🃏", name, coins, game["bet"])
        + f"🎎 Carte: {game['player_hand']} (Totale: <b>{hand_value(game['player_hand'])}</b>)\n"
        f"🤖 Banco: [{game['dealer_hand'][0]}, ?]\n\nCosa fai?"
    )


def _bj_action_kb(owner_id: int):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🎴 Carta", callback_data=f"bj_hit_{owner_id}"),
        InlineKeyboardButton("✋ Stai", callback_data=f"bj_stand_{owner_id}")
    ]])


async def _bj_finish(query, context, game_key: str, game: dict, user, chat_id: int, verdict: str, base_payout: int, owner_id: int):
    BLACKJACK_GAMES.pop(game_key, None)
    bet = game["bet"]
    payout, notes = await _settle(context.bot, chat_id, user.id, "blackjack", bet, base_payout)
    qmsg = quest_progress(chat_id, user.id, "bj_win") if payout > bet else ""
    net = payout - bet
    d = game["dealer_hand"]
    text = (
        game_header("🃏 <b>BLACKJACK 21</b> 🃏", user.first_name, get_user_coins(chat_id, user.id), bet)
        + f"🎎 Tu: {game['player_hand']} (<b>{hand_value(game['player_hand'])}</b>)\n"
        f"🤖 Banco: {d} (<b>{hand_value(d)}</b>)\n\n"
        f"{verdict} <b>{_sign(net)} $SDG</b>"
        + _notes(notes) + (f"\n{qmsg}" if qmsg else "")
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔂 Rigioca", callback_data=f"start_bj_{owner_id}")],
        [InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{owner_id}")]
    ])
    await _safe_edit(query, text, keyboard)


@serialized("user")
async def handle_bj_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    parts = query.data.split("_")
    action = parts[1]
    owner_id = int(parts[-1])

    if not await verify_user_lock(query, owner_id): return
    chat_id = query.message.chat_id
    user = query.from_user
    user_id = user.id
    game_key = f"{chat_id}_{user_id}"
    name = user.first_name or "Giocatore"

    if action == "bet":
        bet = int(parts[2])
        if bet not in BJ_BETS:
            await query.answer()
            return
        if not await _game_on(query, "blackjack"): return
        if recently_started(BLACKJACK_GAMES, game_key, 120):
            await query.answer("⏳ Hai già una mano in corso!", show_alert=True)
            return
        if get_user_coins(chat_id, owner_id) < bet:
            await query.answer("❌ Non hai abbastanza $SDG per questa puntata!", show_alert=True)
            return
        await query.answer()

        _charge(chat_id, owner_id, bet)
        game = {
            "player_id": user_id, "name": name, "bet": bet, "ts": time.time(), "last": time.time(),
            "luck": has_luck(chat_id, owner_id, "blackjack"),
            "player_hand": [random.choice(BJ_PLAYER_CARDS), random.choice(BJ_PLAYER_CARDS)],
        }
        game["dealer_hand"] = [random.choice(_bj_dealer_deck(game))]
        BLACKJACK_GAMES[game_key] = game

        if hand_value(game["player_hand"]) == 21:     # Blackjack naturale: paga 3:2
            await _bj_finish(query, context, game_key, game, user, chat_id,
                             "✨ <b>BLACKJACK NATURALE!</b>", int(bet * 2.5), owner_id)
            return

        await _safe_edit(query, _bj_playing_text(game, name, get_user_coins(chat_id, user_id)), _bj_action_kb(owner_id))
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

    if action == "hit":
        game["player_hand"].append(random.choice(BJ_PLAYER_CARDS))
        score = hand_value(game["player_hand"])

        if score > 21:
            await _bj_finish(query, context, game_key, game, user, chat_id, "💥 <b>SBALLATO!</b>", 0, owner_id)
        elif len(game["player_hand"]) >= BJ_CHARLIE_CARDS:
            await _bj_finish(query, context, game_key, game, user, chat_id,
                             "🖐️ <b>CHARLIE! 5 carte senza sballare.</b>", bet * 2, owner_id)
        else:
            await _safe_edit(query, _bj_playing_text(game, name, get_user_coins(chat_id, user_id)), _bj_action_kb(owner_id))

    elif action == "stand":
        player_score = hand_value(game["player_hand"])
        dealer_hand = game["dealer_hand"]
        deck = _bj_dealer_deck(game)
        while hand_value(dealer_hand) < 17:
            dealer_hand.append(random.choice(deck))
        dealer_score = hand_value(dealer_hand)

        if dealer_score > 21 or player_score > dealer_score:
            await _bj_finish(query, context, game_key, game, user, chat_id, "🏆 <b>VITTORIA!</b>", bet * 2, owner_id)
        elif player_score < dealer_score:
            await _bj_finish(query, context, game_key, game, user, chat_id, "❌ <b>SCONFITTA!</b>", 0, owner_id)
        else:
            await _bj_finish(query, context, game_key, game, user, chat_id,
                             "⚖️ <b>PAREGGIO!</b> Puntata restituita.", bet, owner_id)


# =====================================================================
# SLOT MACHINE 777
# =====================================================================
SLOT_BETS = (10, 25, 50, 100)
SLOT_SYMBOLS = ["🍒", "🍋", "🔔", "💎", "7️⃣"]
SLOT_TRIPLE_MULT = {"🍒": 3, "🍋": 4, "🔔": 5, "💎": 6, "7️⃣": 10}
SLOT_PAIR_MULT = {"💎": 2, "7️⃣": 2}      # le altre doppiette valgono x1 (solo rimborso)
SLOT_REEL_DELAY = 0.8                    # (non più usato: i ritmi sono in utils.ANIM_GAPS)
LUCK_REROLL_CHANCE = 0.25                # Boost Fortuna: probabilità di rilanciare una giocata persa
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
    if not await _game_on(query, "slot"): return
    await query.answer()
    coins = get_user_coins(query.message.chat_id, owner_id)

    text = (
        game_header("🎰 <b>SLOT MACHINE 777</b> 🎰", query.from_user.first_name, coins)
        + "🏆 <b>Premi (moltiplicatore sulla puntata):</b>\n"
        "7️⃣7️⃣7️⃣ ×10 | 💎💎💎 ×6 | 🔔🔔🔔 ×5 | 🍋🍋🍋 ×4 | 🍒🍒🍒 ×3\n"
        "Doppia 7️⃣ o 💎: ×2 | Altre doppie: rimborso\n\n"
        "<i>Scegli la puntata:</i>"
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
    name = user.first_name or "Giocatore"

    if not await _game_on(query, "slot"): return
    if slot_key in SLOT_BUSY:
        await query.answer("⏳ I rulli stanno ancora girando!", show_alert=True)
        return
    if get_user_coins(chat_id, user.id) < bet:
        await query.answer(f"❌ Servono {bet} $SDG per questa puntata!", show_alert=True)
        return

    SLOT_BUSY.add(slot_key)                  # subito, senza await in mezzo (l'handler gira con block=False)
    try:
        await query.answer()
        luck = has_luck(chat_id, user.id, "slot")
        _charge(chat_id, user.id, bet)
        bal_spin = get_user_coins(chat_id, user.id)           # saldo mostrato durante l'animazione (senza spoiler)

        reels = [random.choice(SLOT_SYMBOLS) for _ in range(3)]
        payout, kind = slot_payout(reels, bet)
        luck_used = False
        if kind == "none" and luck and random.random() < LUCK_REROLL_CHANCE:
            reels = [random.choice(SLOT_SYMBOLS) for _ in range(3)]
            payout, kind = slot_payout(reels, bet)
            luck_used = True

        paid, notes = await _settle(context.bot, chat_id, user.id, "slot", bet, payout)   # pagato PRIMA dell'animazione
        qmsg = quest_progress(chat_id, user.id, "slot_play")
        if luck_used:
            notes.insert(0, "🍀 Boost Fortuna: giro rilanciato!")

        head = game_header("🎰 <b>SLOT MACHINE 777</b> 🎰", name, bal_spin, bet)
        spin = "\n\n<i>Giro rulli in corso...</i>"

        # I tre rulli si fermano uno alla volta: all'inizio girano tutti, poi si fermano in sequenza
        await _safe_edit(query, head + "[ 🔄 | 🔄 | 🔄 ]" + spin)
        await asyncio.sleep(anim_gap(chat_id, "slot"))
        if frame_ok(chat_id):                 # i rulli si fermano uno alla volta; se il gruppo è al limite di Telegram si salta
            await _safe_edit(query, head + f"[ {reels[0]} | 🔄 | 🔄 ]" + spin)
            await asyncio.sleep(anim_gap(chat_id, "slot"))
        if frame_ok(chat_id):
            await _safe_edit(query, head + f"[ {reels[0]} | {reels[1]} | 🔄 ]" + spin)
            await asyncio.sleep(anim_gap(chat_id, "slot"))

        net = paid - bet
        head = game_header("🎰 <b>SLOT MACHINE 777</b> 🎰", name, get_user_coins(chat_id, user.id), bet)
        text = head + f"[ {reels[0]} | {reels[1]} | {reels[2]} ]\n\n"
        if kind == "triple" and reels[0] == "7️⃣":
            text += f"🔥 <b>JACKPOT SUPREMO 777!</b> 🔥 Hai vinto <b>+💳 {fmt(net)} $SDG</b>!"
        elif kind == "triple":
            text += f"🎉 <b>TRIPLETTA!</b> Hai vinto <b>+💳 {fmt(net)} $SDG</b>!"
        elif kind == "pair" and net > 0:
            text += f"✨ <b>DOPPIETTA D'ORO!</b> Ti torna la puntata più un bonus: <b>+💳 {fmt(net)} $SDG</b>!"
        elif kind == "pair":
            text += "✨ <b>DOPPIETTA!</b> Recuperi la tua puntata."
        else:
            text += f"💸 <b>NESSUNA COMBINAZIONE!</b> Hai perso {fmt(bet)} $SDG."
        text += _notes(notes) + (f"\n{qmsg}" if qmsg else "")

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

    if not await _game_on(query, "wordle"): return
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

    _charge(chat_id, user.id, 10)
    WORDLE_GAMES[game_key] = {
        "player_id": user.id, "name": user.first_name or "un giocatore",
        "secret": random.choice(WORDLE_WORDS),
        "attempts": 0, "history": [], "ts": time.time(), "last": time.time()
    }

    rewards = ", ".join(f"+{WORDLE_REWARDS[i] - 10}" for i in sorted(WORDLE_REWARDS))
    await _safe_edit(
        query,
        game_header("🔠 <b>WORDLE EXPRESS</b> 🔠", user.first_name, get_user_coins(chat_id, user.id), 10)
        + "Ho scelto una parola italiana di <b>5 lettere</b>!\n"
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

    if not await _game_on(query, "mastermind"): return
    if recently_started(MASTERMIND_GAMES, game_key):
        await query.answer("⏳ Hai già una partita a Mastermind in corso!", show_alert=True)
        return
    if get_user_coins(chat_id, user.id) < 10:
        await query.answer("❌ Servono 10 $SDG per giocare a Mastermind!", show_alert=True)
        return
    await query.answer()

    _charge(chat_id, user.id, 10)
    digits = list("0123456789")
    random.shuffle(digits)
    MASTERMIND_GAMES[game_key] = {
        "player_id": user.id, "name": user.first_name or "un giocatore",
        "secret": "".join(digits[:3]),
        "attempts": 0, "history": [], "ts": time.time(), "last": time.time()
    }

    await _safe_edit(
        query,
        game_header("🔐 <b>MASTERMIND EXPRESS</b> 🔐", user.first_name, get_user_coins(chat_id, user.id), 10)
        + "Ho scelto un codice segreto di <b>3 cifre uniche</b>!\n"
        "Scrivilo direttamente in chat per tentare (5 tentativi).\n"
        f"<i>Premio: {MASTERMIND_REWARDS[1]} $SDG se lo trovi entro il 3° tentativo, "
        f"{MASTERMIND_REWARDS[4]} al 4°, {MASTERMIND_REWARDS[5]} al 5°. Se abbandoni per 5 minuti, ti rimborso.</i>"
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
        (POKER_GAMES, "Poker", 10),
    ):
        for key, g in list(store.items()):
            if now - g.get("last", g.get("ts", now)) <= GAME_IDLE_SECS:
                continue
            store.pop(key, None)
            chat_id, user_id = key.rsplit("_", 1)
            refund = g.get("pot_p", g.get("bet", default_refund))      # Poker: rimborsa quanto già investito
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
# SDROGO HEIST
# V1 (utenti senza V2): rapina a bottoni casuali con cashout, invariata.
# V2: escape room a tempo (300 s), 5 livelli a skill. Il tipo di sessione lo decide hub.py alla creazione.
# =====================================================================
HEIST_COST = 150
HEIST_NOTE = "\n\n<i>I premi sono nel saldo del gruppo: riapri /sdrogocomm lì per vederli.</i>"

# ---------------- V1 (legacy) ----------------
# Probabilità di successo: L1 = 2/3, L2 = 62,5%, L3 = 2/3, L4 = 2/3, L5 = 50%.
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


def _choice_keyboard(stage: str, labels: list, user_id: int, n_win: int = 1):
    """Tastiera con i bottoni vincenti scelti a caso: la risposta giusta non è mai riconoscibile."""
    win_idx = set(random.sample(range(len(labels)), n_win))
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(lbl, callback_data=f"heist_{stage}res_{'win' if i in win_idx else 'fail'}_{user_id}")]
        for i, lbl in enumerate(labels)
    ])


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


# ---------------- V2 (escape room a tempo) ----------------
HEIST_TIME = 300              # timer globale in secondi
HEIST_SHARE = 0.20            # quota del Banco in premio
HEIST_SHARE_FOLLIA = 0.50     # durante la Follia Sdrogo la rapina "prosciuga" di più
HEIST_MIN_PRIZE = 300         # premio minimo garantito (altrimenti con il Banco vuoto non coprirebbe il pass)
HEIST_GUARD_FRAMES = 4
HEIST_FRAME_DELAY = 1.0


def heist_new_game(chat_id: int) -> dict:
    """Sessione Heist V2: la crea hub.py all'acquisto del pass."""
    return {"v2": True, "chat_id": chat_id, "level": 0, "started": False,
            "deadline": 0.0, "ts": time.time(), "puzzle": None, "hack": None}


def heist_intro_text() -> str:
    return (
        "🏢 <b>SDROGO HEIST - ESCAPE ROOM</b> 🕵️‍♂️\n\n"
        f"Hai <b>{HEIST_TIME // 60} minuti</b> (timer unico) per superare 5 livelli:\n"
        "1️⃣ Centralino • 2️⃣ Guardie • 3️⃣ Hacking • 4️⃣ Caveau • 5️⃣ Fuga finale\n\n"
        f"🎁 Premio: il <b>{int(HEIST_SHARE * 100)}%</b> del Banco Centrale "
        f"(<b>{int(HEIST_SHARE_FOLLIA * 100)}%</b> durante la Follia Sdrogo), minimo {HEIST_MIN_PRIZE} $SDG.\n"
        "⚠️ Un errore e la rapina fallisce. Il timer parte quando premi INIZIA."
    )


def heist_start_kb(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔓 INIZIA LA RAPINA (parte il timer)", callback_data=f"heist_go_{user_id}")]])


def _heist_left(game: dict) -> int:
    return max(0, int(game["deadline"] - time.time()))


def _heist_head(user, game: dict, level: int) -> str:
    return (
        f"🏢 <b>SDROGO HEIST</b> — Livello {level}/5\n"
        f"👤 Player: <b>{html.escape(user.first_name or 'Giocatore')}</b>\n"
        f"💳 Saldo Attuale: <b>{fmt(get_user_coins(game['chat_id'], user.id))} $SDG</b>\n"
        f"🎯 Puntata: <b>{fmt(HEIST_COST)} $SDG</b>\n"
        f"⏱️ Tempo rimasto: <b>{_heist_left(game)}s</b>\n\n"
    )


def _ans_kb(level: int, options: list, user_id: int, cols: int = 2) -> InlineKeyboardMarkup:
    rows, row = [], []
    for i, label in enumerate(options):
        row.append(InlineKeyboardButton(label, callback_data=f"heist_ans_{level}_{i}_{user_id}"))
        if len(row) == cols:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)


def _make_guards() -> dict:
    """Guardie sui monitor: una sola dorme in TUTTI i fotogrammi, ogni altra è sveglia almeno una volta."""
    n = random.choice([3, 4])
    target = random.randrange(n)
    awake_frame = {i: random.randrange(HEIST_GUARD_FRAMES) for i in range(n) if i != target}
    frames = []
    for f in range(HEIST_GUARD_FRAMES):
        row = []
        for i in range(n):
            if i == target:
                row.append("😴")
            elif awake_frame[i] == f:
                row.append("👀")
            else:
                row.append(random.choice(["😴", "👀"]))
        frames.append(row)
    return {"frames": frames, "ans": target, "options": [f"📺 Monitor {i + 1}" for i in range(n)]}


def _apply(op: tuple, v: int) -> int:
    sym, n = op
    if sym == "+":
        return v + n
    if sym == "−":
        return v - n
    return v * n


def _make_hack() -> dict:
    """Hacking: 3 pressioni esatte sulle operazioni per raggiungere il codice. Sempre risolvibile."""
    ops = [("+", random.randint(3, 9)), ("−", random.randint(2, 6)), ("×", random.choice([2, 3]))]
    while True:
        start = random.randint(1, 9)
        val = start
        for i in [random.randrange(3) for _ in range(3)]:
            val = _apply(ops[i], val)
        if 0 < val <= 200 and val != start:
            return {"ops": ops, "val": start, "target": val, "left": 3}


def _hack_text(user, game: dict) -> str:
    hk = game["hack"]
    return (
        _heist_head(user, game, 3)
        + "💻 <b>HACKING</b>\n\nHai <b>3 pressioni</b> per portare il valore al codice obiettivo.\n\n"
        f"🔢 Valore attuale: <b>{hk['val']}</b>\n🎯 Codice obiettivo: <b>{hk['target']}</b>\n"
        f"🕹️ Mosse rimaste: <b>{hk['left']}</b>"
    )


def _hack_kb(user_id: int, hk: dict) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(f"{sym}{n}", callback_data=f"heist_hack_{i}_{user_id}")
        for i, (sym, n) in enumerate(hk["ops"])
    ]])


def _make_vault() -> dict:
    """Caveau: ordine A-B-C da ricavare dagli indizi, che identificano UNA sola combinazione."""
    letters = ["A", "B", "C"]
    perms = list(itertools.permutations(letters))
    secret = random.choice(perms)
    pool = []
    for L in letters:
        pos = secret.index(L)
        pool.append((f"<b>{L}</b> è in posizione {pos + 1}", lambda p, L=L, pos=pos: p.index(L) == pos))
        for q in range(3):
            if q != pos:
                pool.append((f"<b>{L}</b> NON è in posizione {q + 1}", lambda p, L=L, q=q: p.index(L) != q))
    for X in letters:
        for Y in letters:
            if X == Y:
                continue
            if secret.index(X) < secret.index(Y):
                pool.append((f"<b>{X}</b> viene prima di <b>{Y}</b>", lambda p, X=X, Y=Y: p.index(X) < p.index(Y)))
            else:
                pool.append((f"<b>{X}</b> viene dopo <b>{Y}</b>", lambda p, X=X, Y=Y: p.index(X) > p.index(Y)))
    random.shuffle(pool)
    remaining, clues = list(perms), []
    for text, fn in pool:
        narrowed = [p for p in remaining if fn(p)]
        if len(narrowed) < len(remaining):
            clues.append(text)
            remaining = narrowed
        if len(remaining) == 1:
            break
    options = [" → ".join(p) for p in perms]
    random.shuffle(options)
    return {
        "text": "🔐 <b>CAVEAU</b>\n\nTrova la combinazione di sblocco dell'ordine A-B-C. Indizi:\n"
                + "\n".join(f"• {c}" for c in clues),
        "options": options, "ans": options.index(" → ".join(secret)),
    }


_ESC_WEATHER = ["☀️ cielo sereno", "⛈️ tempesta in arrivo", "🌧️ pioggia torrenziale con allagamenti"]
_ESC_POLICE = ["🚁 elicotteri della polizia in volo", "🚧 blocchi stradali su tutte le uscite", "🚓 poche pattuglie sparse"]
_ESC_INFORMANT = ["🕵️ l'informatore dice: le fogne sono libere", "🕵️ l'informatore dice: le fogne sono sorvegliate"]
_ESC_VEHICLES = ["🚁 Elicottero", "🏍️ Moto", "🕳️ Fognature"]


def _make_escape() -> dict:
    """Fuga finale: indizi meteo/polizia/informatore; esattamente UN mezzo è praticabile."""
    combos = []
    for w in range(3):
        for p in range(3):
            for i in range(2):
                ok = [w != 1 and p != 0, p != 1, i == 0 and w != 2]
                if sum(ok) == 1:
                    combos.append((w, p, i, ok.index(True)))
    w, p, i, good = random.choice(combos)
    order = list(range(3))
    random.shuffle(order)
    options = [_ESC_VEHICLES[k] for k in order]
    return {
        "text": (
            "🚁 <b>FUGA FINALE</b>\n\nScegli il mezzo per scappare col bottino.\n\n"
            f"🌤️ Meteo: {_ESC_WEATHER[w]}\n👮 Polizia: {_ESC_POLICE[p]}\n{_ESC_INFORMANT[i]}\n\n"
            "<i>Regole:\n"
            "🚁 Elicottero: non decolla con la tempesta ed è inutile se la polizia ha elicotteri in volo.\n"
            "🏍️ Moto: bloccata dai blocchi stradali.\n"
            "🕳️ Fognature: impraticabili con gli allagamenti o se sono sorvegliate.</i>"
        ),
        "options": options, "ans": order.index(good),
    }


async def _heist_present(query, game: dict, user):
    lvl = game["level"]
    uid = user.id
    if lvl == 1:
        p = _puzzle_wires()
        game["puzzle"] = p
        await _safe_edit(query, _heist_head(user, game, 1) + p["text"], _ans_kb(1, p["options"], uid, 2))
    elif lvl == 2:
        p = _make_guards()
        game["puzzle"] = None
        for i, row in enumerate(p["frames"]):
            line = "   ".join(f"📺{j + 1} {e}" for j, e in enumerate(row))
            await _safe_edit(
                query,
                _heist_head(user, game, 2) + f"💂 <b>GUARDIE</b>\nGuarda bene i monitor!\n\nFotogramma {i + 1}/{len(p['frames'])}:\n{line}"
            )
            await asyncio.sleep(HEIST_FRAME_DELAY)
        game["puzzle"] = {"ans": p["ans"], "options": p["options"]}
        await _safe_edit(
            query,
            _heist_head(user, game, 2) + "💂 <b>GUARDIE</b>\n\nQuale guardia ha dormito in <b>TUTTI</b> i fotogrammi? Quella è la via libera!",
            _ans_kb(2, p["options"], uid, 2)
        )
    elif lvl == 3:
        game["hack"] = _make_hack()
        await _safe_edit(query, _hack_text(user, game), _hack_kb(uid, game["hack"]))
    elif lvl == 4:
        p = _make_vault()
        game["puzzle"] = p
        await _safe_edit(query, _heist_head(user, game, 4) + p["text"], _ans_kb(4, p["options"], uid, 2))
    else:
        p = _make_escape()
        game["puzzle"] = p
        await _safe_edit(query, _heist_head(user, game, 5) + p["text"], _ans_kb(5, p["options"], uid, 1))


async def _heist_fail(query, game: dict, user_id: int, reason: str):
    HEIST_GAMES.pop(user_id, None)
    record_single_result(game["chat_id"], user_id, False, -HEIST_COST, game="heist", bet=HEIST_COST)
    await _safe_edit(query, f"{reason}\n\n💸 Rapina fallita: hai perso il pass da {fmt(HEIST_COST)} $SDG.{HEIST_NOTE}")


async def _heist_win(context, query, game: dict, user):
    user_id, chat_id = user.id, game["chat_id"]
    share = HEIST_SHARE_FOLLIA if follia_active() else HEIST_SHARE
    taken = banco_take(int(banco_balance() * share))
    prize = max(taken, HEIST_MIN_PRIZE)
    elapsed = HEIST_TIME - _heist_left(game)
    HEIST_GAMES.pop(user_id, None)
    add_user_coins(chat_id, user_id, prize)
    record_single_result(chat_id, user_id, True, prize - HEIST_COST, game="heist", bet=HEIST_COST)
    d = inv(chat_id, user_id)
    d["stars"] = d.get("stars", 0) + 1
    d["titles"] = d.get("titles", 0) + 1
    d["persecutes"] = d.get("persecutes", 0) + 1
    save_db()
    await _safe_edit(
        query,
        "🏆 <b>RAPINA PERFETTA COMPLETATA!</b>\n"
        f"⏱️ Tempo impiegato: <b>{elapsed}s</b>\n"
        f"💰 Hai svaligiato il Banco Centrale: <b>+{fmt(prize)} $SDG</b>, 1 Titolo, 1 Persecuzione e 1 STELLA ⭐!" + HEIST_NOTE
    )
    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"👑 <b>COLPO DEL SECOLO!</b> 🏢\n\n"
                f"<b>{html.escape(user.first_name or 'Un giocatore')}</b> ha superato l'escape room di Sdrogo Heist "
                f"in {elapsed} secondi e ha svuotato una parte del Banco Centrale!\n"
                f"Guadagna <b>💳 {fmt(prize)} $SDG</b> e 1 STELLA ⭐ di prestigio!"
            ),
            parse_mode="HTML"
        )
    except Exception:
        pass
    await banco_check(context.bot)


async def expire_heists(context: ContextTypes.DEFAULT_TYPE):
    """Chiude le rapine V2 con il timer scaduto anche se il giocatore non clicca più nulla."""
    now = time.time()
    for uid, g in list(HEIST_GAMES.items()):
        if g.get("v2") and g.get("started") and now > g["deadline"] + 2:
            HEIST_GAMES.pop(uid, None)
            record_single_result(g["chat_id"], uid, False, -HEIST_COST, game="heist", bet=HEIST_COST)
            try:
                await context.bot.send_message(
                    chat_id=uid, parse_mode="HTML",
                    text=f"⏰ <b>TEMPO SCADUTO!</b> La polizia è arrivata: rapina fallita, pass da {fmt(HEIST_COST)} $SDG perso."
                )
            except Exception:
                pass


@serialized("user")
async def handle_heist_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    parts = query.data.split("_")
    stage = parts[1]
    owner_id = int(parts[-1])

    if query.from_user.id != owner_id:
        await query.answer("🛑 Questa non è la tua rapina!", show_alert=True)
        return

    user = query.from_user
    user_id = user.id
    if user_id not in HEIST_GAMES:
        await query.answer()
        await _safe_edit(query, "❌ Sessione Rapina terminata.")
        return

    game = HEIST_GAMES[user_id]
    game["ts"] = time.time()
    chat_id = game["chat_id"]

    # ===================== HEIST V2 =====================
    if stage in ("go", "ans", "hack"):
        if not game.get("v2"):
            await query.answer()
            return

        if stage == "go":
            if game["started"]:
                await query.answer()
                return
            await query.answer()
            game["started"] = True
            game["deadline"] = time.time() + HEIST_TIME
            game["level"] = 1
            await _heist_present(query, game, user)
            return

        if not game["started"]:
            await query.answer("Premi prima INIZIA LA RAPINA!", show_alert=True)
            return
        if time.time() > game["deadline"]:
            await query.answer()
            await _heist_fail(query, game, user_id, "⏰ <b>TEMPO SCADUTO!</b> La polizia è arrivata.")
            return

        if stage == "ans":            # heist_ans_{livello}_{indice}_{uid}
            lvl, idx = int(parts[2]), int(parts[3])
            puzzle = game.get("puzzle")
            if lvl != game["level"] or not puzzle:
                await query.answer()
                return
            game["puzzle"] = None      # consumato: un secondo tap viene ignorato
            await query.answer()
            if idx != puzzle["ans"]:
                await _heist_fail(query, game, user_id, "💥 <b>SCELTA SBAGLIATA!</b> L'allarme è scattato.")
                return
        else:                          # heist_hack_{op}_{uid}
            hk = game.get("hack")
            if game["level"] != 3 or not hk:
                await query.answer()
                return
            await query.answer()
            hk["val"] = _apply(hk["ops"][int(parts[2])], hk["val"])
            hk["left"] -= 1
            if hk["left"] > 0:
                await _safe_edit(query, _hack_text(user, game), _hack_kb(user_id, hk))
                return
            game["hack"] = None
            if hk["val"] != hk["target"]:
                await _heist_fail(query, game, user_id,
                                  f"💥 <b>CODICE ERRATO!</b> Hai ottenuto {hk['val']} invece di {hk['target']}.")
                return

        # livello superato
        if game["level"] >= 5:
            await _heist_win(context, query, game, user)
        else:
            game["level"] += 1
            await _heist_present(query, game, user)
        return

    # ===================== HEIST V1 (legacy) =====================
    if game.get("v2"):
        await query.answer()
        return

    await query.answer()
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
            record_single_result(chat_id, user_id, False, -HEIST_COST, game="heist", bet=HEIST_COST)
            await _safe_edit(query, HEIST_FAIL_TEXT[n])

        elif n == 5:
            del HEIST_GAMES[user_id]
            add_user_coins(chat_id, user_id, HEIST_FINAL)
            record_single_result(chat_id, user_id, True, HEIST_FINAL - HEIST_COST, game="heist", bet=HEIST_COST)
            inv_key = ukey(user_id)
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
                        f"<b>{html.escape(user.first_name or 'Un giocatore')}</b> ha svaligiato il Caveau di Sdrogo Heist "
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
            record_single_result(chat_id, user_id, False, -HEIST_COST, game="heist", bet=HEIST_COST)
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
        record_single_result(chat_id, user_id, amount > HEIST_COST, amount - HEIST_COST, game="heist", bet=HEIST_COST)
        await _safe_edit(
            query,
            f"💰 <b>CASHOUT EFFETTUATO!</b> Ti ritiri dalla rapina incassando <b>+💳 {amount} $SDG</b>!" + HEIST_NOTE
        )


# =====================================================================
# AVIATOR, ROULETTE, GRATTA E VINCI E SDROGO POKER (single player)
# Callback con prefisso "sp_": sp_avi*, sp_roul*, sp_scr*, sp_pk*.
# Ogni gioco è accendibile/spegnibile da /admin (GAME_STATUS). Lo spegnimento blocca solo le NUOVE giocate:
# voli, biglietti e mani già iniziati si possono concludere.
# =====================================================================
SP_BETS = (10, 25, 50, 100)


def _sp_back_row(owner_id: int):
    return [
        InlineKeyboardButton("🕹️ Single Player", callback_data=f"hub_single_{owner_id}"),
        InlineKeyboardButton("🔙 HUB", callback_data=f"hub_main_{owner_id}"),
    ]


def _sp_bet_keyboard(prefix: str, owner_id: int):
    return [
        [InlineKeyboardButton("10 $SDG", callback_data=f"{prefix}_10_{owner_id}"), InlineKeyboardButton("25 $SDG", callback_data=f"{prefix}_25_{owner_id}")],
        [InlineKeyboardButton("50 $SDG", callback_data=f"{prefix}_50_{owner_id}"), InlineKeyboardButton("100 $SDG", callback_data=f"{prefix}_100_{owner_id}")],
        _sp_back_row(owner_id),
    ]


# ---------------------------------------------------------------------
# 1) SDROGO ROULETTE
# ---------------------------------------------------------------------
RED_NUMBERS = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}
ROUL_CHOICES = {
    "red": "🔴 Rosso", "black": "⚫ Nero",
    "even": "🔢 Pari", "odd": "🔢 Dispari",
    "green": "🟢 Verde (0)",
}
ROUL_FRAME_DELAY = 1.0


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


async def handle_sp_roulette(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    parts = query.data.split("_")        # sp_roul_OWNER | sp_roulbet_BET_OWNER | sp_roulpick_BET_CHOICE_OWNER
    action = parts[1]
    try:
        owner_id = int(parts[-1])
    except ValueError:
        await query.answer()
        return

    if not await verify_user_lock(query, owner_id):
        return
    if not await _game_on(query, "roulette"):
        return

    user = query.from_user
    chat_id = query.message.chat_id
    name = user.first_name or "Giocatore"
    coins = get_user_coins(chat_id, user.id)
    title = "🎡 <b>SDROGO ROULETTE</b> 🎡"

    # Fase 1: scelta della puntata
    if action == "roul":
        await query.answer()
        text = (
            game_header(title, name, coins)
            + "Rosso / Nero / Pari / Dispari pagano 1:1, il Verde (0) paga 35:1.\n"
            "<i>Lo zero fa perdere tutto tranne chi ha puntato sul Verde.</i>\n\n"
            "<b>Fase 1:</b> scegli la puntata."
        )
        await _safe_edit(query, text, InlineKeyboardMarkup(_sp_bet_keyboard("sp_roulbet", owner_id)))
        return

    # Fase 2: su cosa puntare
    if action == "roulbet":
        try:
            bet = int(parts[2])
        except (IndexError, ValueError):
            await query.answer()
            return
        if bet not in SP_BETS:
            await query.answer()
            return
        if coins < bet:
            await query.answer(f"❌ Servono {bet} $SDG per questa puntata!", show_alert=True)
            return
        await query.answer()
        text = game_header(title, name, coins, bet) + "<b>Fase 2:</b> su cosa punti?"
        keyboard = [
            [InlineKeyboardButton("🔴 Rosso", callback_data=f"sp_roulpick_{bet}_red_{owner_id}"), InlineKeyboardButton("⚫ Nero", callback_data=f"sp_roulpick_{bet}_black_{owner_id}")],
            [InlineKeyboardButton("🔢 Pari", callback_data=f"sp_roulpick_{bet}_even_{owner_id}"), InlineKeyboardButton("🔢 Dispari", callback_data=f"sp_roulpick_{bet}_odd_{owner_id}")],
            [InlineKeyboardButton("🟢 Verde (0) · 35:1", callback_data=f"sp_roulpick_{bet}_green_{owner_id}")],
            [InlineKeyboardButton("💰 Cambia puntata", callback_data=f"sp_roul_{owner_id}")],
            _sp_back_row(owner_id),
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
        if bet not in SP_BETS or choice not in ROUL_CHOICES:
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
            luck = has_luck(chat_id, user.id, "roulette")
            _charge(chat_id, user.id, bet)
            bal_spin = get_user_coins(chat_id, user.id)

            n = random.randint(0, 36)
            mult = roulette_multiplier(choice, n)
            luck_used = False
            if mult == 0 and luck and random.random() < LUCK_REROLL_CHANCE:
                n = random.randint(0, 36)
                mult = roulette_multiplier(choice, n)
                luck_used = True

            if n == 0:
                from economy import bump_stat
                bump_stat(user.id, "roul_zero")
            paid, notes = await _settle(context.bot, chat_id, user.id, "roulette", bet, bet * mult)   # PRIMA dell'animazione
            qmsg = quest_progress(chat_id, user.id, "roulette_play")
            if luck_used:
                notes.insert(0, "🍀 Boost Fortuna: giro rilanciato!")

            head = game_header(title, name, bal_spin, bet) + f"🎲 Su: <b>{ROUL_CHOICES[choice]}</b>\n\n"
            frames = [
                "🎡 <i>La ruota gira...</i>",
                "🌀 <i>La pallina rimbalza...</i>",
                "⏳ <i>Sta rallentando...</i>",
            ]
            for i, frame in enumerate(frames):
                if i > 0 and not frame_ok(chat_id):
                    continue                  # gruppo al limite di Telegram: si salta il fotogramma
                await _safe_edit(query, head + f"{frame}\n\n[ {_wheel_strip()} ]")
                await asyncio.sleep(anim_gap(chat_id, "roulette"))

            net = paid - bet
            head = game_header(title, name, get_user_coins(chat_id, user.id), bet) + f"🎲 Su: <b>{ROUL_CHOICES[choice]}</b>\n\n"
            text = head + f"⚪ La pallina si ferma sul <b>{n}</b> {roulette_color_emoji(n)}\n\n"
            if mult == 36:
                text += f"🔥 <b>VERDE! PAGA 35:1!</b> Hai vinto <b>+💳 {fmt(net)} $SDG</b>!"
            elif mult == 2:
                text += f"🎉 <b>VINTO!</b> Hai vinto <b>+💳 {fmt(net)} $SDG</b>!"
            else:
                text += f"💸 <b>PERSO!</b> Hai perso {fmt(bet)} $SDG."
            text += _notes(notes) + (f"\n{qmsg}" if qmsg else "")

            end_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton(f"🔂 Rigioca ({bet} su {ROUL_CHOICES[choice]})", callback_data=f"sp_roulpick_{bet}_{choice}_{owner_id}")],
                [InlineKeyboardButton("💰 Cambia puntata", callback_data=f"sp_roul_{owner_id}")],
                _sp_back_row(owner_id),
            ])
            await _safe_edit(query, text, end_keyboard)
        finally:
            LAB_BUSY.discard(key)
        return

    await query.answer()


# ---------------------------------------------------------------------
# 2) SDROGO AVIATOR
# ---------------------------------------------------------------------
AVIATOR_RATE = 0.075        # crescita continua del moltiplicatore: ×(e^(0.075·t)), cioè circa +7,8% al secondo
AVIATOR_GRACE = 0.25        # secondi di tolleranza a favore del giocatore per il ritardo di rete del clic
AVIATOR_MAX_CRASH = 100.0


def _avi_mult(elapsed: float) -> float:
    """Moltiplicatore dopo `elapsed` secondi di volo (curva continua, arrotondata per difetto a 2 decimali)."""
    return max(1.0, math.floor(math.exp(AVIATOR_RATE * max(0.0, elapsed)) * 100) / 100)


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
        game_header("✈️ <b>SDROGO AVIATOR</b> ✈️", game["name"], game["coins_view"], game["bet"])
        + f"🚀 Moltiplicatore: <b>{mult:.2f}x</b>\n"
        f"💵 Incasso attuale: <b>{fmt(int(game['bet'] * mult))} $SDG</b>\n\n"
        f"{status}"
    )


def _avi_end_keyboard(bet: int, owner_id: int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🔂 Rivola ({bet} $SDG)", callback_data=f"sp_avibet_{bet}_{owner_id}")],
        [InlineKeyboardButton("💰 Cambia puntata", callback_data=f"sp_avi_{owner_id}")],
        _sp_back_row(owner_id),
    ])


async def _aviator_flight(query, game: dict, key: str, owner_id: int, chat_id: int, user_id: int):
    bet, crash = game["bet"], game["crash"]
    bot = query.get_bot()
    cash_kb = InlineKeyboardMarkup([[InlineKeyboardButton("💰 CASHOUT", callback_data=f"sp_avicash_{owner_id}")]])

    async def finish_crash():
        game["finished"] = True
        if AVIATOR_GAMES.get(key) is game:
            del AVIATOR_GAMES[key]
        _, notes = await _settle(bot, chat_id, user_id, "aviator", bet, 0)
        text = (
            game_header("✈️ <b>SDROGO AVIATOR</b> ✈️", game["name"], get_user_coins(chat_id, user_id), bet)
            + f"💥 <b>L'aereo è volato via (Crash a {crash:.2f}x)!</b>\n\n"
            f"💸 Hai perso {fmt(bet)} $SDG." + _notes(notes)
        )
        await _safe_edit(query, text, _avi_end_keyboard(bet, owner_id))

    async with game["lock"]:
        await _safe_edit(query, _avi_text(game, 1.0, "🛫 <i>Decollo in corso...</i>"), cash_kb)

    if crash <= 1.0:
        await asyncio.sleep(1.5)
        async with game["lock"]:
            if not game["finished"]:
                await finish_crash()
        return

    # Il moltiplicatore è una funzione del TEMPO (orologio del server): game["t0"] = inizio del volo.
    # Le modifiche al messaggio mostrano solo il valore del momento (privato 0,5 s, gruppo 0,8 s); il CASHOUT
    # viene invece calcolato sull'istante esatto del clic, quindi il ritmo delle animazioni non cambia il payout.
    game["t0"] = time.time()
    crash_t = math.log(crash) / AVIATOR_RATE          # istante (secondi di volo) in cui l'aereo vola via
    gap = anim_gap(chat_id, "aviator")
    mult = 1.0
    while True:
        elapsed = time.time() - game["t0"]
        await asyncio.sleep(min(gap, max(0.05, crash_t - elapsed + 0.02)))   # il crash compare nell'istante in cui avviene
        async with game["lock"]:
            if game["finished"]:
                return
            elapsed = time.time() - game["t0"]
            if elapsed >= crash_t:
                await finish_crash()
                return
            nxt = _avi_mult(elapsed)
            if nxt > mult and frame_ok(chat_id):      # gruppo al limite di Telegram: si salta l'aggiornamento
                await _safe_edit(query, _avi_text(game, nxt, "🚀 <i>Sale... premi CASHOUT prima che voli via!</i>"), cash_kb)
                game["current"] = nxt
                mult = nxt
            if game["finished"]:
                return


async def handle_sp_aviator(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t_click = time.time()               # istante in cui il server riceve il clic: prima di qualunque altra attesa
    query = update.callback_query
    parts = query.data.split("_")        # sp_avi_OWNER | sp_avibet_BET_OWNER | sp_avicash_OWNER
    action = parts[1]
    try:
        owner_id = int(parts[-1])
    except ValueError:
        await query.answer()
        return

    if not await verify_user_lock(query, owner_id):
        return

    user = query.from_user
    chat_id = query.message.chat_id
    key = f"{chat_id}_{user.id}"
    name = user.first_name or "Giocatore"

    # Scelta puntata
    if action == "avi":
        if not await _game_on(query, "aviator"):
            return
        await query.answer()
        text = (
            game_header("✈️ <b>SDROGO AVIATOR</b> ✈️", name, get_user_coins(chat_id, user.id))
            + "Il moltiplicatore sale finché l'aereo non vola via. Premi <b>CASHOUT</b> prima del crash "
            "per incassare puntata × moltiplicatore. Se l'aereo vola via prima, perdi la puntata.\n\n"
            "Scegli la puntata:"
        )
        await _safe_edit(query, text, InlineKeyboardMarkup(_sp_bet_keyboard("sp_avibet", owner_id)))
        return

    # CASHOUT (sempre permesso, anche se il gioco viene spento a volo iniziato)
    if action == "avicash":
        game = AVIATOR_GAMES.get(key)
        if not game or game["finished"]:
            await query.answer("✈️ Il volo è già terminato!", show_alert=True)
            return
        # sincrono, senza await: il ciclo di volo vede subito finished=True
        game["finished"] = True
        bet = game["bet"]
        if AVIATOR_GAMES.get(key) is game:
            del AVIATOR_GAMES[key]
        t0 = game.get("t0")
        el_click = max(0.0, t_click - t0 - AVIATOR_GRACE) if t0 else 0.0
        crash_t = math.log(game["crash"]) / AVIATOR_RATE if game["crash"] > 1.0 else 0.0
        if t0 and el_click >= crash_t:
            # il clic è arrivato dopo il crash (anche se il messaggio non era ancora stato aggiornato)
            await query.answer("💥 Troppo tardi: l'aereo era già volato via!", show_alert=True)
            _, notes = await _settle(context.bot, chat_id, user.id, "aviator", bet, 0)
            async with game["lock"]:
                text = (
                    game_header("✈️ <b>SDROGO AVIATOR</b> ✈️", name, get_user_coins(chat_id, user.id), bet)
                    + f"💥 <b>L'aereo è volato via (Crash a {game['crash']:.2f}x)!</b>\n\n"
                    f"💸 Hai perso {fmt(bet)} $SDG." + _notes(notes)
                )
                await _safe_edit(query, text, _avi_end_keyboard(bet, owner_id))
            return
        cur = _avi_mult(el_click) if t0 else 1.0
        base = int(bet * cur)
        await query.answer(f"💰 Incassato a {cur:.2f}x!")
        paid, notes = await _settle(context.bot, chat_id, user.id, "aviator", bet, base)
        async with game["lock"]:
            net = paid - bet
            text = (
                game_header("✈️ <b>SDROGO AVIATOR</b> ✈️", name, get_user_coins(chat_id, user.id), bet)
                + f"💰 <b>CASHOUT a {cur:.2f}x!</b>\n"
                f"Incasso: <b>{fmt(paid)} $SDG</b> (<b>{_sign(net)}</b>)\n\n"
                f"<i>L'aereo sarebbe volato via a {game['crash']:.2f}x.</i>" + _notes(notes)
            )
            await _safe_edit(query, text, _avi_end_keyboard(bet, owner_id))
        return

    # Avvio volo
    if action == "avibet":
        try:
            bet = int(parts[2])
        except (IndexError, ValueError):
            await query.answer()
            return
        if bet not in SP_BETS:
            await query.answer()
            return
        existing = AVIATOR_GAMES.get(key)
        if existing and not existing["finished"] and time.time() - existing["ts"] < 300:
            await query.answer("✈️ Hai già un volo in corso!", show_alert=True)
            return
        if not await _game_on(query, "aviator"):
            return
        if get_user_coins(chat_id, user.id) < bet:
            await query.answer(f"❌ Servono {bet} $SDG per questa puntata!", show_alert=True)
            return

        # nessun await tra i controlli e queste righe: due tap simultanei non possono passare entrambi
        _charge(chat_id, user.id, bet)
        game = {
            "bet": bet, "crash": aviator_crash_point(), "current": 1.0, "name": name,
            "coins_view": get_user_coins(chat_id, user.id),
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
# 3) GRATTA E VINCI
# ---------------------------------------------------------------------
SCRATCH_COST = 20
SCRATCH_PICKS = 3
# (simbolo, premio in $SDG, probabilità per casella): ritorno atteso ≈ 91% del costo con 3 caselle
SCRATCH_TABLE = [("💀", 0, 0.62), ("💰", 10, 0.28), ("💎", 25, 0.09), ("👑", 100, 0.01)]
# con Boost Fortuna: ritorno atteso ≈ 111%
SCRATCH_TABLE_LUCK = [("💀", 0, 0.59), ("💰", 10, 0.29), ("💎", 25, 0.10), ("👑", 100, 0.02)]
SCRATCH_PRIZE = {sym: prize for sym, prize, _ in SCRATCH_TABLE}


def scratch_grid(luck: bool = False) -> list:
    table = SCRATCH_TABLE_LUCK if luck else SCRATCH_TABLE
    return random.choices([s for s, _, _ in table], weights=[w for _, _, w in table], k=9)


def _scratch_keyboard(game: dict, owner_id: int, finished: bool = False):
    rows = []
    for r in range(3):
        row = []
        for c in range(3):
            i = r * 3 + c
            sym = game["grid"][i]
            if i in game["picked"]:
                row.append(InlineKeyboardButton(f"✅{sym}", callback_data=f"sp_scrnoop_{owner_id}"))
            elif finished:
                row.append(InlineKeyboardButton(sym, callback_data=f"sp_scrnoop_{owner_id}"))
            else:
                row.append(InlineKeyboardButton("🎁", callback_data=f"sp_scrcell_{i}_{owner_id}"))
        rows.append(row)
    if finished:
        rows.append([InlineKeyboardButton(f"🎟️ Nuovo biglietto ({SCRATCH_COST} $SDG)", callback_data=f"sp_scr_{owner_id}")])
        rows.append(_sp_back_row(owner_id))
    return rows


def _scratch_text(game: dict, coins: int) -> str:
    left = SCRATCH_PICKS - len(game["picked"])
    found = " ".join(game["grid"][i] for i in game["picked"]) or "—"
    return (
        game_header("🎟️ <b>GRATTA E VINCI</b> 🎟️", game["name"], coins, SCRATCH_COST)
        + f"Grattate rimaste: <b>{left}</b>\n"
        f"Trovato finora: {found}\n\n"
        "💀 vuoto • 💰 10 • 💎 25 • 👑 100 $SDG"
    )


@serialized("user")
async def handle_sp_scratch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    parts = query.data.split("_")        # sp_scr_OWNER | sp_scrcell_IDX_OWNER | sp_scrnoop_OWNER
    action = parts[1]
    try:
        owner_id = int(parts[-1])
    except ValueError:
        await query.answer()
        return

    if not await verify_user_lock(query, owner_id):
        return

    user = query.from_user
    chat_id = query.message.chat_id
    key = f"{chat_id}_{user.id}"
    name = user.first_name or "Giocatore"

    if action == "scrnoop":
        await query.answer()
        return

    # Acquisto biglietto
    if action == "scr":
        game = SCRATCH_GAMES.get(key)
        if game and time.time() - game["ts"] < 600:
            await query.answer("🎟️ Hai già un biglietto da finire!")
            await _safe_edit(query, _scratch_text(game, get_user_coins(chat_id, user.id)),
                             InlineKeyboardMarkup(_scratch_keyboard(game, owner_id)))
            return
        if not await _game_on(query, "scratch"):
            return
        if get_user_coins(chat_id, user.id) < SCRATCH_COST:
            await query.answer(f"❌ Servono {SCRATCH_COST} $SDG per un biglietto!", show_alert=True)
            return
        await query.answer()
        luck = has_luck(chat_id, user.id, "scratch")
        _charge(chat_id, user.id, SCRATCH_COST)
        game = {"grid": scratch_grid(luck), "picked": [], "ts": time.time(), "name": name}
        SCRATCH_GAMES[key] = game
        await _safe_edit(query, _scratch_text(game, get_user_coins(chat_id, user.id)),
                         InlineKeyboardMarkup(_scratch_keyboard(game, owner_id)))
        return

    # Grattata di una casella (sempre permessa: il biglietto già pagato si può finire)
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
            await _safe_edit(query, _scratch_text(game, get_user_coins(chat_id, user.id)),
                             InlineKeyboardMarkup(_scratch_keyboard(game, owner_id)))
            return

        # biglietto finito: somma dei premi, accredito e tastiera disabilitata
        base = sum(SCRATCH_PRIZE[game["grid"][i]] for i in game["picked"])
        del SCRATCH_GAMES[key]
        paid, notes = await _settle(context.bot, chat_id, user.id, "scratch", SCRATCH_COST, base)
        qmsg = quest_progress(chat_id, user.id, "scratch_play")
        found = " ".join(game["grid"][i] for i in game["picked"])
        net = paid - SCRATCH_COST
        if paid == 0:
            verdict = "💸 Nessun premio, ritenta!"
        else:
            verdict = f"🎉 Hai vinto <b>{fmt(paid)} $SDG</b> (<b>{_sign(net)}</b> netti)!"
        text = (
            game_header("🎟️ <b>GRATTA E VINCI</b> 🎟️", name, get_user_coins(chat_id, user.id), SCRATCH_COST)
            + f"Hai grattato: {found}\n\n{verdict}\n\n"
            "<i>Le caselle senza ✅ sono quelle che non hai scoperto.</i>"
            + _notes(notes) + (f"\n{qmsg}" if qmsg else "")
        )
        await _safe_edit(query, text, InlineKeyboardMarkup(_scratch_keyboard(game, owner_id, finished=True)))
        return

    await query.answer()


# ---------------------------------------------------------------------
# 4) SDROGO POKER (Texas Hold'em semplificato, 1v1 contro il Bot)
# Ante = puntata. 4 giri (pre-flop, flop, turn, river): ogni giro puoi Passare/Vedere, Rilanciare (+puntata) o Lasciare.
# Il bot punta o passa in base alla forza della mano (con qualche bluff) e non rilancia mai.
# I pareggi restituiscono la puntata: nessun vantaggio fisso per il banco.
# ---------------------------------------------------------------------
POKER_SUITS = ["♠️", "♥️", "♦️", "♣️"]
POKER_STREETS = ["Pre-flop", "Flop", "Turn", "River"]
HAND_NAMES = ["Carta alta", "Coppia", "Doppia coppia", "Tris", "Scala", "Colore", "Full", "Poker", "Scala colore"]


def _card_str(c: tuple) -> str:
    names = {11: "J", 12: "Q", 13: "K", 14: "A"}
    return f"{names.get(c[0], c[0])}{POKER_SUITS[c[1]]}"


def _cards_str(cards: list) -> str:
    return " ".join(_card_str(c) for c in cards) if cards else "—"


def _new_deck() -> list:
    deck = [(r, s) for r in range(2, 15) for s in range(4)]
    random.shuffle(deck)
    return deck


def _eval5(cards) -> tuple:
    ranks = sorted((c[0] for c in cards), reverse=True)
    flush = len({c[1] for c in cards}) == 1
    uniq = sorted(set(ranks), reverse=True)
    straight_high = None
    if len(uniq) == 5:
        if uniq[0] - uniq[4] == 4:
            straight_high = uniq[0]
        elif uniq == [14, 5, 4, 3, 2]:
            straight_high = 5
    counts = {}
    for r in ranks:
        counts[r] = counts.get(r, 0) + 1
    groups = sorted(counts.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)
    pattern = [g[1] for g in groups]
    if straight_high and flush:
        return (8, [straight_high])
    if pattern[0] == 4:
        return (7, [groups[0][0], groups[1][0]])
    if pattern == [3, 2]:
        return (6, [groups[0][0], groups[1][0]])
    if flush:
        return (5, ranks)
    if straight_high:
        return (4, [straight_high])
    if pattern[0] == 3:
        return (3, [g[0] for g in groups])
    if pattern == [2, 2, 1]:
        return (2, [g[0] for g in groups])
    if pattern[0] == 2:
        return (1, [g[0] for g in groups])
    return (0, ranks)


def _best_hand(cards: list) -> tuple:
    return max(_eval5(c) for c in itertools.combinations(cards, 5))


def _bot_strength(hole: list, board: list) -> float:
    """Forza 0..1 della mano del bot (il bot conosce solo le sue carte e il tavolo)."""
    if not board:
        r1, r2 = sorted((hole[0][0], hole[1][0]), reverse=True)
        s = 0.30 + (r1 + r2) / 28 * 0.35
        if r1 == r2:
            s += 0.25 + r1 / 14 * 0.05
        if hole[0][1] == hole[1][1]:
            s += 0.04
        if r1 != r2 and r1 - r2 <= 2:
            s += 0.03
        return min(1.0, s)
    cat, tb = _best_hand(hole + board)
    base = {0: 0.15, 1: 0.5, 2: 0.7, 3: 0.8, 4: 0.85, 5: 0.9, 6: 0.95, 7: 1.0, 8: 1.0}[cat]
    hole_ranks = {hole[0][0], hole[1][0]}
    if cat == 0:
        base += tb[0] / 14 * 0.15
    elif cat == 1:
        base = (0.5 + tb[0] / 14 * 0.1) if tb[0] in hole_ranks else 0.25
    return base


POKER_RAKE = 0.10      # il banco trattiene il 10% di quanto il bot ha messo nel piatto (se vinci)


def poker_win_payout(game: dict) -> int:
    return game["pot_p"] + int(game["pot_b"] * (1 - POKER_RAKE))


def _bot_calls_raise(s: float, street: int = 0) -> bool:
    """Il bot vede il rilancio del giocatore. Lascia solo con mani davvero deboli e di rado
    (il rilancio e' sempre una puntata fissa: contro un piatto gia' grosso lasciare e' raramente giusto)."""
    if s < 0.25:
        fold = 0.12 + 0.02 * street
    elif s < 0.40:
        fold = 0.04
    else:
        fold = 0.0
    if street == 0:
        fold *= 0.5                           # pre-flop quasi mai
    return random.random() >= fold


def _bot_bets(s: float) -> bool:
    if s >= 0.62:
        return random.random() < 0.90
    if s < 0.35:
        return random.random() < 0.33      # bluff: non si deve poter leggere "il bot punta = ha una mano forte"
    return random.random() < 0.50


def _poker_text(game: dict, user, coins: int, reveal: bool = False, footer: str = "") -> str:
    pot = game["pot_p"] + game["pot_b"]
    return (
        game_header("🃏 <b>SDROGO POKER</b> 🃏", user.first_name, coins, game["bet"])
        + f"🎴 <b>{POKER_STREETS[game['street']]}</b>\n"
        f"🂡 Tavolo: {_cards_str(game['board'])}\n"
        f"🧑 Le tue carte: {_cards_str(game['hole_p'])}\n"
        f"🤖 Bot: {_cards_str(game['hole_b']) if reveal else '🂠 🂠'}\n"
        f"💰 Piatto: <b>{fmt(pot)} $SDG</b> (tu hai messo {fmt(game['pot_p'])})\n"
        + (f"\n{footer}" if footer else "")
    )


def _poker_kb(game: dict, owner_id: int, coins: int = 0) -> InlineKeyboardMarkup:
    sq = game.get("seq", 0)          # numero di stato: un tasto di uno stato vecchio (doppio tocco) viene ignorato
    """Se il saldo non basta per vedere/rilanciare compare il tasto ALL-IN (invece di costringere a lasciare)."""
    b = game["bet"]
    if game["phase"] == "respond":
        if coins >= b:
            return InlineKeyboardMarkup([[
                InlineKeyboardButton(f"🃏 Vedi (+{b})", callback_data=f"sp_pkact_check_{sq}_{owner_id}"),
                InlineKeyboardButton("🏳️ Lascia", callback_data=f"sp_pkact_fold_{sq}_{owner_id}"),
            ]])
        return InlineKeyboardMarkup([
            [InlineKeyboardButton(f"🔥 ALL-IN ({fmt(coins)} $SDG)", callback_data=f"sp_pkact_allin_{sq}_{owner_id}")],
            [InlineKeyboardButton("🏳️ Lascia", callback_data=f"sp_pkact_fold_{sq}_{owner_id}")],
        ])
    if coins >= b:
        raise_btn = InlineKeyboardButton(f"💰 Rilancia (+{b})", callback_data=f"sp_pkact_raise_{sq}_{owner_id}")
    elif coins > 0:
        raise_btn = InlineKeyboardButton(f"🔥 ALL-IN ({fmt(coins)} $SDG)", callback_data=f"sp_pkact_allin_{sq}_{owner_id}")
    else:
        raise_btn = None
    top = [InlineKeyboardButton("🃏 Parola", callback_data=f"sp_pkact_check_{sq}_{owner_id}")]
    if raise_btn:
        top.append(raise_btn)
    return InlineKeyboardMarkup([top, [InlineKeyboardButton("🏳️ Lascia", callback_data=f"sp_pkact_fold_{sq}_{owner_id}")]])


def _poker_end_kb(bet: int, owner_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🔂 Nuova mano ({bet} $SDG)", callback_data=f"sp_pkbet_{bet}_{owner_id}")],
        [InlineKeyboardButton("💰 Cambia puntata", callback_data=f"sp_pk_{owner_id}")],
        _sp_back_row(owner_id),
    ])


async def _poker_end(context, query, user, chat_id: int, key: str, game: dict, result: str, owner_id: int):
    """result: 'fold' (lascia il giocatore), 'bot_fold' (lascia il bot) oppure 'showdown'."""
    POKER_GAMES.pop(key, None)
    reveal = result == "showdown"
    detail = ""
    if result == "fold":
        base = 0
        verdict = "🏳️ <b>Hai lasciato la mano.</b>"
    elif result == "bot_fold":
        base = poker_win_payout(game)
        verdict = "🏆 <b>Il bot lascia!</b> Il piatto è tuo."
    else:
        mine = _best_hand(game["hole_p"] + game["board"])
        theirs = _best_hand(game["hole_b"] + game["board"])
        detail = f"🧑 Tu: <b>{HAND_NAMES[mine[0]]}</b> | 🤖 Bot: <b>{HAND_NAMES[theirs[0]]}</b>\n"
        if mine > theirs:
            base = poker_win_payout(game)
            verdict = "🏆 <b>HAI VINTO LO SHOWDOWN!</b>"
            hq = quest_progress(chat_id, user.id, "poker:hand", mine[0])     # missioni "vinci con un Tris / Full"
            if hq:
                detail += hq + "\n"
        elif mine < theirs:
            base = 0
            verdict = "❌ <b>Il bot vince lo showdown.</b>"
        else:
            base = game["pot_p"]
            verdict = "⚖️ <b>PAREGGIO!</b> Ti restituisco quanto hai messo."
    try:
        paid, notes = await _settle(context.bot, chat_id, user.id, "poker", game["pot_p"], base)
    except Exception as e:                                   # errore imprevisto: la puntata non va persa
        logging.error(f"Poker settle: {e}")
        add_user_coins(chat_id, user.id, game["pot_p"])
        await _safe_edit(query, "⚠️ Errore nel calcolo della mano: puntata rimborsata.",
                         _poker_end_kb(game["bet"], owner_id))
        return
    net = paid - game["pot_p"]
    text = _poker_text(game, user, get_user_coins(chat_id, user.id), reveal=reveal,
                       footer=f"{detail}{verdict} <b>{_sign(net)} $SDG</b>{_notes(notes)}")
    await _safe_edit(query, text, _poker_end_kb(game["bet"], owner_id))


async def _poker_advance(query, user, chat_id: int, game: dict, owner_id: int, context, key: str, footer: str = ""):
    """Passa al giro successivo (o allo showdown dopo il river)."""
    game["street"] += 1
    if game["street"] > 3:
        game["street"] = 3
        await _poker_end(context, query, user, chat_id, key, game, "showdown", owner_id)
        return
    deal = 3 if game["street"] == 1 else 1
    game["board"] += [game["deck"].pop() for _ in range(deal)]
    game["phase"] = "act"
    game["to_call"] = 0
    await _safe_edit(query, _poker_text(game, user, get_user_coins(chat_id, user.id), footer=footer),
                     _poker_kb(game, owner_id, get_user_coins(chat_id, user.id)))


@serialized("user")
async def handle_sp_poker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    parts = query.data.split("_")        # sp_pk_OWNER | sp_pkbet_BET_OWNER | sp_pkact_ACT_OWNER
    action = parts[1]
    try:
        owner_id = int(parts[-1])
    except ValueError:
        await query.answer()
        return

    if not await verify_user_lock(query, owner_id):
        return

    user = query.from_user
    chat_id = query.message.chat_id
    key = f"{chat_id}_{user.id}"
    name = user.first_name or "Giocatore"

    if not is_v2(user.id):
        await query.answer("🃏 Sdrogo Poker non è ancora disponibile!", show_alert=True)
        return

    # Menu puntata
    if action == "pk":
        if not await _game_on(query, "poker"):
            return
        await query.answer()
        text = (
            game_header("🃏 <b>SDROGO POKER</b> 🃏", name, get_user_coins(chat_id, user.id))
            + "Texas Hold'em 1v1 contro il Bot: ogni giro puoi <b>Passare/Vedere</b>, <b>Rilanciare</b> o <b>Lasciare</b>.\n"
            "Vince la mano migliore allo showdown; i pareggi restituiscono la puntata.\n\n"
            "Scegli la puntata (è anche l'importo di ogni rilancio):"
        )
        await _safe_edit(query, text, InlineKeyboardMarkup(_sp_bet_keyboard("sp_pkbet", owner_id)))
        return

    # Nuova mano
    if action == "pkbet":
        try:
            bet = int(parts[2])
        except (IndexError, ValueError):
            await query.answer()
            return
        if bet not in SP_BETS:
            await query.answer()
            return
        if not await _game_on(query, "poker"):
            return
        if key in POKER_GAMES and time.time() - POKER_GAMES[key].get("last", 0) < GAME_IDLE_SECS:
            await query.answer("⏳ Hai già una mano in corso!", show_alert=True)
            return
        if get_user_coins(chat_id, user.id) < bet:
            await query.answer(f"❌ Servono {bet} $SDG per questa puntata!", show_alert=True)
            return
        await query.answer()
        _charge(chat_id, user.id, bet)
        deck = _new_deck()
        game = {
            "name": name, "bet": bet, "deck": deck,
            "hole_p": [deck.pop(), deck.pop()], "hole_b": [deck.pop(), deck.pop()], "board": [],
            "street": 0, "phase": "act", "to_call": 0, "seq": 0,
            "pot_p": bet, "pot_b": bet, "ts": time.time(), "last": time.time(),
        }
        POKER_GAMES[key] = game
        await _safe_edit(query, _poker_text(game, user, get_user_coins(chat_id, user.id)), _poker_kb(game, owner_id, get_user_coins(chat_id, user.id)))
        return

    # Azione in una mano in corso
    if action == "pkact":
        game = POKER_GAMES.get(key)
        if not game:
            # mano già chiusa (doppio tocco, tasto vecchio): NON si tocca il messaggio, che mostra già il risultato
            await query.answer("Questa mano è già terminata.")
            return
        act = parts[2]
        try:
            seq = int(parts[3]) if len(parts) > 4 else -1
        except ValueError:
            seq = -1
        if seq != game.get("seq", 0):
            await query.answer()                              # tasto di uno stato vecchio: ignorato in silenzio
            return
        game["seq"] = seq + 1                                 # da qui i vecchi tasti non valgono più
        game["last"] = time.time()
        bet = game["bet"]
        _undo = lambda: game.__setitem__("seq", seq)          # azione rifiutata: i tasti restano validi

        if act == "fold":
            await query.answer()
            await _poker_end(context, query, user, chat_id, key, game, "fold", owner_id)
            return

        if act == "check":
            if game["phase"] == "respond":                     # il bot ha puntato: vedi
                if get_user_coins(chat_id, user.id) < bet:
                    _undo()
                    await query.answer("❌ Saldo insufficiente per vedere: usa ALL-IN o lascia.", show_alert=True)
                    return
                await query.answer()
                _charge(chat_id, user.id, bet)
                game["pot_p"] += bet
                await _poker_advance(query, user, chat_id, game, owner_id, context, key, footer="🃏 Hai visto la puntata del bot.")
                return
            await query.answer()
            s = _bot_strength(game["hole_b"], game["board"])
            if _bot_bets(s):
                game["phase"] = "respond"
                game["to_call"] = bet
                game["pot_b"] += bet
                await _safe_edit(
                    query,
                    _poker_text(game, user, get_user_coins(chat_id, user.id), footer=f"🤖 Il bot <b>punta {fmt(bet)} $SDG</b>: vedi o lasci?"),
                    _poker_kb(game, owner_id, get_user_coins(chat_id, user.id))
                )
            else:
                await _poker_advance(query, user, chat_id, game, owner_id, context, key, footer="🤖 Il bot passa.")
            return

        if act == "allin":
            # Saldo insufficiente per il rilancio/la puntata del bot: si va All-In con quello che resta e subito allo showdown
            x = get_user_coins(chat_id, user.id)
            if game["phase"] == "respond":
                if x >= bet:
                    _undo()
                    await query.answer("Hai abbastanza saldo: usa Vedi.", show_alert=True)
                    return
            elif x >= bet or x <= 0:
                _undo()
                await query.answer()
                return
            await query.answer()
            if x > 0:
                _charge(chat_id, user.id, x)
            game["pot_p"] += x
            if game["phase"] == "respond":
                game["pot_b"] -= (bet - x)          # il bot riprende la parte che l'All-In non copre (side pot)
            else:
                s = _bot_strength(game["hole_b"], game["board"])
                if not _bot_calls_raise(s, 3):
                    await _poker_end(context, query, user, chat_id, key, game, "bot_fold", owner_id)
                    return
                game["pot_b"] += x
            while len(game["board"]) < 5:           # si scoprono tutte le carte del tavolo
                game["board"].append(game["deck"].pop())
            game["street"] = 3
            await _poker_end(context, query, user, chat_id, key, game, "showdown", owner_id)
            return

        if act == "raise":
            if game["phase"] != "act":
                _undo()
                await query.answer()
                return
            if get_user_coins(chat_id, user.id) < bet:
                _undo()
                await query.answer(f"❌ Servono {bet} $SDG per rilanciare!", show_alert=True)
                return
            await query.answer()
            _charge(chat_id, user.id, bet)
            game["pot_p"] += bet
            s = _bot_strength(game["hole_b"], game["board"])
            if _bot_calls_raise(s, game["street"]):
                game["pot_b"] += bet
                await _poker_advance(query, user, chat_id, game, owner_id, context, key,
                                     footer=f"💰 Hai rilanciato di {fmt(bet)} $SDG: il bot vede.")
            else:
                await _poker_end(context, query, user, chat_id, key, game, "bot_fold", owner_id)
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
    # Aviator, Roulette, Gratta e Vinci, Poker: menu Single Player
    app.add_handler(CallbackQueryHandler(handle_sp_roulette, pattern="^sp_roul", block=False))
    app.add_handler(CallbackQueryHandler(handle_sp_aviator, pattern="^sp_avi", block=False))
    app.add_handler(CallbackQueryHandler(handle_sp_scratch, pattern="^sp_scr"))
    app.add_handler(CallbackQueryHandler(handle_sp_poker, pattern="^sp_pk"))
    if app.job_queue:
        app.job_queue.run_repeating(expire_abandoned_games, interval=60, first=60)
        app.job_queue.run_repeating(expire_heists, interval=15, first=15)
