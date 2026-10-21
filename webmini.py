"""Mini app: DAILY HEIST (mini rapine). Per ora SOLO account admin di prova.
Ogni giorno (mezzanotte italiana) c'è uno schema uguale per tutti: due mini rapine ("Colpo Rapido 1/2") da 3 giochini ciascuna,
scelti a rotazione da un pool di 5. Costo 100 $SDG, premio fisso 300 $SDG, 90 secondi, al 2° allarme si fallisce.
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
MINI_TIME = 90
MINI_MAX_ALARMS = 2
MINI_ALARM_PEN = 10
MINI_PLAYS = 3
MINI_GAP = 2 * 3600
POOL = ["face", "cable", "print", "freq", "voice"]
NAMES = {"face": "Riconoscimento facciale", "cable": "Taglio cavi", "print": "Impronta digitale",
         "freq": "Hacking frequenze", "voice": "Impronta vocale"}
TITLES = {"A": "Colpo Rapido 1", "B": "Colpo Rapido 2"}

MINI = {}        # uid -> rapina in corso
LOG = {}         # uid -> {"day", "A": [istanti di partenza], "B": [...]}

BADGE = ["rossa", "blu", "gialla", "verde", "viola", "arancio"]
CABLES = ["Rosso", "Blu", "Giallo", "Verde"]


def scheme(day=None):
    day = day or today_str()
    rng = random.Random("mini-" + day)
    a = rng.sample(POOL, 3)
    rest = [x for x in POOL if x not in a]
    b = rest + [rng.choice(a)]
    rng.shuffle(b)
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
def _gen(kind):
    r = random
    if kind == "face":
        badges = list(range(6)); r.shuffle(badges)
        glasses = [True] * 6; glasses[r.randrange(6)] = False
        guards = [{"hat": r.randrange(6), "glasses": glasses[i], "badge": badges[i], "skin": r.randrange(4)} for i in range(6)]
        if r.random() < 0.5:
            col = r.randrange(6)
            ans = badges.index(col)
            q = f"Quale guardia aveva la scheda {BADGE[col].upper()}?"
        else:
            ans = glasses.index(False)
            q = "Quale guardia NON portava gli occhiali?"
        return {"guards": guards, "q": q, "ans": ans, "show": 2.8, "limit": 9}
    if kind == "cable":
        order = CABLES[:]; r.shuffle(order)
        mode = r.choice(["max", "min", "eq"])
        if mode == "max":
            q, ans = "Taglia il cavo il cui nome ha PIÙ lettere", order.index("Giallo")
        elif mode == "min":
            q, ans = "Taglia il cavo il cui nome ha MENO lettere", order.index("Blu")
        else:
            pos = r.randint(1, 4)
            while True:
                a, b = r.randint(2, 6), r.randint(2, 6)
                if 1 <= a + b - pos <= 7:
                    break
            q, ans = f"Taglia il cavo in posizione {a} + {b} - {a + b - pos} (da sinistra)", pos - 1
        return {"cables": order, "q": q, "ans": ans, "limit": 7}
    if kind == "print":
        t = [[1 if r.random() < 0.45 else 0 for _ in range(5)] for _ in range(5)]
        cands = []
        for _ in range(2):
            m = [row[:] for row in t]
            for _ in range(2):
                i, j = r.randrange(5), r.randrange(5)
                m[i][j] ^= 1
            if m == t:
                m[0][0] ^= 1
            cands.append(m)
        ans = r.randrange(3)
        cands.insert(ans, [row[:] for row in t])
        return {"target": t, "cands": cands, "ans": ans, "limit": 10}
    if kind == "freq":
        return {"f0": round(r.uniform(2.0, 4.0), 2), "amp": 0.5, "w": 0.6, "ph": round(r.uniform(0, 6.28), 2), "need": 3.0, "tol": 0.18, "limit": 16}
    # voice
    cs = []
    while len(cs) < 3:
        c = round(r.uniform(0.15, 0.85), 2)
        if all(abs(c - x) >= 0.22 for x in cs):
            cs.append(c)
    return {"nodes": cs, "w": 0.12, "speed": round(r.uniform(0.5, 0.65), 2), "phase0": round(r.random(), 3), "limit": 14}


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
    g["st"] = _gen(kind)
    g["t_game"] = time.time()
    g["kind"] = kind


def _view(g):
    return {"level": g["i"] + 1, "total": len(g["games"]), "name": NAMES[g["kind"]], "kind": g["kind"], "alarms": g["alarms"],
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
        return _fail(uid, g, reason + " Secondo allarme: la rapina è fallita.")
    if _left(g) <= 0:
        return _fail(uid, g, reason + " Tempo scaduto.")
    _begin_game(g)
    return _out(g, {"type": "alarm", "msg": "ALLARME! " + reason})


def _done(uid, g):
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
    if kind in ("face", "cable", "print") and a == "an":
        try:
            i = int(data.get("i"))
        except (TypeError, ValueError):
            return _out(g)
        if i == st["ans"]:
            return _done(uid, g)
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
            return _done(uid, g)
        return _alarm(uid, g, "Frequenze non sincronizzate!")
    if kind == "voice" and a == "vt":
        try:
            taps = [int(x) for x in (data.get("taps") or [])][:3]
        except (TypeError, ValueError):
            return _out(g)
        ok = len(taps) == 3 and all(taps[i + 1] - taps[i] >= 250 for i in range(2)) and taps[0] >= 0 and taps[-1] / 1000.0 <= el + 0.6
        if ok:
            for k, tp in enumerate(taps):
                if abs(_tri(st["phase0"] + st["speed"] * tp / 1000.0) - st["nodes"][k]) > st["w"] / 2 + 0.01:
                    ok = False
                    break
        if ok:
            return _done(uid, g)
        return _alarm(uid, g, "Impronta vocale non riconosciuta!")
    return _out(g)
