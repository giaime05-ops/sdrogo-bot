"""Mini app: Sdrogo Heist in 2D (per ora SOLO account admin di prova).
Stessi livelli, stessi generatori e stesse regole della rapina nel bot (handlers/heist.py), ma giocati con dei
giochini nella mini app. Il server decide tutto (sequenze, turni delle guardie, codice del caveau, premio).
Nel profilo di prova il Banco vero non viene mai toccato: il premio si calcola dal Banco ma non lo si preleva."""
import math
import random
import time

from economy import fmt, inv, banco_balance, follia_active, record_single_result, quest_progress
from handlers import heist as H
from handlers.games import _charge
from storage import get_user_coins, add_user_coins, save_db, is_web_sandbox
from state import game_enabled as _ge

WEB_HEIST = {}
WEB_COST = 350                  # la rapina principale della mini app costa di più di quella del bot
WEB_TIME = 480                  # otto minuti: i livelli sono 7 e più lunghi di abilità
KINDS = ["seq", "laser", "hack", "lock", "vault", "circuit", "chase"]          # livelli 1..7
NAMES = ["Centralino", "Corridoio laser", "Hacking", "Serratura", "Caveau", "Bypass circuiti", "Inseguimento"]
WEB_SHARE = 0.25                # con 7 livelli la rapina principale paga il 25% del Banco
TOTAL = len(KINDS)
SEQ_SHOW_PER = 0.8
HACK_WEB_ROUNDS = (3,)          # un solo terminale (verificato con BFS: si può sempre risolvere)
LASER_X = [14, 30, 46, 62, 78]  # posizioni dei laser lungo il corridoio (0..100)
LASER_SPEED = 24.0              # unità al secondo mentre tieni premuto
LASER_ZONE = 1.5                # il server è un po' più permissivo del telefono (1,8)
LOCK_ROUNDS = [(0.20, 0.50), (0.14, 0.70), (0.09, 0.90)]            # (larghezza zona verde, cicli al secondo)


def _left(g):
    return max(0, int(g["deadline"] - time.time()))


def info(uid):
    return {"cost": WEB_COST, "time": WEB_TIME, "alarm_penalty": H.ALARM_PENALTY, "max_alarms": H.MAX_ALARMS,
            "share": int(WEB_SHARE * 100), "share_follia": int(H.SHARE_FOLLIA * 100), "banco_protect": H.BANCO_PROTECT,
            "prize_fixed": H.PRIZE_FIXED, "balance": get_user_coins(uid, uid), "total": TOTAL, "names": NAMES,
            "levels": [["Centralino", "Memorizza le sequenze di colori"], ["Corridoio laser", "Tieni premuto e fermati quando il raggio è acceso"],
                       ["Hacking", "Raggiungi il codice con mosse contate"], ["Serratura", "Rilascia quando l'indicatore è nel verde"],
                       ["Caveau", "Indovina il codice a 4 cifre"], ["Bypass circuiti", "Guida la sonda nel circuito senza toccare pareti e scariche"],
                       ["Inseguimento", "Schiva i posti di blocco e scegli il bivio giusto"]]}


# ---------------------------------------------------------------- viste
def _base(g):
    return {"level": g["level"], "alarms": g["alarms"], "max_alarms": H.MAX_ALARMS, "left": _left(g), "phase": g["phase"]}


def _view(g):
    v = _base(g); st = g["st"]; kind = KINDS[g["level"] - 1]; v["kind"] = kind; v["total"] = TOTAL; v["name"] = NAMES[g["level"] - 1]
    if kind == "seq":
        v.update({"r": st.get("r", 0), "rounds": len(H.SEQ_ROUNDS), "colors": len(H.SEQ_SYMBOLS)})
        if g["phase"] in ("show", "input") and "seq" in st:
            v["seq"] = st["seq"]; v["show_in"] = max(0.0, st.get("show_from", 0) - time.time()); v["show_secs"] = st.get("secs", 0)
            v["pos"] = st.get("pos", 0)
    elif kind == "laser":
        L = st.get("L")
        if g["phase"] == "laser" and L:
            v.update({"lasers": L["lasers"], "speed": LASER_SPEED, "zone": 1.8, "delay": max(0.0, L["t0"] - time.time()), "length": 100})
    elif kind == "hack":
        h = st["h"]
        v.update({"r": st["r"], "rounds": len(HACK_WEB_ROUNDS), "start": h["start"], "target": h["target"], "val": h["val"], "left_moves": h["left"],
                  "min_moves": h["min"], "ops": [[o[0], o[1]] for o in h["ops"]]})
    elif kind == "lock":
        k = st["k"]
        v.update({"r": st["r"], "rounds": len(LOCK_ROUNDS), "c": k["c"], "w": k["w"], "speed": k["speed"], "phase0": k["phase0"]})
    elif kind == "vault":
        vt = st["v"]
        v.update({"hint": [vt["hint"], vt["secret"][vt["hint"]]], "tries": vt["tries"], "max_tries": H.VAULT_TRIES,
                  "hist": [[gs, b, c] for gs, b, c in vt["hist"]], "vault_left": max(0, int(H.VAULT_TIME - (time.time() - vt["t0"])))})
    elif kind == "circuit":
        c = st.get("c")
        if g["phase"] == "circuit" and c:
            v.update({"n": c["n"], "open": c["open"], "start": c["start"], "exit": c["exit"], "pins": c["pins"], "hz": c["hz"], "r": st["r"],
                      "rounds": CIRC_SECTORS, "grace": CIRC_GRACE, "delay": max(0.0, c["t0"] - time.time())})
        else:
            v.update({"r": st.get("r", 0), "rounds": CIRC_SECTORS})
    elif kind == "chase":
        c = st.get("c")
        if g["phase"] == "chase" and c:
            v.update({"D": CH_D, "v0": CH_V0, "acc": CH_A, "vmax": CH_VMAX, "slow": CH_SLOW, "obs": c["obs"], "forks": c["forks"], "segs": c["segs"],
                      "delay": max(0.0, c["t0"] - time.time())})
    elif kind == "flight":
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
    record_single_result(uid, uid, False, -WEB_COST, game="heist", bet=WEB_COST)
    return {"end": {"result": "fail", "msg": reason, "level": g["level"]}, "balance": get_user_coins(uid, uid), "view": None}


def _win(uid, g, qmsg=""):
    WEB_HEIST.pop(uid, None)
    bal = banco_balance()
    prize = H.PRIZE_FIXED if bal < H.BANCO_PROTECT else int(bal * (H.SHARE_FOLLIA if follia_active() else WEB_SHARE))
    prize = max(prize, H.PRIZE_FIXED)
    add_user_coins(uid, uid, prize)
    record_single_result(uid, uid, True, prize - WEB_COST, game="heist", bet=WEB_COST)
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
    if done >= TOTAL:
        return _win(uid, g, qmsg)
    g["level"] += 1
    _present(g)
    return _out(g, {"type": "level", "level": done, "msg": qmsg or ""})


# ---------------------------------------------------------------- livelli
def _present(g):
    kind = KINDS[g["level"] - 1]
    g["st"] = {"r": 0}
    g["phase"] = "brief"
    if kind == "hack":
        _hack_new(g)
    elif kind == "lock":
        _lock_new(g)
    elif kind == "vault":
        _vault_new(g)
    elif kind == "flight":
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


def _laser_begin(g, delay):
    lasers = []
    for x in LASER_X:
        per = round(random.uniform(1.25, 2.0), 2)
        lasers.append({"x": x + random.randint(-3, 3), "p": per, "ph": round(random.uniform(0, per), 2), "duty": 0.5})
    g["st"]["L"] = {"lasers": lasers, "t0": time.time() + delay}
    g["phase"] = "laser"


def _laser_check(lasers, ev, fin_ms):
    """Rigioca la partita del telefono: ritorna True se il giocatore arriva alla fine senza toccare un raggio acceso."""
    ev = sorted((int(a), int(b)) for a, b in ev)
    x, t, dt, i = 0.0, 0.0, 0.005, 0
    end = fin_ms / 1000.0
    while t <= end + 0.02 and x < 100:
        hold = any(a / 1000.0 <= t < b / 1000.0 for a, b in ev)
        if hold:
            x += LASER_SPEED * dt
            for L in lasers:
                if abs(x - L["x"]) < LASER_ZONE and ((t + L["ph"]) % L["p"]) < L["p"] * L["duty"]:
                    return False
        t += dt
    return x >= 100


# ---------------------------------------------------------------- Bypass circuiti (livello 6)
# Labirinto di piastrelle: si muove la sonda con la croce di tasti sotto lo schermo (niente dito sopra il disegno).
# Si attivano i perni numerati IN ORDINE per riaprire la serratura, poi si raggiunge l'uscita. Le scariche pattugliano un
# corridoio avanti e indietro, a tempo: il server rigioca le mosse e controlla muri, ordine dei perni e scariche.
CIRC_STAGES = [(7, 2, 2, 0.70), (9, 3, 3, 0.60), (11, 3, 5, 0.52)]   # (lato, perni, scariche, secondi per piastrella)
CIRC_SECTORS = len(CIRC_STAGES)
CIRC_GRACE = 0.10               # appena entrato in una piastrella la scarica non conta per 0,1 s (il server è più permissivo)
CIRC_SLACK = 0.08
DIRS = [(0, -1), (1, 0), (0, 1), (-1, 0)]                               # su, destra, giù, sinistra


def _maze(n):
    op = [[0] * n for _ in range(n)]
    seen, stack = {(0, 0)}, [(0, 0)]
    while stack:
        x, y = stack[-1]
        nb = [(i, x + dx, y + dy) for i, (dx, dy) in enumerate(DIRS) if 0 <= x + dx < n and 0 <= y + dy < n and (x + dx, y + dy) not in seen]
        if not nb:
            stack.pop()
            continue
        i, nx, ny = random.choice(nb)
        op[y][x] |= 1 << i
        op[ny][nx] |= 1 << ((i + 2) % 4)
        seen.add((nx, ny))
        stack.append((nx, ny))
    for _ in range(max(n, n * n // 3)):                                 # molti muri in meno: stanze e giri, non solo corridoi
        x, y, i = random.randrange(n), random.randrange(n), random.randrange(4)
        nx, ny = x + DIRS[i][0], y + DIRS[i][1]
        if 0 <= nx < n and 0 <= ny < n:
            op[y][x] |= 1 << i
            op[ny][nx] |= 1 << ((i + 2) % 4)
    return op


def _patrols(op, n, k, banned):
    """Scariche che pattugliano un tratto dritto di 3 piastrelle (tipo fantasmini): sempre aggirabili o da aspettare."""
    res, used = [], set(banned)
    for _ in range(300):
        if len(res) >= k:
            break
        x, y, d = random.randrange(n), random.randrange(n), random.randrange(4)
        cells = [(x, y)]
        while len(cells) < 3:
            cx, cy = cells[-1]
            if not (op[cy][cx] >> d) & 1:
                break
            cells.append((cx + DIRS[d][0], cy + DIRS[d][1]))
        if len(cells) == 3 and not any(c in used for c in cells):
            res.append(cells)
            used.update(cells)
            for cx, cy in cells:                                        # niente due scariche vicine: lasciano sempre un passaggio
                used.update({(cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)})
    return res


def _hz_cell(h, t):
    n = len(h["cells"])
    cyc = 2 * (n - 1)
    m = int(max(0.0, t) / h["T"]) % cyc
    return h["cells"][m if m < n else cyc - m]


def _circ_solvable(c, limit=60.0):
    """Cerca (con attese) un percorso che raccoglie i perni in ordine e arriva all'uscita senza mai incrociare una scarica, con
    ampi margini: il labirinto è accettato solo se si può fare. Ritorna il tempo minimo oppure None."""
    DT = 0.2
    op, pins, ex = c["open"], [tuple(p) for p in c["pins"]], tuple(c["exit"])
    start = (tuple(c["start"]), 0, 0)
    seen, cur, k = {start}, [start], 0
    while cur and k * DT < limit:
        nxt = []
        k += 1
        t = k * DT
        bad = {tuple(_hz_cell(h, t + o)) for h in c["hz"] for o in (-0.25, 0.0, 0.25)}
        for pos, got, _ in cur:
            for d in (None, 0, 1, 2, 3):
                if d is None:
                    np_, ng = pos, got
                else:
                    x, y = pos
                    if not (op[y][x] >> d) & 1:
                        continue
                    np_ = (x + DIRS[d][0], y + DIRS[d][1])
                    ng = got + 1 if got < len(pins) and np_ == pins[got] else got
                if np_ in bad:
                    continue
                key = (np_, ng, k)
                if key in seen:
                    continue
                if np_ == ex and ng == len(pins):
                    return t
                seen.add(key)
                nxt.append(key)
        cur = nxt
    return None


def _circ_begin(g, delay):
    r = g["st"].get("r", 0)
    n, npins, nhz, T = CIRC_STAGES[min(r, CIRC_SECTORS - 1)]
    for _try in range(25):
        op = _maze(n)
        sx, sy = random.choice([0, n - 1]), random.choice([0, n - 1])
        start, exit_ = (sx, sy), (n - 1 - sx, n - 1 - sy)
        cells = [(x, y) for x in range(n) for y in range(n) if (x, y) not in (start, exit_)]
        pins = []
        for _ in range(300):
            pins = random.sample(cells, npins)
            if all(abs(a[0] - b[0]) + abs(a[1] - b[1]) >= 3 for i, a in enumerate(pins) for b in pins[i + 1:]):
                break
        hz = [{"cells": [list(c) for c in cs], "T": T} for cs in _patrols(op, n, nhz, [start, exit_] + pins)]
        c = {"n": n, "open": op, "start": list(start), "exit": list(exit_), "pins": [list(p) for p in pins], "hz": hz,
             "t0": time.time() + delay}
        tmin = _circ_solvable(c)
        if tmin is not None and tmin <= 12 + n * 3.2:
            break
    g["st"]["c"] = c
    g["phase"] = "circuit"


def _circ_check(c, moves, fin_ms):
    """Rigioca le mosse: nessun muro attraversato, perni nell'ordine giusto, uscita raggiunta, nessuna scarica incrociata."""
    try:
        mv = sorted((int(a), int(b)) for a, b in moves)[:400]
        fin = int(fin_ms) / 1000.0
    except (TypeError, ValueError):
        return False
    pos, t_in, pins, stay, prev = list(c["start"]), 0.0, 0, [], -1000
    for tm, d in mv:
        if d not in (0, 1, 2, 3) or tm < 0 or tm - prev < 70:
            return False
        prev = tm
        x, y = pos
        if not (c["open"][y][x] >> d) & 1:
            return False
        stay.append((tuple(pos), t_in, tm / 1000.0))
        pos = [x + DIRS[d][0], y + DIRS[d][1]]
        t_in = tm / 1000.0
        if pins < len(c["pins"]) and pos == c["pins"][pins]:
            pins += 1
    if fin < t_in:
        return False
    stay.append((tuple(pos), t_in, fin))
    if pos != c["exit"] or pins < len(c["pins"]):
        return False
    for cell, a, b in stay:
        for h in c["hz"]:
            T = h["T"]
            for k in range(int(max(0.0, a) / T), int(b / T) + 1):
                if tuple(_hz_cell(h, k * T + T / 2)) != cell:
                    continue
                if min(b, (k + 1) * T) - max(a + CIRC_GRACE, k * T) > CIRC_SLACK:
                    return False
    return True


# ---------------------------------------------------------------- Fuga (livello 7)
# Corsa a DISTANZA: il furgone parte lento e accelera da solo; cambi corsia. Ostacoli che bocciano la fuga (posti di blocco,
# auto contromano, muri, faro dell'elicottero) e ostacoli che fanno solo rallentare (olio, buche). Ai bivi leggi il cartello:
# l'elicottero sorveglia un percorso, prendi l'altro. Il server rigioca corsie e velocità e verifica il tempo di arrivo.
CH_D = 4300.0                   # metri totali
CH_V0, CH_A, CH_VMAX = 26.0, 0.9, 62.0
CH_SLOW = 0.5                   # olio, buche, fango e coni dimezzano la velocità
CH_H_SRV = 0.6                  # mezza lunghezza del furgone per il server: il telefono usa 2,0 (margine per gli scarti di tempo)
CH_FINISH_TOL = 0.7
LETHAL = ("block", "car", "wall", "light", "rock", "wreck")
CH_LEN = {"block": 3.0, "car": 5.0, "wall": 3.0, "light": 16.0, "oil": 4.0, "pit": 3.0, "rock": 3.0, "wreck": 5.0, "mud": 6.0, "cone": 3.0}
# Percorsi ai bivi: nome, ambientazione che si incontra dopo, simboli di pericolo possibili (compaiono SOLO accanto alla strada pericolosa)
ROUTES = {
    "tunnel": ("TUNNEL", "tunnel", ["🚔", "🚨"]),
    "overpass": ("SOPRAELEVATA", "bridge", ["🚁", "🚔"]),
    "dirt": ("STERRATO", "dirt", ["🚧", "🚨"]),
    "city": ("CENTRO CITTÀ", "city", ["🚔", "🚧", "🚨"]),
    "coast": ("LUNGOMARE", "coast", ["🚁", "🚓"]),
}
ENV_POOL = {                                                            # ostacoli tipici di ogni ambientazione
    "highway": ["oil", "pit", "block", "car", "wall", "oil", "pit"],
    "tunnel": ["oil", "pit", "wreck", "wall", "block", "wreck"],
    "bridge": ["cone", "cone", "block", "car", "wall", "oil"],
    "dirt": ["rock", "rock", "mud", "mud", "pit", "wall"],
    "city": ["car", "block", "oil", "wall", "cone", "car"],
    "coast": ["oil", "car", "block", "pit", "cone", "wall"],
}


def _ch_v(d):
    return min(CH_VMAX, math.sqrt(CH_V0 * CH_V0 + 2 * CH_A * d))


def _chase_feasible(obs, forks):
    """Esiste una sequenza di corsie che evita tutti gli ostacoli letali (passi da 4 m, margine largo)?"""
    step, H = 4.0, 2.2
    cur = {1}
    n = int(CH_D / step) + 1
    for i in range(1, n + 1):
        p = i * step
        ok = set()
        for ln in {x for l in cur for x in (l - 1, l, l + 1) if 0 <= x <= 2}:
            bad = any((m >> ln) & 1 and k in LETHAL and p + H > d and p - step - H < d + L for d, m, k, L in obs if d < p + 10 and d + L > p - step - 10)
            for f in forks:
                if p - step < f["d"] <= p and ln != f["safe"]:
                    bad = True
            if not bad:
                ok.add(ln)
        cur = ok
        if not cur:
            return False
    return True


def _chase_begin(g, delay):
    for _try in range(30):
        fk = [CH_D * 0.2, CH_D * 0.45, CH_D * 0.7]
        forks, segs = [], []
        for f in fk:
            a, b = random.sample(list(ROUTES), 2)                      # due strade diverse a caso
            side = random.choice([0, 2])                               # corsia della strada SICURA (0 = sinistra, 2 = destra)
            safe, danger = (a, b)
            names = {side: safe, 2 - side: danger}
            emo = random.choice(ROUTES[danger][2])
            forks.append({"d": round(f, 1), "safe": side, "L": {"r": names[0], "name": ROUTES[names[0]][0], "emo": emo if names[0] == danger else ""},
                          "R": {"r": names[2], "name": ROUTES[names[2]][0], "emo": emo if names[2] == danger else ""}})
            segs.append([round(f + 25, 1), round(f + 25 + 650, 1), ROUTES[safe][1]])

        def env_at(d):
            for a0, b0, e in segs:
                if a0 <= d < b0:
                    return e
            return "highway"
        obs, d = [], 110.0
        while d < CH_D - 260:
            d += random.uniform(0.85, 1.4) * _ch_v(d)
            if d >= CH_D - 260:
                break
            if any(abs(d - f) < 2.2 * _ch_v(f) for f in fk):
                continue
            pool = list(ENV_POOL[env_at(d)])
            if d / CH_D > 0.35 and env_at(d) in ("highway", "bridge", "coast"):
                pool.append("light")
            kind = random.choice(pool)
            if kind == "wall":
                free = random.randrange(3)
                obs.append([round(d, 1), 7 & ~(1 << free), "wall", CH_LEN["wall"]])
                continue
            lanes = random.sample(range(3), 2 if random.random() < 0.3 else 1)
            for i, ln in enumerate(lanes):
                kk = kind if i == 0 else random.choice(ENV_POOL[env_at(d)])
                if kk == "wall":
                    kk = "block"
                obs.append([round(d + i * 0.1, 1), 1 << ln, kk, CH_LEN[kk]])
        obs.sort()
        if _chase_feasible(obs, forks):
            break
    g["st"]["c"] = {"obs": obs, "forks": forks, "segs": segs, "t0": time.time() + delay}
    g["phase"] = "chase"


def _chase_check(c, ev, sl, fin_ms):
    """Rigioca corsie e velocità. ev = cambi di corsia [ms, corsia]; sl = rallentamenti dichiarati dal telefono [ms, indice
    ostacolo] (il server li verifica e pretende anche quelli che non ha potuto evitare), così posizioni e tempi restano uguali."""
    try:
        ev = sorted((int(a), int(b)) for a, b in ev)[:260]
        sl = sorted((int(a), int(b)) for a, b in sl)[:80]
        fin = int(fin_ms) / 1000.0
    except (TypeError, ValueError):
        return False
    if any(l not in (0, 1, 2) or t < 0 for t, l in ev) or any(ev[i + 1][0] - ev[i][0] < 60 for i in range(len(ev) - 1)):
        return False
    obs, forks = c["obs"], c["forks"]
    if any(i < 0 or i >= len(obs) or obs[i][2] in LETHAL for _, i in sl) or len({i for _, i in sl}) != len(sl):
        return False
    dt, n, p, v, lane, plane, ei, si, fi = 0.02, 0, 0.0, CH_V0, 1, 1, 0, 0, 0
    claimed, strict = {i for _, i in sl}, set()
    while p < CH_D:
        tms = n * 20                                                    # tempo a passi interi: identico a quello del telefono
        while ei < len(ev) and ev[ei][0] <= tms:
            plane, lane = lane, ev[ei][1]
            ei += 1
        while si < len(sl) and sl[si][0] <= tms:
            d, mask, _k, ln = obs[sl[si][1]]
            if not (p + 3.0 > d and p - 3.0 < d + ln and ((mask >> lane) & 1 or (mask >> plane) & 1)):
                return False
            v = max(CH_V0, v * CH_SLOW)
            si += 1
        v = min(CH_VMAX, v + CH_A * dt)
        p += v * dt
        n += 1
        if n > 20000:
            return False
        for i, (d, mask, kind, ln) in enumerate(obs):
            if d > p + 5:
                break
            if p + CH_H_SRV > d and p - CH_H_SRV < d + ln and (mask >> lane) & 1:
                if kind in LETHAL:
                    return False
                strict.add(i)
        while fi < len(forks) and p >= forks[fi]["d"]:
            if lane != forks[fi]["safe"]:
                return False
            fi += 1
    return strict <= claimed and abs(n * dt - fin) <= CH_FINISH_TOL


def _tri(u):
    f = u - int(u)
    return 1.0 - abs(2.0 * f - 1.0)


def _lock_new(g):
    w, sp = LOCK_ROUNDS[min(g["st"].get("r", 0), len(LOCK_ROUNDS) - 1)]
    g["st"]["k"] = {"c": round(random.uniform(0.25 + w / 2, 0.75 - w / 2), 3), "w": w, "speed": sp, "phase0": round(random.random(), 3), "t0": time.time()}
    g["phase"] = "lock"


def _hack_new(g):
    h = H.make_hack(HACK_WEB_ROUNDS[g["st"]["r"]])
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


def start(uid, lvl=1):
    if not is_web_sandbox():
        return {"error": "Disponibile solo nell'account di prova.", "code": 403}
    if not _ge("heist"):
        return {"error": "Lo Sdrogo Heist è in manutenzione.", "code": 503}
    g = WEB_HEIST.get(uid)
    if g and time.time() <= g["deadline"] + 1:
        return _out(g)
    if get_user_coins(uid, uid) < WEB_COST:
        return {"error": f"Servono {WEB_COST} $SDG per il pass.", "code": 400}
    _charge(uid, uid, WEB_COST)
    now = time.time()
    try:
        lvl = max(1, min(TOTAL, int(lvl)))                   # strumento admin: si può partire da un livello qualsiasi
    except (TypeError, ValueError):
        lvl = 1
    g = {"uid": uid, "level": lvl, "deadline": now + WEB_TIME, "alarms": 0, "t_start": now, "phase": "brief", "st": {"r": 0}}
    WEB_HEIST[uid] = g
    if lvl > 1:
        _present(g)
    return _out(g, {"type": "start"})


def act(uid, a, data):
    g = WEB_HEIST.get(uid)
    if not g:
        return {"error": "Nessuna rapina in corso.", "code": 409}
    if time.time() > g["deadline"]:
        return _fail(uid, g, "⏰ Tempo scaduto: la polizia è arrivata.")
    lvl, phase, st, now = g["level"], g["phase"], g["st"], time.time()

    kind = KINDS[lvl - 1]
    if a == "retry":                                           # strumenti admin: riprova il livello da capo / salta al prossimo
        g["alarms"] = 0
        g["deadline"] = max(g["deadline"], now + 180)
        _present(g)
        return _out(g, {"type": "retry", "msg": "Livello ricominciato (strumento admin)."})
    if a == "skip":
        if lvl >= TOTAL:
            return _out(g, {"type": "ok", "msg": "Questo è l'ultimo livello: non c'è un successivo da saltare."})
        g["level"] += 1
        g["alarms"] = 0
        g["deadline"] = max(g["deadline"], now + 180)
        _present(g)
        return _out(g, {"type": "level", "level": lvl, "msg": "Livello saltato (strumento admin)."})
    if a == "rd":                                              # "comincia" dei livelli con la presentazione
        if phase != "brief":
            return _out(g)
        if kind == "seq":
            _seq_begin(g, True)
        elif kind == "laser":
            _laser_begin(g, 0.9)
        elif kind == "circuit":
            _circ_begin(g, 0.9)
        elif kind == "chase":
            _chase_begin(g, 0.9)
        return _out(g)

    if kind == "seq" and a == "sq" and phase == "show":
        if now < st.get("input_at", 0):
            return _out(g)
        g["phase"] = phase = "input"
    if kind == "seq" and a == "sq" and phase == "input":
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

    if kind == "laser" and phase == "laser" and a == "lz":
        L = st["L"]
        if data.get("hit"):
            r = _alarm(uid, g, "Raggio laser! Hai fatto scattare l'allarme.")
            if r:
                return r
            _laser_begin(g, 1.4)
            return _out(g, {"type": "alarm", "msg": "ALLARME! I raggi si sono riavviati."})
        try:
            ev = [[int(x), int(y)] for x, y in (data.get("ev") or [])][:80]
            fin = int(data.get("fin"))
        except (TypeError, ValueError):
            return _out(g)
        ok = (fin / 1000.0 <= (now - L["t0"]) + 0.4) and _laser_check(L["lasers"], ev, fin)
        if ok:
            return _level_done(uid, g)
        r = _alarm(uid, g, "Raggio laser! Hai fatto scattare l'allarme.")
        if r:
            return r
        _laser_begin(g, 1.4)
        return _out(g, {"type": "alarm", "msg": "ALLARME! I raggi si sono riavviati."})

    if kind == "circuit" and phase == "circuit" and a == "ci":
        c = st["c"]
        if data.get("hit"):
            r = _alarm(uid, g, "Scarica di tensione! La sonda è andata in corto.")
            if r:
                return r
            _circ_begin(g, 1.4)
            return _out(g, {"type": "alarm", "msg": "ALLARME! Il circuito si è riavviato."})
        try:
            fin = int(data.get("fin"))
        except (TypeError, ValueError):
            return _out(g)
        if fin / 1000.0 <= (now - c["t0"]) + 0.5 and _circ_check(c, data.get("mv") or [], fin):
            st["r"] += 1
            if st["r"] >= CIRC_SECTORS:
                return _level_done(uid, g)
            _circ_begin(g, 1.2)
            return _out(g, {"type": "ok", "msg": "Serratura riaperta! Prossimo settore, più grande…"})
        r = _alarm(uid, g, "Mosse non valide: muro, perni fuori ordine o scarica!")
        if r:
            return r
        _circ_begin(g, 1.4)
        return _out(g, {"type": "alarm", "msg": "ALLARME! Il settore riparte dall'inizio."})

    if kind == "chase" and phase == "chase" and a == "ch":
        c = st["c"]
        if data.get("hit"):
            r = _alarm(uid, g, "Impatto! Il furgone è finito fuori strada.")
            if r:
                return r
            _chase_begin(g, 1.6)
            return _out(g, {"type": "alarm", "msg": "ALLARME! L'inseguimento riparte."})
        try:
            ev = [[int(x), int(y)] for x, y in (data.get("ev") or [])][:260]
            sl = [[int(x), int(y)] for x, y in (data.get("sl") or [])][:80]
            fin = int(data.get("fin"))
        except (TypeError, ValueError):
            return _out(g)
        if fin / 1000.0 <= (now - c["t0"]) + 0.5 and _chase_check(c, ev, sl, fin):
            return _level_done(uid, g)
        r = _alarm(uid, g, "Impatto o bivio sbagliato!")
        if r:
            return r
        _chase_begin(g, 1.6)
        return _out(g, {"type": "alarm", "msg": "ALLARME! L'inseguimento riparte."})

    if kind == "lock" and phase == "lock" and a == "lk":
        k = st["k"]
        try:
            d = int(data.get("d")) / 1000.0
        except (TypeError, ValueError):
            return _out(g)
        if d < 0.12 or d > 12 or d > (now - k["t0"]) + 0.5:
            return _out(g)
        p = _tri(k["phase0"] + k["speed"] * d)
        if abs(p - k["c"]) <= k["w"] / 2:
            st["r"] += 1
            if st["r"] >= len(LOCK_ROUNDS):
                return _level_done(uid, g)
            _lock_new(g)
            return _out(g, {"type": "ok", "msg": "Scatto! Prossimo perno, zona più stretta…"})
        r = _alarm(uid, g, "Hai bloccato il perno fuori posto!")
        if r:
            return r
        _lock_new(g)
        return _out(g, {"type": "alarm", "msg": "ALLARME! La serratura si è riarmata."})

    if kind == "hack" and phase == "hack":
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
            if st["r"] >= len(HACK_WEB_ROUNDS):
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

    if kind == "vault" and phase == "vault" and a == "vs":
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

    if kind == "flight" and phase == "flight" and a == "ft":
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
