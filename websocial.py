"""Mini app, fase 5: parti "sociali" (classifiche e diario). Solo lettura: non modificano saldi né statistiche.
Sono funzioni normali, eseguite sul loop del bot (webapi._run) perché USER_DATA cambia solo lì."""
import hashlib
from state import USER_DATA, USER_INVENTORIES, SYSTEM_FLAGS
from storage import ukey, is_web_sandbox

LB = {                                   # stessi campi e stesse categorie di /topricconi
    "coins":   {"title": "Re dei Ricconi", "icon": "🪙", "field": "coins",       "unit": "$SDG"},
    "casino":  {"title": "Re del Casinò",  "icon": "🎰", "field": "casino_wins", "unit": "vittorie"},
    "quizzes": {"title": "Re dei Quiz",    "icon": "🧠", "field": "quizzes_won", "unit": "quiz vinti"},
    "duels":   {"title": "Re dei Duelli",  "icon": "⚔️", "field": "duels_wins",  "unit": "duelli vinti"},
}
TOP_N = 10


# ---- personalizzazione del profilo ("Modifica profilo"): salvata sull'account VERO e mostrata a tutti ----
def _hv(n):
    return ["stars", n, "Vinci %d Sdrogo Heist" % n if n > 1 else "Vinci 1 Sdrogo Heist"]


def _cw(n):
    return ["casino_wins", n, "Vinci %d partite al casinò" % n]


def _gw(g, n, label):
    return ["gw:" + g, n, "Vinci %d %s" % (n, label)]


# Requisiti di sblocco (fonte unica: il telefono li legge dal portafoglio). Chiave "categoria.id"; senza voce = libero.
STYLE_REQ = {
    # --- colore nome ---
    "name.ciano": ["quizzes_won", 10, "Vinci 10 quiz"], "name.rosa": ["duels_wins", 5, "Vinci 5 duelli"],
    "name.verde": _gw("wordle", 3, "partite a Wordle"), "name.giallo": _gw("scratch", 3, "Gratta e vinci"),
    "name.blu": _gw("blackjack", 5, "mani a Blackjack"), "name.arancio": _cw(25), "name.argento": _cw(75),
    "name.smeraldo": _gw("mastermind", 5, "partite a Mastermind"), "name.neonfucsia": _gw("slot", 15, "volte alle slot"),
    "name.neonviola": _gw("aviator", 8, "partite ad Aviator"), "name.oro": _hv(1),
    "name.tramonto": ["st:roul_zero", 1, "Fai uscire lo 0 verde alla roulette"], "name.oceano": _gw("poker", 5, "partite a Poker"),
    "name.lava": ["st:avi_best", 10, "Incassa un moltiplicatore x10 ad Aviator"], "name.aurora": _hv(3),
    "name.galassia": _cw(250), "name.diamante": _cw(500), "name.arcobaleno": _hv(5),
    # --- colore avatar ---
    "av.nero": _cw(10), "av.giallo": _gw("scratch", 3, "Gratta e vinci"), "av.verde": _gw("wordle", 3, "partite a Wordle"),
    "av.azzurro": ["quizzes_won", 3, "Vinci 3 quiz"], "av.arancio": ["duels_wins", 3, "Vinci 3 duelli"], "av.argento": _cw(75),
    "av.smeraldo": _gw("blackjack", 10, "mani a Blackjack"), "av.neonfucsia": _gw("roulette", 8, "volte alla roulette"),
    "av.neonviola": _gw("poker", 5, "partite a Poker"), "av.notte": _cw(120),
    "av.tramonto": ["st:roul_zero", 1, "Fai uscire lo 0 verde alla roulette"], "av.oceano": _gw("mastermind", 8, "partite a Mastermind"),
    "av.lava": ["st:avi_best", 20, "Incassa un moltiplicatore x20 ad Aviator"], "av.aurora": _hv(3), "av.galassia": _cw(300),
    "av.oro": _hv(1), "av.diamante": _cw(600), "av.arcobaleno": _hv(7),
    # --- cornice ---
    "frame.ghiaccio": ["quizzes_won", 10, "Vinci 10 quiz"], "frame.fuoco": _cw(50), "frame.argento": _gw("wordle", 8, "partite a Wordle"),
    "frame.smeraldo": _gw("scratch", 8, "Gratta e vinci"), "frame.onde": _gw("aviator", 15, "partite ad Aviator"),
    "frame.vento": _gw("slot", 30, "volte alle slot"), "frame.elettrico": ["st:roul_zero", 3, "Fai uscire lo 0 verde 3 volte alla roulette"],
    "frame.neonfucsia": _gw("roulette", 15, "volte alla roulette"), "frame.galassia": _cw(300), "frame.oro": _hv(2),
    "frame.diamante": ["st:avi_best", 30, "Incassa un moltiplicatore x30 ad Aviator"], "frame.arcobaleno": _hv(10),
    # --- barra in classifica: SOLO Sdrogo Heist (viola e ciano sono liberi) ---
    "bar.fiamme": _hv(1), "bar.onde": _hv(2), "bar.fiammeblu": _hv(3), "bar.vento": _hv(4), "bar.inferno": _hv(5),
    "bar.tempesta": _hv(7), "bar.fiammeverdi": _hv(10), "bar.aurora": _hv(12), "bar.dragone": _hv(15), "bar.leggenda": _hv(20),
}


def style_req_table() -> dict:
    return STYLE_REQ


STYLE_OPTS = {
    "name":  {"", "nero", "viola", "rosso", "giallo", "verde", "blu", "ciano", "rosa", "arancio", "argento", "smeraldo", "neonfucsia",
              "neonviola", "oro", "tramonto", "oceano", "lava", "aurora", "galassia", "diamante", "arcobaleno"},
    "av":    {"", "nero", "viola", "rosso", "giallo", "verde", "blu", "azzurro", "arancio", "argento", "smeraldo", "neonfucsia",
              "neonviola", "oro", "notte", "tramonto", "oceano", "lava", "aurora", "galassia", "diamante", "arcobaleno"},
    "frame": {"", "neon", "bianco", "nero", "rosso", "giallo", "verde", "blu", "ghiaccio", "fuoco", "argento", "smeraldo", "onde", "vento",
              "elettrico", "neonfucsia", "galassia", "oro", "diamante", "arcobaleno"},
    "bar":   {"", "viola", "ciano", "verde", "fiamme", "onde", "fiammeblu", "vento", "inferno", "tempesta", "fiammeverdi", "aurora",
              "dragone", "leggenda"},
}


def clean_style(raw) -> dict:
    out = {}
    if isinstance(raw, dict):
        for k, ok in STYLE_OPTS.items():
            v = raw.get(k, "")
            if isinstance(v, str) and v in ok and v:
                out[k] = v
    return out


def _style(d: dict) -> dict:
    return clean_style((d or {}).get("style"))


def _real_stat(u: dict, inv: dict, key: str):
    if key == "stars":
        return int(inv.get("stars", 0) or 0)
    if key.startswith("gw:"):
        return int((u.get("game_wins") or {}).get(key[3:], 0) or 0)
    if key.startswith("st:"):
        return float((u.get("stats") or {}).get(key[3:], 0) or 0)
    return float(u.get(key, 0) or 0)


def set_style(uid: int, data: dict, enforce: bool = False) -> dict:
    """Salva lo stile sul profilo reale (anche se l'admin sta giocando nell'account di prova). Per ora solo admin."""
    from storage import _touched_users, save_db
    k = str(uid)
    d = USER_DATA.get(k)
    if d is None:
        return {"error": "Profilo non trovato.", "code": 404}
    st = clean_style(data)
    if enforce:                                                  # giocatori veri: solo ciò che hanno davvero sbloccato (l'admin prova tutto)
        inv = USER_INVENTORIES.get(str(uid)) or {}
        for cat, val in st.items():
            rq = STYLE_REQ.get(cat + "." + val)
            if rq and _real_stat(d, inv, rq[0]) < rq[1]:
                return {"error": "Non hai ancora sbloccato: " + val + " (" + rq[2] + ").", "code": 403}
    d["style"] = st
    _touched_users.add(k)
    save_db()
    return {"style": d["style"]}


def _pid(k: str) -> str:
    return hashlib.sha1(("sdg:" + str(k)).encode()).hexdigest()[:12]


def profile(uid: int, pid: str) -> dict:
    """Profilo pubblico di un altro giocatore (sola lettura), trovato dal codice opaco della classifica."""
    for k, u in list(USER_DATA.items()):
        if str(k).startswith("sbx_") or _pid(k) != pid:
            continue
        inv = USER_INVENTORIES.get(str(k)) or {}
        c_wins, d_wins = int(u.get("casino_wins", 0) or 0), int(u.get("duels_wins", 0) or 0)
        sp, dp = int(u.get("single_played", 0) or 0), int(u.get("duels_played", 0) or 0)
        sw = int(u.get("single_won", c_wins) or 0)
        return {"name": _name(str(k), u), "badge": badge_of(u), "stars": stars_split(u, inv)[0], "stars_old": stars_split(u, inv)[1],
                "balance": int(u.get("coins", 0) or 0), "net_profit": int(u.get("net_profit", 0) or 0),
                "quizzes_won": int(u.get("quizzes_won", 0) or 0), "casino_wins": c_wins, "duels_wins": d_wins,
                "wr_single": min(100, int(sw / sp * 100)) if sp > 0 else 0,
                "wr_duels": min(100, int(d_wins / dp * 100)) if dp > 0 else 0, "style": _style(u)}
    return {"error": "Giocatore non trovato."}


def badge_of(u) -> str:
    """Badge mostrati: quelli storici nuovi ('h5', 'q0') restano nascosti finché le novità non sono aperte."""
    b = str((u or {}).get("badge", "") or "")
    if not (SYSTEM_FLAGS.get("NEW_PUBLIC") or is_web_sandbox()):
        b = ",".join(x for x in b.split(",") if x not in ("h5", "q0"))
    return b


def stars_split(u, inv):
    """(nuove, vecchie): le vecchie (gialle) sono quelle della prima versione, congelate in stars_v1; chi ha il badge H5 non le mostra (le sostituisce il badge)."""
    tot = int((inv or {}).get("stars", 0) or 0)
    old = min(tot, int((u or {}).get("stars_v1", 0) or 0))
    if "h5" in badge_of(u).split(","):
        shown_old = 0
    else:
        shown_old = old
    return tot - old, shown_old


def _name(uid: str, d: dict) -> str:
    n = (d.get("name") or "").strip()
    short = n.split("#")[0].strip()                 # nomi del tipo «Marco#maF…»: in classifica si mostra solo la parte prima del # (il nome salvato non cambia)
    n = short or n
    return n or f"Giocatore {uid[-4:]}"


ONLINE_SECS = 150            # "online" = ha usato la mini app o il bot negli ultimi 2,5 minuti


def friends(uid: int) -> dict:
    """Sezione Amici: tutti i giocatori del gruppo con lo stato online/offline (chi è online per primo)."""
    import time
    from storage import LAST_SEEN
    now = time.time()
    rows = []
    for k, u in list(USER_DATA.items()):
        k = str(k)
        if not k.isdigit() or k == str(uid):
            continue
        seen = max(float(LAST_SEEN.get(k) or 0), float(u.get("seen") or 0))
        if not seen:
            continue
        rows.append({"pid": _pid(k), "name": _name(k, u), "badge": badge_of(u), "style": _style(u),
                     "online": now - seen <= ONLINE_SECS, "ago": int(now - seen)})
    rows.sort(key=lambda r: (not r["online"], r["ago"]))
    return {"friends": rows[:60], "online": sum(1 for r in rows if r["online"])}


def leaderboard(uid: int, cat: str) -> dict:
    meta = LB.get(cat) or LB["coins"]
    cat = cat if cat in LB else "coins"
    field = meta["field"]
    rows = []
    for k, d in list(USER_DATA.items()):                    # copia: il dizionario non cambia mentre lo leggo
        if str(k).startswith("sbx_"):                       # profili di prova dell'admin: mai in classifica
            continue
        v = int(d.get(field, 0) or 0)
        if v > 0:
            rows.append((str(k), v, d))
    rows.sort(key=lambda r: r[1], reverse=True)
    me = ukey(uid)
    top = [{"rank": i, "name": _name(k, d), "value": v, "me": k == str(uid), "badge": badge_of(d), "pid": _pid(k), "style": _style(d)}
           for i, (k, v, d) in enumerate(rows[:TOP_N], start=1)]
    mine = None
    for i, (k, v, d) in enumerate(rows, start=1):
        if k == str(uid):
            mine = {"rank": i, "value": v}
            break
    if mine is None and not me.startswith("sbx_"):
        mine = {"rank": None, "value": int((USER_DATA.get(me) or {}).get(field, 0) or 0)}
    cats = [{"key": k, "title": m["title"], "icon": m["icon"]} for k, m in LB.items()]
    return {"cat": cat, "title": meta["title"], "icon": meta["icon"], "unit": meta["unit"],
            "top": top, "me": mine, "total": len(rows), "cats": cats}


def diary(uid: int) -> dict:
    d = USER_DATA.get(str(uid)) or {}                       # lo storico reale, non quello del profilo di prova
    hist = []
    for h in reversed(list(d.get("history", []))):          # dalla più recente
        try:
            hist.append({"t": int(h["t"]), "game": str(h["g"]), "bet": int(h["b"]), "paid": int(h["w"]),
                         "net": int(h["w"]) - int(h["b"]), "outcome": str(h.get("o", ""))})
        except (KeyError, TypeError, ValueError):
            continue
    return {"history": hist,
            "stats": {"net_profit": int(d.get("net_profit", 0) or 0), "casino_wins": int(d.get("casino_wins", 0) or 0),
                      "quizzes_won": int(d.get("quizzes_won", 0) or 0), "duels_wins": int(d.get("duels_wins", 0) or 0),
                      "single_played": int(d.get("single_played", 0) or 0), "duels_played": int(d.get("duels_played", 0) or 0)}}


def quests(uid: int) -> dict:
    """Sfida di gruppo e missioni di oggi (le stesse del bot). Le missioni esistono solo per chi ha la V2."""
    from economy import GROUP_CHALLENGE, CHALLENGE_REWARD, quests_for
    from utils import is_v2
    gc = GROUP_CHALLENGE
    ch = {"active": bool(gc.get("active"))}
    if ch["active"]:
        goal = max(1, int(gc.get("goal", 1)))
        parts = gc.get("participants", {}) or {}
        ch.update({"goal": goal, "spent": int(gc.get("spent", 0)), "end": int(gc.get("end", 0)),
                   "players": len(parts), "reward": int(CHALLENGE_REWARD),
                   "mine": int(parts.get(str(uid), 0) or 0) if isinstance(parts, dict) else 0})
    lst = []
    if is_v2(uid):
        for i, q in enumerate(quests_for(uid, uid)):
            lst.append({"idx": i, "kind": q.get("kind"), "text": str(q.get("text", "")), "unit": q.get("unit", ""),
                        "progress": min(q.get("progress", 0), q.get("target", 0)), "target": q.get("target", 0),
                        "reward": int(q.get("reward", 0)), "done": bool(q.get("done")),
                        "claimed": bool(q.get("claimed", False)) if q.get("done") else False})
    return {"challenge": ch, "quests": lst, "v2": is_v2(uid)}


def quest_claim_web(uid: int, idx: int) -> dict:
    from economy import quest_claim
    ok, msg = quest_claim(uid, uid, idx)
    if not ok:
        return {"error": msg, "code": 409}
    return {"message": msg, "balance": int((USER_DATA.get(ukey(uid)) or {}).get("coins", 0))}
