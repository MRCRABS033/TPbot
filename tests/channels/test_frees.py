"""Pruebas de #frees sin internet ni Discord. Uso: python -m unittest discover -s tests -t ."""
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from src.channels.frees.service import ACCOUNT_STATUS_TTL, FreeService


class FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeApi:
    """Reemplaza a src.api.tibiadata. `accounts` = { nombre: "Free Account" | "Premium Account" | None (falla) }."""

    def __init__(self, accounts):
        self.accounts = accounts
        self.online = [{"name": name, "level": 100, "vocation": "Knight"} for name in accounts]
        self.player_queries = []

    async def fetch_world_online_players(self, session, world):
        self.world = world
        return self.online

    async def fetch_player_data(self, session, name):
        self.player_queries.append(name)
        status = self.accounts[name]
        return {"character_info": {"account_status": status}} if status else None


def make_service(api, clock=lambda: 0):
    return FreeService(api, session_factory=FakeSession, clock=clock)


def names(players):
    return [p["name"] for p in players]


class FreeServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_devuelve_solo_los_free_del_mundo_pedido(self):
        api = FakeApi({"Ana": "Free Account", "Beto": "Premium Account"})
        self.assertEqual(names(await make_service(api).get_free_players("Quidera")), ["Ana"])
        self.assertEqual(api.world, "Quidera")

    async def test_no_repite_consultas_mientras_dura_el_cache(self):
        api = FakeApi({"Ana": "Free Account", "Beto": "Premium Account"})
        service = make_service(api)
        await service.get_free_players("Quidera")
        second = await service.get_free_players("Quidera")
        self.assertEqual(names(second), ["Ana"])
        self.assertEqual(sorted(api.player_queries), ["Ana", "Beto"])

    async def test_vuelve_a_consultar_cuando_vence_el_cache(self):
        now = [0]
        api = FakeApi({"Ana": "Free Account"})
        service = make_service(api, clock=lambda: now[0])
        await service.get_free_players("Quidera")
        now[0] = ACCOUNT_STATUS_TTL
        await service.get_free_players("Quidera")
        self.assertEqual(api.player_queries, ["Ana", "Ana"])

    async def test_una_consulta_fallida_no_se_cachea_y_no_cuenta_como_free(self):
        api = FakeApi({"Ana": None})
        service = make_service(api)
        self.assertEqual(await service.get_free_players("Quidera"), [])
        api.accounts["Ana"] = "Free Account"
        self.assertEqual(names(await service.get_free_players("Quidera")), ["Ana"])

    async def test_olvida_a_quien_se_desconecta(self):
        api = FakeApi({"Ana": "Free Account"})
        service = make_service(api)
        await service.get_free_players("Quidera")
        online = api.online
        api.online = []
        await service.get_free_players("Quidera")
        api.online = online
        await service.get_free_players("Quidera")
        self.assertEqual(api.player_queries, ["Ana", "Ana"])


class FreeTrackerPageTests(unittest.IsolatedAsyncioTestCase):
    async def test_el_loop_publica_en_frees_lo_que_devuelve_el_service(self):
        os.environ.setdefault("DISCORD_TOKEN", "test")  # config.py lo exige al importarse
        from src.channels.frees import page

        service = make_service(FakeApi({"Ana": "Free Account", "Beto": "Premium Account"}))
        # Sin __init__: así no arranca la tarea automática real
        cog = page.FreeTracker.__new__(page.FreeTracker)
        cog.bot, cog.service, cog.free_embed_message, cog.last_list_embed = MagicMock(), service, {}, None

        with patch.object(page, "send_or_update_embed", new=AsyncMock()) as send:
            await page.FreeTracker.update_free_players.coro(cog)

        self.assertEqual(send.await_args.args[2], page.CHANNEL_FREES)
        text = str([embed.to_dict() for embed in cog.last_list_embed])
        self.assertIn("Ana", text)
        self.assertNotIn("Beto", text)


if __name__ == "__main__":
    unittest.main()
