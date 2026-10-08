import asyncio
from datetime import datetime
import aiohttp
import discord
from discord.ext import commands, tasks

from config import logger
from services.discord_utils import send_or_update_embed
from services.tibia_api import fetch_player_data, fetch_world_online_players


class FreeTracker(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.free_embed_message = {}
        self.update_free_players.start()

    def cog_unload(self):
        self.update_free_players.cancel()

    async def _fetch_account_status(self, session, semaphore, player):
        async with semaphore:
            p_name = player.get("name")
            res = await fetch_player_data(session, p_name)
            if not res or not res.get("character_info"):
                return None

            account_status = res["character_info"].get("account_status", "").lower()
            if "free" in account_status:
                url = f"https://www.tibia.com/community/?subtopic=characters&name={p_name.replace(' ', '+')}"
                return f"• [{p_name}]({url}) (Nivel {player.get('level', 0)} - {player.get('vocation', '')})"
            return None

    @tasks.loop(seconds=60)
    async def update_free_players(self):
        world = "Quidera"
        logger.info(f"🔄 Consultando Free Accounts online en {world}...")
        semaphore = asyncio.Semaphore(8)

        async with aiohttp.ClientSession() as session:
            online_players = await fetch_world_online_players(session, world)
            free_players = []

            if online_players:
                tasks_list = [self._fetch_account_status(session, semaphore, p) for p in online_players]
                results = await asyncio.gather(*tasks_list)
                free_players = [res for res in results if res is not None]

            embed = discord.Embed(
                title=f"🆓 Jugadores Free Account Online en {world}",
                color=discord.Color.gold(),
            )
            embed.description = "\n".join(free_players[:25]) if free_players else "ℹ️ No hay jugadores Free conectados."
            embed.set_footer(
                text=f"Total Free: {len(free_players)} | Última actualización: {datetime.now().strftime('%H:%M:%S')}"
            )

            await send_or_update_embed(self.bot, self.free_embed_message, "frees", embed)

    @update_free_players.before_loop
    async def before_update(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(FreeTracker(bot))