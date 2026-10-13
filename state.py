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
# Giochi single player animati / a sessione (Aviator, Gratta e Vinci, Roulette singola)
AVIATOR_GAMES = {}     # chiave "chat_user" -> volo in corso
SCRATCH_GAMES = {}     # chiave "chat_user" -> biglietto Gratta e Vinci in corso
LAB_BUSY = set()       # chiavi "chat_user" con una giocata animata in corso (Roulette singola)

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
    "aviator": True,
    "roulette": True,      # Sdrogo Roulette single player (diversa da "roulette1v1")
    "scratch": True,
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
