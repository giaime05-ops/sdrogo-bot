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
    if not hist:
        await msg.reply_text(head + "\nNessuna giocata registrata (la cronologia parte dall'aggiornamento).", parse_mode="HTML")
        return
    icon = {"vinta": "✅", "persa": "❌", "pareggio": "⚖️"}
    lines = []
    for h in reversed(hist):                                # dalla più recente
        net = h["w"] - h["b"]
        lines.append(
            f"{icon.get(h['o'], '•')} <code>{_rome_from_ts(h['t'])}</code> <b>{html.escape(h['g'])}</b> · "
            f"puntata {fmt(h['b'])} · incasso {fmt(h['w'])} (<b>{'+' if net >= 0 else '-'}{fmt(abs(net))}</b>)")
    text = head + f"\nUltime {len(hist)} giocate (dalla più recente):\n" + "\n".join(lines)
    await msg.reply_text(text[:4096], parse_mode="HTML")


def register(app):
    app.add_handler(CommandHandler(["annuncio", "dici"], annuncio_command))
    app.add_handler(CommandHandler("storico", storico_command))
