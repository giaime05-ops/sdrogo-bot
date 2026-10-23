"""Mini app, sezione Extra (Attività): /lavora a mini quiz e /dona con elenco dei giocatori del gruppo."""
import re
import time

from economy import (make_puzzle, work_cooldown_left, work_set_cooldown, WORK_REWARD, WORK_TIME, WORK_MAX_BALANCE,
                     main_chat_id, fmt)
from state import USER_DATA, DAILY_DONATIONS
from storage import ukey, add_user_coins, get_user_data_dict, save_db, log_play, is_web_sandbox, chat_user_ids
from utils import is_v2

WEB_WORK = {}                   # uid -> {"puzzle", "deadline", "done"}
DONATE_MAX = 50
GRACE = 4                       # secondi di tolleranza per la rete


def _bal(uid):
    return int((USER_DATA.get(ukey(uid)) or {}).get("coins", 0))


def _plain(t):
    return re.sub(r"<[^>]+>", "", t or "").strip()


def work_state(uid: int) -> dict:
    sbx = is_web_sandbox()
    v2u = is_v2(uid)
    cd = 0 if sbx else work_cooldown_left(uid, uid)
    too_rich = (WORK_MAX_BALANCE is not None and _bal(uid) >= WORK_MAX_BALANCE and not sbx)
    from economy import rome_now, today_str
    n = rome_now()
    wheel_ok = ((get_user_data_dict(uid, uid).get("last_daily") or "") != today_str()) or sbx
    return {"wheel_available": wheel_ok, "wheel_in": 86400 - (n.hour * 3600 + n.minute * 60 + n.second),
            "v2": v2u, "reward": WORK_REWARD, "time": WORK_TIME, "cooldown": cd, "max_balance": WORK_MAX_BALANCE,
            "too_rich": too_rich, "balance": _bal(uid), "sandbox": sbx, "loan": loan_state(uid)}


def loan_state(uid: int) -> dict:
    from economy import LOAN_OPTIONS, LOAN_TRIGGER, LOAN_FEE, LOAN_CUT, LOAN_DAILY_MAX, today_str, loan_get
    u = get_user_data_dict(uid, uid)
    L = loan_get(uid)
    ld = u.get("loan_day") or {}
    taken = int(ld.get("taken", 0)) if ld.get("day") == today_str() else 0
    sbx = is_web_sandbox()
    left = LOAN_DAILY_MAX if sbx else max(0, LOAN_DAILY_MAX - taken)
    bal = _bal(uid)
    tot = int(L.get("total", L["owed"])) if L else 0
    return {"owed": int(L["owed"]) if L else 0, "total": tot, "paid": max(0, tot - int(L["owed"])) if L else 0,
            "amount": int(L.get("amount", 0)) if L else 0, "balance": bal, "trigger": LOAN_TRIGGER, "fee": int(LOAN_FEE * 100),
            "cut": int(LOAN_CUT * 100), "daily_left": left, "daily_max": LOAN_DAILY_MAX,
            "options": [o for o in LOAN_OPTIONS if o <= left],
            "eligible": (L is None and (bal < LOAN_TRIGGER or sbx) and left > 0), "low": bal < LOAN_TRIGGER or sbx}


def loan_take(uid: int, amount) -> dict:
    from economy import LOAN_OPTIONS, LOAN_FEE, today_str, banco_add
    st = loan_state(uid)
    try:
        amount = int(amount)
    except (TypeError, ValueError):
        return {"error": "Importo non valido.", "code": 400}
    if st["owed"] > 0:
        return {"error": "Hai già un prestito attivo: restituiscilo prima.", "code": 409}
    if st["balance"] >= st["trigger"] and not is_web_sandbox():
        return {"error": f"Il Banco presta solo a chi ha meno di {st['trigger']} $SDG.", "code": 409}
    if amount not in LOAN_OPTIONS or amount > st["daily_left"]:
        return {"error": "Importo non disponibile oggi.", "code": 400}
    u = get_user_data_dict(uid, uid)
    owed = int(round(amount * (1 + LOAN_FEE)))
    u["loan"] = {"owed": owed, "amount": amount, "total": owed, "day": today_str()}
    ld = u.get("loan_day") or {}
    if ld.get("day") != today_str():
        ld = {"day": today_str(), "taken": 0}
    ld["taken"] = int(ld["taken"]) + amount
    u["loan_day"] = ld
    add_user_coins(uid, uid, amount)
    save_db()
    out = loan_state(uid)
    out["balance"] = _bal(uid)
    out["msg"] = f"Il Banco ti presta {amount} $SDG. Devi restituirne {owed}."
    return out


def work_start(uid: int) -> dict:
    st = work_state(uid)
    if not st["v2"]:
        return {"error": "Disponibile solo per chi ha la V2.", "code": 403}
    if st["too_rich"]:
        return {"error": f"/lavora è per chi ha meno di {WORK_MAX_BALANCE} $SDG: vai a giocare!", "code": 409}
    if st["cooldown"] > 0:
        return {"error": "Sei ancora in pausa: riprova più tardi.", "code": 409, "state": st}
    p = make_puzzle()
    WEB_WORK[uid] = {"puzzle": p, "deadline": time.time() + WORK_TIME + GRACE, "done": False}
    if not is_web_sandbox():
        work_set_cooldown(uid, uid)                       # come nel bot: la pausa parte appena si inizia
    return {"puzzle": {"text": _plain(p["text"]), "options": p["options"]}, "time": WORK_TIME}


def work_answer(uid: int, idx: int) -> dict:
    g = WEB_WORK.get(uid)
    if not g or g["done"]:
        return {"error": "Sfida già conclusa.", "code": 409}
    g["done"] = True
    WEB_WORK.pop(uid, None)
    if time.time() > g["deadline"]:
        return {"result": {"ok": False, "late": True, "message": "Tempo scaduto: niente paga."}, "balance": _bal(uid)}
    right = idx == g["puzzle"]["ans"]
    if right:
        add_user_coins(uid, uid, WORK_REWARD)
        log_play(uid, "lavora", 0, WORK_REWARD, "vinta")
    return {"result": {"ok": bool(right), "answer": g["puzzle"]["ans"],
                       "message": f"Esatto! +{WORK_REWARD} $SDG" if right else "Risposta sbagliata: niente paga."},
            "balance": _bal(uid)}


def donors(uid: int) -> dict:
    cid = main_chat_id()
    out = []
    if cid:
        for k in chat_user_ids(cid):
            if str(k).startswith("sbx_") or str(k) == str(uid):
                continue
            d = USER_DATA.get(k) or {}
            name = (d.get("name") or "").strip()
            if name:
                out.append({"id": str(k), "name": name})
    out.sort(key=lambda x: x["name"].lower())
    return {"players": out, "max": DONATE_MAX, "balance": _bal(uid), "sandbox": is_web_sandbox()}


def donate(uid: int, target: str, amount) -> dict:
    try:
        amount = int(amount)
    except (TypeError, ValueError):
        return {"error": "Importo non valido.", "code": 400}
    if amount < 1 or amount > DONATE_MAX:
        return {"error": f"Puoi donare da 1 a {DONATE_MAX} $SDG per volta.", "code": 400}
    target = str(target)
    cid = main_chat_id()
    if not cid or target not in chat_user_ids(cid) or target.startswith("sbx_") or target == str(uid):
        return {"error": "Destinatario non valido.", "code": 400}
    if _bal(uid) < amount:
        return {"error": "Non hai abbastanza $SDG.", "code": 409}
    from economy import today_str, loan_get
    if loan_get(uid):
        return {"error": "Finché hai un prestito col Banco non puoi donare.", "code": 409}
    day = today_str()
    DAILY_DONATIONS.difference_update({k for k in DAILY_DONATIONS if not k.endswith(day)})
    key = f"{cid}_{uid}_{target}_{day}"
    sbx = is_web_sandbox()
    if key in DAILY_DONATIONS and not sbx:
        return {"error": "Hai già fatto una donazione a questa persona oggi: riprova domani.", "code": 409}
    add_user_coins(uid, uid, -amount)
    name = (USER_DATA.get(target) or {}).get("name", "")
    if not sbx:                                           # nel profilo di prova la donazione è simulata
        add_user_coins(int(cid), int(target), amount)
        DAILY_DONATIONS.add(key)
    return {"message": f"Hai donato {fmt(amount)} $SDG a {name}" + (" (simulato)" if sbx else "") + "!", "balance": _bal(uid)}
