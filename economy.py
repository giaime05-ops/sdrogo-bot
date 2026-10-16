"""Economia V2: Banco Centrale, Follia Sdrogo, boost/malus, sfida di gruppo, missioni, enigmi di /lavora, ruota daily."""
import asyncio
import html
import logging
import os
import random
import time
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
    _TZ = ZoneInfo("Europe/Rome")
except Exception:          # tzdata mancante (tipico delle immagini Docker "slim"): uso il calcolo manuale sotto
    _TZ = None

from config import GROUP_CHAT_ID
from state import USER_INVENTORIES, BANCO_STATE, GROUP_CHALLENGE, GAME_STATUS
from storage import (add_user_coins, get_user_coins, get_user_data_dict, save_db, BANCO_INITIAL,
                     ukey, in_sandbox, is_sandbox, log_play, touch_inv)
from utils import is_v2, smart_edit_msg

# ======================================================================
# COSTANTI
# ======================================================================
FOLLIA_ON = 15000         # il Banco raggiunge questa soglia: parte la Follia Sdrogo
FOLLIA_HOURS = 12         # la Follia dura esattamente 12 ore da quando scatta (timer, non soglia di consumo)
FOLLIA_MULT = {"default": 1.5, "aviator": 2.0, "scratch": 2.0}   # moltiplicatore sulle vincite (parte di profitto)

BOOST_WIN_GAMES = 10      # Boost Vincita (x1.5): partite single player
BOOST_LUCK_GAMES = 15     # Boost Fortuna: partite single player
LUCK_GAMES = {"blackjack", "slot", "roulette", "scratch"}
SANG_PCT = 0.25           # Sanguisuga: quota rubata a ogni vincita
SANG_GAMES = 10

CHALLENGE_GOAL = 5000
CHALLENGE_REWARD = 500
CHALLENGE_WEEKDAY = 3                 # la sfida si rinnova ogni GIOVEDÌ a mezzanotte italiana (0=lunedì ... 3=giovedì)
CHALLENGE_MIN_HOURS = 48              # una sfida avviata a mano a ridosso del giovedì dura fino a quello dopo
CHALLENGE_EDIT_EVERY = 5              # secondi minimi tra due aggiornamenti del messaggio fissato

WORK_REWARD = 50
WORK_COOLDOWN = 2 * 3600
WORK_TIME = 60
WORK_MAX_BALANCE = 100    # /lavora è per chi è al verde: serve un saldo sotto questa soglia (None = nessun limite)

DAILY_WHEEL = [(25, 40), (50, 35), (75, 15), (100, 8), (500, 2)]   # (premio, peso %)

# Filtro muto di massa (oggetto V2): utenti colpiti, configurabili da variabile d'ambiente
# MUTE_FILTER_TARGETS="username1,username2" (senza @). Nessun username è scritto nel codice.
MUTE_FILTER_TARGETS = {
    u.strip().lower().lstrip("@")
    for u in os.environ.get("MUTE_FILTER_TARGETS", "").split(",") if u.strip()
}

_chal_shown = None        # progresso mostrato nel messaggio fissato (None = sconosciuto, es. dopo un riavvio)
_chal_last_edit = 0.0

# Bot per i messaggi privati di notifica (impostato da main.py con set_bot)
_BOT = None


def set_bot(bot):
    global _BOT
    _BOT = bot


# ======================================================================
# UTILITÀ
# ======================================================================
def fmt(n) -> str:
    return f"{int(n):,}".replace(",", ".")


def _last_sunday(year: int, month: int) -> datetime:
    d = datetime(year, month, 31, tzinfo=timezone.utc)      # marzo e ottobre hanno 31 giorni
    return d - timedelta(days=(d.weekday() + 1) % 7)


def rome_now() -> datetime:
    """Ora attuale in Italia. Con tzdata usa ZoneInfo; altrimenti applica la regola europea dell'ora legale
    (dall'ultima domenica di marzo all'ultima di ottobre, alle 01:00 UTC): il reset NON dipende dal server."""
    if _TZ is not None:
        return datetime.now(_TZ)
    now = datetime.now(timezone.utc)
    start = _last_sunday(now.year, 3).replace(hour=1)
    end = _last_sunday(now.year, 10).replace(hour=1)
    return now + timedelta(hours=2 if start <= now < end else 1)


def today_str() -> str:
    """Data odierna in Italia: Ruota Daily, missioni giornaliere e donazioni si azzerano a mezzanotte italiana."""
    return str(rome_now().date())


def _rome_offset_hours(utc_dt: datetime) -> int:
    """Scarto dell'Italia da UTC (1 inverno, 2 estate) in un dato istante."""
    if _TZ is not None:
        return int(utc_dt.astimezone(_TZ).utcoffset().total_seconds() // 3600)
    start = _last_sunday(utc_dt.year, 3).replace(hour=1)
    end = _last_sunday(utc_dt.year, 10).replace(hour=1)
    return 2 if start <= utc_dt < end else 1


def rome_midnight_ts(d) -> float:
    """Timestamp della mezzanotte italiana (00:00) della data d."""
    guess = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    return (guess - timedelta(hours=_rome_offset_hours(guess))).timestamp()


def challenge_end_ts(start_ts: float) -> float:
    """Fine della sfida: la prossima mezzanotte italiana tra mercoledì e giovedì.
    Se mancano meno di CHALLENGE_MIN_HOURS ore, quella del giovedì successivo."""
    utc = datetime.fromtimestamp(start_ts, timezone.utc)
    local = utc + timedelta(hours=_rome_offset_hours(utc))
    ahead = (CHALLENGE_WEEKDAY - local.weekday()) % 7 or 7
    end = rome_midnight_ts(local.date() + timedelta(days=ahead))
    if end - start_ts < CHALLENGE_MIN_HOURS * 3600:
        end = rome_midnight_ts(local.date() + timedelta(days=ahead + 7))
    return end


_DAYS_IT = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]


def _fmt_deadline(ts: float) -> str:
    utc = datetime.fromtimestamp(ts, timezone.utc)
    loc = utc + timedelta(hours=_rome_offset_hours(utc))
    return f"{_DAYS_IT[loc.weekday()]} {loc.day:02d}/{loc.month:02d} alle {loc.hour:02d}:{loc.minute:02d}"


def game_header(title: str, user_name: str, coins: int, bet=None) -> str:
    """Intestazione uniforme di tutti i giochi, da rigenerare a ogni turno col saldo aggiornato."""
    h = (
        f"{title}\n"
        f"👤 Player: <b>{html.escape(user_name or 'Giocatore')}</b>\n"
        f"💳 Saldo Attuale: <b>{fmt(coins)} $SDG</b>\n"
    )
    if bet is not None:
        h += f"🎯 Puntata: <b>{fmt(bet)} $SDG</b>\n"
    return h + "\n"


def main_chat_id():
    """Chat del gruppo principale (annunci, Follia, sfida di gruppo)."""
    if GROUP_CHAT_ID:
        try:
            return int(GROUP_CHAT_ID)
        except ValueError:
            return None
    return GROUP_CHALLENGE.get("chat_id")


async def _announce(bot, text: str):
    cid = main_chat_id()
    if not cid:
        return None
    try:
        return await bot.send_message(chat_id=cid, text=text, parse_mode="HTML")
    except Exception as e:
        logging.warning(f"Annuncio non inviato: {e}")
        return None


def record_single_result(chat_id: int, user_id: int, won: bool, profit: int, is_casino: bool = True,
                         game: str = "", bet: int = 0):
    """Aggiorna le statistiche. Con game/bet registra anche la giocata nello storico (/storico)."""
    if game:
        log_play(user_id, game, bet, bet + profit)
    u = get_user_data_dict(chat_id, user_id)
    u["single_played"] = u.get("single_played", 0) + 1
    u["net_profit"] = u.get("net_profit", 0) + profit

    if won:
        u["single_won"] = u.get("single_won", 0) + 1
        if is_casino:
            u["casino_wins"] = u.get("casino_wins", 0) + 1
    save_db()


# ======================================================================
# INVENTARIO
# ======================================================================
def inv(chat_id: int, user_id: int) -> dict:
    """Inventario dell'utente con tutti i campi V2 inizializzati.
    Oggetti posseduti: titles, persecutes, silence, mutefilter, boost_win, boost_luck, sang.
    Effetti attivi (con le cariche residue, salvate nel database): boost_win_left, boost_luck_left,
    sang_on_me ({"buyer", "left"})."""
    key = ukey(user_id)                   # profilo unico (str(id)): stesso inventario in privato e in tutti i gruppi; Sandbox: "sbx_<id>"
    d = USER_INVENTORIES.setdefault(key, {})
    touch_inv(key)
    for k, v in (("titles", 0), ("persecutes", 0), ("stars", 0), ("silence", 0), ("mutefilter", 0),
                 ("boost_win", 0), ("boost_luck", 0), ("sang", 0),
                 ("boost_win_left", 0), ("boost_luck_left", 0)):
        d.setdefault(k, v)
    return d


def has_luck(chat_id: int, user_id: int, game: str) -> bool:
    return game in LUCK_GAMES and inv(chat_id, user_id)["boost_luck_left"] > 0


# ======================================================================
# BANCO CENTRALE & FOLLIA SDROGO
# ======================================================================
def banco_balance() -> int:
    return int(BANCO_STATE.get("BANCO_BALANCE", 0))


def banco_add(amount: int):
    if in_sandbox():
        return                                        # Sandbox admin: il Banco vero non si tocca
    if amount > 0:
        BANCO_STATE["BANCO_BALANCE"] = banco_balance() + int(amount)
        save_db()


def banco_take(amount: int, count: bool = True) -> int:
    """Preleva fino a `amount` dal Banco. Restituisce quanto è stato davvero prelevato.
    Durante la Follia Sdrogo ciò che esce dal Banco conta nel consumo (count=False per i rimborsi)."""
    taken = max(0, min(int(amount), banco_balance()))
    if in_sandbox():
        return taken                                  # Sandbox: premio simulato, il Banco vero resta intatto
    if taken:
        BANCO_STATE["BANCO_BALANCE"] = banco_balance() - taken
        if count and follia_active():
            BANCO_STATE["FOLLIA_CONSUMED"] = int(BANCO_STATE.get("FOLLIA_CONSUMED", 0)) + taken
        save_db()
    return taken


def follia_active() -> bool:
    return bool(BANCO_STATE.get("FOLLIA_ATTIVA"))


def follia_consumed() -> int:
    return int(BANCO_STATE.get("FOLLIA_CONSUMED", 0))


def follia_left_text() -> str:
    left = max(0, int(BANCO_STATE.get("FOLLIA_END", 0) - time.time()))
    return f"{left // 3600}h {left % 3600 // 60:02d}m"


def follia_line() -> str:
    """Una riga discreta con lo stato della Follia (per HUB, portafoglio e pannello admin)."""
    if follia_active():
        return f"🎪 Follia Sdrogo ATTIVA · finisce tra {follia_left_text()}"
    return f"🎪 Follia Sdrogo: Banco {fmt(banco_balance())}/{fmt(FOLLIA_ON)} per l'attivazione"


async def banco_check(bot):
    """Se il Banco è a zero lo riporta al valore iniziale.
    FOLLIA: scatta quando il Banco arriva a FOLLIA_ON (15.000) e parte un timer di FOLLIA_HOURS (12 ore).
    Allo scadere si spegne, il contatore si azzera del tutto e per la successiva il Banco deve scendere sotto
    la soglia e accumulare di nuovo FOLLIA_ON. Annuncia nel gruppo principale."""
    if banco_balance() <= 0:
        BANCO_STATE["BANCO_BALANCE"] = BANCO_INITIAL
        save_db()
    bal = banco_balance()
    now = time.time()

    # dopo una Follia il sistema si riarma solo quando il Banco è sceso sotto la soglia di attivazione
    if BANCO_STATE.get("FOLLIA_RESET") and not follia_active() and bal < FOLLIA_ON:
        BANCO_STATE["FOLLIA_RESET"] = False
        save_db()

    if not follia_active() and not BANCO_STATE.get("FOLLIA_RESET") and bal >= FOLLIA_ON:
        BANCO_STATE["FOLLIA_ATTIVA"] = True
        BANCO_STATE["FOLLIA_CONSUMED"] = 0
        BANCO_STATE["FOLLIA_END"] = now + FOLLIA_HOURS * 3600
        save_db()
        await _announce(
            bot,
            "🎉🎊🥳 <b>FOLLIA SDROGO!</b> 🥳🎊🎉\n"
            "🎈🎆✨🎇🎈🎆✨🎇🎈\n\n"
            f"🏦 Il Banco Centrale ha accumulato <b>{fmt(bal)} $SDG</b>: è FESTA!\n\n"
            "💥 Vincite dei giochi Single Player <b>×1.5</b> (<b>×2</b> su Aviator e Gratta e Vinci)!\n"
            "🏢 La Sdrogo Heist può <b>svuotare il Banco</b>!\n\n"
            f"<i>La Follia dura <b>{FOLLIA_HOURS} ore</b>: poi si spegne.</i>\n"
            "🎰🃏🎡✈️🎟️ Giocate, giocate, giocate!"
        )
    elif follia_active() and now >= float(BANCO_STATE.get("FOLLIA_END", 0)):
        BANCO_STATE["FOLLIA_ATTIVA"] = False
        BANCO_STATE["FOLLIA_CONSUMED"] = 0
        BANCO_STATE["FOLLIA_END"] = 0
        BANCO_STATE["FOLLIA_RESET"] = True          # la prossima parte solo dopo aver riaccumulato FOLLIA_ON
        save_db()
        await _announce(
            bot,
            "🌙 <b>La Follia Sdrogo è finita!</b>\n"
            f"Il Banco Centrale è a {fmt(bal)} $SDG. "
            f"Per la prossima Follia servono di nuovo {fmt(FOLLIA_ON)} $SDG nel Banco. 🎪"
        )


# ======================================================================
# SPESA / SFIDA DI GRUPPO
# ======================================================================
def note_spend(chat_id: int, user_id: int, amount: int):
    """Registra una spesa nei giochi per la sfida di gruppo. Il profilo è unico: contano le spese fatte nel
    gruppo della sfida e quelle in chat privata col bot (Heist compreso), ma non quelle di altri gruppi."""
    gc = GROUP_CHALLENGE
    if not gc.get("active") or in_sandbox() or is_sandbox(user_id):
        return
    try:
        in_other_group = int(chat_id) < 0 and str(chat_id) != str(gc.get("chat_id"))
    except (TypeError, ValueError):
        in_other_group = True
    if in_other_group:
        return
    if time.time() > gc.get("end", 0) or amount <= 0:
        return
    gc["spent"] = int(gc.get("spent", 0)) + int(amount)
    p = gc.setdefault("participants", {})
    p[str(user_id)] = int(p.get(str(user_id), 0)) + int(amount)
    save_db()


def charge(chat_id: int, user_id: int, amount: int):
    """Scala una puntata dal saldo e la conta nella sfida di gruppo."""
    add_user_coins(chat_id, user_id, -amount)
    note_spend(chat_id, user_id, amount)


def _bar(spent: int, goal: int) -> str:
    filled = max(0, min(10, int(spent * 10 / goal)))
    return "█" * filled + "░" * (10 - filled)


def challenge_text(footer: str = "") -> str:
    gc = GROUP_CHALLENGE
    goal = max(1, int(gc.get("goal", CHALLENGE_GOAL)))
    spent = int(gc.get("spent", 0))
    pct = min(100, int(spent * 100 / goal))
    left = max(0, int(gc.get("end", 0) - time.time()))
    days, rem = divmod(left, 86400)
    hours = rem // 3600
    return (
        "🎯 <b>SFIDA DI GRUPPO</b> 🎯\n\n"
        f"Spendete <b>{fmt(goal)} $SDG</b> nei giochi entro <b>{_fmt_deadline(gc.get('end', 0))}</b> (ora italiana)!\n\n"
        f"[{_bar(spent, goal)}] <b>{fmt(spent)}/{fmt(goal)}</b> ({pct}%)\n"
        f"👥 Partecipanti: <b>{len(gc.get('participants', {}))}</b>\n"
        f"⏳ Tempo rimasto: <b>{days}g {hours}h</b>\n\n"
        f"🎁 Premio: <b>{CHALLENGE_REWARD} $SDG + 1 Boost Fortuna</b> a ogni partecipante!"
        + (f"\n\n{footer}" if footer else "")
    )


async def challenge_start(bot, chat_id=None, goal=None, days=None):
    """Avvia (o resetta) la sfida di gruppo: invia, fissa (in silenzio) e salva il messaggio. Restituisce (ok, nota).
    Avvio manuale dell'admin: quota `goal` e durata `days` scelte da lui (la fine è esattamente tra `days` giorni).
    Rinnovo automatico (nessun parametro): ultima quota scelta, fino alla mezzanotte tra mercoledì e giovedì."""
    global _chal_shown
    gc = GROUP_CHALLENGE
    cid = chat_id or main_chat_id()
    if not cid:
        return False, "GROUP_CHAT_ID non configurato."

    # reset: toglie dal pin il vecchio messaggio
    if gc.get("msg_id") and gc.get("chat_id"):
        try:
            await bot.unpin_chat_message(chat_id=gc["chat_id"], message_id=gc["msg_id"])
        except Exception:
            pass

    now = time.time()
    goal = int(goal or gc.get("cfg_goal") or CHALLENGE_GOAL)
    end = now + int(days) * 86400 if days else challenge_end_ts(now)
    gc.update({
        "active": True, "goal": goal, "cfg_goal": goal, "start": now, "end": end,
        "spent": 0, "participants": {}, "chat_id": int(cid), "msg_id": None, "next_start": 0.0,
    })
    note = "Sfida avviata."
    try:
        msg = await bot.send_message(chat_id=int(cid), text=challenge_text(), parse_mode="HTML")
        gc["msg_id"] = msg.message_id
        try:
            # pin SILENZIOSO: nessuna notifica ai membri del gruppo
            await bot.pin_chat_message(chat_id=int(cid), message_id=msg.message_id, disable_notification=True)
        except Exception as e:
            note = f"Sfida avviata, ma non sono riuscito a fissare il messaggio ({e}). Dammi il permesso di fissare messaggi."
    except Exception as e:
        gc["active"] = False
        save_db()
        return False, f"Impossibile inviare il messaggio nel gruppo: {e}"
    _chal_shown = 0
    save_db()
    return True, note


async def _challenge_finish(bot, success: bool):
    gc = GROUP_CHALLENGE
    cid, mid = gc.get("chat_id"), gc.get("msg_id")
    gc["active"] = False
    # la prossima sfida parte a mezzanotte tra mercoledì e giovedì (subito, se la scadenza è già arrivata)
    gc["next_start"] = max(time.time(), float(gc.get("end", 0)))
    participants = dict(gc.get("participants", {}))
    spent = int(gc.get("spent", 0))

    if success:
        for uid in participants:
            try:
                add_user_coins(cid, int(uid), CHALLENGE_REWARD)
                inv(cid, int(uid))["boost_luck"] += 1
            except Exception:
                pass
        final = (
            "🏆🎉 <b>SFIDA DI GRUPPO COMPLETATA!</b> 🎉🏆\n\n"
            f"Il gruppo ha speso <b>{fmt(spent)} $SDG</b>!\n"
            f"🎁 Ogni partecipante ({len(participants)}) riceve <b>{CHALLENGE_REWARD} $SDG + 1 Boost Fortuna</b> "
            "(lo trovi nell'Inventario 🎒).\n\n"
            "⏭️ La prossima sfida parte giovedì a mezzanotte!"
        )
    else:
        final = (
            "⌛ <b>SFIDA DI GRUPPO SCADUTA</b>\n\n"
            f"Il gruppo ha speso <b>{fmt(spent)}/{fmt(gc.get('goal', CHALLENGE_GOAL))} $SDG</b>: obiettivo mancato. "
            "Niente premi, ma una nuova sfida parte subito!"
        )

    save_db()
    if cid:
        if mid:
            await smart_edit_msg(bot, cid, mid, challenge_text(), None)
            try:
                await bot.unpin_chat_message(chat_id=cid, message_id=mid)
            except Exception:
                pass
        try:
            await bot.send_message(chat_id=cid, text=final, parse_mode="HTML")
        except Exception as e:
            logging.warning(f"Annuncio fine sfida non inviato: {e}")


async def challenge_tick(context):
    """Job ogni ~10 secondi: aggiorna la barra sul messaggio fissato (con limite di frequenza),
    chiude la sfida a obiettivo raggiunto o a scadenza, avvia quella programmata."""
    global _chal_shown, _chal_last_edit
    bot = context.bot
    gc = GROUP_CHALLENGE
    now = time.time()

    try:
        await banco_check(bot)             # spegne la Follia allo scadere delle 12 ore anche se nessuno sta giocando
    except Exception as e:
        logging.error(f"banco_check nel tick: {e}")

    if gc.get("active"):
        if int(gc.get("spent", 0)) >= int(gc.get("goal", CHALLENGE_GOAL)):
            await _challenge_finish(bot, True)
        elif now >= gc.get("end", 0):
            await _challenge_finish(bot, False)
        elif gc.get("msg_id"):
            spent = int(gc.get("spent", 0))
            if _chal_shown is None:
                _chal_shown = spent                  # dopo un riavvio: non so cosa c'è scritto, nessuna modifica inutile
            elif spent != _chal_shown and now - _chal_last_edit >= CHALLENGE_EDIT_EVERY:
                # il messaggio fissato si modifica SOLO se il progresso è cambiato dal tick precedente
                _chal_shown = spent
                _chal_last_edit = now
                await smart_edit_msg(bot, gc["chat_id"], gc["msg_id"], challenge_text(), None)
    elif gc.get("next_start") and now >= gc["next_start"]:
        await challenge_start(bot)


# ======================================================================
# BOOST, MALUS, FOLLIA: liquidazione di una partita single player
# ======================================================================
async def settle_single(bot, chat_id: int, user_id: int, game: str, bet: int, base_payout: int,
                        is_casino: bool = True):
    """Liquida una partita single player (la puntata è già stata scalata con charge()).
    base_payout = totale restituito prima dei modificatori (0 se perdita).
    Applica Boost Vincita, Follia Sdrogo, Sanguisuga; alimenta il Banco; registra le statistiche.
    Restituisce (importo accreditato al giocatore, lista di note da mostrare)."""
    state = inv(chat_id, user_id)
    notes = []
    base_payout = int(base_payout)
    payout = base_payout
    profit = payout - bet

    if profit > 0:
        if state["boost_win_left"] > 0:
            bonus = int(profit * 0.5)
            if bonus:
                payout += bonus
                notes.append(f"🚀 Boost Vincita ×1.5: +{fmt(bonus)}")
        if follia_active():
            fm = FOLLIA_MULT.get(game, FOLLIA_MULT["default"])
            got = banco_take(int(profit * (fm - 1)))
            if got:
                payout += got
                notes.append(f"🎪 Follia Sdrogo ×{fm}: +{fmt(got)} dal Banco")

    # Sanguisuga: il 25% della vincita va a chi ha lanciato il malus (conta ogni partita, vinta o persa)
    steal = 0
    sang = state.get("sang_on_me")
    if sang and sang.get("left", 0) > 0:
        net = payout - bet
        if net > 0:
            steal = int(net * SANG_PCT)
            if steal:
                add_user_coins(chat_id, sang["buyer"], steal)
                notes.append(f"🧛 Sanguisuga: -{fmt(steal)} rubati")
        sang["left"] -= 1
        if sang["left"] <= 0:
            state.pop("sang_on_me", None)
            notes.append("🧛 La Sanguisuga su di te è terminata.")

    # i boost si consumano a ogni partita
    if state["boost_win_left"] > 0:
        state["boost_win_left"] -= 1
        if state["boost_win_left"] == 0:
            notes.append("🚀 Boost Vincita esaurito.")
    if game in LUCK_GAMES and state["boost_luck_left"] > 0:
        state["boost_luck_left"] -= 1
        if state["boost_luck_left"] == 0:
            notes.append("🍀 Boost Fortuna esaurito.")

    # il Banco incassa le perdite nette dei giocatori
    if base_payout < bet:
        banco_add(bet - base_payout)

    user_payout = payout - steal
    if user_payout > 0:
        add_user_coins(chat_id, user_id, user_payout)
    record_single_result(chat_id, user_id, user_payout > bet, user_payout - bet, is_casino=is_casino)
    log_play(user_id, game, bet, user_payout)
    qmsg = single_quest_events(chat_id, user_id, game, bet, base_payout, user_payout)
    if qmsg:
        notes.append(qmsg)
    save_db()                       # salva anche le cariche residue di boost e malus
    await banco_check(bot)
    return user_payout, notes


# ======================================================================
# MISSIONI GIORNALIERE (V2): 1 single player + 1 multiplayer per giocatore, diverse da utente a utente.
# Si rinnovano a mezzanotte italiana. Gli eventi arrivano da settle_single (tutti i giochi single player che
# liquidano), da record_duel_result (duelli), da quiz, /lavora e Heist.
#
# Eventi single player (generati da settle_single):  "<gioco>:win" "<gioco>:profit" "<gioco>:streak"
#   "<gioco>:best" "aviator:mult"   con <gioco> in blackjack, slot, roulette, aviator, scratch, poker, wordle, mastermind
# Altri eventi: "poker:hand", "quiz_win", "work_done", "heist:level", "duel:win", "duel:profit", "duel:streak",
#   "duel:<tipo>:win" (dice, tris, highlow, rr, ghig, quiz1v1), "quizmulti_win".
# mode "add": il progresso somma i valori (partite vinte, profitto accumulato).
# mode "max": conta il valore migliore del giorno (serie di vittorie, moltiplicatore, vincita singola, mano).
# ======================================================================
def _q(qid, kind, tier, game, event, target, reward, text, mode="add", unit=""):
    return {"id": qid, "kind": kind, "tier": tier, "game": game, "event": event, "target": target,
            "reward": reward, "text": text, "mode": mode, "unit": unit}


QUEST_POOL = [
    # ---------------- SINGLE PLAYER ----------------
    _q("bj_w1", "sp", 1, "blackjack", "blackjack:win", 1, 35, "Vinci 1 mano a Blackjack"),
    _q("bj_s2", "sp", 2, "blackjack", "blackjack:streak", 2, 90, "Vinci 2 mani di fila a Blackjack", "max"),
    _q("bj_p100", "sp", 3, "blackjack", "blackjack:profit", 100, 140, "Guadagna 100 $SDG di profitto a Blackjack", "add", " $SDG"),
    _q("sl_w2", "sp", 1, "slot", "slot:win", 2, 40, "Vinci 2 giri alla Slot Machine"),
    _q("sl_p60", "sp", 2, "slot", "slot:profit", 60, 80, "Guadagna 60 $SDG di profitto alla Slot Machine", "add", " $SDG"),
    _q("ro_w2", "sp", 1, "roulette", "roulette:win", 2, 40, "Vinci 2 giri alla Sdrogo Roulette"),
    _q("ro_s3", "sp", 3, "roulette", "roulette:streak", 3, 170, "Fai una tripletta alla Sdrogo Roulette (3 vittorie di fila)", "max"),
    _q("av_p50", "sp", 2, "aviator", "aviator:profit", 50, 70, "Guadagna 50 $SDG di profitto su Aviator", "add", " $SDG"),
    _q("av_m3", "sp", 2, "aviator", "aviator:mult", 3, 80, "Incassa a ×3 o più su Aviator", "max", "×"),
    _q("av_m6", "sp", 3, "aviator", "aviator:mult", 6, 170, "Incassa a ×6 o più su Aviator", "max", "×"),
    _q("sc_w2", "sp", 1, "scratch", "scratch:win", 2, 40, "Vinci 2 Gratta e Vinci"),
    _q("sc_b100", "sp", 3, "scratch", "scratch:best", 100, 140, "Vinci almeno 100 $SDG netti con un solo Gratta e Vinci", "max", " $SDG"),
    _q("pk_w1", "sp", 1, "poker", "poker:win", 1, 45, "Vinci 1 mano a Sdrogo Poker"),
    _q("pk_h3", "sp", 2, "poker", "poker:hand", 3, 100, "Vinci uno showdown a Sdrogo Poker con almeno un Tris", "max"),
    _q("pk_h6", "sp", 3, "poker", "poker:hand", 6, 190, "Vinci uno showdown a Sdrogo Poker con almeno un Full", "max"),
    _q("wd_w1", "sp", 1, "wordle", "wordle:win", 1, 35, "Indovina 1 Wordle"),
    _q("wd_f3", "sp", 2, "wordle", "wordle:best", 30, 70, "Indovina il Wordle entro il 3° tentativo", "max", " $SDG"),
    _q("mm_w1", "sp", 1, "mastermind", "mastermind:win", 1, 35, "Risolvi 1 Mastermind"),
    _q("mm_f3", "sp", 2, "mastermind", "mastermind:best", 40, 75, "Risolvi il Mastermind entro il 3° tentativo", "max", " $SDG"),
    _q("qz_w3", "sp", 1, None, "quiz_win", 3, 45, "Rispondi correttamente a 3 Quiz"),
    _q("hs_l3", "sp", 2, "heist", "heist:level", 3, 100, "Supera il Livello 3 di Sdrogo Heist", "max"),
    _q("wk_1", "sp", 1, "lavora", "work_done", 1, 30, "Effettua /lavora 1 volta"),
    # ---------------- MULTIPLAYER ----------------
    _q("du_w1", "mp", 1, None, "duel:win", 1, 50, "Vinci 1 duello 1v1 (qualsiasi gioco)"),
    _q("du_s2", "mp", 2, None, "duel:streak", 2, 110, "Vinci 2 duelli di fila", "max"),
    _q("du_p100", "mp", 2, None, "duel:profit", 100, 100, "Guadagna 100 $SDG di profitto nei duelli", "add", " $SDG"),
    _q("du_dice", "mp", 1, "dice", "duel:dice:win", 1, 60, "Vinci 1 duello a dadi"),
    _q("du_ttt", "mp", 1, "ttt", "duel:tris:win", 1, 60, "Vinci 1 partita a Tris"),
    _q("du_hl", "mp", 1, "highlow", "duel:highlow:win", 1, 60, "Vinci 1 sfida Più Alto o Più Basso"),
    _q("du_rr", "mp", 2, "roulette1v1", "duel:rr:win", 1, 80, "Vinci 1 Roulette Russa 1v1"),
    _q("du_gh", "mp", 2, "ghig", "duel:ghig:win", 1, 90, "Vinci 1 duello alla Ghigliottina"),
    _q("du_qz", "mp", 1, "quiz1v1", "duel:quiz1v1:win", 1, 60, "Vinci 1 Quiz 1v1"),
    _q("du_qm", "mp", 1, "quizmulti", "quizmulti_win", 2, 50, "Rispondi per primo a 2 Quiz Multiplayer"),
]
QUEST_IDS = {q["id"] for q in QUEST_POOL}
QUEST_BY_ID = {q["id"]: q for q in QUEST_POOL}
_TIER_WEIGHTS = {1: 50, 2: 35, 3: 15}      # facili più frequenti, quelle difficili ogni tanto


def _pick_quest(kind: str, can_work: bool, exclude: str = ""):
    eligible = [p for p in QUEST_POOL
                if p["kind"] == kind and p["id"] != exclude
                and (p["id"] != "wk_1" or can_work)
                and (p["game"] is None or GAME_STATUS.get(p["game"], True))]
    if not eligible:
        return None
    weights = [_TIER_WEIGHTS[p["tier"]] for p in eligible]
    return random.choices(eligible, weights=weights, k=1)[0]


def quests_for(chat_id: int, user_id: int) -> list:
    """Missioni di oggi: 1 single player + 1 multiplayer, scelte a caso e diverse da giocatore a giocatore."""
    u = get_user_data_dict(chat_id, user_id)
    q = u.get("quests")
    stale = (not q or q.get("date") != today_str()
             or any(x.get("id") not in QUEST_IDS for x in q.get("list", [])))
    if stale:
        # premi completati ma mai riscattati prima della mezzanotte: vengono accreditati, non si perdono
        if q:
            for old in q.get("list", []):
                if old.get("done") and not old.get("claimed", True) and old.get("id") in QUEST_IDS:
                    add_user_coins(chat_id, user_id, old.get("reward", 0))
        # "/lavora" è proponibile solo a chi può davvero usarlo (saldo sotto la soglia)
        can_work = WORK_MAX_BALANCE is None or get_user_coins(chat_id, user_id) < WORK_MAX_BALANCE
        picked = [x for x in (_pick_quest("sp", can_work), _pick_quest("mp", can_work)) if x]
        q = {"date": today_str(), "streaks": {}, "list": [{**p, "progress": 0, "done": False} for p in picked]}
        u["quests"] = q
        save_db()
    return q["list"]


def _fmt_val(v, unit: str) -> str:
    if unit == "×":
        return f"×{v:g}"
    if isinstance(v, float):
        v = int(v)
    return f"{v}{unit}"


def quest_progress(chat_id: int, user_id: int, event: str, n=1) -> str:
    """Avanza le missioni di quell'evento. Se una si completa, accredita il premio e restituisce il messaggio
    da mostrare ('' altrimenti). Non fa nulla per chi non ha la V2."""
    if not is_v2(user_id):
        return ""
    msgs = []
    for qst in quests_for(chat_id, user_id):
        if qst["event"] != event or qst["done"]:
            continue
        if qst.get("mode") == "max":
            qst["progress"] = max(qst["progress"], n)
        else:
            qst["progress"] += n
        if qst["progress"] >= qst["target"]:
            # il premio NON viene accreditato: va riscattato a mano dalla sezione Sfide dell'HUB
            qst["done"] = True
            qst["claimed"] = False
            msgs.append(f"🎯 Quest completata: {qst['text']}! Vai in Sfide & Missioni a riscattare {qst['reward']} $SDG")
            _notify_quest_dm(chat_id, user_id)
    if msgs:
        save_db()
    return "\n".join(msgs)


QUEST_DM_TEXT = "🎯 Quest Completata! Vai nella sezione Sfide a riscattare il premio!"


def _notify_quest_dm(chat_id: int, user_id: int):
    """Messaggio privato di avviso (se possibile: l'utente deve aver avviato il bot in privato).
    In chat privata non serve: il giocatore sta già guardando il bot."""
    if _BOT is None or str(chat_id) == str(user_id):
        return
    try:
        asyncio.get_running_loop().create_task(_send_dm(user_id, QUEST_DM_TEXT))
    except RuntimeError:
        pass


async def _send_dm(user_id: int, text: str):
    try:
        await _BOT.send_message(chat_id=user_id, text=text)
    except Exception:
        pass                                   # utente che non ha mai scritto al bot, o l'ha bloccato


def quests_claimable(chat_id: int, user_id: int) -> list:
    """Indici delle missioni completate e non ancora riscattate."""
    return [i for i, q in enumerate(quests_for(chat_id, user_id)) if q.get("done") and not q.get("claimed", True)]


def quest_claim(chat_id: int, user_id: int, idx: int):
    """Riscatta il premio della missione `idx`. Restituisce (ok, messaggio)."""
    lst = quests_for(chat_id, user_id)
    if idx < 0 or idx >= len(lst):
        return False, "Missione non trovata."
    q = lst[idx]
    if not q.get("done"):
        return False, "Questa missione non è ancora completata."
    if q.get("claimed", True):
        return False, "Premio già riscattato."
    q["claimed"] = True                        # prima di accreditare: un doppio tocco non paga due volte
    add_user_coins(chat_id, user_id, q["reward"])
    save_db()
    return True, f"🎁 Hai riscattato {q['reward']} $SDG!"


def _streak(chat_id: int, user_id: int, key: str, won: bool) -> int:
    """Serie di vittorie consecutive di oggi per la chiave data (gioco o 'duel'). Si azzera a una sconfitta."""
    quests_for(chat_id, user_id)
    st = get_user_data_dict(chat_id, user_id)["quests"].setdefault("streaks", {})
    st[key] = st.get(key, 0) + 1 if won else 0
    return st[key]


def single_quest_events(chat_id: int, user_id: int, game: str, bet: int, base_payout: int, user_payout: int) -> str:
    """Eventi di missione di una partita single player appena liquidata. Restituisce i messaggi di completamento."""
    if not is_v2(user_id):
        return ""
    base_net = int(base_payout) - int(bet)
    won = base_net > 0
    profit = int(user_payout) - int(bet)
    msgs = []

    def ev(name, n):
        m = quest_progress(chat_id, user_id, f"{game}:{name}", n)
        if m:
            msgs.append(m)

    streak = _streak(chat_id, user_id, game, won)
    if won:
        ev("win", 1)
        ev("best", base_net)
        ev("streak", streak)
        if game == "aviator" and bet > 0:
            ev("mult", round(base_payout / bet, 2))
    if profit > 0:
        ev("profit", profit)
    return "\n".join(msgs)


def duel_quest_events(chat_id: int, winner_id: int, loser_id: int, bet: int, game: str) -> str:
    """Eventi di missione di un duello concluso (game: dice, tris, highlow, rr, ghig, quiz1v1).
    Restituisce il messaggio da mostrare al vincitore. Lo sconfitto vede solo azzerarsi la sua serie."""
    if loser_id and is_v2(loser_id):
        _streak(chat_id, loser_id, "duel", False)
    if not winner_id or not is_v2(winner_id):
        return ""
    msgs = []
    streak = _streak(chat_id, winner_id, "duel", True)
    for ev, n in (("duel:win", 1), (f"duel:{game}:win", 1), ("duel:profit", bet), ("duel:streak", streak)):
        m = quest_progress(chat_id, winner_id, ev, n)
        if m:
            msgs.append(m)
    return "\n".join(msgs)


def quests_text(chat_id: int, user_id: int) -> str:
    lines = ["🎯 <b>MISSIONI GIORNALIERE</b>\n"]
    for qst in quests_for(chat_id, user_id):
        kind = "🎮" if qst["kind"] == "sp" else "⚔️"
        unit = qst.get("unit", "")
        prog = min(qst["progress"], qst["target"])
        if qst["done"] and not qst.get("claimed", True):
            icon, state = "🎁", "<b>COMPLETATA — da riscattare!</b>"
        elif qst["done"]:
            icon, state = "✅", f"riscattata (+{qst['reward']} $SDG)"
        else:
            icon, state = "🔸", f"<b>{_fmt_val(prog, unit)}/{_fmt_val(qst['target'], unit)}</b> · 🎁 {qst['reward']} $SDG"
        lines.append(f"{icon} {kind} {qst['text']} — {state}")
    lines.append("\n<i>🎮 single player · ⚔️ multiplayer. Le missioni si rinnovano ogni giorno a mezzanotte (ora italiana) "
                 "e sono diverse da giocatore a giocatore.</i>")
    return "\n".join(lines)


# ======================================================================
# RUOTA DAILY
# ======================================================================
def spin_daily() -> int:
    prizes = [p for p, _ in DAILY_WHEEL]
    weights = [w for _, w in DAILY_WHEEL]
    return random.choices(prizes, weights=weights, k=1)[0]


def wheel_prizes_text() -> str:
    return " | ".join(f"{p} ({w}%)" for p, w in DAILY_WHEEL)


# ======================================================================
# /lavora: enigmi procedurali
# ======================================================================
WIRE_COLORS = [("🔴", "rosso"), ("🔵", "blu"), ("🟡", "giallo"), ("🟢", "verde"), ("🟣", "viola")]
ARROWS = ["⬆️", "➡️", "⬇️", "⬅️"]


def _num_options(correct: int, spread: int = 9):
    opts = {correct}
    while len(opts) < 4:
        cand = correct + random.choice([-1, 1]) * random.randint(1, spread)
        if cand != correct:
            opts.add(cand)
    opts = list(opts)
    random.shuffle(opts)
    return [str(o) for o in opts], opts.index(correct)


def _puzzle_math():
    a, b, c = random.randint(3, 15), random.randint(2, 9), random.randint(2, 20)
    kind = random.choice(["a*b+c", "a+b*c", "(a+b)*c", "a*b-c"])
    if kind == "a*b+c":
        text, ans = f"{a} × {b} + {c}", a * b + c
    elif kind == "a+b*c":
        text, ans = f"{a} + {b} × {c}", a + b * c
    elif kind == "(a+b)*c":
        c = random.randint(2, 6)
        text, ans = f"({a} + {b}) × {c}", (a + b) * c
    else:
        text, ans = f"{a} × {b} − {c}", a * b - c
    options, idx = _num_options(ans, 12)
    return {"text": f"🧮 <b>CALCOLO RAPIDO</b>\n\nQuanto fa <b>{text}</b>?", "options": options, "ans": idx}


def _puzzle_wires():
    seq = [random.choice(WIRE_COLORS) for _ in range(6)]
    names = [n for _, n in seq]
    rule = random.choice(["after_first", "most", "second_last_not"])
    if rule == "most":
        counts = {n: names.count(n) for n in set(names)}
        top = max(counts.values())
        if list(counts.values()).count(top) != 1:
            rule = "after_first"
    if rule == "after_first":
        candidates = [n for n in dict.fromkeys(names[:-1])]
        x = random.choice(candidates)
        answer = seq[names.index(x) + 1]
        rule_text = f"Taglia il filo che viene <b>subito dopo il primo filo {x}</b>."
    elif rule == "most":
        top_name = max(counts, key=counts.get)
        answer = next(c for c in seq if c[1] == top_name)
        rule_text = "Taglia il filo del colore che compare <b>più volte</b>."
    else:
        x = random.choice(names)
        others = [c for c in seq if c[1] != x]
        if len(others) < 2:
            return _puzzle_wires()
        answer = others[-2]
        rule_text = f"Taglia il <b>penultimo filo che NON è {x}</b>."
    wires = " ".join(f"{i + 1}{e}" for i, (e, _) in enumerate(seq))
    options = [f"{e} {n}" for e, n in WIRE_COLORS]
    return {
        "text": f"🔌 <b>CENTRALINO</b>\n\nFili: {wires}\n\n{rule_text}",
        "options": options,
        "ans": options.index(f"{answer[0]} {answer[1]}"),
    }


def _puzzle_arrows():
    start = random.randrange(4)
    cur = start
    steps = []
    for _ in range(random.randint(3, 5)):
        right = random.random() < 0.5
        steps.append("a destra ↪️" if right else "a sinistra ↩️")
        cur = (cur + (1 if right else -1)) % 4
    text = (
        f"🧭 <b>FRECCE</b>\n\nParti da {ARROWS[start]} e ruota di 90° ogni volta:\n"
        + "\n".join(f"{i + 1}. {s}" for i, s in enumerate(steps))
        + "\n\nIn che direzione punti alla fine?"
    )
    return {"text": text, "options": list(ARROWS), "ans": cur}


def _puzzle_sequence():
    kind = random.choice(["arith", "geom", "alt"])
    if kind == "arith":
        a, d = random.randint(1, 20), random.randint(2, 9)
        seq = [a + d * i for i in range(5)]
    elif kind == "geom":
        a, r = random.randint(1, 4), random.randint(2, 3)
        seq = [a * (r ** i) for i in range(5)]
    else:
        a, d1, d2 = random.randint(1, 15), random.randint(2, 6), random.randint(7, 12)
        seq = [a]
        for i in range(4):
            seq.append(seq[-1] + (d1 if i % 2 == 0 else d2))
    shown, ans = seq[:4], seq[4]
    options, idx = _num_options(ans, 8)
    return {
        "text": f"🔢 <b>SEQUENZA</b>\n\nQual è il numero successivo?\n<b>{', '.join(map(str, shown))}, ?</b>",
        "options": options, "ans": idx,
    }


def make_puzzle() -> dict:
    """Genera al volo un enigma diverso ogni volta: {'text', 'options', 'ans'}."""
    return random.choice([_puzzle_math, _puzzle_wires, _puzzle_arrows, _puzzle_sequence])()


def work_cooldown_left(chat_id: int, user_id: int) -> int:
    last = float(get_user_data_dict(chat_id, user_id).get("last_work", 0))
    return max(0, int(last + WORK_COOLDOWN - time.time()))


def work_set_cooldown(chat_id: int, user_id: int):
    """Avvia il cooldown di /lavora e conta la missione 'Effettua /lavora 1 volta'."""
    get_user_data_dict(chat_id, user_id)["last_work"] = time.time()
    save_db()
    quest_progress(chat_id, user_id, "work_done")
