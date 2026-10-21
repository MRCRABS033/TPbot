import asyncio
import os

import discord
from discord.ext import commands

from config import TOKEN, logger
from services.storage import load_data, load_hunteds
from services.vocation_thumbnails import load_vocation_thumbnails

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
    "cogs.creature_boosted",
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
    # Outfits de vocación ampliados para las listas de #onlines, #frees y #hunteds
    await load_vocation_thumbnails()
    # Registra los comandos de barra (/) en Discord para que aparezcan en el menú.
    # Solo cuando se pide (SYNC_SLASH_COMMANDS=1): sincronizar en cada arranque
    # gasta el rate limit de Discord y con reinicios seguidos puede bloquearlo.
    # Pon la variable en Dokploy, despliega una vez y quítala.
    if os.getenv("SYNC_SLASH_COMMANDS") == "1":
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