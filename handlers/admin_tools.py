"""Strumenti riservati all'Admin.

/storico @username  -  da chat PRIVATA: le ultime 20 giocate/transazioni del giocatore (data, gioco, puntata, esito, incasso).

/annuncio <testo>  (alias /dici)  -  da chat PRIVATA: il bot scrive il testo nel gruppo principale (GROUP_CHAT_ID).
Il testo può contenere HTML (<b>, <i>, <a href=...>, <code>) oppure Markdown (*grassetto*, _corsivo_, `codice`).
Se si usa la formattazione di Telegram (grassetto, corsivo...) direttamente nel messaggio, viene mantenuta.
"""
import logging
import re

from telegram import Update
from telegram.error import BadRequest, Forbidden
from telegram.ext import CommandHandler, ContextTypes

import html
from datetime import datetime, timedelta

from economy import main_chat_id, rome_now, fmt
from state import USER_DATA
from storage import find_user_id
from utils import is_admin

_CMD_PREFIX = re.compile(r"^/\w+(?:@\w+)?\s*", re.UNICODE)
MAX_LEN = 4096


def _strip_cmd(text: str) -> str:
    return _CMD_PREFIX.sub("", text or "", count=1).strip()


async def annuncio_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not msg or not user or not is_admin(user.id):
        return                                              # i comandi admin non rispondono agli altri
    if chat.type != "private":
        await msg.reply_text("🔒 Usa /annuncio (o /dici) in chat privata con me: il testo verrà pubblicato nel gruppo.")
        return

    raw = _strip_cmd(msg.text)
    if not raw:
        await msg.reply_text(
            "📣 <b>Uso:</b> <code>/annuncio testo da pubblicare</code>  (alias <code>/dici</code>)\n\n"
            "Il messaggio viene inviato nel gruppo principale. Puoi usare HTML "
            "(<code>&lt;b&gt;grassetto&lt;/b&gt;</code>, <code>&lt;i&gt;corsivo&lt;/i&gt;</code>) "
            "oppure Markdown (<code>*grassetto*</code>, <code>_corsivo_</code>).",
            parse_mode="HTML"
        )
        return
    if len(raw) > MAX_LEN:
        await msg.reply_text(f"❌ Testo troppo lungo: {len(raw)} caratteri (massimo {MAX_LEN}).")
        return

    cid = main_chat_id()
    if not cid:
        await msg.reply_text("❌ Gruppo principale non configurato: imposta la variabile GROUP_CHAT_ID.")
        return

    attempts = []
    has_fmt = any(e.type != "bot_command" for e in (msg.entities or []))
    if has_fmt:
        # Formattazione applicata dall'app di Telegram: la conservo convertendola in HTML
        html_text = _strip_cmd(msg.text_html)
        if html_text:
            attempts.append(("HTML", html_text))
    attempts += [("HTML", raw), ("Markdown", raw), (None, raw)]

    last_err = None
    for mode, text in attempts:
        try:
            await context.bot.send_message(chat_id=cid, text=text, parse_mode=mode)
            label = {"HTML": "HTML", "Markdown": "Markdown", None: "testo semplice"}[mode]
            await msg.reply_text(f"✅ Annuncio inviato nel gruppo ({label}).")
            logging.info(f"Annuncio admin inviato in {cid} ({label}).")
            return
        except BadRequest as e:
            last_err = e                                     # formattazione non valida: provo il formato successivo
            continue
        except Forbidden as e:
            await msg.reply_text(f"❌ Non posso scrivere nel gruppo: {e}")
            return
        except Exception as e:
            logging.error(f"Errore /annuncio: {e}")
            await msg.reply_text(f"❌ Errore nell'invio: {e}")
            return
    await msg.reply_text(f"❌ Invio non riuscito: {last_err}")


def _rome_from_ts(ts: int) -> str:
    """Timestamp UTC -> data e ora italiane (scarto ricavato dall'orologio italiano di economy)."""
    off = rome_now().replace(tzinfo=None) - datetime.utcnow()
    return (datetime.utcfromtimestamp(ts) + timedelta(seconds=round(off.total_seconds()))).strftime("%d/%m %H:%M:%S")


def _hm(secs) -> str:
    secs = int(secs or 0)
    return f"{secs // 3600}h {secs % 3600 // 60:02d}m"


def _wk_now() -> str:
    return rome_now().strftime("%G-W%V")


async def storico_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/storico @username (o ID numerico): cronologia delle ultime giocate. Solo admin, solo in privato."""
    msg = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not msg or not user or not is_admin(user.id):
        return                                              # gli altri non ricevono nessuna risposta
    if chat.type != "private":
        await msg.reply_text("🔒 Usa /storico in chat privata con me.")
        return
    arg = _strip_cmd(msg.text)
    if not arg:
        await msg.reply_text("📜 <b>Uso:</b> <code>/storico @username</code>  (oppure l'ID numerico)", parse_mode="HTML")
        return
    uid = find_user_id(arg.split()[0])
    if not uid:
        await msg.reply_text(
            "❌ Giocatore non trovato. Il bot ricorda l'@username solo di chi lo ha usato dopo questo aggiornamento: "
            "fallo scrivere una volta oppure cercalo con l'ID numerico.")
        return
    d = USER_DATA.get(uid, {})
    hist = d.get("history", [])
    uname = f"@{d['username']}" if d.get("username") else html.escape(d.get("name") or uid)
    head = (
        f"📜 <b>STORICO di {uname}</b> (ID <code>{uid}</code>)\n"
        f"💳 Saldo: <b>{fmt(d.get('coins', 0))} $SDG</b> · netto totale: <b>{fmt(d.get('net_profit', 0))}</b> · "
        f"giocate single: {d.get('single_played', 0)} · duelli: {d.get('duels_played', 0)}\n"
        f"⏱️ Tempo d'uso: <b>{_hm(d.get('use_secs', 0))}</b> totali · questa settimana <b>{_hm((d.get('use_wk') or {}).get(_wk_now(), 0))}</b>\n"
    )
    if not hist and not d.get("acts"):
        await msg.reply_text(head + "\nNessuna giocata registrata (la cronologia parte dall'aggiornamento).", parse_mode="HTML")
        return
    icon = {"vinta": "✅", "persa": "❌", "pareggio": "⚖️", "spesa": "🛒", "record": "🏆", "rimborso": "↩️"}
    rows = [(h["t"], "p", h) for h in hist] + [(a["t"], "a", a) for a in d.get("acts", [])]
    rows.sort(key=lambda x: x[0], reverse=True)                # dalla più recente: spese e movimenti insieme alle sezioni aperte
    lines = []
    for t, kind, h in rows:
        if kind == "a":
            lines.append(f"👣 <code>{_rome_from_ts(h['t'])}</code> {html.escape(h['a'])}")
            continue
        net = h["w"] - h["b"]
        lines.append(
            f"{icon.get(h['o'], '•')} <code>{_rome_from_ts(h['t'])}</code> <b>{html.escape(h['g'])}</b> · "
            f"puntata {fmt(h['b'])} · incasso {fmt(h['w'])} (<b>{'+' if net >= 0 else '-'}{fmt(abs(net))}</b>)")
    chunks, cur = [], head + f"\nUltimi {len(rows)} eventi (👣 = sezione aperta, dal più recente):\n"
    for ln in lines:
        if len(cur) + len(ln) + 1 > 3900:
            chunks.append(cur); cur = ""
        cur += ln + "\n"
    chunks.append(cur)
    for ch in chunks[:4]:
        await msg.reply_text(ch, parse_mode="HTML")


async def postgp_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/postgp: pubblica nel gruppo l'immagine del Multiplayer (tavoli, 1 vs 1, Grand Prix) con il tasto "🎮 Gioca adesso" (link diretto: apre l'Arcade sul Multiplayer, già col tuo account)."""
    import os
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    msg = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not msg or not user or not is_admin(user.id):
        return
    if chat.type != "private":
        await msg.reply_text("🔒 Usa /postgp in chat privata con me: il messaggio verrà pubblicato nel gruppo.")
        return
    link = (os.environ.get("ARCADE_LINK") or "").strip()
    if link and not link.lower().startswith(("http://", "https://", "tg://")):
        link = "https://" + link.lstrip("/")
    if "t.me/" not in link:
        await msg.reply_text(
            "⚠️ Serve il link diretto della mini app (quello di BotFather, tipo <code>https://t.me/NomeBot/nomeapp</code>) nella variabile "
            "<b>ARCADE_LINK</b> su Northflank. Senza, nel gruppo il tasto non può aprire la mini app con il tuo account.", parse_mode="HTML")
        return
    url = link + ("&" if "?" in link else "?") + "startapp=multiplayer"
    gid = main_chat_id()
    if not gid:
        await msg.reply_text("⚠️ GROUP_CHAT_ID non impostato.")
        return
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🎮 Gioca adesso", url=url)]])
    cap = "🎮 <b>SDROGO MULTIPLAYER</b>\nTavoli, sfide 1 contro 1 e Grand Prix con gli amici. Entra e gioca!"
    img = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "mp_banner.jpg")
    try:
        if os.path.exists(img):
            with open(img, "rb") as f:
                sent = await context.bot.send_photo(gid, f, caption=cap, parse_mode="HTML", reply_markup=kb)
        else:
            sent = await context.bot.send_message(gid, cap, parse_mode="HTML", reply_markup=kb)
        pinned = True
        try:
            await context.bot.pin_chat_message(gid, sent.message_id, disable_notification=True)
        except (BadRequest, Forbidden):
            pinned = False
        await msg.reply_text("✅ Pubblicato nel gruppo" + (" e fissato in alto." if pinned else " (non l'ho fissato: mi serve il permesso di fissare messaggi)."))
    except (BadRequest, Forbidden) as e:
        await msg.reply_text(f"❌ Non sono riuscito a pubblicare: {e}")


async def _adm_target(update):
    """Solo admin, solo in privato. Ritorna (uid, resto_argomenti) oppure None dopo aver risposto."""
    msg, user, chat = update.effective_message, update.effective_user, update.effective_chat
    if not msg or not user or not is_admin(user.id):
        return None
    if chat.type != "private":
        await msg.reply_text("🔒 Usa questo comando in chat privata con me.")
        return None
    parts = _strip_cmd(msg.text).split()
    if not parts:
        return "", []
    uid = find_user_id(parts[0])
    if not uid:
        await msg.reply_text("❌ Giocatore non trovato (prova con l'ID numerico).")
        return None
    return uid, parts[1:]


async def cheat_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/cheat @utente: record, saldo e premi del giorno di un giocatore sospetto."""
    t = await _adm_target(update)
    if t is None:
        return
    uid, _ = t
    msg = update.effective_message
    if not uid:
        await msg.reply_text("🕵️ Uso: <code>/cheat @utente</code>  ·  <code>/purge</code> (riverifica tutti i record)  ·  "
                             "<code>/purge @utente</code> (cancella i suoi tempi)  ·  <code>/purge @utente saldo 500</code> (e porta il saldo a 500)", parse_mode="HTML")
        return
    import webrace
    db = webrace._db(); d = USER_DATA.get(uid, {})
    L = ["🕵️ <b>%s</b> (ID <code>%s</code>)" % (html.escape(d.get("name") or d.get("username") or uid), uid),
         "💳 Saldo: <b>%s $SDG</b> · netto totale %s" % (fmt(d.get("coins", 0)), fmt(d.get("net_profit", 0))),
         "🏁 Premi record oggi: %s/%d" % ((db["day"].get(uid) or {}).get("n", 0), webrace.MAX_REWARDS_DAY)]
    for tid in webrace.TRACKS:
        b = (db["best"].get(tid) or {}).get(uid)
        if b is None:
            continue
        r = db["rec"].get(tid) or {}
        why = webrace.check_run(tid, b, r.get("g")) if r.get("u") == uid else None
        flag = " 🚩 RECORD" if r.get("u") == uid else ""
        lim = " ⚠️ sotto il limite fisico (%s)" % webrace._fmt(webrace.floor_ms(tid)) if b < webrace.floor_ms(tid) else ""
        L.append("• %s: %s%s%s%s" % (tid, webrace._fmt(b), flag, lim, (" ⚠️ " + why) if why else ""))
    await msg.reply_text("\n".join(L), parse_mode="HTML")


async def purge_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/purge: riverifica i record; /purge @utente: cancella i suoi tempi; opzionale 'saldo N' per sistemare il saldo."""
    t = await _adm_target(update)
    if t is None:
        return
    uid, rest = t
    msg = update.effective_message
    import webrace
    out = webrace.purge(uid or None)
    if uid and len(rest) >= 2 and rest[0].lower() == "saldo" and rest[1].lstrip("-").isdigit():
        from storage import add_user_coins, get_user_coins
        cur = int(get_user_coins(int(uid), int(uid))); new = max(0, int(rest[1]))
        add_user_coins(int(uid), int(uid), new - cur)
        out.append("saldo: %s → %s $SDG" % (fmt(cur), fmt(new)))
    await msg.reply_text("🧹 <b>Fatto</b>\n" + ("\n".join(html.escape(x) for x in out[:60]) if out else "Nessun record sospetto."), parse_mode="HTML")


async def fonti_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/fonti @utente: da quali giochi arrivano i suoi soldi (registro per gioco + ultime giocate), per scoprire come ha fatto."""
    t = await _adm_target(update)
    if t is None:
        return
    uid, _ = t
    msg = update.effective_message
    if not uid:
        await msg.reply_text("💸 Uso: <code>/fonti @utente</code>", parse_mode="HTML")
        return
    d = USER_DATA.get(uid, {})
    led = d.get("g_led") or {}
    L = ["💸 <b>FONTI di %s</b> (ID <code>%s</code>)" % (html.escape(d.get("name") or d.get("username") or uid), uid),
         "Saldo <b>%s</b> · netto totale dichiarato <b>%s</b> · giocate single %s" % (fmt(d.get("coins", 0)), fmt(d.get("net_profit", 0)), d.get("single_played", 0))]
    if led:
        L.append("\n📒 <b>Registro per gioco</b> (parte dall'ultimo aggiornamento):")
        for g, (n, sp, inc) in sorted(led.items(), key=lambda kv: kv[1][2] - kv[1][1], reverse=True):
            L.append("• %s: %d giocate · netto <b>%+d</b>" % (html.escape(g), n, inc - sp))
    else:
        L.append("\n📒 Registro per gioco ancora vuoto (si riempie dalle prossime giocate).")
    hist = d.get("history") or []
    if hist:
        agg = {}
        for h in hist:
            a = agg.setdefault(h["g"].split(" ")[0], [0, 0])
            a[0] += 1; a[1] += h["w"] - h["b"]
        L.append("\n🕓 <b>Ultime %d giocate</b> per gioco:" % len(hist))
        for g, (n, net) in sorted(agg.items(), key=lambda kv: kv[1][1], reverse=True):
            L.append("• %s: %d · netto <b>%+d</b>" % (html.escape(g), n, net))
        L.append("Prima: %s · ultima: %s" % (_rome_from_ts(hist[0]["t"]), _rome_from_ts(hist[-1]["t"])))
    wins = d.get("game_wins") or {}
    if wins:
        L.append("\n🏆 Vittorie totali: " + ", ".join("%s %d" % (html.escape(k), v) for k, v in sorted(wins.items(), key=lambda kv: -kv[1])))
    await msg.reply_text("\n".join(L), parse_mode="HTML")


def register(app):
    app.add_handler(CommandHandler("postgp", postgp_command))
    app.add_handler(CommandHandler(["annuncio", "dici"], annuncio_command))
    app.add_handler(CommandHandler("storico", storico_command))
    app.add_handler(CommandHandler("cheat", cheat_command))
    app.add_handler(CommandHandler("fonti", fonti_command))
    app.add_handler(CommandHandler("purge", purge_command))
