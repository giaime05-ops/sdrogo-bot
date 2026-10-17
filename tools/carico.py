"""Test di carico del server del bot (sola lettura: apre solo la pagina di stato "/").
Uso (dal tuo computer, serve solo Python 3):
    python3 carico.py https://site--sdrogobot--ptv7wttm2w6r.code.run
Fa tre prove da 6 secondi con 5, 20 e 50 collegamenti contemporanei e stampa richieste al secondo e tempi di risposta.
Non usa account né soldi: misura quanto regge il server (rete di Northflank + programma), non la logica dei giochi.
"""
import sys
import threading
import time
import urllib.request

if len(sys.argv) < 2:
    sys.exit("Uso: python3 carico.py https://indirizzo-del-bot")
URL = sys.argv[1].rstrip("/") + "/"
DURATA = 6.0


def prova(n):
    stop = time.time() + DURATA
    tempi, errori, lock = [], [0], threading.Lock()

    def lavora():
        while time.time() < stop:
            t = time.perf_counter()
            try:
                with urllib.request.urlopen(urllib.request.Request(URL, headers={"User-Agent": "carico-test"}), timeout=15) as r:
                    r.read()
                    ok = r.status == 200
            except Exception:
                ok = False
            dt = (time.perf_counter() - t) * 1000
            with lock:
                if ok:
                    tempi.append(dt)
                else:
                    errori[0] += 1

    th = [threading.Thread(target=lavora) for _ in range(n)]
    t0 = time.time()
    for t in th:
        t.start()
    for t in th:
        t.join()
    el = time.time() - t0
    tempi.sort()
    q = lambda p: round(tempi[min(len(tempi) - 1, int(len(tempi) * p))]) if tempi else -1
    print(f"{n:>3} collegamenti: {len(tempi) / el:6.0f} richieste/s | mediana {q(.5)} ms | 95% sotto {q(.95)} ms | peggiore {q(1)} ms | errori {errori[0]}")


print(f"Provo {URL}")
for n in (5, 20, 50):
    prova(n)
    time.sleep(2)
print("Fatto. Con 4-5 giocatori contemporanei basta stare bene nella prima riga.")
