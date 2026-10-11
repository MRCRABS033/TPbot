import discord
from discord.ext import commands

from config import WATCHED_PLAYERS, PLAYERS_STATE, logger
from services.tibia_api import fetch_player_data, fetch_world_online_players
from services.storage import save_data

class EmpleadoCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.watched_players = WATCHED_PLAYERS

    @commands.command(name="empleado", aliases=["top", "semana"])
    async def show_weekly_top(self, ctx):
        """Muestra el ranking actual de niveles ganados en la semana."""
        logger.info(f"📩 Comando !empleado ejecutado por {ctx.author}")
        if not WATCHED_PLAYERS:
            await ctx.send("📋 La lista de seguimiento está vacía.")
            return

        ranking = []
        for key, data in WATCHED_PLAYERS.items():
            curr = data.get("current_level", 0)
            start = data.get("weekly_start_level", curr)
            gained = max(0, curr - start)
            ranking.append((data["char_name"], data["owner"], gained, curr, start))

        ranking.sort(key=lambda x: x[2], reverse=True)

        embed = discord.Embed(
            title="🏆 Ranking Semanal - Empleado del Mes",
            description="Progreso de niveles acumulados durante la semana actual:",
            color=discord.Color.gold(),
        )

        medals = ["🥇", "🥈", "🥉"]
        lines = []

        for idx, (char, owner, gained, curr, start) in enumerate(ranking, start=1):
            icon = medals[idx - 1] if idx <= 3 else f"`#{idx}`"
            lines.append(
                f"{icon} **{char}** ({owner}) ➔ **+{gained} lvl** (Inicio: {start} ➔ Actual: {curr})"
            )

        embed.description = "\n".join(lines)
        embed.set_footer(text="El conteo se reinicia cada domingo en el Server Save.")
        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(EmpleadoCommands(bot))