import logging
from threading import Thread
from flask import Flask
from config import PORT

app = Flask(__name__)

@app.route('/')
def home():
    return "SdrogoBot v5.2 Attivo H24!"

def run_flask():
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    try:
        from waitress import serve                     # server vero (a thread fissi): regge molte richieste insieme
    except ImportError:                                # se il pacchetto manca si torna al server di sviluppo di Flask
        print("ATTENZIONE: waitress non installato, uso il server di sviluppo di Flask", flush=True)
        app.run(host='0.0.0.0', port=PORT)
        return
    logging.getLogger('waitress').setLevel(logging.ERROR)
    print(f"Server web: waitress (16 thread) sulla porta {PORT}", flush=True)
    serve(app, host='0.0.0.0', port=PORT, threads=16, max_request_body_size=65536)

def start_flask():
    Thread(target=run_flask, daemon=True).start()
