import logging
from datetime import datetime

logger = logging.getLogger("TibiaBot")

async def fetch_player_data(session, character_name: str):
    """Consulta la API pública de TibiaData v4 y extrae la información formateada del personaje."""
    url = f"https://api.tibiadata.com/v4/character/{character_name.replace(' ', '%20')}"
    logger.info(f"🔍 Consultando API TibiaData para personaje: '{character_name}'...")

    try:
        start_time = datetime.now()
        async with session.get(url) as response:
            elapsed = (datetime.now() - start_time).total_seconds()

            if response.status == 200:
                data = await response.json()
                logger.info(f" HTTP 200 OK - Respuesta recibida para '{character_name}' ({elapsed:.2f}s)")

                character_data = data.get("character", {})
                char_info = character_data.get("character", {})
                official_name = char_info.get("name", character_name)

                tibia_urls = data.get("information", {}).get("tibia_urls", [])
                official_url = tibia_urls[0] if tibia_urls else f"https://www.tibia.com/community/?subtopic=characters&name={official_name.replace(' ', '+')}"
                tibia_link = f"[{official_name}]({official_url})"

                return {
                    "raw_data": character_data,
                    "character_info": char_info,
                    "deaths": character_data.get("deaths", []),
                    "official_name": official_name,
                    "tibia_url": official_url,
                    "tibia_link": tibia_link
                }
            else:
                logger.warning(f"⚠️ HTTP {response.status} al consultar '{character_name}' ({elapsed:.2f}s)")
    except Exception as e:
        logger.error(f"❌ Error al consultar a {character_name}: {e}")

    return None


async def fetch_world_online_players(session, world_name: str = "Quidera"):
    """Consulta los jugadores online de un mundo en la API de TibiaData v4."""
    url = f"https://api.tibiadata.com/v4/world/{world_name}"
    logger.info(f"🌐 Consultando jugadores online en el mundo '{world_name}'...")

    try:
        async with session.get(url) as response:
            if response.status == 200:
                data = await response.json()
                world_data = data.get("world", {})
                online_players = world_data.get("online_players", [])
                return online_players
            else:
                logger.warning(f"⚠️ HTTP {response.status} al consultar el mundo '{world_name}'")
    except Exception as e:
        logger.error(f"❌ Error al consultar el mundo {world_name}: {e}")

    return []

async def fetch_boosted_boss(session):
    """Boss boosted del día según TibiaData v4. Devuelve {"name", "image_url"} o None si falla."""
    url = "https://api.tibiadata.com/v4/boostablebosses"
    try:
        async with session.get(url) as response:
            if response.status == 200:
                data = await response.json()
                boosted = data.get("boostable_bosses", {}).get("boosted", {})
                if boosted.get("name"):
                    return {"name": boosted["name"], "image_url": boosted.get("image_url")}
            else:
                logger.warning(f"⚠️ HTTP {response.status} al consultar el boss boosted")
    except Exception as e:
        logger.error(f"❌ Error al consultar el boss boosted: {e}")
    return None

async def fetch__boosted_creature(session):
    """Devuelve las creaturas boosted del día según TibiaData v4. Devuelve {"name", "image_url"} o None si falla."""
    url = "https://api.tibiadata.com/v4/creatures"
    try:
        async with session.get(url) as response:
            if response.status == 200:
                data = await response.json()
                boosted = data.get("creatures", {}).get("boosted", {})
                if boosted.get("name"):
                    return {"name": boosted["name"], "image_url": boosted.get("image_url")}
            else:
                logger.warning(f"⚠️ HTTP {response.status} al consultar el boosted creature")
    except Exception as e:
        logger.error(f"❌ Error al consultar el boosted creature: {e}")
    return None