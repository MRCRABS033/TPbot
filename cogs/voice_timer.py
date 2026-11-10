import asyncio
import io
import math
import re
import struct

import discord
from discord.ext import commands

from config import logger

ASK_TIMEOUT = 60          # segundos para responder el intervalo
MIN_INTERVAL = 5          # segundos
MAX_INTERVAL = 6 * 3600   # 6 horas

_DURATION_RE = re.compile(r"(\d+)\s*([hms]?)", re.IGNORECASE)
_UNIT_SECONDS = {"h": 3600, "m": 60, "s": 1, "": 60}  # número solo = minutos


def parse_duration(text):
    """'90s', '5m', '1h30m', '1m 30s' o '5' (minutos) -> segundos. None si no es válido."""
    text = text.strip().lower().replace("min", "m").replace("seg", "s")
    if not text or not re.fullmatch(r"(\s*\d+\s*[hms]?)+\s*", text):
        return None
    return sum(int(n) * _UNIT_SECONDS[u] for n, u in _DURATION_RE.findall(text))


def format_duration(seconds):
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    parts = [f"{h}h" if h else "", f"{m}m" if m else "", f"{s}s" if s else ""]
    return " ".join(p for p in parts if p) or "0s"


BEEP_VOLUME = 0.95  # 0.0 a 1.0
BEEP_DRIVE = 3.0    # cuanto más alto, onda más "cuadrada" y se percibe más fuerte


def _build_beep():
    """PCM crudo (48 kHz, 16 bits, estéreo) con tres pitidos. No necesita FFmpeg."""
    rate = 48000
    frames = bytearray()
    norm = math.tanh(BEEP_DRIVE)

    def tone(freq, ms):
        n = int(rate * ms / 1000)
        fade = int(rate * 0.005)  # 5 ms de fundido para que no haga "clic"
        for i in range(n):
            env = min(1.0, i / fade, (n - i) / fade)
            wave = math.tanh(BEEP_DRIVE * math.sin(2 * math.pi * freq * i / rate)) / norm
            v = int(32767 * BEEP_VOLUME * env * wave)
            frames.extend(struct.pack("<hh", v, v))

    def silence(ms):
        frames.extend(b"\x00" * (int(rate * ms / 1000) * 4))

    for _ in range(3):
        tone(1000, 250)
        silence(120)
    return bytes(frames)


BEEP_PCM = _build_beep()


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
        self.timers = {}  # guild_id -> asyncio.Task

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
            if vc.is_connected():
                await vc.disconnect()

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

        if intervalo and intervalo.strip().lower() in ("stop", "parar", "detener"):
            stopped = await self.stop_timer(ctx.guild.id)
            await ctx.send("⏹️ Temporizador detenido." if stopped else "ℹ️ No hay ningún temporizador activo.")
            return

        voice_state = ctx.author.voice
        if not voice_state or not voice_state.channel:
            await ctx.send("🔇 Tienes que estar en un canal de voz para usar el temporizador.")
            return

        if ctx.guild.id in self.timers:
            await ctx.send("⚠️ Ya hay un temporizador activo en este servidor. Usa `!timer stop` o el botón **Detener**.")
            return

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

        channel = voice_state.channel
        perms = channel.permissions_for(ctx.guild.me)
        if not perms.connect or not perms.speak:
            await ctx.send(f"🔇 No tengo permiso para **Conectar** y **Hablar** en **{channel.name}**. Pide a un admin que me lo dé.")
            return

        try:
            vc = ctx.guild.voice_client
            if vc and vc.is_connected():
                await vc.move_to(channel)
            else:
                vc = await channel.connect()
        except Exception as e:
            logger.error(f"❌ No se pudo conectar al canal de voz {channel}: {e}")
            await ctx.send(f"❌ No pude entrar a **{channel.name}**. Revisa que tenga permisos para conectarme y hablar.")
            return

        if ctx.guild.me.voice and ctx.guild.me.voice.mute:
            await ctx.send("⚠️ Estoy **silenciado por el servidor** en este canal, así que nadie me va a escuchar. Un admin debe quitarme el silencio (clic derecho sobre mí → desactivar *Silenciar del servidor*).")

        # Pitido de confirmación. Se espera un momento para que termine la conexión de voz
        # (cifrado incluido); si suena apenas conecta, Discord puede descartar el audio.
        await asyncio.sleep(1.5)
        error = await self._play_beep(vc)
        if error:
            await ctx.send(f"❌ Entré al canal pero no puedo reproducir sonido: {error}.")
            await vc.disconnect()
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
        # Si el bot queda solo en el canal, se detiene el temporizador
        vc = member.guild.voice_client
        if not vc or member.guild.id not in self.timers or before.channel != vc.channel:
            return
        if not any(not m.bot for m in vc.channel.members):
            logger.info(f"⏱️ Canal {vc.channel} vacío, deteniendo temporizador")
            await self.stop_timer(member.guild.id)


async def setup(bot):
    await bot.add_cog(VoiceTimer(bot))
