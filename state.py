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

# Giochi sperimentali del Laboratorio Segreto (solo admin, solo privato)
AVIATOR_GAMES = {}     # chiave "chat_user" -> volo in corso
SCRATCH_GAMES = {}     # chiave "chat_user" -> biglietto Gratta e Vinci in corso
LAB_BUSY = set()       # chiavi "chat_user" con una giocata animata in corso (Roulette)

ACTIVE_TITLES = {}
ACTIVE_PERSECUTE = {}
USER_INVENTORIES = {}

FLAGS = {"troll": False, "maintenance": False}

# Interruttori dei singoli giochi (gestiti da /admin). True = acceso, False = in manutenzione.
GAME_STATUS = {
    "wordle": True,
    "blackjack": True,
    "slot": True,
    "mastermind": True,
    "heist": True,
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
