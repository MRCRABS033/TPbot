import discord
from discord.ext import commands

from config import logger

# (comando, descripción) por sección. Mantener actualizado al agregar comandos nuevos.
COMMAND_SECTIONS = {
    "Panel": [
        ("!panel", "Vuelve a poner el panel del grupo al final de #tpbot. Los canales del bot se limpian solos cada 12 horas."),
    ],
    "Personajes": [
        ("!add <dueño> <personaje>", "Agrega un personaje a la lista de monitoreo."),
        ("!remove <personaje>", "Elimina un personaje de la lista de monitoreo."),
        ("!list", "Muestra todos los personajes monitoreados."),
        ("!whois <personaje>", "Muestra el dueño y el progreso de un personaje."),
        ("!owner <dueño>", "Muestra los personajes de un dueño."),
    ],
    "Seguimiento": [
        ("!online", "Muestra qué personajes monitoreados están conectados."),
        ("!nivel [personaje]", "Sin nombre revisa los niveles ahora. Con nombre muestra su ficha de nivel."),
        ("!empleado", "Muestra el ranking semanal del empleado del mes."),
        ("!muertes", "Muestra las muertes de hoy de los personajes del grupo."),
        ("!free", "Muestra la lista de jugadores free online."),
    ],
    "Bosses": [
        ("!boss", "Muestra el boss boosted de hoy, con el botón para organizarlo en grupo."),
    ],
    "Administración": [
        ("!limpiar", "Limpia ahora todos los canales del bot. Solo administradores, desarrolladores y programadores."),
    ],
    "Hunteds": [
        ("!hunted", "Muestra la lista de hunteds, separados en conectados y desconectados."),
        ("!hunted add <personaje>", "Agrega un personaje a la lista de hunteds."),
        ("!hunted remove <personaje>", "Elimina un personaje de la lista de hunteds."),
    ],
}


def build_help_embed():
    embed = discord.Embed(
        title="Comandos disponibles",
        description="Usa `!comando`, o escribe `/` para ver los comandos con autocompletado.",
        color=discord.Color.blurple(),
    )
    for section, commands_list in COMMAND_SECTIONS.items():
        value = "\n\n".join(f"`{cmd}`\n{desc}" for cmd, desc in commands_list)
        embed.add_field(name=section, value=value, inline=False)
    embed.set_footer(text="[ ] = opcional  ·  < > = obligatorio")
    return embed


class HelpCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name="help", aliases=["ayuda", "comandos"], description="Muestra la lista de comandos")
    async def help_command(self, ctx):
        logger.info(f"📩 Comando !help ejecutado por {ctx.author}")
        await ctx.send(embed=build_help_embed())

    @commands.Cog.listener()
    async def on_message(self, message):
        # Si alguien escribe solo "!", se muestra la lista de comandos
        if message.author.bot or message.content.strip() != "!":
            return
        logger.info(f"📩 Lista de comandos pedida con '!' por {message.author}")
        await message.channel.send(embed=build_help_embed())


async def setup(bot):
    await bot.add_cog(HelpCommands(bot))
