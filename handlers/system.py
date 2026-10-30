"""Admin, gestione errori e router dei messaggi di testo (V1 + V2)."""
import html
import logging
import random
import time

from types import SimpleNamespace

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import RetryAfter, BadRequest, TimedOut, NetworkError
from telegram.ext import (
    CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes,
    ApplicationHandlerStop, TypeHandler,
)

from config import FRASE_PENITENZA, TARGET_MAP
from economy import (
    fmt, game_header, today_str, banco_take, challenge_start, quest_progress, follia_line, follia_enabled,
)
from state import (
    FLAGS, ACTIVE_DUELS, HIGHLOW_DUELS, DICE_DUELS, BLACKJACK_GAMES, WORDLE_GAMES,
    MASTERMIND_GAMES, QUIZ_GAMES, QUIZ_DUELS_1V1, GHIGLIOTTINA_DUELS,
    HEIST_GAMES, PENITENZE_ATTIVE, ACTIVE_PERSECUTE, TIC_TAC_TOE_GAMES,
    DAILY_DONATIONS, USER_DATA, GAME_STATUS, AVIATOR_GAMES, SCRATCH_GAMES, POKER_GAMES,
    WORK_GAMES, SYSTEM_FLAGS, GROUP_CHALLENGE, SANDBOX,
    SILENCED, MUTE_FILTER, REACT_TARGETS, ACTIVE_TITLES, PENDING_INPUT,
    game_enabled, MAINT_GAME_TEXT, maint_mode, user_block,
)
from storage import (LAST_SEEN, add_user_coins, get_user_coins, add_user_stat, save_db, chat_user_ids, log_play,
                     find_user_id, sandbox_set, is_sandbox)
from utils import is_admin, serialized, smart_edit
from handlers.hub import apply_title_command, apply_persecute_command
from handlers.multiplayer import (
    setup_challenge_menu, record_duel_result, quiz1v1_advance, rematch_row, STATUS_KEY, balances_line,
)
from handlers.games import (
    HEIST_COST, WORDLE_REWARDS, MASTERMIND_REWARDS, WORD_SET, norm_word, SCRATCH_COST, _settle,
)
from handlers.quiz import quiz_reward
from handlers import v2

MAINT_TEXT = (
    "🛠️ <b>SdrogoBot è in manutenzione</b>\n"
    "HUB e giochi sono sospesi per qualche minuto. Riprova tra poco!"
)
MAINT_ALERT = "🛠️ Bot in manutenzione: i giochi sono temporaneamente sospesi. Riprova tra poco!"
MAINT_COOLDOWN = 20   # secondi tra due avvisi nella stessa chat

DONATE_DEFAULT = 5
DONATE_MAX = 50

RICARICA_DEFAULT = 1000
RICARICA_MAX = 1_000_000

# parole in chat (senza "/") che avviano un gioco o un acquisto
GAME_TRIGGERS = {
    "sfido", "sfidodadi", "sfidotris", "sfidoroulette", "sfidohighlow",
    "sfidoghigliottina", "sfidoquiz", "titolo", "perseguita", "dona",
    "silenzia", "sanguisuga",
}

# Il MessageHandler esclude i comandi (~filters.COMMAND), quindi qui servono solo le parole senza "/"
CHALLENGES = {
    "sfidodadi": "dice",
    "sfidotris": "ttt",
    "sfidoroulette": "roulette",
    "sfidohighlow": "highlow",
    "sfidoghigliottina": "ghig",
    "sfidoquiz": "quiz1v1",
    "sfido": "roulette",
}

# Dashboard /admin: (chiave in GAME_STATUS, etichetta)
ADMIN_GAMES = [
    ("wordle", "🔠 Wordle"),
    ("blackjack", "🃏 Blackjack"),
    ("slot", "🎰 Slot 777"),
    ("mastermind", "🔐 Mastermind"),
    ("heist", "🏢 Sdrogo Heist"),
    ("aviator", "✈️ Aviator"),
    ("roulette", "🎡 Sdrogo Roulette"),
    ("scratch", "🎟️ Gratta e Vinci"),
    ("poker", "🃏 Sdrogo Poker"),
    ("lavora", "💼 /lavora"),
    ("dice", "🎲 Dadi 1v1"),
    ("ttt", "❌ Tris 1v1"),
    ("roulette1v1", "🎯 Roulette 1v1"),
    ("highlow", "📈 High/Low 1v1"),
    ("ghig", "🪓 Ghigliottina 1v1"),
    ("quiz1v1", "⚔️ Quiz 1v1"),
    ("quizmulti", "🌐 Quiz Multi"),
]


# --- DASHBOARD ADMIN (/admin) ---
def _admin_allowed(user_id: int, chat_type: str) -> bool:
    return is_admin(user_id) and chat_type == "private"


def _admin_view():
    gc = GROUP_CHALLENGE
    if gc.get("active"):
        left = max(0, int(gc.get("end", 0) - time.time()))
        chal = f"attiva ({fmt(gc.get('spent', 0))}/{fmt(gc.get('goal', 5000))} $SDG, {left // 86400}g {left % 86400 // 3600}h)"
    else:
        chal = "nessuna sfida attiva"
    text = (
        "🛠️ <b>DASHBOARD ADMIN</b>\n\n"
        f"🆕 Novità Arcade (nuovo Sdrogo Heist + profilo): <b>{'ON — la vedono tutti' if SYSTEM_FLAGS.get('NEW_PUBLIC') else 'OFF — solo account di prova'}</b>\n"
        f"📱 Mini app Arcade: <b>{'APERTA ai membri del gruppo' if SYSTEM_FLAGS.get('ARCADE_PUBLIC') else 'CHIUSA — solo tu'}</b>\n"
        f"🎮 Multiplayer per tutti: <b>{'ON — /sdrogocomm mostra solo profilo + Apri Arcade' if SYSTEM_FLAGS.get('MULTI_PUBLIC') else 'OFF — solo account di prova, /sdrogocomm come prima'}</b>\n"
        f"🎯 Sfida di gruppo: <b>{chal}</b>\n"
        f"{follia_line() or '🎪 Follia Sdrogo: spenta'}\n\n"
        "🟢 = attivo | 🔴 = in manutenzione\n"
        "Tocca un gioco per accenderlo o spegnerlo. "
        "<i>Le partite già in corso possono sempre finire.</i>"
    )
    rows = [
        [InlineKeyboardButton("🟢 Novità Arcade: ON" if SYSTEM_FLAGS.get("NEW_PUBLIC") else
                              ("🔴 Novità: OFF · timer %s" % __import__("datetime").datetime.fromtimestamp(float(SYSTEM_FLAGS["NEW_AT"]), __import__("economy")._TZ).strftime("%d/%m %H:%M")
                               if SYSTEM_FLAGS.get("NEW_AT") and __import__("economy")._TZ else "🔴 Novità Arcade: OFF (solo prova)"),
                              callback_data="adm_new")],
        [InlineKeyboardButton("⏰ Programma apertura novità", callback_data="adm_newat")],
        [InlineKeyboardButton("🟢 Mini app Arcade: APERTA" if SYSTEM_FLAGS.get("ARCADE_PUBLIC") else "🔴 Mini app Arcade: CHIUSA",
                              callback_data="adm_arc")],
        [InlineKeyboardButton("🟢 Multiplayer per tutti: ON" if SYSTEM_FLAGS.get("MULTI_PUBLIC") else "🔴 Multiplayer per tutti: OFF",
                              callback_data="adm_mp")],
        [InlineKeyboardButton("🎯 Configura Sfida di Gruppo", callback_data="adm_cfg"),
         InlineKeyboardButton("👥 Utenti online", callback_data="adm_on_0")],
        [InlineKeyboardButton("⏱️ Tempo d'uso (classifica settimanale)", callback_data="adm_use_0")],
        [InlineKeyboardButton("🗑️ Annulla sfida di gruppo", callback_data="adm_chalx")],
        [InlineKeyboardButton("🎪 Follia: ACCESA" if follia_enabled() else "🎪 Follia: SPENTA", callback_data="aec_fol"),
         InlineKeyboardButton("🔄 Reset saldi", callback_data="aec_menu")],
    ]
    row = []
    for key, label in ADMIN_GAMES:
        icon = "🟢" if GAME_STATUS.get(key, True) else "🔴"
        row.append(InlineKeyboardButton(f"{icon} {label}", callback_data=f"adm_toggle_{key}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([
        InlineKeyboardButton("🟢 Accendi tutti", callback_data="adm_all_1"),
        InlineKeyboardButton("🔴 Spegni tutti", callback_data="adm_all_0"),
    ])
    rows.append([InlineKeyboardButton("✖️ Chiudi", callback_data="adm_close")])
    return text, InlineKeyboardMarkup(rows)


# --- TEMPO D'USO ---
def _hm(secs) -> str:
    secs = int(secs or 0)
    return f"{secs // 3600}h {secs % 3600 // 60:02d}m"


def _usage_view(off: int = 0):
    """Classifica del tempo passato sul bot / mini app: settimana corrente (off=0) o scorsa (off=1)."""
    import datetime as _dt
    from economy import rome_now
    n = rome_now() - _dt.timedelta(days=7 * off)
    wk = n.strftime("%G-W%V")
    rows = []
    for uid, d in USER_DATA.items():
        if not str(uid).isdigit():
            continue
        w = int((d.get("use_wk") or {}).get(wk, 0))
        if w > 0:
            rows.append((w, int(d.get("use_secs", 0)), d.get("name") or uid))
    rows.sort(reverse=True)
    medals = ["🥇", "🥈", "🥉"]
    lines = [f"⏱️ <b>TEMPO D'USO — {'questa settimana' if off == 0 else 'settimana scorsa'}</b> ({wk})\n"]
    for i, (w, tot, name) in enumerate(rows[:20]):
        lines.append(f"{medals[i] if i < 3 else str(i + 1) + '.'} <b>{html.escape(str(name).split('#')[0])}</b> — {_hm(w)} <i>(totale {_hm(tot)})</i>")
    if not rows:
        lines.append("Ancora nessun dato: il tempo si conta dall'ultimo aggiornamento.")
    lines.append(f"\nTotale gruppo: <b>{_hm(sum(r[0] for r in rows))}</b> · si conta il tempo tra azioni ravvicinate (max 5 min di pausa)")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Settimana scorsa" if off == 0 else "➡️ Questa settimana", callback_data=f"adm_use_{1 - off}"),
         InlineKeyboardButton("📤 Manda nel gruppo", callback_data=f"adm_usesend_{off}")],
        [InlineKeyboardButton("⬅️ Dashboard", callback_data="adm_back")]])
    return "\n".join(lines), kb


# --- UTENTI ONLINE ---
ONLINE_WINDOW = 300      # "online" = ha usato il bot negli ultimi 5 minuti
ONLINE_PAGE = 15


def _ago(secs: float) -> str:
    secs = int(max(0, secs))
    if secs < 60:
        return "ora"
    if secs < 3600:
        return f"{secs // 60} min fa"
    if secs < 86400:
        return f"{secs // 3600} h fa"
    return f"{secs // 86400} g fa"


def _online_view(page: int = 0):
    now = time.time()
    rows_u = []
    for uid, d in USER_DATA.items():
        if not str(uid).isdigit():
            continue                                    # profili di prova (Sandbox) esclusi
        seen = LAST_SEEN.get(uid) or d.get("seen") or 0
        rows_u.append((seen, uid, d))
    rows_u.sort(key=lambda r: r[0], reverse=True)
    online = sum(1 for r in rows_u if r[0] and now - r[0] <= ONLINE_WINDOW)
    total = len(rows_u)
    pages = max(1, (total + ONLINE_PAGE - 1) // ONLINE_PAGE)
    page = max(0, min(page, pages - 1))
    lines = []
    for seen, uid, d in rows_u[page * ONLINE_PAGE:(page + 1) * ONLINE_PAGE]:
        on = bool(seen) and now - seen <= ONLINE_WINDOW
        label = f"@{d['username']}" if d.get("username") else (d.get("name") or f"ID {uid}")
        when = "online ora" if on else (_ago(now - seen) if seen else "mai visto")
        lines.append(f"{'🟢' if on else '🔴'} {html.escape(label)} · <i>{when}</i>")
    text = (
        "👥 <b>UTENTI ONLINE</b>\n\n"
        f"🟢 Online (ultimi {ONLINE_WINDOW // 60} min): <b>{online}</b> · 🔴 Offline: <b>{total - online}</b> · Totale: <b>{total}</b>\n\n"
        + ("\n".join(lines) if lines else "Nessun giocatore registrato.")
        + f"\n\n<i>Pagina {page + 1}/{pages}. Vedi solo chi sta usando il bot, non cosa fa.</i>"
    )
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"adm_on_{page - 1}"))
    nav.append(InlineKeyboardButton("🔄 Aggiorna", callback_data=f"adm_on_{page}"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"adm_on_{page + 1}"))
    return text, InlineKeyboardMarkup([nav, [InlineKeyboardButton("🚫 Gestisci accessi", callback_data="adm_blkm_0")],
                                       [InlineKeyboardButton("⬅️ Dashboard", callback_data="adm_back")]])


# --- ACCESSI: blocco per singolo utente ---
_BLOCK_LABEL = {0: "🟢 libero", 1: "🕹️ Arcade bloccata", 2: "🚫 tutto bloccato"}


def _set_block(uid, level: int):
    d = SYSTEM_FLAGS.setdefault("BLOCKS", {})
    if level <= 0:
        d.pop(str(uid), None)
    else:
        d[str(uid)] = int(level)
    save_db()


def _block_view(page: int = 0):
    now = time.time()
    rows_u = []
    for uid, d in USER_DATA.items():
        if not str(uid).isdigit() or is_admin(int(uid)):
            continue
        seen = LAST_SEEN.get(uid) or d.get("seen") or 0
        rows_u.append((user_block(uid), seen, uid, d))
    rows_u.sort(key=lambda r: (-r[0], -r[1]))                 # prima i bloccati, poi i più recenti
    pages = max(1, (len(rows_u) + 7) // 8)
    page = max(0, min(page, pages - 1))
    kb = []
    for lvl, seen, uid, d in rows_u[page * 8:(page + 1) * 8]:
        label = f"@{d['username']}" if d.get("username") else (d.get("name") or f"ID {uid}")
        kb.append([InlineKeyboardButton(f"{label[:22]} · {_BLOCK_LABEL[lvl]}", callback_data=f"adm_blk_{uid}_{page}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"adm_blkm_{page - 1}"))
    nav.append(InlineKeyboardButton("🔄", callback_data=f"adm_blkm_{page}"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"adm_blkm_{page + 1}"))
    kb.append(nav)
    kb.append([InlineKeyboardButton("⬅️ Dashboard", callback_data="adm_back")])
    text = ("🚫 <b>ACCESSI</b>\n\nTocca un utente per cambiare lo stato: <b>libero</b> → <b>Arcade bloccata</b> (non entra nella mini app) → "
            "<b>tutto bloccato</b> (niente /sdrogocomm, tasti e mini app) → libero.\n"
            "Oppure: <code>/blocca @utente</code> (tutto), <code>/blocca @utente arcade</code>, <code>/sblocca @utente</code>.\n"
            f"<i>Pagina {page + 1}/{pages}.</i>")
    return text, InlineKeyboardMarkup(kb)


async def ws_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/ws on | off | stat — interruttore del WebSocket delle corse (spento = tutto torna al sistema HTTP di sempre)."""
    user, msg = update.effective_user, update.effective_message
    if not user or not msg or not is_admin(user.id):
        return
    import wsrooms
    arg = (context.args[0].lower() if context.args else "stat")
    if arg == "on":
        if not wsrooms.url():
            await msg.reply_text("⚠️ Manca la variabile WS_URL su Northflank: senza indirizzo i giocatori non possono collegarsi. Vedi le istruzioni che ti ho dato.")
            return
        wsrooms.set_enabled(True)
    elif arg == "off":
        wsrooms.set_enabled(False)
    await msg.reply_text(wsrooms.stat_text() + "\n\n<code>/ws on</code> · <code>/ws off</code> · <code>/ws stat</code>", parse_mode="HTML")


async def block_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user, msg = update.effective_user, update.effective_message
    if not user or not msg or not is_admin(user.id):
        return
    parts = (msg.text or "").split()
    cmd = parts[0].lstrip("/").split("@")[0].lower()
    target = None
    if len(parts) > 1:
        target = find_user_id(parts[1])
    elif msg.reply_to_message and msg.reply_to_message.from_user:
        target = str(msg.reply_to_message.from_user.id)
    if not target:
        await msg.reply_text("Uso: <code>/blocca @utente</code> (oppure <code>/blocca @utente arcade</code>) · <code>/sblocca @utente</code>.", parse_mode="HTML")
        return
    if is_admin(int(target)):
        await msg.reply_text("Non puoi bloccare l'admin 🙂")
        return
    if cmd == "sblocca":
        _set_block(target, 0)
        await msg.reply_text("✅ Utente sbloccato.")
    else:
        lvl = 1 if (len(parts) > 2 and parts[2].lower().startswith("arc")) else 2
        _set_block(target, lvl)
        await msg.reply_text("🚫 Utente bloccato: " + _BLOCK_LABEL[lvl] + ".")


# --- MENU COMANDI ADMIN (/com) ---
def _com_view():
    maint = maint_mode() != "off"
    troll = FLAGS.get("troll", False)
    text = (
        "🧰 <b>COMANDI ADMIN</b>\n\n"
        "Tocca un tasto: i comandi senza parametri partono subito, per gli altri ti mostro come scriverli.\n\n"
        "🛠️ <b>/admin</b> – dashboard: giochi ON/OFF, V2, Sandbox, Sfida\n"
        "👥 <b>Utenti online</b> – chi sta usando il bot adesso\n"
        "🚧 <b>/manutenzione</b> – blocca il bot per tutti tranne te\n"
        "🧹 <b>/pen</b> – toglie le penitenze attive\n"
        "🙉 <b>/troll</b> – attiva/disattiva l'Auto-Troll\n"
        "🔄 <b>/resetduello</b> – annulla tutte le partite e rimborsa\n"
        "📜 <b>/storico</b> <code>@utente</code> – spese, incassi e sezioni aperte da un giocatore\n"
        "💸 <b>/fonti</b> <code>@utente</code> – da quali giochi arrivano i suoi soldi (caccia ai furbi)\n"
        "🕵️ <b>/cheat</b> <code>@utente</code> – i suoi tempi record, saldo e premi di oggi, con i sospetti\n"
        "🧹 <b>/purge</b> – riverifica i record di tutti · <code>/purge @utente [saldo N]</code> cancella i suoi tempi (e sistema il saldo)\n"
        "🚫 <b>/blocca</b> <code>@utente [arcade]</code> · <b>/sblocca</b> <code>@utente</code> – accessi (o menu «Gestisci accessi» in /admin)\n"
        "🏁 <b>/postgp</b> – pubblica nel gruppo il banner Multiplayer (tavoli, 1v1, Grand Prix) con il tasto «Gioca adesso»\n"
        "📣 <b>/annuncio</b> <code>testo</code> (o /dici) – scrive nel gruppo\n"
        "💰 <b>/ricarica</b> <code>[@utente] [quantità]</code> – regala $SDG\n"
        "🎯 <b>/sfida</b> <code>quota giorni</code> – sfida di gruppo con numeri liberi (es. 50000 7)\n"
        "💸 <b>/ricaricatutti</b> <code>quota</code> – regala $SDG a TUTTI\n"
        "🔄 <b>/resettutti</b> <code>[quota]</code> – tutti i saldi a quota (2000) + Banco 1000\n"
        "🏦 <b>/resetbanco</b> <code>[quota]</code> – solo il Banco (1000)\n"
        "👤 <b>/resetsoldi</b> <code>@utente [quota]</code> – un solo giocatore\n"
        "⏰ <b>/resetauto</b> – stato/annulla reset programmato (<code>off</code>)\n"
    )
    rows = [
        [InlineKeyboardButton("🛠️ Dashboard", callback_data="adm_c_admin"),
         InlineKeyboardButton("👥 Utenti online", callback_data="adm_on_0")],
        [InlineKeyboardButton("🚧 Manutenzione: " + ("ON" if maint else "OFF"), callback_data="adm_c_manut"),
         InlineKeyboardButton("🙉 Troll: " + ("ON" if troll else "OFF"), callback_data="adm_c_troll")],
        [InlineKeyboardButton("🧹 Togli penitenze", callback_data="adm_c_pen"),
         InlineKeyboardButton("🔄 Reset partite", callback_data="adm_c_reset")],
        [InlineKeyboardButton("🕵️ Heist: " + ("SEMPRE" if SYSTEM_FLAGS.get("HEIST_UNLIMITED") else "1 al giorno"), callback_data="adm_c_heistlim")],
        [InlineKeyboardButton("📜 Storico", callback_data="adm_c_storico"),
         InlineKeyboardButton("📣 Annuncio", callback_data="adm_c_annuncio"),
         InlineKeyboardButton("💰 Ricarica", callback_data="adm_c_ricarica")],
        [InlineKeyboardButton("💸 Fonti", callback_data="adm_c_fonti"),
         InlineKeyboardButton("🕵️ Cheat", callback_data="adm_c_cheat"),
         InlineKeyboardButton("🧹 Purge", callback_data="adm_c_purge")],
        [InlineKeyboardButton("🔌 WebSocket", callback_data="adm_c_ws")],
        [InlineKeyboardButton("🔄 Reset saldi", callback_data="aec_menu"),
         InlineKeyboardButton("💸 Ricarica TUTTI", callback_data="aec_rmenu")],
        [InlineKeyboardButton("🎯 Sfida libera", callback_data="aec_free")],
        [InlineKeyboardButton("✖️ Chiudi", callback_data="adm_close")],
    ]
    return text, InlineKeyboardMarkup(rows)


async def com_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user, chat = update.effective_user, update.effective_chat
    if not user or not is_admin(user.id):
        return
    if chat.type != "private":
        await update.message.reply_text("🔒 /com funziona solo in chat privata col bot.")
        return
    text, markup = _com_view()
    await update.message.reply_text(text, reply_markup=markup, parse_mode="HTML")


_COM_HELP = {
    "storico": "📜 Scrivi: <code>/storico @username</code> (oppure l'ID numerico).",
    "ws": "🔌 <code>/ws stat</code> stato · <code>/ws on</code> accende il WebSocket delle corse · <code>/ws off</code> lo spegne (le corse tornano subito a HTTP).",
    "fonti": "💸 Scrivi: <code>/fonti @username</code>: mostra da quali giochi arrivano i suoi soldi.",
    "cheat": "🕵️ Scrivi: <code>/cheat @username</code>: tempi record, saldo e premi di oggi con i sospetti.",
    "purge": "🧹 <code>/purge</code> riverifica tutti i record · <code>/purge @username</code> cancella i suoi tempi · <code>/purge @username saldo 500</code> li cancella e porta il saldo a 500.",
    "annuncio": "📣 Scrivi: <code>/annuncio il tuo testo</code> (o <code>/dici ...</code>): il bot lo pubblica nel gruppo.",
    "ricarica": "💰 Scrivi: <code>/ricarica 100</code> (a te) oppure <code>/ricarica @username 100</code>.",
}


async def _com_action(query, context, cmd: str):
    """Esegue dal menu /com un comando senza parametri, come se l'admin l'avesse scritto."""
    shim = SimpleNamespace(effective_user=query.from_user, effective_chat=query.message.chat,
                           effective_message=query.message, message=query.message, callback_query=None)
    if cmd == "admin":
        await admin_dashboard(shim, context)
    elif cmd == "manut":
        await toggle_maintenance(shim, context)
    elif cmd == "troll":
        await toggle_troll(shim, context)
    elif cmd == "pen":
        await clear_penalties(shim, context)
    elif cmd == "reset":
        await reset_duello(shim, context)



CHAL_GOALS = (5000, 10000, 15000, 25000)
CHAL_DAYS = ((1, "1 Giorno"), (3, "3 Giorni"), (7, "7 Giorni"))


def _chal_cfg_view():
    goal = int(GROUP_CHALLENGE.get("cfg_goal", 5000))
    days = int(GROUP_CHALLENGE.get("cfg_days", 1))
    text = (
        "🎯 <b>CONFIGURA SFIDA DI GRUPPO</b>\n\n"
        f"Quota target: <b>{fmt(goal)} $SDG</b>\n"
        f"Durata: <b>{days} {'giorno' if days == 1 else 'giorni'}</b>\n\n"
        "Scegli quota e durata, poi premi <b>Avvia</b>. "
        "<i>Una sfida già attiva viene sostituita; il messaggio nel gruppo viene fissato in silenzio.</i>"
    )
    rows = [[InlineKeyboardButton(("✅ " if g == goal else "") + fmt(g), callback_data=f"adm_cg_{g}") for g in CHAL_GOALS]]
    rows.append([InlineKeyboardButton(("✅ " if d == days else "") + lab, callback_data=f"adm_cd_{d}") for d, lab in CHAL_DAYS])
    rows.append([InlineKeyboardButton("✏️ Numeri liberi (es. 50000 7)", callback_data="aec_free")])
    rows.append([InlineKeyboardButton("🚀 Avvia sfida", callback_data="adm_chal")])
    rows.append([InlineKeyboardButton("🗑️ Annulla sfida attiva", callback_data="adm_chalx")])
    rows.append([InlineKeyboardButton("⬅️ Indietro", callback_data="adm_back")])
    return text, InlineKeyboardMarkup(rows)


async def admin_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user, chat = update.effective_user, update.effective_chat
    if not user or not is_admin(user.id):
        return                                    # i non-admin non ricevono nessuna risposta
    if chat.type != "private":
        await update.message.reply_text("🔒 /admin funziona solo in chat privata col bot.")
        return
    text, markup = _admin_view()
    await update.message.reply_text(text, reply_markup=markup, parse_mode="HTML")


async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _admin_allowed(query.from_user.id, query.message.chat.type):
        await query.answer("🔒 Area riservata all'admin in chat privata.", show_alert=True)
        return

    parts = query.data.split("_")        # adm_toggle_wordle | adm_all_1 | adm_v2 | adm_chal | adm_close
    action = parts[1]

    if action == "close":
        await query.answer()
        try:
            await query.message.delete()
        except Exception:
            pass
        return

    if action == "toggle":
        key = parts[2] if len(parts) > 2 else ""
        if key not in GAME_STATUS:
            await query.answer("Gioco sconosciuto.", show_alert=True)
            return
        GAME_STATUS[key] = not GAME_STATUS[key]
        await query.answer(f"{key}: {'🟢 acceso' if GAME_STATUS[key] else '🔴 spento'}")
    elif action == "all":
        value = parts[2] == "1" if len(parts) > 2 else True
        for key in GAME_STATUS:
            GAME_STATUS[key] = value
        await query.answer("Tutti i giochi " + ("🟢 accesi" if value else "🔴 spenti"))
    elif action == "new":
        SYSTEM_FLAGS["NEW_PUBLIC"] = not SYSTEM_FLAGS.get("NEW_PUBLIC", False)
        save_db()                                   # persistente: sopravvive ai riavvii
        await query.answer("🟢 NOVITÀ ATTIVE: nuovo Sdrogo Heist e personalizzazione profilo per tutti i membri."
                           if SYSTEM_FLAGS["NEW_PUBLIC"] else "🔴 Novità spente: restano solo nell'account di prova.", show_alert=True)
    elif action == "newat":
        context.user_data["await_newat"] = True
        await query.answer()
        await query.message.reply_text("⏰ A che ora aprire le novità? Scrivi l'ora italiana (es. <b>13:55</b>), oppure «ora» per aprire subito, «off» per annullare il timer.", parse_mode="HTML")
        return
    elif action == "mp":
        SYSTEM_FLAGS["MULTI_PUBLIC"] = not SYSTEM_FLAGS.get("MULTI_PUBLIC", False)
        if SYSTEM_FLAGS["MULTI_PUBLIC"]:
            SYSTEM_FLAGS["ARCADE_PUBLIC"] = True       # senza mini app aperta il tasto "Apri Arcade" non avrebbe senso
            try:                                    # e il pulsante blu "Apri Arcade" in chat privata diventa per tutti
                from telegram import MenuButtonWebApp, WebAppInfo
                import os as _os
                await context.bot.set_chat_menu_button(menu_button=MenuButtonWebApp(
                    text="🎮 Apri Arcade", web_app=WebAppInfo(url=_os.environ.get("ARCADE_URL", "https://giaime05-ops.github.io/sdrogobot-arcade/"))))
            except Exception as e:
                logging.warning(f"Pulsante Arcade per tutti non aggiornato: {e}")
        save_db()                                   # persistente: sopravvive ai riavvii
        await query.answer("🟢 MULTIPLAYER APERTO A TUTTI: la mini app mostra Multiplayer e /sdrogocomm diventa solo profilo + Apri Arcade (+ Quiz)."
                           if SYSTEM_FLAGS["MULTI_PUBLIC"] else "🔴 Multiplayer di nuovo solo nell'account di prova, /sdrogocomm com'era.", show_alert=True)
    elif action == "arc":
        SYSTEM_FLAGS["ARCADE_PUBLIC"] = not SYSTEM_FLAGS.get("ARCADE_PUBLIC", False)
        save_db()                                   # persistente: sopravvive ai riavvii
        try:                                        # il pulsante blu "Apri Arcade" in chat privata: per tutti quando è aperta
            from telegram import MenuButtonWebApp, MenuButtonDefault, WebAppInfo
            import os as _os
            if SYSTEM_FLAGS["ARCADE_PUBLIC"]:
                await context.bot.set_chat_menu_button(menu_button=MenuButtonWebApp(
                    text="🎮 Apri Arcade", web_app=WebAppInfo(url=_os.environ.get("ARCADE_URL", "https://giaime05-ops.github.io/sdrogobot-arcade/"))))
            else:
                await context.bot.set_chat_menu_button(menu_button=MenuButtonDefault())
        except Exception as e:
            logging.warning(f"Pulsante Arcade per tutti non aggiornato: {e}")
        await query.answer("🟢 Mini app APERTA ai membri del gruppo." if SYSTEM_FLAGS["ARCADE_PUBLIC"]
                           else "🔴 Mini app CHIUSA: solo tu.", show_alert=True)
    elif action == "cfg":
        await query.answer()
        await smart_edit(query, *_chal_cfg_view())
        return
    elif action == "cg":                            # adm_cg_<quota>
        try:
            GROUP_CHALLENGE["cfg_goal"] = int(parts[2])
        except (IndexError, ValueError):
            pass
        await query.answer(f"Quota: {fmt(GROUP_CHALLENGE['cfg_goal'])} $SDG")
        await smart_edit(query, *_chal_cfg_view())
        return
    elif action == "cd":                            # adm_cd_<giorni>
        try:
            GROUP_CHALLENGE["cfg_days"] = max(1, int(parts[2]))
        except (IndexError, ValueError):
            pass
        await query.answer(f"Durata: {GROUP_CHALLENGE['cfg_days']} g")
        await smart_edit(query, *_chal_cfg_view())
        return
    elif action == "chalx":
        from economy import challenge_cancel
        was = await challenge_cancel(context.bot)
        await query.answer("🗑️ Sfida annullata (nessun premio, non ne parte un'altra)." if was else "Non c'era nessuna sfida attiva.", show_alert=True)
        await smart_edit(query, *_admin_view())
        return
    elif action == "chal":
        await query.answer()
        ok, note = await challenge_start(
            context.bot,
            goal=int(GROUP_CHALLENGE.get("cfg_goal", 5000)),
            days=int(GROUP_CHALLENGE.get("cfg_days", 1)),
        )
        try:
            await context.bot.send_message(chat_id=query.from_user.id, text=("✅ " if ok else "⚠️ ") + note)
        except Exception:
            pass
        await smart_edit(query, *_admin_view())
        return
    elif action == "blkm":                          # adm_blkm_<pagina>
        await query.answer()
        try:
            page = int(parts[2])
        except (IndexError, ValueError):
            page = 0
        await smart_edit(query, *_block_view(page))
        return
    elif action == "blk":                           # adm_blk_<uid>_<pagina>: cicla libero -> arcade -> tutto
        try:
            uid_t, page = parts[2], int(parts[3])
        except (IndexError, ValueError):
            await query.answer()
            return
        _set_block(uid_t, (user_block(uid_t) + 1) % 3)
        await query.answer(_BLOCK_LABEL[user_block(uid_t)])
        await smart_edit(query, *_block_view(page))
        return
    elif action == "use":                           # adm_use_<0|1>
        await query.answer()
        try:
            off = int(parts[2])
        except (IndexError, ValueError):
            off = 0
        await smart_edit(query, *_usage_view(off))
        return
    elif action == "usesend":                       # adm_usesend_<0|1>: pubblica la classifica nel gruppo
        try:
            off = int(parts[2])
        except (IndexError, ValueError):
            off = 0
        from economy import main_chat_id
        cid = main_chat_id()
        if not cid:
            await query.answer("Gruppo non trovato.", show_alert=True)
            return
        await context.bot.send_message(chat_id=int(cid), text=_usage_view(off)[0], parse_mode="HTML")
        await query.answer("📤 Inviata nel gruppo.")
        return
    elif action == "on":                            # adm_on_<pagina>
        await query.answer()
        try:
            page = int(parts[2])
        except (IndexError, ValueError):
            page = 0
        await smart_edit(query, *_online_view(page))
        return
    elif action == "c":                             # adm_c_<comando> (menu /com)
        cmd = parts[2] if len(parts) > 2 else ""
        if cmd in _COM_HELP:
            await query.answer()
            await query.message.reply_text(_COM_HELP[cmd], parse_mode="HTML")
            return
        if cmd == "heistlim":
            SYSTEM_FLAGS["HEIST_UNLIMITED"] = not SYSTEM_FLAGS.get("HEIST_UNLIMITED", False)
            save_db()
            await query.answer("🕵️ Sdrogo Heist: " + ("SEMPRE ATTIVO (senza limite giornaliero)" if SYSTEM_FLAGS["HEIST_UNLIMITED"] else "1 rapina al giorno"), show_alert=True)
            await smart_edit(query, *_admin_view())
            return
        if cmd == "reset":
            await query.answer()
            await smart_edit(query, "🔄 <b>Reset partite</b>\n\nAnnulla TUTTE le partite in corso (duelli, blackjack, poker...) "
                             "e rimborsa le puntate. Vuoi procedere?",
                             InlineKeyboardMarkup([[InlineKeyboardButton("✅ Sì, resetta", callback_data="adm_c_resetok"),
                                                    InlineKeyboardButton("❌ Annulla", callback_data="adm_com")]]))
            return
        await query.answer()
        await _com_action(query, context, "reset" if cmd == "resetok" else cmd)
        if cmd == "resetok":
            await smart_edit(query, *_com_view())
        elif cmd in ("manut", "troll"):
            await smart_edit(query, *_com_view())
        return
    elif action == "com":
        await query.answer()
        await smart_edit(query, *_com_view())
        return
    elif action == "back":
        await query.answer()
    else:
        await query.answer()
        return

    text, markup = _admin_view()
    await smart_edit(query, text, markup)


# --- UTILITIES / ADMIN ---
SOFT_NOTICE = ("🛠️ <b>Il server andrà in manutenzione al termine di questa partita.</b>\n"
               "Finisci pure la mano/il giro che hai già iniziato, ma non potrai più cominciarne di nuove.")


async def toggle_maintenance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/manutenzione        → avvia la manutenzione GRADUALE (si finisce la partita in corso, poi stop) oppure la spegne
       /manutenzione ora    → blocco immediato di bot e mini app
    Blocca /sdrogocomm e l'Arcade (mini app) per tutti TRANNE l'admin (che quindi non vede alcun blocco)."""
    if not is_admin(update.effective_user.id):
        return
    msg = update.effective_message or update.message
    arg = ((getattr(msg, "text", "") or "").split() + [""])[1].lower() if getattr(msg, "text", "") else ""
    if maint_mode() != "off":
        FLAGS["maintenance"] = False
        await msg.reply_text("✅ Manutenzione DISATTIVATA: bot e mini app sono di nuovo operativi.")
        return
    hard = arg in ("ora", "subito", "hard")
    FLAGS["maintenance"] = True if hard else "soft"
    FLAGS["maint_since"] = time.time()
    if hard:
        await msg.reply_text("🛠️ Manutenzione ATTIVA (blocco immediato): /sdrogocomm e Arcade sono chiusi per tutti tranne te.")
        return
    sent = 0
    now = time.time()
    for uid, ts in list(LAST_SEEN.items()):
        if now - ts > 600 or is_admin(int(uid)) or str(uid).startswith("sbx_"):
            continue
        try:
            await context.bot.send_message(chat_id=int(uid), text=SOFT_NOTICE, parse_mode="HTML")
            sent += 1
        except Exception:
            pass
    await msg.reply_text(
        "🛠️ Manutenzione GRADUALE attiva: chi sta giocando può finire la partita in corso, ma non può iniziarne di nuove; "
        "/sdrogocomm è già chiuso. Dopo 10 minuti il blocco diventa totale (oppure usa <code>/manutenzione ora</code>).\n"
        f"📣 Avviso inviato a {sent} utenti online.\n<i>Tu (admin) non vedi blocchi: sei sempre esente.</i>",
        parse_mode="HTML")


async def block_gate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Utenti con accesso sospeso dall'admin (livello 2): comandi, tasti e parole-trigger vengono ignorati."""
    user = update.effective_user
    if not user or is_admin(user.id) or user_block(user.id) < 2:
        return
    if update.callback_query:
        try:
            await update.callback_query.answer("🚫 Il tuo accesso al bot è stato sospeso dall'admin.", show_alert=True)
        except Exception:
            pass
        raise ApplicationHandlerStop
    msg = update.effective_message
    first = ((msg.text or "").strip().lower().split() or [""])[0] if msg else ""
    if first.startswith("/") or first in GAME_TRIGGERS:
        raise ApplicationHandlerStop


async def maintenance_gate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mode = maint_mode()
    if mode == "off":
        return
    user = update.effective_user
    if user and is_admin(user.id):
        return                                   # l'admin passa sempre
    if mode == "soft" and update.callback_query:
        return                                   # graduale: i tasti delle partite già iniziate continuano a funzionare

    # 1) Bottoni inline: solo popup, nessun messaggio in chat
    if update.callback_query:
        try:
            await update.callback_query.answer(MAINT_ALERT, show_alert=True)
        except Exception:
            pass
        raise ApplicationHandlerStop

    msg = update.effective_message
    if not msg or not msg.text:
        return

    text = msg.text.strip().lower()
    if not text:
        return
    first = text.split()[0]

    # 2) Comandi: solo /sdrogocomm riceve l'avviso, tutti gli altri vengono ignorati in silenzio
    if first.startswith("/"):
        name, _, for_bot = first[1:].partition("@")
        if for_bot and for_bot != (context.bot.username or "").lower():
            return                               # comando indirizzato a un altro bot
        if name != "sdrogocomm":
            raise ApplicationHandlerStop         # nessuna risposta
    # 3) Parole-trigger dei giochi ("sfidoquiz @x", "titolo @x", ...)
    elif first not in GAME_TRIGGERS:
        return                                   # testo normale: NON fare nulla

    # avviso con cooldown per non spammare
    last = context.bot_data.setdefault("maint_last", {})
    now = time.time()
    if now - last.get(msg.chat_id, 0) > MAINT_COOLDOWN:
        last[msg.chat_id] = now
        try:
            await msg.reply_text(MAINT_TEXT, parse_mode="HTML")
        except Exception:
            pass
    raise ApplicationHandlerStop


async def _find_user_id_by_username(context: ContextTypes.DEFAULT_TYPE, chat_id: int, username: str):
    """Cerca l'ID di un utente della chat dal suo @username: prima la cache, poi la scansione del registro."""
    cache = context.bot_data.setdefault("usernames", {})
    uid = cache.get(f"{chat_id}_{username}")
    if uid:
        return int(uid)
    for candidate in chat_user_ids(chat_id):
        try:
            m = await context.bot.get_chat_member(chat_id, int(candidate))
            if m.user.username and m.user.username.lower() == username:
                cache[f"{chat_id}_{username}"] = int(candidate)
                return int(candidate)
        except Exception:
            pass
    return None


async def ricarica_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/ricarica [quantità] (su te stesso, in qualunque chat)
       /ricarica @username [quantità]  oppure  risposta a un messaggio + /ricarica [quantità] (gruppi).
       Solo admin: gli altri vengono ignorati in silenzio."""
    user, chat, msg = update.effective_user, update.effective_chat, update.message
    if not user or not msg or not is_admin(user.id):
        return

    parts = (msg.text or "").split()[1:]
    amount = RICARICA_DEFAULT
    amount_set = False
    target_username = None
    for p in parts:
        if p.startswith("@") and len(p) > 1 and not target_username:
            target_username = p[1:].lower()
        elif p.isdigit() and not amount_set:
            amount = int(p)
            amount_set = True

    if amount < 1 or amount > RICARICA_MAX:
        await msg.reply_text(f"❌ La quantità deve essere tra 1 e {RICARICA_MAX} $SDG.")
        return

    chat_id = chat.id
    target_id = None
    target_label = None

    reply = msg.reply_to_message
    if target_username:
        if chat.type == "private":
            await msg.reply_text(
                "🔒 In privato posso ricaricare solo te: i saldi sono separati per chat. "
                "Usa <code>/ricarica @username [quantità]</code> nel gruppo.",
                parse_mode="HTML")
            return
        target_id = await _find_user_id_by_username(context, chat_id, target_username)
        target_label = f"@{html.escape(target_username)}"
    elif reply and reply.from_user:
        if reply.from_user.is_bot:
            await msg.reply_text("❌ Non puoi ricaricare un bot!")
            return
        target_id = reply.from_user.id
        target_label = html.escape(reply.from_user.first_name or "utente")
    else:
        target_id = user.id
        target_label = "te"

    if not target_id:
        await msg.reply_text("❌ Utente non trovato nel registro della chat!")
        return

    add_user_coins(chat_id, target_id, amount)
    await msg.reply_text(
        f"🧪 <b>RICARICA ADMIN:</b> +💳 {fmt(amount)} $SDG a {target_label}. "
        f"Saldo: <b>{fmt(get_user_coins(chat_id, target_id))} $SDG</b>",
        parse_mode="HTML")


async def toggle_troll(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    FLAGS["troll"] = not FLAGS["troll"]
    await update.message.reply_text(f"Modalità Auto-Troll: {'ATTIVATA 🙉' if FLAGS['troll'] else 'DISATTIVATA 🛑'}")


async def clear_penalties(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    n = len(PENITENZE_ATTIVE)
    PENITENZE_ATTIVE.clear()
    for d in (SILENCED, MUTE_FILTER, REACT_TARGETS, ACTIVE_PERSECUTE, ACTIVE_TITLES, PENDING_INPUT):
        n += len(d)
        d.clear()
    FLAGS["troll"] = False                      # niente più "silenzio totale"/reazioni automatiche verso i due bersagli
    await update.message.reply_text(
        f"🧹 Penitenze rimosse! ({n} effetti annullati: silenzi, filtri muti, persecuzioni, titoli, "
        "reazioni forzate e Auto-Troll spento.)")
    save_db()


async def reset_duello(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    for d in (HIGHLOW_DUELS, DICE_DUELS, ACTIVE_DUELS, TIC_TAC_TOE_GAMES,
              GHIGLIOTTINA_DUELS, QUIZ_DUELS_1V1):
        for cid, g in list(d.items()):
            if g.get("target_id") and g.get("bet"):      # accettato = puntate già scalate
                add_user_coins(cid, g["sfidante_id"], g["bet"])
                add_user_coins(cid, g["target_id"], g["bet"])
        d.clear()
    for key, g in list(BLACKJACK_GAMES.items()):
        cid, uid = key.rsplit("_", 1)
        add_user_coins(int(cid), int(uid), g["bet"])
    for key, g in list(POKER_GAMES.items()):
        cid, uid = key.rsplit("_", 1)
        add_user_coins(int(cid), int(uid), g.get("pot_p", g.get("bet", 0)))
    for store in (WORDLE_GAMES, MASTERMIND_GAMES):
        for key in list(store.keys()):
            cid, uid = key.rsplit("_", 1)
            add_user_coins(int(cid), int(uid), 10)
    for key, g in list(QUIZ_GAMES.items()):
        if not g.get("multi") and "_" in str(key):
            cid, uid = str(key).rsplit("_", 1)
            add_user_coins(int(cid), int(uid), 5)
    for uid, g in list(HEIST_GAMES.items()):
        add_user_coins(g["chat_id"], uid, HEIST_COST)
        if g.get("v2"):
            banco_take(HEIST_COST)                       # il pass V2 era finito nel Banco: lo rimetto al giocatore
    # Aviator / Gratta e Vinci: voli in corso (puntata rimborsata, il ciclo di volo si ferma) e biglietti non finiti
    for key, g in list(AVIATOR_GAMES.items()):
        if not g.get("finished"):
            g["finished"] = True
            cid, uid = key.rsplit("_", 1)
            add_user_coins(int(cid), int(uid), g["bet"])
    for key in list(SCRATCH_GAMES.keys()):
        cid, uid = key.rsplit("_", 1)
        add_user_coins(int(cid), int(uid), SCRATCH_COST)
    for d in (BLACKJACK_GAMES, POKER_GAMES, WORDLE_GAMES, MASTERMIND_GAMES, QUIZ_GAMES, HEIST_GAMES,
              AVIATOR_GAMES, SCRATCH_GAMES, WORK_GAMES):
        d.clear()
    await update.message.reply_text("🛠️ Giochi resettati. Tutte le puntate in corso sono state rimborsate.")


# --- COMANDO DONAZIONE CREDITI (/dona @utente [importo]) ---
@serialized("user")
async def donate_coins_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    text = (update.message.text or "").strip()

    # importo opzionale: il primo numero dopo il comando (default 5, massimo 50)
    amount = DONATE_DEFAULT
    for part in text.split()[1:]:
        if part.isdigit():
            amount = int(part)
            break
    if amount < 1 or amount > DONATE_MAX:
        await update.message.reply_text(f"❌ Puoi donare da 1 a {DONATE_MAX} $SDG per volta.", parse_mode="HTML")
        return

    target_username = None
    target_id = None
    target_label = None
    for part in text.split():
        if part.startswith("@") and len(part) > 1:
            target_username = part[1:].lower()
            break

    reply = update.message.reply_to_message
    if not target_username and reply and reply.from_user:
        if reply.from_user.is_bot:
            await update.message.reply_text("❌ Non puoi donare crediti a un bot!", parse_mode="HTML")
            return
        target_id = reply.from_user.id
        target_label = html.escape(reply.from_user.first_name or "utente")

    if not target_username and not target_id:
        await update.message.reply_text(
            f"❌ Uso corretto: <code>/dona @username [importo]</code> (da 1 a {DONATE_MAX} $SDG, default {DONATE_DEFAULT})",
            parse_mode="HTML")
        return

    if target_username and target_username == (user.username or "").lower():
        await update.message.reply_text("❌ Non puoi donare crediti a te stesso!", parse_mode="HTML")
        return

    if target_username:
        target_label = f"@{html.escape(target_username)}"
        target_id = await _find_user_id_by_username(context, chat_id, target_username)

    if not target_id:
        await update.message.reply_text("❌ Utente destinatario non trovato nel registro della chat!", parse_mode="HTML")
        return
    if target_id == user.id:
        await update.message.reply_text("❌ Non puoi donare crediti a te stesso!", parse_mode="HTML")
        return

    today_str_ = today_str()
    DAILY_DONATIONS.difference_update({k for k in DAILY_DONATIONS if not k.endswith(today_str_)})
    donation_key = f"{chat_id}_{user.id}_{target_id}_{today_str_}"
    if donation_key in DAILY_DONATIONS:
        await update.message.reply_text("❌ Hai già fatto una donazione a questo utente oggi! Riprova domani.", parse_mode="HTML")
        return

    if get_user_coins(chat_id, user.id) < amount:
        await update.message.reply_text(f"❌ Non hai abbastanza $SDG (servono almeno {amount} crediti)!", parse_mode="HTML")
        return

    add_user_coins(chat_id, user.id, -amount)
    add_user_coins(chat_id, target_id, amount)
    DAILY_DONATIONS.add(donation_key)

    donor = f"@{html.escape(user.username)}" if user.username else html.escape(user.first_name or "Utente")
    await update.message.reply_text(f"🎁 <b>DONAZIONE EFFETTUATA!</b> {donor} ha donato <b>💳 {amount} $SDG</b> a {target_label}!", parse_mode="HTML")


# --- SISTEMA DI DEBUG ED ERRORI ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    err = context.error
    if isinstance(err, RetryAfter):
        ra = err.retry_after
        secs = int(ra.total_seconds()) if hasattr(ra, "total_seconds") else int(ra)
        logging.warning(f"Flood control: Telegram chiede di attendere {secs}s")
        cq = getattr(update, "callback_query", None)
        if cq:
            try:
                await cq.answer(f"⏳ Troppe richieste, riprova tra {secs + 1} secondi.", show_alert=True)
            except Exception:
                pass
        return
    if isinstance(err, BadRequest) and "not modified" in str(err).lower():
        return
    if isinstance(err, (TimedOut, NetworkError)):
        # timeout di rete verso Telegram (httpx.ReadTimeout...): succede ogni tanto, non è un bug del codice.
        # Si registra una riga sola, senza traceback, e si prosegue; il giocatore può riprovare.
        logging.warning(f"Timeout/rete verso Telegram ({type(err).__name__}): {err}")
        cq = getattr(update, "callback_query", None)
        if cq:
            try:
                await cq.answer("⚠️ Telegram è lento, riprova tra un attimo.")
            except Exception:
                pass
        return
    logging.error(msg="Eccezione non gestita durante l'update:", exc_info=err)
    print(f"🚨 ERRORE: {err}", flush=True)


async def catch_all_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cq = update.callback_query
    name = update.effective_user.first_name if update.effective_user else "?"
    logging.debug(f"Click ricevuto: {cq.data if cq else None} da {name}")


# --- HANDLER MESSAGGI GENERICI (TEXT ROUTER) ---
async def handle_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.from_user: return

    user = update.message.from_user
    chat_id_int = update.message.chat_id
    chat_id = str(chat_id_int)
    text = (update.message.text or "").strip()
    username_lower = user.username.lower() if user.username else ""
    uname = html.escape(user.first_name or "Giocatore")

    # Cache username -> id, usata da /dona, /ricarica e dagli oggetti V2
    if username_lower:
        context.bot_data.setdefault("usernames", {})[f"{chat_id}_{username_lower}"] = user.id

    # Effetti V2: silenzio, filtro, reazioni forzate, input privato (frase d'umiliazione)
    if await v2.process_effects(update, context):
        return

    # Handling Penitenze
    if user.id in PENITENZE_ATTIVE and PENITENZE_ATTIVE[user.id] > 0:
        if text.lower() != FRASE_PENITENZA:
            try:
                await update.message.delete()
                await context.bot.send_message(chat_id=chat_id, text=f"🚫 Devi scrivere esattamente: `{FRASE_PENITENZA}`", parse_mode="Markdown")
                return
            except Exception: pass
        else:
            del PENITENZE_ATTIVE[user.id]
            await update.message.reply_text(f"✅ {user.first_name} riabilitato!")
            return

    # Handling Tag Persecutore (V1)
    persecute_key = f"{chat_id}_{username_lower}"
    if persecute_key in ACTIVE_PERSECUTE:
        p_data = ACTIVE_PERSECUTE[persecute_key]
        if p_data["count"] > 0:
            p_data["count"] -= 1
            await update.message.reply_text(p_data["phrase"])
            if p_data["count"] == 0:
                del ACTIVE_PERSECUTE[persecute_key]

    # Prima parola del messaggio: i trigger testuali scattano solo se è la prima parola E c'è un bersaglio
    first = text.lower().split()[0] if text else ""
    has_mention = "@" in text
    has_reply = update.message.reply_to_message is not None

    # Handling Comandi Shop Testuali e Dona
    if first == "titolo" and (has_mention or has_reply):
        await apply_title_command(update, context)
        return
    if first == "perseguita" and (has_mention or has_reply):
        await apply_persecute_command(update, context)
        return
    if first == "silenzia" and (has_mention or has_reply):
        await v2.silence_command(update, context)
        return
    if first == "sanguisuga" and (has_mention or has_reply):
        await v2.sang_command(update, context)
        return
    if first == "dona" and (has_mention or has_reply):
        await donate_coins_command(update, context)
        return

    # Handling Sfide Unificate
    if first in CHALLENGES and (first != "sfido" or has_mention):
        if not game_enabled(STATUS_KEY[CHALLENGES[first]]):
            await update.message.reply_text(MAINT_GAME_TEXT)
            return
        target = next((p[1:].lower() for p in text.split() if p.startswith("@") and len(p) > 1), None)
        if not target:
            await update.message.reply_text(f"❌ Uso: <code>{first} @username</code>", parse_mode="HTML")
            return
        if target == username_lower:
            await update.message.reply_text("🤡 Non puoi sfidare te stesso!")
            return
        await setup_challenge_menu(update, context, CHALLENGES[first], target, chat_id_int, user)
        return

    text_upper = text.upper()

    # Handling Risposta Ghigliottina 1v1
    if chat_id_int in GHIGLIOTTINA_DUELS and GHIGLIOTTINA_DUELS[chat_id_int].get("active"):
        duel = GHIGLIOTTINA_DUELS[chat_id_int]
        if user.id in [duel["sfidante_id"], duel["target_id"]] and text_upper == duel["word"]:
            loser_id = duel["target_id"] if user.id == duel["sfidante_id"] else duel["sfidante_id"]
            del GHIGLIOTTINA_DUELS[chat_id_int]
            # record_duel_result aggiorna già la missione "Vinci 1 duello" e restituisce il messaggio da mostrare
            qmsg = record_duel_result(chat_id_int, user.id, loser_id, duel["bet"], "ghig")
            montepremi = duel["bet"] * 2
            end_keyboard = [
                rematch_row("ghig", duel),
                [InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{user.id}")]
            ]
            await update.message.reply_text(
                f"🏆 <b>GHIGLIOTTINA RISOLTA!</b> <b>{uname}</b> indovina <b>{duel['word']}</b> e vince +💳 {fmt(montepremi)} $SDG!\n\n"
                + balances_line(chat_id_int, [(duel["sfidante_id"], duel["sfidante_name"]), (duel["target_id"], duel["target_name"])])
                + (f"\n{qmsg}" if qmsg else ""),
                reply_markup=InlineKeyboardMarkup(end_keyboard), parse_mode="HTML")
            return

    # Handling Risposta Duello Quiz 1v1
    if chat_id_int in QUIZ_DUELS_1V1 and QUIZ_DUELS_1V1[chat_id_int].get("active"):
        duel = QUIZ_DUELS_1V1[chat_id_int]
        if user.id in [duel["sfidante_id"], duel["target_id"]] and text_upper == duel.get("current_target"):
            duel["msg_id"] = None          # invalida il timer del round appena vinto
            duel["current_target"] = None  # nessun secondo punto per la stessa domanda
            if user.id == duel["sfidante_id"]: duel["p1_score"] += 1
            else: duel["p2_score"] += 1
            await update.message.reply_text(f"🎯 <b>RISPOSTA CORRETTA!</b> <b>{uname}</b> prende il punto del Round {duel['round']}!", parse_mode="HTML")
            await quiz1v1_advance(context.bot, chat_id_int)
            return

    # Handling Mastermind Express
    mm_key = f"{chat_id}_{user.id}"
    mm = MASTERMIND_GAMES.get(mm_key)
    if mm and len(text) == 3 and text.isdigit():
        if len(set(text)) < 3:
            await update.message.reply_text("❌ Il codice ha <b>3 cifre tutte diverse</b>: questo tentativo non conta.", parse_mode="HTML")
            return
        mm["last"] = time.time()
        mm["attempts"] += 1
        secret = mm["secret"]
        c_hits = sum(1 for i in range(3) if text[i] == secret[i])
        p_hits = sum(1 for i in range(3) if text[i] != secret[i] and text[i] in secret)
        res_str = f"🎯 {c_hits} Centrati | 🔄 {p_hits} Presenti | ❌ {3 - (c_hits + p_hits)} Assenti"
        mm["history"].append(f"<code>{text}</code> -> {res_str}")
        res_text = "\n".join(mm["history"])
        end_keyboard = [[InlineKeyboardButton("🔂 Rigioca", callback_data=f"start_mm_{user.id}"), InlineKeyboardButton("🔙 HUB", callback_data=f"hub_main_{user.id}")] ]
        title = "🔐 <b>MASTERMIND EXPRESS</b> 🔐"
        if c_hits == 3:
            del MASTERMIND_GAMES[mm_key]
            reward = MASTERMIND_REWARDS.get(mm["attempts"], MASTERMIND_REWARDS[5])
            paid, notes = await _settle(context.bot, chat_id_int, user.id, "mastermind", 10, reward, is_casino=False)
            qmsg = quest_progress(chat_id_int, user.id, "mm_win")
            head = game_header(title, user.first_name, get_user_coins(chat_id_int, user.id), 10)
            await update.message.reply_text(
                f"{head}🎉 <b>ESATTO!</b> Codice: <b>{secret}</b>! Vinti +💳 {fmt(paid)} $SDG!"
                + ("\n" + "\n".join(notes) if notes else "") + (f"\n{qmsg}" if qmsg else "") + f"\n\n{res_text}",
                reply_markup=InlineKeyboardMarkup(end_keyboard), parse_mode="HTML")
        elif mm["attempts"] >= 5:
            del MASTERMIND_GAMES[mm_key]
            _, notes = await _settle(context.bot, chat_id_int, user.id, "mastermind", 10, 0, is_casino=False)
            head = game_header(title, user.first_name, get_user_coins(chat_id_int, user.id), 10)
            await update.message.reply_text(
                f"{head}💥 <b>GAME OVER!</b> Il codice era {secret}." + ("\n" + "\n".join(notes) if notes else "") + f"\n\n{res_text}",
                reply_markup=InlineKeyboardMarkup(end_keyboard), parse_mode="HTML")
        else:
            head = game_header(title, user.first_name, get_user_coins(chat_id_int, user.id), 10)
            await update.message.reply_text(f"{head}<b>Tentativo {mm['attempts']}/5</b>\n\n{res_text}", parse_mode="HTML")
        return

    # Handling Quiz Multiplayer & Single (con statistiche Quizzes Won)
    if chat_id in QUIZ_GAMES and QUIZ_GAMES[chat_id].get("multi") and text_upper == QUIZ_GAMES[chat_id]["target"]:
        q = QUIZ_GAMES[chat_id]
        del QUIZ_GAMES[chat_id]
        add_user_coins(chat_id_int, user.id, 15)
        add_user_stat(chat_id_int, user.id, "quizzes_won", 1)
        qmsg = quest_progress(chat_id_int, user.id, "quizmulti_win")
        await update.message.reply_text(
            f"🎉 <b>QUIZ MULTI RISOLTO!</b> <b>{uname}</b> indovina <b>{q['target']}</b> e vince +💳 15 $SDG!"
            + (f"\n{qmsg}" if qmsg else ""), parse_mode="HTML")
        return

    quiz_key = f"{chat_id}_{user.id}"
    if quiz_key in QUIZ_GAMES and text_upper == QUIZ_GAMES[quiz_key]["target"]:
        q = QUIZ_GAMES[quiz_key]
        del QUIZ_GAMES[quiz_key]
        reward = quiz_reward(q["step"])
        add_user_coins(chat_id_int, user.id, reward)
        add_user_stat(chat_id_int, user.id, "quizzes_won", 1)
        log_play(user.id, "quiz", 5, reward)
        qmsg = quest_progress(chat_id_int, user.id, "quiz_win")
        await update.message.reply_text(
            f"🎉 <b>CORRETTO!</b> <b>{uname}</b> indovina <b>{q['target']}</b>! Vinti +💳 {reward} $SDG!"
            + (f"\n{qmsg}" if qmsg else ""), parse_mode="HTML")
        return

    # Handling Wordle
    game_key = f"{chat_id}_{user.id}"
    game = WORDLE_GAMES.get(game_key)
    if game and len(text_upper) == 5 and text_upper.isalpha():
        guess = norm_word(text_upper)          # "CITTÀ" -> "CITTA"
        if len(guess) != 5:
            return
        if guess not in WORD_SET:
            await update.message.reply_text("❌ Questa parola non è nel dizionario italiano: il tentativo non conta.", parse_mode="HTML")
            return
        game["last"] = time.time()
        game["attempts"] += 1
        secret = game["secret"]
        secret_letters = list(secret)
        res_colors = ["⬛"] * 5
        for i in range(5):
            if guess[i] == secret[i]:
                res_colors[i] = "🟩"
                secret_letters[i] = None
        for i in range(5):
            if res_colors[i] != "🟩" and guess[i] in secret_letters:
                res_colors[i] = "🟨"
                secret_letters[secret_letters.index(guess[i])] = None
        letters_row = "  ".join(list(guess))
        colors_row = " ".join(res_colors)
        game["history"].append(f"<code>{letters_row}</code>\n{colors_row}")
        res_text = "\n\n".join(game["history"])
        end_keyboard = [[InlineKeyboardButton("🔂 Rigioca", callback_data=f"start_wordle_{user.id}"), InlineKeyboardButton("🔙 HUB", callback_data=f"hub_main_{user.id}")] ]
        title = "🔠 <b>WORDLE EXPRESS</b> 🔠"

        if guess == secret:
            del WORDLE_GAMES[game_key]
            reward = WORDLE_REWARDS.get(game["attempts"], WORDLE_REWARDS[5])
            paid, notes = await _settle(context.bot, chat_id_int, user.id, "wordle", 10, reward, is_casino=False)
            qmsg = quest_progress(chat_id_int, user.id, "wordle_win")
            head = game_header(title, user.first_name, get_user_coins(chat_id_int, user.id), 10)
            await update.message.reply_text(
                f"{head}🎉 <b>ESATTO!</b> Parola: <b>{secret}</b>! Vinti +💳 {fmt(paid)} $SDG (netti +{fmt(paid - 10)})!"
                + ("\n" + "\n".join(notes) if notes else "") + (f"\n{qmsg}" if qmsg else "") + f"\n\n{res_text}",
                reply_markup=InlineKeyboardMarkup(end_keyboard), parse_mode="HTML")
        elif game["attempts"] >= 5:
            del WORDLE_GAMES[game_key]
            _, notes = await _settle(context.bot, chat_id_int, user.id, "wordle", 10, 0, is_casino=False)
            head = game_header(title, user.first_name, get_user_coins(chat_id_int, user.id), 10)
            await update.message.reply_text(
                f"{head}💥 <b>GAME OVER!</b> La parola era {secret}." + ("\n" + "\n".join(notes) if notes else "") + f"\n\n{res_text}",
                reply_markup=InlineKeyboardMarkup(end_keyboard), parse_mode="HTML")
        else:
            head = game_header(title, user.first_name, get_user_coins(chat_id_int, user.id), 10)
            await update.message.reply_text(f"{head}<b>Tentativo {game['attempts']}/5</b>\n\n{res_text}", parse_mode="HTML")
        return

    # Auto-Troll
    if FLAGS["troll"] and username_lower in TARGET_MAP:
        if random.random() < 0.85:
            try:
                await context.bot.set_message_reaction(chat_id=chat_id, message_id=update.message.message_id, reaction=TARGET_MAP[username_lower])
            except Exception as e:
                logging.warning(f"Auto-troll: reazione non riuscita: {e}")


# --- REGISTRAZIONE HANDLER ---
def register(app):
    app.add_error_handler(error_handler)
    app.add_handler(TypeHandler(Update, maintenance_gate), group=-2)
    app.add_handler(TypeHandler(Update, block_gate), group=-2)
    app.add_handler(CommandHandler("ws", ws_command))
    app.add_handler(CommandHandler("blocca", block_command))
    app.add_handler(CommandHandler("sblocca", block_command))
    app.add_handler(CallbackQueryHandler(catch_all_callbacks), group=-1)
    app.add_handler(CommandHandler("troll", toggle_troll))
    app.add_handler(CommandHandler("pen", clear_penalties))
    app.add_handler(CommandHandler("resetduello", reset_duello))
    app.add_handler(CommandHandler("dona", donate_coins_command))
    app.add_handler(CommandHandler("manutenzione", toggle_maintenance))
    app.add_handler(CommandHandler("ricarica", ricarica_command))
    app.add_handler(CommandHandler("admin", admin_dashboard))
    app.add_handler(CommandHandler(["com", "comandi"], com_command))
    app.add_handler(CallbackQueryHandler(admin_callback, pattern="^adm_"))


def register_text(app):
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_messages))
