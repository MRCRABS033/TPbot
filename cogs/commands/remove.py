from discord import app_commands
from discord.ext import commands

from config import logger
from services.roster import remove_group_member


class RemoveCommand(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name="remove", description="Elimina un personaje de la lista de monitoreo")
    @app_commands.describe(character_name="Nombre del personaje")
    async def remove_player(self, ctx, *, character_name: str):
        logger.info(f"📩 Comando !remove ejecutado por {ctx.author} para: '{character_name}'")
        await ctx.send(embed=remove_group_member(character_name))


async def setup(bot):
    await bot.add_cog(RemoveCommand(bot))
