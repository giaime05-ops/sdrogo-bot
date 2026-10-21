"""Mini app: DAILY HEIST (mini rapine). Per ora SOLO account admin di prova.
Ogni giorno (mezzanotte italiana) c'è uno schema uguale per tutti: due mini rapine ("Colpo Rapido 1/2") da 3 giochini ciascuna,
scelti a rotazione da un pool di 7 (memoria, matematica, logica, riflessi...). Ogni giochino ha 3 round di difficoltà crescente.
Costo 100 $SDG, premio fisso 300 $SDG, 150 secondi, al 2° allarme si fallisce.
Ogni mini rapina si può giocare 3 volte al giorno, con almeno 2 ore tra una partenza e la successiva.
Il server genera e controlla tutto (risposte giuste, tempi, tracciati)."""
import random
import time

from economy import today_str, record_single_result
from handlers.games import _charge
from storage import get_user_coins, add_user_coins, is_web_sandbox
from state import game_enabled as _ge

MINI_COST = 100
MINI_PRIZE = 300
MINI_TIME = 150
MINI_MAX_ALARMS = 2
ROUNDS = 3
MINI_ALARM_PEN = 10
MINI_PLAYS = 3
MINI_GAP = 2 * 3600
POOL = ["face", "cable", "print", "freq", "voice", "logic", "laser"]
NAMES = {"face": "Riconoscimento facciale", "cable": "Taglio cavi", "print": "Impronta digitale",
         "freq": "Hacking frequenze", "voice": "Impronta vocale", "logic": "Decifra il codice", "laser": "Corridoio laser"}
TITLES = {"A": "Colpo Rapido 1", "B": "Colpo Rapido 2"}

MINI = {}        # uid -> rapina in corso
LOG = {}         # uid -> {"day", "A": [istanti di partenza], "B": [...]}

BADGE = ["rossa", "blu", "gialla", "verde", "viola", "arancione"]          # "scheda ..."
HATN = ["rosso", "blu", "giallo", "verde", "viola", "arancione"]           # "cappello ..."
CABLES = ["Rosso", "Blu", "Giallo", "Verde", "Viola", "Arancio"]
LASER_SPEED = 24.0


def scheme(day=None):
    day = day or today_str()
    rng = random.Random("mini-" + day)
    a = rng.sample(POOL, 3)
    rest = [x for x in POOL if x not in a]
    b = rng.sample(rest, 3)
    return {"A": a, "B": b}


def _log(uid):
    d = LOG.get(uid)
    if not d or d.get("day") != today_str():
        d = LOG[uid] = {"day": today_str(), "A": [], "B": []}
    return d


def _wait(uid, which):
    L = _log(uid)[which]
    if len(L) >= MINI_PLAYS:
        return -1
    if not L:
        return 0
    return max(0, int(L[-1] + MINI_GAP - time.time()))


def info(uid):
    sc = scheme()
    minis = []
    for w in ("A", "B"):
        L = _log(uid)[w]
        minis.append({"id": w, "title": TITLES[w], "games": [NAMES[k] for k in sc[w]], "used": len(L), "max": MINI_PLAYS, "wait": _wait(uid, w)})
    return {"cost": MINI_COST, "prize": MINI_PRIZE, "time": MINI_TIME, "max_alarms": MINI_MAX_ALARMS, "alarm_penalty": MINI_ALARM_PEN,
            "gap": MINI_GAP, "plays": MINI_PLAYS, "minis": minis, "balance": get_user_coins(uid, uid)}


# ---------------------------------------------------------------- giochini
def _series(r):
    """Serie numerica da completare (round 1: progressione, 2: moltiplicazione o alternanza, 3: differenze crescenti / Fibonacci)."""
    rn = random
    if r == 0:
        st, k = rn.randint(1, 20), rn.randint(2, 9)
        seq = [st + k * i for i in range(6)]
    elif r == 1:
        if rn.random() < 0.5:
            st, k = rn.randint(1, 4), rn.choice([2, 3])
            seq = [st * k ** i for i in range(6)]
        else:
            st, a, b = rn.randint(5, 20), rn.randint(3, 9), rn.randint(1, 4)
            seq = [st]
            for i in range(5):
                seq.append(seq[-1] + (a if i % 2 == 0 else -b))
    else:
        if rn.random() < 0.5:
            st, d0, dd = rn.randint(1, 9), rn.randint(1, 3), rn.randint(1, 3)
            seq, d = [st], d0
            for _ in range(5):
                seq.append(seq[-1] + d); d += dd
        else:
            a, b = rn.randint(1, 4), rn.randint(2, 5)
            seq = [a, b]
            while len(seq) < 6:
                seq.append(seq[-1] + seq[-2])
    return seq


def _gen(kind, r=0):
    rn = random
    if kind == "face":
        n = (4, 5, 6)[r]
        badges, hats = rn.sample(range(6), n), rn.sample(range(6), n)
        glasses = [True] * n
        glasses[rn.randrange(n)] = False
        guards = [{"hat": hats[i], "glasses": glasses[i], "badge": badges[i], "skin": rn.randrange(4)} for i in range(n)]
        t = rn.randrange(n)
        mode = rn.choice(["badge", "hat"] if r == 0 else ["badge", "hat", "glass"] if r == 1 else ["both", "hat", "glass", "badge"])
        if mode == "badge":
            ans, q = t, f"Chi aveva la scheda {BADGE[badges[t]].upper()}?"
        elif mode == "hat":
            ans, q = t, f"Chi portava il cappello {HATN[hats[t]].upper()}?"
        elif mode == "glass":
            ans, q = glasses.index(False), "Chi NON portava gli occhiali?"
        else:
            ans, q = t, f"Chi aveva il cappello {HATN[hats[t]].upper()} E la scheda {BADGE[badges[t]].upper()}?"
        return {"guards": guards, "q": q, "ans": ans, "show": (3.0, 3.5, 4.0)[r], "limit": (7, 8, 9)[r]}
    if kind == "cable":
        n = (4, 5, 6)[r]
        order = rn.sample(CABLES, n)
        for _ in range(2000):
            if r == 0:
                a, b, c = rn.randint(2, 7), rn.randint(2, 7), rn.randint(1, 9)
                pos, ex = a + b - c, f"{a} + {b} − {c}"
            elif r == 1:
                a, b, c = rn.randint(2, 5), rn.randint(2, 4), rn.randint(1, 9)
                pos, ex = a * b - c, f"{a} × {b} − {c}"
            else:
                a, b, c, d = rn.randint(1, 5), rn.randint(1, 5), rn.randint(2, 4), rn.randint(1, 9)
                pos, ex = (a + b) * c - d, f"({a} + {b}) × {c} − {d}"
            if 1 <= pos <= n:
                break
        return {"cables": order, "q": f"Taglia il cavo in posizione {ex}  (contando da sinistra)", "ans": pos - 1, "limit": (8, 10, 12)[r]}
    if kind == "logic":
        seq = _series(r)
        ans = seq[-1]
        opts = {ans}
        gap = max(1, abs(seq[-1] - seq[-2]))
        for cand in (ans + gap, ans - gap, ans + 1, ans - 1, ans + 2 * gap, ans + 2, ans - 2, ans + 3):
            if len(opts) >= 4:
                break
            if cand > 0:
                opts.add(cand)
        opts = list(opts)
        rn.shuffle(opts)
        return {"seq": seq[:-1], "options": opts, "ans": opts.index(ans), "q": "Quale numero completa la serie?", "limit": (9, 11, 13)[r]}
    if kind == "print":
        n, diff = (3, 4, 5)[r], (2, 2, 1)[r]
        t = [[1 if rn.random() < 0.45 else 0 for _ in range(5)] for _ in range(5)]
        cands = []
        while len(cands) < n - 1:
            m = [row[:] for row in t]
            for _ in range(diff):
                i, j = rn.randrange(5), rn.randrange(5)
                m[i][j] ^= 1
            if m != t and m not in cands:
                cands.append(m)
        ans = rn.randrange(n)
        cands.insert(ans, [row[:] for row in t])
        return {"target": t, "cands": cands, "ans": ans, "limit": (9, 10, 12)[r]}
    if kind == "freq":
        return {"f0": round(rn.uniform(2.0, 4.0), 2), "amp": 0.5, "w": (0.4, 0.55, 0.7)[r], "ph": round(rn.uniform(0, 6.28), 2),
                "need": (2.0, 2.5, 2.5)[r], "tol": (0.28, 0.24, 0.21)[r], "limit": (14, 16, 18)[r]}
    if kind == "laser":
        xs = [(35, 70), (25, 50, 75), (18, 38, 58, 78)][r]
        lo, hi = [(1.4, 2.0), (1.25, 1.8), (1.1, 1.6)][r]
        lasers = []
        for x in xs:
            per = round(rn.uniform(lo, hi), 2)
            lasers.append({"x": x + rn.randint(-3, 3), "p": per, "ph": round(rn.uniform(0, per), 2), "duty": 0.5})
        return {"lasers": lasers, "speed": LASER_SPEED, "zone": 1.8, "delay": 1.0, "limit": 22}
    # voice
    n = (2, 3, 4)[r]
    w = (0.16, 0.12, 0.10)[r]
    base = sorted(rn.uniform(0.0, 0.7 - (n - 1) * 0.2) for _ in range(n))           # zone distanziate di almeno 0,2
    cs = [round(0.15 + base[i] + 0.2 * i, 2) for i in range(n)]
    rn.shuffle(cs)                                                                  # l'ordine in cui toccarle è casuale
    return {"nodes": cs, "w": w, "speed": round(rn.uniform(0.5, 0.6) + 0.07 * r, 2), "phase0": round(rn.random(), 3), "limit": (12, 14, 16)[r]}


def _pub(kind, st):
    d = {k: v for k, v in st.items() if k != "ans"}
    return d


def _tri(u):
    f = u - int(u)
    return 1.0 - abs(2.0 * f - 1.0)


def _left(g):
    return max(0, int(g["deadline"] - time.time()))


def _begin_game(g):
    kind = g["games"][g["i"]]
    g["st"] = _gen(kind, g.get("r", 0))
    g["t_game"] = time.time()
    g["kind"] = kind


def _view(g):
    return {"level": g["i"] + 1, "total": len(g["games"]), "round": g.get("r", 0) + 1, "rounds": ROUNDS, "name": NAMES[g["kind"]], "kind": g["kind"], "alarms": g["alarms"],
            "max_alarms": MINI_MAX_ALARMS, "left": _left(g), "phase": "play", "mini": True, "data": _pub(g["kind"], g["st"])}


def _out(g, event=None):
    return {"view": _view(g), "event": event or {}, "balance": get_user_coins(g["uid"], g["uid"])}


def _fail(uid, g, reason):
    MINI.pop(uid, None)
    record_single_result(uid, uid, False, -MINI_COST, game="mini", bet=MINI_COST)
    return {"end": {"result": "fail", "msg": reason, "mini": True, "which": g["id"]}, "balance": get_user_coins(uid, uid), "view": None}


def _win(uid, g):
    MINI.pop(uid, None)
    add_user_coins(uid, uid, MINI_PRIZE)
    record_single_result(uid, uid, True, MINI_PRIZE - MINI_COST, game="mini", bet=MINI_COST)
    return {"end": {"result": "win", "prize": MINI_PRIZE, "elapsed": int(time.time() - g["t_start"]), "alarms": g["alarms"], "mini": True, "which": g["id"], "msg": ""},
            "balance": get_user_coins(uid, uid), "view": None}


def _alarm(uid, g, reason):
    g["alarms"] += 1
    g["deadline"] -= MINI_ALARM_PEN
    if g["alarms"] >= MINI_MAX_ALARMS:
        return _fail(uid, g, reason + f" {MINI_MAX_ALARMS}° allarme: la rapina è fallita.")
    if _left(g) <= 0:
        return _fail(uid, g, reason + " Tempo scaduto.")
    _begin_game(g)
    return _out(g, {"type": "alarm", "msg": "ALLARME! " + reason})


def _next(uid, g):
    """Round superato: il prossimo del giochino, oppure il giochino successivo."""
    g["r"] = g.get("r", 0) + 1
    if g["r"] < ROUNDS:
        _begin_game(g)
        return _out(g, {"type": "ok", "msg": f"Round {g['r'] + 1}/{ROUNDS}: più difficile!"})
    return _done(uid, g)


def _done(uid, g):
    g["r"] = 0
    g["i"] += 1
    if g["i"] >= len(g["games"]):
        return _win(uid, g)
    _begin_game(g)
    return _out(g, {"type": "level", "level": g["i"], "msg": ""})


# ---------------------------------------------------------------- azioni
def state(uid):
    out = {"info": info(uid), "active": False}
    g = MINI.get(uid)
    if g:
        if time.time() > g["deadline"] + 1:
            out.update(_fail(uid, g, "⏰ Tempo scaduto."))
            out["info"] = info(uid)
        else:
            out.update({"active": True, "view": _view(g)})
    return out


def start(uid, which="A"):
    if not is_web_sandbox():
        return {"error": "Disponibile solo nell'account di prova.", "code": 403}
    if not _ge("heist"):
        return {"error": "Lo Sdrogo Heist è in manutenzione.", "code": 503}
    which = "B" if which == "B" else "A"
    g = MINI.get(uid)
    if g and time.time() <= g["deadline"] + 1:
        return _out(g)
    w = _wait(uid, which)
    if w == -1:
        return {"error": "Hai già fatto le 3 prove di oggi: torna domani.", "code": 409}
    if w > 0:
        return {"error": "Devi ancora aspettare prima di riprovare.", "code": 409}
    if get_user_coins(uid, uid) < MINI_COST:
        return {"error": f"Servono {MINI_COST} $SDG.", "code": 400}
    _charge(uid, uid, MINI_COST)
    _log(uid)[which].append(time.time())
    now = time.time()
    g = {"uid": uid, "id": which, "games": scheme()[which], "i": 0, "alarms": 0, "deadline": now + MINI_TIME, "t_start": now}
    MINI[uid] = g
    _begin_game(g)
    return _out(g, {"type": "start"})


def reset_waits(uid):
    """Strumento admin: azzera tentativi e attese di oggi."""
    LOG.pop(uid, None)
    return {"info": info(uid)}


def act(uid, a, data):
    g = MINI.get(uid)
    if not g:
        return {"error": "Nessuna mini rapina in corso.", "code": 409}
    now = time.time()
    if now > g["deadline"]:
        return _fail(uid, g, "⏰ Tempo scaduto.")
    if a == "skip":                                            # strumento admin
        return _done(uid, g)
    if a == "retry":
        _begin_game(g)
        return _out(g, {"type": "retry", "msg": "Gioco rigenerato."})
    kind, st = g["kind"], g["st"]
    el = now - g["t_game"]
    if a == "to":
        return _alarm(uid, g, "Tempo del giochino scaduto.")
    if el > st.get("limit", 10) + st.get("show", 0) + 3:        # risposta arrivata troppo tardi
        return _alarm(uid, g, "Troppo lento!")
    if kind in ("face", "cable", "print", "logic") and a == "an":
        try:
            i = int(data.get("i"))
        except (TypeError, ValueError):
            return _out(g)
        if i == st["ans"]:
            return _next(uid, g)
        return _alarm(uid, g, "Risposta sbagliata!")
    if kind == "freq" and a == "fq":
        import math
        try:
            sm = sorted((int(t), float(f)) for t, f in (data.get("s") or []))[:800]
            fin = int(data.get("fin"))
        except (TypeError, ValueError):
            return _out(g)
        ok, run0, prev = False, None, None
        for t, f in sm:
            tgt = st["f0"] + st["amp"] * math.sin(st["w"] * t / 1000.0 + st["ph"])
            good = abs(f - tgt) <= st["tol"] + 0.04
            if good and (prev is not None and t - prev <= 300) and run0 is not None:
                if (t - run0) / 1000.0 >= st["need"] - 0.1:
                    ok = True
                    break
            elif good:
                run0 = t
            else:
                run0 = None
            prev = t
        if ok and fin / 1000.0 <= el + 0.6:
            return _next(uid, g)
        return _alarm(uid, g, "Frequenze non sincronizzate!")
    if kind == "voice" and a == "vt":
        try:
            taps = [int(x) for x in (data.get("taps") or [])][:6]
        except (TypeError, ValueError):
            return _out(g)
        nn = len(st["nodes"])
        ok = len(taps) == nn and all(taps[i + 1] - taps[i] >= 250 for i in range(nn - 1)) and taps[0] >= 0 and taps[-1] / 1000.0 <= el + 0.6
        if ok:
            for k, tp in enumerate(taps):
                if abs(_tri(st["phase0"] + st["speed"] * tp / 1000.0) - st["nodes"][k]) > st["w"] / 2 + 0.01:
                    ok = False
                    break
        if ok:
            return _next(uid, g)
        return _alarm(uid, g, "Impronta vocale non riconosciuta!")
    if kind == "laser" and a == "lz":
        if data.get("hit"):
            return _alarm(uid, g, "Raggio laser! Hai fatto scattare l'allarme.")
        from webheist import _laser_check
        try:
            ev = [[int(x), int(y)] for x, y in (data.get("ev") or [])][:80]
            fin = int(data.get("fin"))
        except (TypeError, ValueError):
            return _out(g)
        if fin / 1000.0 <= el + 0.6 and _laser_check(st["lasers"], ev, fin):
            return _next(uid, g)
        return _alarm(uid, g, "Raggio laser! Hai fatto scattare l'allarme.")
    return _out(g)
