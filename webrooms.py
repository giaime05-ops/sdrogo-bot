"""Stanze multiplayer in tempo reale per Sdrogo Arcade (stesso processo e stesso servizio del bot).

Come funziona (semplice e robusto, nessun pacchetto in più):
  * ogni stanza ha un registro ordinato di eventi; i client mandano eventi (mosse, posizioni, messaggi) con POST /api/room/send
    e leggono quelli nuovi con GET /api/room/poll?since=N (richieste brevi, ~4 al secondo: nessun thread bloccato);
  * il server decide CHI c'è, CHI è l'host, QUANDO si parte e SOLDI e premi: puntate e premi passano solo da qui;
  * puntata (stake): trattenuta dal saldo reale di chi entra e restituita se la stanza si scioglie prima del via;
  * a fine partita ogni giocatore invia la classifica che ha visto: si paga solo se la maggioranza è d'accordo,
    altrimenti tutti vengono rimborsati (nessuno può inventarsi una vittoria da solo);
  * premio extra "dal Banco" (Grand Prix): 1°/2°/3° = 100%/75%/50% del premio scelto, solo se ci sono almeno 2 persone vere;
  * l'account di prova dell'admin gioca con soldi simulati (sim=True): il saldo vero non si tocca mai;
  * inviti: messaggio privato del bot con il pulsante "Apri Arcade"; se l'amico è offline viene anche taggato nel gruppo.
Tutto passa da require_admin() di webapi: stessi permessi, stesso interruttore "Multiplayer per tutti".
"""
import logging
import secrets
import threading
import time

from flask import jsonify, request

GAMES = {"srf", "dadi", "hl", "tris", "rou", "rr", "ghig", "race", "poker", "bj", "scopa"}
MAX_PLAYERS = {"srf": 2, "dadi": 2, "hl": 2, "tris": 2, "rou": 2, "rr": 2, "ghig": 2, "race": 8, "poker": 4, "bj": 4, "scopa": 4}
MAX_STAKE = 5000
MAX_EV, EV_BYTES = 600, 900
ACT_OK = {"gp", "gp_single", "gp_quick", "gp_lobby", "gara", "multi", "tavoli", "sfide", "shop", "casino", "attivita", "classifica"}
ROOM_IDLE = 900                      # secondi senza attività: la stanza si chiude (e le puntate tornano indietro)
ROOMS = {}
_L = threading.RLock()


def _now():
    return time.time()


def _view(r, uid):
    return {"id": r["id"], "game": r["game"], "host": r["host"], "status": r["status"], "cfg": r["cfg"], "sim": r["sim"],
            "players": [{"uid": p["uid"], "pid": p["pid"], "name": p["name"], "style": p["style"], "ready": p["ready"], "col": p.get("col"),
                         "online": _now() - p["seen"] < 12} for p in r["players"]],
            "invited": r["invited"], "n": len(r["events"]), "me": uid, "result": r.get("settled"),
            "away": max(0, int(r["closeAt"] - _now())) if r.get("closeAt") else 0, "st": round(_now(), 3),
            "sy": {"rdy": r.get("rdy", {}), "go": r.get("go", {}), "q": r.get("q", {}), "grid": r.get("gridv")}}


def _prof(uid):
    import websocial
    from state import USER_DATA
    d = USER_DATA.get(str(uid)) or {}
    return {"uid": str(uid), "pid": websocial._pid(str(uid)), "name": websocial._name(str(uid), d), "style": websocial._style(d)}


def _uid_of_pid(pid):
    import websocial
    from state import USER_DATA
    for k in list(USER_DATA.keys()):
        if str(k).isdigit() and websocial._pid(str(k)) == pid:
            return str(k)
    return None


def _x(d):
    out = {}
    if isinstance(d, dict):
        for k, v in list(d.items())[:12]:
            if isinstance(k, str) and len(k) <= 8 and isinstance(v, (int, bool, float)):
                out[k] = v
    return out


def _wallet_ok(uid, amount):
    from storage import get_user_coins
    return get_user_coins(int(uid), int(uid)) >= amount


def _move(uid, amount):
    from storage import add_user_coins
    add_user_coins(int(uid), int(uid), int(amount))


def _gc():
    now = _now()
    for rid in [k for k, r in ROOMS.items() if now - r["act"] > ROOM_IDLE or (r.get("closeAt") and now > r["closeAt"])]:
        r = ROOMS.pop(rid)
        r["events"].append({"k": "leave", "u": r["host"]})
        if r["status"] != "done":
            _refund(r)
    for r in list(ROOMS.values()):                                    # in lobby chi sparisce per più di 60 s esce da solo (con la puntata restituita)
        if r["status"] != "lobby" or r["game"] != "race":
            continue
        for p in list(r["players"]):
            if p["uid"] != r["host"] and now - p["seen"] > 60:
                if p.get("paid") and not r["sim"]:
                    _hist(p["uid"], str(r["game"]) + " (rimborso)", p["paid"], p["paid"], "rimborso")
                    _move(p["uid"], p["paid"]); p["paid"] = 0
                r["players"] = [q for q in r["players"] if q["uid"] != p["uid"]]
                r["events"].append({"k": "leave", "u": p["uid"]})


def _hist(uid, game, bet, pay, outcome=""):
    try:
        from storage import log_play
        log_play(int(uid), "Online " + str(game), bet, pay, outcome)
    except Exception as e:
        logging.warning(f"Storico stanza non scritto: {e}")


def _refund(r):
    if r["sim"] or r.get("refunded"):
        return
    r["refunded"] = True
    for p in r["players"]:
        if p.get("paid"):
            _hist(p["uid"], str(r["game"]) + " (rimborso)", p["paid"], p["paid"], "rimborso")
            _move(p["uid"], p["paid"]); p["paid"] = 0


async def _co(fn, *a):
    return fn(*a)


def _settle(r):
    """Chiamata col lock preso, sul loop del bot. Paga se la maggioranza concorda, altrimenti rimborsa."""
    votes = {}
    for u, v in r["votes"].items():
        order, chips = (v if isinstance(v, tuple) else (v, ()))
        votes.setdefault((tuple(order), tuple(chips)), []).append(u)
    now = _now()
    active = [p for p in r["players"] if now - p["seen"] < 45 or p["uid"] in r["votes"]]   # chi ha abbandonato non blocca il pagamento
    need = max(1, len(active) // 2 + 1)
    best = max(votes.items(), key=lambda kv: len(kv[1]), default=None)
    if not best or len(best[1]) < need:
        return False
    order = list(best[0][0])
    chips = dict(best[0][1])
    valid = {p["uid"] for p in r["players"]}
    order = [u for u in order if u in valid]
    chips = {u: c for u, c in chips.items() if u in valid and c > 0}
    pot = sum(p.get("paid", 0) for p in r["players"]) if not r["sim"] else 0       # tutto ciò che i giocatori hanno messo (puntata + eventuali ricompre)
    pay = {}
    if chips and pot:                                              # tavoli: ognuno incassa in proporzione alle fiches finali (il totale è sempre il piatto)
        tot = sum(chips.values())
        for u, c in chips.items():
            pay[u] = int(pot * c / tot)
        top = max(chips, key=lambda k: chips[k])
        pay[top] += pot - sum(pay.values())
        order = sorted(chips, key=lambda k: -chips[k])
    elif order and pot:
        if r["game"] in ("race", "poker", "bj", "scopa") and len(order) >= 3:
            split = [0.6, 0.3, 0.1]
        elif len(order) >= 3:
            split = [0.7, 0.3]
        else:
            split = [1.0]
        for i, share in enumerate(split):
            if i < len(order):
                pay[order[i]] = pay.get(order[i], 0) + int(pot * share)
    prize = int(r["cfg"].get("prize", 0)) if r["game"] == "race" and len(r["players"]) >= 2 else 0
    if prize and not r["sim"]:                                   # il premio lo genera il server: il Banco non si tocca
        for i, f in enumerate((1.0, 0.75, 0.5)):
            if i < len(order):
                pay[order[i]] = pay.get(order[i], 0) + int(prize * f)
    if not r["sim"]:
        for u, amt in pay.items():
            if amt:
                _move(u, amt)
        for p in r["players"]:
            if p.get("paid") or pay.get(p["uid"]):
                _hist(p["uid"], r["game"], p.get("paid", 0), pay.get(p["uid"], 0))
            p["paid"] = 0
    r["settled"] = {"order": order, "pay": pay, "sim": r["sim"]}
    r["status"] = "done"
    return True


def install(wa):
    bp = wa.webapi

    def need(rid, user):
        r = ROOMS.get(str(rid))
        if not r:
            return None, wa._err("Stanza non trovata o chiusa.", 404)
        if not any(p["uid"] == str(user["id"]) for p in r["players"]) and str(user["id"]) not in r["invited"]:
            return None, wa._err("Non sei in questa stanza.", 403)
        return r, None

    def post():
        user, data, err = wa._post_json()
        return user, data, err

    @bp.route("/api/room/create", methods=["POST", "OPTIONS"])
    def room_create():
        if request.method == "OPTIONS":
            return "", 204
        user, data, err = post()
        if err:
            return err
        game = str(data.get("game", ""))[:10]
        if game not in GAMES:
            return wa._err("Gioco sconosciuto.")
        cfg = data.get("cfg") if isinstance(data.get("cfg"), dict) else {}
        stake = max(0, min(MAX_STAKE, int(cfg.get("stake", 0) or 0)))
        prize = max(0, min(5000, int(cfg.get("prize", 0) or 0)))
        sim = bool(wa.is_web_sandbox())
        uid = str(user["id"])
        with _L:
            _gc()
            for old in [k for k, r in ROOMS.items() if r["host"] == uid and r["status"] == "lobby" and r["game"] == game]:   # una sola lobby aperta per gioco: la vecchia si chiude
                o = ROOMS.pop(old); _refund(o)
            if sum(1 for r in ROOMS.values() if r["host"] == uid and r["status"] != "done") >= 3:
                return wa._err("Hai già troppe stanze aperte.")
        if stake and not sim:
            resp, ok = wa._avi_call(_co(_wallet_ok, uid, stake))
            if resp is not None:
                return resp
            if not ok:
                return wa._err("Saldo insufficiente per la puntata.")
            resp, _ = wa._avi_call(_co(_move, uid, -stake))
            if resp is not None:
                return resp
        p = dict(_prof(uid), ready=True, seen=_now(), paid=stake if not sim else 0)
        r = {"id": secrets.token_urlsafe(6), "game": game, "host": uid, "status": "lobby", "sim": sim, "act": _now(),
             "cfg": {"stake": stake, "prize": prize if len([1]) else 0, "laps": int(cfg.get("laps", 3) or 3), "track": str(cfg.get("track", ""))[:16],
                     "hands": int(cfg.get("hands", 0) or 0), "x": _x(cfg.get("x")), "max": min(MAX_PLAYERS[game], int(cfg.get("max", MAX_PLAYERS[game]) or 2))},
             "players": [p], "invited": [], "events": [], "votes": {}}
        with _L:
            ROOMS[r["id"]] = r
        return jsonify({"success": True, "room": _view(r, uid)})

    @bp.route("/api/room/join", methods=["POST", "OPTIONS"])
    def room_join():
        if request.method == "OPTIONS":
            return "", 204
        user, data, err = post()
        if err:
            return err
        uid = str(user["id"])
        r = ROOMS.get(str(data.get("id", "")))
        if not r:
            return wa._err("Stanza non trovata o chiusa.", 404)
        if r["status"] != "lobby":
            return wa._err("La partita è già iniziata.")
        if any(p["uid"] == uid for p in r["players"]):
            return jsonify({"success": True, "room": _view(r, uid)})
        if len(r["players"]) >= r["cfg"]["max"]:
            return wa._err("La stanza è piena.")
        if r["sim"] != bool(wa.is_web_sandbox()):
            return wa._err("Questa stanza è di prova (soldi finti): non puoi entrare con l'account vero.")
        stake = r["cfg"]["stake"]
        if stake and not r["sim"]:
            resp, ok = wa._avi_call(_co(_wallet_ok, uid, stake))
            if resp is not None:
                return resp
            if not ok:
                return wa._err("Saldo insufficiente per la puntata.")
            resp, _ = wa._avi_call(_co(_move, uid, -stake))
            if resp is not None:
                return resp
        with _L:
            r["players"].append(dict(_prof(uid), ready=(r["game"] != "race"), seen=_now(), paid=stake if not r["sim"] else 0)); r["act"] = _now()
            r["events"].append({"k": "join", "u": uid})
            r["invited"] = [x for x in r["invited"] if x != uid]
        return jsonify({"success": True, "room": _view(r, uid)})

    @bp.route("/api/room/leave", methods=["POST", "OPTIONS"])
    def room_leave():
        if request.method == "OPTIONS":
            return "", 204
        user, data, err = post()
        if err:
            return err
        uid = str(user["id"]); r, e = need(data.get("id"), user)
        if e:
            return e
        with _L:
            if r["status"] == "lobby" and r["host"] == uid and data.get("grace") and len(r["players"]) >= 1:   # uscita dell'host: 15 s per rientrare, poi si chiude
                r["closeAt"] = _now() + 15; r["act"] = _now()
                return jsonify({"success": True, "grace": 15})
            if r["status"] == "lobby" and r["host"] == uid:      # l'host chiude la sua lobby: si chiude per tutti e gli inviti spariscono
                if not r["sim"]:
                    for p in r["players"]:
                        if p.get("paid"):
                            _hist(p["uid"], str(r["game"]) + " (rimborso)", p["paid"], p["paid"], "rimborso")
                            wa._avi_call(_co(_move, p["uid"], p["paid"])); p["paid"] = 0
                    r["refunded"] = True
                r["events"].append({"k": "leave", "u": uid}); r["status"] = "done"
                ROOMS.pop(r["id"], None)
                return jsonify({"success": True, "closed": True})
            r["invited"] = [x for x in r["invited"] if x != uid]   # chi esce non ha più un invito pendente
            me = next((p for p in r["players"] if p["uid"] == uid), None)
            if me and r["status"] == "lobby" and me.get("paid") and not r["sim"]:
                _hist(uid, str(r["game"]) + " (rimborso)", me["paid"], me["paid"], "rimborso")
                wa._avi_call(_co(_move, uid, me["paid"])); me["paid"] = 0
            r["players"] = [p for p in r["players"] if p["uid"] != uid]
            r["events"].append({"k": "leave", "u": uid}); r["act"] = _now()
            if not r["players"]:
                ROOMS.pop(r["id"], None)
            elif r["host"] == uid:
                r["host"] = r["players"][0]["uid"]
            if r["status"] == "run" and len(r["players"]) < 2:
                r["status"] = "lobby" if False else "run"
        return jsonify({"success": True})

    @bp.route("/api/room/invite", methods=["POST", "OPTIONS"])
    def room_invite():
        if request.method == "OPTIONS":
            return "", 204
        user, data, err = post()
        if err:
            return err
        r, e = need(data.get("id"), user)
        if e:
            return e
        to = _uid_of_pid(str(data.get("pid", ""))[:24])
        if not to:
            return wa._err("Giocatore non trovato.")
        if to in r["invited"] or any(p["uid"] == to for p in r["players"]):
            return jsonify({"success": True, "sent": False})
        import websocial
        from storage import LAST_SEEN
        from state import USER_DATA
        offline = _now() - float(LAST_SEEN.get(to) or (USER_DATA.get(to) or {}).get("seen") or 0) > websocial.ONLINE_SECS
        me = _prof(user["id"])
        label = {"race": "Grand Prix", "poker": "Poker", "bj": "Blackjack", "scopa": "Scopa", "dadi": "Dadi", "tris": "Tris", "srf": "Sasso-Carta-Forbici",
                 "hl": "Alto/Basso", "rou": "Roulette", "rr": "Roulette russa", "ghig": "Ghigliottina"}.get(r["game"], r["game"])
        stake = r["cfg"]["stake"]
        txt = f"🎮 {me['name']} ti invita a una partita di {label}" + (f" (puntata {stake} $SDG)" if stake and not r["sim"] else "") + ".\nApri Sdrogo Arcade → Multiplayer → Inviti."

        async def send():
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            bot = wa._bot(); ok = False
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("🎮 Apri Arcade", url=wa.ARCADE_ORIGIN + "/sdrogobot-arcade/")]])
            try:
                await bot.send_message(int(to), txt, reply_markup=kb); ok = True
            except Exception as ex:
                logging.info(f"Invito in privato non consegnato a {to}: {ex}")
            if (offline or not ok) and wa.GROUP_CHAT_ID:
                try:
                    await bot.send_message(int(wa.GROUP_CHAT_ID), f"🎮 <a href=\"tg://user?id={int(to)}\">{_prof(to)['name']}</a>, {me['name']} ti aspetta in una lobby di {label}!", parse_mode="HTML")
                except Exception as ex:
                    logging.info(f"Tag nel gruppo non riuscito: {ex}")
            return ok
        resp, ok = wa._avi_call(send())
        if resp is not None:
            return resp
        with _L:
            r["invited"].append(to); r["act"] = _now()
        return jsonify({"success": True, "sent": True, "offline": offline, "dm": bool(ok)})

    @bp.route("/api/act", methods=["POST", "OPTIONS"])
    def act_log():
        """Dove si trova l'utente (solo etichette ammesse): serve all'admin per capire cosa fanno le persone, senza registrare ogni tocco."""
        if request.method == "OPTIONS":
            return "", 204
        user, data, err = post()
        if err:
            return err
        a = str(data.get("a", ""))[:60]
        if a.split(":")[0] in ACT_OK and all(ch.isalnum() or ch in " :_-.·" for ch in a):
            from storage import log_act
            log_act(user["id"], a)
        return jsonify({"success": True})

    @bp.route("/api/room/kick", methods=["POST", "OPTIONS"])
    def room_kick():
        """L'host toglie un giocatore dalla lobby."""
        if request.method == "OPTIONS":
            return "", 204
        user, data, err = post()
        if err:
            return err
        r, e = need(data.get("id"), user)
        if e:
            return e
        uid = str(user["id"]); tg = str(data.get("uid", ""))
        with _L:
            if r["host"] != uid or r["status"] != "lobby" or tg == uid:
                return wa._err("Non puoi farlo.", 403)
            p = next((q for q in r["players"] if q["uid"] == tg), None)
            if p:
                if p.get("paid") and not r["sim"]:
                    _hist(tg, str(r["game"]) + " (rimborso)", p["paid"], p["paid"], "rimborso")
                    wa._avi_call(_co(_move, tg, p["paid"])); p["paid"] = 0
                r["players"] = [q for q in r["players"] if q["uid"] != tg]
            r["invited"] = [x for x in r["invited"] if x != tg]
            r["events"].append({"k": "leave", "u": tg}); r["act"] = _now()
        return jsonify({"success": True, "room": _view(r, uid)})

    @bp.route("/api/room/color", methods=["POST", "OPTIONS"])
    def room_color():
        """Ognuno sceglie il colore della propria auto in lobby (0-7)."""
        if request.method == "OPTIONS":
            return "", 204
        user, data, err = post()
        if err:
            return err
        r, e = need(data.get("id"), user)
        if e:
            return e
        try:
            c = int(data.get("col"))
        except (TypeError, ValueError):
            return wa._err("Colore non valido.")
        with _L:
            me = next((p for p in r["players"] if p["uid"] == str(user["id"])), None)
            if not me or r["status"] != "lobby" or not 0 <= c <= 7:
                return wa._err("Non puoi cambiare colore.")
            me["col"] = c; me["seen"] = _now(); r["act"] = _now()
        return jsonify({"success": True, "room": _view(r, str(user["id"]))})

    @bp.route("/api/room/ready", methods=["POST", "OPTIONS"])
    def room_ready():
        if request.method == "OPTIONS":
            return "", 204
        user, data, err = post()
        if err:
            return err
        r, e = need(data.get("id"), user)
        if e:
            return e
        with _L:
            me = next((p for p in r["players"] if p["uid"] == str(user["id"])), None)
            if not me or r["status"] != "lobby":
                return wa._err("Non sei in lobby.")
            me["ready"] = bool(data.get("ready")); me["seen"] = _now(); r["act"] = _now()
        return jsonify({"success": True, "room": _view(r, str(user["id"]))})

    @bp.route("/api/room/rejoin", methods=["POST", "OPTIONS"])
    def room_rejoin():
        """L'host rientra nella sua lobby entro i 15 s di tolleranza."""
        if request.method == "OPTIONS":
            return "", 204
        user, data, err = post()
        if err:
            return err
        r = ROOMS.get(str(data.get("id", "")))
        if not r or r["host"] != str(user["id"]) or r["status"] != "lobby":
            return wa._err("La lobby non c'è più.", 404)
        with _L:
            r.pop("closeAt", None); r["act"] = _now()
            for p in r["players"]:
                if p["uid"] == r["host"]:
                    p["seen"] = _now()
        return jsonify({"success": True, "room": _view(r, str(user["id"]))})

    @bp.route("/api/room/open", methods=["GET", "OPTIONS"])
    def room_open():
        """Lobby pubbliche del Grand Prix: le vedono tutti i membri, si entra senza invito."""
        if request.method == "OPTIONS":
            return "", 204
        user, err = wa.require_admin()
        if err:
            return err
        uid = str(user["id"]); out = []; sim = bool(wa.is_web_sandbox())
        with _L:
            _gc()
            for r in ROOMS.values():
                if r["game"] != "race" or r["status"] != "lobby" or r["sim"] != sim or r.get("closeAt") or not (r["cfg"].get("x") or {}).get("vis", 1):
                    continue
                if any(p["uid"] == uid for p in r["players"]) or len(r["players"]) >= r["cfg"]["max"]:
                    continue
                h = next((p for p in r["players"] if p["uid"] == r["host"]), None)
                if h and _now() - h["seen"] < 40:
                    out.append(_view(r, uid))
        return jsonify({"success": True, "rooms": out})

    @bp.route("/api/room/mine", methods=["GET", "OPTIONS"])
    def room_mine():
        if request.method == "OPTIONS":
            return "", 204
        user, err = wa.require_admin()
        if err:
            return err
        uid = str(user["id"]); out = []
        with _L:
            _gc()
            for r in ROOMS.values():
                if r["status"] != "lobby":
                    continue
                mine_p = any(p["uid"] == uid for p in r["players"])
                if mine_p:
                    out.append(_view(r, uid))
                elif uid in r["invited"]:                       # invito pendente: solo se l'host è ancora lì (non una lobby abbandonata)
                    h = next((p for p in r["players"] if p["uid"] == r["host"]), None)
                    if h and _now() - h["seen"] < 60 and not r.get("closeAt"):
                        out.append(_view(r, uid))
        return jsonify({"success": True, "rooms": out})

    @bp.route("/api/room/start", methods=["POST", "OPTIONS"])
    def room_start():
        if request.method == "OPTIONS":
            return "", 204
        user, data, err = post()
        if err:
            return err
        r, e = need(data.get("id"), user)
        if e:
            return e
        if r["host"] != str(user["id"]):
            return wa._err("Solo chi ha creato la stanza può avviare.", 403)
        if len(r["players"]) < 2:
            return wa._err("Servono almeno 2 giocatori.")
        if r["game"] == "race":
            now0 = _now()
            late = [p["name"] for p in r["players"] if p["uid"] != r["host"] and now0 - p["seen"] < 30 and not p.get("ready")]
            if late:
                return wa._err("Non tutti sono pronti: " + ", ".join(late[:3]))
        with _L:
            r["go"] = {}; r["rdy"] = {}; r["q"] = {}; r["gridv"] = None; r.pop("closeAt", None)
            r["status"] = "run"; r["act"] = _now(); r["events"].append({"k": "start", "t": round(_now(), 3), "seed": secrets.randbelow(2 ** 31), "order": [p["uid"] for p in r["players"]]})
        return jsonify({"success": True, "room": _view(r, str(user["id"]))})

    @bp.route("/api/room/send", methods=["POST", "OPTIONS"])
    def room_send():
        if request.method == "OPTIONS":
            return "", 204
        user, data, err = post()
        if err:
            return err
        uid = str(user["id"]); r, e = need(data.get("id"), user)
        if e:
            return e
        ev = data.get("ev")
        if not isinstance(ev, dict) or len(repr(ev)) > EV_BYTES:
            return wa._err("Evento non valido.")
        if not any(p["uid"] == uid for p in r["players"]):
            return wa._err("Non sei in questa stanza.", 403)
        with _L:
            ev = dict(ev); ev["u"] = uid; ev["t"] = round(_now(), 3)
            r["events"].append(ev); r["act"] = _now()
            if len(r["events"]) > MAX_EV:                    # i vecchi non servono: gli indici restano coerenti con "base"
                cut = len(r["events"]) - MAX_EV; r["events"] = r["events"][cut:]; r["base"] = r.get("base", 0) + cut
        return jsonify({"success": True, "n": r.get("base", 0) + len(r["events"])})

    @bp.route("/api/room/poll", methods=["GET", "OPTIONS"])
    def room_poll():
        if request.method == "OPTIONS":
            return "", 204
        user, err = wa.require_admin()
        if err:
            return err
        uid = str(user["id"]); r, e = need(request.args.get("id"), user)
        if e:
            return e
        try:
            since = int(request.args.get("since", "0"))
        except ValueError:
            since = 0
        with _L:
            for p in r["players"]:
                if p["uid"] == uid:
                    p["seen"] = _now()
            base = r.get("base", 0)
            evs = [e for e in r["events"][max(0, since - base):] if "to" not in e or uid in e["to"]]
            v = _view(r, uid); v["n"] = base + len(r["events"])
        return jsonify({"success": True, "room": v, "events": evs, "from": max(since, base)})

    @bp.route("/api/room/result", methods=["POST", "OPTIONS"])
    def room_result():
        if request.method == "OPTIONS":
            return "", 204
        user, data, err = post()
        if err:
            return err
        uid = str(user["id"]); r, e = need(data.get("id"), user)
        if e:
            return e
        order = data.get("order")
        if not isinstance(order, list) or not all(isinstance(x, str) for x in order) or len(order) > 8:
            return wa._err("Classifica non valida.")
        if r["status"] == "done":
            return jsonify({"success": True, "result": r.get("settled")})
        ch = data.get("chips")
        chips = ()
        if isinstance(ch, dict) and len(ch) <= 8:
            try:
                chips = tuple(sorted((str(k), int(v)) for k, v in ch.items() if 0 <= int(v) <= 10 ** 7))
            except (TypeError, ValueError):
                return wa._err("Fiches non valide.")
        with _L:
            r["votes"][uid] = ([str(x) for x in order], chips); r["act"] = _now()
        resp, ok = wa._avi_call(_co(lambda: _settle_locked(r)))
        if resp is not None:
            return resp
        return jsonify({"success": True, "settled": bool(ok), "result": r.get("settled")})

    def _settle_locked(r):
        with _L:
            if r["status"] == "done":
                return True
            ok = _settle(r)
            if not ok and len(r["votes"]) >= len(r["players"]) >= 2:      # tutti hanno votato ma non concordano: nessuno vince, puntate restituite
                _refund(r)
                r["settled"] = {"order": [], "pay": {}, "sim": r["sim"], "refund": True}
                r["status"] = "done"; ok = True
            return ok

    @bp.route("/api/room/topup", methods=["POST", "OPTIONS"])
    def room_topup():
        """Ricompra al tavolo (poker): i soldi escono dal saldo vero e vanno nel piatto del tavolo."""
        if request.method == "OPTIONS":
            return "", 204
        user, data, err = post()
        if err:
            return err
        uid = str(user["id"]); r, e = need(data.get("id"), user)
        if e:
            return e
        me = next((p for p in r["players"] if p["uid"] == uid), None)
        if not me or r["status"] != "run" or r["game"] != "poker":
            return wa._err("Ricompra non possibile.")
        try:
            amt = int(data.get("amount", 0))
        except (TypeError, ValueError):
            amt = 0
        if amt <= 0 or amt > MAX_STAKE or me.get("topped", 0) + amt > 4 * MAX_STAKE:
            return wa._err("Importo non valido.")
        if not r["sim"]:
            resp, ok = wa._avi_call(_co(_wallet_ok, uid, amt))
            if resp is not None:
                return resp
            if not ok:
                return wa._err("Saldo insufficiente.")
            resp, _ = wa._avi_call(_co(_move, uid, -amt))
            if resp is not None:
                return resp
            with _L:
                me["paid"] = me.get("paid", 0) + amt
        with _L:
            me["topped"] = me.get("topped", 0) + amt; r["act"] = _now()
        return jsonify({"success": True})

    @bp.route("/api/room/cfg", methods=["POST", "OPTIONS"])
    def room_cfg():
        if request.method == "OPTIONS":
            return "", 204
        user, data, err = post()
        if err:
            return err
        r, e = need(data.get("id"), user)
        if e:
            return e
        if r["host"] != str(user["id"]) or r["status"] != "lobby":
            return wa._err("Solo l'host può cambiare le regole prima del via.", 403)
        c = data.get("cfg") if isinstance(data.get("cfg"), dict) else {}
        with _L:
            if "track" in c:
                r["cfg"]["track"] = str(c["track"])[:16]
            for k in ("laps", "prize", "hands"):
                if k in c:
                    r["cfg"][k] = max(0, min(5000, int(c[k] or 0)))
            if "x" in c:
                r["cfg"]["x"] = _x(c["x"])
            r["act"] = _now()
        return jsonify({"success": True})

    @bp.route("/api/room/sync", methods=["POST", "OPTIONS"])
    def room_sync():
        """Un'unica richiesta per ciclo: manda (facoltativo) il mio evento e riceve quelli nuovi."""
        if request.method == "OPTIONS":
            return "", 204
        user, data, err = post()
        if err:
            return err
        uid = str(user["id"])
        with _L:
            _gc()
        r = ROOMS.get(str(data.get("id", "")))
        if not r:
            return jsonify({"success": True, "gone": True, "events": [], "n": 0})
        if not any(p["uid"] == uid for p in r["players"]) and uid not in r["invited"]:
            return wa._err("Non sei in questa stanza.", 403)
        ev = data.get("ev")
        try:
            since = int(data.get("since", 0))
        except (TypeError, ValueError):
            since = 0
        with _L:
            for p in r["players"]:
                if p["uid"] == uid:
                    p["seen"] = _now()
            if isinstance(ev, dict) and len(repr(ev)) <= EV_BYTES and any(p["uid"] == uid for p in r["players"]):
                ev = dict(ev); ev["u"] = uid; ev["t"] = round(_now(), 3)
                k0 = ev.get("k"); ph0 = str(ev.get("ph", ""))[:6]
                if k0 == "rdy":                               # pronto alla partenza (fine presentazione): lo stato sta nella stanza, non si perde
                    lst = r.setdefault("rdy", {}).setdefault(ph0, [])
                    if uid not in lst:
                        lst.append(uid)
                    ev = None
                elif k0 == "q":                               # tempo di qualifica (il primo vale)
                    try:
                        qt = float(ev.get("qt"))
                    except (TypeError, ValueError):
                        qt = 9999.0
                    r.setdefault("q", {}).setdefault(uid, round(qt, 3)); ev = None
                elif k0 == "grid":                            # griglia decisa: la prima che arriva vale per tutti
                    g = ev.get("g")
                    if r.get("gridv") is None and isinstance(g, list) and all(isinstance(x, str) for x in g[:12]):
                        r["gridv"] = g[:12]
                    ev = None
                elif ev.get("k") == "go":                       # via della partenza: il primo che lo manda decide l'istante (orologio del server) per tutti
                    ph = str(ev.get("ph", ""))[:6]; gos = r.setdefault("go", {})
                    if ph in gos:
                        ev = None
                    else:
                        ev["G"] = round(_now() + 1.5, 3); gos[ph] = ev["G"]
                if ev is not None and "to" in ev and not (isinstance(ev["to"], list) and all(isinstance(x, str) for x in ev["to"])):
                    ev.pop("to")
                if ev is not None:
                    r["events"].append(ev)
                r["act"] = _now()
                if len(r["events"]) > MAX_EV:
                    cut = len(r["events"]) - MAX_EV; r["events"] = r["events"][cut:]; r["base"] = r.get("base", 0) + cut
            base = r.get("base", 0)
            evs = [e for e in r["events"][max(0, since - base):] if "to" not in e or uid in e["to"]]
            v = _view(r, uid); n = base + len(r["events"])
        return jsonify({"success": True, "room": v, "events": evs, "n": n, "from": max(since, base)})
