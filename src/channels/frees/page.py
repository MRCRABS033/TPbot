import discord
from discord.ext import commands, tasks

from src.core.channel_names import CHANNEL_FREES, find_channel
from src.core.config import WORLD, logger
from src.shared.task_utils import resilient
from src.shared.discord_utils import move_list_to_bottom, send_or_update_embed
from src.shared.online_embeds import build_grouped_online_embeds, player_url
from src.shared.vocation_thumbnails import thumbnail_files
from src.api import tibiadata
from src.channels.frees.service import FreeService


class FreeTracker(commands.Cog):
    """
    #frees: una sola lista de jugadores free online, agrupada por vocación.
    Se edita cada minuto (sin un mensaje por cada conexión).
    """

    def __init__(self, bot, service):
        self.bot = bot
        self.service = service
        self.free_embed_message = {}
        self.last_list_embed = None   # Última lista generada (la usa !free)
        self.update_free_players.start()

    def cog_unload(self):
        self.update_free_players.cancel()

    @resilient
    @tasks.loop(seconds=60)
    async def update_free_players(self):
        logger.info(f"Consultando Free Accounts online en {WORLD}...")
        free_players = await self.service.get_free_players(WORLD)

        self.last_list_embed = self._build_list_embeds(free_players, WORLD)
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
    await bot.add_cog(FreeTracker(bot, FreeService(tibiadata)))
