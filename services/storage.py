import json
import os
from config import HUNTED_PLAYERS, WATCHED_PLAYERS, logger

DATA_FILE = "watched_players.json"
HUNTED_FILE = "hunteds.json"

def load_data():
    """Carga los personajes desde el archivo JSON al iniciar el bot."""
    if not os.path.exists(DATA_FILE):
        logger.info(f"📁 {DATA_FILE} no existe. Se creará uno nuevo al agregar personajes.")
        return

    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            WATCHED_PLAYERS.clear()
            WATCHED_PLAYERS.update(data)
            logger.info(f"✅ Se cargaron {len(WATCHED_PLAYERS)} personajes desde {DATA_FILE}")
    except Exception as e:
        logger.error(f"❌ Error al cargar {DATA_FILE}: {e}")

def save_data():
    """Guarda el diccionario WATCHED_PLAYERS en el archivo JSON."""
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(WATCHED_PLAYERS, f, indent=4, ensure_ascii=False)
            logger.info(f"💾 Datos guardados en {DATA_FILE}")
    except Exception as e:
        logger.error(f"❌ Error al guardar {DATA_FILE}: {e}")


def load_hunteds():
    """Carga la lista de hunteds desde el archivo JSON al iniciar el bot."""
    if not os.path.exists(HUNTED_FILE):
        return

    try:
        with open(HUNTED_FILE, "r", encoding="utf-8") as f:
            HUNTED_PLAYERS.clear()
            HUNTED_PLAYERS.update(json.load(f))
            logger.info(f"✅ Se cargaron {len(HUNTED_PLAYERS)} hunteds desde {HUNTED_FILE}")
    except Exception as e:
        logger.error(f"❌ Error al cargar {HUNTED_FILE}: {e}")


def save_hunteds():
    """Guarda el diccionario HUNTED_PLAYERS en el archivo JSON."""
    try:
        with open(HUNTED_FILE, "w", encoding="utf-8") as f:
            json.dump(HUNTED_PLAYERS, f, indent=4, ensure_ascii=False)
    except Exception as e:
        logger.error(f"❌ Error al guardar {HUNTED_FILE}: {e}")
