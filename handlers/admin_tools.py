"""Strumenti riservati all'Admin.

/annuncio <testo>  (alias /dici)  -  da chat PRIVATA: il bot scrive il testo nel gruppo principale (GROUP_CHAT_ID).
Il testo può contenere HTML (<b>, <i>, <a href=...>, <code>) oppure Markdown (*grassetto*, _corsivo_, `codice`).
Se si usa la formattazione di Telegram (grassetto, corsivo...) direttamente nel messaggio, viene mantenuta.
"""
import logging
import re

from telegram import Update
from telegram.error import BadRequest, Forbidden
from telegram.ext import CommandHandler, ContextTypes

from economy import main_chat_id
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


def register(app):
    app.add_handler(CommandHandler(["annuncio", "dici"], annuncio_command))
