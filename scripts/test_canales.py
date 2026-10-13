"""
Envía avisos de muerte de PRUEBA al canal de muertes (CHANNEL_DEATHS) usando
el mismo código del bot, y se cierra solo.

Uso (desde la carpeta del proyecto):
    python scripts/test_canales.py
        1. La última muerte real del primer personaje monitoreado (contra monstruo).
        2. Una muerte PvP falsa, con otro personaje monitoreado como asesino.

    python scripts/test_canales.py vocaciones
        Una muerte PvP falsa por cada vocación (knight, paladin, sorcerer, druid,
        monk y sin vocación), usando como asesino a un jugador real online en Quidera.

⚠️ Usa el token del .env: úsalo solo con el bot de pruebas.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiohttp
import discord

from channels import CHANNEL_DEATHS, find_channel
from cogs.death_tracker import DeathTracker
from config import TOKEN, WATCHED_PLAYERS, logger
from services.storage import load_data
from services.tibia_api import fetch_player_data, fetch_world_online_players
from services.tibia_wiki import VOCATION_OUTFITS

WORLD = "Quidera"
MODE = sys.argv[1].lower() if len(sys.argv) > 1 else "basico"

intents = discord.Intents.default()
client = discord.Client(intents=intents)


def fake_pvp_death(victim, killer_name, assist_name=None):
    # Las asistencias en Tibia siempre son jugadores
    assists = [{"name": assist_name, "player": True, "traded": False, "summon": ""}] if assist_name else []
    return {
        "time": discord.utils.utcnow().isoformat(),
        "level": victim.get("current_level", 0),
        "killers": [{"name": killer_name, "player": True, "traded": False, "summon": ""}],
        "assists": assists,
        "reason": "🧪 PRUEBA: muerte PvP falsa",
    }


async def test_basico(tracker, victim, char_info, deaths):
    # 1. Última muerte real (normalmente contra un monstruo)
    if deaths:
        await tracker._notify_death(victim["tibia_link"], victim["owner"], victim.get("current_level", 0), char_info, deaths[0])
        logger.info(f"🧪 Enviada la última muerte real de {victim['char_name']}: {deaths[0].get('reason')}")
    else:
        logger.warning(f"⚠️ {victim['char_name']} no tiene muertes recientes en la API; se omite la prueba 1.")

    # 2. Muerte PvP falsa con otro personaje monitoreado como asesino
    others = [p for p in WATCHED_PLAYERS.values() if p is not victim]
    if not others:
        logger.warning("⚠️ Se necesitan al menos 2 personajes monitoreados para la prueba PvP.")
        return
    killer = others[0]
    assist = others[1] if len(others) > 1 else None
    assist_name = assist["char_name"] if assist else None
    await tracker._notify_death(victim["tibia_link"], victim["owner"], victim.get("current_level", 0), char_info, fake_pvp_death(victim, killer["char_name"], assist_name))
    logger.info(f"🧪 Enviada muerte PvP falsa (asesino: {killer['char_name']}, {killer.get('vocation')} | asistencia: {assist_name})")


async def test_vocaciones(tracker, victim, char_info):
    async with aiohttp.ClientSession() as session:
        online = await fetch_world_online_players(session, WORLD)

    # Un jugador online por cada vocación base (+ "sin vocación")
    wanted = list(VOCATION_OUTFITS) + ["none"]
    found = {}
    for player in online:
        vocation = player.get("vocation", "").lower()
        key = next((v for v in VOCATION_OUTFITS if v in vocation), "none")
        found.setdefault(key, player)

    for key in wanted:
        player = found.get(key)
        if not player:
            logger.warning(f"⚠️ No hay ningún jugador '{key}' online en {WORLD} ahora mismo; se omite.")
            continue
        # Asistencia: otro jugador online distinto del asesino
        assist = next((p for p in online if p["name"] != player["name"]), None)
        assist_name = assist["name"] if assist else None
        await tracker._notify_death(victim["tibia_link"], victim["owner"], victim.get("current_level", 0), char_info, fake_pvp_death(victim, player["name"], assist_name))
        logger.info(f"🧪 Enviada muerte PvP falsa ({key}): asesino {player['name']} ({player.get('vocation')}) | asistencia: {assist_name}")


@client.event
async def on_ready():
    load_data()
    victim = next(iter(WATCHED_PLAYERS.values()), None)
    if not victim:
        logger.error("❌ No hay personajes monitoreados para la prueba.")
        await client.close()
        return

    if not client.guilds:
        logger.error("❌ El bot no está en ningún servidor. Invítalo primero.")
        await client.close()
        return

    targets = [g for g in client.guilds if find_channel(g.text_channels, CHANNEL_DEATHS)]
    for guild in client.guilds:
        if guild not in targets:
            logger.warning(f"⚠️ El servidor '{guild.name}' no tiene un canal #{CHANNEL_DEATHS}")
    if not targets:
        await client.close()
        return

    # Instancia el cog sin arrancar sus tareas automáticas
    tracker = DeathTracker.__new__(DeathTracker)
    tracker.bot = client

    async with aiohttp.ClientSession() as session:
        res = await fetch_player_data(session, victim["char_name"])
    char_info = (res or {}).get("character_info", {"world": WORLD})
    deaths = (res or {}).get("deaths", [])

    if MODE == "vocaciones":
        await test_vocaciones(tracker, victim, char_info)
    else:
        await test_basico(tracker, victim, char_info, deaths)

    for guild in targets:
        logger.info(f"🧪 Avisos enviados a #{CHANNEL_DEATHS} en '{guild.name}'")
    await client.close()


client.run(TOKEN, log_handler=None)
