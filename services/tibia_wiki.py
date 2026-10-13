import logging

logger = logging.getLogger("TibiaBot")

# TibiaWiki BR: redirige directo a la imagen. (La wiki de fandom bloquea las descargas de bots)
WIKI_FILE_URL = "https://www.tibiawiki.com.br/wiki/Special:FilePath/{}"
WIKI_HEADERS = {"User-Agent": "Mozilla/5.0 (TPbot)"}

# Outfit genérico por vocación (archivos de TibiaWiki)
VOCATION_OUTFITS = {
    "knight": "Outfit_Knight_Male.gif",
    "paladin": "Outfit_Hunter_Male.gif",
    "sorcerer": "Outfit_Mage_Male.gif",
    "druid": "Outfit_Summoner_Male.gif",
    "monk": "Outfit_Monk_Male.gif",
}
DEFAULT_OUTFIT = "Outfit_Citizen_Male.gif"  # Sin vocación o desconocida

_LOWERCASE_WORDS = {"of", "the", "a", "an", "in", "on", "and"}
_image_cache = {}  # { file_name: bytes | None }


def creature_file_name(creature_name: str) -> str:
    """'burster spectre' -> 'Burster_Spectre.gif' (formato de TibiaWiki)."""
    words = creature_name.strip().split()
    titled = [
        w if i > 0 and w.lower() in _LOWERCASE_WORDS else w[:1].upper() + w[1:]
        for i, w in enumerate(words)
    ]
    return "_".join(titled) + ".gif"


def vocation_key(vocation: str) -> str:
    """'Elite Knight' -> 'knight'. Devuelve 'none' si no tiene vocación o no se reconoce."""
    vocation = (vocation or "").lower()
    return next((key for key in VOCATION_OUTFITS if key in vocation), "none")


def vocation_file_name(vocation: str) -> str:
    """'Elite Knight' -> 'Outfit_Knight_Male.gif'."""
    return VOCATION_OUTFITS.get(vocation_key(vocation), DEFAULT_OUTFIT)


def vocation_image_url(vocation: str) -> str:
    """URL pública del outfit genérico de la vocación (Discord la carga directamente)."""
    return WIKI_FILE_URL.format(vocation_file_name(vocation))


async def fetch_wiki_image(session, file_name: str):
    """Descarga una imagen de TibiaWiki. Devuelve los bytes o None si no existe."""
    if file_name in _image_cache:
        return _image_cache[file_name]

    image = None
    try:
        async with session.get(WIKI_FILE_URL.format(file_name), headers=WIKI_HEADERS) as response:
            if response.status == 200 and response.content_type.startswith("image/"):
                image = await response.read()
            elif response.status == 404:
                logger.info(f"🖼️ TibiaWiki no tiene la imagen {file_name}")
            else:
                logger.warning(f"⚠️ HTTP {response.status} al descargar {file_name} de TibiaWiki")
                return None  # No se cachea para reintentar la próxima vez
    except Exception as e:
        logger.error(f"❌ Error al consultar TibiaWiki para {file_name}: {e}")
        return None  # No se cachea para reintentar la próxima vez

    _image_cache[file_name] = image
    return image
