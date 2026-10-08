from datetime import datetime
import discord
from discord.ext import commands, tasks
import pytz

from config import WATCHED_PLAYERS, logger
from services.storage import save_data


class WeeklyTracker(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.has_announced_today = False
        self.pinned_message = None  # Guardará la referencia al mensaje fijado

        # Iniciar las dos tareas programadas
        self.check_server_save.start()
        self.update_pinned_empleado.start()

    def cog_unload(self):
        self.check_server_save.cancel()
        self.update_pinned_empleado.cancel()

    # --- TAREA 1: ACTUALIZA EL MENSAJE FIJADO CADA MINUTO ---
    @tasks.loop(minutes=1)
    async def update_pinned_empleado(self):
        channel = discord.utils.get(self.bot.get_all_channels(), name="empleado_del_mes")
        if not channel:
            channel = discord.utils.get(self.bot.get_all_channels(), name="empleado")

        if not channel or not WATCHED_PLAYERS:
            return

        # 1. Buscar el mensaje fijado si aún no tenemos la referencia
        if not self.pinned_message:
            try:
                pins = await channel.pins()
                if pins:
                    self.pinned_message = pins[0]
            except discord.HTTPException:
                pass

        if not self.pinned_message:
            return

        # 2. Calcular quién va en 1° lugar ahora mismo
        ranking = []
        for key, data in WATCHED_PLAYERS.items():
            curr = data.get("current_level", 0)
            start = data.get("weekly_start_level", curr)
            gained = max(0, curr - start)
            ranking.append((data.get("char_name", key), data.get("owner", "Jugador"), gained))

        ranking.sort(key=lambda x: x[2], reverse=True)
        winner_char, winner_owner, max_gained = ranking[0]

        # 3. Formatear la leyenda
        leyenda = (
            f"**{winner_owner}** ({winner_char}), eres un perro vicioso hijuetuputa madre felicidades, eres el empleado del mes.\n"
            f"🏆 **Niveles ganados esta semana:** +{max_gained}\n"
            f"⏱️ *Última actualización: <t:{int(discord.utils.utcnow().timestamp())}:R>*"
        )

        # 4. Editar el mensaje fijado
        try:
            await self.pinned_message.edit(content=leyenda)
        except discord.NotFound:
            self.pinned_message = None  # Si borraron el mensaje, resetea la referencia
        except discord.HTTPException:
            pass

    # --- COMANDO MANUAL PARA VER LA TABLA ACTUAL ---
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
            ranking.append((data["char_name"], data["owner"], gained, curr, start))

        ranking.sort(key=lambda x: x[2], reverse=True)

        embed = discord.Embed(
            title="🏆 Ranking Semanal - Empleado del Mes",
            description="Progreso de niveles acumulados durante la semana actual:",
            color=discord.Color.gold(),
        )

        medals = ["🥇", "🥈", "🥉"]
        lines = []

        for idx, (char, owner, gained, curr, start) in enumerate(ranking, start=1):
            icon = medals[idx - 1] if idx <= 3 else f"`#{idx}`"
            lines.append(f"{icon} **{char}** ({owner}) ➔ **+{gained} lvl** (Inicio: {start} ➔ Actual: {curr})")

        embed.description = "\n".join(lines)
        embed.set_footer(text="El conteo se reinicia cada domingo en el Server Save.")
        await ctx.send(embed=embed)

    # --- TAREA 2: EVALUACIÓN EN EL SERVER SAVE (DOMINGOS) ---
    @tasks.loop(minutes=1)
    async def check_server_save(self):
        germany_tz = pytz.timezone("Europe/Berlin")
        now_germany = datetime.now(germany_tz)

        # Resetear el candado cuando pase la hora del Server Save
        if now_germany.hour != 10 or now_germany.minute != 0:
            self.has_announced_today = False

        # Si es las 10:00 AM hora Alemania
        if now_germany.hour == 10 and now_germany.minute == 0 and not self.has_announced_today:
            logger.info("⏰ Hora de Server Save detectada.")
            self.has_announced_today = True

            # Si es DOMINGO, publicamos el resumen con foto y reiniciamos contadores
            if now_germany.weekday() == 6:
                await self.announce_weekly_winner()

    async def announce_weekly_winner(self):
        channel = discord.utils.get(self.bot.get_all_channels(), name="empleado_del_mes")
        if not channel:
            channel = discord.utils.get(self.bot.get_all_channels(), name="empleado")

        if not channel or not WATCHED_PLAYERS:
            return

        ranking = []
        for key, data in WATCHED_PLAYERS.items():
            curr = data.get("current_level", 0)
            start = data.get("weekly_start_level", curr)
            gained = max(0, curr - start)
            ranking.append((data["char_name"], data["owner"], gained, curr, start))

            # Reiniciar nivel de inicio para la nueva semana
            data["weekly_start_level"] = curr

        save_data()
        ranking.sort(key=lambda x: x[2], reverse=True)

        winner_char, winner_owner, max_gained, _, _ = ranking[0]

        # Texto con la leyenda requerida
        leyenda_ganador = (
            f"@everyone\n"
            f"👑 **{winner_owner}** ({winner_char}), eres un perro vicioso hijuetuputa madre felicidades, eres el empleado del mes.\n\n"
            f"📊 **RESUMEN DE PROGRESO DE LA SEMANA:**"
        )

        embed = discord.Embed(
            title="🏆 Tabla de Posiciones Final de la Semana",
            color=discord.Color.purple(),
        )

        medals = ["🥇", "🥈", "🥉"]
        lines = []

        for idx, (char, owner, gained, curr, start) in enumerate(ranking, start=1):
            icon = medals[idx - 1] if idx <= 3 else f"`#{idx}`"
            lines.append(f"{icon} **{char}** ({owner}) ➔ **+{gained} lvl** (Inicio: {start} ➔ Final: {curr})")

        embed.description = "\n".join(lines)
        embed.set_footer(text="Los contadores han sido reiniciados para la nueva semana.")

        # Enviar el mensaje con la foto perro.jpg y el Embed con la tabla de todos
        try:
            with open("perro.jpg", "rb") as f:
                picture = discord.File(f)
                await channel.send(content=leyenda_ganador, file=picture, embed=embed)
        except FileNotFoundError:
            await channel.send(content=leyenda_ganador, embed=embed)

    @check_server_save.before_loop
    @update_pinned_empleado.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(WeeklyTracker(bot))