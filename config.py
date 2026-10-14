import logging
import os
from dotenv import load_dotenv

load_dotenv()

# --- LOGS ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("TibiaBot")

# --- CREDENCIALES ---
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise ValueError("Falta DISCORD_TOKEN en el archivo .env")

# --- ESTADO COMPARTIDO ---
WATCHED_PLAYERS = {}  # { char_key: { "char_name": str, "tibia_link": str, "owner": str } }
PLAYERS_STATE = {}    # { char_key: { "level": int, "last_death_time": str } }
HUNTED_PLAYERS = {}   # { char_key: { "char_name", "tibia_link", "world", "level", "vocation", "added_by", "last_death_time" } }

# Mundo de Tibia que se vigila (onlines, frees, hunteds)
WORLD = "Quidera"

# Nombre del team (se muestra en las gráficas)
TEAM_NAME = "Tres Puntos"

# --- PROXY PARA EL CHAR BAZAAR (opcional) ---
# tibia.com bloquea las IPs de muchos hostings (error 403). Si se define, las consultas al bazaar
# salen por este proxy. Formatos: http://usuario:clave@host:puerto  o  socks5://usuario:clave@host:puerto
BAZAAR_PROXY_URL = os.getenv("BAZAAR_PROXY_URL") or None
