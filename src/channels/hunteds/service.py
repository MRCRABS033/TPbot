"""#hunteds: administra la lista y detecta conexiones, niveles y muertes. No sabe nada de Discord."""
import asyncio
from collections import defaultdict
from dataclasses import dataclass
from typing import NamedTuple

from src.api.http import create_session

# Consultas simultáneas a la API al revisar muertes de hunteds
MAX_CONCURRENT_LOOKUPS = 5

ADDED, NOT_FOUND, ALREADY_LISTED = "added", "not_found", "already_listed"


class DeathAlert(NamedTuple):
    data: dict
    death: dict
    char_info: dict


class LevelAlert(NamedTuple):
    data: dict
    old_level: int
    new_level: int
    vocation: str


class OnlineHunted(NamedTuple):
    key: str
    level: int
    vocation: str
    name: str


@dataclass
class HuntedCheck:
    alerts: list  # DeathAlert y LevelAlert, en el orden en que se detectaron
    online: list  # OnlineHunted


@dataclass
class AddResult:
    status: str
    name: str = None
    data: dict = None
    world: str = None  # Mundo que sigue el servidor


class HuntedService:
    def __init__(self, api, players, guilds, logins_factory, parse_time, session_factory=create_session):
        self._api = api
        self._players = players
        self._guilds = guilds
        self._logins = defaultdict(logins_factory)  # { guild_id: hora de ingreso de cada hunted conectado }
        self._parse_time = parse_time               # Convierte el last_login de la API
        self._session_factory = session_factory

    async def world(self, guild_id):
        """Mundo que sigue el servidor."""
        return await self._guilds.world(guild_id)

    # --- LISTA ---
    async def all(self, guild_id):
        return await self._players.for_guild(guild_id).all()

    async def has_players(self, guild_id):
        return await self.player_count(guild_id) > 0

    async def player_count(self, guild_id):
        return await self._players.for_guild(guild_id).count()

    async def get(self, guild_id, character_name):
        """(clave, datos) del hunted; los datos son None si no está en la lista."""
        key = character_name.strip().lower()
        return key, await self._players.for_guild(guild_id).get(key)

    def online_keys(self, guild_id):
        """Hunteds conectados ahora."""
        return set(self._logins[guild_id].since)

    def login_text(self, guild_id, key):
        return self._logins[guild_id].text(key)

    async def split_by_connection(self, guild_id):
        """(conectados, desconectados), de mayor a menor nivel."""
        online_keys = self.online_keys(guild_id)
        players = await self.all(guild_id)
        hunteds = sorted(players.items(), key=lambda kv: kv[1].get("level", 0), reverse=True)
        online = [data for key, data in hunteds if key in online_keys]
        offline = [data for key, data in hunteds if key not in online_keys]
        return online, offline

    async def add(self, guild_id, character_name, added_by):
        async with self._session_factory() as session:
            res = await self._api.fetch_player_data(session, character_name.strip())
        char_info = (res or {}).get("character_info")
        if not char_info:
            return AddResult(NOT_FOUND)

        repository = self._players.for_guild(guild_id)
        name = res["official_name"]
        key = name.lower()
        if await repository.get(key):
            return AddResult(ALREADY_LISTED, name=name)

        deaths = res.get("deaths", [])
        data = {
            "char_name": name,
            "tibia_link": res["tibia_link"],
            "world": char_info.get("world", "N/A"),
            "level": char_info.get("level", 0),
            "vocation": char_info.get("vocation", "N/A"),
            "added_by": added_by,
            # Última muerte conocida: solo se avisarán las muertes posteriores
            "last_death_time": deaths[0]["time"] if deaths else None,
        }
        await repository.put(key, data)
        return AddResult(ADDED, name=name, data=data, world=await self.world(guild_id))

    async def remove(self, guild_id, character_name):
        """Devuelve los datos del hunted eliminado, o None si no estaba en la lista."""
        return await self._players.for_guild(guild_id).remove(character_name.strip().lower())

    # --- REVISIÓN AUTOMÁTICA ---
    async def check(self, guild_id, world):
        repository = self._players.for_guild(guild_id)
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_LOOKUPS)

        async def lookup(name):
            async with semaphore:
                return await self._api.fetch_player_data(session, name)

        entries = list((await repository.all()).items())
        async with self._session_factory() as session:
            online_world = await self._api.fetch_world_online_players(session, world)
            # Se consulta a cada hunted para detectar muertes (aunque esté desconectado)
            details = await asyncio.gather(*(lookup(d["char_name"]) for _, d in entries))

        online_lookup = {p.get("name", "").lower(): p for p in online_world}
        current = await repository.all()  # Pudieron eliminar a alguien mientras se revisaba
        alerts = []
        online = []
        last_logins = {}

        for (key, data), res in zip(entries, details):
            if key not in current:
                continue

            changes = {}

            # --- MUERTES ---
            deaths = (res or {}).get("deaths", [])
            if res and deaths and deaths[0].get("time") != data.get("last_death_time"):
                changes["last_death_time"] = deaths[0]["time"]
                alerts.append(DeathAlert(data, deaths[0], res.get("character_info", {})))

            player = online_lookup.get(key)
            if player:
                # --- CONECTADO (sin aviso: aparece en la lista con su hora de ingreso) ---
                lvl = player.get("level", 0)
                voc = player.get("vocation", data.get("vocation", "N/A"))
                last_logins[key] = self._parse_time((res or {}).get("character_info", {}).get("last_login"))

                # --- NIVEL ---
                old_level = data.get("level")
                if old_level and lvl != old_level:
                    alerts.append(LevelAlert(data, old_level, lvl, voc))
                if lvl != old_level or voc != data.get("vocation"):
                    changes.update(level=lvl, vocation=voc)

                online.append(OnlineHunted(key, lvl, voc, data["char_name"]))

            if changes:
                await repository.update(key, **changes)

        # Si la API del mundo falló (lista vacía), no se actualiza el estado de conexión
        if online_world:
            self._logins[guild_id].update({player.key for player in online}, last_logins)
        return HuntedCheck(alerts, online)
