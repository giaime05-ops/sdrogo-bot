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
    app.run(host='0.0.0.0', port=PORT)

def start_flask():
    Thread(target=run_flask, daemon=True).start()
