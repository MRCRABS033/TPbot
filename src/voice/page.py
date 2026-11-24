"""
Temporizador de voz.

- !timer [intervalo]: entra a tu canal de voz y suena cada cierto tiempo. Sin intervalo lo pregunta.
- !timer stop (o el botón Detener): lo detiene. También se detiene solo si todos salen del canal.
"""
import asyncio
import io

import discord
from discord.ext import commands

from src.core.config import logger
from src.voice.service import MAX_INTERVAL, MIN_INTERVAL, build_beep, format_duration, parse_duration

ASK_TIMEOUT = 60  # segundos para responder el intervalo
STOP_WORDS = ("stop", "parar", "detener")

BEEP_PCM = build_beep()


class StopTimerView(discord.ui.View):
    """Vista persistente: custom_id fijo para que el botón siga funcionando tras reiniciar el bot."""

    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Detener", style=discord.ButtonStyle.danger, emoji="⏹️", custom_id="voice_timer:stop")
    async def stop_button(self, interaction, button):
        # Discord exige responder en < 3 s: primero se responde y luego se desconecta de voz
        await interaction.response.edit_message(content=f"⏹️ Temporizador detenido por {interaction.user.mention}.", embed=None, view=None)
        await self.cog.stop_timer(interaction.guild_id)


class VoiceTimer(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.timers = {}      # guild_id -> asyncio.Task
        self.starting = set() # guild_id con un !timer a medio arrancar (preguntando o conectando)

    async def cog_load(self):
        self.bot.add_view(StopTimerView(self))

    async def cog_unload(self):
        for guild_id in list(self.timers):
            await self.stop_timer(guild_id)

    async def _ask_interval(self, ctx):
        await ctx.send("⏱️ ¿Cada cuánto tiempo quieres que suene? (ej: `30s`, `5m`, `1h30m`, o solo `5` = 5 minutos)")

        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel

        try:
            reply = await self.bot.wait_for("message", check=check, timeout=ASK_TIMEOUT)
        except asyncio.TimeoutError:
            await ctx.send("⌛ No respondiste a tiempo, temporizador cancelado.")
            return None
        return reply.content

    async def _play_beep(self, vc):
        """Reproduce el pitido. Devuelve None si salió bien o el texto del error."""
        if not vc.is_connected():
            return "el bot no está conectado al canal de voz"
        if vc.is_playing():
            vc.stop()

        def after(error):
            if error:
                logger.error(f"❌ Error reproduciendo el pitido en {vc.channel}: {error!r}")

        try:
            vc.play(discord.PCMAudio(io.BytesIO(BEEP_PCM)), after=after)
        except discord.opus.OpusNotLoaded:
            logger.error("❌ No se pudo cargar libopus: instala el paquete libopus0 en el servidor")
            return "falta la librería de audio **libopus** en el servidor donde corre el bot"
        except Exception as e:
            logger.error(f"❌ Error reproduciendo el pitido en {vc.channel}: {e!r}")
            return f"`{e!r}`"
        return None

    async def _run(self, guild_id, vc, interval):
        rounds = 0
        try:
            while True:
                await asyncio.sleep(interval)
                if not vc.is_connected():
                    break
                rounds += 1
                await self._play_beep(vc)
                logger.info(f"⏱️ Temporizador sonó en {vc.channel} (vuelta {rounds})")
        except asyncio.CancelledError:
            pass
        finally:
            self.timers.pop(guild_id, None)
            # Siempre se desconecta (aunque ya no esté conectado) para que discord.py
            # libere la conexión y el próximo !timer pueda entrar sin problema.
            await self._force_disconnect(vc)

    async def _force_disconnect(self, vc):
        if not vc:
            return
        try:
            await vc.disconnect(force=True)
        except Exception as e:
            logger.warning(f"⚠️ Error al desconectar de voz: {e!r}")

    async def stop_timer(self, guild_id):
        task = self.timers.pop(guild_id, None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            return True
        return False

    @commands.hybrid_command(name="timer", aliases=["temporizador"], description="Hace sonar un aviso en tu canal de voz cada cierto tiempo")
    async def timer(self, ctx, *, intervalo: str = None):
        logger.info(f"📩 Comando !timer ejecutado por {ctx.author}: '{intervalo}'")
        if not ctx.guild:
            return

        if intervalo and intervalo.strip().lower() in STOP_WORDS:
            stopped = await self.stop_timer(ctx.guild.id)
            await ctx.send("⏹️ Temporizador detenido." if stopped else "ℹ️ No hay ningún temporizador activo.")
            return

        voice_state = ctx.author.voice
        if not voice_state or not voice_state.channel:
            await ctx.send("🔇 Tienes que estar en un canal de voz para usar el temporizador.")
            return

        if ctx.guild.id in self.timers or ctx.guild.id in self.starting:
            await ctx.send("⚠️ Ya hay un temporizador activo en este servidor. Usa `!timer stop` o el botón **Detener**.")
            return

        # Con /timer, Discord exige responder en < 3 s y conectarse a voz tarda más
        await ctx.defer()
        self.starting.add(ctx.guild.id)
        try:
            await self._start(ctx, voice_state.channel, intervalo)
        finally:
            self.starting.discard(ctx.guild.id)

    async def _start(self, ctx, channel, intervalo):
        if not intervalo:
            intervalo = await self._ask_interval(ctx)
            if intervalo is None:
                return

        seconds = parse_duration(intervalo)
        if seconds is None:
            await ctx.send("❌ No entendí el intervalo. Usa algo como `30s`, `5m` o `1h30m`.")
            return
        if not MIN_INTERVAL <= seconds <= MAX_INTERVAL:
            await ctx.send(f"❌ El intervalo debe estar entre {format_duration(MIN_INTERVAL)} y {format_duration(MAX_INTERVAL)}.")
            return

        perms = channel.permissions_for(ctx.guild.me)
        if not perms.connect or not perms.speak:
            await ctx.send(f"🔇 No tengo permiso para **Conectar** y **Hablar** en **{channel.name}**. Pide a un admin que me lo dé.")
            return

        vc = ctx.guild.voice_client
        if vc and not vc.is_connected():
            # Conexión "fantasma": pasa cuando expulsan al bot del canal y discord.py no la limpia.
            # Si no se descarta, connect() falla con "Already connected to a voice channel".
            logger.info(f"🧹 Limpiando conexión de voz caída en {ctx.guild.name}")
            await self._force_disconnect(vc)
            vc = None

        try:
            if vc:
                await vc.move_to(channel)
            else:
                vc = await channel.connect(timeout=20)
        except discord.Forbidden:
            logger.error(f"❌ Sin permisos para entrar al canal de voz {channel}")
            await ctx.send(f"🔇 Discord no me deja entrar a **{channel.name}**. Revisa mis permisos de **Ver canal**, **Conectar** y **Hablar**.")
            return
        except asyncio.TimeoutError:
            logger.error(f"❌ Tiempo agotado conectando al canal de voz {channel}")
            await self._force_disconnect(ctx.guild.voice_client)
            await ctx.send(f"⌛ No pude terminar de conectarme a **{channel.name}** (tiempo agotado). Intenta de nuevo en unos segundos.")
            return
        except Exception as e:
            logger.error(f"❌ No se pudo conectar al canal de voz {channel}: {e!r}")
            await self._force_disconnect(ctx.guild.voice_client)
            await ctx.send(f"❌ No pude entrar a **{channel.name}**: `{e}`. Intenta de nuevo; si sigue pasando, revisa mis permisos.")
            return

        if ctx.guild.me.voice and ctx.guild.me.voice.mute:
            await ctx.send("⚠️ Estoy **silenciado por el servidor** en este canal, así que nadie me va a escuchar. Un admin debe quitarme el silencio (clic derecho sobre mí → desactivar *Silenciar del servidor*).")

        # Pitido de confirmación. Se espera un momento para que termine la conexión de voz
        # (cifrado incluido); si suena apenas conecta, Discord puede descartar el audio.
        await asyncio.sleep(1.5)
        error = await self._play_beep(vc)
        if error:
            await ctx.send(f"❌ Entré al canal pero no puedo reproducir sonido: {error}.")
            await self._force_disconnect(vc)
            return

        self.timers[ctx.guild.id] = asyncio.create_task(self._run(ctx.guild.id, vc, seconds))

        embed = discord.Embed(
            title="⏱️ Temporizador activo",
            description=f"Sonará en **{channel.name}** cada **{format_duration(seconds)}**.",
            color=discord.Color.green(),
        )
        embed.set_footer(text="Detenlo con el botón o con !timer stop. Si todos salen del canal se detiene solo.")
        await ctx.send(embed=embed, view=StopTimerView(self))

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        # Si sacaron al bot del canal de voz, se detiene el temporizador y se libera la conexión
        if member.id == self.bot.user.id and before.channel and after.channel is None:
            if member.guild.id in self.timers:
                logger.info(f"⏱️ Sacaron al bot de {before.channel}, deteniendo temporizador")
                await self.stop_timer(member.guild.id)
            elif member.guild.id not in self.starting:
                await self._force_disconnect(member.guild.voice_client)
            return

        # Si el bot queda solo en el canal, se detiene el temporizador
        vc = member.guild.voice_client
        if not vc or member.guild.id not in self.timers or before.channel != vc.channel:
            return
        if not any(not m.bot for m in vc.channel.members):
            logger.info(f"⏱️ Canal {vc.channel} vacío, deteniendo temporizador")
            await self.stop_timer(member.guild.id)


async def setup(bot):
    await bot.add_cog(VoiceTimer(bot))
