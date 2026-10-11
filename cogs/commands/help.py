
import discord
from discord.ext import commands

from config import logger


class HelpCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="help")
    async def help_command(self, ctx):
        logger.info(f"📩 Comando !help ejecutado por {ctx.author}")
        embed = discord.Embed(
            title="📖 Comandos Disponibles",
            description="Lista de comandos que puedes usar con este bot:",
            color=discord.Color.blue(),
        )
        embed.add_field(name="!add <dueño> <personaje>", value="Agrega un personaje a la lista de monitoreo.", inline=False)
        embed.add_field(name="!remove <personaje>", value="Elimina un personaje de la lista de monitoreo.", inline=False)
        embed.add_field(name="!list", value="Muestra todos los personajes actualmente monitoreados.", inline=False)
        embed.add_field(name="!empleado", value="Muestra el ranking semanal de niveles ganados.", inline=False)
        embed.add_field(name="!free [mundo]", value="Muestra jugadores Free Account online en el mundo especificado.", inline=False)
        embed.add_field(name="!online", value="Muestra qué personajes de la lista están actualmente en línea.", inline=False)
        embed.add_field(name="!owner <dueño>", value="Muestra todos los personajes registrados a nombre del dueño especificado.", inline=False)
        embed.add_field(name="!whois <personaje>", value="Muestra el dueño e información detallada de nivel.", inline=False)
        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(HelpCommands(bot))