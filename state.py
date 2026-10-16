USER_DATA = {}

ACTIVE_DUELS = {}
HIGHLOW_DUELS = {}
BLACKJACK_GAMES = {}
WORDLE_GAMES = {}
MASTERMIND_GAMES = {}
QUIZ_GAMES = {}
QUIZ_DUELS_1V1 = {}
GHIGLIOTTINA_DUELS = {}
HEIST_GAMES = {}
PENITENZE_ATTIVE = {}
TIC_TAC_TOE_GAMES = {}
DICE_DUELS = {}
DAILY_DONATIONS = set()

# Giochi single player animati / a sessione
AVIATOR_GAMES = {}     # chiave "chat_user" -> volo in corso
SCRATCH_GAMES = {}     # chiave "chat_user" -> biglietto Gratta e Vinci in corso
LAB_BUSY = set()       # chiavi "chat_user" con una giocata animata in corso (Roulette singola)
POKER_GAMES = {}       # chiave "chat_user" -> mano di Sdrogo Poker in corso

# --- V2: stati temporanei (in memoria) ---
WORK_GAMES = {}        # user_id -> enigma di /lavora in corso
PENDING_INPUT = {}     # user_id -> input atteso in privato (es. frase umiliante per "silenzia")
SILENCED = {}          # chiave "chat_user" -> {"until", "left", "phrase"}
REACT_TARGETS = {}     # chiave "chat_user" -> {"until", "emoji"}
MUTE_FILTER = {}       # chiave "chat_user" -> timestamp di fine del filtro muto

ACTIVE_TITLES = {}
ACTIVE_PERSECUTE = {}
USER_INVENTORIES = {}

FLAGS = {"troll": False, "maintenance": False}

# --- V2: stati PERSISTENTI (salvati in database.json da storage.py) ---
SYSTEM_FLAGS = {"UPDATE_V2_PUBLIC": False}

# BANCO_BALANCE: $SDG persi dai giocatori nei giochi single player. FOLLIA_ATTIVA: evento "Follia Sdrogo" in corso.
# FOLLIA_CONSUMED: $SDG pagati dal Banco durante la Follia in corso. FOLLIA_RESET: la Follia è appena finita e non può
# ripartire finché il Banco non scende sotto la soglia di attivazione e la raggiunge di nuovo.
BANCO_STATE = {"BANCO_BALANCE": 0, "FOLLIA_ATTIVA": False, "FOLLIA_CONSUMED": 0, "FOLLIA_END": 0, "FOLLIA_RESET": False}

# Sfida di gruppo (quota e durata scelte dall'admin; i rinnovi automatici durano fino al giovedì a mezzanotte). participants: {user_id (str): $SDG spesi}
GROUP_CHALLENGE = {
    "active": False,
    "goal": 5000,
    "start": 0.0,
    "end": 0.0,
    "spent": 0,
    "participants": {},
    "msg_id": None,       # message_id del messaggio fissato
    "chat_id": None,
    "next_start": 0.0,    # quando parte il ciclo successivo (0 = nessuno programmato)
    "cfg_goal": 5000, "cfg_days": 1,     # ultima quota scelta dall'admin: la usano anche i rinnovi automatici del giovedì
}

# Interruttori dei singoli giochi (gestiti da /admin). True = acceso, False = in manutenzione.
GAME_STATUS = {
    "wordle": True,
    "blackjack": True,
    "slot": True,
    "mastermind": True,
    "heist": True,
    "aviator": True,
    "roulette": True,      # Sdrogo Roulette single player (diversa da "roulette1v1")
    "scratch": True,
    "poker": True,
    "lavora": True,
    "dice": True,
    "ttt": True,
    "roulette1v1": True,
    "highlow": True,
    "ghig": True,
    "quiz1v1": True,
    "quizmulti": True,
}

MAINT_GAME_TEXT = "🚧 Gioco temporaneamente in manutenzione!"


def game_enabled(name: str) -> bool:
    """True se il gioco è acceso (un gioco sconosciuto è considerato acceso)."""
    return GAME_STATUS.get(name, True)
