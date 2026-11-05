"""Pruebas de administración: cada comando solo toca los datos y canales del servidor donde se usa."""
import json
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from tests.fakes import GUILD_ID, FakeCharacterRepository, FakeGuildRepository

OTHER_GUILD = 2


def member(name="Ana", owner="Vale"):
    return {"char_name": name, "owner": owner, "tibia_link": f"[{name}](http://x)"}


def attachment(data, filename="respaldo.json"):
    content = json.dumps(data).encode() if not isinstance(data, bytes) else data
    return MagicMock(size=len(content), filename=filename, read=AsyncMock(return_value=content))


def ctx_for(guild_id=GUILD_ID, attachments=()):
    return MagicMock(
        guild=MagicMock(id=guild_id), send=AsyncMock(), defer=AsyncMock(), message=MagicMock(attachments=list(attachments)),
    )


def file_json(file):
    file.fp.seek(0)
    return json.loads(file.fp.read())


class BackupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        os.environ.setdefault("DISCORD_TOKEN", "test")  # config.py lo exige al importarse
        from src.admin import backup
        self.backup = backup
        self.players = {"ana": member()}
        self.other_players = {"zoe": member("Zoe", "Luis")}
        self.hunteds = {"beto": {"char_name": "Beto", "level": 100}}
        self.cog = backup.Backup(
            MagicMock(),
            FakeCharacterRepository(self.players, {OTHER_GUILD: self.other_players}),
            FakeCharacterRepository(self.hunteds),
        )

    async def run_restore(self, ctx):
        with patch.object(self.backup, "is_bot_admin", return_value=True):
            await self.backup.Backup.restore_command.callback(self.cog, ctx)
        return ctx.send.await_args.kwargs

    async def test_respaldo_solo_incluye_este_servidor(self):
        ctx = ctx_for()
        with patch.object(self.backup, "is_bot_admin", return_value=True):
            await self.backup.Backup.backup_command.callback(self.cog, ctx)
        files = {f.filename: file_json(f) for f in ctx.send.await_args.kwargs["files"]}
        self.assertEqual(files["watched_players.json"], self.players)
        self.assertEqual(files["hunteds.json"], self.hunteds)
        self.assertNotIn("zoe", files["watched_players.json"])

    async def test_sin_permiso_no_hace_nada(self):
        ctx = ctx_for(attachments=[attachment({"x": member("X")})])
        with patch.object(self.backup, "is_bot_admin", return_value=False):
            await self.backup.Backup.restore_command.callback(self.cog, ctx)
            await self.backup.Backup.backup_command.callback(self.cog, ctx)
        self.assertNotIn("x", self.players)
        self.assertNotIn("files", ctx.send.await_args.kwargs)

    async def test_restaurar_reemplaza_y_adjunta_lo_anterior(self):
        kwargs = await self.run_restore(ctx_for(attachments=[attachment({"caro": member("Caro")})]))
        self.assertEqual(list(self.players), ["caro"])
        self.assertEqual(kwargs["files"][0].filename, "anterior_watched_players.json")
        self.assertEqual(file_json(kwargs["files"][0]), {"ana": member()})
        self.assertIn("1 personajes del grupo", kwargs["embed"].description)

    async def test_restaurar_no_toca_otros_servidores(self):
        await self.run_restore(ctx_for(attachments=[attachment({"caro": member("Caro")})]))
        self.assertEqual(list(self.other_players), ["zoe"])

    async def test_reconoce_hunteds_por_su_contenido(self):
        await self.run_restore(ctx_for(attachments=[attachment({"dani": {"char_name": "Dani"}})]))
        self.assertEqual(list(self.hunteds), ["dani"])
        self.assertEqual(list(self.players), ["ana"])

    async def test_archivos_invalidos_no_cambian_nada(self):
        kwargs = await self.run_restore(ctx_for(attachments=[attachment(b"no es json"), attachment({"x": "y"})]))
        self.assertEqual(list(self.players), ["ana"])
        self.assertEqual(kwargs["files"], [])
        self.assertIn("no es un JSON válido", kwargs["embed"].description)
        self.assertIn("no parece un respaldo", kwargs["embed"].description)


class OwnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_solo_busca_en_este_servidor(self):
        os.environ.setdefault("DISCORD_TOKEN", "test")  # config.py lo exige al importarse
        from src.admin import owner

        repository = FakeCharacterRepository({"ana": member()}, {OTHER_GUILD: {"zoe": member("Zoe", "Vale")}})
        cog = owner.OwnerCommands(MagicMock(), repository, FakeGuildRepository())
        ctx = ctx_for()
        await owner.OwnerCommands.list_chars_by_owner.callback(cog, ctx, owner_name=" vale ")
        text = ctx.send.await_args.kwargs["embed"].description
        self.assertIn("Ana", text)
        self.assertNotIn("Zoe", text)


class CleanupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        os.environ.setdefault("DISCORD_TOKEN", "test")  # config.py lo exige al importarse
        from src.admin import cleanup
        self.cleanup = cleanup
        self.channels = {guild_id: MagicMock(purge=AsyncMock(return_value=[])) for guild_id in (GUILD_ID, OTHER_GUILD, 3)}
        guilds = [MagicMock(id=guild_id, text_channels=channel) for guild_id, channel in self.channels.items()]
        self.cog = cleanup.Cleanup.__new__(cleanup.Cleanup)  # Sin __init__: no arranca la tarea
        self.cog.bot = MagicMock(guilds=guilds, get_cog=MagicMock(return_value=None))
        self.cog.guilds = FakeGuildRepository({GUILD_ID: "Quidera", OTHER_GUILD: "Antica"})

    def find(self, channels, name):
        return channels

    async def test_limpiar_solo_toca_el_servidor_donde_se_pidio(self):
        ctx = ctx_for(OTHER_GUILD)
        ctx.guild = self.cog.bot.guilds[1]
        with patch.object(self.cleanup, "is_bot_admin", return_value=True), \
                patch.object(self.cleanup, "find_channel", side_effect=self.find):
            await self.cleanup.Cleanup.clean_command.callback(self.cog, ctx)
        self.channels[OTHER_GUILD].purge.assert_awaited()
        self.channels[GUILD_ID].purge.assert_not_awaited()
        self.channels[3].purge.assert_not_awaited()

    async def test_la_limpieza_automatica_no_toca_servidores_sin_configurar(self):
        with patch.object(self.cleanup, "find_channel", side_effect=self.find):
            await self.cleanup.Cleanup.clean_channels.coro(self.cog)
        self.channels[GUILD_ID].purge.assert_awaited()
        self.channels[OTHER_GUILD].purge.assert_awaited()
        self.channels[3].purge.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
