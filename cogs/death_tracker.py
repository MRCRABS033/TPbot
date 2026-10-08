import asyncio
import aiohttp
import discord
from discord.ext import commands, tasks

from config import CHANNEL_NAME, PLAYERS_STATE, WATCHED_PLAYERS, logger
from services.tibia_api import fetch_player_data


class DeathTracker(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.track_tibia_players.start()

    def cog_unload(self):
        self.track_tibia_players.cancel()

    @tasks.loop(seconds=60)
    async def track_tibia_players(self):
        logger.info("🔄 === Verificando niveles y muertes de personajes monitoreados ===")

        if not WATCHED_PLAYERS:
            return

        current_entries = list(WATCHED_PLAYERS.items())

        async with aiohttp.ClientSession() as session:
            for char_key, info in current_entries:
                char_name = info["char_name"]
                owner_name = info["owner"]

                res = await fetch_player_data(session, char_name)
                if not res or not res.get("character_info"):
                    continue

                char_info = res["character_info"]
                current_level = char_info.get("level", 0)
                deaths = res.get("deaths", [])
                tibia_link = res.get("tibia_link", char_name)

                # Registro Inicial
                if char_key not in PLAYERS_STATE:
                    PLAYERS_STATE[char_key] = {
                        "level": current_level,
                        "last_death_time": deaths[0]["time"] if deaths else None,
                    }
                    await self._notify_initial_register(tibia_link, owner_name, current_level, char_info)
                    await asyncio.sleep(0.5)
                    continue

                last_state = PLAYERS_STATE[char_key]

                # Subida de Nivel
                if current_level > last_state["level"]:
                    await self._notify_level_up(tibia_link, owner_name, current_level)
                    PLAYERS_STATE[char_key]["level"] = current_level

                # Detección de Muerte
                if deaths and deaths[0]["time"] != last_state["last_death_time"]:
                    latest_death = deaths[0]
                    await self._notify_death(tibia_link, owner_name, current_level, char_info, latest_death)
                    PLAYERS_STATE[char_key]["last_death_time"] = latest_death["time"]

                await asyncio.sleep(0.5)

    @track_tibia_players.before_loop
    async def before_track(self):
        await self.bot.wait_until_ready()

    # --- Notificaciones auxiliares ---
    async def _broadcast_embed(self, embed: discord.Embed):
        for guild in self.bot.guilds:
            channel = discord.utils.get(guild.text_channels, name=CHANNEL_NAME)
            if channel:
                await channel.send(embed=embed)

    async def _notify_initial_register(self, tibia_link, owner, level, char_info):
        embed = discord.Embed(
            title="📌 Registro Inicial de Personaje",
            description=f"El personaje {tibia_link} ha comenzado a ser monitoreado.",
            color=discord.Color.blue(),
        )
        embed.add_field(name="👤 Dueño", value=owner, inline=True)
        embed.add_field(name="📊 Nivel Inicial", value=str(level), inline=True)
        embed.add_field(name="🌎 Mundo", value=char_info.get("world", "N/A"), inline=True)
        embed.add_field(name="🗡️ Vocación", value=char_info.get("vocation", "N/A"), inline=True)
        await self._broadcast_embed(embed)

    async def _notify_level_up(self, tibia_link, owner, level):
        embed = discord.Embed(
            title="🚀 ¡Subida de Nivel!",
            description=f"{tibia_link} (Dueño: **{owner}**) ha alcanzado el nivel **{level}**.",
            color=discord.Color.green(),
        )
        await self._broadcast_embed(embed)

    async def _notify_death(self, tibia_link, owner, level, char_info, death_info):
        embed = discord.Embed(
            title="💀 ¡Muerte Detectada!",
            description=f"{tibia_link} (Nivel {level}) ha muerto.",
            color=discord.Color.red(),
        )
        embed.add_field(name="👤 Dueño", value=owner, inline=True)
        embed.add_field(name="🌎 Mundo", value=char_info.get("world", "Desconocido"), inline=True)
        embed.add_field(name="🕒 Fecha/Hora", value=death_info.get("time", "N/A"), inline=False)
        embed.add_field(name="⚔️ Detalle", value=death_info.get("reason", "Causa desconocida"), inline=False)
        await self._broadcast_embed(embed)


async def setup(bot):
    await bot.add_cog(DeathTracker(bot))