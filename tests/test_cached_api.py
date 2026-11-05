"""Pruebas de la caché de consultas a TibiaData."""
import asyncio
import unittest
from unittest.mock import AsyncMock

from src.shared.cached_api import CACHE_SECONDS, CachedTibiaApi


class FakeApi:
    def __init__(self):
        self.fetch_player_data = AsyncMock(side_effect=lambda session, name: {"name": name})
        self.fetch_world_online_players = AsyncMock(side_effect=lambda session, world: [{"name": f"en {world}"}])


class CachedApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = [0]
        self.api = FakeApi()
        self.cached = CachedTibiaApi(self.api, clock=lambda: self.now[0])

    async def test_el_mismo_mundo_se_consulta_una_vez(self):
        first = await self.cached.fetch_world_online_players("sesion", "Quidera")
        second = await self.cached.fetch_world_online_players("otra", "Quidera")
        self.assertEqual(first, second)
        self.api.fetch_world_online_players.assert_awaited_once()

    async def test_mundos_distintos_no_se_mezclan(self):
        self.assertEqual((await self.cached.fetch_world_online_players("s", "Quidera"))[0]["name"], "en Quidera")
        self.assertEqual((await self.cached.fetch_world_online_players("s", "Antica"))[0]["name"], "en Antica")

    async def test_vence_antes_de_la_siguiente_revision(self):
        self.assertLess(CACHE_SECONDS, 60)
        await self.cached.fetch_player_data("s", "Ana")
        self.now[0] = CACHE_SECONDS
        await self.cached.fetch_player_data("s", "Ana")
        self.assertEqual(self.api.fetch_player_data.await_count, 2)

    async def test_el_personaje_no_distingue_mayusculas(self):
        await self.cached.fetch_player_data("s", "Ana")
        await self.cached.fetch_player_data("s", " ANA ")
        self.api.fetch_player_data.assert_awaited_once()

    async def test_las_consultas_fallidas_no_se_recuerdan(self):
        self.api.fetch_player_data = AsyncMock(side_effect=[None, {"name": "Ana"}])
        self.assertIsNone(await self.cached.fetch_player_data("s", "Ana"))
        self.assertEqual(await self.cached.fetch_player_data("s", "Ana"), {"name": "Ana"})

    async def test_una_lista_vacia_de_un_mundo_no_se_recuerda(self):
        self.api.fetch_world_online_players = AsyncMock(side_effect=[[], [{"name": "Ana"}]])
        self.assertEqual(await self.cached.fetch_world_online_players("s", "Quidera"), [])
        self.assertEqual(len(await self.cached.fetch_world_online_players("s", "Quidera")), 1)

    async def test_pedidos_simultaneos_comparten_la_consulta(self):
        release = asyncio.Event()

        async def slow(session, name):
            await release.wait()
            return {"name": name}

        self.api.fetch_player_data = AsyncMock(side_effect=slow)
        tasks = [asyncio.create_task(self.cached.fetch_player_data("s", "Ana")) for _ in range(5)]
        await asyncio.sleep(0)
        release.set()
        results = await asyncio.gather(*tasks)
        self.assertEqual(results, [{"name": "Ana"}] * 5)
        self.api.fetch_player_data.assert_awaited_once()

    async def test_un_error_llega_a_quien_pidio_y_no_queda_guardado(self):
        self.api.fetch_player_data = AsyncMock(side_effect=[RuntimeError("caído"), {"name": "Ana"}])
        with self.assertRaises(RuntimeError):
            await self.cached.fetch_player_data("s", "Ana")
        self.assertEqual(await self.cached.fetch_player_data("s", "Ana"), {"name": "Ana"})


class ServersShareQueriesTests(unittest.IsolatedAsyncioTestCase):
    async def test_dos_servidores_del_mismo_mundo_consultan_una_vez(self):
        from src.channels.frees.service import FreeService
        from tests.fakes import FakeSession

        api = FakeApi()
        api.fetch_player_data = AsyncMock(return_value={"character_info": {"account_status": "Free Account"}})
        service = FreeService(CachedTibiaApi(api), session_factory=FakeSession)
        await service.get_free_players("Quidera")
        await service.get_free_players("Quidera")
        api.fetch_world_online_players.assert_awaited_once()
        api.fetch_player_data.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
