from datetime import datetime, time, timedelta, timezone
import asyncio
import discord
from discord.ext import commands, tasks
import pytz

from channels import CHANNEL_EMPLEADO, CHANNEL_EMPLEADO_ALT, find_channel
from config import WATCHED_PLAYERS, logger
from services.task_utils import resilient
from services.storage import save_data

# --- PUBLICACIÓN AUTOMÁTICA DEL RANKING ---
# Hora de Colombia (UTC-5, sin horario de verano)
RANKING_TZ = timezone(timedelta(hours=-5))
RANKING_TIMES = [
    time(hour=0, minute=0, tzinfo=RANKING_TZ),   # 12:00 a. m.
    time(hour=12, minute=0, tzinfo=RANKING_TZ),  # 12:00 p. m.
]


class WatchedPlayers(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Último ranking diario y último anuncio del ganador por servidor (la limpieza los conserva)
        self.ranking_messages = {}
        self.winner_messages = {}
        self.check_server_save.start()
        self.post_daily_ranking.start()

    def cog_unload(self):
        self.check_server_save.cancel()
        self.post_daily_ranking.cancel()

    def _get_empleado_channel(self):
        channel = find_channel(self.bot.get_all_channels(), CHANNEL_EMPLEADO)
        if not channel:
            channel = find_channel(self.bot.get_all_channels(), CHANNEL_EMPLEADO_ALT)
        return channel

    # --- COMANDO PARA VER LA TABLA ACTUAL ---
    @commands.hybrid_command(name="empleado", aliases=["top", "semana"], description="Muestra el ranking semanal del empleado del mes")
    async def show_weekly_top(self, ctx):
        """Muestra el ranking actual de la semana."""
        logger.info(f"📩 Comando !empleado ejecutado por {ctx.author}")
        if not WATCHED_PLAYERS:
            await ctx.send("📋 La lista de seguimiento está vacía.")
            return

        await ctx.send(embed=await self._build_ranking_embed())

    # --- TAREA: PUBLICA EL RANKING TODOS LOS DÍAS A LAS 12 A. M. Y 12 P. M. ---
    @resilient
    @tasks.loop(time=RANKING_TIMES)
    async def post_daily_ranking(self):
        if not WATCHED_PLAYERS:
            return

        channel = self._get_empleado_channel()
        if not channel:
            logger.warning(f"⚠️ No se encontró el canal '{CHANNEL_EMPLEADO}' o '{CHANNEL_EMPLEADO_ALT}' para el ranking diario.")
            return

        msg = await channel.send(embed=await self._build_ranking_embed())
        self.ranking_messages[channel.guild.id] = msg.id
        logger.info(f"🏆 Ranking diario publicado en #{channel.name}")

    @post_daily_ranking.before_loop
    async def before_daily_ranking(self):
        await self.bot.wait_until_ready()

    @staticmethod
    def _center_over(medal, name):
        """
        Pone espacios antes de la medalla para que quede aprox. centrada sobre el nombre.
        Discord no permite centrar texto, así que se estima el ancho midiendo en medios
        espacios (U+2002, que Discord no elimina): ~0.9 por letra en negrita y ~2.2 la medalla.
        Los nombres de más de ~21 letras se parten en dos líneas en el podio, así que se
        centra sobre la primera línea.
        """
        visible_chars = min(len(name), 21)
        spaces = round((visible_chars * 0.9 - 2.2) / 2)
        return "​" + " " * max(0, spaces) + medal

    @staticmethod
    def _niveles(n):
        return "nivel" if n == 1 else "niveles"

    async def _build_ranking_embed(self):
        ranking = []
        for data in WATCHED_PLAYERS.values():
            curr = data.get("current_level", 0)
            start = data.get("weekly_start_level", curr)
            gained = max(0, curr - start)
            ranking.append((data["char_name"], data["owner"], gained, curr, start))

        ranking.sort(key=lambda x: x[2], reverse=True)

        embed = discord.Embed(
            title="🏆 Empleado del mes",
            description="Niveles ganados durante la semana actual.",
            color=discord.Color.dark_gold(),
            timestamp=discord.utils.utcnow(),
        )

        # Podio olímpico: 2.º a la izquierda, 1.º al centro (más alto), 3.º a la derecha.
        # La "altura" se simula con líneas vacías antes de cada bloque.
        blank = "​"
        podium = [(1, "🥈", 1), (0, "🥇", 0), (2, "🥉", 1)]  # (posición, medalla, líneas vacías)
        if len(ranking) >= 3:
            for pos, medal, padding in podium:
                char, owner, gained, curr, start = ranking[pos]
                embed.add_field(
                    name=blank,
                    value=(
                        f"{blank}\n" * padding
                        + f"{self._center_over(medal, char)}\n**{char}**\n{owner}\n**+{gained}** {self._niveles(gained)}\nNivel actual: {curr}"
                    ),
                    inline=True,
                )
        else:
            # Con menos de 3 personajes no hay podio: se muestran en orden
            for medal, (char, owner, gained, curr, start) in zip(["🥇", "🥈"], ranking):
                embed.add_field(
                    name=blank,
                    value=f"{self._center_over(medal, char)}\n**{char}**\n{owner}\n**+{gained}** {self._niveles(gained)}\nNivel actual: {curr}",
                    inline=True,
                )

        # Resto: una entrada por personaje, separadas por una línea en blanco.
        # Se reparte en varios campos si supera el límite de 1024 caracteres por campo.
        entries = [
            f"`{idx}.` **{char}** · {owner}\n"
            f"  +{gained} {self._niveles(gained)} · Nivel actual: {curr}"
            for idx, (char, owner, gained, curr, _) in enumerate(ranking[3:], start=4)
        ]
        if entries:
            embed.add_field(name=blank, value=blank, inline=False)  # Separación con el podio
        chunk = []
        title = "Resto del ranking"
        for entry in entries:
            if len("\n\n".join(chunk + [entry])) > 1020:
                embed.add_field(name=title, value="\n\n".join(chunk), inline=False)
                chunk, title = [], blank
            chunk.append(entry)
        if chunk:
            embed.add_field(name=title, value="\n\n".join(chunk), inline=False)

        embed.set_footer(text="Se reinicia cada domingo en el Server Save (10:00 CET)")
        return embed

    # --- TAREA PROGRAMADA EN EL SERVER SAVE ---
    @resilient
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
        channel = self._get_empleado_channel()
        if not channel:
            logger.warning(f"⚠️ No se encontró el canal '{CHANNEL_EMPLEADO}' o '{CHANNEL_EMPLEADO_ALT}' en Discord.")
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

        msg = await channel.send(content="@everyone", embed=embed)
        self.winner_messages[channel.guild.id] = msg.id

    @check_server_save.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(WatchedPlayers(bot))