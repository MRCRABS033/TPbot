import asyncio
import time
import aiohttp
import discord
from discord.ext import commands, tasks

from channels import CHANNEL_FREES, find_channel
from config import logger, WORLD
from services.task_utils import resilient
from services.discord_utils import move_list_to_bottom, send_or_update_embed
from services.online_embeds import build_grouped_online_embeds, player_url
from services.vocation_thumbnails import thumbnail_files
from services.tibia_api import fetch_player_data, fetch_world_online_players

# Cuánto tiempo se recuerda si una cuenta es free (evita consultar a todos los jugadores cada minuto)
ACCOUNT_STATUS_TTL = 60 * 60  # 1 hora


class FreeTracker(commands.Cog):
    """
    #frees: una sola lista de jugadores free online, agrupada por vocación.
    Se edita cada minuto (sin un mensaje por cada conexión).
    """

    def __init__(self, bot):
        self.bot = bot
        self.free_embed_message = {}
        self.account_cache = {}       # { nombre: (es_free, hora_de_consulta) }
        self.last_list_embed = None   # Última lista generada (la usa !free)
        self.update_free_players.start()

    def cog_unload(self):
        self.update_free_players.cancel()

    async def _is_free(self, session, semaphore, name):
        """True/False si es free, o None si no se pudo consultar."""
        cached = self.account_cache.get(name)
        if cached and time.monotonic() - cached[1] < ACCOUNT_STATUS_TTL:
            return cached[0]

        async with semaphore:
            res = await fetch_player_data(session, name)
        if not res or not res.get("character_info"):
            return None  # No se cachea: se reintenta en la siguiente revisión

        is_free = "free" in res["character_info"].get("account_status", "").lower()
        self.account_cache[name] = (is_free, time.monotonic())
        return is_free

    @resilient
    @tasks.loop(seconds=60)
    async def update_free_players(self):
        world = WORLD
        logger.info(f"🔄 Consultando Free Accounts online en {world}...")
        semaphore = asyncio.Semaphore(8)

        async with aiohttp.ClientSession() as session:
            online_players = await fetch_world_online_players(session, world)
            statuses = await asyncio.gather(
                *(self._is_free(session, semaphore, p.get("name")) for p in online_players)
            )

        free_players = [p for p, is_free in zip(online_players, statuses) if is_free]

        # Limpia del caché a los que ya no están online
        online_names = {p.get("name") for p in online_players}
        for name in list(self.account_cache):
            if name not in online_names:
                del self.account_cache[name]

        self.last_list_embed = self._build_list_embeds(free_players, world)
        # Siempre se edita el mismo mensaje (entre o salga alguien)
        await send_or_update_embed(self.bot, self.free_embed_message, CHANNEL_FREES, self.last_list_embed)

    @staticmethod
    def _build_list_embeds(free_players, world):
        entries = [
            (
                p.get("level", 0),
                p.get("vocation", ""),
                f"**[{p['name']}]({player_url(p['name'])})**\n  Nivel {p.get('level', 0)} · {p.get('vocation', '')}",
            )
            for p in free_players
        ]
        return build_grouped_online_embeds(
            entries,
            title="Jugadores free online",
            footer=f"Mundo: {world} · Conectados: {len(free_players)}",
            color=discord.Color.dark_gold(),
            empty_text="No hay jugadores free conectados.",
        )

    @commands.hybrid_command(name="free", description="Muestra la lista de jugadores free online")
    async def free_command(self, ctx):
        """Muestra la lista de frees donde se pide. En #frees, la mueve al final del canal."""
        in_frees = find_channel([ctx.channel], CHANNEL_FREES) is not None
        await ctx.defer(ephemeral=in_frees)
        await self.update_free_players()
        if in_frees:
            await move_list_to_bottom(ctx, self.bot, self.free_embed_message, CHANNEL_FREES, self.last_list_embed)
        else:
            await ctx.send(embeds=self.last_list_embed, files=thumbnail_files(self.last_list_embed))

    @update_free_players.before_loop
    async def before_update(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(FreeTracker(bot))
