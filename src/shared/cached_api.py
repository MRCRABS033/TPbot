"""
Envuelve a la API de TibiaData para no repetir consultas: si varios canales o servidores piden
lo mismo en pocos segundos (el mismo mundo, el mismo personaje), se consulta una sola vez.
Se usa igual que src/api/tibiadata.py. Las consultas fallidas no se recuerdan: se reintentan.
"""
import asyncio
import time

CACHE_SECONDS = 30  # Menos que el minuto entre revisiones: cada revisión sigue viendo datos nuevos
MAX_ENTRIES = 5000


class CachedTibiaApi:
    def __init__(self, api, ttl=CACHE_SECONDS, clock=time.monotonic):
        self._api = api
        self._ttl = ttl
        self._clock = clock
        self._cache = {}     # { clave: (hora, resultado) }
        self._in_flight = {}  # { clave: tarea en curso }

    def _forget_expired(self):
        now = self._clock()
        for key, (stored_at, _) in list(self._cache.items()):
            if now - stored_at >= self._ttl:
                del self._cache[key]

    async def _cached(self, key, fetch):
        cached = self._cache.get(key)
        if cached and self._clock() - cached[0] < self._ttl:
            return cached[1]

        task = self._in_flight.get(key)
        if task is None:
            task = asyncio.ensure_future(fetch())
            self._in_flight[key] = task
            task.add_done_callback(lambda _: self._in_flight.pop(key, None))

        result = await asyncio.shield(task)
        if result:
            if len(self._cache) >= MAX_ENTRIES:
                self._forget_expired()
            self._cache[key] = (self._clock(), result)
        return result

    async def fetch_player_data(self, session, character_name):
        return await self._cached(
            ("player", character_name.strip().lower()),
            lambda: self._api.fetch_player_data(session, character_name),
        )

    async def fetch_world_online_players(self, session, world_name):
        return await self._cached(
            ("world", world_name),
            lambda: self._api.fetch_world_online_players(session, world_name),
        )
