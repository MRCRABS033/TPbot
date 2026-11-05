"""#cementerio: detecta muertes del grupo y reúne los datos de cada aviso. No sabe nada de Discord."""
import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from src.api.http import create_session

# Máximo de jugadores (asesinos + asistencias) a consultar en la API por cada muerte
MAX_PLAYER_LOOKUPS = 10
# Zona horaria para decidir qué es "hoy" en !muertes (Colombia, UTC-5)
LOCAL_TZ = timezone(timedelta(hours=-5))
# Consultas simultáneas a la API en !muertes
MAX_CONCURRENT_LOOKUPS = 5


@dataclass
class TodayDeaths:
    entries: list   # [(hora, jugador, muerte)], de la más reciente a la más antigua
    details: dict   # { char_name: respuesta de la API }
    checked: int    # Personajes revisados
    failed: int     # Personajes sin respuesta de la API


def parse_time(time_str):
    try:
        return datetime.fromisoformat(time_str.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


class DeathService:
    def __init__(self, api, wiki, players, state, session_factory=create_session,
                 now=lambda: datetime.now(LOCAL_TZ), pause_seconds=0.5):
        self._api = api
        self._wiki = wiki
        self._players = players  # { char_key: { "char_name", "owner", ... } }
        self._state = state      # { char_key: { "level", "last_death_time" } }
        self._session_factory = session_factory
        self._now = now
        self._pause_seconds = pause_seconds

    async def check_players(self, on_death):
        """
        Revisa a los personajes monitoreados y llama a
        on_death(tibia_link, owner, level, char_info, death) por cada muerte nueva.
        La muerte se marca como vista después de avisar: si el aviso falla, se reintenta en la siguiente revisión.
        """
        if not self._players:
            return

        current_entries = list(self._players.items())

        async with self._session_factory() as session:
            for char_key, info in current_entries:
                char_name = info["char_name"]
                owner_name = info["owner"]

                res = await self._api.fetch_player_data(session, char_name)
                if not res or not res.get("character_info"):
                    continue

                char_info = res["character_info"]
                current_level = char_info.get("level", 0)
                deaths = res.get("deaths", [])
                tibia_link = res.get("tibia_link", char_name)

                # Registro inicial silencioso: solo se guarda el punto de partida
                if char_key not in self._state:
                    self._state[char_key] = {
                        "level": current_level,
                        "last_death_time": deaths[0]["time"] if deaths else None,
                    }
                    continue

                last_state = self._state[char_key]

                # Los avisos de nivel los publica watched_tracker en el canal de niveles
                self._state[char_key]["level"] = current_level

                # Detección de Muerte
                if deaths and deaths[0]["time"] != last_state["last_death_time"]:
                    latest_death = deaths[0]
                    await on_death(tibia_link, owner_name, current_level, char_info, latest_death)
                    self._state[char_key]["last_death_time"] = latest_death["time"]

                await asyncio.sleep(self._pause_seconds)

    async def deaths_today(self):
        """Muertes de hoy (hora local) de todos los personajes monitoreados."""
        today = self._now().date()
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_LOOKUPS)
        players = list(self._players.values())

        async with self._session_factory() as session:
            async def lookup(name):
                async with semaphore:
                    return await self._api.fetch_player_data(session, name)

            results = await asyncio.gather(*(lookup(p["char_name"]) for p in players))

        entries = []
        details = {}
        failed = 0
        for data, res in zip(players, results):
            if not res or not res.get("character_info"):
                failed += 1
                continue
            details[data["char_name"]] = res
            for death in res.get("deaths", []):
                when = parse_time(death.get("time"))
                if when and when.astimezone(LOCAL_TZ).date() == today:
                    entries.append((when, data, death))
        entries.sort(key=lambda d: d[0], reverse=True)
        return TodayDeaths(entries, details, checked=len(players), failed=failed)

    async def fetch_players_info(self, participants):
        """Consulta en la API a los jugadores de la lista. Devuelve { nombre: character_info }."""
        names = list(dict.fromkeys(k["name"] for k in participants if k.get("player") and k.get("name")))
        names = names[:MAX_PLAYER_LOOKUPS]
        if not names:
            return {}

        async with self._session_factory() as session:
            results = await asyncio.gather(*(self._api.fetch_player_data(session, name) for name in names))
        return {
            name: res["character_info"]
            for name, res in zip(names, results)
            if res and res.get("character_info")
        }

    async def killer_image(self, killer, player_infos):
        """Imagen del monstruo, o un outfit genérico según la vocación si es un jugador."""
        if not killer:
            return None

        async with self._session_factory() as session:
            if killer.get("player"):
                vocation = player_infos.get(killer["name"], {}).get("vocation", "")
                return await self._wiki.fetch_wiki_image(session, self._wiki.vocation_file_name(vocation))
            return await self._wiki.fetch_wiki_image(session, self._wiki.creature_file_name(killer["name"]))
