from discord.ext import commands

from channels import CHANNEL_ONLINES, find_channel
from config import logger
from services.discord_utils import move_list_to_bottom


class OnlineCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name="online", description="Muestra qué personajes monitoreados están conectados")
    async def list_online_members(self, ctx):
        """Muestra la lista de conectados donde se pide. En #onlines, la mueve al final del canal."""
        logger.info(f"📩 Comando !online ejecutado por {ctx.author}")
        in_onlines = find_channel([ctx.channel], CHANNEL_ONLINES) is not None
        await ctx.defer(ephemeral=in_onlines)

        tracker = self.bot.get_cog("WatchedTracker")
        if not tracker:
            await ctx.send("⚠️ El seguimiento de conectados no está activo.")
            return

        await tracker.update_online_watched_players()
        if in_onlines:
            # En #onlines: la lista actualizada se mueve al final del canal
            await move_list_to_bottom(
                ctx, self.bot, tracker.watched_embed_message, CHANNEL_ONLINES, tracker.last_online_embeds
            )
        else:
            await ctx.send(embeds=tracker.last_online_embeds)


async def setup(bot):
    await bot.add_cog(OnlineCommands(bot))
