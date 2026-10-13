import logging
import re

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


# --- INFORMACIÓN DE BOSSES (TibiaWiki en inglés, vía su API de texto) ---
FANDOM_API = "https://tibia.fandom.com/api.php"
BOSS_INFO_FIELDS = ("hp", "exp", "location", "bosstiaryclass", "cooldown")
_boss_info_cache = {}  # { nombre: dict | None }


def _mapper_link(params):
    """{{Mapper Coords|text=aquí|132.94|122.246|6|...}} -> enlace al mapa de tibiamaps.io."""
    parts = params.split("|")
    text = next((p.split("=", 1)[1] for p in parts if p.startswith("text=")), "mapa")
    coords = [p for p in parts if "=" not in p]
    try:
        # Formato de TibiaWiki: "sector.posición" (256 posiciones por sector)
        x_sector, x_pos = coords[0].split(".")
        y_sector, y_pos = coords[1].split(".")
        x, y, z = int(x_sector) * 256 + int(x_pos), int(y_sector) * 256 + int(y_pos), int(coords[2])
        return f"[{text}](https://tibiamaps.io/map#{x},{y},{z}:2)"
    except (IndexError, ValueError):
        return text


def clean_wikitext(text):
    """Convierte el texto de la wiki en texto de Discord: enlaces internos -> texto, mapas -> enlace."""
    text = re.sub(r"\{\{Mapper Coords\|([^{}]*)\}\}", lambda m: _mapper_link(m.group(1)), text)
    text = re.sub(r"\{\{[^{}]*\}\}", "", text)                       # Otras plantillas
    text = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", text)     # [[Página|texto]] -> texto
    text = re.sub(r"<[^>]+>", " ", text)                             # Etiquetas HTML
    text = re.sub(r"\s+", " ", text).strip(" ,.;")
    return text


def _wiki_page_names(boss_name):
    names = [n[:-4] for n in wiki_file_names_for(boss_name)]
    return list(dict.fromkeys(names))


def wiki_file_names_for(boss_name):
    """Variantes del nombre como en TibiaWiki ('Neferi The Spy' -> Neferi_The_Spy.gif, Neferi_the_Spy.gif...)."""
    words = boss_name.split()
    small_lower = [w.lower() if i > 0 and w.lower() in _LOWERCASE_WORDS else w for i, w in enumerate(words)]
    hyphen_lower = [w[:1] + w[1:].lower() for w in small_lower]
    names = ["_".join(words), "_".join(small_lower), "_".join(hyphen_lower)]
    return [f"{n}.gif" for n in dict.fromkeys(names)]


async def fetch_boss_info(session, boss_name):
    """
    Datos del boss desde TibiaWiki: {"hp", "exp", "location", "bosstiaryclass", "cooldown"}.
    Devuelve None si no se encuentra. Se guarda en memoria (cambia muy poco).
    """
    if boss_name in _boss_info_cache:
        return _boss_info_cache[boss_name]

    for page in _wiki_page_names(boss_name):
        params = {"action": "parse", "page": page, "prop": "wikitext", "format": "json", "redirects": 1}
        try:
            async with session.get(FANDOM_API, params=params, headers=WIKI_HEADERS) as response:
                if response.status != 200:
                    continue
                data = await response.json(content_type=None)
        except Exception as e:
            logger.warning(f"⚠️ Error al consultar TibiaWiki para {boss_name}: {e}")
            return None  # No se cachea: se reintenta la próxima vez
        wikitext = data.get("parse", {}).get("wikitext", {}).get("*")
        if not wikitext or "{{Infobox Creature" not in wikitext:
            continue

        info = {}
        for field in BOSS_INFO_FIELDS:
            match = re.search(rf"^\|\s*{field}\s*=\s*(.*)$", wikitext, re.M)
            if match:
                value = clean_wikitext(match.group(1))
                if value and value not in ("?", "--"):
                    info[field] = value
        _boss_info_cache[boss_name] = info
        return info

    _boss_info_cache[boss_name] = None
    return None
