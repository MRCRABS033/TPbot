import asyncio
import discord
from discord.ext import commands

from src.core.config import TOKEN, logger
from services.storage import load_data, load_hunteds
from src.shared.vocation_thumbnails import load_vocation_thumbnails

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

INITIAL_EXTENSIONS = [
    "src.channels.cementerio.page",
    "src.channels.frees.page",
    "src.channels.empleado.page",
    "src.channels.onlines.page",
    "src.channels.hunteds.page",
    "src.channels.bosses.page",
    "src.channels.bazaar.page",
    "src.channels.tpbot.page",
    "cogs.cleanup",
    "cogs.backup",
    "cogs.commands.owner",
    "cogs.commands.help",
]


async def setup_hook():
    # Outfits de vocación ampliados para las listas de #onlines, #frees y #hunteds
    await load_vocation_thumbnails()
    # Registra los comandos de barra (/) en Discord para que aparezcan en el menú
    synced = await bot.tree.sync()
    logger.info(f"Comandos de barra sincronizados: {len(synced)}")

bot.setup_hook = setup_hook

async def main():
    # 1. Cargar personajes guardados desde el JSON
    load_data()
    load_hunteds()

    async with bot:
        for extension in INITIAL_EXTENSIONS:
            await bot.load_extension(extension)
            logger.info(f"Extension cargada: {extension}")
            
        await bot.start(TOKEN)

if __name__ == "__main__":
    asyncio.run(main())