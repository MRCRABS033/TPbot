import aiohttp
import discord
from discord.ext import commands

from config import WATCHED_PLAYERS, PLAYERS_STATE, logger
from services.tibia_api import fetch_player_data, fetch_world_online_players
from services.storage import save_data

class ListCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot


    @commands.command(name="list")
    async def list_players(self, ctx):
        
        logger.info(f"📩 Comando !list ejecutado por {ctx.author}")
        
        if not WATCHED_PLAYERS:
            await ctx.send("📋 La lista de seguimiento está vacía.")
            return

        embed = discord.Embed(title="📋 Personajes Monitoreados", color=discord.Color.blue())

        grouped = {}
        for data in WATCHED_PLAYERS.values():
            owner = data["owner"]
            link = data["tibia_link"]
            init_lvl = data.get("initial_level", "N/A")
            curr_lvl = data.get("current_level", "N/A")
            voc = data.get("vocation", "")

            entry = f"• {link} (Nivel Ingreso: **{init_lvl}** ➔ Actual: **{curr_lvl}** - {voc})"
            grouped.setdefault(owner, []).append(entry)

        for owner, entries in grouped.items():
            chars_formatted = "\n".join(entries)
            embed.add_field(name=f"👤 Dueño: {owner}", value=chars_formatted, inline=False)

        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(ListCommands(bot))