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


def _build_beep():
    """PCM crudo (48 kHz, 16 bits, estéreo) con tres pitidos. No necesita FFmpeg."""
    rate = 48000
    frames = bytearray()

    def tone(freq, ms, volume=0.35):
        n = int(rate * ms / 1000)
        fade = int(rate * 0.005)  # 5 ms de fundido para que no haga "clic"
        for i in range(n):
            env = min(1.0, i / fade, (n - i) / fade)
            v = int(32767 * volume * env * math.sin(2 * math.pi * freq * i / rate))
            frames.extend(struct.pack("<hh", v, v))

    def silence(ms):
        frames.extend(b"\x00" * (int(rate * ms / 1000) * 4))

    for _ in range(3):
        tone(880, 180)
        silence(120)
    return bytes(frames)


BEEP_PCM = _build_beep()


class StopTimerView(discord.ui.View):
    def __init__(self, cog, guild_id):
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = guild_id

    @discord.ui.button(label="Detener", style=discord.ButtonStyle.danger, emoji="⏹️")
    async def stop_button(self, interaction, button):
        await self.cog.stop_timer(self.guild_id)
        await interaction.response.edit_message(content=f"⏹️ Temporizador detenido por {interaction.user.mention}.", embed=None, view=None)


class VoiceTimer(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.timers = {}  # guild_id -> asyncio.Task

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
        if not vc.is_connected():
            return
        if vc.is_playing():
            vc.stop()
        vc.play(discord.PCMAudio(io.BytesIO(BEEP_PCM)))

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

        # Pitido corto de confirmación para saber que el audio funciona
        await self._play_beep(vc)

        self.timers[ctx.guild.id] = asyncio.create_task(self._run(ctx.guild.id, vc, seconds))

        embed = discord.Embed(
            title="⏱️ Temporizador activo",
            description=f"Sonará en **{channel.name}** cada **{format_duration(seconds)}**.",
            color=discord.Color.green(),
        )
        embed.set_footer(text="Detenlo con el botón o con !timer stop. Si todos salen del canal se detiene solo.")
        await ctx.send(embed=embed, view=StopTimerView(self, ctx.guild.id))

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
