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

# --- BASE DE DATOS ---
# Ej: mongodb://localhost:27017/tibia?directConnection=true (el nombre de la base va en la URI)
MONGODB_URI = os.getenv("MONGODB_URI")

# --- PROXY PARA EL CHAR BAZAAR (opcional) ---
# tibia.com bloquea las IPs de muchos hostings (error 403). Si se define, las consultas al bazaar
# salen por este proxy. Formatos: http://usuario:clave@host:puerto  o  socks5://usuario:clave@host:puerto
# Se quitan espacios, saltos de línea y comillas (por si la dirección se pegó partida o entre comillas)
BAZAAR_PROXY_URL = "".join((os.getenv("BAZAAR_PROXY_URL") or "").split()).strip("\"'") or None
