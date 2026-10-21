import aiohttp
import discord
from discord.ext import commands

from channels import CHANNEL_FREES, find_channel
from config import WATCHED_PLAYERS, PLAYERS_STATE, WORLD, logger
from services.tibia_api import fetch_player_data, fetch_world_online_players
from services.storage import save_data
from services.http import new_session

class FreeCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="free")
    async def list_free_players(self, ctx, world: str = WORLD):
        logger.info(f"📩 Comando !free ejecutado por {ctx.author} para el mundo: '{world}'")

        target_channel = find_channel(ctx.guild.text_channels, CHANNEL_FREES)
        if not target_channel:
            await ctx.send(f"⚠️ No se encontró el canal de texto **#{CHANNEL_FREES}** en este servidor.")
            return

        await ctx.send(f"🔍 Consultando jugadores Free Account en **{world}**... (El resultado se enviará a {target_channel.mention})")

        FREE_VOCATIONS = {
            "paladin",
            "knight",
            "sorcerer",
            "druid",
            "monk",
            "none",
            "none (no vocation)",
            "no vocation",
        }

        async with new_session() as session:
            online_players = await fetch_world_online_players(session, world)

            if not online_players:
                await target_channel.send(f"ℹ️ No se encontraron jugadores conectados en este momento en **{world}**.")
                return

            free_players = []
            for player in online_players:
                p_name = player.get("name")
                p_level = player.get("level", 0)
                p_vocation = player.get("vocation", "").strip()

                if p_vocation.lower() in FREE_VOCATIONS:
                    official_url = f"https://www.tibia.com/community/?subtopic=characters&name={p_name.replace(' ', '+')}"
                    tibia_link = f"[{p_name}]({official_url})"
                    free_players.append(f"• {tibia_link} (Nivel {p_level} - {p_vocation})")

            if not free_players:
                await target_channel.send(f"ℹ️ No se encontraron jugadores **Free Account** conectados en este momento en **{world}**.")
                return

            embed = discord.Embed(
                title=f"🆓 Jugadores Free Account Online en {world}",
                description="\n".join(free_players[:25]),
                color=discord.Color.gold(),
            )
            embed.set_footer(text=f"Total Free Online: {len(free_players)}")
            await target_channel.send(embed=embed)

async def setup(bot):
    await bot.add_cog(FreeCommands(bot))