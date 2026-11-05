"""#onlines y #level-up: quién del grupo está conectado y quién cambió de nivel. No sabe nada de Discord."""
import asyncio
from dataclasses import dataclass
from typing import NamedTuple

from src.api.http import create_session

MAX_CONCURRENT_LOOKUPS = 5


class OnlinePlayer(NamedTuple):
    key: str
    level: int
    vocation: str
    name: str
    owner: str


@dataclass
class CharacterCheck:
    """Resultado de !nivel <personaje>. Sin `info`: no está monitoreado. Sin `char_info`: la API no respondió."""
    info: dict = None
    char_info: dict = None
    changed_from: int = None  # Nivel anterior, si cambió


class OnlineService:
    def __init__(self, api, players, save, logins, parse_time, session_factory=create_session):
        self._api = api
        self._players = players
        self._save = save
        self._logins = logins          # Hora de ingreso de cada personaje conectado
        self._parse_time = parse_time  # Convierte el last_login de la API
        self._session_factory = session_factory

    def has_players(self):
        return bool(self._players)

    def player_count(self):
        return len(self._players)

    def login_text(self, key):
        return self._logins.text(key)

    async def check_online(self, world, on_level_change):
        """
        Devuelve los personajes monitoreados conectados y llama a
        on_level_change(info, old_level, new_level, vocation) por cada cambio de nivel.
        """
        async with self._session_factory() as session:
            online_world_players = await self._api.fetch_world_online_players(session, world)
            online_lookup = {
                p.get("name", "").lower(): p for p in (online_world_players or [])
            }

            online = []
            for char_key, info in self._players.items():
                char_name = info["char_name"]
                player_data = online_lookup.get(char_name.lower())
                if player_data is None:
                    continue

                lvl = player_data.get("level", 0)
                voc = player_data.get("vocation", "N/A")
                old_level = info.get("current_level")

                if old_level is None:
                    # Inicializar si no estaba registrado
                    info["current_level"] = lvl
                    self._save()
                elif lvl != old_level:
                    # Subió o bajó de nivel (por ejemplo, al morir)
                    info["current_level"] = lvl
                    self._save()
                    await on_level_change(info, old_level, lvl, voc)

                online.append(OnlinePlayer(char_key, lvl, voc, char_name, info["owner"]))

            # Hora de ingreso (si la API falló y la lista vino vacía, no se toca el estado)
            if online_world_players:
                online_keys = {player.key for player in online}
                last_logins = await self._api_login_times(session, online_keys) if self._logins.needs_api_times else None
                self._logins.update(online_keys, last_logins)

        return online

    async def check_character(self, character_name):
        """Consulta un personaje monitoreado y guarda su nivel si cambió."""
        info = self._players.get(character_name.strip().lower())
        if not info:
            return CharacterCheck()

        async with self._session_factory() as session:
            res = await self._api.fetch_player_data(session, info["char_name"])
        char_info = (res or {}).get("character_info")
        if not char_info:
            return CharacterCheck(info=info)

        level = char_info.get("level", 0)
        old_level = info.get("current_level", level)
        if level == old_level:
            return CharacterCheck(info=info, char_info=char_info)

        # Subió o bajó y el aviso automático aún no lo había detectado
        info["current_level"] = level
        self._save()
        return CharacterCheck(info=info, char_info=char_info, changed_from=old_level)

    async def _api_login_times(self, session, keys):
        """{clave: hora de ingreso} según la API (solo al arrancar el bot, para quienes ya estaban online)."""
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_LOOKUPS)

        async def lookup(key):
            async with semaphore:
                res = await self._api.fetch_player_data(session, self._players[key]["char_name"])
            return key, self._parse_time((res or {}).get("character_info", {}).get("last_login"))

        return dict(await asyncio.gather(*(lookup(k) for k in keys)))
