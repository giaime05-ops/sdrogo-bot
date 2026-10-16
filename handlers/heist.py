"""SDROGO HEIST V3 - escape room a difficoltà crescente (3-5 minuti di skill, reazione e logica).

Livelli:
  1  Centralino  - memoria: sequenza di colori da ripetere (3 round, sempre più lunga)
  2  Guardie     - precisione/reazione: passa SOLO quando tutte le guardie dormono (giudizio lato server
                   con compensazione del lag)
  3  Hacking     - logica/matematica: raggiungi il codice con mosse limitate (puzzle verificato con BFS)
  4  Caveau      - mastermind numerico a 4 cifre sotto pressione (tentativi e tempo limitati)
  5  Fuga        - scelta tattica: meteo, polizia, traffico e informatore (2 tappe)

Regole: timer unico (HEIST_TIME), massimo 3 allarmi, ogni allarme toglie ALARM_PENALTY secondi.
Premio: Banco < BANCO_PROTECT -> PRIZE_FIXED fissi (il Banco non viene toccato), altrimenti il 20% del Banco
(50% durante la Follia Sdrogo).

Le sessioni stanno in HEIST_GAMES (stesso dizionario usato da hub.py) e hanno i flag "v2" e "v3".
Callback: prefisso "hst_" (la vecchia Heist di games.py usa "heist_" e non interferisce).
"""
import asyncio
import html
import logging
import random
import time

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler, ContextTypes

from economy import (
    fmt, inv, banco_balance, banco_take, banco_check, follia_active, record_single_result, quest_progress,
)
from handlers.games import HEIST_COST
from state import HEIST_GAMES
from storage import get_user_coins, add_user_coins, save_db
from utils import serialized, smart_edit_msg

# ----------------------------------------------------------------------------------------------
# PARAMETRI
# ----------------------------------------------------------------------------------------------
HEIST_TIME = 300            # timer unico (300 secondi = 5 minuti): parte quando premi INIZIA
ALARM_PENALTY = 20          # secondi tolti dal timer a ogni allarme
MAX_ALARMS = 3              # al terzo allarme la rapina fallisce

BANCO_PROTECT = 1000        # sotto questa soglia il Banco non viene toccato
PRIZE_FIXED = 200           # premio fisso se il Banco è sotto soglia
SHARE = 0.20                # quota del Banco in premio
SHARE_FOLLIA = 0.50         # durante la Follia Sdrogo

SEQ_ROUNDS = (4, 5, 6)      # Livello 1: lunghezza della sequenza nei 3 round
SEQ_SYMBOLS = ["🔴", "🔵", "🟡", "🟢", "🟣", "🟠"]

GUARD_ROUNDS = [(2, 1.6, 10), (3, 1.4, 12), (4, 1.2, 14)]   # (guardie, secondi per fotogramma, fotogrammi)
GUARD_LAG = 0.7             # tolleranza (secondi) per lag di rete/reazione dopo la fine del fotogramma buono

HACK_MOVES = 5              # mosse a disposizione per ogni puzzle del Livello 3
HACK_ROUNDS = (3, 4)        # mosse minime necessarie (garantite dal BFS) nei 2 puzzle

VAULT_TIME = 150            # secondi per ogni caveau
VAULT_TRIES = 9             # tentativi per ogni caveau

FLIGHT_ROUNDS = 2           # scenari di fuga da risolvere

HEIST_NOTE = "\n\n<i>I premi sono nel saldo del gruppo: riapri /sdrogocomm lì per vederli.</i>"

_TASKS = set()


def _spawn(coro):
    t = asyncio.create_task(coro)
    _TASKS.add(t)
    t.add_done_callback(_TASKS.discard)
    return t


# ----------------------------------------------------------------------------------------------
# INTERFACCIA CON hub.py
# ----------------------------------------------------------------------------------------------
def heist_new_game(chat_id: int) -> dict:
    """Sessione Heist V3: la crea hub.py all'acquisto del pass."""
    return {
        "v2": True, "v3": True, "chat_id": chat_id, "level": 0, "running": False,
        "deadline": 0.0, "ts": time.time(), "alarms": 0, "tok": 0, "phase": "intro",
        "name": "", "chat": None, "mid": None, "st": {}, "t_start": 0.0,
    }


def heist_intro_text() -> str:
    return (
        "🏢 <b>SDROGO HEIST - ESCAPE ROOM</b> 🕵️‍♂️\n\n"
        f"Hai <b>{HEIST_TIME // 60} minuti</b> (timer unico) per superare 5 livelli, sempre più difficili:\n"
        "1️⃣ Centralino: memorizza le sequenze\n"
        "2️⃣ Guardie: passa solo quando dormono TUTTE (riflessi!)\n"
        "3️⃣ Hacking: raggiungi il codice con mosse contate\n"
        "4️⃣ Caveau: indovina il codice a 4 cifre\n"
        "5️⃣ Fuga: scegli il mezzo giusto leggendo i rapporti\n\n"
        f"🚨 Hai <b>{MAX_ALARMS - 1} errori</b> di margine: ogni allarme toglie <b>{ALARM_PENALTY}s</b> "
        f"e al {MAX_ALARMS}° la rapina fallisce.\n"
        f"🎁 Premio: il <b>{int(SHARE * 100)}%</b> del Banco Centrale "
        f"(<b>{int(SHARE_FOLLIA * 100)}%</b> durante la Follia Sdrogo). "
        f"Se il Banco è sotto <b>{fmt(BANCO_PROTECT)} $SDG</b> il premio è fisso: <b>{PRIZE_FIXED} $SDG</b>.\n"
        "⏱️ Il timer parte quando premi INIZIA."
    )


def heist_start_kb(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔓 INIZIA LA RAPINA (parte il timer)", callback_data=f"hst_go_{user_id}")]])


# ----------------------------------------------------------------------------------------------
# UTILITÀ
# ----------------------------------------------------------------------------------------------
def _left(game: dict) -> int:
    return max(0, int(game["deadline"] - time.time()))


def _hdr(game: dict, title: str) -> str:
    uid = game["uid"]
    return (
        f"🏢 <b>SDROGO HEIST</b> — Livello {game['level']}/5 · {title}\n"
        f"👤 <b>{html.escape(game['name'] or 'Giocatore')}</b> · "
        f"💳 {fmt(get_user_coins(game['chat_id'], uid))} $SDG · 🎯 Puntata {fmt(HEIST_COST)}\n"
        f"⏱️ Tempo: <b>{_left(game)}s</b> · 🚨 Allarmi: <b>{game['alarms']}/{MAX_ALARMS}</b>\n\n"
    )


async def _edit(bot, game: dict, text: str, kb=None) -> bool:
    if game.get("mid") is None:
        return False
    return await smart_edit_msg(bot, game["chat"], game["mid"], text, kb)


def _alive(uid: int, game: dict, tok: int) -> bool:
    return HEIST_GAMES.get(uid) is game and game["running"] and game["tok"] == tok


def _kb(rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(rows)


# ----------------------------------------------------------------------------------------------
# FINE PARTITA
# ----------------------------------------------------------------------------------------------
async def _fail(bot, uid: int, game: dict, reason: str):
    game["tok"] += 1
    game["running"] = False
    if HEIST_GAMES.get(uid) is game:
        HEIST_GAMES.pop(uid, None)
        record_single_result(game["chat_id"], uid, False, -HEIST_COST)
    await _edit(bot, game, f"{reason}\n\n💸 Rapina fallita: hai perso il pass da {fmt(HEIST_COST)} $SDG.{HEIST_NOTE}")


def _prize_amount() -> tuple:
    """(premio, quota_prelevata_dal_banco)."""
    bal = banco_balance()
    if bal < BANCO_PROTECT:
        return PRIZE_FIXED, 0                       # Banco protetto: premio fisso, nessun prelievo
    share = SHARE_FOLLIA if follia_active() else SHARE
    taken = banco_take(int(bal * share))
    return taken, taken


async def _win(bot, uid: int, game: dict, qmsg: str = ""):
    game["tok"] += 1
    game["running"] = False
    if HEIST_GAMES.get(uid) is not game:
        return
    HEIST_GAMES.pop(uid, None)
    chat_id = game["chat_id"]
    prize, _ = _prize_amount()
    elapsed = int(time.time() - game["t_start"])
    add_user_coins(chat_id, uid, prize)
    record_single_result(chat_id, uid, True, prize - HEIST_COST)
    d = inv(chat_id, uid)
    d["stars"] = d.get("stars", 0) + 1
    d["titles"] = d.get("titles", 0) + 1
    d["persecutes"] = d.get("persecutes", 0) + 1
    save_db()
    await _edit(
        bot, game,
        "🏆 <b>RAPINA PERFETTA COMPLETATA!</b>\n"
        f"⏱️ Tempo impiegato: <b>{elapsed // 60}m {elapsed % 60:02d}s</b> · 🚨 Allarmi: {game['alarms']}/{MAX_ALARMS}\n"
        f"💰 Premio: <b>+{fmt(prize)} $SDG</b>, 1 Titolo, 1 Persecuzione e 1 STELLA ⭐!"
        + (f"\n{qmsg}" if qmsg else "") + HEIST_NOTE
    )
    try:
        await bot.send_message(
            chat_id=chat_id, parse_mode="HTML",
            text=(
                f"👑 <b>COLPO DEL SECOLO!</b> 🏢\n\n"
                f"<b>{html.escape(game['name'] or 'Un giocatore')}</b> ha superato l'escape room di Sdrogo Heist "
                f"in {elapsed // 60}m {elapsed % 60:02d}s!\n"
                f"Guadagna <b>💳 {fmt(prize)} $SDG</b> e 1 STELLA ⭐ di prestigio!"
            ),
        )
    except Exception:
        pass
    try:
        await banco_check(bot)
    except Exception as e:
        logging.error(f"Heist banco_check: {e}")


async def _alarm(bot, uid: int, game: dict, reason: str) -> bool:
    """Registra un allarme. Ritorna False se la rapina è finita (fallita)."""
    game["alarms"] += 1
    game["deadline"] -= ALARM_PENALTY
    if game["alarms"] >= MAX_ALARMS:
        await _fail(bot, uid, game, f"{reason}\n🚨 <b>Terzo allarme!</b> La polizia circonda l'edificio.")
        return False
    if _left(game) <= 0:
        await _fail(bot, uid, game, f"{reason}\n⏰ <b>TEMPO SCADUTO!</b> La polizia è arrivata.")
        return False
    return True


async def _level_done(bot, uid: int, game: dict):
    game["tok"] += 1
    done = game["level"]
    qmsg = quest_progress(game["chat_id"], uid, "heist:level", done)      # missione "Supera il Livello 3"
    if done >= 5:
        await _win(bot, uid, game, qmsg)
        return
    game["level"] += 1
    await _present(bot, uid, game, f"✅ <b>Livello {done} superato!</b>\n" + (f"{qmsg}\n" if qmsg else "") + "\n")


async def expire_heists_v3(context: ContextTypes.DEFAULT_TYPE):
    """Chiude le rapine con il timer scaduto anche se il giocatore non preme più nulla."""
    now = time.time()
    for uid, g in list(HEIST_GAMES.items()):
        if g.get("v3") and g.get("running") and now > g["deadline"] + 1:
            await _fail(context.bot, uid, g, "⏰ <b>TEMPO SCADUTO!</b> La polizia è arrivata.")


# ----------------------------------------------------------------------------------------------
# PRESENTAZIONE LIVELLI
# ----------------------------------------------------------------------------------------------
async def _present(bot, uid: int, game: dict, note: str = ""):
    lvl = game["level"]
    game["st"] = {}
    game["phase"] = "brief"
    if lvl == 1:
        game["st"] = {"r": 0}
        await _edit(bot, game, _hdr(game, "Centralino") + note + (
            "🎛️ <b>CENTRALINO</b>\n"
            "Ti mostro una sequenza di colori per pochi secondi. Poi sparisce e devi ripeterla "
            "<b>nello stesso ordine</b>.\n"
            f"3 round: {', '.join(str(n) for n in SEQ_ROUNDS)} colori. Un errore = allarme e la sequenza viene ripetuta."
        ), _kb([[InlineKeyboardButton("▶️ MOSTRAMI LA SEQUENZA", callback_data=f"hst_rd_{uid}")]]))
    elif lvl == 2:
        game["st"] = {"r": 0}
        await _edit(bot, game, _hdr(game, "Guardie") + note + (
            "💂 <b>GUARDIE</b>\n"
            "Sul monitor vedi lo stato di ogni guardia: 😴 dorme, 👀 è sveglia.\n"
            "Premi <b>PASSA</b> nel momento in cui <b>TUTTE</b> dormono. Se premi troppo presto, troppo tardi "
            "o mentre una guardia è sveglia, scatta l'allarme.\n"
            f"3 telecamere, ogni volta più guardie e più veloci. Hai meno di un secondo di margine."
        ), _kb([[InlineKeyboardButton("▶️ ACCENDI IL MONITOR", callback_data=f"hst_rd_{uid}")]]))
    elif lvl == 3:
        game["st"] = {"r": 0}
        await _hack_new(bot, uid, game, note)
    elif lvl == 4:
        await _vault_new(bot, uid, game, note)
    else:
        game["st"] = {"r": 0}
        await _flight_new(bot, uid, game, note)


# ============================================================================================
# LIVELLO 1 - CENTRALINO (memoria)
# ============================================================================================
def _seq_make(n: int) -> list:
    return [random.choice(SEQ_SYMBOLS) for _ in range(n)]


def _seq_input_text(game: dict) -> str:
    st = game["st"]
    shown = "".join(st["seq"][:st["pos"]]) + "▫️" * (len(st["seq"]) - st["pos"])
    return (
        _hdr(game, "Centralino") +
        f"🎛️ <b>Round {st['r'] + 1}/{len(SEQ_ROUNDS)}</b> — ripeti la sequenza!\n\n{shown}"
    )


def _seq_kb(uid: int) -> InlineKeyboardMarkup:
    rows, row = [], []
    for i, s in enumerate(SEQ_SYMBOLS):
        row.append(InlineKeyboardButton(s, callback_data=f"hst_sq_{i}_{uid}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    return InlineKeyboardMarkup(rows)


async def _seq_show(bot, uid: int, game: dict, tok: int):
    st = game["st"]
    game["phase"] = "show"
    n = len(st["seq"])
    secs = 0.8 * n + 0.8
    await _edit(bot, game, _hdr(game, "Centralino") + (
        f"🎛️ <b>Round {st['r'] + 1}/{len(SEQ_ROUNDS)}</b> — memorizza!\n\n{' '.join(st['seq'])}\n\n"
        f"<i>Sparisce tra {secs:.0f} secondi...</i>"
    ))
    await asyncio.sleep(secs)
    if not _alive(uid, game, tok):
        return
    st["pos"] = 0
    game["phase"] = "input"
    await _edit(bot, game, _seq_input_text(game), _seq_kb(uid))


def _seq_begin(bot, uid: int, game: dict, new_seq: bool):
    st = game["st"]
    if new_seq or "seq" not in st:
        st["seq"] = _seq_make(SEQ_ROUNDS[st["r"]])
    game["tok"] += 1
    _spawn(_seq_show(bot, uid, game, game["tok"]))


async def _seq_tap(bot, uid: int, game: dict, idx: int):
    st = game["st"]
    if idx < 0 or idx >= len(SEQ_SYMBOLS):
        return
    if SEQ_SYMBOLS[idx] == st["seq"][st["pos"]]:
        st["pos"] += 1
        if st["pos"] >= len(st["seq"]):
            st["r"] += 1
            if st["r"] >= len(SEQ_ROUNDS):
                await _level_done(bot, uid, game)
                return
            game["phase"] = "pause"
            await _edit(bot, game, _hdr(game, "Centralino") + "✅ <b>Sequenza corretta!</b> Prossima, più lunga...")
            await asyncio.sleep(1.2)
            if HEIST_GAMES.get(uid) is game and game["running"] and game["phase"] == "pause":
                _seq_begin(bot, uid, game, True)
            return
        await _edit(bot, game, _seq_input_text(game), _seq_kb(uid))
        return
    # errore
    game["phase"] = "pause"
    game["tok"] += 1
    if not await _alarm(bot, uid, game, "💥 <b>Colore sbagliato!</b>"):
        return
    await _edit(bot, game, _hdr(game, "Centralino") + "🚨 <b>ALLARME!</b> Ti rimostro la stessa sequenza...")
    await asyncio.sleep(1.5)
    if HEIST_GAMES.get(uid) is game and game["running"] and game["phase"] == "pause":
        _seq_begin(bot, uid, game, False)


# ============================================================================================
# LIVELLO 2 - GUARDIE (precisione e reazione)
# ============================================================================================
def make_guard_schedule(n: int, frames: int) -> dict:
    """Esattamente UN fotogramma in cui tutte le guardie dormono; negli altri almeno una è sveglia."""
    c = random.randint(2, frames - 3)
    sched = []
    for i in range(frames):
        if i == c:
            sched.append([False] * n)
            continue
        row = [random.random() < 0.5 for _ in range(n)]
        if not any(row):
            row[random.randrange(n)] = True
        sched.append(row)
    return {"frames": sched, "c": c}


def _guard_text(game: dict, i: int) -> str:
    g = game["st"]["g"]
    row = g["frames"][i]
    line = "   ".join(f"💂{k + 1} {'👀' if aw else '😴'}" for k, aw in enumerate(row))
    return (
        _hdr(game, "Guardie") +
        f"💂 <b>Telecamera {game['st']['r'] + 1}/{len(GUARD_ROUNDS)}</b> — fotogramma {i + 1}/{len(g['frames'])}\n\n"
        f"{line}\n\n"
        "<i>😴 dorme · 👀 sveglia. Premi PASSA quando dormono TUTTE!</i>"
    )


def _guard_kb(uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🏃 PASSA ORA!", callback_data=f"hst_gp_{uid}")]])


async def _guard_loop(bot, uid: int, game: dict, tok: int):
    st = game["st"]
    n, iv, frames = GUARD_ROUNDS[st["r"]]
    g = make_guard_schedule(n, frames)
    g.update({"n": n, "iv": iv, "cur": -1, "t": [None] * frames})
    st["g"] = g
    game["phase"] = "guard"
    for i in range(frames):
        if not _alive(uid, game, tok) or game["phase"] != "guard":
            return
        t0 = time.monotonic()
        g["cur"] = i
        g["t"][i] = t0
        await _edit(bot, game, _guard_text(game, i), _guard_kb(uid))
        spent = time.monotonic() - t0
        if spent > 1.0:                         # edit rallentato da flood control: il fotogramma è comparso tardi
            g["t"][i] = time.monotonic() - 0.3
        await asyncio.sleep(max(0.2, iv - spent))
    if not _alive(uid, game, tok) or game["phase"] != "guard":
        return
    # fine del ciclo senza che il giocatore sia passato
    game["phase"] = "pause"
    game["tok"] += 1
    if not await _alarm(bot, uid, game, "🐌 <b>Troppo lento!</b> Il turno delle guardie è cambiato."):
        return
    await _edit(bot, game, _hdr(game, "Guardie") + "🚨 <b>ALLARME!</b> Riaccendo il monitor...")
    await asyncio.sleep(1.8)
    if HEIST_GAMES.get(uid) is game and game["running"] and game["phase"] == "pause":
        _guard_begin(bot, uid, game)


def _guard_begin(bot, uid: int, game: dict):
    game["tok"] += 1
    game["phase"] = "guard"
    _spawn(_guard_loop(bot, uid, game, game["tok"]))


async def _guard_tap(bot, uid: int, game: dict, now: float):
    g = game["st"]["g"]
    cur, c = g["cur"], g["c"]
    ok = False
    if cur == c:
        ok = True
    elif cur == c + 1 and g["t"][c + 1] is not None and now <= g["t"][c + 1] + GUARD_LAG:
        ok = True
    game["phase"] = "pause"
    game["tok"] += 1                              # ferma il ciclo dei fotogrammi
    if ok:
        game["st"]["r"] += 1
        if game["st"]["r"] >= len(GUARD_ROUNDS):
            await _level_done(bot, uid, game)
            return
        await _edit(bot, game, _hdr(game, "Guardie") + "✅ <b>Sei passato!</b> Prossima telecamera, più guardie e più veloce...")
        await asyncio.sleep(1.6)
        if HEIST_GAMES.get(uid) is game and game["running"] and game["phase"] == "pause":
            _guard_begin(bot, uid, game)
        return
    if cur < c:
        why = "troppo presto: almeno una guardia era ancora sveglia"
    elif cur > c + 1:
        why = "troppo tardi: la finestra era già chiusa"
    else:
        why = "troppo tardi"
    if not await _alarm(bot, uid, game, f"💥 <b>Sei stato visto!</b> Hai premuto {why}."):
        return
    await _edit(bot, game, _hdr(game, "Guardie") + "🚨 <b>ALLARME!</b> Riaccendo il monitor...")
    await asyncio.sleep(1.8)
    if HEIST_GAMES.get(uid) is game and game["running"] and game["phase"] == "pause":
        _guard_begin(bot, uid, game)


# ============================================================================================
# LIVELLO 3 - HACKING (logica con mosse limitate)
# ============================================================================================
def hack_apply(op: tuple, v: int):
    sym, n = op
    if sym == "+":
        r = v + n
    elif sym == "-":
        r = v - n
    elif sym == "*":
        r = v * n
    else:
        if v % n:
            return None
        r = v // n
    if r < 0 or r > 400:
        return None
    return r


def hack_min_depth(start: int, target: int, ops: list, max_depth: int):
    """BFS: numero minimo di mosse per arrivare al target (None se oltre max_depth)."""
    if start == target:
        return 0
    seen = {start}
    frontier = [start]
    for d in range(1, max_depth + 1):
        nxt = []
        for v in frontier:
            for op in ops:
                r = hack_apply(op, v)
                if r is None or r in seen:
                    continue
                if r == target:
                    return d
                seen.add(r)
                nxt.append(r)
        frontier = nxt
    return None


def make_hack(min_d: int, max_moves: int = HACK_MOVES) -> dict:
    """Puzzle sempre risolvibile in almeno min_d e al massimo max_moves mosse (verificato con BFS)."""
    for _ in range(800):
        ops = [("+", random.randint(3, 9)), ("-", random.randint(2, 7)), ("*", random.choice([2, 3])), ("/", 2)]
        start = random.randint(2, 12)
        v = start
        for _step in range(random.choice([min_d, min_d + 1])):
            choices = [(o, hack_apply(o, v)) for o in ops]
            choices = [(o, r) for o, r in choices if r is not None]
            if not choices:
                break
            v = random.choice(choices)[1]
        if not (10 <= v <= 200) or v == start:
            continue
        d = hack_min_depth(start, v, ops, max_moves)
        if d is not None and min_d <= d <= max_moves:
            return {"start": start, "target": v, "ops": ops, "min": d}
    ops = [("+", 5), ("-", 3), ("*", 2), ("/", 2)]
    return {"start": 4, "target": 29, "ops": ops, "min": hack_min_depth(4, 29, ops, 8) or 4}


def _op_label(op: tuple) -> str:
    return {"+": f"➕ {op[1]}", "-": f"➖ {op[1]}", "*": f"✖️ {op[1]}", "/": f"➗ {op[1]}"}[op[0]]


def _hack_text(game: dict, note: str = "") -> str:
    st = game["st"]
    h = st["h"]
    return (
        _hdr(game, "Hacking") + note +
        f"💻 <b>Terminale {st['r'] + 1}/{len(HACK_ROUNDS)}</b>\n"
        f"Parti da <b>{h['start']}</b> e devi ottenere esattamente <b>{h['target']}</b>.\n\n"
        f"Valore attuale: <b>{h['val']}</b>\n"
        f"Mosse rimaste: <b>{h['left']}</b>\n\n"
        "<i>➗ si può usare solo se il valore è divisibile. Nessun valore negativo. "
        "Con RESET riparti da capo (il timer corre).</i>"
    )


def _hack_kb(uid: int, h: dict) -> InlineKeyboardMarkup:
    btns = [InlineKeyboardButton(_op_label(op), callback_data=f"hst_hk_{i}_{uid}") for i, op in enumerate(h["ops"])]
    return InlineKeyboardMarkup([btns[:2], btns[2:], [InlineKeyboardButton("🔄 RESET", callback_data=f"hst_hr_{uid}")]])


async def _hack_new(bot, uid: int, game: dict, note: str = ""):
    st = game["st"]
    h = make_hack(HACK_ROUNDS[st["r"]])
    h["val"] = h["start"]
    h["left"] = HACK_MOVES
    st["h"] = h
    game["phase"] = "hack"
    await _edit(bot, game, _hack_text(game, note), _hack_kb(uid, h))


async def _hack_tap(bot, uid: int, game: dict, query, idx: int):
    st = game["st"]
    h = st["h"]
    if idx < 0 or idx >= len(h["ops"]):
        return
    r = hack_apply(h["ops"][idx], h["val"])
    if r is None:
        await query.answer("Operazione non valida su questo valore.", show_alert=False)
        return
    await query.answer()
    h["val"] = r
    h["left"] -= 1
    if r == h["target"]:
        st["r"] += 1
        if st["r"] >= len(HACK_ROUNDS):
            await _level_done(bot, uid, game)
            return
        await _hack_new(bot, uid, game, "✅ <b>Primo terminale violato!</b>\n\n")
        return
    if h["left"] <= 0:
        if not await _alarm(bot, uid, game, "💥 <b>Mosse finite!</b> Il sistema ti ha bloccato."):
            return
        await _hack_new(bot, uid, game, "🚨 <b>ALLARME!</b> Nuovo terminale.\n\n")
        return
    await _edit(bot, game, _hack_text(game), _hack_kb(uid, h))


async def _hack_reset(bot, uid: int, game: dict):
    h = game["st"]["h"]
    h["val"] = h["start"]
    h["left"] = HACK_MOVES
    await _edit(bot, game, _hack_text(game, "🔄 Terminale riavviato.\n\n"), _hack_kb(uid, h))


# ============================================================================================
# LIVELLO 4 - CAVEAU (mastermind numerico)
# ============================================================================================
def make_vault() -> dict:
    digits = random.sample(range(10), 4)
    return {"secret": digits, "hint": random.randrange(4), "guess": [], "hist": [], "tries": 0, "t0": time.time()}


def vault_score(secret: list, guess: list) -> tuple:
    bulls = sum(1 for a, b in zip(secret, guess) if a == b)
    cows = sum(1 for g in guess if g in secret) - bulls
    return bulls, cows


def _vault_text(game: dict, note: str = "") -> str:
    v = game["st"]["v"]
    left_t = max(0, int(VAULT_TIME - (time.time() - v["t0"])))
    hist = ""
    for g, b, c in v["hist"][-VAULT_TRIES:]:
        hist += f"{''.join(map(str, g))}  →  🟢 {b}  🟡 {c}\n"
    cur = "".join(str(d) for d in v["guess"]) + "▫️" * (4 - len(v["guess"]))
    hint = ["▫️"] * 4
    hint[v["hint"]] = str(v["secret"][v["hint"]])
    return (
        _hdr(game, "Caveau") + note +
        "🔐 <b>CAVEAU</b> — codice di 4 cifre, tutte DIVERSE.\n"
        "🟢 = cifra giusta al posto giusto · 🟡 = cifra giusta al posto sbagliato\n"
        f"🔎 Intercettato: <b>{' '.join(hint)}</b>\n"
        f"🔢 Tentativi: <b>{v['tries']}/{VAULT_TRIES}</b> · ⏳ Caveau: <b>{left_t}s</b>\n\n"
        f"{hist}\n➡️ <b>{cur}</b>"
    )


def _vault_kb(uid: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(str(d), callback_data=f"hst_vd_{d}_{uid}") for d in (1, 2, 3, 4, 5)],
        [InlineKeyboardButton(str(d), callback_data=f"hst_vd_{d}_{uid}") for d in (6, 7, 8, 9, 0)],
        [InlineKeyboardButton("⌫ Cancella", callback_data=f"hst_vb_{uid}"),
         InlineKeyboardButton("✅ INVIA", callback_data=f"hst_vs_{uid}")],
    ]
    return InlineKeyboardMarkup(rows)


async def _vault_new(bot, uid: int, game: dict, note: str = ""):
    game["st"] = {"v": make_vault()}
    game["phase"] = "vault"
    await _edit(bot, game, _vault_text(game, note), _vault_kb(uid))


async def _vault_digit(bot, uid: int, game: dict, query, d: int):
    v = game["st"]["v"]
    if len(v["guess"]) >= 4:
        await query.answer("Hai già 4 cifre: premi INVIA o Cancella.", show_alert=False)
        return
    if d in v["guess"]:
        await query.answer("Le cifre devono essere tutte diverse.", show_alert=False)
        return
    await query.answer()
    v["guess"].append(d)
    await _edit(bot, game, _vault_text(game), _vault_kb(uid))


async def _vault_back(bot, uid: int, game: dict, query):
    v = game["st"]["v"]
    await query.answer()
    if v["guess"]:
        v["guess"].pop()
    await _edit(bot, game, _vault_text(game), _vault_kb(uid))


async def _vault_submit(bot, uid: int, game: dict, query):
    v = game["st"]["v"]
    if time.time() - v["t0"] > VAULT_TIME:
        await query.answer()
        if await _alarm(bot, uid, game, "⏳ <b>Tempo del caveau scaduto!</b> Il sistema si è riarmato."):
            await _vault_new(bot, uid, game, "🚨 <b>ALLARME!</b> Nuovo caveau.\n\n")
        return
    if len(v["guess"]) != 4:
        await query.answer("Servono 4 cifre.", show_alert=False)
        return
    await query.answer()
    g = list(v["guess"])
    b, c = vault_score(v["secret"], g)
    v["tries"] += 1
    v["hist"].append((g, b, c))
    v["guess"] = []
    if b == 4:
        await _level_done(bot, uid, game)
        return
    if v["tries"] >= VAULT_TRIES:
        code = "".join(map(str, v["secret"]))
        if await _alarm(bot, uid, game, f"💥 <b>Tentativi finiti!</b> Il codice era {code}."):
            await _vault_new(bot, uid, game, "🚨 <b>ALLARME!</b> Nuovo caveau.\n\n")
        return
    await _edit(bot, game, _vault_text(game), _vault_kb(uid))


# ============================================================================================
# LIVELLO 5 - FUGA (scelta tattica)
# ============================================================================================
WEATHER = ["sereno", "pioggia", "nebbia"]
POLICE = ["pattuglie", "blocchi", "elicotteri"]
TRAFFIC = ["scorrevole", "intasato"]
INFORMANT = ["libere", "sorvegliate"]
VEHICLES = ["🚁 Elicottero", "🏍️ Moto", "🕳️ Fognature", "🚐 Furgone"]


def valid_vehicles(weather: str, police: str, traffic: str, informant: str) -> list:
    ok = []
    if weather in ("sereno", "pioggia") and police != "elicotteri":
        ok.append(0)
    if police != "blocchi" and weather != "pioggia":
        ok.append(1)
    if weather != "pioggia" and informant == "libere":
        ok.append(2)
    if traffic == "scorrevole" and police != "pattuglie":
        ok.append(3)
    return ok


def flight_combos() -> list:
    out = []
    for w in WEATHER:
        for p in POLICE:
            for t in TRAFFIC:
                for i in INFORMANT:
                    v = valid_vehicles(w, p, t, i)
                    if len(v) == 1:
                        out.append(((w, p, t, i), v[0]))
    return out


_CLUE = {
    "sereno": "Il meteo prevede cielo limpido e luna piena.",
    "pioggia": "Sta diluviando su tutta la città.",
    "nebbia": "Una nebbia fitta copre i tetti.",
    "pattuglie": "La radio della polizia: auto di pattuglia in ogni via del quartiere.",
    "blocchi": "La radio della polizia: posti di blocco su tutte le arterie.",
    "elicotteri": "La radio della polizia: gli elicotteri sorvolano la zona.",
    "scorrevole": "Il traffico è fluido, nessuna coda.",
    "intasato": "La tangenziale è completamente intasata.",
    "libere": "La tua talpa conferma: le fognature sono libere.",
    "sorvegliate": "La tua talpa avverte: i tombini sono sorvegliati.",
}

_RULES = (
    "<b>Manuale di fuga:</b>\n"
    "🚁 Elicottero: cielo sereno o pioggia, e nessun elicottero della polizia.\n"
    "🏍️ Moto: niente blocchi stradali e niente pioggia.\n"
    "🕳️ Fognature: niente pioggia e fognature libere.\n"
    "🚐 Furgone: traffico scorrevole e niente pattuglie."
)

_FLIGHT = flight_combos()


def _flight_text(game: dict, note: str = "") -> str:
    st = game["st"]
    clues = [_CLUE[x] for x in st["combo"]]
    random.Random(st["seed"]).shuffle(clues)
    return (
        _hdr(game, "Fuga") + note +
        f"🚨 <b>FUGA — tappa {st['r'] + 1}/{FLIGHT_ROUNDS}</b>\n"
        "Hai il bottino. Leggi i rapporti e scegli l'UNICO mezzo che funziona.\n\n"
        + "\n".join(f"📻 {c}" for c in clues) + "\n\n" + _RULES
    )


def _flight_kb(uid: int) -> InlineKeyboardMarkup:
    btns = [InlineKeyboardButton(lbl, callback_data=f"hst_ft_{i}_{uid}") for i, lbl in enumerate(VEHICLES)]
    return InlineKeyboardMarkup([btns[:2], btns[2:]])


async def _flight_new(bot, uid: int, game: dict, note: str = ""):
    st = game["st"]
    last = st.get("combo")
    combo, ans = random.choice(_FLIGHT)
    for _ in range(10):
        if combo != last:
            break
        combo, ans = random.choice(_FLIGHT)
    st["combo"], st["ans"], st["seed"] = combo, ans, random.randrange(10 ** 6)
    game["phase"] = "flight"
    await _edit(bot, game, _flight_text(game, note), _flight_kb(uid))


async def _flight_tap(bot, uid: int, game: dict, idx: int):
    st = game["st"]
    if idx == st["ans"]:
        st["r"] += 1
        if st["r"] >= FLIGHT_ROUNDS:
            await _level_done(bot, uid, game)
            return
        await _flight_new(bot, uid, game, "✅ <b>Prima tappa superata!</b>\n\n")
        return
    if not await _alarm(bot, uid, game, f"💥 <b>{VEHICLES[idx]} era la scelta sbagliata!</b>"):
        return
    await _flight_new(bot, uid, game, "🚨 <b>ALLARME!</b> Nuovo scenario.\n\n")


# ============================================================================================
# CALLBACK
# ============================================================================================
@serialized("user")
async def heist_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now = time.monotonic()
    query = update.callback_query
    parts = query.data.split("_")            # hst_<azione>_[argomento_]<uid>
    action = parts[1]
    try:
        owner_id = int(parts[-1])
    except ValueError:
        await query.answer()
        return
    if query.from_user.id != owner_id:
        await query.answer("🛑 Questa non è la tua rapina!", show_alert=True)
        return

    user = query.from_user
    uid = user.id
    game = HEIST_GAMES.get(uid)
    if not game or not game.get("v3"):
        await query.answer()
        try:
            await query.edit_message_text("❌ Sessione Rapina terminata.")
        except Exception:
            pass
        return

    bot = context.bot
    game["ts"] = time.time()
    game["uid"] = uid

    if action == "go":
        await query.answer()
        if game["running"]:
            return
        game["running"] = True
        game["name"] = user.first_name or "Giocatore"
        game["chat"], game["mid"] = query.message.chat_id, query.message.message_id
        game["deadline"] = time.time() + HEIST_TIME
        game["t_start"] = time.time()
        game["level"] = 1
        await _present(bot, uid, game)
        return

    if not game["running"]:
        await query.answer("Premi prima INIZIA LA RAPINA!", show_alert=True)
        return
    if time.time() > game["deadline"]:
        await query.answer()
        await _fail(bot, uid, game, "⏰ <b>TEMPO SCADUTO!</b> La polizia è arrivata.")
        return

    phase, lvl = game["phase"], game["level"]

    if action == "rd":
        await query.answer()
        if phase != "brief":
            return
        if lvl == 1:
            _seq_begin(bot, uid, game, True)
        elif lvl == 2:
            _guard_begin(bot, uid, game)
        return

    if action == "gp":                       # la risposta viene data DOPO il giudizio (così il timing è preciso)
        if lvl != 2 or phase != "guard":
            await query.answer()
            return
        await query.answer()
        await _guard_tap(bot, uid, game, now)
        return

    if action == "sq":
        if lvl != 1 or phase != "input":
            await query.answer()
            return
        await query.answer()
        await _seq_tap(bot, uid, game, int(parts[2]))
        return

    if action in ("hk", "hr"):
        if lvl != 3 or phase != "hack":
            await query.answer()
            return
        if action == "hr":
            await query.answer()
            await _hack_reset(bot, uid, game)
        else:
            await _hack_tap(bot, uid, game, query, int(parts[2]))
        return

    if action in ("vd", "vb", "vs"):
        if lvl != 4 or phase != "vault":
            await query.answer()
            return
        if action == "vd":
            await _vault_digit(bot, uid, game, query, int(parts[2]))
        elif action == "vb":
            await _vault_back(bot, uid, game, query)
        else:
            await _vault_submit(bot, uid, game, query)
        return

    if action == "ft":
        if lvl != 5 or phase != "flight":
            await query.answer()
            return
        await query.answer()
        await _flight_tap(bot, uid, game, int(parts[2]))
        return

    await query.answer()


def register(app):
    app.add_handler(CallbackQueryHandler(heist_callback, pattern="^hst_", block=False))
    if app.job_queue:
        app.job_queue.run_repeating(expire_heists_v3, interval=10, first=10)
