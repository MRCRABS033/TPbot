from discord import app_commands
from discord.ext import commands

from config import logger
from services.roster import add_group_member


class AddCommand(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name="add", description="Agrega un personaje a la lista de monitoreo")
    @app_commands.describe(owner="Dueño del personaje", character_name="Nombre del personaje en Tibia")
    async def add_player(self, ctx, owner: str, *, character_name: str):
        await ctx.defer()
        logger.info(f"📩 Comando !add ejecutado por {ctx.author} para: '{character_name}' (Dueño: {owner})")
        await ctx.send(embed=await add_group_member(owner, character_name))


async def setup(bot):
    await bot.add_cog(AddCommand(bot))
