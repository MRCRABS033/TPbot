import aiohttp
import discord
from discord.ext import commands

from config import WATCHED_PLAYERS, PLAYERS_STATE, logger
from services.tibia_api import fetch_player_data, fetch_world_online_players
from services.storage import save_data

class OnlineCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="online")
    async def list_online_members(self, ctx):
        logger.info(f"📩 Comando !online ejecutado por {ctx.author}")

        if not WATCHED_PLAYERS:
            await ctx.send("📋 La lista de seguimiento está vacía.")
            return

        await ctx.send("🔍 Verificando qué jugadores de la lista están en línea...")

        online_members = []
        async with aiohttp.ClientSession() as session:
            for char_key, data in WATCHED_PLAYERS.items():
                res = await fetch_player_data(session, data["char_name"])
                if res and res.get("character_info"):
                    status = res["character_info"].get("status", "").lower()
                    if status == "online":
                        owner = data["owner"]
                        link = res["tibia_link"]
                        lvl = res["character_info"].get("level", "N/A")
                        voc = res["character_info"].get("vocation", "N/A")
                        
                        # Actualizar el nivel actual en memoria y guardar en disco
                        if isinstance(lvl, int):
                            data["current_level"] = lvl
                            save_data()

                        online_members.append(f"🟢 {link} (Nivel {lvl} {voc}) - **Dueño:** {owner}")

        if not online_members:
            await ctx.send("🔴 Ningún jugador de la lista monitoreada está conectado en este momento.")
            return

        embed = discord.Embed(
            title="🟢 Jugadores Monitoreados En Línea",
            description="\n".join(online_members),
            color=discord.Color.green(),
        )
        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(OnlineCommands(bot))