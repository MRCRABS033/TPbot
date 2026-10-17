import asyncio
import discord
from discord.ext import commands

from config import TOKEN, logger
from services.storage import load_data, load_hunteds

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

INITIAL_EXTENSIONS = [
    "cogs.death_tracker",
    "cogs.free_tracker",
    "cogs.watched_players",
    "cogs.watched_tracker",
    "cogs.hunted_tracker",
    "cogs.bosses",
    "cogs.boosted_creature",
    "cogs.bazaar",
    "cogs.panel",
    "cogs.cleanup",
    "cogs.backup",
    "cogs.commands.add",
    "cogs.commands.remove",
    "cogs.commands.list_players",
    "cogs.commands.whois",
    "cogs.commands.owner",
    "cogs.commands.online",
    "cogs.commands.help",

]


async def setup_hook():
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