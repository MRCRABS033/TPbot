import json
import os
from config import WATCHED_PLAYERS, logger

DATA_FILE = "watched_players.json"

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