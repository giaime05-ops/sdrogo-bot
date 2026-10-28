"""Grand Prix di Sdrogo Arcade: record veri, fantasmi, sblocco piste a pagamento e premi (nello stesso processo del bot).

Regole:
  * il record di una pista = 3 giri completi in modalità Singolo (le gare rapide e le lobby non contano e non pagano);
  * le prime 3 piste sono gratis; le altre si comprano con $SDG (prezzi in PRICE) e restano tue;
  * premio (REWARD) per ogni record che batte il record di UN ALTRO giocatore (o il primo record in assoluto);
    se il record è già tuo, paga solo se migliori di almeno 0,3 s (niente guadagni a colpi di millesimi);
  * i premi NON escono dal Banco: li genera il server. Il Banco incassa solo l'acquisto delle piste (single player);
  * controlli: tempo plausibile, fantasma di dimensione limitata, massimo 12 premi al giorno per giocatore;
  * l'account di prova dell'admin non usa queste rotte (simula in locale).
"""
import logging
import time

from flask import jsonify, request

TRACKS = ["neon", "serp", "tornanti", "salto", "zigzag", "gp", "canyon", "costa", "downtown", "skyline"]
PRICE = [0, 0, 0, 500, 750, 950, 1150, 1400, 1700, 2000]
REWARD = [150, 150, 150, 300, 350, 400, 500, 600, 800, 1000]
MIN_MS, MAX_MS = 20000, 900000
MIN_GAIN_OWN_MS = 300
MAX_REWARDS_DAY = 12
MAX_GHOST = 9000                                  # numeri (x, y, angolo): ~20 Hz per 3 giri


def _db():
    from state import SYSTEM_FLAGS
    d = SYSTEM_FLAGS.setdefault("RACE_DB", {})
    d.setdefault("rec", {}); d.setdefault("best", {}); d.setdefault("day", {})
    return d


def _save():
    from storage import save_db
    save_db()


def _name(uid):
    import websocial
    from state import USER_DATA
    return websocial._name(str(uid), USER_DATA.get(str(uid)) or {})


def _unlocked(uid):
    from storage import get_user_data_dict
    return set(get_user_data_dict(int(uid), int(uid)).get("race_unlocked") or [])


def _view(uid):
    db = _db(); uid = str(uid)
    top, mine = {}, {}
    for tid in TRACKS:
        b = db["best"].get(tid) or {}
        rows = sorted(b.items(), key=lambda kv: kv[1])[:3]
        top[tid] = [{"n": _name(u), "t": t, "me": u == uid} for u, t in rows]
        if uid in b:
            mine[tid] = b[uid]
    un = _unlocked(uid)
    return {"unlocked": [t for i, t in enumerate(TRACKS) if PRICE[i] == 0 or t in un], "top": top, "mine": mine,
            "rec": {tid: {"t": r["t"], "n": _name(r["u"]), "me": r["u"] == uid} for tid, r in db["rec"].items()}}


async def _co(fn, *a):
    return fn(*a)


def install(wa):
    bp = wa.webapi

    def sbx_block():
        if wa.is_web_sandbox():
            return wa._err("L'account di prova simula tutto in locale.", 403)
        return None

    @bp.route("/api/race/state", methods=["GET", "OPTIONS"])
    def race_state():
        if request.method == "OPTIONS":
            return "", 204
        user, err = wa.require_admin()
        if err:
            return err
        b = sbx_block()
        if b:
            return b
        resp, out = wa._avi_call(_co(_view, user["id"]))
        if resp is not None:
            return resp
        return jsonify({"success": True, **out})

    @bp.route("/api/race/ghost", methods=["GET", "OPTIONS"])
    def race_ghost():
        if request.method == "OPTIONS":
            return "", 204
        user, err = wa.require_admin()
        if err:
            return err
        tid = str(request.args.get("track", ""))[:12]
        r = _db()["rec"].get(tid)
        if not r:
            return jsonify({"success": True, "g": None})
        return jsonify({"success": True, "g": r.get("g"), "t": r["t"], "n": _name(r["u"]), "me": r["u"] == str(user["id"])})

    @bp.route("/api/race/unlock", methods=["POST", "OPTIONS"])
    def race_unlock():
        if request.method == "OPTIONS":
            return "", 204
        user, data, err = wa._post_json()
        if err:
            return err
        b = sbx_block()
        if b:
            return b
        tid = str(data.get("track", ""))[:12]
        if tid not in TRACKS:
            return wa._err("Pista sconosciuta.")
        i = TRACKS.index(tid); uid = int(user["id"])

        def do():
            from storage import get_user_data_dict, get_user_coins, add_user_coins, save_db
            d = get_user_data_dict(uid, uid); lst = d.setdefault("race_unlocked", [])
            if PRICE[i] == 0 or tid in lst:
                return {"ok": True}
            if get_user_coins(uid, uid) < PRICE[i]:
                return {"error": "Saldo insufficiente: servono %d $SDG." % PRICE[i]}
            add_user_coins(uid, uid, -PRICE[i]); lst.append(tid)
            try:
                import economy
                economy.banco_add(PRICE[i])                              # chi compra una pista alimenta il Banco (come gli altri giochi singoli)
            except Exception as e:
                logging.warning(f"Banco non aggiornato per lo sblocco: {e}")
            save_db()
            return {"ok": True, "price": PRICE[i]}
        resp, out = wa._avi_call(_co(do))
        if resp is not None:
            return resp
        if out.get("error"):
            return wa._err(out["error"])
        return jsonify({"success": True, **out})

    @bp.route("/api/race/record", methods=["POST", "OPTIONS"])
    def race_record():
        if request.method == "OPTIONS":
            return "", 204
        user, data, err = wa._post_json()
        if err:
            return err
        b = sbx_block()
        if b:
            return b
        tid = str(data.get("track", ""))[:12]
        if tid not in TRACKS:
            return wa._err("Pista sconosciuta.")
        try:
            ms = int(data.get("t", 0))
        except (TypeError, ValueError):
            return wa._err("Tempo non valido.")
        g = data.get("g")
        if not (MIN_MS <= ms <= MAX_MS):
            return wa._err("Tempo non valido.")
        if g is not None and (not isinstance(g, list) or len(g) > MAX_GHOST or not all(isinstance(x, int) for x in g[:50])):
            g = None
        i = TRACKS.index(tid); uid = str(user["id"])

        def do():
            from storage import add_user_coins, save_db
            if PRICE[i] and tid not in _unlocked(uid):
                return {"error": "Pista non sbloccata."}
            db = _db(); best = db["best"].setdefault(tid, {}); prev_me = best.get(uid)
            if prev_me is None or ms < prev_me:
                best[uid] = ms
            cur = db["rec"].get(tid)
            new_rec = cur is None or ms < cur["t"]
            reward = 0
            if new_rec:
                other = cur is not None and cur["u"] != uid
                gain_ok = cur is None or other or (cur["t"] - ms) >= MIN_GAIN_OWN_MS
                today = time.strftime("%Y-%m-%d")
                dd = db["day"].setdefault(uid, {"d": today, "n": 0})
                if dd["d"] != today:
                    dd["d"], dd["n"] = today, 0
                if gain_ok and dd["n"] < MAX_REWARDS_DAY:
                    reward = REWARD[i]; dd["n"] += 1
                db["rec"][tid] = {"u": uid, "t": ms, "g": g}
                if reward:
                    add_user_coins(int(uid), int(uid), reward)          # generati dal server: il Banco non si tocca
            save_db()
            rows = sorted((db["best"].get(tid) or {}).items(), key=lambda kv: kv[1])
            pos = [u for u, _ in rows].index(uid) + 1 if uid in dict(rows) else 0
            return {"record": new_rec, "reward": reward, "pos": pos, "of": len(rows)}
        resp, out = wa._avi_call(_co(do))
        if resp is not None:
            return resp
        if out.get("error"):
            return wa._err(out["error"])
        return jsonify({"success": True, **out})
