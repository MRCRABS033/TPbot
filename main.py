import asyncio
import discord
from discord.ext import commands

from config import TOKEN, logger
from services.storage import load_data

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

INITIAL_EXTENSIONS = [
    "cogs.death_tracker",
    "cogs.free_tracker",
    "cogs.watched_players",
    "cogs.watched_tracker",
]

async def main():
    # 1. Cargar personajes guardados desde el JSON
    load_data()

    async with bot:
        for extension in INITIAL_EXTENSIONS:
            await bot.load_extension(extension)
            logger.info(f"Extension cargada: {extension}")
            
        await bot.start(TOKEN)

if __name__ == "__main__":
    asyncio.run(main())