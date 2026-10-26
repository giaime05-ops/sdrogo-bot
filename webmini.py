"""Mini app: DAILY HEIST (mini rapine). Per ora SOLO account admin di prova.
Ogni giorno (mezzanotte italiana) c'è uno schema uguale per tutti: due mini rapine ("Colpo Rapido 1/2") da 3 giochini ciascuna,
scelti a rotazione da un pool di 7 (memoria, matematica, logica, riflessi...). Ogni giochino ha 3 round di difficoltà crescente.
Costo 100 $SDG, premio fisso 300 $SDG, 150 secondi, al 2° allarme si fallisce.
Ogni mini rapina si può giocare 3 volte al giorno, con almeno 2 ore tra una partenza e la successiva.
Il server genera e controlla tutto (risposte giuste, tempi, tracciati)."""
import random
import time

from economy import today_str, record_single_result, banco_take
from handlers.games import _charge
from storage import get_user_coins, add_user_coins, is_web_sandbox
from state import game_enabled as _ge, SYSTEM_FLAGS

MINI_COSTS = (0, 50, 100)           # costo del 1°, 2° e 3° tentativo del giorno: la prima volta è gratis
MINI_PRIZE = 350
MINI_TIME = 150
MINI_MAX_ALARMS = 2
ROUNDS = 3
MINI_ALARM_PEN = 10
MINI_PLAYS = 3
MINI_GAP = 2 * 3600
POOL = ["face", "cable", "print", "freq", "voice", "logic", "laser", "dodge", "simon"]
NAMES = {"face": "Riconoscimento facciale", "cable": "Taglio cavi", "print": "Impronta digitale",
         "freq": "Hacking frequenze", "voice": "Impronta vocale", "logic": "Decifra il codice", "laser": "Corridoio laser", "dodge": "Scatto laser", "simon": "Sequenza di sicurezza"}
TITLES = {"A": "Colpo Rapido 1", "B": "Colpo Rapido 2"}

MINI = {}        # uid -> rapina in corso
LOG = {}         # uid -> {"day", "A": [istanti di partenza], "B": [...]}

BADGE = ["rossa", "blu", "gialla", "verde", "viola", "arancione"]          # "scheda ..."
HATN = ["rosso", "blu", "giallo", "verde", "viola", "arancione"]           # "cappello ..."
CABLES = ["Rosso", "Blu", "Giallo", "Verde", "Viola", "Arancio"]
LASER_SPEED = 24.0
DG_AIR, DG_W = 0.7, 0.22        # durata di salto/abbassamento e larghezza (in secondi) di un raggio che passa


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


def _cost(uid, which):
    n = len(_log(uid)[which])
    return MINI_COSTS[min(n, len(MINI_COSTS) - 1)]


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
        minis.append({"id": w, "title": TITLES[w], "games": [NAMES[k] for k in sc[w]], "used": len(L), "max": MINI_PLAYS, "wait": _wait(uid, w), "cost": _cost(uid, w)})
    return {"cost": MINI_COSTS[0], "costs": list(MINI_COSTS), "prize": MINI_PRIZE, "time": MINI_TIME, "max_alarms": MINI_MAX_ALARMS, "alarm_penalty": MINI_ALARM_PEN,
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
        for _ in range(400):
            if r == 0:                                                 # round 1: tutti diversi, una sola caratteristica da ricordare
                hats, badges = rn.sample(range(6), n), rn.sample(range(6), n)
            else:                                                      # round 2-3: colori che si ripetono, serve ricordare DUE caratteristiche
                hats, badges = [rn.randrange(4) for _ in range(n)], [rn.randrange(4) for _ in range(n)]
            glasses = [rn.random() < 0.5 for _ in range(n)]
            if all(glasses) or not any(glasses):
                continue
            guards = [{"hat": hats[i], "glasses": glasses[i], "badge": badges[i], "skin": rn.randrange(4)} for i in range(n)]
            t = rn.randrange(n)
            if r == 0:
                mode = rn.choice(["badge", "hat", "glass"])
                if mode == "badge":
                    ans, q = t, f"Chi aveva la scheda {BADGE[badges[t]].upper()}?"
                elif mode == "hat":
                    ans, q = t, f"Chi aveva il cappello {HATN[hats[t]].upper()}?"
                else:
                    wo = rn.random() < 0.5
                    cand = [i for i in range(n) if glasses[i] == wo]
                    if len(cand) != 1:
                        continue
                    ans, q = cand[0], "Chi portava gli occhiali?" if wo else "Chi NON portava gli occhiali?"
                break
            mode = rn.choice(["hb", "hg", "bg"])
            gl_txt = "con gli occhiali" if glasses[t] else "senza occhiali"
            if mode == "hb":
                m = [i for i in range(n) if hats[i] == hats[t] and badges[i] == badges[t]]
                q = f"Chi aveva il cappello {HATN[hats[t]].upper()} E la scheda {BADGE[badges[t]].upper()}?"
            elif mode == "hg":
                m = [i for i in range(n) if hats[i] == hats[t] and glasses[i] == glasses[t]]
                q = f"Chi aveva il cappello {HATN[hats[t]].upper()} ed era {gl_txt.upper()}?"
            else:
                m = [i for i in range(n) if badges[i] == badges[t] and glasses[i] == glasses[t]]
                q = f"Chi aveva la scheda {BADGE[badges[t]].upper()} ed era {gl_txt.upper()}?"
            if len(m) == 1:
                ans = t
                break
        return {"guards": guards, "q": q, "ans": ans, "show": (3.0, 3.0, 3.5)[r], "limit": (7, 8, 9)[r]}
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
        return {"lasers": lasers, "speed": LASER_SPEED, "zone": 2.6, "delay": 1.0, "limit": 22}
    if kind == "dodge":                                                          # corsa laser a 3 checkpoint: salta i raggi a terra, abbassati sotto quelli alti
        n, gap0 = (8, 10, 12)[r], (1.25, 1.05, 0.95)[r]
        obs, t = [], 2.4
        for i in range(n):                      # come il dino di Chrome: gli ostacoli si infittiscono
            obs.append([round(t, 2), rn.choice("LH")])
            t += max(0.8, gap0 * (1 - 0.3 * i / n)) + rn.uniform(0.0, 0.2)
        return {"obs": obs, "delay": 1.0, "limit": int(t + 3), "air": DG_AIR, "w": DG_W}
    if kind == "simon":
        n = (4, 5, 6)[r]
        return {"seq": [rn.randrange(4) for _ in range(n)], "show": int(n * 0.8 + 2), "limit": 12}
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
    if g["cost"]:
        record_single_result(uid, uid, False, -g["cost"], game="mini", bet=g["cost"])
    return {"end": {"result": "fail", "msg": reason, "mini": True, "which": g["id"], "paid": g["cost"]}, "balance": get_user_coins(uid, uid), "view": None}


def _win(uid, g):
    MINI.pop(uid, None)
    banco_take(MINI_PRIZE)                                    # il premio esce (se c'è) dal Banco; in prova non tocca nulla
    from economy import loan_cut
    cut = loan_cut(uid, MINI_PRIZE - g["cost"])
    add_user_coins(uid, uid, MINI_PRIZE - cut)
    record_single_result(uid, uid, True, MINI_PRIZE - g["cost"], game="mini", bet=max(1, g["cost"]))
    from economy import announce_web_win
    announce_web_win(uid, "il Daily Heist", MINI_PRIZE, int(time.time() - g["t_start"]))
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
    if not is_web_sandbox() and not SYSTEM_FLAGS.get("NEW_PUBLIC"):
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
    cost = _cost(uid, which)
    if get_user_coins(uid, uid) < cost:
        return {"error": f"Servono {cost} $SDG.", "code": 400}
    if cost:
        _charge(uid, uid, cost)
    _log(uid)[which].append(time.time())
    now = time.time()
    g = {"uid": uid, "id": which, "cost": cost, "games": scheme()[which], "i": 0, "alarms": 0, "deadline": now + MINI_TIME, "t_start": now}
    MINI[uid] = g
    _begin_game(g)
    return _out(g, {"type": "start"})


def reset_waits(uid):
    """Strumento admin: azzera tentativi e attese di oggi (solo account di prova)."""
    if not is_web_sandbox():
        return {"error": "Azione non disponibile.", "code": 403}
    LOG.pop(uid, None)
    return {"info": info(uid)}


def act(uid, a, data):
    g = MINI.get(uid)
    if not g:
        return {"error": "Nessuna mini rapina in corso.", "code": 409}
    now = time.time()
    if now > g["deadline"]:
        return _fail(uid, g, "⏰ Tempo scaduto.")
    if a in ("skip", "retry") and not is_web_sandbox():
        return {"error": "Azione non disponibile.", "code": 403}
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
        if ok and fin / 1000.0 <= el + 2.0:
            return _next(uid, g)
        return _alarm(uid, g, "Frequenze non sincronizzate!")
    if kind == "voice" and a == "vt":
        try:
            taps = [int(x) for x in (data.get("taps") or [])][:6]
        except (TypeError, ValueError):
            return _out(g)
        nn = len(st["nodes"])
        ok = len(taps) == nn and all(taps[i + 1] - taps[i] >= 250 for i in range(nn - 1)) and taps[0] >= 0 and taps[-1] / 1000.0 <= el + 2.0
        if ok:
            for k, tp in enumerate(taps):
                if abs(_tri(st["phase0"] + st["speed"] * tp / 1000.0) - st["nodes"][k]) > st["w"] / 2 + 0.01:
                    ok = False
                    break
        if ok:
            return _next(uid, g)
        return _alarm(uid, g, "Impronta vocale non riconosciuta!")
    if kind == "simon" and a == "sq":
        try:
            got = [int(x) for x in (data.get("s") or [])][:12]
        except (TypeError, ValueError):
            return _out(g)
        if got == st["seq"]:
            return _next(uid, g)
        return _alarm(uid, g, "Sequenza sbagliata!")
    if kind == "dodge" and a == "dg":
        if data.get("hit"):
            return _alarm(uid, g, "Hai toccato un laser!")
        try:
            ev = sorted((int(x), str(y)[:1]) for x, y in (data.get("ev") or []))[:60]
            fin = int(data.get("fin"))
        except (TypeError, ValueError):
            return _out(g)
        if any(k not in ("j", "d") for _, k in ev) or fin / 1000.0 > el + 0.8 or any(ev[i + 1][0] - ev[i][0] < DG_AIR * 1000 - 60 for i in range(len(ev) - 1)):
            return _alarm(uid, g, "Corsa non valida.")

        def state(tt):
            for tm, k in ev:
                if tm / 1000.0 - 0.06 <= tt <= tm / 1000.0 + DG_AIR:
                    return k
            return ""
        for tb, typ in st["obs"]:
            need = "j" if typ == "L" else "d"
            if state(tb + 0.04) != need or state(tb + DG_W - 0.04) != need:
                return _alarm(uid, g, "Hai toccato un laser!")
        return _next(uid, g)
    if kind == "laser" and a == "lz":
        if data.get("hit"):
            return _alarm(uid, g, "Raggio laser! Hai fatto scattare l'allarme.")
        from webheist import _laser_check
        try:
            ev = [[int(x), int(y)] for x, y in (data.get("ev") or [])][:400]
            fin = int(data.get("fin"))
        except (TypeError, ValueError):
            return _out(g)
        if fin / 1000.0 <= el + 2.0 and _laser_check(st["lasers"], ev, fin):
            return _next(uid, g)
        return _alarm(uid, g, "Raggio laser! Hai fatto scattare l'allarme.")
    return _out(g)
