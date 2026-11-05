"""#frees: decide qué jugadores online son Free Account. No sabe nada de Discord."""
import asyncio
import time

from src.api.http import create_session

# Cuánto tiempo se recuerda si una cuenta es free (evita consultar a todos los jugadores cada minuto)
ACCOUNT_STATUS_TTL = 60 * 60  # 1 hora
MAX_CONCURRENT_REQUESTS = 8


class FreeService:
    def __init__(self, api, session_factory=create_session, clock=time.monotonic):
        self._api = api
        self._session_factory = session_factory
        self._clock = clock
        self._account_cache = {}  # { nombre: (es_free, hora_de_consulta) }

    async def get_free_players(self, world):
        """Jugadores online de `world` con cuenta free."""
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

        async with self._session_factory() as session:
            online_players = await self._api.fetch_world_online_players(session, world)
            statuses = await asyncio.gather(
                *(self._is_free(session, semaphore, p.get("name")) for p in online_players)
            )

        self._forget_offline(online_players)
        return [p for p, is_free in zip(online_players, statuses) if is_free]

    async def _is_free(self, session, semaphore, name):
        """True/False si es free, o None si no se pudo consultar."""
        cached = self._account_cache.get(name)
        if cached and self._clock() - cached[1] < ACCOUNT_STATUS_TTL:
            return cached[0]

        async with semaphore:
            res = await self._api.fetch_player_data(session, name)
        if not res or not res.get("character_info"):
            return None  # No se cachea: se reintenta en la siguiente revisión

        is_free = "free" in res["character_info"].get("account_status", "").lower()
        self._account_cache[name] = (is_free, self._clock())
        return is_free

    def _forget_offline(self, online_players):
        """Limpia del caché a los que ya no están online."""
        online_names = {p.get("name") for p in online_players}
        for name in list(self._account_cache):
            if name not in online_names:
                del self._account_cache[name]
