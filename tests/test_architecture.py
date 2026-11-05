"""Reglas de la estructura del proyecto y carga completa del bot (sin conectarse a Discord)."""
import ast
import os
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
CHANNELS = ROOT / "src" / "channels"


def imported_modules(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module
            yield from (f"{node.module}.{alias.name}" for alias in node.names)


def channel_files():
    return [p for p in CHANNELS.rglob("*.py") if p.name != "__init__.py"]


class ArchitectureTests(unittest.TestCase):
    def test_un_canal_no_importa_a_otro(self):
        for path in channel_files():
            own = path.relative_to(CHANNELS).parts[0]
            for module in imported_modules(path):
                if module.startswith("src.channels."):
                    self.assertEqual(module.split(".")[2], own, f"{path.name} de #{own} importa {module}")

    def test_los_services_no_dependen_de_discord_ni_de_config(self):
        services = list(CHANNELS.glob("*/service.py"))
        self.assertTrue(services)
        for path in services:
            for module in imported_modules(path):
                self.assertFalse(module == "discord" or module.startswith("discord."), f"{path} importa {module}")
                self.assertFalse(module.startswith("src.core.config"), f"{path} importa {module}")

    def test_la_base_no_depende_de_canales(self):
        for folder in ("core", "shared", "api"):
            for path in (ROOT / "src" / folder).rglob("*.py"):
                for module in imported_modules(path):
                    self.assertFalse(module.startswith("src.channels"), f"{path} importa {module}")
                    self.assertFalse(module.startswith("src.admin"), f"{path} importa {module}")


class BotLoadTests(unittest.IsolatedAsyncioTestCase):
    async def test_carga_todo_y_cleanup_encuentra_lo_que_debe_conservar(self):
        os.environ.setdefault("DISCORD_TOKEN", "test")  # config.py lo exige al importarse
        from discord.ext import commands

        import main
        from src.admin.cleanup import CHANNELS_TO_CLEAN

        bot = commands.Bot(command_prefix="!", intents=main.intents, help_command=None)
        async with bot:
            for extension in main.INITIAL_EXTENSIONS:
                await bot.load_extension(extension)

            for channels, cog_name, attributes in CHANNELS_TO_CLEAN:
                if cog_name is None:
                    continue
                cog = bot.get_cog(cog_name)
                self.assertIsNotNone(cog, cog_name)
                for attribute in attributes:
                    self.assertTrue(hasattr(cog, attribute), f"{cog_name}.{attribute}")

            online = bot.get_cog("WatchedTracker")
            self.assertTrue(hasattr(online, "watched_embed_message"))
            self.assertEqual(len(bot.persistent_views), 5)  # Panel, hunteds, bosses, criatura boosted y bazaar


if __name__ == "__main__":
    unittest.main()
