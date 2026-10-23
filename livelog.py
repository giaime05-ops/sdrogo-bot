"""Registro delle ultime azioni dei giocatori nella mini app (per il comando admin /live). Solo in memoria: si azzera al riavvio."""
import time
from collections import deque

_LOG = deque(maxlen=800)
_SKIP = ("/api/me", "/api/social/", "/api/shop", "/api/wheel/state", "/api/health")


def note(uid, method: str, path: str, sandbox: bool = False):
    if any(path.startswith(s) for s in _SKIP) and method == "GET":
        return
    _LOG.append((time.time(), str(uid), method, path, sandbox))


def recent(n: int = 25, uid=None, name_lookup=None):
    rows = [r for r in _LOG if (uid is None or r[1] == str(uid))]
    return rows[-n:]


def label(path: str) -> str:
    p = path.replace("/api/", "").strip("/")
    m = {"heist/start": "avvia Sdrogo Heist", "heist/act": "mossa nello Sdrogo Heist", "mini/start": "avvia Daily Heist",
         "mini/act": "mossa nel Daily Heist", "extra/work_start": "inizia Lavora", "extra/work_answer": "risponde a Lavora",
         "extra/loan_take": "chiede un prestito", "extra/loan_repay": "salda il prestito", "extra/donate": "dona $SDG"}
    return m.get(p, p)
