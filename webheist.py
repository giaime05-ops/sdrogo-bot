"""Mini app: Sdrogo Heist in 2D (per ora SOLO account admin di prova).
Stessi livelli, stessi generatori e stesse regole della rapina nel bot (handlers/heist.py), ma giocati con dei
giochini nella mini app. Il server decide tutto (sequenze, turni delle guardie, codice del caveau, premio).
Nel profilo di prova il Banco vero non viene mai toccato: il premio si calcola dal Banco ma non lo si preleva."""
import random
import time

from economy import fmt, inv, banco_balance, follia_active, record_single_result, quest_progress
from handlers import heist as H
from handlers.games import _charge
from storage import get_user_coins, add_user_coins, save_db, is_web_sandbox
from state import game_enabled as _ge

WEB_HEIST = {}
WEB_COST = 350                  # la rapina principale della mini app costa di più di quella del bot
WEB_TIME = 360                  # un minuto in più: i livelli sono 6 e più di abilità
KINDS = ["seq", "laser", "hack", "lock", "vault", "flight"]          # livelli 1..6
NAMES = ["Centralino", "Corridoio laser", "Hacking", "Serratura", "Caveau", "Fuga"]
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
            "share": int(H.SHARE * 100), "share_follia": int(H.SHARE_FOLLIA * 100), "banco_protect": H.BANCO_PROTECT,
            "prize_fixed": H.PRIZE_FIXED, "balance": get_user_coins(uid, uid), "total": TOTAL, "names": NAMES,
            "levels": [["Centralino", "Memorizza le sequenze di colori"], ["Corridoio laser", "Tieni premuto e fermati quando il raggio è acceso"],
                       ["Hacking", "Raggiungi il codice con mosse contate"], ["Serratura", "Rilascia quando l'indicatore è nel verde"],
                       ["Caveau", "Indovina il codice a 4 cifre"], ["Fuga", "Scegli il mezzo giusto leggendo i rapporti"]]}


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
    return {"end": {"result": "fail", "msg": reason}, "balance": get_user_coins(uid, uid), "view": None}


def _win(uid, g, qmsg=""):
    WEB_HEIST.pop(uid, None)
    bal = banco_balance()
    prize = H.PRIZE_FIXED if bal < H.BANCO_PROTECT else int(bal * (H.SHARE_FOLLIA if follia_active() else H.SHARE))
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


def start(uid):
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
    g = {"uid": uid, "level": 1, "deadline": now + WEB_TIME, "alarms": 0, "t_start": now, "phase": "brief", "st": {"r": 0}}
    WEB_HEIST[uid] = g
    return _out(g, {"type": "start"})


def act(uid, a, data):
    g = WEB_HEIST.get(uid)
    if not g:
        return {"error": "Nessuna rapina in corso.", "code": 409}
    if time.time() > g["deadline"]:
        return _fail(uid, g, "⏰ Tempo scaduto: la polizia è arrivata.")
    lvl, phase, st, now = g["level"], g["phase"], g["st"], time.time()

    kind = KINDS[lvl - 1]
    if a == "rd":                                              # "comincia" dei livelli con la presentazione
        if phase != "brief":
            return _out(g)
        if kind == "seq":
            _seq_begin(g, True)
        elif kind == "laser":
            _laser_begin(g, 0.9)
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
