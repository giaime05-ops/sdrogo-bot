"""Mini app: Sdrogo Heist in 2D (per ora SOLO account admin di prova).
Stessi livelli, stessi generatori e stesse regole della rapina nel bot (handlers/heist.py), ma giocati con dei
giochini nella mini app. Il server decide tutto (sequenze, turni delle guardie, codice del caveau, premio).
Nel profilo di prova il Banco vero non viene mai toccato: il premio si calcola dal Banco ma non lo si preleva."""
import random
import time

from economy import fmt, inv, banco_balance, follia_active, record_single_result, quest_progress
from handlers import heist as H
from handlers.games import HEIST_COST, _charge
from storage import get_user_coins, add_user_coins, save_db, is_web_sandbox
from state import game_enabled as _ge

WEB_HEIST = {}
GUARD_LAG = 0.9                 # tolleranza un po' più larga del bot: qui c'è anche la rete tra telefono e server
ALARM_PAUSE = 1.8
SEQ_SHOW_PER = 0.8


def _left(g):
    return max(0, int(g["deadline"] - time.time()))


def info(uid):
    return {"cost": HEIST_COST, "time": H.HEIST_TIME, "alarm_penalty": H.ALARM_PENALTY, "max_alarms": H.MAX_ALARMS,
            "share": int(H.SHARE * 100), "share_follia": int(H.SHARE_FOLLIA * 100), "banco_protect": H.BANCO_PROTECT,
            "prize_fixed": H.PRIZE_FIXED, "balance": get_user_coins(uid, uid),
            "levels": [["Centralino", "Memorizza le sequenze di colori"], ["Guardie", "Passa solo quando dormono TUTTE"],
                       ["Hacking", "Raggiungi il codice con mosse contate"], ["Caveau", "Indovina il codice a 4 cifre"],
                       ["Fuga", "Scegli il mezzo giusto leggendo i rapporti"]]}


# ---------------------------------------------------------------- viste
def _base(g):
    return {"level": g["level"], "alarms": g["alarms"], "max_alarms": H.MAX_ALARMS, "left": _left(g), "phase": g["phase"]}


def _view(g):
    v = _base(g); st = g["st"]; lvl = g["level"]
    if lvl == 1:
        v.update({"r": st.get("r", 0), "rounds": len(H.SEQ_ROUNDS), "colors": len(H.SEQ_SYMBOLS)})
        if g["phase"] in ("show", "input") and "seq" in st:
            v["seq"] = st["seq"]; v["show_in"] = max(0.0, st.get("show_from", 0) - time.time()); v["show_secs"] = st.get("secs", 0)
            v["pos"] = st.get("pos", 0)
    elif lvl == 2:
        v.update({"r": st.get("r", 0), "rounds": len(H.GUARD_ROUNDS)})
        gg = st.get("g")
        if g["phase"] == "guard" and gg:
            v.update({"frames": gg["frames"], "iv": gg["iv"], "delay": max(0.0, gg["t0"] - time.time()), "n": gg["n"]})
    elif lvl == 3:
        h = st["h"]
        v.update({"r": st["r"], "rounds": len(H.HACK_ROUNDS), "start": h["start"], "target": h["target"], "val": h["val"], "left_moves": h["left"],
                  "ops": [[o[0], o[1]] for o in h["ops"]]})
    elif lvl == 4:
        vt = st["v"]
        v.update({"hint": [vt["hint"], vt["secret"][vt["hint"]]], "tries": vt["tries"], "max_tries": H.VAULT_TRIES,
                  "hist": [[gs, b, c] for gs, b, c in vt["hist"]], "vault_left": max(0, int(H.VAULT_TIME - (time.time() - vt["t0"])))})
    elif lvl == 5:
        clues = [H._CLUE[x] for x in st["combo"]]
        random.Random(st["seed"]).shuffle(clues)
        v.update({"r": st["r"], "rounds": H.FLIGHT_ROUNDS, "clues": clues, "vehicles": H.VEHICLES,
                  "rules": ["🚁 Elicottero: cielo sereno o pioggia, e nessun elicottero della polizia.",
                            "🏍️ Moto: niente blocchi stradali e niente pioggia.",
                            "🕳️ Fognature: niente pioggia e fognature libere.",
                            "🚐 Furgone: traffico scorrevole e niente pattuglie."]})
    return v


def _out(g, event=None, **extra):
    d = {"view": _view(g), "event": event or {}}
    d.update(extra)
    d["balance"] = get_user_coins(g["uid"], g["uid"])
    return d


# ---------------------------------------------------------------- fine partita
def _fail(uid, g, reason):
    WEB_HEIST.pop(uid, None)
    record_single_result(uid, uid, False, -HEIST_COST, game="heist", bet=HEIST_COST)
    return {"end": {"result": "fail", "msg": reason}, "balance": get_user_coins(uid, uid), "view": None}


def _win(uid, g, qmsg=""):
    WEB_HEIST.pop(uid, None)
    bal = banco_balance()
    prize = H.PRIZE_FIXED if bal < H.BANCO_PROTECT else int(bal * (H.SHARE_FOLLIA if follia_active() else H.SHARE))
    prize = max(prize, H.PRIZE_FIXED)
    add_user_coins(uid, uid, prize)
    record_single_result(uid, uid, True, prize - HEIST_COST, game="heist", bet=HEIST_COST)
    d = inv(uid, uid)
    d["stars"] = d.get("stars", 0) + 1; d["titles"] = d.get("titles", 0) + 1; d["persecutes"] = d.get("persecutes", 0) + 1
    save_db()
    el = int(time.time() - g["t_start"])
    return {"end": {"result": "win", "prize": prize, "elapsed": el, "alarms": g["alarms"], "msg": qmsg}, "balance": get_user_coins(uid, uid), "view": None}


def _alarm(uid, g, reason):
    """Registra un allarme. Ritorna (None, ) se la partita continua, altrimenti la risposta di fine."""
    g["alarms"] += 1
    g["deadline"] -= H.ALARM_PENALTY
    if g["alarms"] >= H.MAX_ALARMS:
        return _fail(uid, g, reason + " Terzo allarme: la polizia circonda l'edificio.")
    if _left(g) <= 0:
        return _fail(uid, g, reason + " Tempo scaduto: la polizia è arrivata.")
    return None


def _level_done(uid, g):
    done = g["level"]
    qmsg = quest_progress(uid, uid, "heist:level", done)
    if done >= 5:
        return _win(uid, g, qmsg)
    g["level"] += 1
    _present(g)
    return _out(g, {"type": "level", "level": done, "msg": qmsg or ""})


# ---------------------------------------------------------------- livelli
def _present(g):
    lvl = g["level"]
    g["st"] = {"r": 0}
    g["phase"] = "brief"
    if lvl == 3:
        _hack_new(g)
    elif lvl == 4:
        _vault_new(g)
    elif lvl == 5:
        _flight_new(g)


def _seq_begin(g, new, delay=0.4):
    st = g["st"]
    if new or "seq" not in st:
        st["seq"] = [random.randrange(len(H.SEQ_SYMBOLS)) for _ in range(H.SEQ_ROUNDS[st["r"]])]
    st["pos"] = 0
    st["secs"] = SEQ_SHOW_PER * len(st["seq"]) + 0.8
    st["show_from"] = time.time() + delay
    st["input_at"] = st["show_from"] + st["secs"] - 0.3
    g["phase"] = "show"


def _guard_begin(g, delay):
    st = g["st"]
    n, iv, frames = H.GUARD_ROUNDS[st["r"]]
    sch = H.make_guard_schedule(n, frames)
    sch.update({"n": n, "iv": iv, "t0": time.time() + delay})
    st["g"] = sch
    g["phase"] = "guard"


def _hack_new(g):
    h = H.make_hack(H.HACK_ROUNDS[g["st"]["r"]])
    h["val"] = h["start"]; h["left"] = H.HACK_MOVES
    g["st"]["h"] = h
    g["phase"] = "hack"


def _vault_new(g):
    g["st"] = {"v": H.make_vault()}
    g["phase"] = "vault"


def _flight_new(g):
    st = g["st"]
    last = st.get("combo")
    combo, ans = random.choice(H._FLIGHT)
    for _ in range(10):
        if combo != last:
            break
        combo, ans = random.choice(H._FLIGHT)
    st["combo"], st["ans"], st["seed"] = combo, ans, random.randrange(10 ** 6)
    g["phase"] = "flight"


# ---------------------------------------------------------------- azioni
def state(uid):
    g = WEB_HEIST.get(uid)
    out = {"info": info(uid), "active": False}
    if g:
        if time.time() > g["deadline"] + 1:
            out.update(_fail(uid, g, "⏰ Tempo scaduto: la polizia è arrivata."))
        else:
            out.update({"active": True, "view": _view(g)})
    return out


def start(uid):
    if not is_web_sandbox():
        return {"error": "Disponibile solo nell'account di prova.", "code": 403}
    if not _ge("heist"):
        return {"error": "Lo Sdrogo Heist è in manutenzione.", "code": 503}
    g = WEB_HEIST.get(uid)
    if g and time.time() <= g["deadline"] + 1:
        return _out(g)
    if get_user_coins(uid, uid) < HEIST_COST:
        return {"error": f"Servono {HEIST_COST} $SDG per il pass.", "code": 400}
    _charge(uid, uid, HEIST_COST)
    now = time.time()
    g = {"uid": uid, "level": 1, "deadline": now + H.HEIST_TIME, "alarms": 0, "t_start": now, "phase": "brief", "st": {"r": 0}}
    WEB_HEIST[uid] = g
    return _out(g, {"type": "start"})


def act(uid, a, data):
    g = WEB_HEIST.get(uid)
    if not g:
        return {"error": "Nessuna rapina in corso.", "code": 409}
    if time.time() > g["deadline"]:
        return _fail(uid, g, "⏰ Tempo scaduto: la polizia è arrivata.")
    lvl, phase, st, now = g["level"], g["phase"], g["st"], time.time()

    if a == "rd":                                              # "comincia" del livello 1 o 2
        if phase != "brief":
            return _out(g)
        if lvl == 1:
            _seq_begin(g, True)
        elif lvl == 2:
            _guard_begin(g, 0.6)
        return _out(g)

    if lvl == 1 and a == "sq" and phase == "show":
        if now < st.get("input_at", 0):
            return _out(g)
        g["phase"] = phase = "input"
    if lvl == 1 and a == "sq" and phase == "input":
        try:
            idx = int(data.get("i"))
        except (TypeError, ValueError):
            return _out(g)
        if idx == st["seq"][st["pos"]]:
            st["pos"] += 1
            if st["pos"] >= len(st["seq"]):
                st["r"] += 1
                if st["r"] >= len(H.SEQ_ROUNDS):
                    return _level_done(uid, g)
                _seq_begin(g, True, 1.4)
                return _out(g, {"type": "ok", "msg": "Sequenza corretta! Prossima, più lunga…"})
            return _out(g, {"type": "tap"})
        r = _alarm(uid, g, "Colore sbagliato!")
        if r:
            return r
        _seq_begin(g, False, 1.6)
        return _out(g, {"type": "alarm", "msg": "ALLARME! Ti rimostro la stessa sequenza…"})

    if lvl == 2 and phase == "guard" and a in ("gp", "gm"):
        gg = st["g"]; c, iv, frames = gg["c"], gg["iv"], len(gg["frames"])
        el = now - gg["t0"]
        if a == "gm":                                          # il ciclo è finito senza che il giocatore passasse
            if el < frames * iv - 0.6:
                return _out(g)
            r = _alarm(uid, g, "Troppo lento! Il turno delle guardie è cambiato.")
            if r:
                return r
            _guard_begin(g, ALARM_PAUSE)
            return _out(g, {"type": "alarm", "msg": "ALLARME! Riaccendo il monitor…"})
        cur = int(el // iv) if el >= 0 else -1
        ok = cur == c or (cur == c + 1 and el - (c + 1) * iv <= GUARD_LAG)
        if ok:
            st["r"] += 1
            if st["r"] >= len(H.GUARD_ROUNDS):
                return _level_done(uid, g)
            _guard_begin(g, 1.6)
            return _out(g, {"type": "ok", "msg": "Sei passato! Prossima telecamera, più guardie e più veloce…"})
        why = "troppo presto: almeno una guardia era ancora sveglia" if cur < c else "troppo tardi: la finestra era già chiusa"
        r = _alarm(uid, g, f"Sei stato visto! Hai premuto {why}.")
        if r:
            return r
        _guard_begin(g, ALARM_PAUSE)
        return _out(g, {"type": "alarm", "msg": "ALLARME! Riaccendo il monitor…"})

    if lvl == 3 and phase == "hack":
        h = st["h"]
        if a == "hr":
            h["val"] = h["start"]; h["left"] = H.HACK_MOVES
            return _out(g, {"type": "reset"})
        if a != "hk":
            return _out(g)
        try:
            idx = int(data.get("i"))
            r = H.hack_apply(h["ops"][idx], h["val"])
        except (TypeError, ValueError, IndexError):
            return _out(g)
        if r is None:
            return _out(g, {"type": "invalid", "msg": "Operazione non valida su questo valore."})
        h["val"] = r; h["left"] -= 1
        if r == h["target"]:
            st["r"] += 1
            if st["r"] >= len(H.HACK_ROUNDS):
                return _level_done(uid, g)
            _hack_new(g)
            return _out(g, {"type": "ok", "msg": "Terminale violato! Ecco il prossimo."})
        if h["left"] <= 0:
            res = _alarm(uid, g, "Mosse finite! Il sistema ti ha bloccato.")
            if res:
                return res
            _hack_new(g)
            return _out(g, {"type": "alarm", "msg": "ALLARME! Nuovo terminale."})
        return _out(g, {"type": "tap"})

    if lvl == 4 and phase == "vault" and a == "vs":
        v = st["v"]
        if now - v["t0"] > H.VAULT_TIME:
            res = _alarm(uid, g, "Tempo del caveau scaduto! Il sistema si è riarmato.")
            if res:
                return res
            _vault_new(g)
            return _out(g, {"type": "alarm", "msg": "ALLARME! Nuovo caveau."})
        try:
            guess = [int(x) for x in (data.get("g") or [])]
        except (TypeError, ValueError):
            return _out(g)
        if len(guess) != 4 or len(set(guess)) != 4 or any(d < 0 or d > 9 for d in guess):
            return _out(g, {"type": "invalid", "msg": "Servono 4 cifre tutte diverse."})
        b, c = H.vault_score(v["secret"], guess)
        v["tries"] += 1; v["hist"].append((guess, b, c))
        if b == 4:
            return _level_done(uid, g)
        if v["tries"] >= H.VAULT_TRIES:
            code = "".join(map(str, v["secret"]))
            res = _alarm(uid, g, f"Tentativi finiti! Il codice era {code}.")
            if res:
                return res
            _vault_new(g)
            return _out(g, {"type": "alarm", "msg": f"ALLARME! Il codice era {code}. Nuovo caveau."})
        return _out(g, {"type": "tap", "b": b, "c": c})

    if lvl == 5 and phase == "flight" and a == "ft":
        try:
            idx = int(data.get("i"))
        except (TypeError, ValueError):
            return _out(g)
        if idx == st["ans"]:
            st["r"] += 1
            if st["r"] >= H.FLIGHT_ROUNDS:
                return _level_done(uid, g)
            _flight_new(g)
            return _out(g, {"type": "ok", "msg": "Prima tappa superata!"})
        res = _alarm(uid, g, f"{H.VEHICLES[idx] if 0 <= idx < 4 else 'Quel mezzo'} era la scelta sbagliata!")
        if res:
            return res
        _flight_new(g)
        return _out(g, {"type": "alarm", "msg": "ALLARME! Nuovo scenario."})

    return _out(g)
