"""Shop, inventario, profilo completo e strumenti di prova admin per la mini app (funzioni sincrone, girano sul loop del bot)."""
import re
import time

import economy
from economy import inv, fmt, today_str, quests_for, spin_daily, DAILY_WHEEL, main_chat_id
from state import USER_DATA, USER_INVENTORIES, MUTE_FILTER
from storage import ukey, add_user_coins, get_user_data_dict, save_db, is_web_sandbox, find_user_id
from handlers import v2
from utils import is_v2


def _plain(t: str) -> str:
    return re.sub(r"<[^>]+>", "", t or "").replace("👉 ", "").strip()


def _bal(uid):
    return int((USER_DATA.get(ukey(uid)) or {}).get("coins", 0))


def wallet(uid: int) -> dict:
    u = get_user_data_dict(uid, uid)
    d = inv(uid, uid)
    c_wins, d_wins = u.get("casino_wins", 0), u.get("duels_wins", 0)
    sp, dp = u.get("single_played", 0), u.get("duels_played", 0)
    sw = u.get("single_won", c_wins)
    return {
        "balance": int(u.get("coins", 0)), "net_profit": int(u.get("net_profit", 0)), "stars": int(d.get("stars", 0)),
        "quizzes_won": int(u.get("quizzes_won", 0)), "casino_wins": int(c_wins), "duels_wins": int(d_wins),
        "wr_single": min(100, int(sw / sp * 100)) if sp > 0 else 0,
        "wr_duels": min(100, int(d_wins / dp * 100)) if dp > 0 else 0,
        "wheel_prizes": [{"prize": p, "pct": w} for p, w in DAILY_WHEEL],
        "wheel_available": (u.get("last_daily") or "") != today_str() or is_web_sandbox(),
        "sandbox": is_web_sandbox(),
    }


def shop(uid: int) -> dict:
    v2u = is_v2(uid)
    items = []
    if v2u:
        d = inv(uid, uid)
        for key, field, price, label, short, how in v2.SHOP_ITEMS:
            meta = v2.SHOP_META.get(key, {})
            lim = meta.get("limit")
            left = None if lim is None else (lim if is_web_sandbox() else v2.shop_left_today(uid, uid, key))
            items.append({"key": key, "label": label, "price": price, "short": short, "how": _plain(how),
                          "cat": meta.get("cat", "aiuti"), "limit": lim, "left": left, "owned": int(d.get(field, 0))})
    return {"v2": v2u, "items": items, "balance": _bal(uid), "inventory": inventory(uid)}


def shop_buy(uid: int, key: str) -> dict:
    if not is_v2(uid):
        return {"error": "Lo Shop non è ancora disponibile.", "code": 403}
    spec = v2.SHOP_BY_KEY.get(key)
    if not spec or key == "title" or key not in v2.SHOP_META:
        return {"error": "Oggetto sconosciuto.", "code": 404}
    _k, field, price, label, _s, how = spec
    sbx = is_web_sandbox()
    if not sbx and v2.shop_left_today(uid, uid, key) <= 0:
        return {"error": f"Limite giornaliero raggiunto per {_plain(label)}: riprova domani.", "code": 409}
    if _bal(uid) < price:
        return {"error": f"Servono {fmt(price)} $SDG.", "code": 409}
    d = inv(uid, uid)
    d[field] = d.get(field, 0) + 1
    if not sbx:
        v2.shop_record(uid, uid, key)
    add_user_coins(uid, uid, -price)
    return {"message": f"{_plain(label)} acquistato!", "how": _plain(how), "shop": shop(uid)}


def inventory(uid: int) -> dict:
    d = inv(uid, uid)
    return {
        "items": [
            {"key": "boostwin", "label": "Boost Vincita ×1.5", "count": int(d["boost_win"]), "active": int(d["boost_win_left"]),
             "unit": "partite", "activatable": True},
            {"key": "boostluck", "label": "Boost Fortuna", "count": int(d["boost_luck"]), "active": int(d["boost_luck_left"]),
             "unit": "partite", "activatable": True},
            {"key": "mutefilter", "label": _plain(v2.MUTE_FILTER_LABEL), "count": int(d["mutefilter"]), "active": 0,
             "unit": "", "activatable": True},
            {"key": "persecute", "label": "Perseguita", "count": int(d["persecutes"]), "active": 0, "unit": "", "activatable": False,
             "how": _plain(v2.SHOP_BY_KEY["persecute"][5])},
            {"key": "silence", "label": "Silenzia", "count": int(d["silence"]), "active": 0, "unit": "", "activatable": False,
             "how": _plain(v2.SHOP_BY_KEY["silence"][5])},
            {"key": "sang", "label": "Sanguisuga", "count": int(d["sang"]), "active": 0, "unit": "", "activatable": False,
             "how": _plain(v2.SHOP_BY_KEY["sang"][5])},
            {"key": "title", "label": "Titolo Umiliante", "count": int(d["titles"]), "active": 0, "unit": "", "activatable": False,
             "how": _plain(v2.SHOP_BY_KEY["title"][5])},
        ],
        "stars": int(d.get("stars", 0)),
    }


def inv_activate(uid: int, key: str) -> dict:
    if not is_v2(uid):
        return {"error": "Non ancora disponibile.", "code": 403}
    d = inv(uid, uid)
    if key == "boostwin":
        if d["boost_win"] <= 0:
            return {"error": "Non hai Boost Vincita.", "code": 409}
        if d["boost_win_left"] > 0:
            return {"error": f"Boost Vincita già attivo ({d['boost_win_left']} partite).", "code": 409}
        d["boost_win"] -= 1
        d["boost_win_left"] = 10
        msg = "Boost Vincita attivo per 10 partite!"
    elif key == "boostluck":
        if d["boost_luck"] <= 0:
            return {"error": "Non hai Boost Fortuna.", "code": 409}
        if d["boost_luck_left"] > 0:
            return {"error": f"Boost Fortuna già attivo ({d['boost_luck_left']} partite).", "code": 409}
        d["boost_luck"] -= 1
        d["boost_luck_left"] = 15
        msg = "Boost Fortuna attivo per 15 partite!"
    elif key == "mutefilter":
        from config import MUTE_FILTER_TARGETS
        cid = main_chat_id()
        if d["mutefilter"] <= 0:
            return {"error": "Non hai il filtro.", "code": 409}
        if not MUTE_FILTER_TARGETS or not cid:
            return {"error": "Filtro non configurato: oggetto non consumato.", "code": 409}
        d["mutefilter"] -= 1
        if not is_web_sandbox():
            until = time.time() + v2.MUTE_FILTER_MINUTES * 60
            for uname in MUTE_FILTER_TARGETS:
                MUTE_FILTER[f"{cid}_{uname}"] = until
        msg = f"Filtro attivo per {v2.MUTE_FILTER_MINUTES} minuti!"
    else:
        return {"error": "Questo oggetto si usa scrivendo nella chat del gruppo.", "code": 400}
    save_db()
    return {"message": msg, "inventory": inventory(uid)}


# ---------------------------------------------------------------- strumenti di prova (solo account admin)
def admin_tool(uid: int, what: str, value=None) -> dict:
    if not is_web_sandbox():
        return {"error": "Funzione riservata all'account admin di prova.", "code": 403}
    u = get_user_data_dict(uid, uid)
    if what == "coins":
        u["coins"] = max(0, min(int(value or 0), 10 ** 12))
    elif what == "reset":
        u["coins"] = 30_000_000
        u.pop("quests", None)
        u["last_daily"] = ""
    elif what == "quests_new":
        u.pop("quests", None)
        quests_for(uid, uid)
    elif what == "quests_done":
        for q in quests_for(uid, uid):
            if not q.get("done"):
                q["progress"] = q["target"]
                q["done"] = True
                q["claimed"] = False
    elif what == "wheel":
        u["last_daily"] = ""
    elif what == "items":
        d = inv(uid, uid)
        for k in ("titles", "persecutes", "silence", "mutefilter", "boost_win", "boost_luck", "sang"):
            d[k] = d.get(k, 0) + 5
        d["stars"] = d.get("stars", 0) + 5
    else:
        return {"error": "Strumento sconosciuto.", "code": 404}
    save_db()
    return {"message": "Fatto.", "balance": int(u.get("coins", 0))}
