"""Ayudas para trabajar servidor por servidor: cada uno tiene sus datos y su mundo de Tibia."""
from src.core.config import logger

NOT_IN_GUILD_MESSAGE = "⚠️ Este comando solo funciona dentro de un servidor."
NOT_CONFIGURED_MESSAGE = "⚙️ Este servidor aún no eligió su mundo de Tibia. Un administrador debe usar **/config**."


async def for_each_configured_guild(bot, guilds, action):
    """
    Llama a action(guild, world) por cada servidor que ya eligió su mundo.
    Un error en un servidor (por ejemplo, permisos que le faltan al bot) no afecta a los demás.
    """
    worlds = await guilds.worlds()
    for guild in bot.guilds:
        world = worlds.get(guild.id)
        if not world:
            continue
        try:
            await action(guild, world)
        except Exception:
            logger.exception(f"❌ Error en la revisión del servidor {guild.name} ({guild.id})")


async def require_guild(ctx):
    """True si el comando se usó en un servidor; si no, avisa a quien lo usó."""
    if ctx.guild:
        return True
    await ctx.send(NOT_IN_GUILD_MESSAGE)
    return False


async def require_world(ctx, guilds):
    """Mundo del servidor donde se usó el comando; si no hay, avisa a quien lo usó y devuelve None."""
    if not await require_guild(ctx):
        return None
    world = await guilds.world(ctx.guild.id)
    if not world:
        await ctx.send(NOT_CONFIGURED_MESSAGE, ephemeral=True)
    return world
