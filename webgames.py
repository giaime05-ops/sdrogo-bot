"""Giochi a più mosse della mini app (Fase 4): Blackjack, Wordle, Mastermind, Sdrogo Poker.

Stesse regole, stessi premi e stessa economia dei giochi nel bot: qui si riusano le sue funzioni (handlers/games.py)
e le sue stesse tabelle di partite in corso (BLACKJACK_GAMES, WORDLE_GAMES, MASTERMIND_GAMES, POKER_GAMES),
così il rimborso automatico delle partite abbandonate e il reset admin valgono anche per la mini app.

Tutte le funzioni sono coroutine e girano SUL LOOP DEL BOT (le chiama webapi.py con run_coroutine_threadsafe).
Ritornano un dict: {"state": ..., "result": ...} oppure {"error": ..., "code": ...}.
Il server non rivela mai informazioni nascoste (seconda carta del banco, carte del bot, parola/codice segreto)
finché la partita non è finita.
"""
import itertools
import logging
import random
import re
import time

import economy
from state import (USER_DATA, BLACKJACK_GAMES, WORDLE_GAMES, MASTERMIND_GAMES, POKER_GAMES, game_enabled)
from storage import ukey, add_user_coins
from utils import is_v2


def _g():
    from handlers import games as G       # import tardivo: evita dipendenze circolari all'avvio
    return G


def _bot():
    return economy._BOT


def _bal(uid: int) -> int:
    return int((USER_DATA.get(ukey(uid)) or {}).get("coins", 0))


def _clean(notes, extra=None):
    out = [re.sub(r"<[^>]+>", "", n).strip() for n in (notes or []) if n]
    if extra:
        out.append(re.sub(r"<[^>]+>", "", extra).strip())
    return out


def _err(msg, code=400, state=None):
    d = {"error": msg, "code": code}
    if state is not None:
        d["state"] = state
    return d


def _key(uid: int) -> str:
    return f"{uid}_{uid}"


def _fresh(game) -> bool:
    """Partita ancora valida (non scaduta per inattività)."""
    return bool(game) and time.time() - game.get("last", game.get("ts", 0)) <= _g().GAME_IDLE_SECS


# ======================================================================================================
# BLACKJACK
# ======================================================================================================
def _bj_state(uid: int, game=None) -> dict:
    G = _g()
    game = game if game is not None else BLACKJACK_GAMES.get(_key(uid))
    out = {"balance": _bal(uid), "bets": list(G.BJ_BETS), "enabled": bool(game_enabled("blackjack")), "active": False}
    if _fresh(game):
        out.update(active=True, bet=game["bet"], player=list(game["player_hand"]),
                   total=G.hand_value(game["player_hand"]), dealer=list(game["dealer_hand"][:1]),
                   charlie=G.BJ_CHARLIE_CARDS)
    return out


async def _bj_finish(uid: int, game: dict, outcome: str, base: int) -> dict:
    G = _g()
    BLACKJACK_GAMES.pop(_key(uid), None)
    bet = game["bet"]
    paid, notes = await G._settle(_bot(), uid, uid, "blackjack", bet, base)
    q = G.quest_progress(uid, uid, "bj_win") if paid > bet else ""
    return {"state": _bj_state(uid, None),
            "result": {"outcome": outcome, "bet": bet, "player": list(game["player_hand"]), "player_total": G.hand_value(game["player_hand"]),
                       "dealer": list(game["dealer_hand"]), "dealer_total": G.hand_value(game["dealer_hand"]),
                       "paid": paid, "net": paid - bet, "notes": _clean(notes, q), "balance": _bal(uid)}}


async def bj_state(uid, name, data):
    return {"state": _bj_state(uid)}


async def bj_start(uid, name, data):
    G = _g()
    try:
        bet = int(data.get("bet"))
    except (TypeError, ValueError):
        return _err("Puntata non valida.")
    if not game_enabled("blackjack"):
        return _err("Il Blackjack è in manutenzione.", 503)
    if bet not in G.BJ_BETS:
        return _err("Puntata non valida.")
    key = _key(uid)
    old = BLACKJACK_GAMES.get(key)
    if old and _fresh(old):                         # mano già pagata: la si riprende, nessun nuovo addebito
        return {"state": _bj_state(uid, old), "resumed": True}
    if old:                                         # scaduta: come fa il rimborso automatico del bot
        BLACKJACK_GAMES.pop(key, None)
        add_user_coins(uid, uid, old.get("bet", 0))
    if _bal(uid) < bet:
        return _err(f"Servono {bet} $SDG per questa puntata.")
    G._charge(uid, uid, bet)
    now = time.time()
    game = {"player_id": uid, "name": name, "bet": bet, "ts": now, "last": now,
            "luck": G.has_luck(uid, uid, "blackjack"),
            "player_hand": [random.choice(G.BJ_PLAYER_CARDS), random.choice(G.BJ_PLAYER_CARDS)]}
    game["dealer_hand"] = [random.choice(G._bj_dealer_deck(game))]
    BLACKJACK_GAMES[key] = game
    if G.hand_value(game["player_hand"]) == 21:     # blackjack naturale: 3:2
        return await _bj_finish(uid, game, "blackjack", int(bet * 2.5))
    return {"state": _bj_state(uid, game)}


async def bj_hit(uid, name, data):
    G = _g()
    game = BLACKJACK_GAMES.get(_key(uid))
    if not _fresh(game):
        return _err("Nessuna mano in corso.", 409)
    game["last"] = time.time()
    game["player_hand"].append(random.choice(G.BJ_PLAYER_CARDS))
    if G.hand_value(game["player_hand"]) > 21:
        return await _bj_finish(uid, game, "bust", 0)
    if len(game["player_hand"]) >= G.BJ_CHARLIE_CARDS:
        return await _bj_finish(uid, game, "charlie", game["bet"] * 2)
    return {"state": _bj_state(uid, game)}


async def bj_stand(uid, name, data):
    G = _g()
    game = BLACKJACK_GAMES.get(_key(uid))
    if not _fresh(game):
        return _err("Nessuna mano in corso.", 409)
    game["last"] = time.time()
    bet = game["bet"]
    ps = G.hand_value(game["player_hand"])
    dealer = game["dealer_hand"]
    deck = G._bj_dealer_deck(game)
    while G.hand_value(dealer) < 17:
        dealer.append(random.choice(deck))
    ds = G.hand_value(dealer)
    if ds > 21 or ps > ds:
        return await _bj_finish(uid, game, "win", bet * 2)
    if ps < ds:
        return await _bj_finish(uid, game, "lose", 0)
    return await _bj_finish(uid, game, "push", bet)


# ======================================================================================================
# WORDLE EXPRESS  (10 $SDG, 5 tentativi)
# ======================================================================================================
WD_COST = 10
WD_MAX = 5


def _wd_marks(guess: str, secret: str) -> str:
    """G = posto giusto, Y = lettera presente altrove, B = assente (stesso algoritmo del bot)."""
    res = ["B"] * 5
    rest = list(secret)
    for i in range(5):
        if guess[i] == secret[i]:
            res[i] = "G"
            rest[i] = None
    for i in range(5):
        if res[i] != "G" and guess[i] in rest:
            res[i] = "Y"
            rest[rest.index(guess[i])] = None
    return "".join(res)


def _wd_state(uid: int, game=None) -> dict:
    G = _g()
    game = game if game is not None else WORDLE_GAMES.get(_key(uid))
    out = {"balance": _bal(uid), "cost": WD_COST, "max": WD_MAX, "enabled": bool(game_enabled("wordle")) and bool(G.DICT_LOADED),
           "rewards": {str(k): v for k, v in G.WORDLE_REWARDS.items()}, "active": False}
    if _fresh(game):
        out.update(active=True, attempts=game["attempts"], guesses=list(game.get("web_hist", [])))
    return out


async def wd_state(uid, name, data):
    return {"state": _wd_state(uid)}


async def wd_start(uid, name, data):
    G = _g()
    if not game_enabled("wordle"):
        return _err("Wordle è in manutenzione.", 503)
    if not G.DICT_LOADED:
        return _err("Dizionario Wordle non disponibile: avvisa l'admin.", 503)
    key = _key(uid)
    old = WORDLE_GAMES.get(key)
    if old and _fresh(old):
        return {"state": _wd_state(uid, old), "resumed": True}
    if old:
        WORDLE_GAMES.pop(key, None)
        add_user_coins(uid, uid, WD_COST)
    if _bal(uid) < WD_COST:
        return _err(f"Servono {WD_COST} $SDG per giocare.")
    G._charge(uid, uid, WD_COST)
    now = time.time()
    game = {"player_id": uid, "name": name, "secret": random.choice(G.WORDLE_WORDS), "attempts": 0,
            "history": [], "web_hist": [], "ts": now, "last": now}
    WORDLE_GAMES[key] = game
    return {"state": _wd_state(uid, game)}


async def wd_guess(uid, name, data):
    G = _g()
    key = _key(uid)
    game = WORDLE_GAMES.get(key)
    if not _fresh(game):
        return _err("Nessuna partita in corso.", 409)
    raw = str(data.get("word", "")).strip().upper()
    if len(raw) < 5 or not raw.isalpha():
        return _err("Scrivi una parola di 5 lettere.")
    guess = G.norm_word(raw)
    if len(guess) != 5:
        return _err("Scrivi una parola di 5 lettere.")
    if guess not in G.WORD_SET:
        return _err("Questa parola non è nel dizionario: il tentativo non conta.")
    game["last"] = time.time()
    game["attempts"] += 1
    secret = game["secret"]
    marks = _wd_marks(guess, secret)
    game.setdefault("web_hist", []).append({"word": guess, "marks": marks})
    done_won = guess == secret
    done_lost = (not done_won) and game["attempts"] >= WD_MAX
    if not (done_won or done_lost):
        return {"state": _wd_state(uid, game), "marks": marks}
    WORDLE_GAMES.pop(key, None)
    base = G.WORDLE_REWARDS.get(game["attempts"], G.WORDLE_REWARDS[5]) if done_won else 0
    paid, notes = await G._settle(_bot(), uid, uid, "wordle", WD_COST, base, is_casino=False)
    q = G.quest_progress(uid, uid, "wordle_win") if done_won else ""
    return {"state": _wd_state(uid, None), "marks": marks,
            "result": {"won": done_won, "secret": secret, "attempts": game["attempts"], "guesses": game["web_hist"],
                       "paid": paid, "net": paid - WD_COST, "notes": _clean(notes, q), "balance": _bal(uid)}}


# ======================================================================================================
# MASTERMIND EXPRESS  (10 $SDG, codice di 3 cifre diverse, 5 tentativi)
# ======================================================================================================
MM_COST = 10
MM_MAX = 5


def _mm_state(uid: int, game=None) -> dict:
    G = _g()
    game = game if game is not None else MASTERMIND_GAMES.get(_key(uid))
    out = {"balance": _bal(uid), "cost": MM_COST, "max": MM_MAX, "enabled": bool(game_enabled("mastermind")),
           "rewards": {str(k): v for k, v in G.MASTERMIND_REWARDS.items()}, "active": False}
    if _fresh(game):
        out.update(active=True, attempts=game["attempts"], guesses=list(game.get("web_hist", [])))
    return out


async def mm_state(uid, name, data):
    return {"state": _mm_state(uid)}


async def mm_start(uid, name, data):
    G = _g()
    if not game_enabled("mastermind"):
        return _err("Mastermind è in manutenzione.", 503)
    key = _key(uid)
    old = MASTERMIND_GAMES.get(key)
    if old and _fresh(old):
        return {"state": _mm_state(uid, old), "resumed": True}
    if old:
        MASTERMIND_GAMES.pop(key, None)
        add_user_coins(uid, uid, MM_COST)
    if _bal(uid) < MM_COST:
        return _err(f"Servono {MM_COST} $SDG per giocare.")
    G._charge(uid, uid, MM_COST)
    digits = list("0123456789")
    random.shuffle(digits)
    now = time.time()
    game = {"player_id": uid, "name": name, "secret": "".join(digits[:3]), "attempts": 0,
            "history": [], "web_hist": [], "ts": now, "last": now}
    MASTERMIND_GAMES[key] = game
    return {"state": _mm_state(uid, game)}


async def mm_guess(uid, name, data):
    G = _g()
    key = _key(uid)
    game = MASTERMIND_GAMES.get(key)
    if not _fresh(game):
        return _err("Nessuna partita in corso.", 409)
    code = str(data.get("code", "")).strip()
    if len(code) != 3 or not code.isdigit():
        return _err("Il codice ha 3 cifre.")
    if len(set(code)) < 3:
        return _err("Il codice ha 3 cifre tutte diverse: il tentativo non conta.")
    game["last"] = time.time()
    game["attempts"] += 1
    secret = game["secret"]
    hits = sum(1 for i in range(3) if code[i] == secret[i])
    present = sum(1 for i in range(3) if code[i] != secret[i] and code[i] in secret)
    game.setdefault("web_hist", []).append({"code": code, "hits": hits, "present": present})
    won = hits == 3
    lost = (not won) and game["attempts"] >= MM_MAX
    if not (won or lost):
        return {"state": _mm_state(uid, game), "hits": hits, "present": present}
    MASTERMIND_GAMES.pop(key, None)
    base = G.MASTERMIND_REWARDS.get(game["attempts"], G.MASTERMIND_REWARDS[5]) if won else 0
    paid, notes = await G._settle(_bot(), uid, uid, "mastermind", MM_COST, base, is_casino=False)
    q = G.quest_progress(uid, uid, "mm_win") if won else ""
    return {"state": _mm_state(uid, None), "hits": hits, "present": present,
            "result": {"won": won, "secret": secret, "attempts": game["attempts"], "guesses": game["web_hist"],
                       "paid": paid, "net": paid - MM_COST, "notes": _clean(notes, q), "balance": _bal(uid)}}


# ======================================================================================================
# SDROGO POKER (Texas Hold'em 1v1 contro il bot, stesse regole del bot)
# ======================================================================================================
def _card(c):
    return {"r": c[0], "s": c[1]}


def _pk_state(uid: int, game=None, footer: str = "") -> dict:
    G = _g()
    game = game if game is not None else POKER_GAMES.get(_key(uid))
    out = {"balance": _bal(uid), "bets": list(G.SP_BETS), "enabled": bool(game_enabled("poker")), "active": False}
    if not _fresh(game):
        return out
    coins = _bal(uid)
    bet = game["bet"]
    if game["phase"] == "respond":
        acts = ["check", "fold"] if coins >= bet else ["allin", "fold"]
    else:
        acts = ["check"] + (["raise"] if coins >= bet else (["allin"] if coins > 0 else [])) + ["fold"]
    out.update(active=True, bet=bet, street=game["street"], street_name=G.POKER_STREETS[game["street"]],
               board=[_card(c) for c in game["board"]], hole=[_card(c) for c in game["hole_p"]],
               pot_p=game["pot_p"], pot_b=game["pot_b"], phase=game["phase"], seq=game.get("seq", 0),
               actions=acts, footer=footer)
    return out


async def pk_state(uid, name, data):
    return {"state": _pk_state(uid)}


async def pk_start(uid, name, data):
    G = _g()
    if not is_v2(uid):
        return _err("Sdrogo Poker non è ancora disponibile.", 403)
    try:
        bet = int(data.get("bet"))
    except (TypeError, ValueError):
        return _err("Puntata non valida.")
    if bet not in G.SP_BETS:
        return _err("Puntata non valida.")
    if not game_enabled("poker"):
        return _err("Il Poker è in manutenzione.", 503)
    key = _key(uid)
    old = POKER_GAMES.get(key)
    if old and _fresh(old):
        return {"state": _pk_state(uid, old), "resumed": True}
    if old:
        POKER_GAMES.pop(key, None)
        add_user_coins(uid, uid, old.get("pot_p", old.get("bet", 0)))
    if _bal(uid) < bet:
        return _err(f"Servono {bet} $SDG per questa puntata.")
    G._charge(uid, uid, bet)
    deck = G._new_deck()
    now = time.time()
    game = {"name": name, "bet": bet, "deck": deck, "hole_p": [deck.pop(), deck.pop()], "hole_b": [deck.pop(), deck.pop()],
            "board": [], "street": 0, "phase": "act", "to_call": 0, "seq": 0, "pot_p": bet, "pot_b": bet,
            "ts": now, "last": now}
    POKER_GAMES[key] = game
    return {"state": _pk_state(uid, game)}


async def _pk_end(uid: int, game: dict, how: str) -> dict:
    """how: 'fold' (lascia il giocatore), 'bot_fold' (lascia il bot), 'showdown'."""
    G = _g()
    POKER_GAMES.pop(_key(uid), None)
    mine_n = theirs_n = None
    q = ""
    if how == "fold":
        base, outcome = 0, "fold"
    elif how == "bot_fold":
        base, outcome = G.poker_win_payout(game), "win"
    else:
        mine = G._best_hand(game["hole_p"] + game["board"])
        theirs = G._best_hand(game["hole_b"] + game["board"])
        mine_n, theirs_n = G.HAND_NAMES[mine[0]], G.HAND_NAMES[theirs[0]]
        if mine > theirs:
            base, outcome = G.poker_win_payout(game), "win"
            q = G.quest_progress(uid, uid, "poker:hand", mine[0])
        elif mine < theirs:
            base, outcome = 0, "lose"
        else:
            base, outcome = game["pot_p"], "push"
    try:
        paid, notes = await G._settle(_bot(), uid, uid, "poker", game["pot_p"], base)
    except Exception as e:                                   # errore imprevisto: la puntata non va persa
        logging.error(f"Poker (mini app) settle: {e}")
        add_user_coins(uid, uid, game["pot_p"])
        return _err("Errore nel calcolo della mano: puntata rimborsata.", 500, _pk_state(uid, None))
    return {"state": _pk_state(uid, None),
            "result": {"how": how, "outcome": outcome, "mine": mine_n, "theirs": theirs_n, "bet": game["bet"],
                       "board": [_card(c) for c in game["board"]], "hole": [_card(c) for c in game["hole_p"]],
                       "hole_b": [_card(c) for c in game["hole_b"]] if how == "showdown" else None,
                       "pot_p": game["pot_p"], "paid": paid, "net": paid - game["pot_p"],
                       "notes": _clean(notes, q), "balance": _bal(uid)}}


async def _pk_advance(uid: int, game: dict, footer: str) -> dict:
    game["street"] += 1
    if game["street"] > 3:
        game["street"] = 3
        return await _pk_end(uid, game, "showdown")
    game["board"] += [game["deck"].pop() for _ in range(3 if game["street"] == 1 else 1)]
    game["phase"] = "act"
    game["to_call"] = 0
    return {"state": _pk_state(uid, game, footer)}


async def pk_act(uid, name, data):
    G = _g()
    key = _key(uid)
    game = POKER_GAMES.get(key)
    if not _fresh(game):
        return _err("Questa mano è già terminata.", 409, _pk_state(uid, None))
    act = str(data.get("action", ""))
    try:
        seq = int(data.get("seq"))
    except (TypeError, ValueError):
        seq = -1
    if seq != game.get("seq", 0):                           # tasto di uno stato vecchio (doppio tocco): ignorato
        return {"state": _pk_state(uid, game), "ignored": True}
    game["seq"] = seq + 1
    game["last"] = time.time()
    bet = game["bet"]

    def undo():
        game["seq"] = seq

    if act == "fold":
        return await _pk_end(uid, game, "fold")

    if act == "check":
        if game["phase"] == "respond":                      # il bot ha puntato: vedi
            if _bal(uid) < bet:
                undo()
                return _err("Saldo insufficiente per vedere: usa ALL-IN o lascia.", 400, _pk_state(uid, game))
            G._charge(uid, uid, bet)
            game["pot_p"] += bet
            return await _pk_advance(uid, game, "Hai visto la puntata del bot.")
        s = G._bot_strength(game["hole_b"], game["board"])
        if G._bot_bets(s):
            game["phase"] = "respond"
            game["to_call"] = bet
            game["pot_b"] += bet
            return {"state": _pk_state(uid, game, f"Il bot punta {bet} $SDG: vedi o lasci?")}
        return await _pk_advance(uid, game, "Il bot passa.")

    if act == "allin":
        x = _bal(uid)
        if game["phase"] == "respond":
            if x >= bet:
                undo()
                return _err("Hai abbastanza saldo: usa Vedi.", 400, _pk_state(uid, game))
        elif x >= bet or x <= 0:
            undo()
            return _err("ALL-IN non disponibile.", 400, _pk_state(uid, game))
        if x > 0:
            G._charge(uid, uid, x)
        game["pot_p"] += x
        if game["phase"] == "respond":
            game["pot_b"] -= (bet - x)                      # il bot riprende la parte che l'All-In non copre
        else:
            s = G._bot_strength(game["hole_b"], game["board"])
            if not G._bot_calls_raise(s, 3):
                return await _pk_end(uid, game, "bot_fold")
            game["pot_b"] += x
        while len(game["board"]) < 5:
            game["board"].append(game["deck"].pop())
        game["street"] = 3
        return await _pk_end(uid, game, "showdown")

    if act == "raise":
        if game["phase"] != "act":
            undo()
            return _err("Rilancio non disponibile.", 400, _pk_state(uid, game))
        if _bal(uid) < bet:
            undo()
            return _err(f"Servono {bet} $SDG per rilanciare.", 400, _pk_state(uid, game))
        G._charge(uid, uid, bet)
        game["pot_p"] += bet
        s = G._bot_strength(game["hole_b"], game["board"])
        if G._bot_calls_raise(s, game["street"]):
            game["pot_b"] += bet
            return await _pk_advance(uid, game, f"Hai rilanciato di {bet} $SDG: il bot vede.")
        return await _pk_end(uid, game, "bot_fold")

    undo()
    return _err("Azione non valida.")


# ----------------------------------------------------------------------------------------------
# tabella delle azioni (usata da webapi.py: /api/g/<gioco>/<azione>)
# ----------------------------------------------------------------------------------------------
ACTIONS = {
    "bj": {"state": bj_state, "start": bj_start, "hit": bj_hit, "stand": bj_stand},
    "wd": {"state": wd_state, "start": wd_start, "guess": wd_guess},
    "mm": {"state": mm_state, "start": mm_start, "guess": mm_guess},
    "pk": {"state": pk_state, "start": pk_start, "act": pk_act},
}
