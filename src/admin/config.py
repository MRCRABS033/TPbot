"""
Configuración de cada servidor de Discord.

- /config mundo:  elige el mundo de Tibia del servidor (solo administración), con autocompletado.
- !config <mundo> hace lo mismo. Sin mundo muestra el actual.
- Al entrar a un servidor nuevo, el bot pide configurarlo.
"""
import discord
from discord import app_commands
from discord.ext import commands

from src.api import tibiadata
from src.api.http import create_session
from src.core.config import logger
from src.core.constants.roles import NO_PERMISSION_MESSAGE
from src.shared.guild_context import NOT_CONFIGURED_MESSAGE, require_guild
from src.shared.permissions import is_bot_admin

MAX_CHOICES = 25  # Límite de Discord para el autocompletado
MAX_SUGGESTIONS = 5
WELCOME_MESSAGE = (
    "👋 ¡Hola! Para empezar, un administrador debe elegir el mundo de Tibia de este servidor con **/config**. "
    "Hasta entonces no se revisarán personajes ni hunteds. Usa **!help** para ver todos los comandos."
)


def matching_worlds(worlds, text):
    """Mundos que contienen el texto; primero los que empiezan por él."""
    q = text.strip().lower()
    return ([w for w in worlds if w.lower().startswith(q)]
            + [w for w in worlds if q in w.lower() and not w.lower().startswith(q)])


class ConfigCommands(commands.Cog):
    def __init__(self, bot, guilds, api=tibiadata, session_factory=create_session):
        self.bot = bot
        self.guilds = guilds
        self._api = api
        self._session_factory = session_factory
        self._worlds = []

    async def _load_worlds(self):
        """Mundos de Tibia. Se recuerdan una vez consultados; si la API falla, se reintenta la próxima vez."""
        if not self._worlds:
            async with self._session_factory() as session:
                self._worlds = sorted(await self._api.fetch_worlds(session))
        return self._worlds

    @commands.hybrid_command(name="config", description="Muestra o elige el mundo de Tibia de este servidor")
    @app_commands.describe(mundo="Mundo de Tibia que seguirá este servidor (solo administración)")
    async def config_command(self, ctx, *, mundo: str = None):
        if not await require_guild(ctx):
            return
        logger.info(f"📩 Comando !config ejecutado por {ctx.author} en {ctx.guild.name} (mundo: {mundo})")
        current = await self.guilds.world(ctx.guild.id)

        if not mundo:
            text = f"🌍 Este servidor sigue el mundo **{current}**. Para cambiarlo usa **/config**." if current else NOT_CONFIGURED_MESSAGE
            await ctx.send(text, ephemeral=True)
            return
        if not is_bot_admin(ctx):
            await ctx.send(NO_PERMISSION_MESSAGE, ephemeral=True)
            return

        await ctx.defer(ephemeral=True)
        worlds = await self._load_worlds()
        if not worlds:
            await ctx.send("⚠️ No se pudo consultar la lista de mundos de Tibia. Intenta de nuevo en un momento.", ephemeral=True)
            return
        world = next((w for w in worlds if w.lower() == mundo.strip().lower()), None)
        if not world:
            similar = matching_worlds(worlds, mundo)[:MAX_SUGGESTIONS]
            hint = f" ¿Quisiste decir {', '.join(f'**{w}**' for w in similar)}?" if similar else ""
            await ctx.send(f"⚠️ No existe el mundo **{mundo}** en Tibia.{hint}", ephemeral=True)
            return

        await self.guilds.set_world(ctx.guild.id, world)
        logger.info(f"🌍 {ctx.guild.name} ({ctx.guild.id}) ahora sigue el mundo {world} (antes: {current})")
        text = f"✅ Este servidor ahora sigue el mundo **{world}**."
        if current and current != world:
            text += (f"\nLos personajes y hunteds se conservan. Los que no estén en **{world}** aparecerán "
                     f"desconectados, pero sus muertes se seguirán detectando.")
        await ctx.send(text, ephemeral=True)

    @config_command.autocomplete("mundo")
    async def world_autocomplete(self, interaction: discord.Interaction, current: str):
        worlds = await self._load_worlds()
        return [app_commands.Choice(name=w, value=w) for w in matching_worlds(worlds, current)[:MAX_CHOICES]]

    @commands.Cog.listener()
    async def on_guild_join(self, guild):
        logger.info(f"➕ El bot entró al servidor {guild.name} ({guild.id})")
        if await self.guilds.world(guild.id):
            return  # Ya estuvo antes: conserva su configuración y sus datos
        channel = guild.system_channel
        if not channel or not channel.permissions_for(guild.me).send_messages:
            logger.warning(f"⚠️ No se pudo dar la bienvenida en {guild.name}: no hay canal de sistema con permiso")
            return
        try:
            await channel.send(WELCOME_MESSAGE)
        except discord.HTTPException as e:
            logger.warning(f"⚠️ No se pudo dar la bienvenida en {guild.name}: {e}")


async def setup(bot):
    await bot.add_cog(ConfigCommands(bot, bot.data.guilds))
