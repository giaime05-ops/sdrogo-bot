import os
import json
import random
import asyncio
import logging
from threading import Thread
from datetime import datetime, date, timedelta
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, 
    CommandHandler, 
    MessageHandler, 
    CallbackQueryHandler, 
    filters, 
    ContextTypes
)

# --- CONFIGURAZIONE LOGGING ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# --- CONFIGURAZIONE AMBIENTE ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ADMIN_ID = os.environ.get("ADMIN_ID")
GROUP_CHAT_ID = os.environ.get("GROUP_CHAT_ID")
BACKUP_CHAT_ID = os.environ.get("BACKUP_CHAT_ID")

DB_FILE = "database.json"
USER_DATA = {}

# STATI GLOBALI GIOCHI E SHOP
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

# SHOP & PERSECUZIONI
ACTIVE_TITLES = {}      # {chat_id_userid: {"title": "🏳️‍🌈GAY🏳️‍🌈", "expire": datetime}}
ACTIVE_PERSECUTE = {}   # {chat_id_username: {"count": 15, "phrase": "frocio hah"}}
USER_INVENTORIES = {}  # {chat_id_userid: {"titles": 0, "persecutes": 0, "stars": 0}}

TARGET_MAP = {
    "manueiii": "🙉", "spoleto17": "🤡", "artemesio": "💩",
    "marco_palestra": "🖕", "albe960": "🥱", "alessioaynonnt": "🐳"
}

IS_TROLLING_ACTIVE = True
FRASE_PENITENZA = "sono un perdente"

# --- DATABASE 100 PAROLE WORDLE (5 LETTERE) ---
WORDS = [
    "ZAINO", "AMORE", "CANTO", "FIORE", "GATTO", "LATTE", "NOTTE", "PALMA", "SEDIA", "TAZZA",
    "VENTO", "BACIO", "CUORE", "DOLCE", "FIUME", "GIOCO", "LIBRO", "MATTO", "NUOTO", "PORTO",
    "ROSSO", "TIGRE", "TERRA", "VERDE", "AMICO", "BARCA", "CALDO", "DENTI", "FESTA", "ISOLA",
    "MUSEO", "PALLA", "RUOTA", "TRENO", "VALLE", "ACUME", "BOATO", "CIFRA", "DARDO", "ETICA",
    "FALDA", "GELSO", "ICONA", "LARGO", "MANTO", "NINFA", "OVALE", "PIUMA", "ROCCA", "SUONO",
    "TARLO", "VALLO", "ZOLLA", "ABETE", "BORGO", "CREPA", "DOGMA", "ELICA", "FOSSA", "GRANA",
    "LAMPO", "MAREA", "NAFTA", "ORAFO", "PRATO", "QUOTA", "SFERA", "TRONO", "VARCO", "ZANNA",
    "BUSTA", "CRASI", "ESITO", "FATUO", "ALICE", "LAICO", "MITRA", "NENIA", "ROSPO", "PROCI",
    "FALCO", "ZUPPA", "ALATO", "BIZZA", "DOTTO", "ETERE", "FALCE", "IRIDE", "LESTO", "AUREO",
    "ANICE", "CONCA", "EBREO", "PONTE", "LISCA", "TROIA", "KAYAK", "OVALE", "PUNTO", "TURBE"
]

# --- DATABASE 30 GHIGLIOTTINE (PAROLE LEGAME) ---
GHIGLIOTTINA_DB = [
    {"target": "PORTA", "indizi": ["CASA", "CALCIO", "MARE", "SOLE", "APERTO"]},
    {"target": "LUNA", "indizi": ["PIENA", "PARCO", "MIELE", "MORTA", "STORTA"]},
    {"target": "PANE", "indizi": ["CASERECCIO", "FRESCO", "CESTO", "BURRO", "QUOTIDIANO"]},
    {"target": "FILM", "indizi": ["CINEMA", "CAMPIONE", "ORROR", "PREMIO", "MUTI"]},
    {"target": "SOLE", "indizi": ["RAGGIO", "SCALDATO", "OCCHIALI", "ESTATE", "COLPO"]},
    {"target": "CAFFE", "indizi": ["MOKA", "ESPRESSO", "PAUSA", "TORTA", "MACCHIATO"]},
    {"target": "CARTA", "indizi": ["BANCARIA", "MUSICA", "STRACCIA", "REGALO", "FORMATO"]},
    {"target": "PESCE", "indizi": ["FRESCO", "GATTO", "SPADA", "ROSSO", "MERCATO"]},
    {"target": "GATTO", "indizi": ["NERO", "TOPO", "STIVALI", "MIAO", "SIAMESE"]},
    {"target": "FIORE", "indizi": ["ALL'OCCHIELLO", "CAMPO", "FRESCO", "VASO", "SPOSA"]},
    {"target": "LIBRO", "indizi": ["CUORE", "APERTO", "TESTO", "GIALLO", "PAGINA"]},
    {"target": "STELLA", "indizi": ["CADENTE", "POLARE", "MARINA", "NOTTE", "CINEMA"]},
    {"target": "ACQUA", "indizi": ["SANTA", "MINERALE", "RUBINETTO", "VITA", "CORRENTE"]},
    {"target": "TERRA", "indizi": ["PROMESSA", "PADRE", "CONFINE", "FIRMA", "PIANETA"]},
    {"target": "FUOCO", "indizi": ["CAMINO", "ARTIFICIO", "AMICO", "LENTO", "AMORE"]},
    {"target": "MANO", "indizi": ["AMICA", "SINISTRA", "LIBERA", "PRESA", "FATTA"]},
    {"target": "OCCHIO", "indizi": ["RIGUARDO", "NUDO", "LUCIDO", "CIGLIA", "PALLA"]},
    {"target": "TESTA", "indizi": ["CODA", "CALDO", "SERIE", "CASCO", "AMARA"]},
    {"target": "PIEDE", "indizi": ["SCALZO", "PIATTO", "LIBERO", "LETTERA", "PASSO"]},
    {"target": "CAPELLO", "indizi": ["RICCIO", "PETTINE", "TAGLIO", "BIONDO", "LUNGO"]},
    {"target": "TEMPO", "indizi": ["PERSO", "BELLO", "LIBERO", "REALE", "SOSPEDO"]},
    {"target": "NOTTE", "indizi": ["FOLLIA", "A FUMETTI", "FONDO", "BUONA", "STELLATA"]},
    {"target": "CAMPO", "indizi": ["GIOCO", "ESTIVO", "CENTRO", "FIORI", "SANTO"]},
    {"target": "GIOCO", "indizi": ["SQUADRA", "SALA", "AZZARDO", "TAVOLO", "RUOTA"]},
    {"target": "VITA", "indizi": ["DURA", "PRIVATA", "CARRERA", "GIRATA", "CASA"]},
    {"target": "STRADA", "indizi": ["MAESTRA", "CHIUSA", "FERRATA", "CASA", "LIBERA"]},
    {"target": "CASA", "indizi": ["COLONICA", "DOLCE", "CURA", "PENSIONE", "RIPOSO"]},
    {"target": "MONDO", "indizi": ["NOTTE", "UOMO", "NUOVO", "GIRO", "MADRE"]},
    {"target": "AMORE", "indizi": ["PRIMO", "ETERNO", "PROPRIO", "FALSO", "CIECO"]},
    {"target": "STORIA", "indizi": ["AMORE", "PASSATO", "SACRA", "INFINITA", "LIBRO"]}
]

# --- DATABASE 100 CALCIATORI ---
QUIZ_CALCIO_DB = [
    {"target": "TOTTI", "indizi": ["Italia", "Roma", "Un pallonetto al re di coppe in una notte di gala."]},
    {"target": "MESSI", "indizi": ["Argentina", "Barcellona, Inter Miami", "Un sinistro invisibile cresciuto a suon di ormoni."]},
    {"target": "RONALDO", "indizi": ["Portogallo", "Real Madrid, Manchester United", "Rincorsa a gambe aperte prima di un colpo di testa in cielo."]},
    {"target": "MARADONA", "indizi": ["Argentina", "Napoli, Boca Juniors", "Sette avversari saltati prima della gloria iridata."]},
    {"target": "PELE", "indizi": ["Brasile", "Santos, NY Cosmos", "Tre volte sul trono del mondo con la maglia oro."]},
    {"target": "ZIDANE", "indizi": ["Francia", "Juventus, Real Madrid", "Una ruota di classe prima di un colpo di testa sbagliato."]},
    {"target": "BAGGIO", "indizi": ["Italia", "Juventus, Brescia", "L'ultimo rigore verso il cielo sotto il sole americano."]},
    {"target": "CRUIJFF", "indizi": ["Olanda", "Ajax, Barcellona", "Un numero quattordici che ha rivoluzionato il movimento."]},
    {"target": "MBAPPE", "indizi": ["Francia", "PSG, Real Madrid", "Scatto bruciante e tripletta amara in una finale mondiale."]},
    {"target": "MODRIC", "indizi": ["Croazia", "Real Madrid, Dinamo Zagabria", "L'esterno destro che fa girare la testa ai Giganti."]},
    {"target": "RONALDO NAZARIO", "indizi": ["Brasile", "Inter, Real Madrid", "Dribbling sul portiere al limite dell'umano prima del ginocchio di cristallo."]},
    {"target": "BUFFON", "indizi": ["Italia", "Juventus, Parma", "Vent'anni a guidare la difesa senza mai alzare la Champions."]},
    {"target": "MALDINI", "indizi": ["Italia", "Milan", "Fascia al braccio e chiusure perfette in due generazioni rosse e nere."]},
    {"target": "RONALDINHO", "indizi": ["Brasile", "Barcellona, Milan", "Sorriso contagioso e applausi al Bernabéu da avversario."]},
    {"target": "VAN BASTEN", "indizi": ["Olanda", "Milan, Ajax", "Volée impossibile da posizione defilata nell'88."]},
    {"target": "KAKA", "indizi": ["Brasile", "Milan, Real Madrid", "Falcata elegante tra due difensori ad Old Trafford."]},
    {"target": "HENRY", "indizi": ["Francia", "Arsenal, Barcellona", "Interno a giro sul secondo palo nell'anno degli imbattibili."]},
    {"target": "NEUER", "indizi": ["Germania", "Bayern Monaco, Schalke 04", "Uscite di testa a 30 metri dalla porta."]},
    {"target": "HAALAND", "indizi": ["Norvegia", "Manchester City, Borussia Dortmund", "Forza della natura che devasta le difese d'Inghilterra."]},
    {"target": "SHEVCHENKO", "indizi": ["Ucraina", "Milan, Dinamo Kiev", "Lo sguardo fisso su Buffon prima del rigore decisivo a Manchester."]},
    {"target": "GERRARD", "indizi": ["Inghilterra", "Liverpool", "La colpo di testa che diede il via alla notte di Istanbul."]},
    {"target": "PIRLO", "indizi": ["Italia", "Milan, Juventus", "Passaggi millimetrici senza un capello fuori posto."]},
    {"target": "XAVI", "indizi": ["Spagna", "Barcellona", "Il compasso umano al centro del rondo infinito."]},
    {"target": "INIESTA", "indizi": ["Spagna", "Barcellona", "Un tiro al volo al minuto 116 per dedicare un gol a un amico scomparso."]},
    {"target": "MATTHAUS", "indizi": ["Germania", "Inter, Bayern Monaco", "Tiro potente da fuori e cinque edizioni iridate disputate."]},
    {"target": "BATISTUTA", "indizi": ["Argentina", "Fiorentina, Roma", "Esultanza alla bandierina con la mitragliatrice."]},
    {"target": "DEL PIERO", "indizi": ["Italia", "Juventus", "Un tiro spinto a giro nell'incrocio lontano."]},
    {"target": "BECKHAM", "indizi": ["Inghilterra", "Manchester United, Real Madrid", "Cross disegnati col righello e punizioni all'ultimo secondo."]},
    {"target": "CASILLAS", "indizi": ["Spagna", "Real Madrid", "Il piede galeotto ad ipnotizzare Robben a Johannesburg."]},
    {"target": "NEYMAR", "indizi": ["Brasile", "Santos, PSG", "Creste appariscenti e la remuntada orchestrata contro la sua futura squadra."]},
    {"target": "DE BRUYNE", "indizi": ["Belgio", "Manchester City, Wolfsburg", "Traccianti radenti che spiazzano le difese d'Europa."]},
    {"target": "ROMARIO", "indizi": ["Brasile", "PSV, Barcellona", "Punta di piede fulminea nello stretto dell'area di rigore."]},
    {"target": "GULLIT", "indizi": ["Olanda", "Milan, PSV", "Atletismo devastante guidato da una chioma inconfondibile."]},
    {"target": "WEAH", "indizi": ["Liberia", "Milan, PSG", "Una corsa solitaria di ottanta metri da area ad area."]},
    {"target": "NEDVED", "indizi": ["Repubblica Ceca", "Lazio, Juventus", "Biondi capelli al vento e una squalifica amara prima della finale."]},
    {"target": "NAKATA", "indizi": ["Giappone", "Perugia, Roma", "Subentrò al capitano per spaccare la sfida scudetto a Torino."]},
    {"target": "JUNINHO", "indizi": ["Brasile", "Lione", "Traiettorie della sfera che cambiavano direzione a mezz'aria."]},
    {"target": "STANKOVIC", "indizi": ["Serbia", "Lazio, Inter", "Gol al volo direttamente su rinvio del portiere da centrocampo."]},
    {"target": "OKOCHA", "indizi": ["Nigeria", "Bolton, PSG", "Feinte di corpo capaci di mandare al bar i difensori d'Inghilterra."]},
    {"target": "LARSSON", "indizi": ["Svezia", "Celtic, Barcellona", "Subentrò a Parigi per ribaltare la finale continentale con due assist."]},
    {"target": "KLOSE", "indizi": ["Germania", "Lazio, Werder Brema", "Capriola volante e sedici reti nella competizione più importante."]},
    {"target": "FORLAN", "indizi": ["Uruguay", "Villarreal, Atlético Madrid", "Capocannoniere in Sudafrica grazie a traiettorie pazze della sfera."]},
    {"target": "RECOBA", "indizi": ["Uruguay", "Inter, Venezia", "Debutto da incanto oscurando la prima gara del Fenomeno."]},
    {"target": "DI NATALE", "indizi": ["Italia", "Udinese, Empoli", "Reti a raffica rifiutando le grandi metropoli per restare in provincia."]},
    {"target": "BALE", "indizi": ["Galles", "Tottenham, Real Madrid", "Rovesciata spettacolare a Kiev e volate all'esterno del campo."]},
    {"target": "FALCAO", "indizi": ["Colombia", "Porto, Atlético Madrid", "Movimenti e stacchi aerei fulminei nell'area di rigore."]},
    {"target": "MIHAJLOVIC", "indizi": ["Serbia", "Lazio, Sampdoria", "Tre calci da fermo vincenti nella stessa partita contro il suo passato."]},
    {"target": "VARDY", "indizi": ["Inghilterra", "Leicester City", "Dalla quinta serie al titolo d'Inghilterra segnando per undici gare di fila."]},
    {"target": "HAMSIK", "indizi": ["Slovacchia", "Napoli", "Cresta azzurra e record di presenze all'ombra del Vesuvio."]},
    {"target": "RIQUELME", "indizi": ["Argentina", "Boca Juniors, Villarreal", "Pasos lentos ma geniali, fino a quel rigore parato a Londra."]},
    {"target": "SEEDORF", "indizi": ["Olanda", "Ajax, Milan", "Unico a sollevare la coppa dalle grandi orecchie con tre maglie differenti."]},
    {"target": "RIBERY", "indizi": ["Francia", "Bayern Monaco, Fiorentina", "Dribbling ubriacanti in coppia con un olandese sulla fascia opposta."]},
    {"target": "ZOLA", "indizi": ["Italia", "Chelsea, Parma", "Colpo di tacco al volo volante sotto il cielo di Londra."]},
    {"target": "CAZORLA", "indizi": ["Spagna", "Arsenal, Villarreal", "Batteva i calci piazzati indistintamente con entrambi i piedi."]},
    {"target": "MANDZUKIC", "indizi": ["Croazia", "Juventus, Bayern Monaco", "Esterno d'attacco roccioso autore di una rovesciata in una finale persa."]},
    {"target": "HAGI", "indizi": ["Romania", "Steaua Bucarest, Galatasaray", "Mancino vellutato che portò la sua nazione ai quarti Usa '94."]},
    {"target": "SUKER", "indizi": ["Croazia", "Real Madrid, Siviglia", "Scarpa d'oro con sei sigilli che portò la sua patria sul podio mondiale."]},
    {"target": "CAVANI", "indizi": ["Uruguay", "Napoli, PSG", "Corsa inesauribile a coprire l'intera mediana prima di colpire in area."]},
    {"target": "AIMAR", "indizi": ["Argentina", "Valencia, River Plate", "Fantasista tascabile a cui si ispirava da ragazzino la pulce rosarina."]},
    {"target": "ESSIEN", "indizi": ["Ghana", "Chelsea, Lione", "Diga insuperabile a centrocampo autore di un bolide contro il Barcellona."]},
    {"target": "KANU", "indizi": ["Nigeria", "Ajax, Arsenal", "Altissimo e ciondolante, superò problemi cardiaci per vincere in Premier."]},
    {"target": "BERBATOV", "indizi": ["Bulgaria", "Manchester United, Bayer Leverkusen", "Agganci di palla così morbidi da far sembrare facile ogni pallone alto."]},
    {"target": "VALERON", "indizi": ["Spagna", "Deportivo La Coruña", "Cervello fino e passaggi illuminanti nella favola del 'Super Depor'."]},
    {"target": "KRASIC", "indizi": ["Serbia", "CSKA Mosca, Juventus", "Pochi mesi da stella sulla fascia bionda prima di sparire dai radar."]},
    {"target": "NAKAMURA", "indizi": ["Giappone", "Celtic, Reggina", "Punizione telecomandata sotto l'incrocio contro il Manchester United."]},
    {"target": "QUAGLIARELLA", "indizi": ["Italia", "Sampdoria, Udinese", "Reti fuori da ogni logica balistica, spesso di tacco o da centrocampo."]},
    {"target": "NANI", "indizi": ["Portogallo", "Manchester United, Sporting CP", "Capriole acrobatiche per festeggiare giocate all'ombra del connazionale più famoso."]},
    {"target": "ILICIC", "indizi": ["Slovenia", "Atalanta, Palermo", "Poker magico in trasferta in Champions prima che les ombre oscurassero il suo talento."]},
    {"target": "NAVAS", "indizi": ["Costa Rica", "Real Madrid, PSG", "Reattività prodigiosa tra i pali delle tre coppe europee consecutive."]},
    {"target": "ALEX", "indizi": ["Brasile", "Fenerbahçe, Coritiba", "Idolo assoluto a Istanbul, tanto da meritarsi una statua al parco."]},
    {"target": "HUBNER", "indizi": ["Italia", "Piacenza, Brescia", "Capocannoniere in massima serie alternando gol e sigarette in panchina."]},
    {"target": "JARDEL", "indizi": ["Brasile", "Porto, Sporting CP", "Media gol spaventosa colpita quasi tutta di testa in Portogallo."]},
    {"target": "MENDIETA", "indizi": ["Spagna", "Valencia, Lazio", "Due finali perse da capitano prima del passaggio a vuoto nella capitale italiana."]},
    {"target": "PEDERSEN", "indizi": ["Norvegia", "Blackburn Rovers", "Mancino potente ed estetico nell'Inghilterra di metà anni duemila."]},
    {"target": "MORENO", "indizi": ["Spagna", "Alavés, Milan", "Trascinò una cenerentola fino a un folle 5-4 nella finale di Dortmund."]},
    {"target": "MICHU", "indizi": ["Spagna", "Swansea, Rayo Vallecano", "Un'unica annata da fenomeno assoluto con la mano all'orecchio in Galles."]},
    {"target": "CALAIO", "indizi": ["Italia", "Siena, Napoli", "Esultanza scoccando la freccia verso la curva."]},
    {"target": "BEN ARFA", "indizi": ["Francia", "Nizza, Newcastle", "Dribblava l'intera squadra avversaria ma litigava con ogni allenatore."]},
    {"target": "LUALUA", "indizi": ["RD del Congo", "Portsmouth, Newcastle", "Serie interminabile di salti mortali dopo ogni marcatura."]},
    {"target": "MICCOLI", "indizi": ["Italia", "Palermo, Lecce", "Estro e gol da antologia per il folletto del Barbera."]},
    {"target": "TSHABALALA", "indizi": ["Sudafrica", "Kaizer Chiefs", "Il mancino all'incrocio che fece esplodere les vuvuzelas nella gara d'apertura 2010."]},
    {"target": "MACCARONE", "indizi": ["Italia", "Middlesbrough, Empoli", "Gol allo scadere in semifinale europea per portare gli inglesi in finale."]},
    {"target": "TAARABT", "indizi": ["Marocco", "QPR, Milan", "Tunnel sfacciati e giocolerie nella seconda serie inglese."]},
    {"target": "PROTTI", "indizi": ["Italia", "Bari, Livorno", "Capocannoniere nella massima serie con una squadra poi retrocessa."]},
    {"target": "ABREU", "indizi": ["Uruguay", "Botafogo, Defensor", "Il pallonetto su rigore ai mondiali per fare onore al suo soprannome 'El Loco'."]},
    {"target": "MASCARA", "indizi": ["Italia", "Catania", "Pallonetto al volo da 50 metri nel derby siciliano."]},
    {"target": "KIRALY", "indizi": ["Ungheria", "Hertha Berlino, Fulham", "Famoso per i pantaloni grigi della tuta larghi e fuori moda."]},
    {"target": "GIACOMAZZI", "indizi": ["Uruguay", "Lecce", "Capitano e pilastro del centrocampo salentino per oltre un decennio."]},
    {"target": "CASTILLO", "indizi": ["Messico", "Olympiacos, Shakhtar", "Sombrero e gol capolavoro contro il Brasile nella Copa 2007."]},
    {"target": "DI NAPOLI", "indizi": ["Italia", "Messina, Salernitana", "Detto 'Reginella', re dei gol nelle piazze del Sud Italia."]},
    {"target": "SAU", "indizi": ["Italia", "Cagliari, Juve Stabia", "Velocità e scatti fulminei per il 'pattino' sardo."]},
    {"target": "RUIZ", "indizi": ["Costa Rica", "Twente, Sporting CP", "Suo il colpo di testa che condannò gli azzurri nel Mondiale brasiliano."]},
    {"target": "FLO", "indizi": ["Norvegia", "Chelsea, Siena", "Spilungone nordico spietato nei minuti finali in Inghilterra e in Italia."]},
    {"target": "SONG", "indizi": ["Camerun", "Metz, Galatasaray", "Difensore carismatico dai treccine colorate e quattro Mondiali disputati."]},
    {"target": "DRENTHE", "indizi": ["Olanda", "Real Madrid, Hercules", "Inserito tra i giovani più promettenti al mondo, finì presto a fare il rapper."]},
    {"target": "PELLISSIER", "indizi": ["Italia", "Chievo Verona", "Bandiera e capitano storico della favola della frazione veronese."]},
    {"target": "GEOVANNI", "indizi": ["Brasile", "Hull City, Benfica", "Gol da trenta metri che regalarono vittorie storiche a una neopromossa inglese."]},
    {"target": "GOMIS", "indizi": ["Francia", "Lione, Swansea", "Camminata a quattro zampe ruggendo verso le telecamere."]},
    {"target": "AL GHANAM", "indizi": ["Arabia Saudita", "Al-Nassr", "Terzino destro che serve assist al cinque volte pallone d'oro in Medio Oriente."]},
    {"target": "SCARONE", "indizi": ["Uruguay", "Nacional", "Campione olimpico negli anni venti soprannominato 'El Mago'."]}
]

# --- DATABASE 100 FILM ---
QUIZ_CINEMA_DB = [
    {"target": "PULP FICTION", "indizi": ["Crime / Pulp", "Valigetta brillante", "Un ballo a piedi nudi e una siringa d'adrenalina."]},
    {"target": "INCEPTION", "indizi": ["Sci-Fi / Azione", "Trottola", "Costruire architetture dentro l'inconscio altrui."]},
    {"target": "MATRIX", "indizi": ["Fantascienza", "Pillola rossa", "Riconoscere la realtà dietro il codice verde."]},
    {"target": "IL CAVALIERE OSCURO", "indizi": ["Cinecomic / Thriller", "Trucco da pagliaccio", "Portare il caos per mettere alla prova la città."]},
    {"target": "FIGHT CLUB", "indizi": ["Drammatico / Thriller", "Sapone", "Creare un doppio per sfogare la frustrazione moderna."]},
    {"target": "INTERSTELLAR", "indizi": ["Spazio / Sci-Fi", "Buco nero", "Il tempo che scorre diversamente sul pianeta d'acqua."]},
    {"target": "SHUTTER ISLAND", "indizi": ["Thriller / Mistero", "Manicomio isolato", "Indagare sulla scomparsa fino a dubitare della propria mente."]},
    {"target": "DJANGO UNCHAINED", "indizi": ["Western / Revenge", "Tagliagole tedesco", "Un ex schiavo caccia i piantatori del sud."]},
    {"target": "THE WOLF OF WALL STREET", "indizi": ["Biografico / Commedia", "Azioni Penny", "Eccessi e truffe nel tempio della finanza."]},
    {"target": "BASTARDI SENZA GLORIA", "indizi": ["Guerra / Grottesco", "Scalpi", "Un cinema parigino per riscrivere la storia mondiale."]},
    {"target": "RITORNO AL FUTURO", "indizi": ["Avventura / Sci-Fi", "Auto d'epoca", "Viaggiare a 88 miglia orarie nel 1955."]},
    {"target": "JURASSIC PARK", "indizi": ["Avventura / Sci-Fi", "Ambra fossile", "Un parco a tema dove la natura riprende il controllo."]},
    {"target": "IL GLADIATORE", "indizi": ["Storico / Azione", "Arena", "Un generale tradito risale la china come schiavo."]},
    {"target": "TITANIC", "indizi": ["Romantico / Drammatico", "Iceberg", "Un ritratto a matita prima del naufragio."]},
    {"target": "IL SIGNORE DEGLI ANELLI", "indizi": ["Fantasy", "Anello magico", "Nove viandanti partono per distruggere il male."]},
    {"target": "FORREST GUMP", "indizi": ["Commedia / Drammatico", "Scatola di cioccolatini", "Attraversare la storia americana correndo senza sosta."]},
    {"target": "SE MI LASCI TI CANCELLO", "indizi": ["Romantico / Sci-Fi", "Capelli colorati", "Cancellare i ricordi di una relazione fallita."]},
    {"target": "OPPENHEIMER", "indizi": ["Storico / Biografico", "Reazione a catena", "Il dilemma morale nel deserto del Los Alamos."]},
    {"target": "SEVEN", "indizi": ["Thriller / Noir", "Vizi capitali", "Un pacco recapitato nel deserto alla fine della caccia."]},
    {"target": "L'AVVOCATO DEL DIAVOLO", "indizi": ["Thriller / Grottesco", "Studio legale", "La vanità è il peccato preferito del capo."]},
    {"target": "C'ERA UNA VOLTA A HOLLYWOOD", "indizi": ["Commedia / Drammatico", "Stuntman", "L'età d'oro del cinema minacciata da una setta."]},
    {"target": "SHINING", "indizi": ["Horror / Psicologico", "Hotel isolato", "Scrivere la stessa frase all'infinito sulla macchina da scrivere."]},
    {"target": "ARANCIA MECCANICA", "indizi": ["Distopico / Drammatico", "Latte correttivo", "Riconvertire un criminale riproducendo musica classica."]},
    {"target": "WHIPLASH", "indizi": ["Drammatico / Musica", "Batteria", "Mani insanguinate per compiacere un maestro spietato."]},
    {"target": "THE PRESTIGE", "indizi": ["Mistero / Sci-Fi", "Illusionismo", "Il sacrificio per realizzare il trucco del trasporto umano."]},
    {"target": "SCARFACE", "indizi": ["Crime / Drammatico", "Montagna bianca", "Dai bassifondi di Miami fino al trono della droga."]},
    {"target": "IL PADRINO", "indizi": ["Mafia / Drammatico", "Cannoli", "Passaggio di testimone all'interno della famiglia newyorkese."]},
    {"target": "JOKER", "indizi": ["Drammatico / Psicologico", "Scalinata", "Un comico fallito diventa il simbolo della rivolta."]},
    {"target": "PROVA A PRENDERMI", "indizi": ["Commedia / Crime", "Assegni falsi", "Cambiare identità continuamente per sfuggire all'FBI."]},
    {"target": "THE TRUMAN SHOW", "indizi": ["Drammatico / Sci-Fi", "Cupola gigante", "Scoprire che la propria vita è trasmessa in TV."]},
    {"target": "V PER VENDETTA", "indizi": ["Distopico / Azione", "Maschera sorridente", "Far esplodere il parlamento il cinque di novembre."]},
    {"target": "SHREK", "indizi": ["Animazione / Commedia", "Palude", "Un orco e un ciuchino salvano una principessa con un segreto."]},
    {"target": "MONSTERS & CO.", "indizi": ["Animazione", "Porte scorrevoli", "Catturare le urla dei bambini come fonte d'energia."]},
    {"target": "UP", "indizi": ["Animazione / Avventura", "Palloncini", "Far volare una casa fino alle cascate del paradiso."]},
    {"target": "INSIDE OUT", "indizi": ["Animazione", "Isole della memoria", "Le emozioni che guidano i comportamenti alla consolle."]},
    {"target": "PARASITE", "indizi": ["Thriller / Commedia nera", "Seminterrato", "Infiltrarsi in una villa spacciandosi per professionisti."]},
    {"target": "GET OUT", "indizi": ["Horror / Mistero", "Cucchiaino e tazza", "Una visita dai suoceri che nasconde un ipnosi inquietante."]},
    {"target": "DRIVE", "indizi": ["Action / Noir", "Giacca con scorpione", "Pilota di notte e stuntman di giorno nel silenzio."]},
    {"target": "DONNIE DARKO", "indizi": ["Sci-Fi / Mistero", "Coniglio gigante", "Un motore d'aereo cade dal cielo e segna il tempo rimasto."]},
    {"target": "NIGHTCRAWLER", "indizi": ["Thriller / Crime", "Videocamera notturna", "Filmare la cronaca nera arrivando prima dei soccorsi."]},
    {"target": "GRAN TORINO", "indizi": ["Drammatico", "Garage", "Un veterano scorbutico difende i vicini di casa asiatici."]},
    {"target": "LA GRANDE BELLEZZA", "indizi": ["Drammatico", "Feste sui tetti", "Camminare nella capitale alla ricerca dello sconforto e del fascino."]},
    {"target": "SNATCH", "indizi": ["Crime / Commedia", "Diamante", "Scommesse clandestine e pugili nomadi imbattibili."]},
    {"target": "KILL BILL", "indizi": ["Azione / Revenge", "Tuta gialla", "Una lista di nomi da depennare con la katana."]},
    {"target": "LE IENE", "indizi": ["Crime / Thriller", "Nomi di colori", "Una rapina andata male e una spia nel capannone."]},
    {"target": "IL GRANDE LEBOWSKI", "indizi": ["Commedia", "Tappeto", "Uno scambio di persona per colpa di un nome comune."]},
    {"target": "NON E UN PAESE PER VECCHI", "indizi": ["Thriller / Western", "Moneta da lanciare", "Una valigetta piena di soldi e un killer con bombola d'aria spietato."]},
    {"target": "LA FORMA DELL'ACQUA", "indizi": ["Fantastico / Romantico", "Vasca d'acqua", "Una donna muta innamorata di una creatura anfibia."]},
    {"target": "ARRIVAL", "indizi": ["Sci-Fi", "Cerchi di inchiostro", "Decifrare il linguaggio alieno per modificare la percezione del tempo."]},
    {"target": "1917", "indizi": ["Guerra / Drammatico", "Trincee", "Consegnare un ordine a piedi per fermare un attacco suicida."]},
    {"target": "DUNKIRK", "indizi": ["Guerra / Storico", "Molo", "Tre linee temporali per evacuare i soldati dalla spiaggia."]},
    {"target": "PRISONERS", "indizi": ["Thriller / Drammatico", "Labirinto", "Un padre disperato si fa giustizia da solo per ritrovare la figlia."]},
    {"target": "THE SOCIAL NETWORK", "indizi": ["Biografico / Drammatico", "Algoritmo", "Creare una rete universitaria finendo citati in giudizio dagli amici."]},
    {"target": "HER", "indizi": ["Romantico / Sci-Fi", "Auricolare", "Innamorarsi della voce dell'intelligenza artificiale del telefono."]},
    {"target": "EX MACHINA", "indizi": ["Sci-Fi / Thriller", "Test di Turing", "Valutare la coscienza di un robot umanoide in una villa isolata."]},
    {"target": "A QUIET PLACE", "indizi": ["Horror / Sci-Fi", "Sabbia", "Sopravvivere in silenzio assoluto per non attirare i predatori."]},
    {"target": "MAD MAX FURY ROAD", "indizi": ["Azione / Distopico", "Cisterna", "Fuga e inseguimento continuo nel deserto fiammeggiante."]},
    {"target": "THE HATEFUL EIGHT", "indizi": ["Western / Thriller", "Bufera di neve", "Otto sconosciuti bloccati in un emporio dove nessuno è chi dice di essere."]},
    {"target": "BLADE RUNNER 2049", "indizi": ["Sci-Fi / Noir", "Ologrammi", "Cercare un bambino nato da una donna non umana."]},
    {"target": "GLASS ONION", "indizi": ["Giallo / Commedia", "Isola privata", "Un detective stravagante risolve un delitto tra miliardari."]},
    {"target": "LA CITTA INCANTATA", "indizi": ["Animazione / Fantasy", "Bagni termali", "Lavorare per la strega per liberare i genitori trasformati in animali."]},
    {"target": "RANGO", "indizi": ["Animazione / Western", "Camaleonte", "Un geco attore diventa sceriffo in una cittadina nel deserto."]},
    {"target": "COCO", "indizi": ["Animazione", "Chitarra bianca", "Viaggio nella terra dei defunti per scoprire la verità sui propri avi."]},
    {"target": "RATATOUILLE", "indizi": ["Animazione / Commedia", "Cappello da chef", "Un piccolo roditore guida i movimenti del cuciniere."]},
    {"target": "I GOONIES", "indizi": ["Avventura", "Mappa del tesoro", "Ragazzini cercano il galeone pirata per salvare le proprie case."]},
    {"target": "STAND BY ME", "indizi": ["Avventura / Drammatico", "Binari", "Quattro amici camminano nel bosco per trovare un corpo scomparso."]},
    {"target": "I SOLITI IGNOTI", "indizi": ["Commedia / Crime", "Cassaforte", "Sfondare la parete sbagliata e finire a mangiare pasta e fagioli."]},
    {"target": "L'ODIO", "indizi": ["Drammatico", "Banlieue", "Ventiquattro ore nella periferia con una pistola trovata per caso."]},
    {"target": "AMELIE", "indizi": ["Commedia / Romantico", "Creme brûlée", "Piccoli piani segreti per aggiustare le vite degli abitanti di Montmartre."]},
    {"target": "FULL METAL JACKET", "indizi": ["Guerra / Drammatico", "Cappello da istruttore", "Dalla brutalità del campo d'addestramento alle rovine del Vietnam."]},
    {"target": "TAXI DRIVER", "indizi": ["Drammatico / Noir", "Cresta mohawk", "Un reduce insonne guida di notte pulendo le strade dal marciume."]},
    {"target": "OLDBOY", "indizi": ["Thriller / Action", "Martello", "Imprigionato per quindici anni in una stanza cerca la vendetta."]},
    {"target": "MEMENTO", "indizi": ["Thriller / Mistero", "Tatuaggi sul corpo", "La storia raccontata al contrario per chi perde la memoria ogni dieci minuti."]},
    {"target": "BIRDMAN", "indizi": ["Drammatico / Commedia", "Corridoi del teatro", "Un vecchio attore di blockbuster tenta il riscatto a Broadway in piano sequenza."]},
    {"target": "CHINATOWN", "indizi": ["Noir / Mistero", "Cerotto sul naso", "Un'indagine su un adulterio che svela la mafia dell'acqua in città."]},
    {"target": "TORO SCATENATO", "indizi": ["Biografico / Sportivo", "Ring in bianco e nero", "L'autodistruzione di un campione di pugilato accecato dalla gelosia."]},
    {"target": "REQUIEM FOR A DREAM", "indizi": ["Drammatico", "Pupille", "Quattro spirali di dipendenza che distruggono ogni speranza."]},
    {"target": "I FIGLI DEGLI UOMINI", "indizi": ["Distopico / Thriller", "Ultima gravidanza", "Scortare l'unica donna incinta in un mondo diventato sterile."]},
    {"target": "GRAND BUDAPEST HOTEL", "indizi": ["Commedia", "Quadro rubato", "Le avventure di un concierge impeccabile tra le montagne dell'est."]},
    {"target": "DISTRICT 9", "indizi": ["Sci-Fi / Action", "Baraccopoli", "Extraterrestri reclusi in un ghetto e la mutazione del protagonista."]},
    {"target": "UNSTOPPABLE", "indizi": ["Action / Thriller", "Treno senza freni", "Due ferrovieri provano ad agganciare un convoglio carico di veleno."]},
    {"target": "SPOTLIGHT", "indizi": ["Drammatico / Giornalismo", "Archivi cartacei", "Inchiesta della redazione per svelare gli insabbiamenti della Chiesa."]},
    {"target": "LA PAROLA AI GIURATI", "indizi": ["Drammatico / Processuale", "Stanza chiusa", "Un unico giurato instilla il ragionevole dubbio agli altri undici."]},
    {"target": "MULHOLLAND DRIVE", "indizi": ["Mistero / Psicologico", "Chiave blu", "Amnesia e sogni oscuri tra le colline della città del cinema."]},
    {"target": "AMORES PERROS", "indizi": ["Drammatico", "Combattimenti tra cani", "Un incidente d'auto che incrocia tre destini tragici."]},
    {"target": "BEAU HA PAURA", "indizi": ["Commedia nera / Psicologico", "Pigiama azzurro", "Un viaggio allucinante e pieno di ansie per raggiungere la casa materna."]},
    {"target": "THE LOBSTER", "indizi": ["Grottesco / Sci-Fi", "Hotel per single", "Trovare un compagno in 45 giorni o essere trasformati in un animale."]},
    {"target": "ZODIAC", "indizi": ["Thriller / Crime", "Messaggi cifrati", "L'ossessione di un vignettista nel dare un volto al killer seriale."]},
    {"target": "COPIA CONFORME", "indizi": ["Drammatico", "Borgo toscano", "Due sconosciuti iniziano a comportarsi come se fossero sposati da anni."]},
    {"target": "SYNECDOCHE NEW YORK", "indizi": ["Drammatico", "Hangar gigante", "Un regista ricrea la sua intera vita all'interno di un set teatrale."]},
    {"target": "DRIVE MY CAR", "indizi": ["Drammatico", "Auto rossa", "Confessioni e elaborazione del lutto durante i viaggi con la giovane autista."]},
    {"target": "HARDCORE", "indizi": ["Action / Sci-Fi", "Visuale in prima persona", "Risvegliarsi cyborg e combattere a rotta di collo per salvare la moglie."]},
    {"target": "SUPERBAD", "indizi": ["Commedia", "Carta d'identità falsa", "Tre liceali cercano di procurarsi gli alcolici per la festa dell'anno."]},
    {"target": "SCREAM", "indizi": ["Horror / Slasher", "Maschera bianca", "Un quiz sui film horror prima di colpire le vittime al telefono."]},
    {"target": "DON'T LOOK UP", "indizi": ["Commedia / Satira", "Cometa", "Due astronomi provano ad avvertire il mondo dell'imminente catastrofe."]},
    {"target": "SCOTT PILGRIM", "indizi": ["Action / Commedia", "Capelli rosa", "Sconfiggere i sette malvagi ex fidanzati per conquistar la ragazza."]},
    {"target": "UNBREAKABLE", "indizi": ["Thriller / Sci-Fi", "Impermeabile verde", "L'unico sopravvissuto a un disastro ferroviario scopre di non farsi mai male."]},
    {"target": "12 ANNI SCHIAVO", "indizi": ["Storico / Drammatico", "Violino", "Un uomo libero viene rapito e venduto nelle piantagioni del sud."]},
    {"target": "LA LA LAND", "indizi": ["Musical / Romantico", "Vestito giallo", "Inseguire i propri sogni tra tip-tap e piano bar a Los Angeles."]},
    {"target": "TOP GUN MAVERICK", "indizi": ["Action", "Giacca in pelle", "Addestrare giovani piloti per una missione impossibile nei canyon."]}
]

# --- DATABASE 50 SERIE TV ---
QUIZ_SERIE_DB = [
    {"target": "BREAKING BAD", "indizi": ["Crime / Drama", "Camper nel deserto", "La chimica usata per lasciare un'eredità economica."]},
    {"target": "STRANGER THINGS", "indizi": ["Sci-Fi / Mystery", "Luci di Natale sul muro", "Scomparse inspiegabili nella provincia anni '80."]},
    {"target": "IL TRONO DI SPADE", "indizi": ["Fantasy / Politico", "Trono di spade fuse", "Grandi famiglie in lotta mentre il gelo si avvicina."]},
    {"target": "FRIENDS", "indizi": ["Sitcom", "Divano arancione", "L'età adulta affrontata condividendo lo stesso locale."]},
    {"target": "LA CASA DI CARTA", "indizi": ["Thriller / Action", "Maschera di Dalí", "Un colpo milionario pianificato senza spargere sangue."]},
    {"target": "SQUID GAME", "indizi": ["Thriller / Distopico", "Tuta da ginnastica verde", "Sfide dell'infanzia trasformate in trappole per indebitati."]},
    {"target": "PEAKY BLINDERS", "indizi": ["Crime / Storico", "Cappello con lama", "L'ascesa di una famiglia nella Birmingham industriale."]},
    {"target": "THE OFFICE", "indizi": ["Sitcom / Mockumentary", "Tazza Best Boss", "La routine di una noiosa azienda di carta."]},
    {"target": "DARK", "indizi": ["Sci-Fi / Mistero", "Impermeabile giallo", "I segreti di una cittadina legati al fluire del tempo."]},
    {"target": "THE WALKING DEAD", "indizi": ["Horror / Survival", "Cappello da sceriffo", "La lotta per restare umani quando il mondo crolla."]},
    {"target": "GOMORRA", "indizi": ["Crime", "Anello con sigillo", "L'ambizione di potere tra le piazze di spaccio."]},
    {"target": "LOST", "indizi": ["Mistero / Survival", "Sequenza di 6 numeri", "Sopravvissuti a un volo in un luogo privo di mappe."]},
    {"target": "BLACK MIRROR", "indizi": ["Antologico / Distopico", "Schermo nero", "Il lato oscuro del nostro rapporto con la tecnologia."]},
    {"target": "SHERLOCK", "indizi": ["Giallo", "Violino e cappello", "Un intelletto fuori dal comune al servizio della polizia di Londra."]},
    {"target": "THE MANDALORIAN", "indizi": ["Sci-Fi / Space Western", "Elmo in metallo", "Un mercenario solitario che sceglie di proteggere il suo bersaglio."]},
    {"target": "HOW I MET YOUR MOTHER", "indizi": ["Sitcom", "Ombrello giallo", "Un lungo racconto ai figli ripercorrendo anni di gioventù."]},
    {"target": "PRISON BREAK", "indizi": ["Action / Thriller", "Tatuaggio sul torso", "Un piano geniale per salvare un parente ingiustamente condannato."]},
    {"target": "THE BIG BANG THEORY", "indizi": ["Sitcom", "Maglietta di Flash", "Mentibrillanti della fisica alle prese con la vita sociale."]},
    {"target": "VIKINGS", "indizi": ["Storico / Avventura", "Asse di legno e drakkar", "Esploratori del nord alla conquista di nuove sponde."]},
    {"target": "CHERNOBYL", "indizi": ["Storico / Drammatico", "Contatore Geiger", "Le menzogne e il prezzo umano di una catastrofe nucleare."]},
    {"target": "BETTER CALL SAUL", "indizi": ["Legale / Crime", "Abiti sgargianti e targa LWYRUP", "La trasformazione morale di un avvocato alle prime armi."]},
    {"target": "I SOPRANO", "indizi": ["Crime / Drama", "Lettino da terapia", "La doppia vita di un boss tra affari di famiglia e attacchi d'ansia."]},
    {"target": "THE CROWN", "indizi": ["Storico / Drammatico", "Diadema reale", "Un regno decennale sospeso tra dovere pubblico e sacrifici privati."]},
    {"target": "DEXTER", "indizi": ["Crime / Thriller", "Vetrino da laboratorio", "Un codice morale per incanalare impulsi oscuri."]},
    {"target": "SONS OF ANARCHY", "indizi": ["Crime / Drama", "Giacca di pelle con logo", "Fratellanza, motori e traffici ai margini della legge."]},
    {"target": "THE BOYS", "indizi": ["Action / Satirico", "Mantello con la bandiera", "Eroi acclamati dal pubblico che nascondono un volto corrotto."]},
    {"target": "EUPHORIA", "indizi": ["Teen / Drama", "Trucco glitterato", "La ricerca di identità e gli eccessi della gioventù contemporanea."]},
    {"target": "SEX EDUCATION", "indizi": ["Commedia", "Bagno abbandonato della scuola", "Consigli clandestini per aiutare i compagni di liceo."]},
    {"target": "BRIDGERTON", "indizi": ["Romantico / In costume", "Foglio di pettegolezzi", "Caccia al miglior partito nei balli dell'alta società."]},
    {"target": "MERCOLEDI", "indizi": ["Mystery / Fantasy", "Trecce e uniforme nera", "Una studentessa cinica indagatrice tra i segreti del collegio."]},
    {"target": "DOCTOR WHO", "indizi": ["Sci-Fi", "Cabina blu", "Un viaggiatore che muta forma attraverso lo spazio e il tempo."]},
    {"target": "NARCOS", "indizi": ["Crime / Biografico", "Baffi e mazzette di banconote", "La caccia delle forces dell'ordine ai re del narcotraffico."]},
    {"target": "THE LAST OF US", "indizi": ["Sci-Fi / Drama", "Infezione da funghi", "Un viaggio di protezione in un mondo in rovina."]},
    {"target": "SUITS", "indizi": ["Legale", "Abito tre pezzi e cravatta", "Un talento prodigioso operativo senza mai aver preso la laurea."]},
    {"target": "MR ROBOT", "indizi": ["Cyberpunk / Thriller", "Felpa nera", "Un programmatore che vuole smantellare il sistema finanziario."]},
    {"target": "FARGO", "indizi": ["Crime / Black Comedy", "Neve e parka", "Scelte sbagliate che scatenano scie di delitti in provincia."]},
    {"target": "TRUE DETECTIVE", "indizi": ["Crime / Noir", "Lattine di birra piegate", "Un'ossessione investigativa che dura per decenni."]},
    {"target": "THE BEAR", "indizi": ["Drama / Commedia", "Grembiule blu", "La frenesia di una cucina da rimettere in piedi."]},
    {"target": "TED LASSO", "indizi": ["Commedia / Sportivo", "Baffi e cartello sopra la porta", "L'ottimismo di un tecnico americano trapiantato nel calcio inglese."]},
    {"target": "HOUSE OF CARDS", "indizi": ["Politico / Drama", "Anello battuto sul legno", "Trama nell'ombra per scalare le vette del potere."]},
    {"target": "SCRUBS", "indizi": ["Sitcom / Medico", "Camice azzurro", "Tra sogni a occhi aperti e corsie d'ospedale."]},
    {"target": "DR HOUSE", "indizi": ["Medico", "Bastone da passeggio", "Diagnosi impossibili risolte da una mente geniale e cinica."]},
    {"target": "MARE FUORI", "indizi": ["Drama", "Vista sul mare dietro le sbarre", "Riscatto e scelte sbagliate per giovani in un carcere minorile."]},
    {"target": "SUBURRA", "indizi": ["Crime", "Terreni sulla costa", "L'intreccio tra criminalità, Chiesa e politica nella capitale."]},
    {"target": "GOSSIP GIRL", "indizi": ["Teen / Drama", "Cerchietto per capelli", "Un blog misterioso che svela i segreti dei ragazzi ricchi."]},
    {"target": "MODERN FAMILY", "indizi": ["Sitcom", "Interviste sul divano", "Tre diversi nuclei familiari legati da un unico albero genealogico."]},
    {"target": "BOJACK HORSEMAN", "indizi": ["Animazione per adulti", "Giacca da stella del cinema", "La parabola malinconica di una ex star in cerca di riscatto."]},
    {"target": "SKINS", "indizi": ["Teen / Drama", "Feste sregolate", "L'adolescenza senza filtri vista da un gruppo di ragazzi inglesi."]},
    {"target": "GLEE", "indizi": ["Musical / Teen", "Microfono e spartiti", "La rivalsa degli emarginati della scuola attraverso il canto."]},
    {"target": "YELLOWSTONE", "indizi": ["Western / Drama", "Cappello da cowboy e marchio", "La difesa ad ogni costo dei confini del proprio ranch."]}
]

# --- DATABASE 100 PILOTI FORMULA 1 ---
QUIZ_FORMULA1_DB = [
    {"target": "SCHUMACHER", "indizi": ["Germania", "Ferrari, Benetton", "L'era rossa dei sette titoli iridati."]},
    {"target": "HAMILTON", "indizi": ["Regno Unito", "Mercedes, McLaren", "Sette corone mondiali sulla monoposto numero 44."]},
    {"target": "SENNA", "indizi": ["Brasile", "McLaren, Williams", "Il maestro assoluto del giro secco e della pioggia."]},
    {"target": "VERSTAPPEN", "indizi": ["Olanda", "Red Bull, Toro Rosso", "Il giovane prodigio dominatore dell'era moderna."]},
    {"target": "LECLERC", "indizi": ["Monaco", "Ferrari, Sauber", "Il beniamino di Maranello nato e cresciuto nel Principato."]},
    {"target": "LAUDA", "indizi": ["Austria", "Ferrari, McLaren", "Il calcolo perfetto e il rientro record dopo il rogo."]},
    {"target": "PROST", "indizi": ["Francia", "McLaren, Williams", "Soprannominato Il Professore per la sua guida tattica."]},
    {"target": "ALONSO", "indizi": ["Spagna", "Renault, Ferrari", "Due titoli iridati e una carriera lunghissima su tre decenni."]},
    {"target": "RAIKKONEN", "indizi": ["Finlandia", "Ferrari, Lotus", "Iceman, l'ultimo pilota a conquistare il titolo a Maranello."]},
    {"target": "VILLENEUVE", "indizi": ["Canada", "Ferrari", "Il canadese dal cuore grande amato per il suo stile spericolato."]},
    {"target": "VETTEL", "indizi": ["Germania", "Red Bull, Ferrari", "Quattro mondiali consecutivi nell'era dei motori V8."]},
    {"target": "ROSBERG", "indizi": ["Germania", "Mercedes, Williams", "Batté il compagno di squadra nel 2016 per poi ritirarsi subito."]},
    {"target": "BUTTON", "indizi": ["Regno Unito", "Brawn GP, McLaren", "Il trionfo iridato con la favola della scuderia bianca e gialla."]},
    {"target": "HAKKINEN", "indizi": ["Finlandia", "McLaren", "Il Finlandese Volante che è stato il più grande rivale di Schumacher."]},
    {"target": "HILL", "indizi": ["Regno Unito", "Williams, Jordan", "Figlio d'arte diventato campione del mondo nel 1996."]},
    {"target": "MANSELL", "indizi": ["Regno Unito", "Williams, Ferrari", "Detto Il Leone per la sua guida aggressiva ed epica."]},
    {"target": "PIQUET", "indizi": ["Brasile", "Brabham, Williams", "Tre volte iridato celebre per il suo carattere ironico e pungente."]},
    {"target": "HUNT", "indizi": ["Regno Unito", "McLaren, Hesketh", "Lo stile di vita sregolato negli anni settanta."]},
    {"target": "STEWART", "indizi": ["Regno Unito", "Tyrrell", "Tre titoli e pioniere delle battaglie sulla sicurezza in pista."]},
    {"target": "CLARK", "indizi": ["Regno Unito", "Lotus", "Il talento scozzese degli anni sessanta imbattibile sul bagnato."]},
    {"target": "FANGIO", "indizi": ["Argentina", "Alfa Romeo, Mercedes", "Il re degli albori della F1 con cinque titoli in cinque marchi diversi."]},
    {"target": "GRAHAM HILL", "indizi": ["Regno Unito", "BRM, Lotus", "L'unico ad aver conquistato la Triple Crown del motorsport."]},
    {"target": "ANDRETTI", "indizi": ["Stati Uniti", "Lotus, Alfa Romeo", "Campione italo-americano sul tetto del mondo nel 1978."]},
    {"target": "KEKE ROSBERG", "indizi": ["Finlandia", "Williams", "Un solo Gran Premio vinto nell'anno del suo mondiale 1982."]},
    {"target": "JACQUES VILLENEUVE", "indizi": ["Canada", "Williams, BAR", "Figlio del mitico Gilles, vinse il titolo al suo secondo anno in F1."]},
    {"target": "SAINZ", "indizi": ["Spagna", "Ferrari, McLaren", "Il Smooth Operator vincitore a Silverstone e Singapore."]},
    {"target": "NORRIS", "indizi": ["Regno Unito", "McLaren", "Il giovane talento inglese dalla livrea papaya."]},
    {"target": "RUSSELL", "indizi": ["Regno Unito", "Mercedes, Williams", "Lo Mr. Saturday che si è guadagnato il sedile sulle Frecce d'Argento."]},
    {"target": "PEREZ", "indizi": ["Messico", "Red Bull, Racing Point", "Checo, specialista nella gestione delle gomme e nei tracciati cittadini."]},
    {"target": "RICCIARDO", "indizi": ["Australia", "Red Bull, Renault", "Il sorriso australiano e la celebre esultanza bevendo dallo scarpino."]},
    {"target": "BOTTAS", "indizi": ["Finlandia", "Mercedes, Alfa Romeo", "Lo scudiero scandinavo dell'era dominante tedesca."]},
    {"target": "MASSA", "indizi": ["Brasile", "Ferrari, Williams", "Campione del mondo per pochi secondi nel GP di casa 2008."]},
    {"target": "BARRICHELLO", "indizi": ["Brasile", "Ferrari, Honda", "Lo storico compagno di squadra brasiliano nei primi anni duemila."]},
    {"target": "COULTHARD", "indizi": ["Regno Unito", "McLaren, Red Bull", "Il pilota scozzese dal casco con la croce di Sant'Andrea."]},
    {"target": "WEBBER", "indizi": ["Australia", "Red Bull, Jaguar", "Not bad for a number two driver dopo una vittoria contesa."]},
    {"target": "FISICHELLA", "indizi": ["Italia", "Renault, Jordan", "L'ultimo pilota italiano ad aver vinto un Gran Premio di F1."]},
    {"target": "TRULLI", "indizi": ["Italia", "Renault, Toyota", "Il re delle qualifiche dal passo gara inconfondibile."]},
    {"target": "ALESI", "indizi": ["Francia", "Ferrari, Benetton", "Il numero 27 amato per la sua guida di puro cuore."]},
    {"target": "BERGER", "indizi": ["Austria", "Ferrari, McLaren", "Pilota e gran simpaticone, spalla di Senna e vincitore a Monza dopo l'88."]},
    {"target": "PATRESE", "indizi": ["Italia", "Williams, Brabham", "Detentore per anni del record di GP disputati nella massima serie."]},
    {"target": "GROSJEAN", "indizi": ["Francia", "Lotus, Haas", "Il miracolo del fuoco dopo il pauroso schianto in Bahrain."]},
    {"target": "GASLY", "indizi": ["Francia", "AlphaTauri, Alpine", "L'incredibile ed emozionante vittoria a Monza con la scuderia faentina."]},
    {"target": "OCON", "indizi": ["Francia", "Alpine, Force India", "La vittoria a sorpresa nel caos di Budapest 2021."]},
    {"target": "PIASTRI", "indizi": ["Australia", "McLaren", "Il giovane australiano dal talento e la freddezza glaciale."]},
    {"target": "ALBON", "indizi": ["Thailandia", "Williams, Red Bull", "Il pilota thai che ha riportato in alto la storica scuderia di Grove."]},
    {"target": "HULKENBERG", "indizi": ["Germania", "Haas, Force India", "Il talento tedesco con il curioso record di gare disputate senza podi."]},
    {"target": "MAGNUSSEN", "indizi": ["Danimarca", "Haas, McLaren", "Battagliero in pista ed autore di una pole storica a San Paolo."]},
    {"target": "KOBAYASHI", "indizi": ["Giappone", "Sauber, Toyota", "Sorpassi garibaldini e lo storico podio a Suzuka davanti al suo pubblico."]},
    {"target": "KUBICA", "indizi": ["Polonia", "BMW Sauber, Williams", "Il talento cristallino frenato da un terribile incidente nel rally."]},
    {"target": "MONTOYA", "indizi": ["Colombia", "Williams, McLaren", "Il colombiano grintoso famoso per i duelli spalla a spalla con Schumacher."]},
    {"target": "TSUNODA", "indizi": ["Giappone", "AlphaTauri, RB", "Il minuto pilota giapponese famoso per le sue accese comunicazioni via radio."]},
    {"target": "STROLL", "indizi": ["Canada", "Aston Martin, Racing Point", "Il figlio del proprietario del team capace comunque di una pole sul bagnato."]},
    {"target": "ZHOU", "indizi": ["Cina", "Alfa Romeo, Sauber", "Il primo pilota di ruolo cinese ad aver gareggiato in Formula 1."]},
    {"target": "SARGEANT", "indizi": ["Stati Uniti", "Williams", "Il pilota statunitense arrivato nell'era di Drive to Survive."]},
    {"target": "MICK SCHUMACHER", "indizi": ["Germania", "Haas", "Il figlio d'arte portatore di un cognome pesantissimo."]},
    {"target": "LATIFI", "indizi": ["Canada", "Williams", "La sua uscita di pista ad Abu Dhabi nel 2021 cambiò les sorti del mondiale."]},
    {"target": "MAZEPIN", "indizi": ["Russia", "Haas", "Il contestato pilota russo dell'annata 2021."]},
    {"target": "GIOVINAZZI", "indizi": ["Italia", "Alfa Romeo", "L'ultimo pilota italiano titolare in griglia negli ultimi anni."]},
    {"target": "KVYAT", "indizi": ["Russia", "Red Bull, Toro Rosso", "Il Siluro, protagonista di contatti celebri con Vettel."]},
    {"target": "ERICSSON", "indizi": ["Svezia", "Sauber, Caterham", "Lo svedese volante diventato poi vincitore della 500 Miglia di Indianapolis."]},
    {"target": "MALDONADO", "indizi": ["Venezuela", "Williams, Lotus", "L'incredibile ed isolata vittoria a Barcellona nel 2012 tra mille incidenti."]},
    {"target": "VANDOORNE", "indizi": ["Belgio", "McLaren", "Promessa delle categorie minori stritolata dal confronto con Alonso."]},
    {"target": "HARTLEY", "indizi": ["Nuova Zelanda", "Toro Rosso", "Dal programma Endurance alla parentesi in F1."]},
    {"target": "WEHRLEIN", "indizi": ["Germania", "Manor, Sauber", "Prodotto del vivaio Mercedes a punti con vetture di fondo griglia."]},
    {"target": "GUTIERREZ", "indizi": ["Messico", "Sauber, Haas", "Il compagno messicano nelle prime stagioni del team americano."]},
    {"target": "BIANCHI", "indizi": ["Francia", "Marussia", "Il talento del vivaio Ferrari la cui vita si è spezzata a Suzuka."]},
    {"target": "KOVALAINEN", "indizi": ["Finlandia", "McLaren, Renault", "Vincitore del GP di Ungheria 2008 all'ombra di Hamilton."]},
    {"target": "PIQUET JR", "indizi": ["Brasile", "Renault", "Protagonista involontario del celebre scandalo Crashgate a Singapore 2008."]},
    {"target": "LIUZZI", "indizi": ["Italia", "Red Bull, Toro Rosso", "Campione del mondo di kart approdato al team della lattina."]},
    {"target": "SPEED", "indizi": ["Stati Uniti", "Toro Rosso", "Il primo americano dell'era moderna della Red Bull."]},
    {"target": "TAKUMA SATO", "indizi": ["Giappone", "Super Aguri, BAR", "Il leggendario sorpasso su Alonso a Montreal."]},
    {"target": "BRUNI", "indizi": ["Italia", "Minardi", "L'italiano al volante della storica scuderia di Faenza nel 2004."]},
    {"target": "BAUMGARTNER", "indizi": ["Ungheria", "Minardi, Jordan", "Il primo ed unico pilota ungherese a punti in F1."]},
    {"target": "DA MATTA", "indizi": ["Brasile", "Toyota", "Il brasiliano arrivato dai successi nel campionato CART americano."]},
    {"target": "JOS VERSTAPPEN", "indizi": ["Olanda", "Benetton, Arrows", "Padre di Max, famoso per l'incendio ai box a Hockenheim nel 1994."]},
    {"target": "BADOER", "indizi": ["Italia", "Minardi, Ferrari", "Storico collaudatore Ferrari ritornato in gara per sostituire Massa nel 2009."]},
    {"target": "GENE", "indizi": ["Spagna", "Minardi, Williams", "Pilota spagnolo diventato voce tecnica per la TV italiana."]},
    {"target": "DE LA ROSA", "indizi": ["Spagna", "Arrows, McLaren", "L'eterno collaudatore ed esperto di sviluppo."]},
    {"target": "WURZ", "indizi": ["Austria", "Benetton, Williams", "Pilota altissimo diventato punto di riferimento per la sicurezza dei colleghi."]},
    {"target": "SALO", "indizi": ["Finlandia", "Ferrari, Sauber", "Sostituì Schumacher nel 1999 lasciando la vittoria al compagno Irvine."]},
    {"target": "IRVINE", "indizi": ["Regno Unito", "Ferrari, Jaguar", "Il nordirlandese eccentrico sfiorò il titolo mondiale nel 1999."]},
    {"target": "FRENTZEN", "indizi": ["Germania", "Williams, Jordan", "Il tedesco che lottò per il mondiale nel 1999 con la scuderia gialla."]},
    {"target": "PANIS", "indizi": ["Francia", "Ligier, BAR", "L'incredibile vittoria sotto la pioggia nel caos di Monaco 1996."]},
    {"target": "KATAYAMA", "indizi": ["Giappone", "Tyrrell, Venturi", "Popolare guida giapponese degli anni novanta."]},
    {"target": "CAPELLI", "indizi": ["Italia", "Leyton House, Ferrari", "Portò la vettura turchese disegnata da Newey quasi alla vittoria a Le Castellet."]},
    {"target": "MODENA", "indizi": ["Italia", "Tyrrell, Brabham", "Uno dei giovani italiani più promettenti della fine degli anni ottanta."]},
    {"target": "MARTINI", "indizi": ["Italia", "Minardi", "L'uomo simbolo della Minardi, portatala persino in prima fila."]},
    {"target": "DE CESARIS", "indizi": ["Italia", "Alfa Romeo, Jordan", "Detto De Crasheris nei primi anni, disputò oltre 200 GP."]},
    {"target": "BOUTSEN", "indizi": ["Belgio", "Williams, Benetton", "Solido pilota belga vincitore di tre GP tra l'89 e il '90."]},
    {"target": "JOHANSSON", "indizi": ["Svezia", "Ferrari, McLaren", "Lo svedese a podio con le due scuderie rivali negli anni '80."]},
    {"target": "DE ANGELIS", "indizi": ["Italia", "Lotus, Brabham", "Detto Il Pilota Gentiluomo, talento e pianista raffinato."]},
    {"target": "TAMBAY", "indizi": ["Francia", "Ferrari, Renault", "Portò al successo il numero 27 dopo la scomparsa di Villeneuve."]},
    {"target": "ARNOUX", "indizi": ["Francia", "Renault, Ferrari", "Protagonista del favoloso duello a ruote fumanti a Digione nel '79 con Villeneuve."]},
    {"target": "REGAZZONI", "indizi": ["Svizzera", "Ferrari, Williams", "Svizzero dal baffo forte, regalò la prima vittoria della storia alla Williams."]},
    {"target": "PETERSON", "indizi": ["Svezia", "Lotus, March", "Lo Svedese Volante maestro delle sbandate controllate negli anni '70."]},
    {"target": "CEVERT", "indizi": ["Francia", "Tyrrell", "L'erede designato di Jackie Stewart dalla bellezza cinematografica."]},
    {"target": "BANDINI", "indizi": ["Italia", "Ferrari", "L'eroe italiano della Ferrari negli anni sessanta."]},
    {"target": "PHIL HILL", "indizi": ["Stati Uniti", "Ferrari", "Il primo americano a vincere il mondiale nel tragico anno 1961."]},
    {"target": "VON TRIPS", "indizi": ["Germania", "Ferrari", "Il conte tedesco che perse la vita a Monza al passo dal titolo mondiale."]},
    {"target": "HAWTHORN", "indizi": ["Regno Unito", "Ferrari", "Il primo inglese campione del mondo, celebre per il suo papillon in gara."]}
]

# --- DATABASE 100 SUPEREROI MARVEL / DC / INDIE ---
QUIZ_MARVEL_DB = [
    {"target": "SPIDER-MAN", "indizi": ["Marvel / Avengers", "Ragnatele e senso di ragno", "Morso da un aracnide radioattivo, è un ragazzo di Queens."]},
    {"target": "IRON MAN", "indizi": ["Marvel / Avengers", "Armatura ipertecnologica con reattore", "Miliardario, genio, playboy e filantropo."]},
    {"target": "CAPITAN AMERICA", "indizi": ["Marvel / Avengers", "Scudo indistruttibile in vibranio", "Il primo vendicatore ibernato dai tempi della Guerra."]},
    {"target": "THOR", "indizi": ["Marvel / Avengers", "Martello magico Mjolnir", "Il dio del tuono giunto dal regno di Asgard."]},
    {"target": "HULK", "indizi": ["Marvel / Avengers", "Forza sovrumana e pelle verde", "Lo scienziato che si trasforma quando perde la calma."]},
    {"target": "VEDOVA NERA", "indizi": ["Marvel / Avengers", "Spia micidiale e arti marziali", "Ex agente russa dal passato oscuro nella Stanza Rossa."]},
    {"target": "OCCHIO DI FALCO", "indizi": ["Marvel / Avengers", "Arco e frecce speciali", "Il tiratore scelto infallibile del gruppo."]},
    {"target": "WOLVERINE", "indizi": ["Marvel / X-Men", "Artigli scheletrici in adamantio e rigenerazione", "Il mutante canadese dall'animo selvaggio."]},
    {"target": "DEADPOOL", "indizi": ["Marvel", "Fattore rigenerante e rottura della quarta parete", "Il mercenario chiacchierone in tuta rossa e nera."]},
    {"target": "DOCTOR STRANGE", "indizi": ["Marvel / Avengers", "Mantello della Levitazione ed arti mistiche", "L'ex neurochirurgo diventato lo Stregone Supremo."]},
    {"target": "BLACK PANTHER", "indizi": ["Marvel / Avengers", "Tuta in vibranio e agilità felina", "Il re protettore della nazione africana di Wakanda."]},
    {"target": "GROOT", "indizi": ["Marvel / Guardiani della Galassia", "Forma vegetale in grado di ricrescere", "Un albero umanoide che pronuncia un'unica frase."]},
    {"target": "ROCKET RACCOON", "indizi": ["Marvel / Guardiani della Galassia", "Esperto di armi pesanti e ingegneria", "Un procione modificato geneticamente nello spazio."]},
    {"target": "STAR-LORD", "indizi": ["Marvel / Guardiani della Galassia", "Blaster spaziali e mangianastri anni '80", "Il terrestre rapito da piccolo che si crede un fuorilegge leggendario."]},
    {"target": "ANT-MAN", "indizi": ["Marvel / Avengers", "Riduzione e ingrandimento delle proprie dimensioni", "Rimpicciolirsi fino al livello atomico controllando gli insetti."]},
    {"target": "SCARLET WITCH", "indizi": ["Marvel / Avengers", "Magia del caos e manipolazione della realtà", "Una delle entità più potenti capaci di alterare l'esistenza."]},
    {"target": "VISIONE", "indizi": ["Marvel / Avengers", "Corpo in sintetizzatore e gemma sulla fronte", "L'androide nato dall'unione di un'intelligenza artificiale e una gemma dell'infinito."]},
    {"target": "FALCON", "indizi": ["Marvel / Avengers", "Tuta alata ad alta tecnologia", "Ex militare in grado di solcare i cieli al fianco degli eroi."]},
    {"target": "SOLDATO D'INVERNO", "indizi": ["Marvel / Avengers", "Braccio cibernetico in metallo", "L'amico d'infanzia sottoposto al lavaggio del cervello."]},
    {"target": "DAREDEVIL", "indizi": ["Marvel", "Sensi ipersviluppati pur essendo cieco", "Il diavolo del quartiere Hell's Kitchen di New York."]},
    {"target": "PUNISHER", "indizi": ["Marvel", "Teschio bianco sul petto ed arsenale", "L'ex marine che applica una giustizia spietata senza ritorno."]},
    {"target": "PROFESSOR X", "indizi": ["Marvel / X-Men", "Telepatia avanzata e sedia a rotelle", "Il fondatore della scuola per giovani mutanti dotati."]},
    {"target": "MAGNETO", "indizi": ["Marvel / X-Men", "Controllo assoluto dei campi magnetici e dei metalli", "Il signore del metallo che lotta per la supremazia mutante."]},
    {"target": "CICLOPE", "indizi": ["Marvel / X-Men", "Raggi ottici devastanti rossi", "Il leader con la visiera al quarzo rubino."]},
    {"target": "TEMPESTA", "indizi": ["Marvel / X-Men", "Controllo degli elementi atmosferici e dei fulmini", "La dea del clima dalla chioma bianca."]},
    {"target": "JEAN GREY", "indizi": ["Marvel / X-Men", "Poteri di telechinesi trasformati in un'entità di fuoco", "La mutante ospite della forza cosmica della Fenice."]},
    {"target": "ROGUE", "indizi": ["Marvel / X-Men", "Assorbimento di ricordi e poteri con il contatto fisico", "Non può toccare nessuno a pelle nuda senza prosciugarlo."]},
    {"target": "VENOM", "indizi": ["Marvel", "Simbionte alieno nero con lingua ricurva", "L'entità aliena parassita legata alla nemesi del ragno."]},
    {"target": "LOKI", "indizi": ["Marvel", "Illusioni, inganni e magia", "Il dio delle birbanterie fratello adottivo del dio del tuono."]},
    {"target": "THANOS", "indizi": ["Marvel", "Guanto dorato con sei pietre lucenti", "Il titano pazzo che voleva dimezzare l'universo con uno schiocco."]},
    {"target": "BATMAN", "indizi": ["DC Comics / Justice League", "Nessun potere, gadget, veicoli e arti marziali", "Il cavaliere oscuro di Gotham nato dal trauma d'infanzia in un vicolo."]},
    {"target": "SUPERMAN", "indizi": ["DC Comics / Justice League", "Sguardo laser, volo, superforza ed invulnerabilità", "L'ultimo figlio del pianeta Krypton cresciuto in Kansas."]},
    {"target": "WONDER WOMAN", "indizi": ["DC Comics / Justice League", "Lasso della verità e bracciali antiproiettile", "La principessa amazzone dell'isola nascosta di Temiscira."]},
    {"target": "FLASH", "indizi": ["DC Comics / Justice League", "Supervelocità e attraversamento della materia", "L'uomo più veloce della terra connesso alla forza della velocità."]},
    {"target": "AQUAMAN", "indizi": ["DC Comics / Justice League", "Respirazione acquatica e controllo della fauna marina", "Il re del regno sottomarino di Atlantide."]},
    {"target": "LANTERNA VERDE", "indizi": ["DC Comics / Justice League", "Anello in grado di creare qualsiasi costrutto solido", "La sua forza di volontà alimenta l'anello verde del corpo spaziale."]},
    {"target": "CYBORG", "indizi": ["DC Comics / Justice League", "Corpo meccatronico e cannoni al plasma", "Il giovane atleta il cui corpo è stato salvato con la tecnologia aliena."]},
    {"target": "JOKER", "indizi": ["DC Comics", "Risata isterica, trucco da pagliaccio ed acido", "Il principe pagliaccio del crimine e nemesi del pipistrello."]},
    {"target": "HARLEY QUINN", "indizi": ["DC Comics", "Mazza da baseball e follia imprevedibile", "L'ex psichiatra innamoratasi del pazzo di Gotham."]},
    {"target": "ROBIN", "indizi": ["DC Comics", "Agilità acrobatica e bastoni da combattimento", "Il primo giovane aiutante del pipistrello cresciuto al circo."]},
    {"target": "FRECCIA VERDE", "indizi": ["DC Comics / Justice League", "Infallibile con arco e frecce tecnologiche", "Il miliardario sopravvissuto ad un'isola deserta per diventare vigilante."]},
    {"target": "SUPERGIRL", "indizi": ["DC Comics", "Stessi poteri del cugino sotto il sole giallo", "La cugina kryptoniana arrivata sulla Terra in ritardo."]},
    {"target": "SHAZAM", "indizi": ["DC Comics", "Poteri di sei divinità pronunciando una sola parola", "Un ragazzino che si trasforma in un eroe adulto fulmineo."]},
    {"target": "MARTIAN MANHUNTER", "indizi": ["DC Comics / Justice League", "Mutaforma, telepatia e pelle verde", "L'ultimo superstite del pianeta rosso giunto sulla Terra."]},
    {"target": "CATWOMAN", "indizi": ["DC Comics", "Tuta in pelle, frusta ed agilità felina", "La ladra felina indecisa tra il crimine e l'amore per il pipistrello."]},
    {"target": "LEX LUTHOR", "indizi": ["DC Comics", "Genio intellettuale, armature ed ego smisurato", "Il miliardario nemico numero uno dell'eroe in mantello rosso."]},
    {"target": "DARKSEID", "indizi": ["DC Comics", "Raggi Omega dagli occhi e forza titanica", "Il tiranno del pianeta Apokolips alla ricerca dell'equazione dell'anti-vita."]},
    {"target": "DEATHSTROKE", "indizi": ["DC Comics", "Armi da fuoco, spada e maschera divisa in due colori", "Il mercenario potenziato con un occhio solo."]},
    {"target": "PINGUINO", "indizi": ["DC Comics", "Ombrelli modificati e controllo dei bassifondi", "Il boss malavitoso dal fisico tarchiato di Gotham."]},
    {"target": "ENIGMISTA", "indizi": ["DC Comics", "Indovinelli complessi e bastone a punto di domanda", "L'ossessionato dal mettere alla prova l'intelletto del detective."]},
    {"target": "MILES MORALES", "indizi": ["Marvel", "Ragnatele invisibili e scarica venefica", "Il ragazzo di Brooklyn diventato il nuovo ragno di quartiere."]},
    {"target": "SILVER SURFER", "indizi": ["Marvel", "Tavola da stiro cosmica d'argento", "L'araldo d'argento che viaggia nello spazio per Galactus."]},
    {"target": "GHOST RIDER", "indizi": ["Marvel", "Teschio fiammeggiante e catena dell'inferno", "Il motociclista che ha venduto l'anima al diavolo."]},
    {"target": "MOON KNIGHT", "indizi": ["Marvel", "Armi a forma di mezzaluna e personalità multiple", "Il mercenario resuscitato dal dio egizio della luna."]},
    {"target": "BLADE", "indizi": ["Marvel", "Katana da vampiro e siero speciale", "Il diurno mezzo uomo e mezzo vampiro che caccia le creature della notte."]},
    {"target": "SHE-HULK", "indizi": ["Marvel", "Pelle verde ed avvocato di successo", "La cugina del gigante verde che mantiene la sua mente in forma gigante."]},
    {"target": "SHANG-CHI", "indizi": ["Marvel", "Dieci anelli magici dalle braccia", "Il maestro delle arti marziali cresciuto dal padre immortale."]},
    {"target": "HAWKGIRL", "indizi": ["DC Comics / Justice League", "Ali piumate e mazza in metallo Nth", "La guerriera alata reincarnata dal passato."]},
    {"target": "JOHN CONSTANTINE", "indizi": ["DC Comics / Vertigo", "Sigaretta perenne ed esorcismo occulto", "Il detective dell'occulto dal trench nocciola."]},
    {"target": "RORSCHACH", "indizi": ["DC / Watchmen", "Maschera con macchie nere d'inchiostro mutanti", "Il vigilante paranoico del gruppo di Watchmen."]},
    {"target": "DR MANHATTAN", "indizi": ["DC / Watchmen", "Corpo azzurro brillante e controllo della materia", "L'essere supremo diventato quasi un dio dopo un incidente nucleare."]},
    {"target": "POISON IVY", "indizi": ["DC Comics", "Controllo delle piante e tossine velenose", "La botanica di Gotham che protegge la natura a spese degli umani."]},
    {"target": "BANE", "indizi": ["DC Comics", "Tubi di siero Venom che aumentano la massa muscolare", "Il colosso che spezzò la schiena al pipistrello."]},
    {"target": "DUE FACCE", "indizi": ["DC Comics", "Moneta sfregiata lanciata per decidere il destino", "L'ex procuratore distrettuale dal volto deturpato a metà."]},
    {"target": "MR FREEZE", "indizi": ["DC Comics", "Tuta criogenica e pistola congelante", "Lo scienziato che vuole salvare la moglie ibernata."]},
    {"target": "SPAVENTAPASSERI", "indizi": ["DC Comics", "Tossina della paura e maschera in canapa", "L'ex professore di psicologia che usa il terrore sulle sue vittime."]},
    {"target": "CABLE", "indizi": ["Marvel / X-Men", "Occhio cibernetico e viaggi nel tempo", "Il figlio del Ciclope venuto dal futuro per evitare la catastrofe."]},
    {"target": "GAMBIT", "indizi": ["Marvel / X-Men", "Carte da gioco caricate di energia cinetica", "Il mutante ladro di New Orleans dal trench marrone."]},
    {"target": "NIGHTCRAWLER", "indizi": ["Marvel / X-Men", "Teletrasporto con scia di fumo di zolfo e coda", "Il mutante blu dall'aspetto demoniaco ma dal cuore pio."]},
    {"target": "COLOSSO", "indizi": ["Marvel / X-Men", "Pelle in metallo organico indistruttibile", "Il gigante russo dal cuore buono che si trasforma in acciaio."]},
    {"target": "HELLBOY", "indizi": ["Dark Horse", "Mano destra del destino in pietra e corna limate", "Il demone rosso evocato dai nazisti ma allevato dagli americani."]},
    {"target": "SPAWN", "indizi": ["Image Comics", "Mantello vivente rosso e catene ferrate", "Il soldato mandato all'inferno ritornato come demone vendicatore."]},
    {"target": "OMNI-MAN", "indizi": ["Image Comics / Invincible", "Mantello rosso e baffo, invulnerabile", "Il padre alieno apparentemente eroe ma conquistatore spietato."]},
    {"target": "INVINCIBLE", "indizi": ["Image Comics", "Tuta gialla e blu, superforza e volo", "Il ragazzo metà umano e metà alieno Viltrumita."]},
    {"target": "PATRIOTA", "indizi": ["The Boys", "Mantello con la bandiera USA e occhi laser", "Il leader dei sette egocentrico e psicopatico."]},
    {"target": "BILLY BUTCHER", "indizi": ["The Boys", "Impermeabile nero e odio cieco per i Super", "Il leader dei vigilanti che vuole sterminare tutti gli eroi corrotti."]},
    {"target": "THE MASK", "indizi": ["Dark Horse", "Maschera di legno verde porta-follia", "L'oggetto magico che trasforma chi lo indossa in un cartone vivente verde."]},
    {"target": "JUDGE DREDD", "indizi": ["2000 AD", "Casco che copre il viso e pistola Legislatore", "Il giudice, giuria e giustiziere della metropoli del futuro."]},
    {"target": "IL CORVO", "indizi": ["Caliber Comics", "Trucco bianco e nero e rigenerazione", "Il musicista resuscitato per vendicare l'uccisione della sua amata."]},
    {"target": "STARFIRE", "indizi": ["DC Comics / Teen Titans", "Raggi solari e pelle arancione", "La principessa aliena del pianeta Tamaran."]},
    {"target": "CORVINA", "indizi": ["DC Comics / Teen Titans", "Magia oscura e mantello viola con cappuccio", "La figlia del demone interdimensionale Trigon."]},
    {"target": "BEBE BESTIA", "indizi": ["DC Comics / Teen Titans", "Trasformazione in qualsiasi animale ma di colore verde", "Il ragazzino verde capace di diventare dal moscerino al T-Rex."]},
    {"target": "MISTER FANTASTICO", "indizi": ["Marvel / I Fantastici Quattro", "Corpo totalmente allungabile ed elastico", "Il geniale scienziato leader del quartetto cosmico."]},
    {"target": "DONNA INVISIBILE", "indizi": ["Marvel / I Fantastici Quattro", "Campo di forza e invisibilità", "La colonna portante della prima famiglia dei fumetti."]},
    {"target": "TORCIA UMANA", "indizi": ["Marvel / I Fantastici Quattro", "Corpo ricoperto di fiamme e volo", "Fiamma! è il suo celebre urlo prima di prendere quota."]},
    {"target": "LA COSA", "indizi": ["Marvel / I Fantastici Quattro", "Corpo di roccia arancione", "È tempo di distruzione! gridato dal gigante di pietra."]},
    {"target": "GALACTUS", "indizi": ["Marvel", "Elmo cosmico gigante e divoratore di pianeti", "L'entità cosmica che si nutre dell'energia dei mondi."]},
    {"target": "DOTTOR DESTINO", "indizi": ["Marvel", "Maschera di ferro e mantello verde", "Il sovrano dittatore dello stato di Latveria."]},
    {"target": "KINGPIN", "indizi": ["Marvel / DC", "Stazza imponente e bastone da passeggio", "Il boss supremo della malavita newyorkese vestito di bianco."]},
    {"target": "MISTER SINISTRO", "indizi": ["Marvel / X-Men", "Rombo rosso sulla fronte e genetica avanzata", "Lo scienziato ossessionato dalla linea di sangue dei mutanti."]},
    {"target": "BLACK ADAM", "indizi": ["DC Comics", "Fulmine dorato sul petto e costume nero", "L'antico campione egizio dai poteri simili a Shazam."]},
    {"target": "ATROCITUS", "indizi": ["DC Comics", "Anello rosso della rabbia", "Il leader del corpo delle lanterne rosse alimentato dall'odio."]},
    {"target": "BRAINIAC", "indizi": ["DC Comics", "Nave a forma di teschio e dischi sulla testa", "Il collezionista alieno che rimpicciolisce ed imbottiglia le città."]},
    {"target": "ZATANNA", "indizi": ["DC Comics", "Cilindro da illusionista ed incantesimi pronunciati al contrario", "La maga che attiva la magia parlando all'indietro."]},
    {"target": "STATIC SHOCK", "indizi": ["DC Comics", "Controllo dell'elettricità e disco volante", "Il liceale che sposta e controlla le cariche elettriche."]},
    {"target": "ATOM", "indizi": ["DC Comics", "Rimpicciolimento a livello subatomico", "Lo scienziato capace di viaggiare attraverso le linee telefoniche."]},
    {"target": "SENTRY", "indizi": ["Marvel", "Cintura con la S e la forza di un milione di soli esplosivi", "L'eroe dimenticato dal mondo che nasconde un'ombra oscura dentro di sé."]},
    {"target": "NOVA", "indizi": ["Marvel", "Casco dorato con stella e forza della forza Nova", "Il membro del corpo di polizia intergalattico."]},
    {"target": "IKARIS", "indizi": ["Marvel", "Occhi laser e volo cosmico", "Il leader degli esseri immortali inviati dai Celestiali sulla Terra."]},
    {"target": "MORBIUS", "indizi": ["Marvel", "Aspetto da pipistrello umano e sete di sangue", "Lo scienziato affetto da una rara malattia del sangue diventato un vampiro vivente."]}
]

# --- DATABASE 100 PAESI DEL MONDO ---
QUIZ_PAESI_DB = [
    {"target": "ITALIA", "indizi": ["Europa", "Roma", "La patria della pizza, del Rinascimento e del Colosseo."]},
    {"target": "FRANCIA", "indizi": ["Europa", "Parigi", "Famosa per la Torre Eiffel, i formaggi pregiati ed i musei d'arte d'élite."]},
    {"target": "SPAGNA", "indizi": ["Europa", "Madrid", "Terra della paella, del flamenco e delle serate di movida."]},
    {"target": "GERMANIA", "indizi": ["Europa", "Berlino", "Famosa per l'Oktoberfest, i bratwurst e le autostrade senza limiti di velocità."]},
    {"target": "REGNO UNITO", "indizi": ["Europa", "Londra", "La patria del tè delle cinque, dei bus rossi a due piani e della monarchia."]},
    {"target": "GRECIA", "indizi": ["Europa", "Atene", "Culla della democrazia antica, dei templi sull'Acropoli e delle isole dalle case bianche e blu."]},
    {"target": "PAESI BASSI", "indizi": ["Europa", "Amsterdam", "Famosi per i mulini a vento, i campi di tulipani e le biciclette sui canali."]},
    {"target": "PORTOGALLO", "indizi": ["Europa", "Lisbona", "Terra del Fado, dei famosi dolcetti al cream (Pastéis) e del punto più ad ovest d'Europa."]},
    {"target": "SVIZZERA", "indizi": ["Europa", "Berna", "Famosa per gli orologi di precisione, la cioccolata raffinata e la neutralità storica."]},
    {"target": "AUSTRIA", "indizi": ["Europa", "Vienna", "Patria della musica classica di Mozart, dei valzer e della torta Sacher."]},
    {"target": "BELGIO", "indizi": ["Europa", "Bruxelles", "Famoso per i waffle croccanti, le birre artigianali, il cioccolato e i fumetti."]},
    {"target": "IRLANDA", "indizi": ["Europa", "Dublino", "L'isola di trifogli verdi, della festa di San Patrizio e della birra scura Stout."]},
    {"target": "SVEZIA", "indizi": ["Europa", "Stoccolma", "Patria del gruppo pop ABBA, dei mobili da montare a casa e delle polpette di carne."]},
    {"target": "NORVEGIA", "indizi": ["Europa", "Oslo", "Famosa per i maestosi fiordi scavati dai ghiacciai e le luci dell'aurora boreale."]},
    {"target": "DANIMARCA", "indizi": ["Europa", "Copenaghen", "Patria dei mattoncini colorati giocattolo e della sirenetta affacciata sul porto."]},
    {"target": "FINLANDIA", "indizi": ["Europa", "Helsinki", "Terra dei mille laghi, della sauna tradizionale e del villaggio di Babbo Natale."]},
    {"target": "POLONIA", "indizi": ["Europa", "Varsavia", "Patria di Papa Giovanni Paolo II, di Chopin e dei famosi ravioli bolliti (Pierogi)."]},
    {"target": "REPUBBLICA CECA", "indizi": ["Europa", "Praga", "Famosa per le piazze medievali, i castelli fiabeschi e il consumo record di birra."]},
    {"target": "UNGHERIA", "indizi": ["Europa", "Budapest", "Terra del gulash speziato e dei celebri complessi termali lungo il Danubio."]},
    {"target": "ROMANIA", "indizi": ["Europa", "Bucarest", "Famosa per le foreste della Transilvania e le leggende legate al re dei vampiri."]},
    {"target": "UCRAINA", "indizi": ["Europa", "Kiev", "Patria del piatto di zuppa di barbabietole (Borscht) e delle immense distese di girasoli."]},
    {"target": "RUSSIA", "indizi": ["Europa / Asia", "Mosca", "Famosa per le piazze d'inverno, la vodka, i colbacco e le cattedrali a cupole colorate."]},
    {"target": "ISLANDA", "indizi": ["Europa", "Reykjavík", "La terra del ghiaccio e del fuoco, famosa per i geyser e i vulcani attivi."]},
    {"target": "CROAZIA", "indizi": ["Europa", "Zagabria", "Famosa per i parchi naturali di laghi e le meravigliose coste frastagliate sull'Adriatico."]},
    {"target": "TURCHIA", "indizi": ["Europa / Asia", "Ankara", "Terra del caffè forte, dei bagni turchi e del passaggio tra i due continenti."]},
    {"target": "ALBANIA", "indizi": ["Europa", "Tirana", "Nazione delle aquile, famosa per i bunker diffusi e le spiagge incontaminate della riviera."]},
    {"target": "SERBIA", "indizi": ["Europa", "Belgrado", "Cuore dei Balcani sul Danubio, famosa per la vita notturna sui fiumi e l'ospitalità."]},
    {"target": "MALTA", "indizi": ["Europa", "La Valletta", "Piccola isola al centro del Mediterraneo ricca di storia dei cavalieri e mare cristallino."]},
    {"target": "CIPRO", "indizi": ["Europa", "Nicosia", "L'isola mitologica nascita della dea Venere, divisa da un confine al centro della capitale."]},
    {"target": "SLOVACCHIA", "indizi": ["Europa", "Bratislava", "Nazione dell'Europa centrale famosa per i suoi numerosi castelli medievali e le montagne Tatra."]},
    {"target": "GIAPPONE", "indizi": ["Asia", "Tokyo", "Patria del sushi, degli anime, dei ciliegi in fiore e dei treni proiettile ad alta velocità."]},
    {"target": "CINA", "indizi": ["Asia", "Pechino", "Famosa per la Grande Muraglia, la cittadella imperiale e i panda giganti."]},
    {"target": "INDIA", "indizi": ["Asia", "Nuova Delhi", "Famosa per il mausoleo di marmo bianco, il curry spunzo, lo yoga e il fiume Gange."]},
    {"target": "COREA DEL SUD", "indizi": ["Asia", "Seul", "Famosa per la musica K-Pop, le serie drama e il piatto fermentato Kimchi."]},
    {"target": "THAILANDIA", "indizi": ["Asia", "Bangkok", "La terra dei sorrisi, dei templi dorati del Buddha e dello street food speziato."]},
    {"target": "VIETNAM", "indizi": ["Asia", "Hanoi", "Famoso per i cappelli di paglia conici, la baia di isole calcaree e le zuppe Pho."]},
    {"target": "INDONESIA", "indizi": ["Asia", "Giacarta", "L'immenso arcipelago famoso per l'isola di Bali, i vulcani ed i draghi di Komodo."]},
    {"target": "FILIPPINE", "indizi": ["Asia", "Manila", "Arcipelago tropicale di oltre 7.000 isole famoso per le barriere coralline ed i jeepney colorati."]},
    {"target": "MALESIA", "indizi": ["Asia", "Kuala Lumpur", "Famosa per le torri gemelle più alte del mondo ed il mix di culture e foreste pluviali."]},
    {"target": "SINGAPORE", "indizi": ["Asia", "Singapore", "La città-stato futuristica famosa per i giardini con super-alberi e la massima pulizia."]},
    {"target": "MALDIVE", "indizi": ["Asia", "Malé", "Famoso paradiso tropicale composto da atolli corallini e bungalow palafitta sull'acqua."]},
    {"target": "NEPAL", "indizi": ["Asia", "Katmandu", "La terra delle vette dell'Himalaya, dell'Everest e delle bandiere di preghiera colorate."]},
    {"target": "SRI LANKA", "indizi": ["Asia", "Sri Jayawardenepura Kotte", "L'isola a forma di lacrima famosa per le piantagioni di tè Ceylon e gli elefanti selvatici."]},
    {"target": "EMIRATI ARABI UNITI", "indizi": ["Asia", "Abu Dhabi", "Famosi per i grattacieli futuristici nel deserto, il lusso sfrenato e la torre più alta del mondo."]},
    {"target": "ARABIA SAUDITA", "indizi": ["Asia", "Riad", "Il regno del deserto custodiente i luoghi più sacri dell'Islam e le città moderne in espansione."]},
    {"target": "QATAR", "indizi": ["Asia", "Doha", "Ricco stato peninsulare del Golfo, ospite dei mondiali di calcio del 2022 nel deserto."]},
    {"target": "ISRAELE", "indizi": ["Asia", "Gerusalemme", "Famoso per la Città Santa per tre grandi religioni e le acque ipersalate del Mar Morto."]},
    {"target": "GIORDANIA", "indizi": ["Asia", "Amman", "Terra del deserto del Wadi Rum e dell'antica città scolpita nella roccia rosa (Petra)."]},
    {"target": "LIBANO", "indizi": ["Asia", "Beirut", "Famoso per gli alberi di cedro sulla bandiera ed la rinomata cucina mediterranea mediorientale."]},
    {"target": "IRAN", "indizi": ["Asia", "Teheran", "L'antica Persia famosa per i tappeti pregiati, le moschee di piastrelle blu e la poesia."]},
    {"target": "PAKISTAN", "indizi": ["Asia", "Islamabad", "Nazione asiatica dominata dalle imponenti catene del Karakoram e dal picco K2."]},
    {"target": "MONGOLIA", "indizi": ["Asia", "Ulan Bator", "La terra delle steppe sconfinate, dei nomadi, delle tende ger ed i cavalli selvatici."]},
    {"target": "KAZAKISTAN", "indizi": ["Asia", "Astana", "L'immenso stato transcontinental dell'Asia centrale ricco di risorse e steppe sconfinate."]},
    {"target": "CAMBOGIA", "indizi": ["Asia", "Phnom Penh", "Famosa per l'immenso complesso di templi religiosi nella giungla (Angkor Wat)."]},
    {"target": "UZBEKISTAN", "indizi": ["Asia", "Tashkent", "Cuore dell'antica Via della Seta famoso per le piazze turchesi di Samarcanda."]},
    {"target": "STATI UNITI", "indizi": ["America del Nord", "Washington D.C.", "La patria del fast food, dei film di Hollywood, dei grattacieli e delle 50 stelle sulla bandiera."]},
    {"target": "CANADA", "indizi": ["America del Nord", "Ottawa", "Famoso per le foglie d'acero, lo sciroppo dolce, le cascate del Niagara ed il ghiaccio."]},
    {"target": "MESSICO", "indizi": ["America del Nord", "Città del Messico", "Patria dei taco, del tequila, della musica mariachi e delle piramidi Maya."]},
    {"target": "CUBA", "indizi": ["America del Nord (Caraibi)", "L'Avana", "L'isola dei sigari fatti a mano, delle auto d'epoca colorate ed il ritmo della salsa."]},
    {"target": "GIAMAICA", "indizi": ["America del Nord (Caraibi)", "Kingston", "Patria della musica Reggae, del velocista Usain Bolt e dei colori verde, giallo e nero."]},
    {"target": "REPUBBLICA DOMINICANA", "indizi": ["America del Nord (Caraibi)", "Santo Domingo", "Famosa per le spiagge caraibiche di sabbia bianca, il merengue ed i resort."]},
    {"target": "BAHAMAS", "indizi": ["America del Nord (Caraibi)", "Nassau", "Arcipelago caraibico famoso per il mare trasparente e le maialini che nuotano in spiaggia."]},
    {"target": "PANAMA", "indizi": ["America del Nord (Centramerica)", "Città di Panama", "Famoso per l'ingegneristico canale artificiale che collega i due grandi oceani."]},
    {"target": "COSTA RICA", "indizi": ["America del Nord (Centramerica)", "San José", "Nazione pioniera dell'ecoturismo, celebre per la biodiversità e lo stile di vita Pura Vida."]},
    {"target": "GUATEMALA", "indizi": ["America del Nord (Centramerica)", "Città del Guatemala", "Terra dai vulcani attivi, mercati indigeni colorati ed antiche rovine Maya."]},
    {"target": "HONDURAS", "indizi": ["America del Nord (Centramerica)", "Tegucigalpa", "Nazione centroamericana famosa per le rovine Maya di Copán ed il barriera corallina."]},
    {"target": "HAITI", "indizi": ["America del Nord (Caraibi)", "Port-au-Prince", "Prima repubblica nera indipendente al mondo, famosa per la vivace arte caraibica."]},
    {"target": "EL SALVADOR", "indizi": ["America del Nord (Centramerica)", "San Salvador", "Piccola nazione costiera famosa tra i surfer per le onde perfette ed i vulcani."]},
    {"target": "NICARAGUA", "indizi": ["America del Nord (Centramerica)", "Managua", "La terra dei laghi e dei vulcani, famosa per il vulcano dove si pratica il volcano boarding."]},
    {"target": "BARBADOS", "indizi": ["America del Nord (Caraibi)", "Bridgetown", "Isola caraibica patria del rum e della cantante pop globale Rihanna."]},
    {"target": "BRASILE", "indizi": ["America del Sud", "Brasilia", "La patria del carnevale di Rio, del calcio baiano, della samba e della foresta Amazzonica."]},
    {"target": "ARGENTINA", "indizi": ["America del Sud", "Buenos Aires", "Terra del ballo del tango, della carne asado, della Pampa e di Maradona e Messi."]},
    {"target": "COLOMBIA", "indizi": ["America del Sud", "Bogotà", "Famosa per il caffè di altissima qualità, le smeraldi e la musica cumbia."]},
    {"target": "PERU", "indizi": ["America del Sud", "Lima", "Patria dell'antica civiltà Inca, del piatto marinato Ceviche e della fortezza tra le montagne."]},
    {"target": "CILE", "indizi": ["America del Sud", "Santiago del Cile", "Lo stretto paese lunghissimo tra la catena delle Ande e l'Oceano Pacifico."]},
    {"target": "VENEZUELA", "indizi": ["America del Sud", "Caracas", "Famoso per la cascata più alta del mondo (Salto Angel) ed i successi nei concorsi di bellezza."]},
    {"target": "URUGUAY", "indizi": ["America del Sud", "Montevideo", "Piccola nazione sudamericana famosa per il mate, le spiagge ed i due titoli mondiali di calcio antichi."]},
    {"target": "ECUADOR", "indizi": ["America del Sud", "Quito", "Deve il nome alla linea immaginaria che divide la Terra e possiede le isole Galápagos."]},
    {"target": "BOLIVIA", "indizi": ["America del Sud", "Sucre / La Paz", "Famosa per il deserto di sale più grande del mondo (Salar de Uyuni) e la cultura andina."]},
    {"target": "PARAGUAY", "indizi": ["America del Sud", "Asunción", "Nazione dell'interno sudamericano famosa per la lingua guaraní ed l'infuso di erba mate freddo."]},
    {"target": "SURINAME", "indizi": ["America del Sud", "Paramaribo", "Unica nazione dell'America del Sud in cui la lingua ufficiale è l'olandese."]},
    {"target": "GUYANA", "indizi": ["America del Sud", "Georgetown", "Piccolo stato coperto per gran parte da foreste vergini e la maestosa cascata Kaieteur."]},
    {"target": "EGITTO", "indizi": ["Africa", "Il Cairo", "La terra dei faraoni, delle piramidi di pietra, della Sfinge e del lungo fiume Nilo."]},
    {"target": "MAROCCO", "indizi": ["Africa", "Rabat", "Famoso per i mercati delle spezie nelle medine, il tè alla menta ed il deserto del Sahara."]},
    {"target": "SUDAFRICA", "indizi": ["Africa", "Pretoria", "Nazione arcobaleno celebre per i safari nei parchi, il Capo di Buona Speranza ed i tre capitali."]},
    {"target": "KENYA", "indizi": ["Africa", "Nairobi", "Famoso per le corse dei maratoneti, la grande migrazione di animali ed il monte innevato."]},
    {"target": "TANZANIA", "indizi": ["Africa", "Dodoma", "Terra della vetta più alta d'Africa (Kilimangiaro), del Parco Serengeti e delle spiagge di Zanzibar."]},
    {"target": "NIGERIA", "indizi": ["Africa", "Abuja", "Il gigante demografico africano, famoso per l'industria cinematografica Nollywood e la musica Afrobeats."]},
    {"target": "SENEGAL", "indizi": ["Africa", "Dakar", "Famoso per la leggendaria gara di rally del passato, l'ospitalità Teranga ed il piatto di riso e pesce."]},
    {"target": "MADAGASCAR", "indizi": ["Africa", "Antananarivo", "L'enorme isola oceano-africana famosa per gli alberi baobab ed i lemuri unici al mondo."]},
    {"target": "ETIOPIA", "indizi": ["Africa", "Addis Abeba", "Considerata la culla dell'umanità e patria d'origine della pianta del caffè."]},
    {"target": "TUNISIA", "indizi": ["Africa", "Tunisi", "Famosa per le rovine dell'antica Cartagine, i villaggi bianchi e blu ed il deserto set cinematografico."]},
    {"target": "ALGERIA", "indizi": ["Africa", "Algeri", "Il Paese più grande d'Africa, dominato per oltre l'80% dalle dune del deserto del Sahara."]},
    {"target": "GHANA", "indizi": ["Africa", "Accra", "Famoso per essere stato la Costa d'Oro, grande produttore di cacao ed il tessuto colorato Kente."]},
    {"target": "CAMERUN", "indizi": ["Africa", "Yaoundé", "Nazione detta Africa in miniatura per la varietà di paesaggi e per le Ioni Indomabili del calcio."]},
    {"target": "COSTA D'AVORIO", "indizi": ["Africa", "Yamoussoukro", "Il maggior produttore mondiale di cacao, famoso per le foreste tropicali e grandi campioni di calcio."]},
    {"target": "UGANDA", "indizi": ["Africa", "Kampala", "Detto La perla d'Africa da Churchill, celebre per i gorilla di montagna ed le sorgenti del Nilo."]},
    {"target": "AUSTRALIA", "indizi": ["Oceania", "Canberra", "La terra dei canguri, dei koala, del teatro dell'Opera di Sydney e del grande deserto Outback."]},
    {"target": "NUOVA ZELANDA", "indizi": ["Oceania", "Wellington", "Patria del popolo Maori, degli uccelli Kiwi, della squadra di rugby degli All Blacks ed i paesaggi del Signore degli Anelli."]},
    {"target": "FIJI", "indizi": ["Oceania", "Suva", "Arcipelago dell'Oceano Pacifico famoso per i fondali corallini, le camicie colorate ed il rito della bevanda Kava."]}
]

# --- DATABASE 100 ANIME & MANGA ---
QUIZ_ANIME_DB = [
    {"target": "DRAGON BALL", "indizi": ["Shonen / Anni '80-'90", "Sette sfere magiche e aure dorate", "Un guerriero con la coda di scimmia cerca le sfere e si allena per diventare il più forte dell'universo."]},
    {"target": "ONE PIECE", "indizi": ["Shonen / Avventura", "Frutto del diavolo di gomma", "Un ragazzo dal cappello di paglia naviga con la sua ciurma per diventare il Re dei Pirati."]},
    {"target": "NARUTO", "indizi": ["Shonen / Ninja", "Volpe a nove code sigillata nella pancia", "Un ninja emarginato sogna di farsi accettare da tutti diventando Hokage del suo villaggio."]},
    {"target": "L'ATTACCO DEI GIGANTI", "indizi": ["Seinen / Dark Fantasy", "Mura giganti e dispositivo di manovra tridimensionale", "L'umanità vive reclusa per proteggersi da enormi mostri umanoidi che divorano le persone."]},
    {"target": "DEATH NOTE", "indizi": ["Thriller / Soprannaturale", "Quaderno nero della morte", "Uno studente prodigio trova un taccuino di uno Shinigami e inizia a giustiziare i criminali del mondo."]},
    {"target": "DEMON SLAYER", "indizi": ["Shonen / Azione", "Spada Nichirin e tecniche di respirazione", "Un ragazzo viaggia con la sorella trasformata in demone per trovare una cura e vendicare la famiglia."]},
    {"target": "MY HERO ACADEMIA", "indizi": ["Shonen / Supereroi", "Un potere unico tramandato di generazione in generazione", "In un mondo dove quasi tutti hanno superpoteri, un ragazzo nato normale studia nel liceo degli eroi."]},
    {"target": "JUJUTSU KAISEN", "indizi": ["Shonen / Dark Fantasy", "Dita maledette ingoiate e tecniche d'energia nera", "Un liceale ingoia un amuleto maledetto e si iscrive alla scuola di stregoneria per contenere un demone antico."]},
    {"target": "POKEMON", "indizi": ["Avventura / Fantastico", "Sfera Poké rossa e bianca", "Un allenatore di Biancavilla viaggia per tutte le regioni insieme alla sua fidata cavia elettrica gialla."]},
    {"target": "DETECTIVE CONAN", "indizi": ["Giallo / Mistero", "Veleno che rimpicciolisce il corpo", "Un celebre detective liceale torna bambino a causa di una sostanza chimica e risolve casi sotto falsa identità."]},
    {"target": "LUPIN III", "indizi": ["Azione / Commedia", "Giacca colorata e pistola Walther P38", "Il ladro gentiluomo più famoso del mondo sfugge continuamente all'ispettore Zenigata."]},
    {"target": "NEON GENESIS EVANGELION", "indizi": ["Mecha / Psicologico", "Robot giganti biologici guidati da piloti adolescenti", "Tre ragazzi pilotano enormi unità biomeccaniche per difendere la Terra dall'attacco degli 'Angeli'."]},
    {"target": "FULLMETAL ALCHEMIST", "indizi": ["Shonen / Fantasy", "Trasmutazione alchemica e braccio d'acciaio", "Due fratelli cercano la Pietra Filosofale per riavere i corpi persi dopo un rituale proibito."]},
    {"target": "HUNTER X HUNTER", "indizi": ["Shonen / Avventura", "Licenza da Hunter ed energia Nen", "Un ragazzino affronta un esame difficilissimo per trovare il padre diventato un celebre cacciatore."]},
    {"target": "TOKYO GHOUL", "indizi": ["Dark Fantasy / Horror", "Maschera da canino e tentacoli organici", "Dopo un trapianto d'organi, un universitario diventa un ibrido costretto a nutrirsi di carne umana."]},
    {"target": "SAILOR MOON", "indizi": ["Majokko / Shojo", "Spettro di luna e trasformazione con lo scettro", "Una studentessa goffa scopre di essere la guerriera della luna destinata a proteggere la Terra."]},
    {"target": "CREAMY", "indizi": ["Majokko / Anni '80", "Bacchetta magica e doppia identità da cantante", "Una bambina riceve il potere di trasformarsi per un anno in una bellissima popstar di successo."]},
    {"target": "HOLLY E BENJI", "indizi": ["Sportivo / Calcio", "Campi di gioco infiniti e tiri ad effetto", "Un prodigio del pallone sogna di portare la propria nazionale ai Mondiali partendo dal torneo scolastico."]},
    {"target": "KEN IL GUERRIERO", "indizi": ["Azione / Post-apocalittico", "Punti di pressione che fanno esplodere gli avversari", "Il successore di una scuola marziale vaga in un mondo devastato dalla guerra atomica."]},
    {"target": "I CAVALIERI DELLO ZODIACO", "indizi": ["Shonen / Mitologico", "Armature d'oro legate alle costellazioni", "Guerrieri devoti alla dea Atena combattono le forze del male bruciando il proprio cosmo."]},
    {"target": "ONE PUNCH MAN", "indizi": ["Azione / Satirico", "Mantello bianco e pugno unico risolutivo", "Un eroe per hobby è così forte da sconfiggere qualsiasi mostro con un solo colpo, cadendo nella noia."]},
    {"target": "BLEACH", "indizi": ["Shonen / Soprannaturale", "Spada Zanpakuto e veste nera da Shinigami", "Un liceale in grado di vedere gli spiriti riceve i poteri di un Mietitore d'Anime."]},
    {"target": "CHAINSAW MAN", "indizi": ["Dark Fantasy / Splatter", "Motosega che esce dalla testa e dalle braccia", "Un ragazzo fuso con il suo diavolo-cane si arruola nella caccia ai demoni per la pubblica sicurezza."]},
    {"target": "JOJO", "indizi": ["Shonen / Azione", "Evocazioni spirituali chiamate Stand", "Le generazioni di una specifica stirpe familiare combattono minacce soprannaturali."]},
    {"target": "SLAM DUNK", "indizi": ["Sportivo / Basket", "Capelli rossi da teppista", "Un teppista di liceo si iscrive al club di pallacanestro solo per impressionare la ragazza che gli piace."]},
    {"target": "YU-GI-OH!", "indizi": ["Shonen / Giochi", "Carte da gioco magiche e Puzzle del Millennio", "Un ragazzo ricompone un antico manufatto egizio ospitando lo spirito di un faraone."]},
    {"target": "FAIRY TAIL", "indizi": ["Shonen / Fantasy", "Magia del drago di fuoco", "Una maga degli spiriti stellari si unisce alla gilda di maghi più casinista del regno."]},
    {"target": "INUYASHA", "indizi": ["Fantasy / Storico", "Spada ricavata da una zanna e sfera dei quattro spiriti", "Una liceale finisce in un pozzo magico e viaggia nel Giappone feudale insieme a un mezzo demone-cane."]},
    {"target": "CODE GEASS", "indizi": ["Mecha / Politico", "Occhio rosso con il potere del comando assoluto", "Un principe esiliato riceve un potere mentale e guida una ribellione in maschera."]},
    {"target": "SWORD ART ONLINE", "indizi": ["Sci-Fi / VRMMO", "Visore di realtà virtuale mortale e due spade", "Migliaia di giocatori rimangono intrappolati in un videogioco dove morire nel gioco significa morire davvero."]},
    {"target": "HAIKYUU!!", "indizi": ["Sportivo / Pallavolo", "Schiacciata ad altissima velocità e maglia nera numero 10", "Un piccolo schiacciatore e un alzatore geniale fanno rinascere la squadra del loro liceo."]},
    {"target": "STEINS;GATE", "indizi": ["Sci-Fi / Thriller", "Microonde a banane e messaggi nel tempo", "Uno scienziato pazzo inventa per sbaglio un sistema per inviare messaggi nel passato."]},
    {"target": "THE SEVEN DEADLY SINS", "indizi": ["Shonen / Fantasy", "Marchi animali sul corpo e oste di una taverna volante", "La principessa di un regno cerca i sette cavalieri leggendari per salvare il trono."]},
    {"target": "MONSTER", "indizi": ["Thriller / Psicologico", "Chirurgo cerebrale e gemelli", "Un neurochirurgo salva la vita a un bambino che anni dopo si rivela essere un sociopatico spietato."]},
    {"target": "BERSERK", "indizi": ["Dark Fantasy / Seinen", "Spada gigante chiamata Ammazzadraghi e marchio del sacrificio", "Un guerriero nero vaga in cerca di vendetta contro il suo ex migliore amico diventato demone."]},
    {"target": "RANMA 1/2", "indizi": ["Commedia / Arti marziali", "Acqua fredda e calda per cambiare sesso", "Un ragazzo maledetto si trasforma in una ragazza se bagnato con acqua fredda."]},
    {"target": "OCCHI DI GATTO", "indizi": ["Azione / Commedia", "Biglietto da visita rosa lasciata sui luoghi del furto", "Tre sorelle gestiscono un bar di giorno e rubano opere d'arte di notte per ritrovare il padre."]},
    {"target": "E QUASI MAGIA JOHNNY", "indizi": ["Shojo / Commedia", "Cappello rosso e poteri ESP", "Un ragazzo dai poteri telecinetici si ritrova al centro di un triangolo amoroso."]},
    {"target": "INAZUMA ELEVEN", "indizi": ["Sportivo / Fantastico", "Tecniche speciali di calcio elementari", "Il nipote di un celebre portiere recluta calciatori dai poteri fantastici."]},
    {"target": "FATE/STAY NIGHT", "indizi": ["Fantasy / Azione", "Evocazione di spiriti eroici storici per il Santo Graal", "Sette maghi combattono insieme ai loro spiriti guerrieri per ottenere un calice magico."]},
    {"target": "VINLAND SAGA", "indizi": ["Storico / Drammatico", "Pugnali da vichingo", "Un giovane guerriero norreno cerca di vendicare il padre ucciso arruolandosi nella banda dell'assassino."]},
    {"target": "SPY X FAMILY", "indizi": ["Commedia / Spy", "Famiglia fittizia con spia, assassina e telepath", "Una spia crea una falsa famiglia con un'assassina e una bambina che legge nel pensiero."]},
    {"target": "TOKYO REVENGERS", "indizi": ["Azione / Viaggi nel tempo", "Divisa da gang di motociclisti", "Un ragazzo torna indietro nel tempo ai tempi delle medie per salvare la sua ex fidanzata."]},
    {"target": "BLACK CLOVER", "indizi": ["Shonen / Fantasy", "Grimoire con trifoglio a cinque foglie e spada anti-magia", "Un ragazzo privo di potere magico riceve un libro misterioso e punta a diventare Imperatore Magico."]},
    {"target": "GINTAMA", "indizi": ["Commedia / Sci-Fi", "Spada di legno nel Giappone con invasori alieni", "Un samurai pigro gestisce un'agenzia di tuttofare in un'epoca feudale dominata da extraterrestri."]},
    {"target": "ASSASSINATION CLASSROOM", "indizi": ["Shonen / Commedia", "Polpo giallo gigante che insegna a scuola", "Gli studenti di una classe polverosa devono assassinare il loro strano professore alieno."]},
    {"target": "MOB PSYCHO 100", "indizi": ["Commedia / Soprannaturale", "Contatore percentuale dell'esplosione emotiva", "Un mediocre studente nasconde poteri psichici spaventosi che esplodono quando la rabbia arriva al 100%."]},
    {"target": "FIRE FORCE", "indizi": ["Shonen / Azione", "Piedi di fuoco ed abiti da vigile del fuoco", "Speciali vigili del fuoco combattono contro persone che prendono spontaneamente fuoco."]},
    {"target": "DR. STONE", "indizi": ["Sci-Fi / Avventura", "Formula chimica e pietrificazione globale", "Migliaia di anni dopo che l'umanità si è pietrificata, un ragazzo geniale usa la scienza per ricostruire la civiltà."]},
    {"target": "BLUE LOCK", "indizi": ["Sportivo / Calcio", "Struttura d'allenamento isolata per egoisti", "Trecento giovani attaccanti vengono reclusi in una prigione sportiva per creare il centravanti più egoista."]},
    {"target": "PARASYTE", "indizi": ["Horror / Sci-Fi", "Mano destra parlante che si trasforma in lame", "Parassiti alieni si insediano nei cervelli umani, ma uno si fonde solo con la mano del protagonista."]},
    {"target": "PSYCHO-PASS", "indizi": ["Distopico / Sci-Fi", "Pistola Dominator che misura l'indice di criminalità", "In un futuro controllato da un sistema informatico, i poliziotti arrestano le persone prima che commettano reati."]},
    {"target": "GURREN LAGANN", "indizi": ["Mecha / Sci-Fi", "Triangolo-occhiali da sole e trivella gigante", "Due fratelli adottivi scavano dalle caverne sotterranee fino alle stelle guidando un piccolo mecha."]},
    {"target": "KILL LA KILL", "indizi": ["Azione / Ecchi", "Uniformi scolastiche viventi fatte di fibre da combattimento", "Una studentessa armata di mezza forbice gigante cerca l'assassino del padre."]},
    {"target": "MADE IN ABYSS", "indizi": ["Fantasy / Drammatico", "Fischietto bianco ed un enorme voragine verticale", "Una ragazzina e un bambino-robot scendono nelle profondità di una voragine misteriosa."]},
    {"target": "DORORO", "indizi": ["Storico / Dark Fantasy", "Protesi di legno con lame nascoste", "Un giovane guerriero senza organi combatte contro cinquantadue demoni per riprendersi il corpo."]},
    {"target": "GTO", "indizi": ["Commedia", "Giacca di pelle e moto d'epoca", "Un ex teppista motociclista diventa insegnante di liceo applicando metodi non convenzionali."]},
    {"target": "COWBOY BEBOP", "indizi": ["Sci-Fi / Noir", "Navetta spaziale Swordfish e sigaretta accesa", "Un gruppo di cacciatori di taglie viaggia nello spazio a bordo di una vecchia astronave."]},
    {"target": "SAMURAI CHAMPLOO", "indizi": ["Storico / Hip-Hop", "Katana e mosse di breakdance", "Una ragazza ingaggia due samurai opposti per trovare un guerriero che profuma di girasoli."]},
    {"target": "FLCL", "indizi": ["Sci-Fi / Surrealista", "Vespa gialla e chitarra Rickenbacker usata come mazza", "Un ragazzo si ritrova un corno in testa dopo essere stato investito da una strana donna aliena."]},
    {"target": "TRIGUN", "indizi": ["Sci-Fi / Western", "Braccio meccanico trasformabile in cannone", "Un ricercato dalla taglia gigante viaggia in un pianeta deserto rifiutandosi di uccidere."]},
    {"target": "HELLSING", "indizi": ["Horror / Azione", "Pistole sproporzionate e cappello rosso ad tesa larga", "Un vampiro primordiale lavora per un'organizzazione britannica che stermina i mostri della notte."]},
    {"target": "DEVILMAN", "indizi": ["Horror / Dark Fantasy", "Trasformazione demoniaca mantenendo il cuore umano", "Un ragazzo puro si fonde con un demone supremo per combattere l'invasione delle creature infernali."]},
    {"target": "YU DEGLI SPETTRI", "indizi": ["Shonen / Azione", "Pistola dello spirito sparata dall'indice", "Un teppista muore per salvare un bambino e diventa un detective del mondo spirituale."]},
    {"target": "KENSHIN IL VAGABONDO", "indizi": ["Storico / Azione", "Spada con la lama invertita che non uccide", "Un ex assassino dell'era Meiji vaga per il Giappone proteggendo i deboli senza versare sangue."]},
    {"target": "CITY HUNTER", "indizi": ["Azione / Commedia", "Martellone da 100 tonnellate scagliato sulla testa", "Un investigatore privato infallibile con le armi non sa resistere al fascino delle belle donne."]},
    {"target": "MAISON IKKOKU", "indizi": ["Commedia / Romantico", "Grembiule da casa con pulcino", "Uno studente universitario perennemente bocciato si innamora della nuova giovane amministratrice."]},
    {"target": "CAPITAN HARLOCK", "indizi": ["Sci-Fi", "Benda sull'occhio, teschio sul petto ed astronave nera", "Un pirata spaziale solitario difende la Terra da una minaccia aliena a bordo dell'Arcadia."]},
    {"target": "GOLDRAKE", "indizi": ["Mecha / Anni '70", "Alabarda spaziale e maglio perforante", "Un principe alieno rifugiato sulla Terra pilota un gigantesco robot per difenderla dagli invasori."]},
    {"target": "MAZINGA Z", "indizi": ["Mecha / Anni '70", "Raggi fotonici dagli occhi e pugni a reazione", "Un ragazzo guida dentro la testa di un gigante d'acciaio costruito dal nonno."]},
    {"target": "DEVIL MAY CRY", "indizi": ["Azione / Fantasy", "Spada spadone e due pistole chiamate Ebony & Ivory", "Un cacciatore di demoni metà uomo e metà demone gestisce un'agenzia speciale."]},
    {"target": "BAKI", "indizi": ["Sportivo / Arti marziali", "Muscoli deformi e cicatrici sul corpo", "Un giovane lottatore si allena duramente per superare la forza brutale di suo padre."]},
    {"target": "KENGAN ASHURA", "indizi": ["Arti marziali / Seinen", "Scommesse finanziarie su tornei clandestini", "Grandi corporazioni risolvono le dispute d'affari ingaggiando gladiatori per tornei."]},
    {"target": "OVERLORD", "indizi": ["Isekai / Dark Fantasy", "Scheletro gigante in abiti da mago supremo", "Un giocatore rimane intrappolati nei panni del suo personaggio scheletrico e conquista il nuovo mondo."]},
    {"target": "RE:ZERO", "indizi": ["Isekai / Drammatico", "Abilità di rinascere dal punto di controllo dopo la morte", "Un ragazzo calato in un mondo fantasy scopre che ogni volta che muore torna indietro nel tempo."]},
    {"target": "NO GAME NO LIFE", "indizi": ["Isekai / Giochi", "Mondo guidato dai giochi da tavolo senza violenza", "Due fratelli campioni di e-sport vengono trasportati in un mondo dove ogni disputa si risolve giocando."]},
    {"target": "TENSEI SHITARA SLIME", "indizi": ["Isekai", "Gelatina blu mutaforma capace di assorbire abilità", "Un impiegato ucciso in strada si risveglia in un mondo magico sotto forma di una pallina gommosa."]},
    {"target": "THE RISING OF THE SHIELD HERO", "indizi": ["Isekai", "Scudo fisso sul braccio che non può impugnare armi", "Evocato come eroe dello scudo, un ragazzo viene tradito ed accusato ingiustamente."]},
    {"target": "GOBLIN SLAYER", "indizi": ["Dark Fantasy", "Elmo d'acciaio semplice e pugnale corto", "Un guerriero solitario dedica la sua intera esistenza unicamente allo sterminio dei goblin."]},
    {"target": "MUSHISHI", "indizi": ["Fantasy / Episodico", "Mantello da viaggiatore e scatola di legno", "Un viaggiatore studia creature ancestrali invisibili agli occhi comuni che influenzano le vite umane."]},
    {"target": "PLUTO", "indizi": ["Sci-Fi / Mistero", "Detective robotico e corna di fango", "Un detective cyborg indaga sulle morti misteriose dei robot più avanzati del pianeta."]},
    {"target": "DOROHEDORO", "indizi": ["Dark Fantasy / Cyberpunk", "Testa da lucertola e maschera da gatto", "Un uomo con la testa da rettile ed amnesia caccia gli stregoni per ritrovare il suo vero volto."]},
    {"target": "ERASED", "indizi": ["Mistero / Drama", "Salto temporale involontario prima dei delitti", "Un mangaka torna indietro di diciotto anni per salvare una compagna di classe da un killer seriale."]},
    {"target": "ANOHANA", "indizi": ["Drammatico", "Fantasma della bambina con il vestito bianco", "Il fantasma di un'amica d'infanzia appare al leader del vecchio gruppo per esaudire un ultimo desiderio."]},
    {"target": "YOUR LIE IN APRIL", "indizi": ["Drammatico / Musica", "Pianoforte e violino", "Un giovane pianista traumatizzato ritrova la gioia di suonare grazie all'incontro con una violinista."]},
    {"target": "NANA", "indizi": ["Drama / Musica", "Chitarra punk e anello con armatura", "Due ragazze con lo stesso nome condividono un appartamento a Tokyo incrociando i propri destini."]},
    {"target": "INUYASHIKI", "indizi": ["Sci-Fi", "Anziano e ragazzo trasformati in cyborg da un UFO", "Un vecchietto malato riceve un corpo cibernetico e decide di usarlo per salvare vite umane."]},
    {"target": "SERIAL EXPERIMENTS LAIN", "indizi": ["Sci-Fi / Cyberpunk", "Cavi per computer e rete telefonica Wired", "Una timida adolescente riceve un'e-mail da una compagna morta e si immerge nei segreti della rete."]},
    {"target": "AKIRA", "indizi": ["Sci-Fi / Cyberpunk", "Moto rossa futuristica ed espansione cerebrale", "Nella Neo-Tokyo del 2019, un giovane motociclista sviluppa poteri psicocinetici devastanti."]},
    {"target": "GHOST IN THE SHELL", "indizi": ["Sci-Fi / Cyberpunk", "Corpo sintetico e mimetizzazione ottica invisibile", "Il maggiore di una sezione cibernetica della polizia caccia un hacker in grado di violare i cervelli."]},
    {"target": "PAPRIKA", "indizi": ["Sci-Fi / Mistero", "Dispositivo DC Mini per entrare nei sogni", "Una psichiatra usa una tecnologia sperimentale per navigare negli incubi dei suoi pazienti."]},
    {"target": "PRINCESS MONONOKE", "indizi": ["Fantasy / Studio Ghibli", "Maschera rossa e lupi giganti", "Un principe colpito da una maledizione si ritrova al centro della guerra tra umani e spiriti."]},
    {"target": "LA CITTA INCANTATA", "indizi": ["Fantasy / Studio Ghibli", "Bagni termali degli spiriti", "Una bambina deve lavorare nell'albergo degli dei per salvare i genitori trasformati in maiali."]},
    {"target": "IL CASTELLO ERRANTE DI HOWL", "indizi": ["Fantasy / Studio Ghibli", "Struttura meccanica camminante a vapore", "Una giovane calzolaia trasformata in anziana trova riparo nella dimora mobile di un mago."]},
    {"target": "KUROKO'S BASKETBALL", "indizi": ["Sportivo / Basket", "Passaggi invisibili e capelli rossi/blu", "Un giocatore ombra privo di presenza fisica fa squadra con un talento americano per vincere."]},
    {"target": "INITIAL D", "indizi": ["Sportivo / Motori", "Toyota AE86 bianca e nera che fa il drift sui monti", "Il figlio di un venditore di tofu diventa una leggenda delle corse clandestine in montagna."]},
    {"target": "FOOD WARS!", "indizi": ["Commedia / Cucina", "Piatti di cibo che fanno esplodere i vestiti dall'estasi", "Un giovane cuoco si iscrive nell'accademia culinaria più severa del mondo."]},
    {"target": "KAGUYA-SAMA: LOVE IS WAR", "indizi": ["Commedia / Romantico", "Consiglio d'istituto del liceo d'élite", "Due studenti geni e superbi usano strategie psicologiche complesse per far confessare l'altro."]},
    {"target": "OURAN HIGH SCHOOL HOST CLUB", "indizi": ["Commedia / Romantico", "Vaso da otto milioni rotto ed occhiali da vista", "Una ragazza scambiata per un maschio deve lavorare nel club d'intrattenimento del liceo."]},
    {"target": "THE PROMISED NEVERLAND", "indizi": ["Thriller / Mistero", "Numeri tatuati sul collo ed orfanotrofio recintato", "Tre bambini prodigio scoprono che la loro idilliaca casa d'infanzia è un allevamento per demoni."]}
]

# --- DATABASE 100 BRAND & MARCHI ---
QUIZ_BRANDS_DB = [
    {"target": "COCA-COLA", "indizi": ["Bevande / USA", "Scritta bianca su fondo rosso", "La bibita gassata scura servita nella celebre bottiglia di vetro sagomata."]},
    {"target": "APPLE", "indizi": ["Elettronica / USA", "Frutto morso sul retro", "La mela morsa che ha lanciato l'iPhone ed i computer Mac."]},
    {"target": "NIKE", "indizi": ["Abbigliamento sportivo / USA", "Baffo Swoosh e Just Do It", "L'azienda che prende il nome dalla dea della vittoria e produce le Air Jordan."]},
    {"target": "MCDONALD'S", "indizi": ["Fast Food / USA", "Archi dorati a forma di M", "Il colosso del fast food famoso per il Big Mac e l'Happy Meal."]},
    {"target": "FERRARI", "indizi": ["Automotive / Italia", "Cavallino rampante su fondo giallo", "Le supercar rosse di Maranello fondate dal celebre Drake."]},
    {"target": "AMAZON", "indizi": ["E-commerce / USA", "Freccia dal punto A alla Z che forma un sorriso", "Lo store online nato come libreria sul web da Jeff Bezos."]},
    {"target": "GOOGLE", "indizi": ["Tecnologia / USA", "Scritta colorata blu, rossa, gialla e verde", "Il motore di ricerca web diventato sinonimo di navigazione su internet."]},
    {"target": "IKEA", "indizi": ["Arredamento / Svezia", "Scritta gialla dentro un rettangolo blu", "I mobili da montare a casa con la brugola e le polpettine al ristorante."]},
    {"target": "ADIDAS", "indizi": ["Abbigliamento sportivo / Germania", "Tre strisce parallele", "Il marchio tedesco fondato da Adolf Dassler famoso per le tute e le scarpe Superstar."]},
    {"target": "NUTELLA", "indizi": ["Alimentare / Italia", "Barattolo con noci, bicchiere di latte e pane spalmato", "La crema spalmabile al cacao e nocciole prodotta dalla Ferrero."]},
    {"target": "MICROSOFT", "indizi": ["Software / USA", "Quattro quadratini di colore rosso, verde, blu e giallo", "La società di Bill Gates che ha creato il sistema operativo Windows."]},
    {"target": "SAMSUNG", "indizi": ["Elettronica / Corea del Sud", "Scritta bianca dentro un ovale blu", "Il colosso asiatico rivale di Apple famoso per gli smartphone Galaxy."]},
    {"target": "PLAYSTATION", "indizi": ["Gaming / Giappone", "Lettere P e S intrecciate con i simboli X, Cerchio, Triangolo e Quadrato", "La console da gioco per casa prodotta dalla Sony dagli anni '90."]},
    {"target": "BARILLA", "indizi": ["Alimentare / Italia", "Ovale rosso su scatola blu di cartone", "Dove c'è pasta c'è casa, il più grande pastificio del mondo."]},
    {"target": "STARBUCKS", "indizi": ["Ristorazione / USA", "Sirena verde a due code", "La catena di caffetterie famosa per i bicchieroni di Frappuccino con il nome scritto sopra."]},
    {"target": "DISNEY", "indizi": ["Intrattenimento / USA", "Castello incantato con la firma del fondatore", "Il regno dell'animazione nato dal topo con le orecchie tonde Topolino."]},
    {"target": "MERCEDES-BENZ", "indizi": ["Automotive / Germania", "Stella a tre punte d'argento", "La casa automobilistica tedesca di vetture di lusso e Frecce d'Argento."]},
    {"target": "BMW", "indizi": ["Automotive / Germania", "Elica azzurra e bianca", "La casa automobilistica di Monaco di Baviera con la griglia a doppio rene."]},
    {"target": "LEGO", "indizi": ["Giocattoli / Danimarca", "Mattoncini colorati con i bottoncini incastrabili", "I celebri omini gialli ed i mattoncini di plastica da montare."]},
    {"target": "NETFLIX", "indizi": ["Streaming / USA", "Grande N rossa con il suono Ta-dum", "La piattaforma che ha reso famoso il binge-watching di serie TV e film."]},
    {"target": "PEPSI", "indizi": ["Bevande / USA", "Cerchio diviso in due metà rossa e blu con onda bianca", "La storica rivale della cola rossa testimonial nei mondiali di calcio."]},
    {"target": "PORSCHE", "indizi": ["Automotive / Germania", "Scudo con il cavallo di Stoccarda", "Le sportive di lusso celebri per il mitico modello 911."]},
    {"target": "GUCCI", "indizi": ["Moda / Italia", "Doppia G intrecciata ed il nastro verde-rosso-verde", "Lo storico marchio di alta moda fiorentino nato dalla pelletteria."]},
    {"target": "LOUIS VUITTON", "indizi": ["Moda / Francia", "Monogramma LV impresso sulla pelle marrone", "Le borse ed i bauli da viaggio di lusso francesi dal pattern inconfondibile."]},
    {"target": "ROLEX", "indizi": ["Orologeria / Svizzera", "Corona dorata a cinque punte", "Gli orologi da polso svizzeri di lusso simboli di status come il Submariner."]},
    {"target": "RED BULL", "indizi": ["Bevande / Austria", "Due tori rossi che si scontrano davanti al sole", "Ti mette le ali, l'energy drink famoso per le sponsorizzazioni negli sport estremi e F1."]},
    {"target": "RAY-BAN", "indizi": ["Accessori / USA/Italia", "Scritta bianca nell'angolo della lente", "Gli occhiali da sole iconici dei modelli Wayfarer e Aviator da pilota."]},
    {"target": "NINTENDO", "indizi": ["Gaming / Giappone", "Scritta rossa dentro un rettangolo smussato", "La storica casa di Kyoto che ha creato Mario, Zelda ed il Game Boy."]},
    {"target": "SPOTIFY", "indizi": ["Musica / Svezia", "Cerchio verde con tre onde sonore nere", "L'applicazione di streaming musicale verde che ha sostituito i lettori MP3."]},
    {"target": "ZARA", "indizi": ["Abbigliamento / Spagna", "Scritta minimalista nera", "Il colosso del fast fashion fondato dal miliardario Amancio Ortega."]},
    {"target": "H&M", "indizi": ["Abbigliamento / Svezia", "Lettere H ed M rosse", "Catena svedese di abbigliamento economico presente nei centri commerciali."]},
    {"target": "LAMBORGHINI", "indizi": ["Automotive / Italia", "Toro dorato scatenato su fondo nero", "Le supercar aggressive di Sant'Agata Bolognese come la Countach ed la Huracán."]},
    {"target": "PUMA", "indizi": ["Abbigliamento sportivo / Germania", "Felino nero che salta sulla scritta", "Fondata dal fratello del creatore di Adidas, sponsor storici di Usain Bolt."]},
    {"target": "SUPREME", "indizi": ["Moda Streetwear / USA", "Scritta bianca dentro un rettangolo rosso acceso", "Il marchio di moda urbana famoso per le tirature limitatissime ed il logo su ogni oggetto."]},
    {"target": "LAVAZZA", "indizi": ["Alimentare / Italia", "Tazzina fumante e Più lo tiri su...", "Il caffè preferito dagli italiani, colosso torinese della miscela."]},
    {"target": "HEINEKEN", "indizi": ["Bevande / Paesi Bassi", "Stella rossa su etichetta verde", "La birra bionda olandese in bottiglia verde sponsor della Champions League."]},
    {"target": "NIVEA", "indizi": ["Cosmesi / Germania", "Scritta bianca nel cerchio blu notte", "La storica crema idratante per il corpo nella scatola tonda di metallo blu."]},
    {"target": "BACI PERUGINA", "indizi": ["Alimentare / Italia", "Incarto d'argento con lettere blu e cartiglio", "I cioccolatini con la nocciola intera che contengono frasi d'amore."]},
    {"target": "DECATHLON", "indizi": ["Sport / Francia", "Insegna blu con la lettera C deformata a forma di ellisse", "I megastore di articoli sportivi famosi per la tenda da campeggio 2 Seconds."]},
    {"target": "PRADA", "indizi": ["Moda / Italia", "Triangolo rovesciato di metallo impresso", "Il marchio di lusso milanese diretto da Miuccia Prada famoso per il nylon nero."]},
    {"target": "CHANEL", "indizi": ["Moda / Francia", "Doppia C incrociata", "La casa di moda parigina famosa per il tubino nero ed il profumo N° 5."]},
    {"target": "VOLKSWAGEN", "indizi": ["Automotive / Germania", "Lettere V e W sovrapposte dentro un cerchio", "L'auto del popolo che ha creato il Maggiolino ed la Golf."]},
    {"target": "CONVERSE", "indizi": ["Calzature / USA", "Stella blu dentro un cerchio di stoffa bianca", "Le scarpe da ginnastica in tela con la punta in gomma modello All-Star Chuck Taylor."]},
    {"target": "CAMPARI", "indizi": ["Bevande / Italia", "Liquore dal colore rosso brillante", "L'aperitivo rosso amaro milanese servito nel celebre bottiglierino con il selz."]},
    {"target": "KINDER", "indizi": ["Alimentare / Italia", "Scritta con la K nera ed il resto rosso e blu", "La linea della Ferrero dedicata ai bambini con l'uovo con sorpresa e la barretta."]},
    {"target": "TESLA", "indizi": ["Automotive / USA", "Grande lettera T a forma di scudo o stilo", "La casa automobilistica elettrica guidata da Elon Musk."]},
    {"target": "BIRRA MORETTI", "indizi": ["Bevande / Italia", "Baffone con il cappello verde ed il boccale in mano", "La birra italiana con il celebre signore baffuto sull'etichetta."]},
    {"target": "CANON", "indizi": ["Elettronica / Giappone", "Scritta rossa con la lettera C piegata", "Il colosso delle macchine fotografiche reflex e delle stampanti."]},
    {"target": "GILLETTE", "indizi": ["Cura personale / USA", "Scritta blu con il taglio netto sulla lettera I", "Il meglio di un uomo, marchio leader dei rasoi da barba multilama."]},
    {"target": "MONSTER ENERGY", "indizi": ["Bevande / USA", "M verde formato da tre artigli graffiati", "L'energy drink nella lattina nera gigante con il graffio verde."]},
    {"target": "BIC", "indizi": ["Cartoleria / Francia", "Omino giallo con la testa a forma di sfera nera", "Le penne a sfera trasparenti usa e getta ed i famosi accendini."]},
    {"target": "LEVI'S", "indizi": ["Abbigliamento / USA", "Etichetta rossa sulla tasca posteriore destra", "I jeans storici di tela denim con il modello classico 501."]},
    {"target": "PLAYBOY", "indizi": ["Editoria / USA", "Coniglietto nero con il papillon", "La rivista per adulti fondata da Hugh Hefner."]},
    {"target": "VANS", "indizi": ["Calzature / USA", "Onda ricurva bianca sul fianco e suola a nido d'ape", "Le scarpe basse da skateboarder con la linea Off the Wall."]},
    {"target": "ARMANI", "indizi": ["Moda / Italia", "Aquila stilizzata con le lettere GA", "Lo stilista re della moda milanese noto per l'eleganza sobria delle sue giacche."]},
    {"target": "VERSACE", "indizi": ["Moda / Italia", "Testa della Medusa greca dorata", "La casa di moda fondata da Gianni famosa per lo stile barocco, audace e colorato."]},
    {"target": "SWATCH", "indizi": ["Orologeria / Svizzera", "Orologi in plastica colorata ed accessibili", "Gli orologi svizzeri economici e colorati che hanno salvato l'industria negli anni '80."]},
    {"target": "KFC", "indizi": ["Fast Food / USA", "Volto sorridente del Colonnello Sanders con il farfallino", "Catena di fast food famosa per i secchielli di pollo fritto croccante."]},
    {"target": "DOMINO'S PIZZA", "indizi": ["Fast Food / USA", "Tassello del domino rosso e blu con tre pallini", "La catena di consegna pizze a domicilio con la scatola quadrata."]},
    {"target": "PRINGLES", "indizi": ["Alimentare / USA", "Omino con i baffi rossi e papillon (Mr. P)", "Le patatine sfoglia tutte uguali confezionate nel tubo cilindrico di cartone."]},
    {"target": "CHUPA CHUPS", "indizi": ["Alimentare / Spagna", "Fiore giallo disegnato da Salvador Dalí", "I lecca-lecca colorati sul bastoncino di plastica."]},
    {"target": "HARIBO", "indizi": ["Alimentare / Germania", "Orsetto giallo con il fiocco rosso", "Candidamente dei piccoli e dei grandi, famosi per gli orsetti gommosi."]},
    {"target": "TIC TAC", "indizi": ["Alimentare / Italia", "Scatolina di plastica trasparente con il tappo a scatto", "Le famose pastigliette confetti rinfrescanti da due calorie l'una."]},
    {"target": "UNDER ARMOUR", "indizi": ["Abbigliamento sportivo / USA", "Lettere U ed A incrociate in verticale", "Abbigliamento tecnico sportivo traspirante ed aderente molto usato nelle palestre."]},
    {"target": "ASICS", "indizi": ["Abbigliamento sportivo / Giappone", "Linee incrociate a forma di strisce di tigre", "Le scarpe da corsa giapponesi amate dai maratoneti."]},
    {"target": "TIMBERLAND", "indizi": ["Calzature / USA", "Albero stilizzato impresso sul cuoio", "Gli scarponcini gialli in pelle scamosciata impermeabili."]},
    {"target": "DR. MARTENS", "indizi": ["Calzature / USA/UK", "Cucitura gialla sulla suola con cuscinetto d'aria", "Gli anfibi in pelle rigida a 8 buchi con le fettuccia nera e gialla sul retro."]},
    {"target": "LACOSTE", "indizi": ["Abbigliamento / Francia", "Coccodrillo verde ricamato sul petto", "Le polo di cotone nate dal famoso tennista francese René."]},
    {"target": "TOMMY HILFIGER", "indizi": ["Moda / USA", "Rettangolino diviso in due metà bianca e rossa tra due bande blu", "Marchio di moda americana dallo stile classico da college."]},
    {"target": "CALVIN KLEIN", "indizi": ["Moda / USA", "Lettere CK impresse sull'elastico dei boxer", "Famose per le campagne pubblicitarie di intimo e jeans negli anni '90."]},
    {"target": "ILLY", "indizi": ["Alimentare / Italia", "Scritta bianca dentro un quadrato rosso acceso", "La storica azienda di caffè triestina famosa per i barattoli cilindrici in argento."]},
    {"target": "CAFFE BORBONE", "indizi": ["Alimentare / Italia", "Corona dorata su scudo blu", "Marchio napoletano di caffè famoso per le cialde e le capsule della macchinetta."]},
    {"target": "PERONI", "indizi": ["Bevande / Italia", "Nastro azzurro ed etichetta con stemma", "La birra chiara italiana famosa per la campagna Chiamami Peroni, sarò la tua birra."]},
    {"target": "SAN PELLEGRINO", "indizi": ["Bevande / Italia", "Stella rossa sulla bottiglia verde di vetro", "L'acqua minerale gassata ed le bibite come l'aranciata famose nel mondo."]},
    {"target": "APEROL", "indizi": ["Bevande / Italia", "Colore arancione vivo della bottiglia", "Il liquore amaro ingrediente fondamentale dello Spritz."]},
    {"target": "MARTINI", "indizi": ["Bevande / Italia", "Cerchio rosso attraversato da un rettangolo nero", "Il vermut torinese ingrediente del celebre cocktail shakerato non mescolato."]},
    {"target": "DUCATI", "indizi": ["Automotive / Italia", "Scudo rosso con la doppia striscia bianca", "Le moto rosse di Borgo Panigale famose per il motore desmodromico."]},
    {"target": "PIAGGIO", "indizi": ["Automotive / Italia", "Esagono blu con la lettera P", "La casa di Pontedera che ha inventato il mitico scooter Vespa ed il Ciao."]},
    {"target": "ALFA ROMEO", "indizi": ["Automotive / Italia", "Biscione visconteo ed la croce di Milano", "Le vetture sportive italiane con il celebre calandra a trilobo frontale."]},
    {"target": "FIAT", "indizi": ["Automotive / Italia", "Quattro lettere su rettangoli inclinati blu o rossi", "La storica Fabbrica Italiana Automobili Torino che ha creato la 500 e la Panda."]},
    {"target": "PEUGEOT", "indizi": ["Automotive / Francia", "Leone rampante d'argento", "La casa automobilistica francese dal logo felino e la griglia aggressiva."]},
    {"target": "RENAULT", "indizi": ["Automotive / Francia", "Losanga / Diamante d'argento", "La casa francese automobilistica famosa per la Clio ed i successi in F1."]},
    {"target": "VOLVO", "indizi": ["Automotive / Svezia", "Cerchio con la freccia rivolta in alto a destra", "Le vetture svedesi storicamente famose come le più sicure al mondo."]},
    {"target": "JEEP", "indizi": ["Automotive / USA", "Griglia a sette feritoie verticali frontali", "I fuoristrada per eccellenza nati per uso militare nella Seconda Guerra Mondiale."]},
    {"target": "MASTERCARD", "indizi": ["Finanza / USA", "Due cerchi sovrapposti rosso e giallo", "Ci sono cose che non si possono comprare, per tutto il resto c'è..."]},
    {"target": "VISA", "indizi": ["Finanza / USA", "Scritta blu con il taglietto giallo sulla V", "Il circuito di carte di credito ed addebito più diffuso al mondo."]},
    {"target": "PAYPAL", "indizi": ["Servizi Web / USA", "Doppia P blu sovrapposta", "Il sistema di pagamento online sicuro creato tra gli altri da Elon Musk."]},
    {"target": "TRIPADVISOR", "indizi": ["Servizi Web / USA", "Gufo con un occhio verde ed uno rosso", "Il sito web e l'app di recensioni di ristoranti ed alberghi lasciate dagli utenti."]},
    {"target": "AIRBNB", "indizi": ["Servizi Web / USA", "Simbolo Bélo rosa a forma di cuore/A rovesciata", "La piattaforma per affittare case ed appartamenti vacanza da privati."]},
    {"target": "UBER", "indizi": ["Trasporti / USA", "Scritta bianca minimalista su fondo nero", "L'applicazione di trasporto privato di passeggeri tramite autisti con auto propria."]},
    {"target": "TIKTOK", "indizi": ["Social Network / Cina", "Nota musicale stilizzata con effetto glitch rosso e blu", "La piattaforma social cinese di brevi video verticali musicali ed ironici."]},
    {"target": "INSTAGRAM", "indizi": ["Social Network / USA", "Fotocamera stilizzata con sfumatura viola, rosa e arancione", "Il social network nato per la condivisione di foto, filtri e Storie."]},
    {"target": "WHATSAPP", "indizi": ["Messaggistica / USA", "Cornetta telefonica bianca dentro la fumetto verde", "L'applicazione di messaggistica istantanea verde che ha sostituito gli SMS."]},
    {"target": "TELEGRAM", "indizi": ["Messaggistica / Russia/EAE", "Aeroplanino di carta bianco dentro un cerchio azzurro", "L'app di messaggistica con il logo dell'aeroplanino famosa per i canali ed i bot."]},
    {"target": "DUOLINGO", "indizi": ["App / USA", "Gufo verde sorridente", "L'applicazione per imparare le lingue straniere con la mascotte pennuta assillante."]},
    {"target": "NESPRESSO", "indizi": ["Alimentare / Svizzera", "Lettera N stilizzata con capsule colorate", "Le macchinette per il caffè espresso a casa testimonial da George Clooney."]},
    {"target": "DYSON", "indizi": ["Elettronica / UK", "Design avveniristico grigio e fucsia/viola", "Gli aspirapolvere cicloni senza sacchetto ed i famosi asciugacapelli privi di pale."]},
    {"target": "BOSE", "indizi": ["Elettronica / USA", "Scritta in corsivo inclinato nero", "Sistemi audio, casse portatili e cuffie con cancellazione del rumore."]},
    {"target": "DURACELL", "indizi": ["Elettronica / USA", "Coniglietto rosa che suona il tamburo e punta in rame", "Le pile stilo ed ministilo con la parte superiore color rame e la parte inferiore nera."]},
    {"target": "PAMPERS", "indizi": ["Cura personale / USA", "Cuoricino verde e giallo", "I pannolini per neonati e bambini più famosi del mondo."]}
]

# --- DATABASE 100 PERSONAGGI STORICI ---
QUIZ_PERSONAGGI_DB = [
    {"target": "GIULIO CESARE", "indizi": ["Antichità / Generale e Dictator", "Roma Antica", "Passò il fiume Rubicone dicendo 'Il dado è tratto' prima di venire tradito alle Idi di Marzo."]},
    {"target": "ALESSANDRO MAGNO", "indizi": ["Antichità / Re e Condottiero", "Macedonia / Grecia", "Giovanissimo re che creò un impero immenso conquistando la Persia fino all'India."]},
    {"target": "CLEOPATRA", "indizi": ["Antichità / Regina", "Egitto", "L'ultima sovrana dell'Egitto tolemaico, amata da Cesare e Marco Antonio."]},
    {"target": "NAPOLEONE BONAPARTE", "indizi": ["Età Moderna / Imperatore", "Francia", "Conquistò gran parte d'Europa prima della sconfitta definitiva a Waterloo ed all'esilio a Sant'Elena."]},
    {"target": "CARLO MAGNO", "indizi": ["Medioevo / Re ed Imperatore", "Regno dei Franchi", "Incoronato la notte di Natale dell'anno 800 come primo sovrano del Sacro Romano Impero."]},
    {"target": "LEONARDO DA VINCI", "indizi": ["Rinascimento / Pittore ed Inventore", "Italia", "Genio universale autore della Gioconda, dell'Uomo Vitruviano e dell'Ultima Cena."]},
    {"target": "DANTE ALIGHIERI", "indizi": ["Medioevo / Poeta", "Italia (Firenze)", "Il Sommo Poeta della lingua italiana autore del viaggio nei tre regni dell'Oltretomba."]},
    {"target": "MARCO POLO", "indizi": ["Medioevo / Esploratore", "Italia (Venezia)", "Mercante veneziano che viaggiò lungo la Via della Seta raccontando la Cina nel libro 'Il Milione'."]},
    {"target": "CRISTOFORO COLOMBO", "indizi": ["Età Moderna / Navigatore", "Italia (Genova) / Spagna", "Salpò con tre Caravelle nel 1492 sbarcando per errore in un nuovo continente."]},
    {"target": "GALILEO GALILEI", "indizi": ["Età Moderna / Astronomo e Fisico", "Italia", "Perfezionò il cannocchiale e fu processato dall'Inquisizione mormorando 'Eppur si muove'."]},
    {"target": "GENGIS KHAN", "indizi": ["Medioevo / Condottiero", "Mongolia", "Il grande leader nomade che unificò le tribù asiatiche creando il più vasto impero terrestre."]},
    {"target": "SOCRATE", "indizi": ["Antichità / Filosofo", "Grecia (Atene)", "Il padre del metodo maieutico condannato a morte a bere la cicuta per le sue idee."]},
    {"target": "PLATONE", "indizi": ["Antichità / Filosofo", "Grecia (Atene)", "Allievo di Socrate, fondò l'Accademia e scrisse la teoria delle idee ed il mito della caverna."]},
    {"target": "ARISTOTELE", "indizi": ["Antichità / Filosofo", "Grecia", "Maestro di Alessandro Magno e padre della logica e delle scienze naturali antiche."]},
    {"target": "ATTILA", "indizi": ["Antichità / Re degli Unni", "Asia / Europa", "'Dove passa il mio cavallo non cresce più l'erba', il flagello di Dio che terrorizzò Roma."]},
    {"target": "SPARTACO", "indizi": ["Antichità / Gladiatore", "Roma Antica", "Schiavo della Tracia che guidò la più imponente rivolta di gladiatori contro la Repubblica Romana."]},
    {"target": "ANNIBALE BARCA", "indizi": ["Antichità / Generale", "Cartagine", "Valicò le Alpi con gli elefanti da guerra mettendo a ferro e fuoco l'Italia contro i Romani."]},
    {"target": "CICERONE", "indizi": ["Antichità / Oratore e Politico", "Roma Antica", "Il più grande oratore romano celebre per le sue filippiche e le orazioni contro Catilina."]},
    {"target": "AUGUSTO", "indizi": ["Antichità / Primo Imperatore", "Roma Antica", "Nipote di Cesare, trasformò la Repubblica nell'Impero dicendo di aver lasciato Roma di marmo."]},
    {"target": "NERONE", "indizi": ["Antichità / Imperatore", "Roma Antica", "L'imperatore stravagante accusato dalla leggenda di aver suonato la cetra mentre Roma bruciava."]},
    {"target": "GIUSTINIANO", "indizi": ["Antichità / Imperatore", "Impero Romano d'Oriente", "Raccolse tutte le leggi romane nel Corpus Iuris Civilis e fece costruire Santa Sofia."]},
    {"target": "GIOVANNA D'ARCO", "indizi": ["Medioevo / Eroina e Santa", "Francia", "La 'Pulzella d'Orléans' che guidò l'esercito francese contro gli inglesi prima di essere arsa sul rogo."]},
    {"target": "FRANCESCO D'ASSISI", "indizi": ["Medioevo / Religioso e Poeta", "Italia", "Il santo poverello che parlava agli animali e scrisse il Cantico delle Creature."]},
    {"target": "MICHELANGELO BUONARROTI", "indizi": ["Rinascimento / Scultore e Pittore", "Italia", "Scolpì il David di marmo e dipinse l'affresco della Volta della Cappella Sistina."]},
    {"target": "NICCOLO MACHIAVELLI", "indizi": ["Rinascimento / Scrittore e Politico", "Italia (Firenze)", "Autore del trattato 'Il Principe', famoso per la massima 'Il fine giustifica i mezzi'."]},
    {"target": "MARTIN LUTERO", "indizi": ["Età Moderna / Teologo", "Germania", "Affisse le sue 95 tesi sulla porta della chiesa di Wittenberg dando vita alla Riforma Protestante."]},
    {"target": "ENRICO VIII", "indizi": ["Età Moderna / Re", "Inghilterra", "Ebbe sei mogli e staccò la chiesa inglese da Roma fondando la Chiesa Anglicana."]},
    {"target": "ELISABETTA I", "indizi": ["Età Moderna / Regina", "Inghilterra", "La 'Regina Vergine' che sconfisse l'Invincibile Armata spagnola guidando l'epoca d'oro inglese."]},
    {"target": "WILLIAM SHAKESPEARE", "indizi": ["Età Moderna / Drammaturgo", "Inghilterra", "Il bardo autore di Romeo e Giulietta, Amleto, Macbeth e del famoso 'Essere o non essere'."]},
    {"target": "LUIGI XIV", "indizi": ["Età Moderna / Re", "Francia", "Il 'Re Sole' assolutista che fece costruire la reggia di Versailles e disse 'L'État, c'est moi'."]},
    {"target": "ISAAC NEWTON", "indizi": ["Età Moderna / Fisico e Matematico", "Inghilterra", "Scoprì la legge di gravitazione universale vedendo cadere una mela dall'albero."]},
    {"target": "VOLTAIRE", "indizi": ["Età Moderna / Filosofo", "Francia", "Uno dei padri dell'Illuminismo famoso per la difesa della libertà di pensiero e della tolleranza."]},
    {"target": "GEORGE WASHINGTON", "indizi": ["Età Moderna / Generale e Presidente", "Stati Uniti", "Guida della guerra d'indipendenza e primo storico presidente degli Stati Uniti d'America."]},
    {"target": "ROBESPIERRE", "indizi": ["Età Moderna / Rivoluzionario", "Francia", "Il leader giacobino detto 'L'Incorruttibile' figura chiave del periodo del Terrore e della ghigliottina."]},
    {"target": "GIACOMO CASANOVA", "indizi": ["Età Moderna / Scrittore ed Avventuriero", "Italia (Venezia)", "Celebre seduttore veneziano le cui memorie sono diventate sinonimo di grande amatore."]},
    {"target": "MOZART", "indizi": ["Età Moderna / Compositore", "Austria", "Bambino prodigio della musica classica autore del Flauto Magico e delle Nozze di Figaro."]},
    {"target": "BEETHOVEN", "indizi": ["Età Moderna / Compositore", "Germania", "Scrisse la Nona Sinfonia con l'Inno alla Gioia nonostante la totale sordità negli ultimi anni."]},
    {"target": "GIUSEPPE GARIBALDI", "indizi": ["Età Contemporanea / Generale", "Italia", "'L'Eroe dei Due Mondi' che guidò la Spedizione dei Mille per unificare l'Italia."]},
    {"target": "GIUSEPPE MAZZINI", "indizi": ["Età Contemporanea / Politico e Patriota", "Italia", "Fondatore della 'Giovine Italia', teorico del risorgimento e della repubblica."]},
    {"target": "CAVOUR", "indizi": ["Età Contemporanea / Statista", "Italia (Piemonte)", "Il primo Primo Ministro d'Italia, grande regista diplomatico dell'Unità."]},
    {"target": "VITTORIO EMANUELE II", "indizi": ["Età Contemporanea / Primo Re", "Italia", "Il sovrano gentiluomo della dinastia Savoia proclamato primo re d'Italia nel 1861."]},
    {"target": "ABRAHAM LINCOLN", "indizi": ["Età Contemporanea / Presidente", "Stati Uniti", "Guidò l'Unione durante la Guerra di Secessione ed abolì la schiavitù prima di essere assassinato."]},
    {"target": "KARL MARX", "indizi": ["Età Contemporanea / Filosofo ed Economista", "Germania", "Autore del 'Capitale' e del 'Manifesto del Partito Comunista' con Friedrich Engels."]},
    {"target": "REGINA VITTORIA", "indizi": ["Età Contemporanea / Regina", "Regno Unito", "Regnò per oltre 63 anni durante l'apice dell'Impero Britannico e della rivoluzione industriale."]},
    {"target": "CHARLES DARWIN", "indizi": ["Età Contemporanea / Biologo", "Inghilterra", "Formulò la teoria dell'evoluzione delle specie per selezione naturale dopo il viaggio sul Beagle."]},
    {"target": "ALBERT EINSTEIN", "indizi": ["Età Contemporanea / Fisico", "Germania / USA", "Il fisico dal ciuffo spettinato che formulò la Teoria della Relatività ed la formula E=mc²."]},
    {"target": "NIKOLA TESLA", "indizi": ["Età Contemporanea / Inventore ed Ingegnere", "Serbia / USA", "Genio della corrente alternata, della radio e dei campi elettromagnetici rivale di Edison."]},
    {"target": "THOMAS EDISON", "indizi": ["Età Contemporanea / Inventore", "USA", "Registrò oltre mille brevetti tra cui la lampadina ad incandescenza ed il fonografo."]},
    {"target": "SIGMUND FREUD", "indizi": ["Età Contemporanea / Medico e Psicoanalista", "Austria", "Il padre della psicoanalisi che studiò l'inconscio, il complesso di Edipo e l'interpretazione dei sogni."]},
    {"target": "MARIE CURIE", "indizi": ["Età Contemporanea / Scienziata", "Polonia / Francia", "Unica donna a vincere due Premi Nobel in due scienze diverse per gli studi sulla radioattività."]},
    {"target": "WINSTON CHURCHILL", "indizi": ["Età Contemporanea / Primo Ministro", "Regno Unito", "Lo statista dal sigaro che guidò la Gran Bretagna contro il nazismo promettendo 'sangue e lacrime'."]},
    {"target": "ADOLF HITLER", "indizi": ["Età Contemporanea / Dittatore", "Germania", "Il Führer del Terzo Reich responsabile della Seconda Guerra Mondiale e della Shoah."]},
    {"target": "BENITO MUSSOLINI", "indizi": ["Età Contemporanea / Dittatore", "Italia", "Il 'Duce' del fascismo che governò l'Italia per un ventennio fino alla caduta nella guerra."]},
    {"target": "STALIN", "indizi": ["Età Contemporanea / Dittatore", "Unione Sovietica", "Il leader sovietico che guidò l'URSS durante la seconda guerra mondiale con il pugno di ferro."]},
    {"target": "ROOSEVELT", "indizi": ["Età Contemporanea / Presidente", "USA", "L'unico presidente eletto per quattro mandati, guidò il paese fuori dalla Grande Depressione col New Deal."]},
    {"target": "MAHATMA GANDHI", "indizi": ["Età Contemporanea / Leader Politico e Spirituale", "India", "Guidò l'India all'indipendenza attraverso la protesta non violenta (Satyagraha)."]},
    {"target": "NELSON MANDELA", "indizi": ["Età Contemporanea / Leader e Presidente", "Sudafrica", "Simbolo della lotta all'Apartheid, trascorse 27 anni in carcere prima di diventare presidente."]},
    {"target": "MARTIN LUTHER KING", "indizi": ["Età Contemporanea / Attivista", "USA", "Leader dei diritti civili degli afroamericani celebre per il discorso 'I have a dream'."]},
    {"target": "JOHN F. KENNEDY", "indizi": ["Età Contemporanea / Presidente", "USA", "Giovane presidente della Guerra Fredda assassinato a Dallas nel novembre 1963."]},
    {"target": "CHE GUEVARA", "indizi": ["Età Contemporanea / Rivoluzionario", "Argentina / Cuba", "Il medico guerrigliero dalla berretta con la stella, figura simbolo della rivoluzione cubana."]},
    {"target": "FIDEL CASTRO", "indizi": ["Età Contemporanea / Leader e Dittatore", "Cuba", "Il 'Líder Máximo' che governò Cuba per quasi cinquant'anni affrontando il blocco USA."]},
    {"target": "MAO ZEDONG", "indizi": ["Età Contemporanea / Leader Politico", "Cina", "Il fondatore della Repubblica Popolare Cinese e del Libretto Rosso."]},
    {"target": "PAPA GIOVANNI PAOLO II", "indizi": ["Età Contemporanea / Papa", "Polonia / Vaticano", "Il papa polacco viaggiatore che contribuì alla caduta del Muro di Berlino."]},
    {"target": "MADRE TERESA DI CALCUTTA", "indizi": ["Età Contemporanea / Religiosa", "Albania / India", "La piccola suora con il velo bianco ed azzurro consacrata ai più poveri della terra."]},
    {"target": "NEIL ARMSTRONG", "indizi": ["Età Contemporanea / Astronauta", "USA", "Il primo uomo a mettere piede sulla Luna nel 1969 dicendo 'Un piccolo passo per un uomo'."]},
    {"target": "JURIJ GAGARIN", "indizi": ["Età Contemporanea / Cosmonauta", "Unione Sovietica", "Il primo uomo nello spazio a bordo della Vostok 1 nel 1961 dicendo 'La Terra è blu'."]},
    {"target": "ANNA FRANK", "indizi": ["Età Contemporanea / Scrittrice", "Germania / Olanda", "La ragazzina ebrea che raccontò nel suo diario i due anni passati nascosta in un alloggio segreto."]},
    {"target": "ROSA PARKS", "indizi": ["Età Contemporanea / Attivista", "USA", "La donna nera che rifiutò di cedere il posto ad un bianco sull'autobus dando vita al boicottaggio."]},
    {"target": "MALCOLM X", "indizi": ["Età Contemporanea / Attivista", "USA", "Leader radicale afroamericano per i diritti umani ed il nazionalismo nero."]},
    {"target": "GORBACHEV", "indizi": ["Età Contemporanea / Politico", "Unione Sovietica", "L'ultimo leader sovietico che introdusse la Perestrojka portando alla fine della Guerra Fredda."]},
    {"target": "STEVE JOBS", "indizi": ["Età Contemporanea / Imprenditore", "USA", "Il co-fondatore di Apple dal dolcevita nero famoso per il discorso 'Stay hungry, stay foolish'."]},
    {"target": "BILL GATES", "indizi": ["Età Contemporanea / Imprenditore e Filantropo", "USA", "Il fondatore di Microsoft diventato per anni l'uomo più ricco del mondo."]},
    {"target": "LADY DIANA", "indizi": ["Età Contemporanea / Principessa", "Regno Unito", "La 'Principessa del popolo' amata per la beneficenza e scomparsa tragicamente a Parigi."]},
    {"target": "RITA LEVI-MONTALCINI", "indizi": ["Età Contemporanea / Scienziata", "Italia", "Premio Nobel per la medicina grazie alla scoperta del fattore di accrescimento nervoso NGF."]},
    {"target": "ENRICO FERMI", "indizi": ["Età Contemporanea / Fisico", "Italia / USA", "Premio Nobel italiano ideatore del primo reattore nucleare a catena al mondo."]},
    {"target": "GUGLIELMO MARCONI", "indizi": ["Età Contemporanea / Inventore", "Italia", "Premio Nobel padre della telegrafia senza fili e della radio."]},
    {"target": "ALESSANDRO VOLTA", "indizi": ["Età Moderna / Fisico", "Italia", "L'inventore della prima pila elettrica il cui nome ha dato origine all'unità di misura della tensione."]},
    {"target": "ANTONIO MEUCCI", "indizi": ["Età Contemporanea / Inventore", "Italia / USA", "L'inventore italiano del telettrofono riconosciuto ufficialmente come il vero padre del telefono."]},
    {"target": "LUCIANO PAVAROTTI", "indizi": ["Età Contemporanea / Tenore", "Italia", "Il grande tenore emiliano famoso nel mondo per l'interpretazione del 'Nessun dorma'."]},
    {"target": "FEDERICO FELLINI", "indizi": ["Età Contemporanea / Regista", "Italia", "Maestro del cinema italiano vincitore di cinque premi Oscar per capolavori come 'La dolce vita'."]},
    {"target": "CHARLIE CHAPLIN", "indizi": ["Età Contemporanea / Attore e Regista", "Inghilterra / USA", "Il creatore della maschera comica del vagabondo Charlot con la bombetta ed il bastone."]},
    {"target": "MARILYN MONROE", "indizi": ["Età Contemporanea / Attrice", "USA", "L'icona bionda di Hollywood per eccellenza famosa per il vestito bianco sollevato dal vento."]},
    {"target": "ELVIS PRESLEY", "indizi": ["Età Contemporanea / Cantante", "USA", "'Il Re del Rock 'n' Roll' dalle movenze di bacino sfrenate ed i costumi vistosi di Las Vegas."]},
    {"target": "MICHAEL JACKSON", "indizi": ["Età Contemporanea / Cantante", "USA", "'Il Re del Pop' celebre per il guanto unico glitterato, il passo del Moonwalk ed il disco Thriller."]},
    {"target": "FREDDIE MERCURY", "indizi": ["Età Contemporanea / Cantante", "Regno Unito", "L'inimitabile frontman dei Queen dalla voce estesa ed i baffi al Live Aid 1985."]},
    {"target": "BOB MARLEY", "indizi": ["Età Contemporanea / Cantante", "Giamaica", "Il re della musica Reggae ed il movimento rastafariano famoso per canzoni di pace come 'One Love'."]},
    {"target": "JOHN LENNON", "indizi": ["Età Contemporanea / Cantante", "Regno Unito", "Membro fondatore dei Beatles ed autore dell'inno pacifista 'Imagine' ucciso a New York nel 1980."]},
    {"target": "PABLO PICASSO", "indizi": ["Età Contemporanea / Pittore", "Spagna / Francia", "Il padre del Cubismo autore della grande tela di protesta 'Guernica'."]},
    {"target": "SALVADOR DALI", "indizi": ["Età Contemporanea / Pittore", "Spagna", "Il maestro del Surrealismo dai lunghi baffi all'insù famoso per gli orologi molli."]},
    {"target": "VINCENT VAN GOGH", "indizi": ["Età Contemporanea / Pittore", "Olanda / Francia", "Il pittore maledetto che si tagliò l'orecchio autore della 'Notte stellata' e dei 'Girasoli'."]},
    {"target": "FRIDA KAHLO", "indizi": ["Età Contemporanea / Pittrice", "Messico", "Pittrice messicana dal sopracciglio marcato nota per i toccanti ed intensi autoritratti."]},
    {"target": "ANDY WARHOL", "indizi": ["Età Contemporanea / Artista", "USA", "Il fondatore della Pop Art celebre per le serigrafie delle lattine di zuppa Campbell e di Marilyn."]},
    {"target": "MARCO AURELIO", "indizi": ["Antichità / Imperatore e Filosofo", "Roma Antica", "L'imperatore filosofo stoico autore dei 'Pensieri a se stesso'."]},
    {"target": "TOMMASO D'AQUINO", "indizi": ["Medioevo / Teologo", "Italia", "Il dottore angelico autore della Summa Theologiae e colonna della filosofia scolastica."]},
    {"target": "MARCO ANTONIO", "indizi": ["Antichità / Generale e Politico", "Roma Antica", "Luogotenente di Cesare legato sentimentalmente a Cleopatra e sconfitto ad Azio."]},
    {"target": "RASPUTIN", "indizi": ["Età Contemporanea / Mistico", "Russia", "Il monaco siberiano ipnotista che influenzò la corte degli ultimi Romanov prima di venire assassinato."]},
    {"target": "SOLIMANO IL MAGNIFICO", "indizi": ["Età Moderna / Sultano", "Impero Ottomano", "Portò l'Impero Ottomano all'apice dell'espansione e dello splendore culturale nel '500."]},
    {"target": "NOSTRADAMUS", "indizi": ["Età Moderna / Astrologo e Medico", "Francia", "Famoso per le sue oscure quartine in cui avrebbe previsto i grandi eventi del futuro."]},
    {"target": "POCAHONTAS", "indizi": ["Età Moderna / Nativa Americana", "USA", "La figlia del capo tribù Powhatan che favorì la pace con i coloni inglesi in Virginia."]},
    {"target": "JESSE OWENS", "indizi": ["Età Contemporanea / Atleta", "USA", "L'atleta nero che vinse quattro ori olimpici a Berlino 1936 davanti agli occhi di Hitler."]}
]

# --- DATABASE 100 CANZONI ---
QUIZ_CANZONI_DB = [
    {"target": "NEL BLU DIPINTO DI BLU", "indizi": ["1958 / Pop-Melodico", "Domenico Modugno", "'Penso che un sogno così non ritorni mai più, mi dipingevo le mani e il viso di blu...'"]},
    {"target": "ALBACHIARA", "indizi": ["1979 / Rock d'autore", "Vasco Rossi", "'Respiri piano per non far rumore, ti addormenti di sera e ti svegli col sole...'"]},
    {"target": "LA CANZONE DEL SOLE", "indizi": ["1971 / Cantautorato", "Lucio Battisti", "'Le bionde trecce, gli occhi azzurri e poi... le tue calzette rosse...'"]},
    {"target": "L'ANNO CHE VERRA", "indizi": ["1979 / Cantautorato", "Lucio Dalla", "'Caro amico ti scrivo, così mi distraggo un po' e siccome sei molto lontano più forte ti scriverò...'"]},
    {"target": "AZZURRO", "indizi": ["1968 / Pop-Varietà", "Adriano Celentano", "'Cerco l'estate tutto l'anno e all'improvviso eccola qua...'"]},
    {"target": "CENTRO DI GRAVITA PERMANENTE", "indizi": ["1981 / Pop-Elettronico", "Franco Battiato", "'Cerco un [...] che non mi faccia mai cambiare idea sulle cose sulla gente...'"]},
    {"target": "IL CIELO IN UNA STANZA", "indizi": ["1960 / Classico-Pop", "Mina", "'Quando sei qui con me questa stanza non ha più pareti ma alberi, alberi infiniti...'"]},
    {"target": "RIMMEL", "indizi": ["1975 / Cantautorato", "Francesco De Gregori", "'E qualcosa rimane fra le pagine chiare e le pagine scure...'"]},
    {"target": "IL MIO CANTO LIBERO", "indizi": ["1972 / Cantautorato", "Lucio Battisti", "'In un mondo che prigioniero è, respiriamo liberi io e te...'"]},
    {"target": "A MANO A MANO", "indizi": ["1978 / Cantautorato", "Rino Gaetano / Riccardo Cocciante", "'E [...] ti accorgerai che il vento ti soffia sul viso e ti ruba un sorriso...'"]},
    {"target": "LA GUERRA DI PIERO", "indizi": ["1964 / Cantautorato Folk", "Fabrizio De André", "'Dormi sepolto in un campo di grano, non è la rosa non è il tulipano...'"]},
    {"target": "CERTE NOTTI", "indizi": ["1995 / Rock", "Ligabue", "'E [...] la macchina è calda e dove si va con qualcuno le prendi...'"]},
    {"target": "SEI BELLISSIMA", "indizi": ["1975 / Pop-Rock", "Loredana Bertè", "'E poi mi diceva [...] e poi mi diceva sempre [...]'"]},
    {"target": "CARUSO", "indizi": ["1986 / Cantautorato", "Lucio Dalla", "'Te voglio bene assai, ma tanto tanto bene sai...'"]},
    {"target": "LA DONNA CANNONE", "indizi": ["1983 / Cantautorato", "Francesco De Gregori", "'E con le mani amore, per le mani ti prenderò e nello specchio dei tuoi occhi mi guarderò...'"]},
    {"target": "SALLY", "indizi": ["1996 / Rock d'autore", "Vasco Rossi", "'Cammina per la strada senza nemmeno guardare per terra...'"]},
    {"target": "50 SPECIAL", "indizi": ["1999 / Pop-Rock", "Lùnapop", "'Ma quanto è bello andare in giro con le ali sotto i piedi se ho una [...] che mi toglie i problemi...'"]},
    {"target": "UN'EMOZIONE DA POCO", "indizi": ["1978 / Pop-Rock", "Anna Oxa", "'C'è una ragione che cresce tra noi, è una storia che si fa più grande di noi...'"]},
    {"target": "GIANNA", "indizi": ["1978 / Pop-Folk", "Rino Gaetano", "'Sosteneva tesi e illusioni, un po' per forza e un po' per amore...'"]},
    {"target": "SI PUO DARE DI PIU", "indizi": ["1987 / Pop-Melodico", "Morandi, Tozzi, Ruggeri", "'Perché lo fa il mio cuore, lo fa il tuo cuore, [...] senza chiedere niente in cambio...'"]},
    {"target": "TANTI AUGURI", "indizi": ["1978 / Pop-Disco", "Raffaella Carrà", "'Com'è bello far l'amore da Trieste in giù, l'importante è farlo sempre con chi vuoi tu...'"]},
    {"target": "MARACAIBO", "indizi": ["1981 / Pop-Disco", "Lu Colombo", "'Mare forza nove, fuggire sì ma dove? Zaza, il piccolo bar...'"]},
    {"target": "I GIARDINI DI MARZO", "indizi": ["1972 / Cantautorato", "Lucio Battisti", "'Il carretto passava e quell'uomo gridava gelati...'"]},
    {"target": "C'ERA UN RAGAZZO CHE COME ME AMAVA I BEATLES E I ROLLING STONES", "indizi": ["1966 / Folk-Pop", "Gianni Morandi", "'Nel petto un cuore più non ha, ma due medaglie o tre...'"]},
    {"target": "L'ITALIANO", "indizi": ["1983 / Pop", "Toto Cutugno", "'Lasciatemi cantare con la chitarra in mano, lasciatemi cantare una canzone piano piano...'"]},
    {"target": "LA DESCRIZIONE DI UN ATTIMO", "indizi": ["2000 / Indie-Pop", "Tiromancino", "'La [...] è un punto fra due millenni...'"]},
    {"target": "DESTINAZIONE PARADISO", "indizi": ["1995 / Pop-Rock", "Gianluca Grignani", "'In viaggio da solo, senza un bagaglio, con un biglietto di sola andata per la [...]'"]},
    {"target": "TI AMO", "indizi": ["1977 / Pop-Melodico", "Umberto Tozzi", "'Un soldo, ti amo, in aria, ti amo, se viene testa vuol dire che basta...'"]},
    {"target": "PICCOLA STELLA SENZA CIELO", "indizi": ["1990 / Rock", "Ligabue", "'Ti mostrerai [...] ti mostrerai per quello che sei...'"]},
    {"target": "UN'ESTATE ITALIANA", "indizi": ["1989 / Pop-Rock", "Gianna Nannini & Edoardo Bennato", "'Inseguendo un gol, sotto il cielo di un'estate italiana...'"]},
    {"target": "FUORI DAL TUNNEL", "indizi": ["2003 / Hip-Hop/Rap", "Caparezza", "'La mia festa non è come la tua festa, io non ho il privè con la bottiglia in testa...'"]},
    {"target": "APPLAUSI PER FIBRA", "indizi": ["2006 / Rap", "Fabri Fibra", "'Fate [...] a [...] [...] t'ama o t'odia non ci sono vie di mezzo...'"]},
    {"target": "VORREI CANTARE COME BIAGIO", "indizi": ["2004 / Indie-Pop", "Simone Cristicchi", "'E vendere trecentomila copie ed essere un grande artista...'"]},
    {"target": "GENTE DI MARE", "indizi": ["1987 / Pop", "Tozzi e Raf", "'E noi che siamo [...] a noi che cosa ci importa di più...'"]},
    {"target": "LAURA NON C'E", "indizi": ["1997 / Pop-Rock", "Nek", "'E se c'è non è così, se c'è è solo nella mia testa...'"]},
    {"target": "ZITTI E BUONI", "indizi": ["2021 / Rock", "Måneskin", "'Parla, la gente purtroppo parla, non sa di che cazzo parla...'"]},
    {"target": "BRIVIDI", "indizi": ["2022 / Pop", "Mahmood & Blanco", "'E ti vorrei amare, ma sbaglio sempre, e mi vengono i [...] [...] [...]'"]},
    {"target": "CENERE", "indizi": ["2023 / Trap-Pop", "Lazza", "'Aiutami a sparire come [...] rinacerò ti giuro dalle [...]'"]},
    {"target": "TANGO", "indizi": ["2023 / Pop", "Tananai", "'E ci fermiamo qui in un bar di Kiev, le bombe cadono giù...'"]},
    {"target": "PASTELLO BIANCO", "indizi": ["2020 / Indie-Pop", "Pinguini Tattici Nucleari", "'E ho lasciato la mia felpa sul tuo divano per avere una scusa e tornare da te...'"]},
    {"target": "L'ESSENZIALE", "indizi": ["2013 / Pop", "Marco Mengoni", "'Mentre il mondo cade a pezzi io compongo nuovi spazi e desideri che appartengono anche a te...'"]},
    {"target": "MUSICA LEGGERISSIMA", "indizi": ["2021 / Indie-Pop", "Colapesce Dimartino", "'Metti un po' di [...] perché ho voglia di niente e non allineo i pensieri...'"]},
    {"target": "ROMA-BANGKOK", "indizi": ["2015 / Pop-Reggaeton", "Baby K feat. Giusy Ferreri", "'Amo le labbra rosse su una pelle chiara, le cose stonate...'"]},
    {"target": "OCCIDENTALI'S KARMA", "indizi": ["2017 / Pop", "Francesco Gabbani", "'L'evoluzione brancola, la scimmia nuda balla [...]'"]},
    {"target": "DOVE E QUANDO", "indizi": ["2019 / Pop-Summer", "Benji & Fede", "'Dimmi dove e quando, da stasera non arrivo in ritardo...'"]},
    {"target": "MAMBO SALENTINO", "indizi": ["2019 / Pop-Rap", "Boomdabash feat. Alessandra Amoroso", "'Balliamo tutta la notte sotto questo cielo azzurro...'"]},
    {"target": "LA MUSICA NON C'E", "indizi": ["2017 / Indie-Pop", "Coez", "'Vorrei portarti al mare, anzi portarti il mare...'"]},
    {"target": "INFINITO", "indizi": ["2001 / Pop", "Raf", "'Dimmi se ti ricordi di me, dell'amore che c'era tra noi...'"]},
    {"target": "LA VASCA", "indizi": ["2000 / Pop", "Alex Britti", "'Apro la rubrica trovo quattro amici ed un compleanno...'"]},
    {"target": "SERE NERE", "indizi": ["2003 / Pop-R&B", "Tiziano Ferro", "'Ripenso a quelle cose che ci siamo detti, a tutte quelle [...] [...]'"]},
    {"target": "BOHEMIAN RHAPSODY", "indizi": ["1975 / Rock", "Queen", "'Is this the real life? Is this just fantasy? Caught in a landslide...'"]},
    {"target": "IMAGINE", "indizi": ["1971 / Pop-Rock", "John Lennon", "'Imagine all the people living life in peace...'"]},
    {"target": "THRILLER", "indizi": ["1982 / Pop-Funk", "Michael Jackson", "'Cause this is [...], [...] night, and no one's gonna save you from the beast about to strike...'"]},
    {"target": "SMELLS LIKE TEEN SPIRIT", "indizi": ["1991 / Grunge", "Nirvana", "'With the lights out, it's less dangerous, here we are now, entertain us...'"]},
    {"target": "LIKE A PRAYER", "indizi": ["1989 / Pop", "Madonna", "'Life is a mystery, everyone must stand alone...'"]},
    {"target": "HOTEL CALIFORNIA", "indizi": ["1976 / Rock", "Eagles", "'Welcome to the [...], such a lovely place, such a lovely face...'"]},
    {"target": "SWEET CHILD O' MINE", "indizi": ["1987 / Hard Rock", "Guns N' Roses", "'She's got eyes of the bluest skies as if they thought of rain...'"]},
    {"target": "WONDERWALL", "indizi": ["1995 / Britpop", "Oasis", "'Because maybe, you're gonna be the one that saves me, and after all you're my [...]'"]},
    {"target": "SHAPE OF YOU", "indizi": ["2017 / Pop", "Ed Sheeran", "'The club isn't the best place to find a lover so the bar is where I go...'"]},
    {"target": "BLINDING LIGHTS", "indizi": ["2019 / Synth-Pop", "The Weeknd", "'I'm blinded by the lights, no I can't sleep until I feel your touch...'"]},
    {"target": "HALLELUJAH", "indizi": ["1984 / Folk-Rock", "Leonard Cohen / Jeff Buckley", "'I've heard there was a secret chord that David played and it pleased the Lord...'"]},
    {"target": "BILLIE JEAN", "indizi": ["1982 / Pop-Disco", "Michael Jackson", "'[...] [...] is not my lover, she's just a girl who claims that I am the one...'"]},
    {"target": "STAYIN' ALIVE", "indizi": ["1977 / Disco", "Bee Gees", "'Ah, ha, ha, ha, [...] [...] [...] [...]'"]},
    {"target": "DANCING QUEEN", "indizi": ["1976 / Pop-Disco", "ABBA", "'You can dance, you can jive, having the time of your life...'"]},
    {"target": "YESTERDAY", "indizi": ["1965 / Pop-Folk", "The Beatles", "'[...], all my troubles seemed so far away, now it looks as though they're here to stay...'"]},
    {"target": "LET IT BE", "indizi": ["1970 / Pop-Rock", "The Beatles", "'When I find myself in times of trouble, Mother Mary comes to me...'"]},
    {"target": "HEY JUDE", "indizi": ["1968 / Rock", "The Beatles", "'[...], don't make it bad, take a sad song and make it better...'"]},
    {"target": "SATISFACTION", "indizi": ["1965 / Rock", "The Rolling Stones", "'I can't get no [...], cause I try and I try and I try...'"]},
    {"target": "ANOTHER BRICK IN THE WALL", "indizi": ["1979 / Progressive Rock", "Pink Floyd", "'We don't need no education, we don't need no thought control...'"]},
    {"target": "WISH YOU WERE HERE", "indizi": ["1975 / Rock", "Pink Floyd", "'How I wish, how I wish you were here, we're just two lost souls swimming in a fish bowl...'"]},
    {"target": "EYE OF THE TIGER", "indizi": ["1982 / Hard Rock", "Survivor", "'It's the [...], it's the thrill of the fight, rising up to the challenge of our rival...'"]},
    {"target": "LIVIN' ON A PRAYER", "indizi": ["1986 / Rock", "Bon Jovi", "'Tommy used to work on the docks, union's been on strike, he's down on his luck...'"]},
    {"target": "IN THE END", "indizi": ["2000 / Nu Metal", "Linkin Park", "'I tried so hard and got so far, but in the end it doesn't even matter...'"]},
    {"target": "SEVEN NATION ARMY", "indizi": ["2003 / Garage Rock", "The White Stripes", "'I'm gonna fight 'em all, a [...] [...] [...] couldn't hold me back...'"]},
    {"target": "TOXIC", "indizi": ["2003 / Dance-Pop", "Britney Spears", "'With a taste of your lips I'm on a ride, you're toxic I'm slippin' under...'"]},
    {"target": "ROLLING IN THE DEEP", "indizi": ["2010 / Soul-Pop", "Adele", "'There's a fire starting in my heart, reaching a fever pitch and it's bringing me out the dark...'"]},
    {"target": "UPTOWN FUNK", "indizi": ["2014 / Funk-Pop", "Mark Ronson feat. Bruno Mars", "'Don't believe me, just watch, come on!'"]},
    {"target": "DESPACITO", "indizi": ["2017 / Reggaeton", "Luis Fonsi feat. Daddy Yankee", "'Quiero respirar tu cuello [...] dejar que te diga cosas al oído...'"]},
    {"target": "GANGNAM STYLE", "indizi": ["2012 / K-Pop", "PSY", "'Oppa is [...] [...]!'"]},
    {"target": "BAD GUY", "indizi": ["2019 / Electropop", "Billie Eilish", "'So you're a tough guy, like it really rough guy... I'm the [...] [...]'"]},
    {"target": "FELICITA", "indizi": ["1982 / Pop", "Al Bano & Romina Power", "'E tenere la mano, andare lontano, la [...] è un bicchiere di vino con un panino...'"]},
    {"target": "IL GATTO E LA VOLPE", "indizi": ["1977 / Rock-Folk", "Edoardo Bennato", "'Quanta fretta, ma dove vai? Se ci fermiamo un momento ti pentirai...'"]},
    {"target": "PESCATORE", "indizi": ["1980 / Cantautorato", "Pierangelo Bertoli & Fiorella Mannoia", "'Getta le tue reti, buona pesca ci sarà...'"]},
    {"target": "PICCOLA KATY", "indizi": ["1968 / Pop", "Pooh", "'[...] [...] vai via, la porta è socchiusa, la strada è deserta...'"]},
    {"target": "SAMARCANDA", "indizi": ["1977 / Folk-Pop", "Roberto Vecchioni", "'Ridi, ridi, cavallo, ridi, che la bella morte è arrivata qua...'"]},
    {"target": "PIAZZA GRANDE", "indizi": ["1972 / Cantautorato", "Lucio Dalla", "'Ci vorrebbe anche il mare che dorme nelle mie vene...'"]},
    {"target": "IN ALTO MARE", "indizi": ["1980 / Pop-Funk", "Loredana Bertè", "'Fiaba, la mia vita è una fiaba, io non cerco un tesoro...'"]},
    {"target": "SPLENDIDO SPLENDENTE", "indizi": ["1979 / Pop-Disco", "Donatella Rettore", "'Sei bellissima, sei fantastica, con la chirurgia plastica...'"]},
    {"target": "SOTTO QUESTO SOLE", "indizi": ["1990 / Pop", "Francesco Baccini & Ladri di Biciclette", "'[...] [...] [...] bello pedalare, sì ma con la bici giusta...'"]},
    {"target": "MALEDETTA PRIMAVERA", "indizi": ["1981 / Pop-Melodico", "Loretta Goggi", "'Che fa se per errore mi abbandono a un momento d'amore... che importa se è una [...] [...]'"]},
    {"target": "SERVI DELLA GLEBA", "indizi": ["1992 / Pop-Rock Satirico", "Elio e le Storie Tese", "'Ahi, ahi, ahi, la mia testa... [...] a testa alta, verso il centro sociale...'"]},
    {"target": "VIENI A BALLARE IN PUGLIA", "indizi": ["2008 / Hip-Hop", "Caparezza", "'[...] [...] [...] [...] dove la notte è calda e l'atmosfera è tesa...'"]},
    {"target": "LA PRIMA COSA BELLA", "indizi": ["1970 / Pop-Melodico", "Ricchi e Poveri / Nicola Di Bari", "'Prendo la chitarra e suono per te, il tempo di imparare e ti suonerò...'"]},
    {"target": "TUTTA MIA LA CITTA", "indizi": ["1969 / Beat-Pop", "Equipe 84", "'Un passo avanti a me, il vuoto intorno a me...'"]},
    {"target": "SAPORE DI SALE", "indizi": ["1963 / Cantautorato", "Gino Paoli", "'[...] [...] [...], sapore di mare, che hai sulla pelle, che hai sulle labbra...'"]},
    {"target": "STESSA SPIAGGIA STESSO MARE", "indizi": ["1963 / Twist-Pop", "Piero Focaccia / Mina", "'[...] [...] [...] [...] per poterti rivedere, per dirti che sono sempre quel ragazzo che ti amava tanto...'"]},
    {"target": "C'E CHI DICE NO", "indizi": ["1987 / Rock", "Vasco Rossi", "'C'è chi dice no, c'è chi dice no, io non ci sono più...'"]},
    {"target": "BUONANOTTE FIORELLINO", "indizi": ["1975 / Cantautorato Folk", "Francesco De Gregori", "'[...] [...] buonanotte tra le stelle e la stanza, per poterti far sognare...'"]},
    {"target": "I MASCHI", "indizi": ["1987 / Rock", "Gianna Nannini", "'[...] [...] innamorati dentro ai bar, i maschi con le braccia conserte...'"]},
    {"target": "NOSTALGIA CANAGLIA", "indizi": ["1987 / Pop", "Al Bano & Romina Power", "'Che cos'è questo vuoto che sento... è un ricordo che torna nel tempo...'"]}
]

# --- FLASK KEEP ALIVE ---
app = Flask(__name__)

@app.route('/')
def home():
    return "SdrogoBot v5.2 Attivo H24!"

def run_flask():
    port = int(os.environ.get('PORT', 8080))
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    app.run(host='0.0.0.0', port=port)

# --- AUTO-RESTORE DATABASE DA MESSAGGIO FISSATO IN CHAT BACKUP ---
async def auto_restore_from_telegram(bot):
    global USER_DATA
    if not BACKUP_CHAT_ID: return
    try:
        chat_id = int(BACKUP_CHAT_ID)
        if not os.path.exists(DB_FILE):
            print("📦 Ricerca messaggio fissato per il ripristino...", flush=True)
            chat = await bot.get_chat(chat_id)
            if chat.pinned_message and chat.pinned_message.document:
                file_info = await bot.get_file(chat.pinned_message.document.file_id)
                await file_info.download_to_drive(DB_FILE)
                print("✅ Database ripristinato con successo dal messaggio fissato!", flush=True)
                load_db()
            else:
                print("⚠️ Nessun messaggio fissato con documento trovato nella chat di backup.", flush=True)
    except Exception as e:
        logging.error(f"Errore Auto-Restore da messaggio fissato: {e}")

def load_db():
    global USER_DATA
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r") as f:
                USER_DATA = json.load(f)
        except Exception as e:
            logging.error(f"Errore caricamento DB: {e}")
            USER_DATA = {}

def save_db():
    try:
        with open(DB_FILE, "w") as f:
            json.dump(USER_DATA, f, indent=2)
    except Exception as e:
        logging.error(f"Errore salvataggio DB: {e}")

async def backup_to_telegram(context: ContextTypes.DEFAULT_TYPE):
    if BACKUP_CHAT_ID and os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "rb") as f:
                await context.bot.send_document(
                    chat_id=int(BACKUP_CHAT_ID),
                    document=f,
                    caption=f"Backup DB SdrogoBot - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                )
        except Exception as e:
            logging.error(f"Errore backup Telegram: {e}")

def get_user_key(chat_id: int, user_id: int) -> str:
    return f"{chat_id}_{user_id}"

def get_user_coins(chat_id: int, user_id: int) -> int:
    key = get_user_key(chat_id, user_id)
    if key not in USER_DATA:
        USER_DATA[key] = {"coins": 50, "last_daily": ""}
        if str(chat_id) != str(BACKUP_CHAT_ID):
            save_db()
    return USER_DATA[key].get("coins", 50)

def add_user_coins(chat_id: int, user_id: int, amount: int):
    if str(chat_id) == str(BACKUP_CHAT_ID):
        return
    key = get_user_key(chat_id, user_id)
    if key not in USER_DATA:
        USER_DATA[key] = {"coins": 50, "last_daily": ""}
    USER_DATA[key]["coins"] = max(0, USER_DATA[key].get("coins", 50) + amount)
    save_db()

def is_admin(user_id: int) -> bool:
    return str(user_id) == str(ADMIN_ID) if ADMIN_ID else False

async def verify_user_lock(query, owner_id: int) -> bool:
    if query.from_user.id != owner_id:
        await query.answer("🛑 Questo menu appartiene a un altro giocatore! Apri il tuo con /sdrogocomm.", show_alert=True)
        return False
    return True

def get_formatted_name(chat_id: int, user_id: int, default_name: str) -> str:
    key = f"{chat_id}_{user_id}"
    stars_str = ""
    
    if key in USER_INVENTORIES:
        stars = USER_INVENTORIES[key].get("stars", 0)
        if stars > 0:
            stars_str = " " + ("⭐" * min(stars, 5))

    if key in ACTIVE_TITLES:
        title_data = ACTIVE_TITLES[key]
        if datetime.now() < title_data["expire"]:
            return f"{title_data['title']} {default_name}{stars_str}"
        else:
            del ACTIVE_TITLES[key]
            
    return f"{default_name}{stars_str}"

# --- SDROGOBOT HUB (/sdrogocomm) ---
async def show_hub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    coins = get_user_coins(chat_id, user.id)
    display_name = get_formatted_name(chat_id, user.id, user.first_name)
    
    text = (
        "🎰 <b>SDROGOBOT ARCADE HUB</b> 🎮\n\n"
        f"👤 <b>Player:</b> {display_name}\n"
        f"💰 <b>Saldo:</b> <code>💳 {coins} $SDG</code>\n\n"
        "<i>Seleziona una categoria per iniziare:</i>"
    )
    
    keyboard = [
        [InlineKeyboardButton("🕹️ Single Player", callback_data=f"hub_single_{user.id}"), InlineKeyboardButton("⚔️ Multiplayer", callback_data=f"hub_multi_{user.id}")],
        [InlineKeyboardButton("🧠 Quiz Show", callback_data=f"hub_quiz_{user.id}"), InlineKeyboardButton("🛒 SdrogoShop", callback_data=f"hub_shop_{user.id}")],
        [InlineKeyboardButton("💳 Portafoglio", callback_data=f"hub_wallet_{user.id}"), InlineKeyboardButton("🏆 Classifica", callback_data=f"hub_lead_{user.id}")]
    ]
    
    if update.message:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    elif update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

async def hub_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    parts = data.split("_")
    action = parts[1]
    owner_id = int(parts[2]) if len(parts) > 2 else query.from_user.id

    if not await verify_user_lock(query, owner_id):
        return

    chat_id = query.message.chat_id
    user_id = query.from_user.id
    coins = get_user_coins(chat_id, user_id)

    back_button = [InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{owner_id}")]

    if action == "main":
        await show_hub(update, context)

    elif action == "single":
        text = (
            "🕹️ <b>GIOCHI SINGLE PLAYER</b>\n\n"
            "🃏 <b>Blackjack 21</b> — <i>10 $SDG</i>\n"
            "🎰 <b>Slot Machine 777</b> — <i>10 $SDG</i>\n"
            "🔠 <b>Wordle Express</b> — <i>10 $SDG</i>\n"
            "🔐 <b>Mastermind Express</b> — <i>10 $SDG</i>"
        )
        keyboard = [
            [InlineKeyboardButton("🃏 Blackjack", callback_data=f"start_bj_{owner_id}"), InlineKeyboardButton("🎰 Slot 777", callback_data=f"start_slot_{owner_id}")],
            [InlineKeyboardButton("🔠 Wordle", callback_data=f"start_wordle_{owner_id}"), InlineKeyboardButton("🔐 Mastermind", callback_data=f"start_mm_{owner_id}")],
            back_button
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif action == "multi":
        text = (
            "⚔️ <b>GIOCHI MULTIPLAYER</b>\n\n"
            "🎯 <b>Roulette Russa 1v1</b>\n"
            "🎲 <b>High / Low 1v1</b>\n"
            "🪓 <b>Ghigliottina Express 1v1</b> (`sfidoghigliottina @user`)\n"
            "⚔️ <b>Duello Quiz 1v1</b> (`sfidoquiz @user`)\n"
            "🌐 <b>Quiz Multiplayer</b> (Aperto a tutto il gruppo)"
        )
        keyboard = [
            [InlineKeyboardButton("🎯 Roulette 1v1", callback_data=f"start_roulette_{owner_id}"), InlineKeyboardButton("🎲 High/Low 1v1", callback_data=f"start_highlow_{owner_id}")],
            [InlineKeyboardButton("🪓 Ghigliottina 1v1", callback_data=f"start_ghigliottina_prep_{owner_id}"), InlineKeyboardButton("⚔️ Duello Quiz 1v1", callback_data=f"start_quiz1v1_prep_{owner_id}")],
            [InlineKeyboardButton("🌐 Quiz Multi (Scegli Categoria)", callback_data=f"hub_qmulti_{owner_id}")],
            back_button
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif action == "qmulti":
        text = "🌐 <b>QUIZ MULTIPLAYER PER CATEGORIA</b>\n\nScegli la categoria da lanciare in chat di gruppo:"
        keyboard = [
            [InlineKeyboardButton("🎲 Casuale", callback_data=f"start_qmulti_ALL_{owner_id}"), InlineKeyboardButton("⚽ Calcio", callback_data=f"start_qmulti_CALCIO_{owner_id}")],
            [InlineKeyboardButton("🏎️ Formula 1", callback_data=f"start_qmulti_F1_{owner_id}"), InlineKeyboardButton("🦸 Marvel & DC", callback_data=f"start_qmulti_MARVEL_{owner_id}")],
            [InlineKeyboardButton("🎬 Cinema", callback_data=f"start_qmulti_CINEMA_{owner_id}"), InlineKeyboardButton("📺 Serie TV", callback_data=f"start_qmulti_SERIE_{owner_id}")],
            [InlineKeyboardButton("🗺️ Paesi", callback_data=f"start_qmulti_PAESI_{owner_id}"), InlineKeyboardButton("🏮 Anime", callback_data=f"start_qmulti_ANIME_{owner_id}")],
            [InlineKeyboardButton("🏷️ Brand", callback_data=f"start_qmulti_BRANDS_{owner_id}"), InlineKeyboardButton("📜 Personaggi", callback_data=f"start_qmulti_PERSONAGGI_{owner_id}")],
            [InlineKeyboardButton("🎵 Canzoni", callback_data=f"start_qmulti_CANZONI_{owner_id}")],
            back_button
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif action == "quiz":
        text = (
            "🧠 <b>QUIZ SHOW SINGLE PLAYER</b> (5 $SDG)\n\n"
            "Scegli una categoria:"
        )
        keyboard = [
            [InlineKeyboardButton("⚽ Calcio", callback_data=f"start_qcalcio_{owner_id}"), InlineKeyboardButton("🎬 Cinema", callback_data=f"start_qcinema_{owner_id}")],
            [InlineKeyboardButton("📺 Serie TV", callback_data=f"start_qserie_{owner_id}"), InlineKeyboardButton("🏎️ Formula 1", callback_data=f"start_qf1_{owner_id}")],
            [InlineKeyboardButton("🦸 Marvel & DC", callback_data=f"start_qmarvel_{owner_id}"), InlineKeyboardButton("🗺️ Paesi", callback_data=f"start_qpaesi_{owner_id}")],
            [InlineKeyboardButton("🏮 Anime", callback_data=f"start_qanime_{owner_id}"), InlineKeyboardButton("🏷️ Brand", callback_data=f"start_qbrands_{owner_id}")],
            [InlineKeyboardButton("📜 Personaggi", callback_data=f"start_qpersonaggi_{owner_id}"), InlineKeyboardButton("🎵 Canzoni", callback_data=f"start_qcanzoni_{owner_id}")],
            back_button
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif action == "shop":
        inv_key = f"{chat_id}_{user_id}"
        inv = USER_INVENTORIES.get(inv_key, {"titles": 0, "persecutes": 0})
        
        text = (
            "🛒 <b>SDROGOSHOP</b>\n\n"
            f"📦 <b>Inventario:</b> {inv.get('titles', 0)} Titoli | {inv.get('persecutes', 0)} Persecuzioni\n\n"
            "🏷️ <b>1. Titolo Umiliante (100 $SDG)</b>\nAssegna '🏳️‍🌈GAY🏳️‍🌈' a una vittima per 24 ore!\n\n"
            "🗣️ <b>2. Tag Persecutore (120 $SDG)</b>\nIl bot risponde 'frocio hah' ai prossimi 15 messaggi!\n\n"
            "🏢 <b>3. Pass SDROGO HEIST (350 $SDG)</b>\nRapina a 5 livelli in PRIVATO col bot per Jackpot + Stelle!"
        )
        keyboard = [
            [InlineKeyboardButton("🏷️ Compra Titolo (100 $SDG)", callback_data=f"buy_title_{owner_id}")],
            [InlineKeyboardButton("🗣️ Compra Tag Persecutore (120 $SDG)", callback_data=f"buy_persecute_{owner_id}")],
            [InlineKeyboardButton("🏢 Avvia SDROGO HEIST (350 $SDG)", callback_data=f"buy_heist_{owner_id}")],
            back_button
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif action == "wallet":
        text = (
            "💳 <b>PORTAFOGLIO</b>\n\n"
            f"👤 Giocatore: <b>{query.from_user.first_name}</b>\n"
            f"💰 Saldo attuale: <code>💳 {coins} $SDG</code>\n\n"
            "🎁 <b>Bonus Daily:</b> Riscuoti 50 $SDG ogni 24 ore."
        )
        keyboard = [
            [InlineKeyboardButton("🎁 Riscuoti Daily (+50 $SDG)", callback_data=f"claim_daily_{owner_id}")],
            back_button
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif action == "lead":
        await show_leaderboard(update, context, owner_id)

# --- SHOP ACTIONS ---
async def shop_buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split("_")
    item_type = parts[1]
    owner_id = int(parts[2]) if len(parts) > 2 else query.from_user.id

    if not await verify_user_lock(query, owner_id):
        return

    chat_id = query.message.chat_id
    user_id = query.from_user.id
    coins = get_user_coins(chat_id, user_id)
    inv_key = f"{chat_id}_{user_id}"

    if inv_key not in USER_INVENTORIES:
        USER_INVENTORIES[inv_key] = {"titles": 0, "persecutes": 0, "stars": 0}

    if item_type == "title":
        if coins < 100:
            await query.answer("❌ Servono 100 $SDG per comprare il Titolo Umiliante!", show_alert=True)
            return
        add_user_coins(chat_id, user_id, -100)
        USER_INVENTORIES[inv_key]["titles"] += 1
        await query.edit_message_text(
            "✅ <b>TITOLO UMILIANTE ACQUISTATO!</b>\n\n"
            "Per assegnarlo per 24 ORE a una vittima, scrivi in chat:\n"
            "👉 <code>titolo @username</code> (oppure rispondi al suo messaggio con <code>titolo</code>)",
            parse_mode="HTML"
        )

    elif item_type == "persecute":
        if coins < 120:
            await query.answer("❌ Servono 120 $SDG per comprare il Tag Persecutore!", show_alert=True)
            return
        add_user_coins(chat_id, user_id, -120)
        USER_INVENTORIES[inv_key]["persecutes"] += 1
        await query.edit_message_text(
            "✅ <b>TAG PERSECUTORE ACQUISTATO!</b>\n\n"
            "Per perseguitare una vittima per 15 messaggi, scrivi in chat:\n"
            "👉 <code>perseguita @username</code>",
            parse_mode="HTML"
        )

    elif item_type == "heist":
        if coins < 350:
            await query.answer("❌ Servono 350 $SDG per tentare la Rapina Heist!", show_alert=True)
            return
        add_user_coins(chat_id, user_id, -350)
        
        HEIST_GAMES[user_id] = {"level": 1, "chat_id": chat_id}
        
        try:
            keyboard = [[InlineKeyboardButton("🔓 Disattiva Allarme (Livello 1)", callback_data=f"heist_lvl1_{user_id}")]]
            await context.bot.send_message(
                chat_id=user_id,
                text="🏢 <b>SDROGO HEIST - LA RAPINA AL CAVEAU</b> 🕵️‍♂️\n\nBenvenuto al Livello 1! Devi disattivare l'allarme per entrare.",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )
            await query.edit_message_text("🏢 <b>LA RAPINA È INIZIATA!</b> Controlla la tua chat PRIVATA con SdrogoBot per giocare!", parse_mode="HTML")
        except Exception:
            add_user_coins(chat_id, user_id, 350)
            await query.edit_message_text("❌ Devi prima avviare il bot in chat PRIVATA per giocare a Sdrogo Heist!", parse_mode="HTML")

# --- GAME: SDROGO HEIST ---
async def handle_heist_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split("_")
    stage = parts[1]
    owner_id = int(parts[2])

    if query.from_user.id != owner_id:
        return

    user_id = query.from_user.id
    if user_id not in HEIST_GAMES:
        await query.edit_message_text("❌ Sessione Rapina terminata.")
        return

    game = HEIST_GAMES[user_id]
    chat_id = game["chat_id"]

    if stage == "lvl1":
        keyboard = [
            [InlineKeyboardButton("🔴 Cavo Rosso", callback_data=f"heist_lvl1res_fail_{user_id}")],
            [InlineKeyboardButton("🔵 Cavo Blu (Corretto)", callback_data=f"heist_lvl1res_win_{user_id}")],
            [InlineKeyboardButton("🟡 Cavo Giallo", callback_data=f"heist_lvl1res_fail_{user_id}")]
        ]
        random.shuffle(keyboard)
        await query.edit_message_text("🔓 <b>LIVELLO 1: DISATTIVAZIONE ALLARME</b>\n\nQuale cavo tagli per disattivare l'allarme?", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif stage == "lvl1res":
        outcome = parts[2]
        if outcome == "fail":
            del HEIST_GAMES[user_id]
            await query.edit_message_text("💥 <b>ALLARME SCATTATO!</b> Le guardie ti hanno preso. Fuga fallita!")
        else:
            game["level"] = 2
            keyboard = [
                [InlineKeyboardButton("💰 CASHOUT (Prendi 💳 50 $SDG ed esci)", callback_data=f"heist_cashout_50_{user_id}")],
                [InlineKeyboardButton("🔥 RISCHIA IL LIVELLO 2 (Guardia)", callback_data=f"heist_lvl2_{user_id}")]
            ]
            await query.edit_message_text("✅ <b>LIVELLO 1 SUPERATO!</b>\nPremio accumulato: <code>💳 50 $SDG</code>.\n\nCosa vuoi fare?", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif stage == "lvl2":
        p_hand = random.randint(15, 21)
        g_hand = random.randint(14, 21)
        
        if p_hand >= g_hand:
            game["level"] = 3
            keyboard = [
                [InlineKeyboardButton("💰 CASHOUT (Prendi 💳 100 $SDG ed esci)", callback_data=f"heist_cashout_100_{user_id}")],
                [InlineKeyboardButton("🔥 RISCHIA IL LIVELLO 3 (Laser)", callback_data=f"heist_lvl3_{user_id}")]
            ]
            await query.edit_message_text(f"👮 <b>LIVELLO 2 SUPERATO!</b>\nHai messo KO la guardia ({p_hand} vs {g_hand})!\nPremio accumulato: <code>💳 100 $SDG</code>.", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
        else:
            del HEIST_GAMES[user_id]
            await query.edit_message_text(f"👮 <b>LA GUARDIA TI HA VISTO!</b> ({g_hand} vs {p_hand})\nSei stato arrestato! Fuga fallita.")

    elif stage == "lvl3":
        keyboard = [
            [InlineKeyboardButton("🚪 Porta A", callback_data=f"heist_lvl3res_fail_{user_id}")],
            [InlineKeyboardButton("🚪 Porta B", callback_data=f"heist_lvl3res_win_{user_id}")],
            [InlineKeyboardButton("🚪 Porta C", callback_data=f"heist_lvl3res_fail_{user_id}")]
        ]
        random.shuffle(keyboard)
        await query.edit_message_text("⚡ <b>LIVELLO 3: CAMPO LASER</b>\n\nTre porte davanti a te. Solo una non ha i laser attivi!", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif stage == "lvl3res":
        outcome = parts[2]
        if outcome == "fail":
            del HEIST_GAMES[user_id]
            await query.edit_message_text("⚡ <b>COLPITO DAL LASER!</b> L'allarme è scattato. Fuga fallita!")
        else:
            game["level"] = 4
            keyboard = [
                [InlineKeyboardButton("💰 CASHOUT (Prendi 💳 180 $SDG ed esci)", callback_data=f"heist_cashout_180_{user_id}")],
                [InlineKeyboardButton("🔥 RISCHIA IL LIVELLO 4 (Cassaforte)", callback_data=f"heist_lvl4_{user_id}")]
            ]
            await query.edit_message_text("⚡ <b>LIVELLO 3 SUPERATO!</b>\nPremio accumulato: <code>💳 180 $SDG</code>.\n\nCosa vuoi fare?", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif stage == "lvl4":
        keyboard = [
            [InlineKeyboardButton("🔐 Codice 4-8-1 (Sbagliato)", callback_data=f"heist_lvl4res_fail_{user_id}")],
            [InlineKeyboardButton("🔐 Codice 7-7-7 (Sbagliato)", callback_data=f"heist_lvl4res_fail_{user_id}")],
            [InlineKeyboardButton("🔐 Codice 1-2-3 (Corretto)", callback_data=f"heist_lvl4res_win_{user_id}")]
        ]
        random.shuffle(keyboard)
        await query.edit_message_text("🔐 <b>LIVELLO 4: LA CASSAFORTE</b>\n\nTrova la combinazione corretta prima che scada il tempo!", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif stage == "lvl4res":
        outcome = parts[2]
        if outcome == "fail":
            del HEIST_GAMES[user_id]
            await query.edit_message_text("💥 <b>COMBINAZIONE ERRATA!</b> La cassaforte si è bloccata. Fuga fallita!")
        else:
            game["level"] = 5
            keyboard = [
                [InlineKeyboardButton("💰 CASHOUT (Prendi 💳 300 $SDG ed esci)", callback_data=f"heist_cashout_300_{user_id}")],
                [InlineKeyboardButton("🔥 SFIDA IL LIVELLO 5 FINALE!", callback_data=f"heist_lvl5_{user_id}")]
            ]
            await query.edit_message_text("🔐 <b>LIVELLO 4 SUPERATO!</b>\nPremio accumulato: <code>💳 300 $SDG</code>.\n\nSei ad un passo dalla gloria!", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif stage == "lvl5":
        keyboard = [
            [InlineKeyboardButton("🚁 Elicottero sul Tetto", callback_data=f"heist_lvl5res_win_{user_id}")],
            [InlineKeyboardButton("🚗 Fuga in Tunnel", callback_data=f"heist_lvl5res_fail_{user_id}")]
        ]
        await query.edit_message_text("🚁 <b>LIVELLO 5: LA FUGA FINALE</b>\n\nCome scappi col bottino?", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif stage == "lvl5res":
        outcome = parts[2]
        if outcome == "fail":
            del HEIST_GAMES[user_id]
            await query.edit_message_text("🚔 <b>LA POLIZIA TI HA CIRCONDATO IN TUNNEL!</b> Fuga fallita all'ultimo secondo!")
        else:
            del HEIST_GAMES[user_id]
            add_user_coins(chat_id, user_id, 600)
            
            inv_key = f"{chat_id}_{user_id}"
            if inv_key not in USER_INVENTORIES: USER_INVENTORIES[inv_key] = {"titles": 0, "persecutes": 0, "stars": 0}
            USER_INVENTORIES[inv_key]["stars"] = USER_INVENTORIES[inv_key].get("stars", 0) + 1
            USER_INVENTORIES[inv_key]["titles"] += 1
            USER_INVENTORIES[inv_key]["persecutes"] += 1
            
            await query.edit_message_text("🏆 <b>RAPINA PERFETTA COMPLETATA!</b>\nHai vinto +600 $SDG, 1 Titolo e 1 Persecuzione Gratis + 1 STELLA ⭐!")
            
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"👑 <b>COLPO DEL SECOLO!</b> 🏢\n\n<b>{query.from_user.first_name}</b> ha svaligiato il Caveau di Sdrogo Heist arrivando al 5° Livello!\nGuadagna <b>💳 600 $SDG</b> e 1 STELLA ⭐ di prestigio in classifica!",
                    parse_mode="HTML"
                )
            except Exception: pass

    elif stage == "cashout":
        amount = int(parts[2])
        del HEIST_GAMES[user_id]
        add_user_coins(chat_id, user_id, amount)
        await query.edit_message_text(f"💰 <b>CASHOUT EFFETTUATO!</b> Ti ritiri dalla rapina incassando <b>+💳 {amount} $SDG</b>!")

# --- COMANDI SHOP ---
async def apply_title_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    inv_key = f"{chat_id}_{user.id}"
    text = (update.message.text or "").strip()

    if inv_key not in USER_INVENTORIES or USER_INVENTORIES[inv_key].get("titles", 0) <= 0:
        await update.message.reply_text("❌ Non possiedi alcun Titolo Umiliante nel tuo inventario dello /shop!", parse_mode="HTML")
        return

    target_username = None
    for part in text.split():
        if part.startswith("@"):
            target_username = part.replace("@", "").lower()
            break

    if not target_username and update.message.reply_to_message and update.message.reply_to_message.from_user:
        target_username = update.message.reply_to_message.from_user.username.lower() if update.message.reply_to_message.from_user.username else None

    if not target_username:
        await update.message.reply_text("❌ Uso: <code>titolo @username</code> oppure rispondi al suo messaggio con <code>titolo</code>!", parse_mode="HTML")
        return

    target_id = None
    prefix = f"{chat_id}_"
    for k in USER_DATA.keys():
        if k.startswith(prefix):
            uid = k.split("_")[1]
            try:
                m = await context.bot.get_chat_member(chat_id, int(uid))
                if m.user.username and m.user.username.lower() == target_username:
                    target_id = int(uid)
                    break
            except Exception: pass

    if not target_id:
        await update.message.reply_text("❌ Utente non trovato nel registro della chat!", parse_mode="HTML")
        return

    USER_INVENTORIES[inv_key]["titles"] -= 1
    expire_time = datetime.now() + timedelta(hours=24)
    ACTIVE_TITLES[f"{chat_id}_{target_id}"] = {"title": "🏳️‍🌈GAY🏳️‍🌈", "expire": expire_time}
    await update.message.reply_text(f"🔥 <b>TITOLO ASSEGNATO!</b> Per 24 ORE @{target_username} sarà chiamato '🏳️‍🌈GAY🏳️‍🌈' dal bot!", parse_mode="HTML")

async def apply_persecute_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    inv_key = f"{chat_id}_{user.id}"
    text = (update.message.text or "").strip()

    if inv_key not in USER_INVENTORIES or USER_INVENTORIES[inv_key].get("persecutes", 0) <= 0:
        await update.message.reply_text("❌ Non possiedi alcun Tag Persecutore nel tuo inventario dello /shop!", parse_mode="HTML")
        return

    target_username = None
    for part in text.split():
        if part.startswith("@"):
            target_username = part.replace("@", "").lower()
            break

    if not target_username:
        await update.message.reply_text("❌ Uso corretto: <code>perseguita @username</code>", parse_mode="HTML")
        return

    USER_INVENTORIES[inv_key]["persecutes"] -= 1
    ACTIVE_PERSECUTE[f"{chat_id}_{target_username}"] = {"count": 15, "phrase": "frocio hah"}
    await update.message.reply_text(f"😈 <b>PERSECUZIONE ATTIVATA!</b> I prossimi 15 messaggi di @{target_username} riceveranno risposta 'frocio hah' dal bot!", parse_mode="HTML")

# --- GAME: SLOT MACHINE 777 ---
async def start_slot_from_hub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split("_")
    owner_id = int(parts[2]) if len(parts) > 2 else query.from_user.id

    if not await verify_user_lock(query, owner_id): return
    user = query.from_user
    chat_id = query.message.chat_id

    if get_user_coins(chat_id, user.id) < 10:
        await query.answer("❌ Servono 10 $SDG per girare la Slot!", show_alert=True)
        return

    add_user_coins(chat_id, user.id, -10)
    symbols = ["🍒", "🍋", "🔔", "💎", "7️⃣"]
    r1, r2, r3 = random.choice(symbols), random.choice(symbols), random.choice(symbols)

    await query.edit_message_text(
        f"🎰 <b>SLOT MACHINE 777</b> 🎰\n👤 Player: <b>{user.first_name}</b>\n\n"
        f"[ {r1} | 🔄 | ❓ ]\n\n<i>Giro rulli in corso...</i>",
        parse_mode="HTML"
    )
    await asyncio.sleep(0.6)

    await query.edit_message_text(
        f"🎰 <b>SLOT MACHINE 777</b> 🎰\n👤 Player: <b>{user.first_name}</b>\n\n"
        f"[ {r1} | {r2} | 🔄 ]\n\n<i>Giro rulli in corso...</i>",
        parse_mode="HTML"
    )
    await asyncio.sleep(0.6)

    text = f"🎰 <b>SLOT MACHINE 777</b> 🎰\n👤 Player: <b>{user.first_name}</b>\n\n[ {r1} | {r2} | {r3} ]\n\n"

    end_keyboard = [
        [InlineKeyboardButton("🔂 Rigioca (10 $SDG)", callback_data=f"start_slot_{owner_id}")],
        [InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{owner_id}")]
    ]

    if r1 == r2 == r3:
        if r1 == "7️⃣":
            add_user_coins(chat_id, user.id, 150)
            text += "🔥 <b>JACKPOT SUPREMO 777!</b> 🔥 Hai vinto <b>+💳 150 $SDG</b>!"
        else:
            add_user_coins(chat_id, user.id, 30)
            text += "🎉 <b>TRIPLETTA VINCENTE!</b> Hai vinto <b>+💳 30 $SDG</b>!"
    elif r1 == r2 or r2 == r3 or r1 == r3:
        add_user_coins(chat_id, user.id, 10)
        text += "✨ <b>DOPPIETTA!</b> Recuperi i tuoi 10 $SDG!"
    else:
        text += "💸 <b>NESSUNA COMBINAZIONE!</b> Hai perso 10 $SDG."

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(end_keyboard), parse_mode="HTML")

# --- GAME: MASTERMIND EXPRESS ---
async def start_mastermind_from_hub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split("_")
    owner_id = int(parts[2]) if len(parts) > 2 else query.from_user.id

    if not await verify_user_lock(query, owner_id): return
    user = query.from_user
    chat_id = query.message.chat_id
    game_key = f"{chat_id}_{user.id}"

    if get_user_coins(chat_id, user.id) < 10:
        await query.answer("❌ Servono 10 $SDG per giocare a Mastermind!", show_alert=True)
        return

    add_user_coins(chat_id, user.id, -10)
    digits = list("0123456789")
    random.shuffle(digits)
    secret_code = "".join(digits[:3])

    MASTERMIND_GAMES[game_key] = {
        "player_id": user.id, "secret": secret_code,
        "attempts": 0, "history": []
    }

    await query.edit_message_text(
        "🔐 <b>MASTERMIND EXPRESS</b> (Puntata: 10 $SDG)\n\n"
        "Ho scelto un codice segreto di <b>3 cifre uniche</b>!\n"
        "Scrivilo direttamente in chat per tentare (5 tentativi).",
        parse_mode="HTML"
    )

# --- GAME: HIGHLOW 1v1 ---
async def start_highlow_prep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    parts = query.data.split("_")
    owner_id = int(parts[2]) if len(parts) > 2 else query.from_user.id

    if not await verify_user_lock(query, owner_id): return

    await query.edit_message_text(
        "🎲 <b>HIGH / LOW 1v1 (DADO DELLA MORTE)</b>\n\n"
        "Scrivi in chat il nome della tua vittima per sfidarla sul dado:\n\n"
        "👉 <code>sfido highlow @username</code>",
        parse_mode="HTML"
    )

async def handle_highlow_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    chat_id = query.message.chat_id
    user = query.from_user

    if chat_id not in HIGHLOW_DUELS:
        await query.answer("⚠️ Sfida High/Low non attiva.", show_alert=True)
        return

    game = HIGHLOW_DUELS[chat_id]

    if query.data == "hl_accetta":
        if user.username and user.username.lower() != game["target_username"]:
            await query.answer("❌ Solo lo sfidato può accettare!", show_alert=True)
            return

        game["target_id"] = user.id
        game["target_name"] = user.first_name
        game["turno_id"] = random.choice([game["sfidante_id"], user.id])
        game["val"] = random.randint(2, 11)

        turno_nome = game["sfidante_name"] if game["turno_id"] == game["sfidante_id"] else game["target_name"]

        keyboard = [[
            InlineKeyboardButton("📈 PIÙ ALTO", callback_data="hl_guess_high"),
            InlineKeyboardButton("📉 PIÙ BASSO", callback_data="hl_guess_low")
        ]]

        await query.edit_message_text(
            f"🎲 <b>DADO DELLA MORTE 1v1</b>\n\n"
            f"🎯 Numero estratto: <b>{game['val']}</b> (da 1 a 12)\n\n"
            f"👉 Tocca a <b>{turno_nome}</b>: Il prossimo numero sarà Più Alto o Più Basso?",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )

    elif query.data in ["hl_guess_high", "hl_guess_low"]:
        if user.id != game["turno_id"]:
            await query.answer("✋ Non è il tuo turno!", show_alert=True)
            return

        old_val = game["val"]
        new_val = random.randint(1, 12)
        while new_val == old_val: new_val = random.randint(1, 12)

        choice = query.data
        won = (choice == "hl_guess_high" and new_val > old_val) or (choice
