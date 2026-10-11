import aiohttp
import discord
from discord.ext import commands

from config import WATCHED_PLAYERS, PLAYERS_STATE, logger
from services.tibia_api import fetch_player_data, fetch_world_online_players
from services.storage import save_data

class OwnerCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="owner")
    async def list_chars_by_owner(self, ctx, *, owner_name: str):
        logger.info(f"📩 Comando !owner ejecutado por {ctx.author} para el dueño: '{owner_name}'")
        search_owner = owner_name.strip().lower()

        found_chars = []
        for data in WATCHED_PLAYERS.values():
            if data["owner"].lower() == search_owner:
                found_chars.append(data["tibia_link"])

        if not found_chars:
            await ctx.send(f"⚠️ No se encontraron personajes registrados a nombre de **{owner_name}**.")
            return

        embed = discord.Embed(
            title=f"👤 Personajes de {owner_name}",
            description="\n".join([f"• {link}" for link in found_chars]),
            color=discord.Color.purple(),
        )
        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(OwnerCommands(bot))