from discord.ext import commands

from config import logger
from services.roster import build_group_progress


class ListCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name="list", description="Muestra todos los personajes monitoreados")
    async def list_players(self, ctx):
        logger.info(f"📩 Comando !list ejecutado por {ctx.author}")
        embed, chart = build_group_progress()
        if chart:
            await ctx.send(embed=embed, file=chart)
        else:
            await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(ListCommands(bot))
