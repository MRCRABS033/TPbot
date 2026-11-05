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


def is_weekly_reset_day(now):
    return now.weekday() == SUNDAY


def _entry(data):
    curr = data.get("current_level", 0)
    start = data.get("weekly_start_level", curr)
    return RankEntry(data["char_name"], data["owner"], max(0, curr - start), curr)


def _sorted(entries):
    return sorted(entries, key=lambda e: e.gained, reverse=True)


class EmpleadoService:
    def __init__(self, players, save, now=lambda: datetime.now(SERVER_SAVE_TZ)):
        self._players = players
        self._save = save
        self.now = now

    def has_players(self):
        return bool(self._players)

    def ranking(self):
        """Personajes de más a menos niveles ganados en la semana actual."""
        return _sorted(_entry(data) for data in self._players.values())

    def close_week(self):
        """Devuelve el ranking final de la semana y reinicia los contadores para la siguiente."""
        entries = []
        for data in self._players.values():
            entries.append(_entry(data))
            data["weekly_start_level"] = data.get("current_level", 0)
        self._save()
        return _sorted(entries)
