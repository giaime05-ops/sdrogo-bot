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
    "TARLO", "VALLO", "ZOLLA", "ABETO", "BORGO", "CREPA", "DOGMA", "ELICA", "FOSSA", "GRANA",
    "LAMPO", "MAREA", "NAPPA", "ORAFO", "PRATO", "QUOTA", "SFERA", "TRONO", "VARCO", "ZANNA",
    "BLUSA", "CRASI", "EGIDA", "FATUO", "ILICE", "LAICO", "MIGMA", "NENIA", "ONICE", "PRODA",
    "REUMA", "ZURRO", "ALATO", "BIZZA", "DOTTO", "ETERE", "FALCE", "IRIDE", "LESTO", "AUREO",
    "ANICE", "CONCA", "EREBO", "ILOTE", "LIBRA", "MOINA", "NAFTA", "OPALE", "PLICO", "TURBA"
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
    {"target": "ILICIC", "indizi": ["Slovenia", "Atalanta, Palermo", "Poker magico in trasferta in Champions prima che le ombre oscurassero il suo talento."]},
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
    {"target": "TSHABALALA", "indizi": ["Sudafrica", "Kaizer Chiefs", "Il mancino all'incrocio che fece esplodere le vuvuzelas nella gara d'apertura 2010."]},
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
    {"target": "NARCOS", "indizi": ["Crime / Biografico", "Baffi e mazzette di banconote", "La caccia delle forze dell'ordine ai re del narcotraffico."]},
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

# --- DATABASE 100 CITTA ---
QUIZ_CITTA_DB = [
    {"target": "ROMA", "indizi": ["Europa", "Italia", "Colosseo"]},
    {"target": "MILANO", "indizi": ["Europa", "Italia", "Duomo"]},
    {"target": "VENEZIA", "indizi": ["Europa", "Italia", "Piazza San Marco e Canal Grande"]},
    {"target": "FIRENZE", "indizi": ["Europa", "Italia", "Cupola del Brunelleschi e Ponte Vecchio"]},
    {"target": "NAPOLI", "indizi": ["Europa", "Italia", "Vesuvio e Spaccanapoli"]},
    {"target": "PISA", "indizi": ["Europa", "Italia", "Torre Pendente"]},
    {"target": "TORINO", "indizi": ["Europa", "Italia", "Mole Antonelliana"]},
    {"target": "VERONA", "indizi": ["Europa", "Italia", "Arena e Balcone di Giulietta"]},
    {"target": "PALERMO", "indizi": ["Europa", "Italia", "Cattedrale e Teatro Massimo"]},
    {"target": "BOLOGNA", "indizi": ["Europa", "Italia", "Le Due Torri (Asinelli e Garisenda)"]},
    {"target": "PARIGI", "indizi": ["Europa", "Francia", "Torre Eiffel"]},
    {"target": "NIZZA", "indizi": ["Europa", "Francia", "Promenade des Anglais"]},
    {"target": "LIONE", "indizi": ["Europa", "Francia", "Basilica di Notre-Dame de Fourvière"]},
    {"target": "LONDRA", "indizi": ["Europa", "Regno Unito", "Big Ben e Tower Bridge"]},
    {"target": "EDIMBURGO", "indizi": ["Europa", "Regno Unito", "Castello sulla roccia"]},
    {"target": "BARCELLONA", "indizi": ["Europa", "Spagna", "Sagrada Família"]},
    {"target": "MADRID", "indizi": ["Europa", "Spagna", "Museo del Prado e Puerta del Sol"]},
    {"target": "SIVIGLIA", "indizi": ["Europa", "Spagna", "Plaza de España e Giralda"]},
    {"target": "VALENCIA", "indizi": ["Europa", "Spagna", "Città delle Arti e delle Scienze"]},
    {"target": "BERLINO", "indizi": ["Europa", "Germania", "Porta di Brandeburgo"]},
    {"target": "MONACO DI BAVIERA", "indizi": ["Europa", "Germania", "Marienplatz"]},
    {"target": "FRANCOFORTE", "indizi": ["Europa", "Germania", "Grattacieli del quartiere finanziario"]},
    {"target": "AMSTERDAM", "indizi": ["Europa", "Paesi Bassi", "Canali e Museo di Van Gogh"]},
    {"target": "ROTTERDAM", "indizi": ["Europa", "Paesi Bassi", "Case Cubiche di Piet Blom"]},
    {"target": "BRUXELLES", "indizi": ["Europa", "Belgio", "Atomium e Grand Place"]},
    {"target": "VIENNA", "indizi": ["Europa", "Austria", "Castello di Schönbrunn"]},
    {"target": "SALISBURGO", "indizi": ["Europa", "Austria", "Casa natale di Mozart"]},
    {"target": "PRAGA", "indizi": ["Europa", "Repubblica Ceca", "Ponte Carlo e Orologio Astronomico"]},
    {"target": "BUDAPEST", "indizi": ["Europa", "Ungheria", "Parlamento sul Danubio"]},
    {"target": "ATENE", "indizi": ["Europa", "Grecia", "Partenone sull'Acropoli"]},
    {"target": "SANTORINI", "indizi": ["Europa", "Grecia", "Casette bianche con cupole blu"]},
    {"target": "LISBONA", "indizi": ["Europa", "Portogallo", "Torre di Belém e Tram 28"]},
    {"target": "PORTO", "indizi": ["Europa", "Portogallo", "Ponte Dom Luís I"]},
    {"target": "DUBLINO", "indizi": ["Europa", "Irlanda", "Temple Bar e Fabbrica Guinness"]},
    {"target": "COPENAGHEN", "indizi": ["Europa", "Danimarca", "Statua della Sirenetta e Nyhavn"]},
    {"target": "STOCCOLMA", "indizi": ["Europa", "Svezia", "Gamla Stan (Città vecchia)"]},
    {"target": "OSLO", "indizi": ["Europa", "Norvegia", "Parco delle sculture di Vigeland"]},
    {"target": "ZURIGO", "indizi": ["Europa", "Svizzera", "Lago e Chiesa di Grossmünster"]},
    {"target": "GINEVRA", "indizi": ["Europa", "Svizzera", "Jet d'Eau sul lago"]},
    {"target": "VARSAVIA", "indizi": ["Europa", "Polonia", "Palazzo della Cultura e della Scienza"]},
    {"target": "CRACOVIA", "indizi": ["Europa", "Polonia", "Piazza del Mercato"]},
    {"target": "BUCAREST", "indizi": ["Europa", "Romania", "Palazzo del Parlamento"]},
    {"target": "MOSCA", "indizi": ["Europa", "Russia", "Piazza Rossa e Cattedrale di San Basilio"]},
    {"target": "SAN PIETROBURGO", "indizi": ["Europa", "Russia", "Museo Hermitage"]},
    {"target": "ISTANBUL", "indizi": ["Europa/Asia", "Turchia", "Basilica di Santa Sofia e Cisterne"]},
    {"target": "NEW YORK", "indizi": ["America del Nord", "Stati Uniti", "Statua della Libertà e Times Square"]},
    {"target": "LOS ANGELES", "indizi": ["America del Nord", "Stati Uniti", "Scritta Hollywood e Walk of Fame"]},
    {"target": "SAN FRANCISCO", "indizi": ["America del Nord", "Stati Uniti", "Golden Gate Bridge"]},
    {"target": "LAS VEGAS", "indizi": ["America del Nord", "Stati Uniti", "Casinò della Strip"]},
    {"target": "MIAMI", "indizi": ["America del Nord", "Stati Uniti", "Ocean Drive e Art Déco"]},
    {"target": "CHICAGO", "indizi": ["America del Nord", "Stati Uniti", "La scultura The Bean"]},
    {"target": "WASHINGTON", "indizi": ["America del Nord", "Stati Uniti", "Casa Bianca e Capitol Hill"]},
    {"target": "ORLANDO", "indizi": ["America del Nord", "Stati Uniti", "Parchi tematici Disney e Universal"]},
    {"target": "TORONTO", "indizi": ["America del Nord", "Canada", "CN Tower"]},
    {"target": "MONTREAL", "indizi": ["America del Nord", "Canada", "Basilica di Notre-Dame"]},
    {"target": "VANCOUVER", "indizi": ["America del Nord", "Canada", "Stanley Park"]},
    {"target": "CITTA DEL MESSICO", "indizi": ["America del Nord", "Messico", "Zócalo e Cattedrale"]},
    {"target": "CANCUN", "indizi": ["America del Nord", "Messico", "Spiagge e vicini templi Maya"]},
    {"target": "RIO DE JANEIRO", "indizi": ["America del Sud", "Brasile", "Cristo Redentore sul Corcovado"]},
    {"target": "SAN PAOLO", "indizi": ["America del Sud", "Brasile", "Avenida Paulista"]},
    {"target": "BUENOS AIRES", "indizi": ["America del Sud", "Argentina", "Obelisco e quartiere La Boca"]},
    {"target": "SANTIAGO DEL CILE", "indizi": ["America del Sud", "Cile", "Cerro San Cristóbal e le Ande"]},
    {"target": "LIMA", "indizi": ["America del Sud", "Perù", "Plaza Mayor"]},
    {"target": "BOGOTA", "indizi": ["America del Sud", "Colombia", "Cerro de Monserrate"]},
    {"target": "L'AVANA", "indizi": ["America del Nord", "Cuba", "Malecón e auto d'epoca"]},
    {"target": "TOKYO", "indizi": ["Asia", "Giappone", "Incrocio di Shibuya e Torre di Tokyo"]},
    {"target": "KYOTO", "indizi": ["Asia", "Giappone", "Torii rossi del Fushimi Inari"]},
    {"target": "OSAKA", "indizi": ["Asia", "Giappone", "Castello ed insegne di Dotonbori"]},
    {"target": "PECHINO", "indizi": ["Asia", "Cina", "Città Proibita e Grande Muraglia"]},
    {"target": "SHANGHAI", "indizi": ["Asia", "Cina", "Torre della Perla Orientale e The Bund"]},
    {"target": "HONG KONG", "indizi": ["Asia", "Cina", "Skyline del Victoria Harbour"]},
    {"target": "SEUL", "indizi": ["Asia", "Corea del Sud", "Palazzo Gyeongbokgung"]},
    {"target": "BANGKOK", "indizi": ["Asia", "Thailandia", "Tempio del Buddha Sdraiatore"]},
    {"target": "SINGAPORE", "indizi": ["Asia", "Singapore", "Marina Bay Sands e Supertrees"]},
    {"target": "KUALA LUMPUR", "indizi": ["Asia", "Malesia", "Torri Petronas"]},
    {"target": "GIACARTA", "indizi": ["Asia", "Indonesia", "Monumento Nazionale (Monas)"]},
    {"target": "NUOVA DELHI", "indizi": ["Asia", "India", "Porta dell'India"]},
    {"target": "AGRA", "indizi": ["Asia", "India", "Taj Mahal"]},
    {"target": "MUMBAI", "indizi": ["Asia", "India", "Porta dell'India (Porto)"]},
    {"target": "DUBAI", "indizi": ["Asia", "Emirati Arabi Uniti", "Burj Khalifa"]},
    {"target": "ABU DHABI", "indizi": ["Asia", "Emirati Arabi Uniti", "Grande Moschea dello Sceicco Zayed"]},
    {"target": "DOHA", "indizi": ["Asia", "Qatar", "Museo d'Arte Islamica e Souq Waqif"]},
    {"target": "RIAD", "indizi": ["Asia", "Arabia Saudita", "Grattacielo Kingdom Centre"]},
    {"target": "GERUSALEMME", "indizi": ["Asia", "Israele", "Muro del Pianto e Cupola della Roccia"]},
    {"target": "TEL AVIV", "indizi": ["Asia", "Israele", "Lungomare e spiagge della città vecchia"]},
    {"target": "IL CAIRO", "indizi": ["Africa", "Egitto", "Piramidi di Giza e Sfinge"]},
    {"target": "LUXOR", "indizi": ["Africa", "Egitto", "Valle dei Re e Tempio di Karnak"]},
    {"target": "MARRAKECH", "indizi": ["Africa", "Marocco", "Piazza Jemaa el-Fna"]},
    {"target": "CASABLANCA", "indizi": ["Africa", "Marocco", "Moschea di Hassan II"]},
    {"target": "CITTA DEL CAPO", "indizi": ["Africa", "Sudafrica", "Table Mountain e Capo di Buona Speranza"]},
    {"target": "JOHANNESBURG", "indizi": ["Africa", "Sudafrica", "Quartiere Soweto"]},
    {"target": "NAIROBI", "indizi": ["Africa", "Kenya", "Parco Nazionale con fauna selvatica"]},
    {"target": "TUNISI", "indizi": ["Africa", "Tunisia", "Rovine di Cartaginese e Medina"]},
    {"target": "SYDNEY", "indizi": ["Oceania", "Australia", "Opera House e Harbour Bridge"]},
    {"target": "MELBOURNE", "indizi": ["Oceania", "Australia", "Flinders Street Station"]},
    {"target": "PERTH", "indizi": ["Oceania", "Australia", "Kings Park sul fiume Swan"]},
    {"target": "AUCKLAND", "indizi": ["Oceania", "Nuova Zelanda", "Sky Tower"]},
    {"target": "HONOLULU", "indizi": ["Oceania", "Stati Uniti", "Spiaggia di Waikiki"]},
    {"target": "MALE", "indizi": ["Asia", "Maldive", "Isola capitale e atolli turistici"]},
    {"target": "NASSAU", "indizi": ["America del Nord", "Bahamas", "Resort di Paradise Island"]}
]

# --- DATABASE 100 SPORTIVI ---
QUIZ_SPORTIVI_DB = [
    {"target": "FEDERER", "indizi": ["Tennis", "Svizzera", "Elegante re dell'erba di Londra."]},
    {"target": "NADAL", "indizi": ["Tennis", "Spagna", "Dominatore imbattibile sulla terra rossa."]},
    {"target": "DJOKOVIC", "indizi": ["Tennis", "Serbia", "Risposta imbattibile e record di titoli Slam."]},
    {"target": "SINNER", "indizi": ["Tennis", "Italia", "Colpi potenti da fondo per conquistare la cima del ranking."]},
    {"target": "SERENA WILLIAMS", "indizi": ["Tennis", "Stati Uniti", "Potenza devastante con 23 Slam vinti."]},
    {"target": "ALCARAZ", "indizi": ["Tennis", "Spagna", "Prodigio capace di vincere Slam su ogni superficie da giovanissimo."]},
    {"target": "BERRETTINI", "indizi": ["Tennis", "Italia", "Servizio e dritto che lo hanno portato in finale sul manto erboso inglese."]},
    {"target": "PANATTA", "indizi": ["Tennis", "Italia", "Icona degli anni '70 vincitore a Parigi e in Coppa Davis."]},
    {"target": "PIETRANGELI", "indizi": ["Tennis", "Italia", "Leggenda azzurra del passato sulla terra battuta."]},
    {"target": "AGASSI", "indizi": ["Tennis", "Stati Uniti", "Trasformato da ribelle a campione di tutti e quattro gli Slam."]},
    {"target": "SAMPRAS", "indizi": ["Tennis", "Stati Uniti", "Servizio e volée d'acciaio prima dell'era dei Big Three."]},
    {"target": "BORG", "indizi": ["Tennis", "Svezia", "L'Uomo di Ghiaccio che dominava negli anni settanta."]},
    {"target": "MCENROE", "indizi": ["Tennis", "Stati Uniti", "Mancino geniale celebre per i continui litigi con gli arbitri."]},
    {"target": "SHARAPOVA", "indizi": ["Tennis", "Russia", "Urlo inconfondibile durante il colpo e trionfo a Wimbledon da teenager."]},
    {"target": "GRAF", "indizi": ["Tennis", "Germania", "L'unica capace di completare il Golden Slam nello stesso anno."]},
    {"target": "SCHUMACHER", "indizi": ["Formula 1", "Germania / Ferrari", "L'era rossa dei cinque titoli mondiali consecutivi."]},
    {"target": "HAMILTON", "indizi": ["Formula 1", "Regno Unito / Mercedes", "Sette titoli mondiali sulla monoposto numero 44."]},
    {"target": "SENNA", "indizi": ["Formula 1", "Brasile / McLaren", "Talento puro e maestro assoluto della guida sotto la pioggia."]},
    {"target": "VERSTAPPEN", "indizi": ["Formula 1", "Olanda / Red Bull", "Olandese volante dominatore della F1 moderna."]},
    {"target": "LECLERC", "indizi": ["Formula 1", "Monaco / Ferrari", "Il Predestinato beniamino del pubblico di Maranello."]},
    {"target": "LAUDA", "indizi": ["Formula 1", "Austria / Ferrari", "Il ritorno miracoloso in pista dopo il rogo del Nürburgring."]},
    {"target": "PROST", "indizi": ["Formula 1", "Francia / McLaren", "Detto Il Professore per la sua guida tattica e calcolata."]},
    {"target": "ALONSO", "indizi": ["Formula 1", "Spagna / Renault", "Due volte campione capace di duellare in F1 in tre decenni diversi."]},
    {"target": "RAIKKONEN", "indizi": ["Formula 1", "Finlandia / Ferrari", "Iceman, l'ultimo pilota a vincere il titolo con la Rossa."]},
    {"target": "VILLENEUVE", "indizi": ["Formula 1", "Canada / Ferrari", "Guida generosa e spericolata entrata nel cuore dei tifosi."]},
    {"target": "VALENTINO ROSSI", "indizi": ["MotoGP", "Italia / Yamaha e Honda", "Il numero 46 giallo con nove titoli mondiali."]},
    {"target": "AGOSTINI", "indizi": ["Motociclismo", "Italia / MV Agusta", "Il pilota più titolato della storia con 15 mondiali."]},
    {"target": "MARQUEZ", "indizi": ["MotoGP", "Spagna / Honda", "Piegate estreme salvando le cadute con il gomito a terra."]},
    {"target": "BAGNAIA", "indizi": ["MotoGP", "Italia / Ducati", "Campione del mondo riportando la moto italiana in cima."]},
    {"target": "SIMONCELLI", "indizi": ["MotoGP", "Italia / Honda", "Il numero 58 dal grande cuore e la chioma inconfondibile."]},
    {"target": "STONER", "indizi": ["MotoGP", "Australia / Ducati", "L'unico capace di domare la Desmosedici nei primi anni 2000."]},
    {"target": "BIAGGI", "indizi": ["Motociclismo", "Italia / Aprilia", "Il Corsaro, quattro volte iridato nella classe 250."]},
    {"target": "LORENZO", "indizi": ["MotoGP", "Spagna / Yamaha", "Martello dallo stile di guida pulitissimo e preciso."]},
    {"target": "DOVIZIOSO", "indizi": ["MotoGP", "Italia / Ducati", "I duelli fino all'ultima staccata contro il numero 93."]},
    {"target": "JORDAN", "indizi": ["Basket", "Stati Uniti / Chicago Bulls", "Il numero 23 per eccellenza e la capacità di volare a canestro."]},
    {"target": "KOBE BRYANT", "indizi": ["Basket", "Stati Uniti / Los Angeles Lakers", "Black Mamba e gli 81 punti segnati in una sola partita."]},
    {"target": "LEBRON JAMES", "indizi": ["Basket", "Stati Uniti / Lakers", "Il miglior marcatore della storia della lega americana."]},
    {"target": "STEPHEN CURRY", "indizi": ["Basket", "Stati Uniti / Golden State", "Ha cambiato la pallacanestro segnando continuamente da tre punti."]},
    {"target": "SHAQUILLE O'NEAL", "indizi": ["Basket", "Stati Uniti / Los Angeles Lakers", "Il centro più fisicamente dominante vicino al tabellone."]},
    {"target": "MAGIC JOHNSON", "indizi": ["Basket", "Stati Uniti / Los Angeles Lakers", "I passaggi no-look che hanno inventato lo Showtime."]},
    {"target": "LARRY BIRD", "indizi": ["Basket", "Stati Uniti / Boston Celtics", "Tiro mortifero e leggendaria rivalità con i Lakers anni '80."]},
    {"target": "ANTETOKOUNMPO", "indizi": ["Basket", "Grecia / Milwaukee Bucks", "Greek Freak dalle falcate infinite da un canestro all'altro."]},
    {"target": "DONCIC", "indizi": ["Basket", "Slovenia / Dallas Mavericks", "Il talento europeo capace di dominare in America senza sforzo apparente."]},
    {"target": "BELINELLI", "indizi": ["Basket", "Italia / San Antonio Spurs", "Primo ed unico italiano ad aver vinto un titolo NBA."]},
    {"target": "GALLINARI", "indizi": ["Basket", "Italia", "Lungo percorso d'élite oltreoceano con la maglia azzurra nel cuore."]},
    {"target": "POZZECCO", "indizi": ["Basket", "Italia / Varese e Milano", "Mossa del Mosca, playmaker estroso diventato allenatore."]},
    {"target": "MENEGHIN", "indizi": ["Basket", "Italia / Varese e Milano", "Il gigante pilastro della pallacanestro italiana del passato."]},
    {"target": "JOKIC", "indizi": ["Basket", "Serbia / Denver Nuggets", "Il centro serbo che sforna assist e triple doppie a ripetizione."]},
    {"target": "TOMBA", "indizi": ["Sci Alpino", "Italia", "Tomba la Bomba, l'uomo che fermava l'Italia per lo slalom."]},
    {"target": "GOGGIA", "indizi": ["Sci Alpino", "Italia", "Discesista grintosa e senza paura sulle piste più ripide."]},
    {"target": "BRIGNONE", "indizi": ["Sci Alpino", "Italia", "Prima azzurra a conquistare la Coppa del Mondo generale."]},
    {"target": "PELLEGRINI", "indizi": ["Nuoto", "Italia", "La Divina regina dei 200 metri stile libero per oltre 15 anni."]},
    {"target": "PHELPS", "indizi": ["Nuoto", "Stati Uniti", "Lo squalo di Baltimora con 28 medaglie olimpiche sul collo."]},
    {"target": "PALTRINIERI", "indizi": ["Nuoto", "Italia", "Re della resistenza nei 1500 metri in vasca e nelle acque libere."]},
    {"target": "CECCON", "indizi": ["Nuoto", "Italia", "Dorsista veneto e recordman mondiale nei 100 metri."]},
    {"target": "LEDECKY", "indizi": ["Nuoto", "Stati Uniti", "Imbattibile nelle lunghe distanze dello stile libero femminile."]},
    {"target": "BOLT", "indizi": ["Atletica Leggera", "Giamaica", "L'uomo più veloce di sempre e l'esultanza a forma di fulmine."]},
    {"target": "JACOBS", "indizi": ["Atletica Leggera", "Italia", "Il lampo azzurro che ha conquistato l'oro nei 100 metri a Tokyo."]},
    {"target": "TAMBERI", "indizi": ["Atletica Leggera", "Italia", "Spettacolo nel salto in alto con la barba tagliata a metà."]},
    {"target": "MENNEA", "indizi": ["Atletica Leggera", "Italia", "La Freccia del Sud recordman nei 200 metri per ben 17 anni."]},
    {"target": "CARL LEWIS", "indizi": ["Atletica Leggera", "Stati Uniti", "Il Figlio del Vento oro nella corsa e nel salto in lungo."]},
    {"target": "SIMEONI", "indizi": ["Atletica Leggera", "Italia", "Icona del salto in alto e medaglia d'oro a Mosca 1980."]},
    {"target": "DUPLANTIS", "indizi": ["Atletica Leggera", "Svezia", "Supera regolarmente il proprio record del mondo nel salto con l'asta."]},
    {"target": "FIONA MAY", "indizi": ["Atletica Leggera", "Italia", "Saltatrice in lungo vincitrice di due titoli mondiali."]},
    {"target": "ZAYTSEV", "indizi": ["Pallavolo", "Italia", "Il Loong, battute al fulmine ed iconico opposto della Nazionale."]},
    {"target": "GIANNELLI", "indizi": ["Pallavolo", "Italia", "Regista e capitano trascinatore dell'Italia campione del mondo."]},
    {"target": "EGONU", "indizi": ["Pallavolo", "Italia", "Schiacciate ad altezze siderali e potenza d'attacco devastante."]},
    {"target": "GIANI", "indizi": ["Pallavolo", "Italia", "Pilastro della Generazione di Fenomeni degli anni '90."]},
    {"target": "BERNARDI", "indizi": ["Pallavolo", "Italia", "Eletto Miglior giocatore del secolo dalla Federazione."]},
    {"target": "PANTANI", "indizi": ["Ciclismo", "Italia", "Il Pirata dallo scatto secco sulle grandi salite del Giro e del Tour."]},
    {"target": "NIBALI", "indizi": ["Ciclismo", "Italia", "Lo Squalo dello Stretto vincitore di tutti e tre i Grandi Giri."]},
    {"target": "POGACAR", "indizi": ["Ciclismo", "Slovenia", "Il giovane fenomeno dominatore delle corse a tappe e delle classiche."]},
    {"target": "BARTALI", "indizi": ["Ciclismo", "Italia", "Il grande rivale di Coppi, famoso anche per aver salvato molte vite."]},
    {"target": "COPPI", "indizi": ["Ciclismo", "Italia", "Il Campionissimo e il celebre passaggio di borraccia con il rivale."]}
]

# --- FLASK KEEP ALIVE ---
app = Flask(__name__)

@app.route('/')
def home():
    return "SdrogoBot v4.2 Attivo H24!"

def run_flask():
    port = int(os.environ.get('PORT', 8080))
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    app.run(host='0.0.0.0', port=port)

# --- DATABASE LOCALE ---
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
        "🎰 <b>━━━━━━━━━━━━━━━━━━</b> 🎰\n"
        "       <b>SDROGOBOT ARCADE HUB</b> 🎮\n"
        "🎰 <b>━━━━━━━━━━━━━━━━━━</b> 🎰\n\n"
        f"👤 <b>Giocatore:</b> {display_name}\n"
        f"💰 <b>Saldo Chat:</b> <code>{coins} $SDG</code>\n\n"
        "⚡ <i>Scegli una categoria dal menu per giocare:</i>"
    )
    
    keyboard = [
        [InlineKeyboardButton("🕹️ Single Player", callback_data=f"hub_single_{user.id}"), InlineKeyboardButton("⚔️ Multiplayer", callback_data=f"hub_multi_{user.id}")],
        [InlineKeyboardButton("🧠 Quiz Show", callback_data=f"hub_quiz_{user.id}"), InlineKeyboardButton("🛒 SdrogoShop", callback_data=f"hub_shop_{user.id}")],
        [InlineKeyboardButton("💳 Portafoglio / Daily", callback_data=f"hub_wallet_{user.id}"), InlineKeyboardButton("🏆 Classifica", callback_data=f"hub_lead_{user.id}")]
    ]
    
    if update.message:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    elif update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

async def hub_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    parts = data.split("_")
    action = parts[1]
    owner_id = int(parts[2]) if len(parts) > 2 else query.from_user.id

    if not await verify_user_lock(query, owner_id):
        return

    await query.answer()
    chat_id = query.message.chat_id
    user_id = query.from_user.id
    coins = get_user_coins(chat_id, user_id)

    back_button = [InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{user_id}")]

    if action == "main":
        await show_hub(update, context)

    elif action == "single":
        text = (
            "🕹️ <b>GIOCHI SINGLE PLAYER</b>\n"
            "─────────────────────────────\n\n"
            "🃏 <b>Blackjack 21</b> (10 $SDG)\n"
            "🎰 <b>Slot Machine 777</b> (10 $SDG)\n"
            "🔠 <b>Wordle Express</b> (10 $SDG)\n"
            "🔐 <b>Mastermind Express</b> (10 $SDG)"
        )
        keyboard = [
            [InlineKeyboardButton("🃏 Blackjack (10 $SDG)", callback_data=f"start_bj_{user_id}"), InlineKeyboardButton("🎰 Slot 777 (10 $SDG)", callback_data=f"start_slot_{user_id}")],
            [InlineKeyboardButton("🔠 Wordle (10 $SDG)", callback_data=f"start_wordle_{user_id}"), InlineKeyboardButton("🔐 Mastermind (10 $SDG)", callback_data=f"start_mm_{user_id}")],
            back_button
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif action == "multi":
        text = (
            "⚔️ <b>GIOCHI MULTIPLAYER</b>\n"
            "─────────────────────────────\n\n"
            "🎯 <b>Roulette Russa 1v1</b>\n"
            "🎲 <b>High / Low 1v1 (Dado della Morte)</b>\n"
            "🌐 <b>Quiz Multiplayer</b> (Aperto a tutto il gruppo!)"
        )
        keyboard = [
            [InlineKeyboardButton("🎯 Roulette 1v1", callback_data=f"start_roulette_{user_id}")],
            [InlineKeyboardButton("🎲 High / Low 1v1", callback_data=f"start_highlow_{user_id}")],
            [InlineKeyboardButton("🌐 Quiz Multiplayer (Gratis)", callback_data=f"start_qmulti_{user_id}")],
            back_button
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif action == "quiz":
        text = (
            "🧠 <b>QUIZ SHOW SINGLE PLAYER</b> (Costo: 5 $SDG)\n"
            "─────────────────────────────\n"
            "Scegli la tua categoria preferita:"
        )
        keyboard = [
            [InlineKeyboardButton("⚽ Calcio", callback_data=f"start_qcalcio_{user_id}"), InlineKeyboardButton("🎬 Cinema", callback_data=f"start_qcinema_{user_id}")],
            [InlineKeyboardButton("📺 Serie TV", callback_data=f"start_qserie_{user_id}"), InlineKeyboardButton("🌍 Città", callback_data=f"start_qcitta_{user_id}")],
            [InlineKeyboardButton("🏆 Sportivi", callback_data=f"start_qsport_{user_id}")],
            back_button
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif action == "shop":
        inv_key = f"{chat_id}_{user_id}"
        inv = USER_INVENTORIES.get(inv_key, {"titles": 0, "persecutes": 0})
        
        text = (
            "🛒 <b>SDROGOSHOP - MERCATO VIRTUALI</b>\n"
            "─────────────────────────────\n\n"
            f"📦 <b>Tuo Inventario:</b> {inv.get('titles', 0)} Titoli | {inv.get('persecutes', 0)} Persecuzioni\n\n"
            "🏷️ <b>1. Titolo Umiliante (100 $SDG)</b>\nAssegna '🏳️‍🌈GAY🏳️‍🌈' a una vittima per 24 ORE REALI!\n\n"
            "🗣️ <b>2. Tag Persecutore (120 $SDG)</b>\nIl bot risponde 'frocio hah' ai prossimi 15 messaggi di una vittima!\n\n"
            "🏢 <b>3. Pass SDROGO HEIST (350 $SDG)</b>\nRapina a 5 livelli in PRIVATO col bot per vincere Jackpot + Stelle!"
        )
        keyboard = [
            [InlineKeyboardButton("🏷️ Compra Titolo (100 $SDG)", callback_data=f"buy_title_{user_id}")],
            [InlineKeyboardButton("🗣️ Compra Tag Persecutore (120 $SDG)", callback_data=f"buy_persecute_{user_id}")],
            [InlineKeyboardButton("🏢 Avvia SDROGO HEIST (350 $SDG)", callback_data=f"buy_heist_{user_id}")],
            back_button
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif action == "wallet":
        text = (
            "💳 <b>PORTAFOGLIO & ECONOMIA</b>\n"
            "─────────────────────────────\n\n"
            f"👤 Giocatore: <b>{query.from_user.first_name}</b>\n"
            f"💰 Saldo attuale: <code>{coins} $SDG</code>\n\n"
            "🎁 <b>Bonus Daily:</b> Riscuoti 50 $SDG ogni 24 ore."
        )
        keyboard = [
            [InlineKeyboardButton("🎁 Riscuoti Daily (+50 $SDG)", callback_data=f"claim_daily_{user_id}")],
            back_button
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif action == "lead":
        await show_leaderboard(update, context, owner_id)

# --- SHOP ACTIONS ---
async def shop_buy_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
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
                text="🏢 <b>SDROGO HEIST - LA RAPINA AL CAVEAU</b> 🕵️‍♂️\n─────────────────────────────\n\nBenvenuto al Livello 1! Devi disattivare l'allarme per entrare.",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )
            await query.edit_message_text("🏢 <b>LA RAPINA È INIZIATA!</b> Controlla la tua chat PRIVATA con SdrogoBot per giocare!", parse_mode="HTML")
        except Exception:
            add_user_coins(chat_id, user_id, 350)
            await query.edit_message_text("❌ Devi prima avviare il bot in chat PRIVATA per giocare a Sdrogo Heist!", parse_mode="HTML")

# --- GAME: SDROGO HEIST (5 LIVELLI IN CHAT PRIVATA) ---
async def handle_heist_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    parts = query.data.split("_")
    stage = parts[1]
    owner_id = int(parts[2])

    if query.from_user.id != owner_id:
        return

    await query.answer()
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
                [InlineKeyboardButton("💰 CASHOUT (Prendi 50 $SDG ed esci)", callback_data=f"heist_cashout_50_{user_id}")],
                [InlineKeyboardButton("🔥 RISCHIA IL LIVELLO 2 (Guardia)", callback_data=f"heist_lvl2_{user_id}")]
            ]
            await query.edit_message_text("✅ <b>LIVELLO 1 SUPERATO!</b>\nPremio accumulato: +50 $SDG.\n\nCosa vuoi fare?", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif stage == "lvl2":
        p_hand = random.randint(15, 21)
        g_hand = random.randint(14, 21)
        
        if p_hand >= g_hand:
            game["level"] = 3
            keyboard = [
                [InlineKeyboardButton("💰 CASHOUT (Prendi 100 $SDG ed esci)", callback_data=f"heist_cashout_100_{user_id}")],
                [InlineKeyboardButton("🔥 RISCHIA IL LIVELLO 3 (Laser)", callback_data=f"heist_lvl3_{user_id}")]
            ]
            await query.edit_message_text(f"👮 <b>LIVELLO 2 SUPERATO!</b>\nHai messo KO la guardia ({p_hand} vs {g_hand})!\nPremio accumulato: +100 $SDG.", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
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
                [InlineKeyboardButton("💰 CASHOUT (Prendi 180 $SDG ed esci)", callback_data=f"heist_cashout_180_{user_id}")],
                [InlineKeyboardButton("🔥 RISCHIA IL LIVELLO 4 (Cassaforte)", callback_data=f"heist_lvl4_{user_id}")]
            ]
            await query.edit_message_text("⚡ <b>LIVELLO 3 SUPERATO!</b>\nPremio accumulato: +180 $SDG.\n\nCosa vuoi fare?", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

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
                [InlineKeyboardButton("💰 CASHOUT (Prendi 300 $SDG ed esci)", callback_data=f"heist_cashout_300_{user_id}")],
                [InlineKeyboardButton("🔥 SFIDA IL LIVELLO 5 FINALE!", callback_data=f"heist_lvl5_{user_id}")]
            ]
            await query.edit_message_text("🔐 <b>LIVELLO 4 SUPERATO!</b>\nPremio accumulato: +300 $SDG.\n\nSei ad un passo dalla gloria!", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

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
                    text=f"👑 <b>COLPO DEL SECOLO!</b> 🏢\n\n<b>{query.from_user.first_name}</b> ha svaligiato il Caveau di Sdrogo Heist arrivando al 5° Livello!\nGuadagna <b>600 $SDG</b> e 1 STELLA ⭐ di prestigio in classifica!",
                    parse_mode="HTML"
                )
            except Exception: pass

    elif stage == "cashout":
        amount = int(parts[2])
        del HEIST_GAMES[user_id]
        add_user_coins(chat_id, user_id, amount)
        await query.edit_message_text(f"💰 <b>CASHOUT EFFETTUATO!</b> Ti ritiri dalla rapina incassando <b>+{amount} $SDG</b>!")

# --- COMANDI SHOP ---
async def apply_title_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    inv_key = f"{chat_id}_{user.id}"

    if inv_key not in USER_INVENTORIES or USER_INVENTORIES[inv_key].get("titles", 0) <= 0:
        await update.message.reply_text("❌ Non possiedi alcun Titolo Umiliante nel tuo inventario dello /shop!")
        return

    target_username = None
    if context.args and context.args[0].startswith("@"):
        target_username = context.args[0].replace("@", "").lower()
    elif update.message.reply_to_message and update.message.reply_to_message.from_user:
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
        await update.message.reply_text("❌ Utente non trovato nel registro della chat!")
        return

    USER_INVENTORIES[inv_key]["titles"] -= 1
    expire_time = datetime.now() + timedelta(hours=24)
    ACTIVE_TITLES[f"{chat_id}_{target_id}"] = {"title": "🏳️‍🌈GAY🏳️‍🌈", "expire": expire_time}
    await update.message.reply_text(f"🔥 <b>TITOLO ASSEGNATO!</b> Per 24 ORE @{target_username} sarà chiamato '🏳️‍🌈GAY🏳️‍🌈' dal bot!", parse_mode="HTML")

async def apply_persecute_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    inv_key = f"{chat_id}_{user.id}"

    if inv_key not in USER_INVENTORIES or USER_INVENTORIES[inv_key].get("persecutes", 0) <= 0:
        await update.message.reply_text("❌ Non possiedi alcun Tag Persecutore nel tuo inventario dello /shop!")
        return

    if not context.args or not context.args[0].startswith("@"):
        await update.message.reply_text("❌ Uso corretto: <code>perseguita @username</code>", parse_mode="HTML")
        return

    USER_INVENTORIES[inv_key]["persecutes"] -= 1
    target_username = context.args[0].replace("@", "").lower()
    ACTIVE_PERSECUTE[f"{chat_id}_{target_username}"] = {"count": 15, "phrase": "frocio hah"}
    await update.message.reply_text(f"😈 <b>PERSECUZIONE ATTIVATA!</b> I prossimi 15 messaggi di @{target_username} riceveranno risposta 'frocio hah' dal bot!", parse_mode="HTML")

# --- GAME: SLOT MACHINE 777 ---
async def start_slot_from_hub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
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

    text = f"🎰 <b>SLOT MACHINE 777</b> 🎰\n👤 Player: <b>{user.first_name}</b>\n\n[ {r1} | {r2} | {r3} ]\n\n"

    end_keyboard = [
        [InlineKeyboardButton("🔂 Rigioca (10 $SDG)", callback_data=f"start_slot_{user.id}")],
        [InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{user.id}")]
    ]

    if r1 == r2 == r3:
        if r1 == "7️⃣":
            add_user_coins(chat_id, user.id, 150)
            text += "🔥 <b>JACKPOT SUPREMO 777!</b> 🔥 Hai vinto <b>+150 $SDG</b>!"
        else:
            add_user_coins(chat_id, user.id, 30)
            text += "🎉 <b>TRIPLETTA VINCENTE!</b> Hai vinto <b>+30 $SDG</b>!"
    elif r1 == r2 or r2 == r3 or r1 == r3:
        add_user_coins(chat_id, user.id, 10)
        text += "✨ <b>DOPPIETTA!</b> Recuperi i tuoi 10 $SDG!"
    else:
        text += "💸 <b>NESSUNA COMBINAZIONE!</b> Hai perso 10 $SDG."

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(end_keyboard), parse_mode="HTML")

# --- GAME: MASTERMIND EXPRESS ---
async def start_mastermind_from_hub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
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
        "🔐 <b>MASTERMIND EXPRESS</b> (Puntata: 10 $SDG)\n"
        "─────────────────────────────\n\n"
        "Ho scelto un codice segreto di <b>3 cifre uniche</b>!\n"
        "Scrivilo direttamente in chat per tentare (4 tentativi).",
        parse_mode="HTML"
    )

# --- GAME: HIGHLOW 1v1 ---
async def start_highlow_prep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    parts = query.data.split("_")
    owner_id = int(parts[2]) if len(parts) > 2 else query.from_user.id

    if not await verify_user_lock(query, owner_id): return

    await query.edit_message_text(
        "🎲 <b>HIGH / LOW 1v1 (DADO DELLA MORTE)</b>\n"
        "─────────────────────────────\n\n"
        "Scrivi in chat il nome della tua vittima per sfidarla sul dado:\n\n"
        "👉 <code>sfido highlow @username</code>",
        parse_mode="HTML"
    )

async def handle_highlow_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
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
        won = (choice == "hl_guess_high" and new_val > old_val) or (choice == "hl_guess_low" and new_val < old_val)

        if won:
            game["val"] = new_val
            prossimo_id = game["target_id"] if user.id == game["sfidante_id"] else game["sfidante_id"]
            prossimo_nome = game["target_name"] if user.id == game["sfidante_id"] else game["sfidante_name"]
            game["turno_id"] = prossimo_id

            keyboard = [[
                InlineKeyboardButton("📈 PIÙ ALTO", callback_data="hl_guess_high"),
                InlineKeyboardButton("📉 PIÙ BASSO", callback_data="hl_guess_low")
            ]]

            await query.edit_message_text(
                f"✅ <b>GIUSTO! Era {new_val}!</b>\n"
                f"😅 <b>{user.first_name}</b> si salva!\n\n"
                f"🎯 Nuovo numero: <b>{new_val}</b>\n"
                f"👉 Tocca a <b>{prossimo_nome}</b>: Più Alto o Più Basso?",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )
        else:
            PENITENZE_ATTIVE[user.id] = 1
            await query.edit_message_text(
                f"💥 <b>ERRATO! Era {new_val}!</b>\n"
                f"💀 <b>{user.first_name} HA SBAGLIATO E PERDE IL DUELLO!</b>\n\n"
                f"⚠️ Per parlare devi scrivere esattamente:\n👉 <code>{FRASE_PENITENZA}</code>",
                parse_mode="HTML"
            )
            del HIGHLOW_DUELS[chat_id]

# --- GAME: QUIZ MULTIPLAYER ---
async def start_quiz_multiplayer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    parts = query.data.split("_")
    owner_id = int(parts[2]) if len(parts) > 2 else query.from_user.id

    if not await verify_user_lock(query, owner_id): return
    chat_id = str(query.message.chat_id)

    db_choice = random.choice([QUIZ_CALCIO_DB, QUIZ_CINEMA_DB, QUIZ_SERIE_DB, QUIZ_CITTA_DB, QUIZ_SPORTIVI_DB])
    item = random.choice(db_choice)

    QUIZ_GAMES[chat_id] = {
        "multi": True, "target": item["target"],
        "indizi": item["indizi"], "step": 1,
        "created_at": datetime.now()
    }

    keyboard = [
        [InlineKeyboardButton("💡 Chiedi altro indizio", callback_data="quiz_multi_hint")],
        [InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{owner_id}")]
    ]

    await query.edit_message_text(
        f"🌐 <b>QUIZ MULTIPLAYER APERTO A TUTTI</b>\n─────────────────────────────\n\n"
        f"Il primo che risponde in chat vince +15 $SDG!\n\n"
        f"<b>1° Indizio:</b> {item['indizi'][0]}",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )

# --- TIMEOUT QUIZ ---
async def quiz_timeout_check(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now()
    to_delete = [k for k, q in QUIZ_GAMES.items() if q.get("created_at") and (now - q["created_at"]).total_seconds() > 180]
    for k in to_delete: del QUIZ_GAMES[k]

# --- CLASSIFICA RICCONI ---
async def show_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE, owner_id: int = None):
    query = update.callback_query
    chat_id = query.message.chat_id if query else update.effective_chat.id
    current_user_id = query.from_user.id if query else update.effective_user.id

    if query and owner_id and not await verify_user_lock(query, owner_id): return

    prefix = f"{chat_id}_"
    chat_users = []

    for key, data in USER_DATA.items():
        if key.startswith(prefix):
            uid = key.split("_")[1]
            coins = data.get("coins", 0)
            chat_users.append((uid, coins))

    chat_users.sort(key=lambda x: x[1], reverse=True)
    text = "🏆 <b>CLASSIFICA RICCONI $SDG</b> 💰\n─────────────────────────────\n\n"
    medals = ["🥇", "🥈", "🥉"]

    for idx, (uid, coins) in enumerate(chat_users[:10], start=1):
        rank_icon = medals[idx-1] if idx <= 3 else f"{idx}."
        try:
            member = await context.bot.get_chat_member(chat_id, int(uid))
            raw_name = member.user.first_name
            name = get_formatted_name(chat_id, int(uid), raw_name)
        except Exception:
            name = f"Giocatore {uid[-4:]}"

        text += f"{rank_icon} <b>{name}</b> — <code>{coins} $SDG</code>\n"

    keyboard = [[InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{current_user_id}")]]

    if query:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

# --- DAILY ---
async def claim_daily_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    parts = query.data.split("_")
    owner_id = int(parts[2]) if len(parts) > 2 else query.from_user.id

    if not await verify_user_lock(query, owner_id): return

    chat_id = query.message.chat_id
    user_id = query.from_user.id
    key = get_user_key(chat_id, user_id)
    today = str(date.today())

    if key not in USER_DATA: USER_DATA[key] = {"coins": 50, "last_daily": ""}

    if USER_DATA[key].get("last_daily") == today:
        await query.answer("❌ Bonus giornaliero già riscosso oggi!", show_alert=True)
    else:
        USER_DATA[key]["last_daily"] = today
        add_user_coins(chat_id, user_id, 50)
        await query.answer("🎉 Hai riscosso +50 $SDG!", show_alert=True)
        await backup_to_telegram(context)
        await hub_callback(update, context)

async def block_direct_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚫 <b>I giochi si avviano solo dall'HUB!</b>\nUsa /sdrogocomm per accedere.", parse_mode="HTML")

# --- GAME: BLACKJACK ---
async def start_bj_from_hub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    parts = query.data.split("_")
    owner_id = int(parts[2]) if len(parts) > 2 else query.from_user.id

    if not await verify_user_lock(query, owner_id): return
    user = query.from_user
    chat_id = query.message.chat_id

    if get_user_coins(chat_id, user.id) < 10:
        await query.answer("❌ Servono 10 $SDG per giocare a Blackjack!", show_alert=True)
        return

    add_user_coins(chat_id, user.id, -10)
    cards = [2, 3, 4, 5, 6, 7, 8, 9, 10, 10, 10, 10, 11]
    player_hand = [random.choice(cards), random.choice(cards)]
    dealer_hand = [random.choice(cards)]

    BLACKJACK_GAMES[f"{chat_id}_{user.id}"] = {
        "player_id": user.id, "player_hand": player_hand, "dealer_hand": dealer_hand
    }

    keyboard = [[
        InlineKeyboardButton("🎴 Carta", callback_data=f"bj_hit_{user.id}"),
        InlineKeyboardButton("✋ Stai", callback_data=f"bj_stand_{user.id}")
    ]]

    await query.edit_message_text(
        f"🃏 <b>BLACKJACK 21</b> (Puntata: 10 $SDG)\n"
        f"─────────────────────────────\n\n"
        f"👤 Giocatore: <b>{user.first_name}</b>\n"
        f"🎎 Carte: {player_hand} (Totale: <b>{sum(player_hand)}</b>)\n"
        f"🤖 Banco: [{dealer_hand[0]}, ?]\n\nCosa fai?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML'
    )

async def handle_bj_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    parts = query.data.split("_")
    action = parts[1]
    owner_id = int(parts[2]) if len(parts) > 2 else query.from_user.id

    if not await verify_user_lock(query, owner_id): return
    await query.answer()
    chat_id = query.message.chat_id
    user_id = query.from_user.id
    game_key = f"{chat_id}_{user_id}"

    if game_key not in BLACKJACK_GAMES:
        await query.edit_message_text("❌ Partita terminata.")
        return

    game = BLACKJACK_GAMES[game_key]
    cards = [2, 3, 4, 5, 6, 7, 8, 9, 10, 10, 10, 10, 11]
    end_keyboard = [
        [InlineKeyboardButton("🔂 Rigioca (10 $SDG)", callback_data=f"start_bj_{user_id}")],
        [InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{user_id}")]
    ]

    if action == "hit":
        game["player_hand"].append(random.choice(cards))
        score = sum(game["player_hand"])

        if score > 21:
            del BLACKJACK_GAMES[game_key]
            await query.edit_message_text(f"💥 <b>SBALLATO!</b> ({score})\nHai perso 10 $SDG!", reply_markup=InlineKeyboardMarkup(end_keyboard), parse_mode='HTML')
        else:
            keyboard = [[InlineKeyboardButton("🎴 Carta", callback_data=f"bj_hit_{user_id}"), InlineKeyboardButton("✋ Stai", callback_data=f"bj_stand_{user_id}")]]
            await query.edit_message_text(f"🃏 <b>BLACKJACK 21</b>\n\nCarte: {game['player_hand']} ({score})\nBanco: [{game['dealer_hand'][0]}, ?]", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

    elif action == "stand":
        player_score = sum(game["player_hand"])
        dealer_hand = game["dealer_hand"]
        while sum(dealer_hand) < 17: dealer_hand.append(random.choice(cards))
        dealer_score = sum(dealer_hand)
        del BLACKJACK_GAMES[game_key]

        if dealer_score > 21 or player_score > dealer_score:
            add_user_coins(chat_id, user_id, 15)
            await query.edit_message_text(f"🏆 <b>VITTORIA!</b> Tu: {player_score} | Banco: {dealer_score}\nHai vinto <b>+15 $SDG</b>!", reply_markup=InlineKeyboardMarkup(end_keyboard), parse_mode='HTML')
        elif player_score < dealer_score:
            await query.edit_message_text(f"❌ <b>SCONFITTA!</b> Tu: {player_score} | Banco: {dealer_score}\nHai perso la puntata.", reply_markup=InlineKeyboardMarkup(end_keyboard), parse_mode='HTML')
        else:
            add_user_coins(chat_id, user_id, 10)
            await query.edit_message_text(f"⚖️ <b>PAREGGIO!</b> Punti: {player_score}\nPuntata di 10 $SDG restituita.", reply_markup=InlineKeyboardMarkup(end_keyboard), parse_mode='HTML')

# --- GAME: WORDLE EXPRESS ---
async def start_wordle_from_hub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    parts = query.data.split("_")
    owner_id = int(parts[2]) if len(parts) > 2 else query.from_user.id

    if not await verify_user_lock(query, owner_id): return
    user = query.from_user
    chat_id = query.message.chat_id
    game_key = f"{chat_id}_{user.id}"

    if get_user_coins(chat_id, user.id) < 10:
        await query.answer("❌ Servono 10 $SDG per giocare a Wordle!", show_alert=True)
        return

    add_user_coins(chat_id, user.id, -10)
    secret_word = random.choice(WORDS)

    WORDLE_GAMES[game_key] = {
        "player_id": user.id, "secret": secret_word,
        "attempts": 0, "history": []
    }

    await query.edit_message_text(
        "🔠 <b>WORDLE EXPRESS</b> (Puntata: 10 $SDG)\n"
        "─────────────────────────────\n\n"
        "Ho scelto una parola di <b>5 lettere</b>!\n"
        "Scrivila direttamente in chat per tentare (5 tentativi).",
        parse_mode="HTML"
    )

# --- GAME: QUIZ SHOW SINGLE PLAYER ---
async def start_quiz_generic(update: Update, context: ContextTypes.DEFAULT_TYPE, db_source, title_name: str):
    query = update.callback_query
    parts = query.data.split("_")
    owner_id = int(parts[2]) if len(parts) > 2 else query.from_user.id

    if not await verify_user_lock(query, owner_id): return
    chat_id = query.message.chat_id
    user_id = query.from_user.id

    if get_user_coins(chat_id, user_id) < 5:
        await query.answer("❌ Servono 5 $SDG per avviare il Quiz!", show_alert=True)
        return

    add_user_coins(chat_id, user_id, -5)
    item = random.choice(db_source)
    
    QUIZ_GAMES[f"{chat_id}_{user_id}"] = {
        "player_id": user_id, "type": title_name,
        "target": item["target"], "indizi": item["indizi"], "step": 1,
        "created_at": datetime.now()
    }

    keyboard = [
        [InlineKeyboardButton("💡 Chiedi altro indizio (-$SDG)", callback_data=f"quiz_hint_{user_id}")],
        [InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{user_id}")]
    ]

    await query.edit_message_text(
        f"🧠 <b>QUIZ {title_name}</b> (Costo: 5 $SDG)\n"
        "─────────────────────────────\n\n"
        f"👤 Giocatore: <b>{query.from_user.first_name}</b>\n"
        "Indovina la risposta scrivendola in chat!\n\n"
        f"<b>1° Indizio:</b> {item['indizi'][0]}",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )

async def quiz_more_hint(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    parts = query.data.split("_")
    owner_id = int(parts[2]) if len(parts) > 2 else query.from_user.id

    if not await verify_user_lock(query, owner_id): return
    chat_id = str(query.message.chat_id)
    game_key = f"{chat_id}_{owner_id}"

    if game_key not in QUIZ_GAMES:
        await query.answer("Nessun quiz attivo.", show_alert=True)
        return

    q = QUIZ_GAMES[game_key]
    if q["step"] < len(q["indizi"]):
        q["step"] += 1
        hints_text = "\n".join([f"• <b>Indizio {i+1}:</b> {q['indizi'][i]}" for i in range(q["step"])])
        
        keyboard = []
        if q["step"] < len(q["indizi"]):
            keyboard.append([InlineKeyboardButton("💡 Chiedi altro indizio (-$SDG)", callback_data=f"quiz_hint_{owner_id}")])
        keyboard.append([InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{owner_id}")])

        await query.edit_message_text(
            f"🧠 <b>QUIZ {q['type']}</b>\n─────────────────────────────\n\nScrivi la risposta in chat!\n\n{hints_text}",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )

# --- GAME: ROULETTE RUSSA 1v1 ---
async def start_roulette_prep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    parts = query.data.split("_")
    owner_id = int(parts[2]) if len(parts) > 2 else query.from_user.id

    if not await verify_user_lock(query, owner_id): return

    await query.edit_message_text(
        "🎯 <b>ROULETTE RUSSA 1v1</b>\n"
        "─────────────────────────────\n\n"
        "Scrivi in chat il nome della tua vittima:\n\n"
        "👉 <code>sfido @username</code>",
        parse_mode="HTML"
    )

# --- UTILITIES ---
async def toggle_troll(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global IS_TROLLING_ACTIVE
    if not is_admin(update.effective_user.id): return
    IS_TROLLING_ACTIVE = not IS_TROLLING_ACTIVE
    await update.message.reply_text(f"Modalità Auto-Troll: {'ATTIVATA 🙉' if IS_TROLLING_ACTIVE else 'DISATTIVATA 🛑'}")

async def clear_penalties(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    PENITENZE_ATTIVE.clear()
    await update.message.reply_text("🧹 Penitenze rimosse!")

async def reset_duello(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ACTIVE_DUELS.clear()
    HIGHLOW_DUELS.clear()
    BLACKJACK_GAMES.clear()
    WORDLE_GAMES.clear()
    MASTERMIND_GAMES.clear()
    QUIZ_GAMES.clear()
    HEIST_GAMES.clear()
    await update.message.reply_text("🛠️ Tutti i giochi bloccati sono stati resettati.")

# --- HANDLER MESSAGGI GENERICI ---
async def handle_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.from_user: return
    
    user = update.message.from_user
    chat_id_int = update.message.chat_id
    chat_id = str(chat_id_int)
    text = (update.message.text or "").strip()
    username_lower = user.username.lower() if user.username else ""

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

    # Handling Tag Persecutore dallo Shop ("frocio hah")
    persecute_key = f"{chat_id}_{username_lower}"
    if persecute_key in ACTIVE_PERSECUTE:
        p_data = ACTIVE_PERSECUTE[persecute_key]
        if p_data["count"] > 0:
            p_data["count"] -= 1
            await update.message.reply_text(p_data["phrase"])
            if p_data["count"] == 0:
                del ACTIVE_PERSECUTE[persecute_key]

    # Handling Comandi Titolo e Perseguita via messaggio generico
    if text.lower().startswith("titolo"):
        await apply_title_command(update, context)
        return
    elif text.lower().startswith("perseguita"):
        await apply_persecute_command(update, context)
        return

    # Handling Sfida Roulette Russa ("sfido @username")
    if text.lower().startswith("sfido @"):
        target_username = text.split("@")[1].strip().lower()
        ACTIVE_DUELS[chat_id_int] = {
            "sfidante_id": user.id, "sfidante_name": user.first_name,
            "target_username": target_username, "chambers": [False]*6, "current_chamber": 0
        }
        ACTIVE_DUELS[chat_id_int]["chambers"][random.randint(0, 5)] = True

        keyboard = [[
            InlineKeyboardButton("🎯 Accetta Sfida", callback_data="roulette_accetta"),
            InlineKeyboardButton("🐔 Rifiuta", callback_data="roulette_rifiuta")
        ]]
        await update.message.reply_text(
            f"🔫 <b>ROULETTE RUSSA 1v1</b>\n\n"
            f"<b>{user.first_name}</b> ha sfidato <b>@{target_username}</b>!\n"
            f"@{target_username}, rispondi coi bottoni:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
        return

    # Handling Sfida High/Low 1v1 ("sfido highlow @username")
    if text.lower().startswith("sfido highlow @"):
        target_username = text.split("@")[1].strip().lower()
        HIGHLOW_DUELS[chat_id_int] = {
            "sfidante_id": user.id, "sfidante_name": user.first_name,
            "target_username": target_username, "val": 0, "turno_id": None
        }

        keyboard = [[
            InlineKeyboardButton("🎲 Accetta High/Low", callback_data="hl_accetta"),
            InlineKeyboardButton("🐔 Rifiuta", callback_data="hl_rifiuta")
        ]]
        await update.message.reply_text(
            f"🎲 <b>HIGH / LOW 1v1 (DADO DELLA MORTE)</b>\n\n"
            f"<b>{user.first_name}</b> ha sfidato <b>@{target_username}</b> a duello sul dado!\n"
            f"@{target_username}, accetti la sfida?",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
        return

    text_upper = text.upper()

    # Handling Mastermind Express
    mm_key = f"{chat_id}_{user.id}"
    if mm_key in MASTERMIND_GAMES:
        mm = MASTERMIND_GAMES[mm_key]
        if len(text) == 3 and text.isdigit():
            mm["attempts"] += 1
            secret = mm["secret"]
            
            c_hits = sum(1 for i in range(3) if text[i] == secret[i])
            p_hits = sum(1 for i in range(3) if text[i] != secret[i] and text[i] in secret)
            
            res_str = f"🎯 {c_hits} Centrati | 🔄 {p_hits} Presenti | ❌ {3 - (c_hits + p_hits)} Assenti"
            mm["history"].append(f"<code>{text}</code> -> {res_str}")
            res_text = "\n".join(mm["history"])

            end_keyboard = [
                [InlineKeyboardButton("🔂 Rigioca (10 $SDG)", callback_data=f"start_mm_{user.id}")],
                [InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{user.id}")]
            ]

            if c_hits == 3:
                del MASTERMIND_GAMES[mm_key]
                reward = 30 if mm["attempts"] <= 2 else 15
                add_user_coins(chat_id_int, user.id, reward)
                await update.message.reply_text(f"🎉 <b>ESATTO!</b> Codice segreto: <b>{secret}</b>!\nVinti <b>+{reward} $SDG</b>!\n\n{res_text}", reply_markup=InlineKeyboardMarkup(end_keyboard), parse_mode="HTML")
            elif mm["attempts"] >= 4:
                del MASTERMIND_GAMES[mm_key]
                await update.message.reply_text(f"💥 <b>GAME OVER!</b> Il codice era <b>{secret}</b>.\n\n{res_text}", reply_markup=InlineKeyboardMarkup(end_keyboard), parse_mode="HTML")
            else:
                await update.message.reply_text(f"🔐 <b>MASTERMIND ({mm['attempts']}/4)</b>\n\n{res_text}", parse_mode="HTML")
            return

    # Handling Quiz Multiplayer
    if chat_id in QUIZ_GAMES and QUIZ_GAMES[chat_id].get("multi"):
        q = QUIZ_GAMES[chat_id]
        if text_upper == q["target"]:
            del QUIZ_GAMES[chat_id]
            add_user_coins(chat_id_int, user.id, 15)
            end_keyboard = [
                [InlineKeyboardButton("🌐 Altro Quiz Multiplayer", callback_data=f"start_qmulti_{user.id}")],
                [InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{user.id}")]
            ]
            await update.message.reply_text(
                f"🎉 <b>QUIZ MULTIPLAYER RISOLTO!</b>\n\n"
                f"🏆 <b>{user.first_name}</b> è stato il più veloce ed ha indovinato <b>{q['target']}</b>!\n"
                f"Guadagni <b>+15 $SDG</b>!",
                reply_markup=InlineKeyboardMarkup(end_keyboard),
                parse_mode="HTML"
            )
            return

    # Handling Quiz Single Player
    quiz_key = f"{chat_id}_{user.id}"
    if quiz_key in QUIZ_GAMES:
        q = QUIZ_GAMES[quiz_key]
        if text_upper == q["target"]:
            del QUIZ_GAMES[quiz_key]
            steps_used = q["step"]
            reward = 20 if steps_used == 1 else (10 if steps_used == 2 else 6)
            add_user_coins(chat_id_int, user.id, reward)
            
            end_keyboard = [
                [InlineKeyboardButton("🧠 Altro Quiz", callback_data=f"hub_quiz_{user.id}")],
                [InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{user.id}")]
            ]
            await update.message.reply_text(
                f"🎉 <b>CORRETTO!</b> <b>{user.first_name}</b> ha indovinato <b>{q['target']}</b>!\nGuadagni <b>+{reward} $SDG</b>!",
                reply_markup=InlineKeyboardMarkup(end_keyboard),
                parse_mode="HTML"
            )
            return

    # Handling Wordle
    game_key = f"{chat_id}_{user.id}"
    if game_key in WORDLE_GAMES:
        game = WORDLE_GAMES[game_key]
        if len(text_upper) == 5:
            game["attempts"] += 1
            secret = game["secret"]
            
            secret_letters = list(secret)
            res_colors = ["⬛"] * 5
            
            for i in range(5):
                if text_upper[i] == secret[i]:
                    res_colors[i] = "🟩"
                    secret_letters[i] = None
                    
            for i in range(5):
                if res_colors[i] != "🟩" and text_upper[i] in secret_letters:
                    res_colors[i] = "🟨"
                    secret_letters[secret_letters.index(text_upper[i])] = None

            letters_row = "  ".join(list(text_upper))
            colors_row = " ".join(res_colors)
            
            game["history"].append(f"<code>{letters_row}</code>\n{colors_row}")
            res_text = "\n\n".join(game["history"])

            end_keyboard = [
                [InlineKeyboardButton("🔂 Rigioca (10 $SDG)", callback_data=f"start_wordle_{user.id}")],
                [InlineKeyboardButton("🔙 Torna all'HUB", callback_data=f"hub_main_{user.id}")]
            ]

            if text_upper == secret:
                del WORDLE_GAMES[game_key]
                add_user_coins(chat_id_int, user.id, 20)
                await update.message.reply_text(f"🎉 <b>ESATTO!</b> Parola: <b>{secret}</b>!\nVinti <b>+20 $SDG</b>!\n\n{res_text}", reply_markup=InlineKeyboardMarkup(end_keyboard), parse_mode="HTML")
            elif game["attempts"] >= 5:
                del WORDLE_GAMES[game_key]
                await update.message.reply_text(f"💥 <b>GAME OVER!</b> La parola era <b>{secret}</b>.\n\n{res_text}", reply_markup=InlineKeyboardMarkup(end_keyboard), parse_mode="HTML")
            else:
                await update.message.reply_text(f"🔠 <b>WORDLE EXPRESS ({game['attempts']}/5)</b>\n\n{res_text}", parse_mode="HTML")
            return

    # Auto-Troll
    if IS_TROLLING_ACTIVE and username_lower in TARGET_MAP:
        if random.random() < 0.85:
            try: await context.bot.set_message_reaction(chat_id=chat_id, message_id=update.message.message_id, reaction=TARGET_MAP[username_lower])
            except Exception: pass

# --- CALLBACK ROULETTE ---
async def gestione_bottoni_roulette(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat_id
    user = query.from_user

    if chat_id not in ACTIVE_DUELS:
        await query.answer("⚠️ Sfida non attiva.", show_alert=True)
        return

    duel = ACTIVE_DUELS[chat_id]

    if query.data == "roulette_accetta":
        if user.username and user.username.lower() != duel["target_username"]:
            await query.answer("❌ Solo lo sfidato può accettare!", show_alert=True)
            return

        duel["target_id"] = user.id
        duel["target_name"] = user.first_name
        duel["turno_id"] = random.choice([duel["sfidante_id"], user.id])

        keyboard = [[InlineKeyboardButton("🔫 SPARA!", callback_data="roulette_spara")]]
        await query.edit_message_text(
            f"✅ <b>Sfida Accettata!</b> Tamburo caricato (1 proiettile).\n\n"
            f"🎲 Comincia: <b>{user.first_name}</b>!",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )

    elif query.data == "roulette_rifiuta":
        await query.edit_message_text("🐔 Sfida rifiutata!")
        del ACTIVE_DUELS[chat_id]

    elif query.data == "roulette_spara":
        if user.id != duel["turno_id"]:
            await query.answer("✋ Non è il tuo turno!", show_alert=True)
            return

        current_idx = duel["current_chamber"]
        is_bullet = duel["chambers"][current_idx]
        duel["current_chamber"] += 1

        if is_bullet:
            PENITENZE_ATTIVE[user.id] = 1
            await query.edit_message_text(
                f"💥 <b>BAM!</b> 💀 <b>{user.first_name} è morto!</b>\n\n"
                f"⚠️ Per parlare devi scrivere esattamente:\n👉 <code>{FRASE_PENITENZA}</code>",
                parse_mode="HTML"
            )
            del ACTIVE_DUELS[chat_id]
        else:
            prossimo_id = duel["target_id"] if user.id == duel["sfidante_id"] else duel["sfidante_id"]
            duel["turno_id"] = prossimo_id
            keyboard = [[InlineKeyboardButton("🔫 SPARA!", callback_data="roulette_spara")]]
            await query.edit_message_text(
                f"*Click!* 😅 Camera vuota! Si salva <b>{user.first_name}</b>!\n\n👉 Tocca all'altro sfidante!",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )

# --- MAIN ASYNC ---
async def main_async():
    if not TELEGRAM_TOKEN: return
    load_db()

    flask_thread = Thread(target=run_flask, daemon=True)
    flask_thread.start()

    application = Application.builder().token(TELEGRAM_TOKEN).build()

    if application.job_queue:
        application.job_queue.run_repeating(quiz_timeout_check, interval=60)

    # Registrazione Comandi
    application.add_handler(CommandHandler("sdrogocomm", show_hub))
    application.add_handler(CommandHandler("topricconi", show_leaderboard))
    application.add_handler(CommandHandler("troll", toggle_troll))
    application.add_handler(CommandHandler("pen", clear_penalties))
    application.add_handler(CommandHandler("resetduello", reset_duello))

    for cmd in ["roulette", "blackjack", "slot", "highlow", "wordle", "quiz", "shop", "heist", "titolo", "perseguita"]:
        application.add_handler(CommandHandler(cmd, block_direct_command))

    # Callbacks HUB, Shop & Games
    application.add_handler(CallbackQueryHandler(hub_callback, pattern="^hub_"))
    application.add_handler(CallbackQueryHandler(shop_buy_callback, pattern="^buy_"))
    application.add_handler(CallbackQueryHandler(handle_heist_callback, pattern="^heist_"))
    application.add_handler(CallbackQueryHandler(claim_daily_callback, pattern="^claim_daily_"))
    application.add_handler(CallbackQueryHandler(start_bj_from_hub, pattern="^start_bj_"))
    application.add_handler(CallbackQueryHandler(handle_bj_callback, pattern="^bj_"))
    application.add_handler(CallbackQueryHandler(start_slot_from_hub, pattern="^start_slot_"))
    application.add_handler(CallbackQueryHandler(start_wordle_from_hub, pattern="^start_wordle_"))
    application.add_handler(CallbackQueryHandler(start_mastermind_from_hub, pattern="^start_mm_"))
    
    # Category Quizzes
    application.add_handler(CallbackQueryHandler(lambda u, c: start_quiz_generic(u, c, QUIZ_CALCIO_DB, "CALCIO"), pattern="^start_qcalcio_"))
    application.add_handler(CallbackQueryHandler(lambda u, c: start_quiz_generic(u, c, QUIZ_CINEMA_DB, "CINEMA"), pattern="^start_qcinema_"))
    application.add_handler(CallbackQueryHandler(lambda u, c: start_quiz_generic(u, c, QUIZ_SERIE_DB, "SERIE TV"), pattern="^start_qserie_"))
    application.add_handler(CallbackQueryHandler(lambda u, c: start_quiz_generic(u, c, QUIZ_CITTA_DB, "CITTA"), pattern="^start_qcitta_"))
    application.add_handler(CallbackQueryHandler(lambda u, c: start_quiz_generic(u, c, QUIZ_SPORTIVI_DB, "SPORTIVI"), pattern="^start_qsport_"))
    
    application.add_handler(CallbackQueryHandler(start_quiz_multiplayer, pattern="^start_qmulti_"))
    application.add_handler(CallbackQueryHandler(quiz_more_hint, pattern="^quiz_hint_"))
    application.add_handler(CallbackQueryHandler(start_roulette_prep, pattern="^start_roulette_"))
    application.add_handler(CallbackQueryHandler(start_highlow_prep, pattern="^start_highlow_"))
    application.add_handler(CallbackQueryHandler(gestione_bottoni_roulette, pattern="^roulette_"))
    application.add_handler(CallbackQueryHandler(handle_highlow_callback, pattern="^hl_"))

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_messages))

    print("SdrogoBot v4.2 Integrale pronto all'uso!", flush=True)

    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)
    await asyncio.Event().wait()

if __name__ == '__main__':
    try: asyncio.run(main_async())
    except (KeyboardInterrupt, SystemExit): pass
