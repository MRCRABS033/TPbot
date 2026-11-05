"""Pruebas de /config y de las ayudas por servidor, sin internet ni Discord."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from tests.fakes import GUILD_ID, FakeGuildRepository, FakeSession


def make_ctx(guild_id=GUILD_ID):
    guild = MagicMock(id=guild_id) if guild_id else None
    return MagicMock(guild=guild, send=AsyncMock(), defer=AsyncMock())


class ConfigTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        os.environ.setdefault("DISCORD_TOKEN", "test")  # config.py lo exige al importarse
        from src.admin import config
        self.config = config
        self.api = SimpleNamespace(fetch_worlds=AsyncMock(return_value=["Quidera", "Antica", "Secura", "Vunira"]))

    def make_cog(self, worlds=None):
        guilds = FakeGuildRepository(worlds if worlds is not None else {})
        return self.config.ConfigCommands(MagicMock(), guilds, api=self.api, session_factory=FakeSession), guilds

    async def run_command(self, cog, ctx, mundo=None, admin=True):
        with patch.object(self.config, "is_bot_admin", return_value=admin):
            await self.config.ConfigCommands.config_command.callback(cog, ctx, mundo=mundo)
        return ctx.send.await_args.args[0]

    async def test_sin_mundo_muestra_el_actual_o_pide_configurar(self):
        cog, _ = self.make_cog({GUILD_ID: "Quidera"})
        self.assertIn("**Quidera**", await self.run_command(cog, make_ctx(), admin=False))
        cog, _ = self.make_cog()
        self.assertIn("/config", await self.run_command(cog, make_ctx(), admin=False))

    async def test_solo_administracion_puede_cambiarlo(self):
        cog, guilds = self.make_cog()
        text = await self.run_command(cog, make_ctx(), "Quidera", admin=False)
        self.assertEqual(text, self.config.NO_PERMISSION_MESSAGE)
        self.assertEqual(await guilds.worlds(), {})

    async def test_guarda_el_nombre_oficial_del_mundo(self):
        cog, guilds = self.make_cog()
        text = await self.run_command(cog, make_ctx(), "  qUiDeRa ")
        self.assertEqual(await guilds.world(GUILD_ID), "Quidera")
        self.assertNotIn("se conservan", text)

    async def test_cambiar_de_mundo_avisa_que_los_datos_se_conservan(self):
        cog, guilds = self.make_cog({GUILD_ID: "Antica"})
        text = await self.run_command(cog, make_ctx(), "Quidera")
        self.assertEqual(await guilds.world(GUILD_ID), "Quidera")
        self.assertIn("se conservan", text)

    async def test_mundo_inexistente_no_cambia_nada_y_sugiere(self):
        cog, guilds = self.make_cog({GUILD_ID: "Antica"})
        text = await self.run_command(cog, make_ctx(), "qui")
        self.assertIn("**Quidera**", text)
        self.assertEqual(await guilds.world(GUILD_ID), "Antica")

    async def test_si_la_api_falla_no_guarda_y_reintenta_despues(self):
        cog, guilds = self.make_cog()
        self.api.fetch_worlds.return_value = []
        self.assertIn("No se pudo consultar", await self.run_command(cog, make_ctx(), "Quidera"))
        self.assertEqual(await guilds.worlds(), {})
        self.api.fetch_worlds.return_value = ["Quidera"]
        await self.run_command(cog, make_ctx(), "Quidera")
        self.assertEqual(await guilds.world(GUILD_ID), "Quidera")

    async def test_cada_servidor_tiene_su_mundo(self):
        cog, guilds = self.make_cog()
        await self.run_command(cog, make_ctx(1), "Quidera")
        await self.run_command(cog, make_ctx(2), "Antica")
        self.assertEqual(await guilds.worlds(), {1: "Quidera", 2: "Antica"})

    async def test_fuera_de_un_servidor(self):
        cog, guilds = self.make_cog()
        text = await self.run_command(cog, make_ctx(None), "Quidera")
        self.assertIn("dentro de un servidor", text)
        self.assertEqual(await guilds.worlds(), {})

    async def test_autocompletar(self):
        cog, _ = self.make_cog()
        choices = await cog.world_autocomplete(MagicMock(), "ra")
        self.assertEqual([c.value for c in choices], ["Quidera", "Secura", "Vunira"])

    async def test_bienvenida_en_servidor_nuevo(self):
        cog, _ = self.make_cog()
        guild = MagicMock(id=9, system_channel=MagicMock(send=AsyncMock()))
        await cog.on_guild_join(guild)
        self.assertIn("/config", guild.system_channel.send.await_args.args[0])

    async def test_sin_bienvenida_si_ya_estaba_configurado_o_no_hay_canal(self):
        cog, _ = self.make_cog({9: "Quidera"})
        guild = MagicMock(id=9, system_channel=MagicMock(send=AsyncMock()))
        await cog.on_guild_join(guild)
        guild.system_channel.send.assert_not_awaited()

        cog, _ = self.make_cog()
        await cog.on_guild_join(MagicMock(id=10, system_channel=None))  # No falla


class GuildContextTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        os.environ.setdefault("DISCORD_TOKEN", "test")  # config.py lo exige al importarse
        from src.shared import guild_context
        self.context = guild_context

    async def test_solo_recorre_los_configurados_con_su_mundo(self):
        guilds = FakeGuildRepository({1: "Quidera", 2: "Antica", 99: "Secura"})  # El bot ya no está en el 99
        bot = MagicMock(guilds=[MagicMock(id=1), MagicMock(id=2), MagicMock(id=3)])
        action = AsyncMock()
        await self.context.for_each_configured_guild(bot, guilds, action)
        self.assertEqual([(c.args[0].id, c.args[1]) for c in action.await_args_list], [(1, "Quidera"), (2, "Antica")])

    async def test_el_error_de_un_servidor_no_afecta_a_los_demas(self):
        guilds = FakeGuildRepository({1: "Quidera", 2: "Antica"})
        bot = MagicMock(guilds=[MagicMock(id=1), MagicMock(id=2)])
        action = AsyncMock(side_effect=[RuntimeError("sin permisos"), None])
        await self.context.for_each_configured_guild(bot, guilds, action)
        self.assertEqual(action.await_count, 2)

    async def test_require_world(self):
        guilds = FakeGuildRepository({1: "Quidera"})
        self.assertEqual(await self.context.require_world(make_ctx(1), guilds), "Quidera")
        ctx = make_ctx(2)
        self.assertIsNone(await self.context.require_world(ctx, guilds))
        self.assertEqual(ctx.send.await_args.args[0], self.context.NOT_CONFIGURED_MESSAGE)
        ctx = make_ctx(None)
        self.assertIsNone(await self.context.require_world(ctx, guilds))
        self.assertEqual(ctx.send.await_args.args[0], self.context.NOT_IN_GUILD_MESSAGE)


if __name__ == "__main__":
    unittest.main()
