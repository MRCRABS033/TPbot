import discord
from discord import app_commands
from discord.ext import commands

from src.core.config import WATCHED_PLAYERS, logger

class OwnerCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name="owner", description="Muestra los personajes de un dueño")
    @app_commands.describe(owner_name="Nombre del dueño")
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
