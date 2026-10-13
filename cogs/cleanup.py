"""
Limpieza automática de los canales del bot cada 12 horas.
Borra todos los mensajes del canal menos los que el bot mantiene ahí (lista, panel, último ranking).
Los canales que no son del bot (#general, etc.) no se tocan.
"""
from datetime import time, timedelta, timezone

import discord
from discord.ext import commands, tasks

from channels import (
    CHANNEL_BOSSES,
    CHANNEL_DEATHS,
    CHANNEL_EMPLEADO,
    CHANNEL_EMPLEADO_ALT,
    CHANNEL_FREES,
    CHANNEL_HUNTEDS,
    CHANNEL_LEVEL,
    CHANNEL_LEVEL_ALT,
    CHANNEL_ONLINES,
    CHANNEL_TPBOT,
    _normalize,
    find_channel,
)
from config import logger
from services.task_utils import resilient

# Todos los días a las 12:00 a. m. y 12:00 p. m. (hora de Colombia, UTC-5)
CLEANUP_TZ = timezone(timedelta(hours=-5))
CLEANUP_TIMES = [time(hour=0, minute=0, tzinfo=CLEANUP_TZ), time(hour=12, minute=0, tzinfo=CLEANUP_TZ)]

# Roles que pueden usar !limpiar (además de los administradores del servidor). Se ignoran
# mayúsculas e íconos del nombre del rol.
CLEANUP_ROLES = {
    "desarrollador", "desarrolladores", "developer", "developers", "dev", "devs",
    "programador", "programadores", "programadora", "programadoras",
    "admin", "admins", "administrador", "administradores", "administradora", "administradoras", "administrator",
}

# No se borran los mensajes publicados en los últimos minutos (por ejemplo, el ranking
# diario, que se publica a la misma hora que la limpieza)
RECENT_GRACE = timedelta(minutes=2)

# (canales, cog que mantiene los mensajes que se conservan, atributos con el id de esos mensajes por servidor)
CHANNELS_TO_CLEAN = [
    ([CHANNEL_TPBOT], "Panel", ["group_list_message"]),
    ([CHANNEL_ONLINES], "WatchedTracker", ["watched_embed_message"]),
    ([CHANNEL_FREES], "FreeTracker", ["free_embed_message"]),
    ([CHANNEL_HUNTEDS], "HuntedTracker", ["list_message"]),
    ([CHANNEL_BOSSES], "BossTracker", ["card_message"]),
    ([CHANNEL_EMPLEADO, CHANNEL_EMPLEADO_ALT], "WatchedPlayers", ["ranking_messages", "winner_messages"]),
    ([CHANNEL_DEATHS], None, []),
    ([CHANNEL_LEVEL, CHANNEL_LEVEL_ALT], None, []),
]


class Cleanup(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.clean_channels.start()

    def cog_unload(self):
        self.clean_channels.cancel()

    @resilient

    @tasks.loop(time=CLEANUP_TIMES)
    async def clean_channels(self):
        """Limpia los canales del bot. Devuelve { nombre_del_canal: mensajes_borrados }."""
        results = {}
        for channel_names, cog_name, cache_attrs in CHANNELS_TO_CLEAN:
            cog = self.bot.get_cog(cog_name) if cog_name else None
            caches = [getattr(cog, attr, {}) for attr in cache_attrs] if cog else []

            for guild in self.bot.guilds:
                channel = next(
                    (c for c in (find_channel(guild.text_channels, n) for n in channel_names) if c), None
                )
                if not channel:
                    continue

                keep_ids = {cache.get(guild.id) for cache in caches} - {None}
                cutoff = discord.utils.utcnow() - RECENT_GRACE
                try:
                    # Si se borrara una lista o panel, el bot la vuelve a publicar en su siguiente revisión
                    deleted = await channel.purge(
                        limit=None, check=lambda m: m.id not in keep_ids and m.created_at < cutoff
                    )
                    logger.info(f"🧹 #{channel.name} limpiado en {guild.name}: {len(deleted)} mensajes borrados")
                    results[channel.name] = results.get(channel.name, 0) + len(deleted)
                except discord.HTTPException as e:
                    logger.error(f"❌ No se pudo limpiar #{channel.name}: {e}")
                    results[channel.name] = None
        return results

    @clean_channels.before_loop
    async def before_clean(self):
        await self.bot.wait_until_ready()

    # --- COMANDO MANUAL (solo administradores y desarrolladores) ---
    @staticmethod
    def _can_clean(ctx):
        if not ctx.guild:
            return False
        if ctx.author.guild_permissions.administrator:
            return True
        return any(_normalize(role.name) in CLEANUP_ROLES for role in ctx.author.roles)

    @commands.hybrid_command(name="limpiar", description="Limpia ahora todos los canales del bot (solo admins, desarrolladores y programadores)")
    async def clean_command(self, ctx):
        if not self._can_clean(ctx):
            logger.warning(f"⛔ {ctx.author} intentó usar !limpiar sin permiso")
            await ctx.send("⛔ Solo los administradores, desarrolladores y programadores pueden usar este comando.", ephemeral=True)
            return

        logger.info(f"📩 Comando !limpiar ejecutado por {ctx.author}")
        await ctx.defer(ephemeral=True)
        results = await self.clean_channels()

        lines = [
            f"#{name}: {'no se pudo limpiar (¿falta el permiso Gestionar mensajes?)' if count is None else f'{count} borrados'}"
            for name, count in results.items()
        ]
        embed = discord.Embed(
            title="Canales limpiados",
            description="\n".join(lines) if lines else "No se encontró ningún canal del bot.",
            color=discord.Color.dark_grey(),
        )
        embed.set_footer(text="Se conservan las listas, los paneles y lo publicado en los últimos 2 minutos")
        await ctx.send(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Cleanup(bot))
