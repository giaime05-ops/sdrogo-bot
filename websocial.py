"""Mini app, fase 5: parti "sociali" (classifiche e diario). Solo lettura: non modificano saldi né statistiche.
Sono funzioni normali, eseguite sul loop del bot (webapi._run) perché USER_DATA cambia solo lì."""
from state import USER_DATA
from storage import ukey

LB = {                                   # stessi campi e stesse categorie di /topricconi
    "coins":   {"title": "Re dei Ricconi", "icon": "🪙", "field": "coins",       "unit": "$SDG"},
    "quizzes": {"title": "Re dei Quiz",    "icon": "🧠", "field": "quizzes_won", "unit": "quiz vinti"},
    "casino":  {"title": "Re del Casinò",  "icon": "🎰", "field": "casino_wins", "unit": "vittorie"},
    "duels":   {"title": "Re dei Duelli",  "icon": "⚔️", "field": "duels_wins",  "unit": "duelli vinti"},
}
TOP_N = 10


def _name(uid: str, d: dict) -> str:
    n = (d.get("name") or "").strip()
    return n or f"Giocatore {uid[-4:]}"


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
    top = [{"rank": i, "name": _name(k, d), "value": v, "me": k == str(uid)}
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
