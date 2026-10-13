from discord import app_commands
from discord.ext import commands

from config import logger
from services.roster import build_group_member_embed


class WhoisCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name="whois", description="Muestra el dueño y el progreso de un personaje")
    @app_commands.describe(character_name="Nombre del personaje")
    async def whois_char(self, ctx, *, character_name: str):
        logger.info(f"📩 Comando !whois ejecutado por {ctx.author} para el personaje: '{character_name}'")
        await ctx.send(embed=build_group_member_embed(character_name))


async def setup(bot):
    await bot.add_cog(WhoisCommands(bot))
