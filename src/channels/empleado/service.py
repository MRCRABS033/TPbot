"""#empleado-del-mes: calcula el ranking semanal y cierra la semana. No sabe nada de Discord."""
from datetime import datetime
from typing import NamedTuple

import pytz

SERVER_SAVE_TZ = pytz.timezone("Europe/Berlin")
SERVER_SAVE_HOUR = 10
SUNDAY = 6


class RankEntry(NamedTuple):
    char: str
    owner: str
    gained: int
    level: int


def is_server_save(now):
    return now.hour == SERVER_SAVE_HOUR and now.minute == 0


def is_week_close_time(now):
    """Domingo, desde el Server Save hasta el final del día."""
    return now.weekday() == SUNDAY and now.hour >= SERVER_SAVE_HOUR


def _entry(data):
    curr = data.get("current_level", 0)
    start = data.get("weekly_start_level", curr)
    return RankEntry(data["char_name"], data["owner"], max(0, curr - start), curr)


def _sorted(entries):
    return sorted(entries, key=lambda e: e.gained, reverse=True)


class EmpleadoService:
    def __init__(self, players, guilds, now=lambda: datetime.now(SERVER_SAVE_TZ)):
        self._players = players
        self._guilds = guilds
        self.now = now

    async def has_players(self, guild_id):
        return await self._players.for_guild(guild_id).count() > 0

    async def ranking(self, guild_id):
        """Personajes de más a menos niveles ganados en la semana actual."""
        players = await self._players.for_guild(guild_id).all()
        return _sorted(_entry(data) for data in players.values())

    async def week_is_open(self, guild_id, now):
        """True si esta semana todavía no se cerró en ese servidor."""
        return await self._guilds.week_closed_on(guild_id) != now.date().isoformat()

    async def close_week(self, guild_id, now):
        """
        Reinicia los contadores para la semana siguiente y deja registrado el cierre.
        Se llama después de anunciar al ganador: si el anuncio falla, la semana sigue abierta y se reintenta.
        """
        repository = self._players.for_guild(guild_id)
        for key, data in (await repository.all()).items():
            await repository.update(key, weekly_start_level=data.get("current_level", 0))
        await self._guilds.set_week_closed_on(guild_id, now.date().isoformat())
