"""#hunteds: administra la lista y detecta conexiones, niveles y muertes. No sabe nada de Discord."""
import asyncio
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


class HuntedService:
    def __init__(self, api, players, save, logins, parse_time, session_factory=create_session):
        self._api = api
        self._players = players
        self._save = save
        self._logins = logins          # Hora de ingreso de cada hunted conectado
        self._parse_time = parse_time  # Convierte el last_login de la API
        self._session_factory = session_factory

    # --- LISTA ---
    @property
    def players(self):
        return self._players

    def has_players(self):
        return bool(self._players)

    def player_count(self):
        return len(self._players)

    def get(self, character_name):
        """(clave, datos) del hunted; los datos son None si no está en la lista."""
        key = character_name.strip().lower()
        return key, self._players.get(key)

    def online_keys(self):
        """Hunteds conectados ahora."""
        return set(self._logins.since)

    def login_text(self, key):
        return self._logins.text(key)

    def split_by_connection(self):
        """(conectados, desconectados), de mayor a menor nivel."""
        online_keys = self.online_keys()
        hunteds = sorted(self._players.items(), key=lambda kv: kv[1].get("level", 0), reverse=True)
        online = [data for key, data in hunteds if key in online_keys]
        offline = [data for key, data in hunteds if key not in online_keys]
        return online, offline

    async def add(self, character_name, added_by):
        async with self._session_factory() as session:
            res = await self._api.fetch_player_data(session, character_name.strip())
        char_info = (res or {}).get("character_info")
        if not char_info:
            return AddResult(NOT_FOUND)

        name = res["official_name"]
        key = name.lower()
        if key in self._players:
            return AddResult(ALREADY_LISTED, name=name)

        deaths = res.get("deaths", [])
        self._players[key] = {
            "char_name": name,
            "tibia_link": res["tibia_link"],
            "world": char_info.get("world", "N/A"),
            "level": char_info.get("level", 0),
            "vocation": char_info.get("vocation", "N/A"),
            "added_by": added_by,
            # Última muerte conocida: solo se avisarán las muertes posteriores
            "last_death_time": deaths[0]["time"] if deaths else None,
        }
        self._save()
        return AddResult(ADDED, name=name, data=self._players[key])

    def remove(self, character_name):
        """Devuelve los datos del hunted eliminado, o None si no estaba en la lista."""
        data = self._players.pop(character_name.strip().lower(), None)
        if data:
            self._save()
        return data

    # --- REVISIÓN AUTOMÁTICA ---
    async def check(self, world):
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_LOOKUPS)

        async def lookup(name):
            async with semaphore:
                return await self._api.fetch_player_data(session, name)

        entries = list(self._players.items())
        async with self._session_factory() as session:
            online_world = await self._api.fetch_world_online_players(session, world)
            # Se consulta a cada hunted para detectar muertes (aunque esté desconectado)
            details = await asyncio.gather(*(lookup(d["char_name"]) for _, d in entries))

        online_lookup = {p.get("name", "").lower(): p for p in online_world}
        alerts = []
        online = []
        last_logins = {}
        changed = False

        for (key, data), res in zip(entries, details):
            if key not in self._players:
                continue  # Lo eliminaron mientras se revisaba

            # --- MUERTES ---
            deaths = (res or {}).get("deaths", [])
            if res and deaths and deaths[0].get("time") != data.get("last_death_time"):
                data["last_death_time"] = deaths[0]["time"]
                changed = True
                # Copia: el aviso muestra los datos de este momento, antes de actualizar nivel y vocación
                alerts.append(DeathAlert(dict(data), deaths[0], res.get("character_info", {})))

            player = online_lookup.get(key)
            if not player:
                continue

            # --- CONECTADO (sin aviso: aparece en la lista con su hora de ingreso) ---
            lvl = player.get("level", 0)
            voc = player.get("vocation", data.get("vocation", "N/A"))
            last_logins[key] = self._parse_time((res or {}).get("character_info", {}).get("last_login"))

            # --- NIVEL ---
            old_level = data.get("level")
            if old_level and lvl != old_level:
                alerts.append(LevelAlert(dict(data), old_level, lvl, voc))
            if lvl != old_level or voc != data.get("vocation"):
                data["level"], data["vocation"] = lvl, voc
                changed = True

            online.append(OnlineHunted(key, lvl, voc, data["char_name"]))

        if changed:
            self._save()
        # Si la API del mundo falló (lista vacía), no se actualiza el estado de conexión
        if online_world:
            self._logins.update({player.key for player in online}, last_logins)
        return HuntedCheck(alerts, online)
