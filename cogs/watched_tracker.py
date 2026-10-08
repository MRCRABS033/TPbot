from datetime import datetime
import aiohttp
import discord
from discord.ext import commands, tasks

from config import WATCHED_PLAYERS, logger
from services.discord_utils import send_or_update_embed
from services.storage import save_data
from services.tibia_api import fetch_world_online_players


class WatchedTracker(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.watched_embed_message = {}
        self.update_online_watched_players.start()

    def cog_unload(self):
        self.update_online_watched_players.cancel()

    @tasks.loop(seconds=60)
    async def update_online_watched_players(self):
        world = "Quidera"
        logger.info(f"🟢 Verificando personajes monitoreados en {world}...")

        if not WATCHED_PLAYERS:
            return

        # Buscar el canal para alertas de nivel
        level_channel = discord.utils.get(self.bot.get_all_channels(), name="level")
        if not level_channel:
            level_channel = discord.utils.get(self.bot.get_all_channels(), name="levels")

        async with aiohttp.ClientSession() as session:
            online_world_players = await fetch_world_online_players(session, world)
            online_lookup = {
                p.get("name", "").lower(): p for p in (online_world_players or [])
            }

            online_list = []
            for char_key, info in WATCHED_PLAYERS.items():
                char_name = info["char_name"]
                owner_name = info["owner"]

                if char_name.lower() in online_lookup:
                    player_data = online_lookup[char_name.lower()]
                    lvl = player_data.get("level", 0)
                    voc = player_data.get("vocation", "N/A")
                    url = f"https://www.tibia.com/community/?subtopic=characters&name={char_name.replace(' ', '+')}"

                    # --- DETECCIÓN Y ALERTA DE SUBIDA DE NIVEL ---
                    old_level = info.get("current_level")

                    if old_level is None:
                        # Inicializar si no estaba registrado
                        info["current_level"] = lvl
                        save_data()
                    elif lvl > old_level:
                        # Actualizar nivel
                        info["current_level"] = lvl
                        save_data()

                        init_lvl = info.get("initial_level", old_level)
                        gained = lvl - init_lvl
                        tibia_link = info.get("tibia_link", f"[{char_name}]({url})")

                        # Enviar alerta si el canal existe
                        if level_channel:
                            embed_level = discord.Embed(
                                title="🎉 ¡Subida de Nivel!",
                                description=f"El personaje {tibia_link} ha subido de nivel.",
                                color=discord.Color.gold(),
                            )
                            embed_level.add_field(name="👤 Dueño", value=owner_name, inline=True)
                            embed_level.add_field(name="🛡️ Vocación", value=voc, inline=True)
                            embed_level.add_field(name="📊 Nivel", value=f"**{old_level}** ➔ **{lvl}**", inline=False)
                            embed_level.add_field(
                                name="📈 Progreso en el Team",
                                value=f"+{gained} niveles desde que ingresó (Nivel inicial: {init_lvl})",
                                inline=False,
                            )
                            embed_level.set_footer(text="Tibia Level Tracker")
                            await level_channel.send(embed=embed_level)

                        logger.info(f"🎉 Alerta de nivel para {char_name}: {old_level} -> {lvl}")

                    online_list.append(
                        f"🟢 [{char_name}]({url}) (Lvl {lvl} - {voc}) — Dueño: **{owner_name}**"
                    )

            embed = discord.Embed(
                title=f"🟢 Personajes Monitoreados Online en {world}",
                color=discord.Color.green(),
            )
            embed.description = (
                "\n".join(online_list)
                if online_list
                else "🔴 Ningún personaje monitoreado está conectado."
            )
            embed.set_footer(
                text=f"Monitoreados: {len(WATCHED_PLAYERS)} | Conectados: {len(online_list)} | {datetime.now().strftime('%H:%M:%S')}"
            )

            await send_or_update_embed(
                self.bot, self.watched_embed_message, "onlines", embed
            )

    @update_online_watched_players.before_loop
    async def before_update(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(WatchedTracker(bot))