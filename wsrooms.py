"""Canale WebSocket per le corse online (riserva: il sistema HTTP /api/room/sync resta e funziona sempre).

Perché: con HTTP ogni 100 ms il client apre una richiesta nuova (intestazioni, autenticazione, coda) e non può ricevere nulla finché non chiede.
Con una connessione WebSocket sempre aperta i dati viaggiano subito in entrambe le direzioni e le novità degli altri arrivano appena
esistono (push). La LOGICA della stanza è la stessa di HTTP: qui si chiama webrooms.sync_core, quindi nessuna regola è duplicata.

Il server è un piccolo server asyncio (protocollo RFC 6455 scritto qui, nessuna libreria in più) in un thread dello stesso processo,
su una porta propria (WS_PORT, default 8081), perché waitress (porta principale) non parla WebSocket.

Interruttori:
  /ws on | /ws off | /ws stat  (solo admin)        → SYSTEM_FLAGS["WS_ON"], spegnendo si chiudono subito le connessioni e i client tornano a HTTP
  variabile WS_DISABLE=1                              → il server non parte proprio
  variabile WS_URL=wss://....                         → indirizzo pubblico che il client userà (senza non si offre il WebSocket)
"""
import asyncio
import base64
import hashlib
import json
import logging
import os
import struct
import threading
import time

WS_PORT = int(os.environ.get("WS_PORT", "8081") or 8081)
WS_DISABLE = os.environ.get("WS_DISABLE", "") not in ("", "0")
MAX_CONNS = 400
MAX_PER_USER = 3
MAX_FRAME = 4096
IDLE_S = 25
HELLO_S = 6
_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

_wa = None                       # modulo webapi (per autenticazione)
_loop = None
_started = False
_ROOMCONNS = {}                  # id stanza -> insieme di Conn
_ALL = set()
ST = {"opened": 0, "closed": 0, "msgs": 0, "pushes": 0, "rej": 0, "err": 0, "since": 0.0, "last_err": ""}


def url():
    return os.environ.get("WS_URL", "").strip()


def _flag():
    try:
        from state import SYSTEM_FLAGS
        return bool(SYSTEM_FLAGS.get("WS_ON"))
    except Exception:
        return False


def enabled():
    """True se i client possono usare il WebSocket adesso."""
    return bool(_started and _flag() and url())


def set_enabled(on):
    from state import SYSTEM_FLAGS
    SYSTEM_FLAGS["WS_ON"] = bool(on)
    try:
        import storage
        storage.save_db()
    except Exception:
        pass
    if not on:
        close_all("spento dall'admin")


def close_all(why=""):
    lp = _loop
    if not lp:
        return
    def _do():
        for c in list(_ALL):
            c.kill(1001)
    try:
        lp.call_soon_threadsafe(_do)
    except Exception:
        pass


def stat_text():
    up = (time.time() - ST["since"]) if ST["since"] else 0
    return (f"🔌 WebSocket: {'ACCESO' if _flag() else 'spento'} · server {'attivo' if _started else 'NON attivo'} · porta {WS_PORT}\n"
            f"Indirizzo per i client: {url() or '— (manca WS_URL)'}\n"
            f"Collegati ora: {len(_ALL)} in {len(_ROOMCONNS)} stanze · aperte {ST['opened']} · chiuse {ST['closed']}\n"
            f"Messaggi ricevuti {ST['msgs']} · push inviati {ST['pushes']} · rifiutati {ST['rej']} · errori {ST['err']}"
            + (f"\nUltimo errore: {ST['last_err']}" if ST["last_err"] else "")
            + (f"\nAttivo da {int(up // 60)} min" if up else ""))


# ---------------------------------------------------------------------------------------------
class Conn:
    def __init__(self, reader, writer, ip):
        self.r, self.w, self.ip = reader, writer, ip
        self.uid = None
        self.rid = None
        self.dead = False
        self.tok = 60.0
        self.tk = time.monotonic()

    def kill(self, code=1000):
        if self.dead:
            return
        self.dead = True
        try:
            self.w.write(_frame(0x8, struct.pack(">H", code)))
        except Exception:
            pass
        try:
            self.w.close()
        except Exception:
            pass

    def send_json(self, obj, force=False):
        if self.dead:
            return False
        try:
            if not force and self.w.transport.get_write_buffer_size() > 96 * 1024:      # client lento: niente accodamento infinito
                return False
            self.w.write(_frame(0x1, json.dumps(obj, separators=(",", ":")).encode()))
            return True
        except Exception:
            self.dead = True
            return False

    def allow(self):
        now = time.monotonic()
        self.tok = min(60.0, self.tok + (now - self.tk) * 40.0); self.tk = now          # 40 messaggi/s a regime (ne servono ~10)
        if self.tok < 1:
            return False
        self.tok -= 1
        return True


def _frame(op, payload=b""):
    n = len(payload)
    if n < 126:
        h = struct.pack(">BB", 0x80 | op, n)
    elif n < 65536:
        h = struct.pack(">BBH", 0x80 | op, 126, n)
    else:
        h = struct.pack(">BBQ", 0x80 | op, 127, n)
    return h + payload


async def _read_msg(c):
    """Legge un messaggio di testo completo (gestisce ping/pong/chiusura e frammenti). None = connessione da chiudere."""
    buf = b""
    while True:
        h = await asyncio.wait_for(c.r.readexactly(2), IDLE_S)
        fin, op = h[0] & 0x80, h[0] & 0x0F
        masked, ln = h[1] & 0x80, h[1] & 0x7F
        if ln == 126:
            ln = struct.unpack(">H", await c.r.readexactly(2))[0]
        elif ln == 127:
            ln = struct.unpack(">Q", await c.r.readexactly(8))[0]
        if ln > MAX_FRAME or not masked:
            c.kill(1009 if ln > MAX_FRAME else 1002)
            return None
        mk = await c.r.readexactly(4)
        data = bytearray(await c.r.readexactly(ln))
        for i in range(ln):
            data[i] ^= mk[i & 3]
        if op == 0x8:
            c.kill(1000)
            return None
        if op == 0x9:
            c.w.write(_frame(0xA, bytes(data)))
            continue
        if op == 0xA:
            continue
        if op not in (0x0, 0x1):
            c.kill(1003)
            return None
        buf += bytes(data)
        if len(buf) > MAX_FRAME:
            c.kill(1009)
            return None
        if fin:
            return buf


async def _handshake(c):
    head = b""
    while b"\r\n\r\n" not in head:
        chunk = await asyncio.wait_for(c.r.read(2048), HELLO_S)
        if not chunk:
            return False
        head += chunk
        if len(head) > 8192:
            return False
    lines = head.split(b"\r\n\r\n")[0].decode("latin1").split("\r\n")
    hd = {}
    for ln in lines[1:]:
        if ":" in ln:
            k, v = ln.split(":", 1)
            hd[k.strip().lower()] = v.strip()
    if hd.get("upgrade", "").lower() != "websocket" or "sec-websocket-key" not in hd:
        body = b"SdrogoBot WS ok"                                   # controllo di salute / apertura da browser
        c.w.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s" % (len(body), body))
        await c.w.drain()
        return False
    acc = base64.b64encode(hashlib.sha1(hd["sec-websocket-key"].encode() + _GUID).digest()).decode()
    c.w.write(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: %s\r\n\r\n" % acc).encode())
    await c.w.drain()
    return True


def _auth(init):
    """Stesse regole di webapi.require_admin (blocchi, gruppo, manutenzione) senza richiesta Flask. Restituisce uid o None."""
    wa = _wa
    from state import maint_mode, user_block
    user = wa.validate_init_data(init)
    if not user:
        return None
    uid = str(user["id"])
    if not wa._is_admin_id(user["id"]):
        if not wa.arcade_open():
            return None
        if not wa._is_member(user["id"]):
            return None
        if user_block(user["id"]) >= 1 or maint_mode() == "hard":
            return None
    return uid


async def _client(reader, writer):
    ip = (writer.get_extra_info("peername") or ("?",))[0]
    c = Conn(reader, writer, ip)
    ok_hs = False
    try:
        if len(_ALL) >= MAX_CONNS or not _flag():
            ST["rej"] += 1
            return
        if not await _handshake(c):
            return
        ok_hs = True
        _ALL.add(c)
        ST["opened"] += 1
        # primo messaggio: identificazione
        raw = await asyncio.wait_for(_read_msg(c), HELLO_S)
        if raw is None:
            return
        m = json.loads(raw)
        if m.get("t") != "hi":
            c.kill(1008)
            return
        loop = asyncio.get_running_loop()
        uid = await loop.run_in_executor(None, _auth, str(m.get("i", "")))
        rid = str(m.get("id", ""))
        import webrooms
        if not uid or not webrooms.is_member(rid, uid):
            ST["rej"] += 1
            c.send_json({"t": "no", "e": "auth"}, True)
            c.kill(1008)
            return
        if sum(1 for x in _ROOMCONNS.get(rid, ()) if x.uid == uid) >= MAX_PER_USER:
            for x in [x for x in _ROOMCONNS.get(rid, ()) if x.uid == uid][:1]:
                x.kill(1000)                                   # la connessione più vecchia cede il posto (ricarica pagina, cambio rete)
        c.uid, c.rid = uid, rid
        _ROOMCONNS.setdefault(rid, set()).add(c)
        try:
            _wa.mark_seen(uid)
        except Exception:
            pass
        c.send_json({"t": "ok", "st": round(time.time(), 3)}, True)
        while not c.dead:
            raw = await _read_msg(c)
            if raw is None:
                break
            if not _flag():
                c.send_json({"t": "no", "e": "off"}, True)
                c.kill(1001)
                break
            ST["msgs"] += 1
            if not c.allow():
                continue
            try:
                m = json.loads(raw)
            except ValueError:
                continue
            if m.get("t") == "s":
                code, out, push = webrooms.sync_core(uid, {"id": rid, "since": m.get("since", 0), "ev": m.get("ev")})
                if code != 200:
                    c.send_json({"t": "r", "success": False, "error": out, "q": m.get("q")})
                    continue
                out["t"] = "r"; out["q"] = m.get("q")
                c.send_json(out, True)
                if push:
                    notify(rid, uid, push)
            elif m.get("t") == "ping":
                c.send_json({"t": "pong", "q": m.get("q"), "st": round(time.time(), 3)}, True)
    except (asyncio.IncompleteReadError, asyncio.TimeoutError, ConnectionError, OSError):
        pass
    except Exception as e:
        ST["err"] += 1
        ST["last_err"] = f"{type(e).__name__}: {e}"[:160]
        logging.warning(f"WebSocket: errore connessione: {e}")
    finally:
        if ok_hs:
            ST["closed"] += 1
        _ALL.discard(c)
        if c.rid:
            s = _ROOMCONNS.get(c.rid)
            if s:
                s.discard(c)
                if not s:
                    _ROOMCONNS.pop(c.rid, None)
        c.dead = True
        try:
            writer.close()
        except Exception:
            pass


def _push_now(rid, from_uid, push):
    import webrooms
    for c in list(_ROOMCONNS.get(rid, ())):
        if c.uid == from_uid or c.dead:
            continue
        try:
            if push.get("special"):
                v = webrooms.view_for(rid, c.uid)
                msg = {"t": "p", "n": push["n"], "from": push["from"], "events": [], "room": v}
                evs = [e for e in push["evs"] if "to" not in e or c.uid in e["to"]]
                msg["events"] = evs
            else:
                evs = [e for e in push["evs"] if "to" not in e or c.uid in e["to"]]
                if not evs:
                    continue
                msg = {"t": "p", "n": push["n"], "from": push["from"], "events": evs}
            if c.send_json(msg):
                ST["pushes"] += 1
        except Exception:
            ST["err"] += 1


def notify(rid, from_uid, push):
    """Spinge subito la novità a chi è collegato col WebSocket nella stanza (chiamabile da qualunque thread)."""
    if not _started or not _ROOMCONNS.get(rid):
        return
    try:
        _loop.call_soon_threadsafe(_push_now, rid, from_uid, push)
    except Exception:
        pass


# ---------------------------------------------------------------------------------------------
def start(wa):
    """Avvia il server in un thread (non blocca, non lancia mai eccezioni: se la porta non si apre, resta tutto su HTTP)."""
    global _wa, _loop, _started
    _wa = wa
    if WS_DISABLE:
        print("ℹ️ WebSocket: disattivato (WS_DISABLE).", flush=True)
        return

    def run():
        global _loop, _started
        try:
            lp = asyncio.new_event_loop()
            asyncio.set_event_loop(lp)
            srv = lp.run_until_complete(asyncio.start_server(_client, "0.0.0.0", WS_PORT, limit=16384))
            _loop = lp
            _started = True
            ST["since"] = time.time()
            print(f"🔌 WebSocket: in ascolto sulla porta {WS_PORT} (acceso: {_flag()}, WS_URL: {url() or 'non impostato'})", flush=True)
            lp.run_forever()
        except Exception as e:
            _started = False
            ST["last_err"] = f"{type(e).__name__}: {e}"[:160]
            print(f"⚠️ WebSocket non avviato ({e}): le corse restano su HTTP.", flush=True)

    threading.Thread(target=run, daemon=True, name="ws-rooms").start()
