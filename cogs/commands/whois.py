import aiohttp
import discord
from discord.ext import commands

from config import WATCHED_PLAYERS, PLAYERS_STATE, logger
from services.tibia_api import fetch_player_data, fetch_world_online_players
from services.storage import save_data

class WhoisCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="whois")
    async def whois_char(self, ctx, *, character_name: str):
        logger.info(f"📩 Comando !whois ejecutado por {ctx.author} para el personaje: '{character_name}'")
        char_key = character_name.strip().lower()

        if char_key in WATCHED_PLAYERS:
            p = WATCHED_PLAYERS[char_key]
            link = p["tibia_link"]
            owner = p["owner"]
            init_lvl = p.get("initial_level", "N/A")
            curr_lvl = p.get("current_level", "N/A")
            weekly_start = p.get("weekly_start_level", curr_lvl)
            voc = p.get("vocation", "")

            # Calcular diferencias de nivel
            if isinstance(curr_lvl, int):
                total_gained = curr_lvl - init_lvl if isinstance(init_lvl, int) else 0
                weekly_gained = curr_lvl - weekly_start if isinstance(weekly_start, int) else 0

                progress_str = (
                    f"📈 **Progreso total:** {total_gained:+} niveles\n"
                    f"📊 **Progreso semanal:** {weekly_gained:+} niveles"
                )
            else:
                progress_str = ""

            await ctx.send(
                f"🔎 El personaje {link} pertenece a **{owner}**.\n"
                f"🛡️ **Vocación:** {voc}\n"
                f"📍 **Nivel ingreso:** {init_lvl} | **Nivel actual:** {curr_lvl}\n"
                f"{progress_str}"
            )
        else:
            await ctx.send(f"⚠️ El personaje **{character_name}** no se encuentra en la lista de monitoreo.")

async def setup(bot):
    await bot.add_cog(WhoisCommands(bot))