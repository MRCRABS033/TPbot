import discord
from discord import app_commands
from discord.ext import commands

from src.core.config import logger
from src.shared.guild_context import require_world

class OwnerCommands(commands.Cog):
    def __init__(self, bot, players, guilds):
        self.bot = bot
        self.players = players
        self.guilds = guilds

    @commands.hybrid_command(name="owner", description="Muestra los personajes de un dueño")
    @app_commands.describe(owner_name="Nombre del dueño")
    async def list_chars_by_owner(self, ctx, *, owner_name: str):
        logger.info(f"📩 Comando !owner ejecutado por {ctx.author} para el dueño: '{owner_name}'")
        if not await require_world(ctx, self.guilds):
            return
        search_owner = owner_name.strip().lower()

        found_chars = []
        for data in (await self.players.for_guild(ctx.guild.id).all()).values():
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
    await bot.add_cog(OwnerCommands(bot, bot.data.players, bot.data.guilds))
