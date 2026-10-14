"""Economia V2: Banco Centrale, Follia Sdrogo, boost/malus, sfida di gruppo, missioni, enigmi di /lavora, ruota daily."""
import html
import logging
import os
import random
import time
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
    _TZ = ZoneInfo("Europe/Rome")
except Exception:
    _TZ = None

from config import GROUP_CHAT_ID
from state import USER_INVENTORIES, BANCO_STATE, GROUP_CHALLENGE
from storage import add_user_coins, get_user_data_dict, save_db
from utils import is_v2, smart_edit_msg

# ======================================================================
# COSTANTI
# ======================================================================
FOLLIA_ON = 7000          # il Banco raggiunge questa soglia: parte la Follia Sdrogo
FOLLIA_OFF = 4500         # la Follia finisce solo quando il Banco scende sotto questa soglia
FOLLIA_MULT = {"default": 1.5, "aviator": 2.0, "scratch": 2.0}   # moltiplicatore sulle vincite (parte di profitto)

BOOST_WIN_GAMES = 10      # Boost Vincita (x1.5): partite single player
BOOST_LUCK_GAMES = 15     # Boost Fortuna: partite single player
LUCK_GAMES = {"blackjack", "slot", "roulette", "scratch"}
SANG_PCT = 0.25           # Sanguisuga: quota rubata a ogni vincita
SANG_GAMES = 10

CHALLENGE_GOAL = 5000
CHALLENGE_DAYS = 7
CHALLENGE_REWARD = 500
CHALLENGE_NEXT_DELAY = 24 * 3600      # pausa tra una sfida e la successiva
CHALLENGE_EDIT_EVERY = 5              # secondi minimi tra due aggiornamenti del messaggio fissato

WORK_REWARD = 50
WORK_COOLDOWN = 2 * 3600
WORK_TIME = 60
WORK_MAX_BALANCE = 100    # /lavora è per chi è al verde: serve un saldo sotto questa soglia (None = nessun limite)

DAILY_WHEEL = [(25, 40), (50, 35), (75, 15), (100, 8), (500, 2)]   # (premio, peso %)

# Filtro muto di massa (oggetto V2): utenti colpiti, configurabili da variabile d'ambiente
# MUTE_FILTER_TARGETS="username1,username2" (senza @). Nessun username è scritto nel codice.
MUTE_FILTER_TARGETS = {
    u.strip().lower().lstrip("@")
    for u in os.environ.get("MUTE_FILTER_TARGETS", "").split(",") if u.strip()
}

_chal_dirty = False
_chal_last_edit = 0.0


# ======================================================================
# UTILITÀ
# ======================================================================
def fmt(n) -> str:
    return f"{int(n):,}".replace(",", ".")


def today_str() -> str:
    return str(datetime.now(_TZ).date())


def game_header(title: str, user_name: str, coins: int, bet=None) -> str:
    """Intestazione uniforme di tutti i giochi, da rigenerare a ogni turno col saldo aggiornato."""
    h = (
        f"{title}\n"
        f"👤 Player: <b>{html.escape(user_name or 'Giocatore')}</b>\n"
        f"💳 Saldo Attuale: <b>{fmt(coins)} $SDG</b>\n"
    )
    if bet is not None:
        h += f"🎯 Puntata: <b>{fmt(bet)} $SDG</b>\n"
    return h + "\n"


def main_chat_id():
    """Chat del gruppo principale (annunci, Follia, sfida di gruppo)."""
    if GROUP_CHAT_ID:
        try:
            return int(GROUP_CHAT_ID)
        except ValueError:
            return None
    return GROUP_CHALLENGE.get("chat_id")


async def _announce(bot, text: str):
    cid = main_chat_id()
    if not cid:
        return None
    try:
        return await bot.send_message(chat_id=cid, text=text, parse_mode="HTML")
    except Exception as e:
        logging.warning(f"Annuncio non inviato: {e}")
        return None


def record_single_result(chat_id: int, user_id: int, won: bool, profit: int, is_casino: bool = True):
    u = get_user_data_dict(chat_id, user_id)
    u["single_played"] = u.get("single_played", 0) + 1
    u["net_profit"] = u.get("net_profit", 0) + profit

    if won:
        u["single_won"] = u.get("single_won", 0) + 1
        if is_casino:
            u["casino_wins"] = u.get("casino_wins", 0) + 1
    save_db()


# ======================================================================
# INVENTARIO
# ======================================================================
def inv(chat_id: int, user_id: int) -> dict:
    """Inventario dell'utente con tutti i campi V2 inizializzati.
    Oggetti posseduti: titles, persecutes, silence, mutefilter, boost_win, boost_luck, sang.
    Effetti attivi: boost_win_left, boost_luck_left, sang_on_me ({"buyer", "left"})."""
    key = f"{chat_id}_{user_id}"
    d = USER_INVENTORIES.setdefault(key, {})
    for k, v in (("titles", 0), ("persecutes", 0), ("stars", 0), ("silence", 0), ("mutefilter", 0),
                 ("boost_win", 0), ("boost_luck", 0), ("sang", 0),
                 ("boost_win_left", 0), ("boost_luck_left", 0)):
        d.setdefault(k, v)
    return d


def has_luck(chat_id: int, user_id: int, game: str) -> bool:
    return game in LUCK_GAMES and inv(chat_id, user_id)["boost_luck_left"] > 0


# ======================================================================
# BANCO CENTRALE & FOLLIA SDROGO
# ======================================================================
def banco_balance() -> int:
    return int(BANCO_STATE.get("BANCO_BALANCE", 0))


def banco_add(amount: int):
    if amount > 0:
        BANCO_STATE["BANCO_BALANCE"] = banco_balance() + int(amount)
        save_db()


def banco_take(amount: int) -> int:
    """Preleva fino a `amount` dal Banco. Restituisce quanto è stato davvero prelevato."""
    taken = max(0, min(int(amount), banco_balance()))
    if taken:
        BANCO_STATE["BANCO_BALANCE"] = banco_balance() - taken
        save_db()
    return taken


def follia_active() -> bool:
    return bool(BANCO_STATE.get("FOLLIA_ATTIVA"))


async def banco_check(bot):
    """Isteresi: la Follia parte a >= 7.000 e finisce solo sotto 4.500. Annuncia nel gruppo principale."""
    bal = banco_balance()
    if not follia_active() and bal >= FOLLIA_ON:
        BANCO_STATE["FOLLIA_ATTIVA"] = True
        save_db()
        await _announce(
            bot,
            "🎉🎊🥳 <b>FOLLIA SDROGO!</b> 🥳🎊🎉\n"
            "🎈🎆✨🎇🎈🎆✨🎇🎈\n\n"
            f"🏦 Il Banco Centrale ha accumulato <b>{fmt(bal)} $SDG</b>: è FESTA!\n\n"
            "💥 Vincite dei giochi Single Player <b>×1.5</b> (<b>×2</b> su Aviator e Gratta e Vinci)!\n"
            "🏢 La Sdrogo Heist può <b>svuotare il Banco</b>!\n\n"
            f"<i>La Follia dura finché il Banco non scende sotto {fmt(FOLLIA_OFF)} $SDG.</i>\n"
            "🎰🃏🎡✈️🎟️ Giocate, giocate, giocate!"
        )
    elif follia_active() and bal < FOLLIA_OFF:
        BANCO_STATE["FOLLIA_ATTIVA"] = False
        save_db()
        await _announce(
            bot,
            "🌙 <b>La Follia Sdrogo è finita!</b>\n"
            f"Il Banco Centrale è sceso a {fmt(bal)} $SDG. "
            "Le vincite tornano normali... fino alla prossima Follia! 🎪"
        )


# ======================================================================
# SPESA / SFIDA DI GRUPPO
# ======================================================================
def note_spend(chat_id: int, user_id: int, amount: int):
    """Registra una spesa nei giochi per la sfida di gruppo (solo nella chat della sfida)."""
    global _chal_dirty
    gc = GROUP_CHALLENGE
    if not gc.get("active") or str(chat_id) != str(gc.get("chat_id")):
        return
    if time.time() > gc.get("end", 0) or amount <= 0:
        return
    gc["spent"] = int(gc.get("spent", 0)) + int(amount)
    p = gc.setdefault("participants", {})
    p[str(user_id)] = int(p.get(str(user_id), 0)) + int(amount)
    _chal_dirty = True
    save_db()


def charge(chat_id: int, user_id: int, amount: int):
    """Scala una puntata dal saldo e la conta nella sfida di gruppo."""
    add_user_coins(chat_id, user_id, -amount)
    note_spend(chat_id, user_id, amount)


def _bar(spent: int, goal: int) -> str:
    filled = max(0, min(10, int(spent * 10 / goal)))
    return "█" * filled + "░" * (10 - filled)


def challenge_text(footer: str = "") -> str:
    gc = GROUP_CHALLENGE
    goal = max(1, int(gc.get("goal", CHALLENGE_GOAL)))
    spent = int(gc.get("spent", 0))
    pct = min(100, int(spent * 100 / goal))
    left = max(0, int(gc.get("end", 0) - time.time()))
    days, rem = divmod(left, 86400)
    hours = rem // 3600
    return (
        "🎯 <b>SFIDA DI GRUPPO</b> 🎯\n\n"
        f"Spendete <b>{fmt(goal)} $SDG</b> nei giochi entro 7 giorni!\n\n"
        f"[{_bar(spent, goal)}] <b>{fmt(spent)}/{fmt(goal)}</b> ({pct}%)\n"
        f"👥 Partecipanti: <b>{len(gc.get('participants', {}))}</b>\n"
        f"⏳ Tempo rimasto: <b>{days}g {hours}h</b>\n\n"
        f"🎁 Premio: <b>{CHALLENGE_REWARD} $SDG + 1 Boost Fortuna</b> a ogni partecipante!"
        + (f"\n\n{footer}" if footer else "")
    )


async def challenge_start(bot, chat_id=None):
    """Avvia (o resetta) la sfida di gruppo: invia, fissa e salva il messaggio. Restituisce (ok, nota)."""
    global _chal_dirty
    gc = GROUP_CHALLENGE
    cid = chat_id or main_chat_id()
    if not cid:
        return False, "GROUP_CHAT_ID non configurato."

    # reset: toglie dal pin il vecchio messaggio
    if gc.get("msg_id") and gc.get("chat_id"):
        try:
            await bot.unpin_chat_message(chat_id=gc["chat_id"], message_id=gc["msg_id"])
        except Exception:
            pass

    now = time.time()
    gc.update({
        "active": True, "goal": CHALLENGE_GOAL, "start": now, "end": now + CHALLENGE_DAYS * 86400,
        "spent": 0, "participants": {}, "chat_id": int(cid), "msg_id": None, "next_start": 0.0,
    })
    note = "Sfida avviata."
    try:
        msg = await bot.send_message(chat_id=int(cid), text=challenge_text(), parse_mode="HTML")
        gc["msg_id"] = msg.message_id
        try:
            await bot.pin_chat_message(chat_id=int(cid), message_id=msg.message_id, disable_notification=False)
        except Exception as e:
            note = f"Sfida avviata, ma non sono riuscito a fissare il messaggio ({e}). Dammi il permesso di fissare messaggi."
    except Exception as e:
        gc["active"] = False
        save_db()
        return False, f"Impossibile inviare il messaggio nel gruppo: {e}"
    _chal_dirty = False
    save_db()
    return True, note


async def _challenge_finish(bot, success: bool):
    gc = GROUP_CHALLENGE
    cid, mid = gc.get("chat_id"), gc.get("msg_id")
    gc["active"] = False
    gc["next_start"] = time.time() + CHALLENGE_NEXT_DELAY
    participants = dict(gc.get("participants", {}))
    spent = int(gc.get("spent", 0))

    if success:
        for uid in participants:
            try:
                add_user_coins(cid, int(uid), CHALLENGE_REWARD)
                inv(cid, int(uid))["boost_luck"] += 1
            except Exception:
                pass
        final = (
            "🏆🎉 <b>SFIDA DI GRUPPO COMPLETATA!</b> 🎉🏆\n\n"
            f"Il gruppo ha speso <b>{fmt(spent)} $SDG</b>!\n"
            f"🎁 Ogni partecipante ({len(participants)}) riceve <b>{CHALLENGE_REWARD} $SDG + 1 Boost Fortuna</b> "
            "(lo trovi nell'Inventario 🎒).\n\n"
            "⏭️ La prossima sfida partirà tra 24 ore!"
        )
    else:
        final = (
            "⌛ <b>SFIDA DI GRUPPO SCADUTA</b>\n\n"
            f"Il gruppo ha speso <b>{fmt(spent)}/{fmt(gc.get('goal', CHALLENGE_GOAL))} $SDG</b>: obiettivo mancato. "
            "Niente premi, ma la prossima sfida parte tra 24 ore!"
        )

    save_db()
    if cid:
        if mid:
            await smart_edit_msg(bot, cid, mid, challenge_text(), None)
            try:
                await bot.unpin_chat_message(chat_id=cid, message_id=mid)
            except Exception:
                pass
        try:
            await bot.send_message(chat_id=cid, text=final, parse_mode="HTML")
        except Exception as e:
            logging.warning(f"Annuncio fine sfida non inviato: {e}")


async def challenge_tick(context):
    """Job ogni ~10 secondi: aggiorna la barra sul messaggio fissato (con limite di frequenza),
    chiude la sfida a obiettivo raggiunto o a scadenza, avvia quella programmata."""
    global _chal_dirty, _chal_last_edit
    bot = context.bot
    gc = GROUP_CHALLENGE
    now = time.time()

    if gc.get("active"):
        if int(gc.get("spent", 0)) >= int(gc.get("goal", CHALLENGE_GOAL)):
            await _challenge_finish(bot, True)
        elif now >= gc.get("end", 0):
            await _challenge_finish(bot, False)
        elif _chal_dirty and now - _chal_last_edit >= CHALLENGE_EDIT_EVERY and gc.get("msg_id"):
            _chal_dirty = False
            _chal_last_edit = now
            await smart_edit_msg(bot, gc["chat_id"], gc["msg_id"], challenge_text(), None)
    elif gc.get("next_start") and now >= gc["next_start"]:
        await challenge_start(bot)


# ======================================================================
# BOOST, MALUS, FOLLIA: liquidazione di una partita single player
# ======================================================================
async def settle_single(bot, chat_id: int, user_id: int, game: str, bet: int, base_payout: int,
                        is_casino: bool = True):
    """Liquida una partita single player (la puntata è già stata scalata con charge()).
    base_payout = totale restituito prima dei modificatori (0 se perdita).
    Applica Boost Vincita, Follia Sdrogo, Sanguisuga; alimenta il Banco; registra le statistiche.
    Restituisce (importo accreditato al giocatore, lista di note da mostrare)."""
    state = inv(chat_id, user_id)
    notes = []
    base_payout = int(base_payout)
    payout = base_payout
    profit = payout - bet

    if profit > 0:
        if state["boost_win_left"] > 0:
            bonus = int(profit * 0.5)
            if bonus:
                payout += bonus
                notes.append(f"🚀 Boost Vincita ×1.5: +{fmt(bonus)}")
        if follia_active():
            fm = FOLLIA_MULT.get(game, FOLLIA_MULT["default"])
            got = banco_take(int(profit * (fm - 1)))
            if got:
                payout += got
                notes.append(f"🎪 Follia Sdrogo ×{fm}: +{fmt(got)} dal Banco")

    # Sanguisuga: il 25% della vincita va a chi ha lanciato il malus (conta ogni partita, vinta o persa)
    steal = 0
    sang = state.get("sang_on_me")
    if sang and sang.get("left", 0) > 0:
        net = payout - bet
        if net > 0:
            steal = int(net * SANG_PCT)
            if steal:
                add_user_coins(chat_id, sang["buyer"], steal)
                notes.append(f"🧛 Sanguisuga: -{fmt(steal)} rubati")
        sang["left"] -= 1
        if sang["left"] <= 0:
            state.pop("sang_on_me", None)
            notes.append("🧛 La Sanguisuga su di te è terminata.")

    # i boost si consumano a ogni partita
    if state["boost_win_left"] > 0:
        state["boost_win_left"] -= 1
        if state["boost_win_left"] == 0:
            notes.append("🚀 Boost Vincita esaurito.")
    if game in LUCK_GAMES and state["boost_luck_left"] > 0:
        state["boost_luck_left"] -= 1
        if state["boost_luck_left"] == 0:
            notes.append("🍀 Boost Fortuna esaurito.")

    # il Banco incassa le perdite nette dei giocatori
    if base_payout < bet:
        banco_add(bet - base_payout)

    user_payout = payout - steal
    if user_payout > 0:
        add_user_coins(chat_id, user_id, user_payout)
    record_single_result(chat_id, user_id, user_payout > bet, user_payout - bet, is_casino=is_casino)
    save_db()
    await banco_check(bot)
    return user_payout, notes


# ======================================================================
# MISSIONI GIORNALIERE (2 al giorno per giocatore, solo V2)
# ======================================================================
QUEST_POOL = [
    {"id": "mm_win", "text": "Vinci 2 partite a Mastermind", "target": 2, "reward": 40},
    {"id": "quiz_win", "text": "Indovina 3 Quiz", "target": 3, "reward": 40},
    {"id": "wordle_win", "text": "Vinci 1 partita a Wordle", "target": 1, "reward": 35},
    {"id": "bj_win", "text": "Vinci 2 mani a Blackjack", "target": 2, "reward": 40},
    {"id": "slot_play", "text": "Gira la Slot 5 volte", "target": 5, "reward": 30},
    {"id": "scratch_play", "text": "Gratta 2 biglietti", "target": 2, "reward": 30},
    {"id": "roulette_play", "text": "Gioca 3 volte a Sdrogo Roulette", "target": 3, "reward": 30},
    {"id": "duel_win", "text": "Vinci 1 duello 1v1", "target": 1, "reward": 45},
]


def quests_for(chat_id: int, user_id: int) -> list:
    u = get_user_data_dict(chat_id, user_id)
    q = u.get("quests")
    if not q or q.get("date") != today_str():
        picked = random.sample(QUEST_POOL, 2)
        q = {"date": today_str(),
             "list": [{**p, "progress": 0, "done": False} for p in picked]}
        u["quests"] = q
        save_db()
    return q["list"]


def quest_progress(chat_id: int, user_id: int, event: str, n: int = 1) -> str:
    """Avanza le missioni dell'evento. Se una si completa, accredita il premio e restituisce il messaggio
    da mostrare ('' altrimenti). Non fa nulla per chi non ha la V2."""
    if not is_v2(user_id):
        return ""
    msgs = []
    for qst in quests_for(chat_id, user_id):
        if qst["id"] == event and not qst["done"]:
            qst["progress"] += n
            if qst["progress"] >= qst["target"]:
                qst["done"] = True
                add_user_coins(chat_id, user_id, qst["reward"])
                msgs.append(f"🎯 Missione completata: {qst['text']}! +{qst['reward']} $SDG")
    if msgs:
        save_db()
    return "\n".join(msgs)


def quests_text(chat_id: int, user_id: int) -> str:
    lines = ["🎯 <b>MISSIONI GIORNALIERE</b>\n"]
    for qst in quests_for(chat_id, user_id):
        icon = "✅" if qst["done"] else "🔸"
        prog = min(qst["progress"], qst["target"])
        lines.append(f"{icon} {qst['text']} — <b>{prog}/{qst['target']}</b> · 🎁 {qst['reward']} $SDG")
    lines.append("\n<i>Le missioni si rinnovano ogni giorno a mezzanotte.</i>")
    return "\n".join(lines)


# ======================================================================
# RUOTA DAILY
# ======================================================================
def spin_daily() -> int:
    prizes = [p for p, _ in DAILY_WHEEL]
    weights = [w for _, w in DAILY_WHEEL]
    return random.choices(prizes, weights=weights, k=1)[0]


def wheel_prizes_text() -> str:
    return " | ".join(f"{p} ({w}%)" for p, w in DAILY_WHEEL)


# ======================================================================
# /lavora: enigmi procedurali
# ======================================================================
WIRE_COLORS = [("🔴", "rosso"), ("🔵", "blu"), ("🟡", "giallo"), ("🟢", "verde"), ("🟣", "viola")]
ARROWS = ["⬆️", "➡️", "⬇️", "⬅️"]


def _num_options(correct: int, spread: int = 9):
    opts = {correct}
    while len(opts) < 4:
        cand = correct + random.choice([-1, 1]) * random.randint(1, spread)
        if cand != correct:
            opts.add(cand)
    opts = list(opts)
    random.shuffle(opts)
    return [str(o) for o in opts], opts.index(correct)


def _puzzle_math():
    a, b, c = random.randint(3, 15), random.randint(2, 9), random.randint(2, 20)
    kind = random.choice(["a*b+c", "a+b*c", "(a+b)*c", "a*b-c"])
    if kind == "a*b+c":
        text, ans = f"{a} × {b} + {c}", a * b + c
    elif kind == "a+b*c":
        text, ans = f"{a} + {b} × {c}", a + b * c
    elif kind == "(a+b)*c":
        c = random.randint(2, 6)
        text, ans = f"({a} + {b}) × {c}", (a + b) * c
    else:
        text, ans = f"{a} × {b} − {c}", a * b - c
    options, idx = _num_options(ans, 12)
    return {"text": f"🧮 <b>CALCOLO RAPIDO</b>\n\nQuanto fa <b>{text}</b>?", "options": options, "ans": idx}


def _puzzle_wires():
    seq = [random.choice(WIRE_COLORS) for _ in range(6)]
    names = [n for _, n in seq]
    rule = random.choice(["after_first", "most", "second_last_not"])
    if rule == "most":
        counts = {n: names.count(n) for n in set(names)}
        top = max(counts.values())
        if list(counts.values()).count(top) != 1:
            rule = "after_first"
    if rule == "after_first":
        candidates = [n for n in dict.fromkeys(names[:-1])]
        x = random.choice(candidates)
        answer = seq[names.index(x) + 1]
        rule_text = f"Taglia il filo che viene <b>subito dopo il primo filo {x}</b>."
    elif rule == "most":
        top_name = max(counts, key=counts.get)
        answer = next(c for c in seq if c[1] == top_name)
        rule_text = "Taglia il filo del colore che compare <b>più volte</b>."
    else:
        x = random.choice(names)
        others = [c for c in seq if c[1] != x]
        if len(others) < 2:
            return _puzzle_wires()
        answer = others[-2]
        rule_text = f"Taglia il <b>penultimo filo che NON è {x}</b>."
    wires = " ".join(f"{i + 1}{e}" for i, (e, _) in enumerate(seq))
    options = [f"{e} {n}" for e, n in WIRE_COLORS]
    return {
        "text": f"🔌 <b>CENTRALINO</b>\n\nFili: {wires}\n\n{rule_text}",
        "options": options,
        "ans": options.index(f"{answer[0]} {answer[1]}"),
    }


def _puzzle_arrows():
    start = random.randrange(4)
    cur = start
    steps = []
    for _ in range(random.randint(3, 5)):
        right = random.random() < 0.5
        steps.append("a destra ↪️" if right else "a sinistra ↩️")
        cur = (cur + (1 if right else -1)) % 4
    text = (
        f"🧭 <b>FRECCE</b>\n\nParti da {ARROWS[start]} e ruota di 90° ogni volta:\n"
        + "\n".join(f"{i + 1}. {s}" for i, s in enumerate(steps))
        + "\n\nIn che direzione punti alla fine?"
    )
    return {"text": text, "options": list(ARROWS), "ans": cur}


def _puzzle_sequence():
    kind = random.choice(["arith", "geom", "alt"])
    if kind == "arith":
        a, d = random.randint(1, 20), random.randint(2, 9)
        seq = [a + d * i for i in range(5)]
    elif kind == "geom":
        a, r = random.randint(1, 4), random.randint(2, 3)
        seq = [a * (r ** i) for i in range(5)]
    else:
        a, d1, d2 = random.randint(1, 15), random.randint(2, 6), random.randint(7, 12)
        seq = [a]
        for i in range(4):
            seq.append(seq[-1] + (d1 if i % 2 == 0 else d2))
    shown, ans = seq[:4], seq[4]
    options, idx = _num_options(ans, 8)
    return {
        "text": f"🔢 <b>SEQUENZA</b>\n\nQual è il numero successivo?\n<b>{', '.join(map(str, shown))}, ?</b>",
        "options": options, "ans": idx,
    }


def make_puzzle() -> dict:
    """Genera al volo un enigma diverso ogni volta: {'text', 'options', 'ans'}."""
    return random.choice([_puzzle_math, _puzzle_wires, _puzzle_arrows, _puzzle_sequence])()


def work_cooldown_left(chat_id: int, user_id: int) -> int:
    last = float(get_user_data_dict(chat_id, user_id).get("last_work", 0))
    return max(0, int(last + WORK_COOLDOWN - time.time()))


def work_set_cooldown(chat_id: int, user_id: int):
    get_user_data_dict(chat_id, user_id)["last_work"] = time.time()
    save_db()
