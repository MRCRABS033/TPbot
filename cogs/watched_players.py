from datetime import datetime
import asyncio
import discord
from discord.ext import commands, tasks
import pytz
import services.tibia_api as tibiapy

from config import WATCHED_PLAYERS, logger
from services.storage import save_data


class WatchedPlayers(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.check_server_save.start()
        self.update_players_levels.start()

    def cog_unload(self):
        self.check_server_save.cancel()
        self.update_players_levels.cancel()

    # --- TAREA PARA ACTUALIZAR NIVELES DESDE LA API DE TIBIA ---
    @tasks.loop(hours=1)
    async def update_players_levels(self):
        """Consulta periódicamente la API de Tibia para actualizar el nivel de cada personaje."""
        client = tibiapy.TibiaClient()
        
        for key, data in WATCHED_PLAYERS.items():
            char_name = data.get("char_name")
            if not char_name:
                continue
            
            try:
                char_data = await client.get_character(char_name)
                if char_data and char_data.character:
                    new_level = char_data.character.level
                    
                    if "weekly_start_level" not in data:
                        data["weekly_start_level"] = new_level
                        
                    data["current_level"] = new_level
                    logger.info(f"📊 [API Tibia] Actualizado {char_name}: Nivel {new_level}")
            except Exception as e:
                logger.error(f"❌ Error al consultar la API de Tibia para {char_name}: {e}")
            
            await asyncio.sleep(2)
            
        save_data()

    @update_players_levels.before_loop
    async def before_update(self):
        await self.bot.wait_until_ready()

    # --- COMANDO PARA VER LA TABLA ACTUAL ---
    @commands.command(name="empleado", aliases=["top", "semana"])
    async def show_weekly_top(self, ctx):
        """Muestra el ranking actual de la semana."""
        if not WATCHED_PLAYERS:
            await ctx.send("📋 La lista de seguimiento está vacía.")
            return

        ranking = []
        for key, data in WATCHED_PLAYERS.items():
            curr = data.get("current_level", 0)
            start = data.get("weekly_start_level", curr)
            gained = max(0, curr - start)
            ranking.append((data["char_name"], data["owner"], gained, curr))

        ranking.sort(key=lambda x: x[2], reverse=True)

        embed = discord.Embed(
            title="🏆 Ranking Semanal - Empleado del Mes",
            description="Progreso de niveles acumulados durante la semana actual:\n",
            color=discord.Color.gold(),
        )

        medals = ["🥇", "🥈", "🥉"]
        lines = []

        for idx, (char, owner, gained, curr) in enumerate(ranking, start=1):
            icon = medals[idx - 1] if idx <= 3 else f"`#{idx}`"
            lines.append(f"{icon} **{char}** ({owner}) ➔ **+{gained} lvl** (Nivel actual: {curr})")

        embed.description += "\n".join(lines)
        embed.set_footer(text="El conteo se reinicia cada domingo en el Server Save (10:00 AM CET).")
        await ctx.send(embed=embed)

    # --- TAREA PROGRAMADA EN EL SERVER SAVE ---
    @tasks.loop(minutes=1)
    async def check_server_save(self):
        germany_tz = pytz.timezone("Europe/Berlin")
        now_germany = datetime.now(germany_tz)

        if now_germany.hour == 10 and now_germany.minute == 0:
            logger.info("⏰ Hora de Server Save detectada.")

            if now_germany.weekday() == 6:
                await self.announce_weekly_winner()
                await asyncio.sleep(60)

    async def announce_weekly_winner(self):
        channel = discord.utils.get(self.bot.get_all_channels(), name="empleado-del-mes")
        if not channel:
            channel = discord.utils.get(self.bot.get_all_channels(), name="empleado")

        if not channel:
            logger.warning("⚠️ No se encontró el canal 'empleado-del-mes' o 'empleado' en Discord.")
            return

        ranking = []
        for key, data in WATCHED_PLAYERS.items():
            curr = data.get("current_level", 0)
            start = data.get("weekly_start_level", curr)
            gained = max(0, curr - start)
            ranking.append((data["char_name"], data["owner"], gained))

            data["weekly_start_level"] = curr

        save_data()

        if not ranking:
            return

        ranking.sort(key=lambda x: x[2], reverse=True)
        winner_char, winner_owner, max_gained = ranking[0]

        embed = discord.Embed(
            title="👑 ¡EMPLEADO DE LA SEMANA! 👑",
            description=f"¡Felicidades a **{winner_owner}** por llevar a **{winner_char}** al primer lugar esta semana!",
            color=discord.Color.purple(),
        )
        embed.add_field(name="📈 Niveles Ganados", value=f"**+{max_gained} Niveles**", inline=False)
        embed.set_footer(text="Los contadores semanales han sido reiniciados para la nueva semana.")

        await channel.send(content="@everyone", embed=embed)

    @check_server_save.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(WatchedPlayers(bot))