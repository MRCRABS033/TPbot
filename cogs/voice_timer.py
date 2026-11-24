import array
import asyncio
import io
import math
import re
import struct
import time
import wave
from pathlib import Path

import discord
from discord.ext import commands

from config import logger

ASK_TIMEOUT = 60          # segundos para responder el intervalo
MIN_INTERVAL = 5          # segundos
MAX_INTERVAL = 6 * 3600   # 6 horas

# Temporizador de boss
BOSS_MIN_INTERVAL = 10
BOSS_MIN_PAUSE = 2        # la voz "¡mecánica!" dura ~1 s, no debe cortarse
BOSS_MAX_PAUSE = 10 * 60
PRE_COUNTDOWN = 10        # cuenta "diez… uno" antes de cada mecánica
COUNTDOWN = 5             # cuenta "cinco… uno" al final de cada mecánica

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


def _build_sound(pattern):
    """PCM crudo (48 kHz, 16 bits, estéreo) a partir de [(frecuencia, ms), ...]; frecuencia 0 = silencio.
    No necesita FFmpeg."""
    rate = 48000
    frames = bytearray()
    norm = math.tanh(BEEP_DRIVE)
    fade = int(rate * 0.005)  # 5 ms de fundido para que no haga "clic"

    for freq, ms in pattern:
        n = int(rate * ms / 1000)
        if not freq:
            frames.extend(b"\x00" * (n * 4))
            continue
        for i in range(n):
            env = min(1.0, i / fade, (n - i) / fade)
            wave = math.tanh(BEEP_DRIVE * math.sin(2 * math.pi * freq * i / rate)) / norm
            v = int(32767 * BEEP_VOLUME * env * wave)
            frames.extend(struct.pack("<hh", v, v))
    return bytes(frames)


# Pitido de !timer y de confirmación al entrar al canal: tres tonos iguales
BEEP_PCM = _build_sound([(1000, 250), (0, 120)] * 3)

# --- Voces del temporizador de boss ---
# WAV de 48 kHz, 16 bits, estéreo (el formato que manda discord.py, así no hace falta FFmpeg).
# Se generaron con la voz "Sabina" de Windows; para regenerarlas ver scripts/generar_voces.ps1
SOUNDS_DIR = Path(__file__).resolve().parent.parent / "assets" / "sounds"
VOICE_SILENCE = 400     # amplitud por debajo de la cual se considera silencio (de 32767)
VOICE_MARGIN_MS = 20    # se deja este margen al recortar el silencio para no comerse la voz


def _load_voice(name, fallback):
    """Carga assets/sounds/<name>.wav, le recorta el silencio de los bordes y lo sube al volumen máximo.
    Si falta o tiene otro formato, usa `fallback` (un pitido) para que el temporizador siga sirviendo."""
    try:
        with wave.open(str(SOUNDS_DIR / f"{name}.wav"), "rb") as w:
            if (w.getframerate(), w.getsampwidth(), w.getnchannels()) != (48000, 2, 2):
                raise ValueError("debe ser 48 kHz, 16 bits, estéreo")
            samples = array.array("h", w.readframes(w.getnframes()))
    except Exception as e:
        logger.warning(f"⚠️ No se pudo cargar la voz '{name}' ({e}); se usará un pitido")
        return fallback

    loud = [i for i, v in enumerate(samples) if abs(v) > VOICE_SILENCE]
    if not loud:
        return fallback
    margin = int(48 * VOICE_MARGIN_MS) * 2  # muestras (2 por cuadro estéreo)
    start = max(0, loud[0] - margin) // 2 * 2
    end = min(len(samples), loud[-1] + margin + 2) // 2 * 2
    samples = samples[start:end]

    gain = 32767 * BEEP_VOLUME / max(abs(v) for v in samples)
    return array.array("h", (int(v * gain) for v in samples)).tobytes()


_TONE = _build_sound([(1200, 150)])
COUNT_VOICES = {n: _load_voice(str(n), _TONE)                # "diez"… "uno"
                for n in range(1, max(PRE_COUNTDOWN, COUNTDOWN) + 1)}
MECHANIC_VOICE = _load_voice("mecanica", BEEP_PCM)          # "¡Mecánica!"
RESUME_VOICE = _load_voice("fin_mecanica", _TONE)           # "Fin de la mecánica"
END_VOICE = _load_voice("fin", BEEP_PCM)                    # "Tiempo terminado"


def boss_schedule(total, first, interval, pause):
    """Lista de (segundo, sonido) desde el inicio. La primera ronda espera `first` y las siguientes
    `interval`. Cada ronda: cuenta "diez… uno", "¡mecánica!", cuenta "cinco… uno" al final de la
    mecánica y "fin de la mecánica". Al llegar al total: "tiempo terminado"."""
    events = []
    cycle_start = 0
    wait = first
    while True:
        mechanic = cycle_start + wait
        # Tras "fin de la mecánica" (~1 s) se deja un respiro antes de empezar a contar
        earliest = cycle_start + 2 if cycle_start else 1
        for n in range(PRE_COUNTDOWN, 0, -1):
            t = mechanic - n
            if earliest <= t < total:
                events.append((t, COUNT_VOICES[n]))
        if mechanic >= total:
            break
        events.append((mechanic, MECHANIC_VOICE))
        resume = mechanic + pause
        for n in range(COUNTDOWN, 0, -1):
            t = resume - n
            # deja 1 s para que se oiga "¡mecánica!" si la mecánica es muy corta
            if mechanic + 1 <= t < total:
                events.append((t, COUNT_VOICES[n]))
        if resume >= total:
            break
        events.append((resume, RESUME_VOICE))
        cycle_start = resume
        wait = interval
    events.append((total, END_VOICE))
    return events


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


class BossTimerModal(discord.ui.Modal, title="⚔️ Temporizador de boss"):
    """Formulario con los tiempos del boss. Cada campo acepta `20m`, `15s`, `1m 30s` o solo `5` (minutos)."""

    total = discord.ui.TextInput(label="⏳ Duración total", placeholder="ej: 20m", default="20m", max_length=12)
    # Ojo: Discord rechaza el formulario entero si un label pasa de 45 caracteres (los emojis cuentan varios)
    first = discord.ui.TextInput(label="1️⃣ Primera ronda (hasta la 1ª mecánica)", placeholder="ej: 1m30s",
                                 default="1m30s", max_length=12)
    interval = discord.ui.TextInput(label="🔁 Siguientes rondas (opcional)", placeholder="ej: 1m10s · vacío = igual que la primera",
                                    default="1m10s", required=False, max_length=12)
    pause = discord.ui.TextInput(label="⚔️ Cuánto dura la mecánica", placeholder="ej: 15s", default="15s", max_length=12)

    def __init__(self, cog):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction):
        total, first, pause = (parse_duration(f.value) for f in (self.total, self.first, self.pause))
        interval = parse_duration(self.interval.value) if self.interval.value.strip() else first
        values = [total, first, interval, pause]
        if None in values:
            await interaction.response.send_message(
                "❌ No entendí algún tiempo. Usa algo como `20m`, `2m`, `15s` o `1m 30s`.", ephemeral=True)
            return
        # Entrar a voz tarda más de los 3 s que da Discord para responder
        await interaction.response.defer(thinking=True)
        await self.cog.start_boss(interaction.guild, interaction.user, interaction.followup.send, *values)


class BossSetupView(discord.ui.View):
    """Botón que abre el formulario (los formularios solo se pueden abrir desde un botón o un comando /).
    Persistente (custom_id fijo, sin timeout) para que siga funcionando aunque pase el tiempo o se reinicie el bot."""

    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Configurar y empezar", style=discord.ButtonStyle.primary, emoji="⚙️", custom_id="voice_timer:boss_setup")
    async def configure(self, interaction, button):
        logger.info(f"⚙️ {interaction.user} abrió el formulario del temporizador de boss")
        await interaction.response.send_modal(BossTimerModal(self.cog))


def boss_setup_embed():
    embed = discord.Embed(
        title="⚔️ Temporizador de boss",
        description="Toca **Configurar y empezar** y llena los tiempos. Tienes que estar en un canal de voz.",
        color=discord.Color.red(),
    )
    embed.add_field(name="⏳ Duración total", value="Cuánto dura todo.\nEj: `20m`", inline=True)
    embed.add_field(name="1️⃣ Primera ronda", value="Cuánto falta para la primera mecánica.\nEj: `1m30s`", inline=True)
    embed.add_field(name="🔁 Siguientes rondas", value="Espera entre mecánicas después de la primera.\nEj: `1m10s`", inline=True)
    embed.add_field(name="⚔️ Mecánica", value="Cuánto dura la mecánica.\nEj: `15s`", inline=True)
    embed.add_field(
        name="🔊 Qué vas a escuchar",
        value="Una voz cuenta **10… 1** y dice **\"¡Mecánica!\"**; al final de la mecánica cuenta **5… 1** "
              "y dice **\"Fin de la mecánica\"**. Al llegar al total dice **\"Tiempo terminado\"**.",
        inline=False,
    )
    embed.set_footer(text="Atajo sin formulario: !bosstimer 20m 1m30s 1m10s 15s  (total, primera, siguientes, mecánica)")
    return embed


class VoiceTimer(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.timers = {}  # guild_id -> asyncio.Task (!timer o !bosstimer, uno por servidor)

    async def cog_load(self):
        self.bot.add_view(StopTimerView(self))
        self.bot.add_view(BossSetupView(self))

    async def cog_unload(self):
        for guild_id in list(self.timers):
            await self.stop_timer(guild_id)

    async def _ask(self, ctx, question):
        await ctx.send(question)

        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel

        try:
            reply = await self.bot.wait_for("message", check=check, timeout=ASK_TIMEOUT)
        except asyncio.TimeoutError:
            await ctx.send("⌛ No respondiste a tiempo, temporizador cancelado.")
            return None
        return reply.content

    async def _play_beep(self, vc, sound=BEEP_PCM):
        """Reproduce un sonido. Devuelve None si salió bien o el texto del error."""
        if not vc.is_connected():
            return "el bot no está conectado al canal de voz"
        if vc.is_playing():
            vc.stop()

        def after(error):
            if error:
                logger.error(f"❌ Error reproduciendo el pitido en {vc.channel}: {error!r}")

        try:
            vc.play(discord.PCMAudio(io.BytesIO(sound)), after=after)
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

    async def _run_boss(self, guild_id, vc, events):
        loop = asyncio.get_running_loop()
        start = loop.time()  # se mide contra el inicio para que los ciclos no se vayan atrasando
        try:
            for at, sound in events:
                delay = start + at - loop.time()
                if delay > 0:
                    await asyncio.sleep(delay)
                if not vc.is_connected():
                    break
                await self._play_beep(vc, sound)
                if sound is MECHANIC_VOICE:
                    logger.info(f"⚔️ Mecánica de boss en {vc.channel} (minuto {format_duration(at)})")
            else:
                logger.info(f"🏁 Temporizador de boss terminado en {vc.channel}")
                await asyncio.sleep(2.5)  # deja terminar el sonido final antes de salir
        except asyncio.CancelledError:
            pass
        finally:
            self.timers.pop(guild_id, None)
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

    def _check_can_start(self, guild, member):
        """Devuelve el mensaje de error si no se puede iniciar un temporizador, o None."""
        voice_state = member.voice
        if not voice_state or not voice_state.channel:
            return "🔇 Tienes que estar en un canal de voz para usar el temporizador."
        if guild.id in self.timers:
            return "⚠️ Ya hay un temporizador activo en este servidor. Usa `!timer stop` o el botón **Detener**."
        return None

    async def _join_voice(self, guild, member, send):
        """Entra al canal de voz de `member` y hace el pitido de confirmación. Devuelve el VoiceClient o None.
        `send` es ctx.send o interaction.followup.send, para avisar los errores."""
        channel = member.voice.channel
        perms = channel.permissions_for(guild.me)
        if not perms.connect or not perms.speak:
            await send(f"🔇 No tengo permiso para **Conectar** y **Hablar** en **{channel.name}**. Pide a un admin que me lo dé.")
            return None

        vc = guild.voice_client
        if vc and not vc.is_connected():
            # Conexión "fantasma": pasa cuando expulsan al bot del canal y discord.py no la limpia.
            # Si no se descarta, connect() falla con "Already connected to a voice channel".
            logger.info(f"🧹 Limpiando conexión de voz caída en {guild.name}")
            await self._force_disconnect(vc)
            vc = None

        try:
            if vc:
                await vc.move_to(channel)
            else:
                vc = await channel.connect(timeout=20)
        except discord.Forbidden:
            logger.error(f"❌ Sin permisos para entrar al canal de voz {channel}")
            await send(f"🔇 Discord no me deja entrar a **{channel.name}**. Revisa mis permisos de **Ver canal**, **Conectar** y **Hablar**.")
            return None
        except asyncio.TimeoutError:
            logger.error(f"❌ Tiempo agotado conectando al canal de voz {channel}")
            await self._force_disconnect(guild.voice_client)
            await send(f"⌛ No pude terminar de conectarme a **{channel.name}** (tiempo agotado). Intenta de nuevo en unos segundos.")
            return None
        except Exception as e:
            logger.error(f"❌ No se pudo conectar al canal de voz {channel}: {e!r}")
            await self._force_disconnect(guild.voice_client)
            await send(f"❌ No pude entrar a **{channel.name}**: `{e}`. Intenta de nuevo; si sigue pasando, revisa mis permisos.")
            return None

        if guild.me.voice and guild.me.voice.mute:
            await send("⚠️ Estoy **silenciado por el servidor** en este canal, así que nadie me va a escuchar. Un admin debe quitarme el silencio (clic derecho sobre mí → desactivar *Silenciar del servidor*).")

        # Pitido de confirmación. Se espera un momento para que termine la conexión de voz
        # (cifrado incluido); si suena apenas conecta, Discord puede descartar el audio.
        await asyncio.sleep(1.5)
        error = await self._play_beep(vc)
        if error:
            await send(f"❌ Entré al canal pero no puedo reproducir sonido: {error}.")
            await vc.disconnect()
            return None
        return vc

    async def start_boss(self, guild, member, send, total, first, interval, pause):
        """Valida los tiempos, entra a voz y arranca el temporizador de boss. Lo usan el comando y el formulario."""
        error = self._check_can_start(guild, member)
        for name, value in (("de la primera ronda", first), ("de las siguientes rondas", interval)):
            if not error and not BOSS_MIN_INTERVAL <= value <= MAX_INTERVAL:
                error = f"❌ El tiempo {name} debe estar entre {format_duration(BOSS_MIN_INTERVAL)} y {format_duration(MAX_INTERVAL)}."
        if not error and not BOSS_MIN_PAUSE <= pause <= BOSS_MAX_PAUSE:
            error = f"❌ La mecánica debe durar entre {format_duration(BOSS_MIN_PAUSE)} y {format_duration(BOSS_MAX_PAUSE)}."
        if not error and not first < total <= MAX_INTERVAL:
            error = f"❌ La duración total debe ser mayor que la primera ronda y como máximo {format_duration(MAX_INTERVAL)}."
        if error:
            await send(error)
            return

        vc = await self._join_voice(guild, member, send)
        if not vc:
            return

        events = boss_schedule(total, first, interval, pause)
        mechanics = sum(1 for _, sound in events if sound is MECHANIC_VOICE)
        end_ts = int(time.time() + total)
        self.timers[guild.id] = asyncio.create_task(self._run_boss(guild.id, vc, events))
        logger.info(f"⚔️ Temporizador de boss iniciado por {member} en {vc.channel}: total {format_duration(total)}, "
                    f"primera {format_duration(first)}, siguientes {format_duration(interval)}, mecánica {format_duration(pause)}")

        if first == interval:
            wait_text = f"Cada **{format_duration(interval)}**"
        else:
            wait_text = f"Primera mecánica a los **{format_duration(first)}**, después cada **{format_duration(interval)}**"
        embed = discord.Embed(
            title="⚔️ Temporizador de boss activo",
            description=(
                f"Canal: **{vc.channel.name}**\n"
                f"{wait_text} → cuenta **10…1** y dice **\"¡Mecánica!\"**\n"
                f"La mecánica dura **{format_duration(pause)}** → cuenta **5…1** y dice **\"Fin de la mecánica\"**\n"
                f"Total: **{format_duration(total)}** ({mechanics} mecánicas) · termina <t:{end_ts}:t> (<t:{end_ts}:R>)"
            ),
            color=discord.Color.red(),
        )
        embed.set_footer(text="Detenlo con el botón o con !bosstimer stop. Si todos salen del canal se detiene solo.")
        await send(embed=embed, view=StopTimerView(self))

    @commands.hybrid_command(name="timer", aliases=["temporizador"], description="Hace sonar un aviso en tu canal de voz cada cierto tiempo")
    async def timer(self, ctx, *, intervalo: str = None):
        logger.info(f"📩 Comando !timer ejecutado por {ctx.author}: '{intervalo}'")
        if not ctx.guild:
            return

        if intervalo and intervalo.strip().lower() in ("stop", "parar", "detener"):
            stopped = await self.stop_timer(ctx.guild.id)
            await ctx.send("⏹️ Temporizador detenido." if stopped else "ℹ️ No hay ningún temporizador activo.")
            return

        error = self._check_can_start(ctx.guild, ctx.author)
        if error:
            await ctx.send(error)
            return

        if not intervalo:
            intervalo = await self._ask(ctx, "⏱️ ¿Cada cuánto tiempo quieres que suene? (ej: `30s`, `5m`, `1h30m`, o solo `5` = 5 minutos)")
            if intervalo is None:
                return

        seconds = parse_duration(intervalo)
        if seconds is None:
            await ctx.send("❌ No entendí el intervalo. Usa algo como `30s`, `5m` o `1h30m`.")
            return
        if not MIN_INTERVAL <= seconds <= MAX_INTERVAL:
            await ctx.send(f"❌ El intervalo debe estar entre {format_duration(MIN_INTERVAL)} y {format_duration(MAX_INTERVAL)}.")
            return

        vc = await self._join_voice(ctx.guild, ctx.author, ctx.send)
        if not vc:
            return

        self.timers[ctx.guild.id] = asyncio.create_task(self._run(ctx.guild.id, vc, seconds))

        embed = discord.Embed(
            title="⏱️ Temporizador activo",
            description=f"Sonará en **{vc.channel.name}** cada **{format_duration(seconds)}**.",
            color=discord.Color.green(),
        )
        embed.set_footer(text="Detenlo con el botón o con !timer stop. Si todos salen del canal se detiene solo.")
        await ctx.send(embed=embed, view=StopTimerView(self))

    @commands.hybrid_command(name="bosstimer", aliases=["bt"], description="Temporizador de boss: avisa cada mecánica hasta el tiempo total")
    async def bosstimer(self, ctx, *, tiempos: str = None):
        logger.info(f"📩 Comando !bosstimer ejecutado por {ctx.author}: '{tiempos}'")
        if not ctx.guild:
            return

        if tiempos and tiempos.strip().lower() in ("stop", "parar", "detener"):
            stopped = await self.stop_timer(ctx.guild.id)
            await ctx.send("⏹️ Temporizador detenido." if stopped else "ℹ️ No hay ningún temporizador activo.")
            return

        if not tiempos:
            # Sin tiempos: formulario. Con /bosstimer se abre directo; con !bosstimer hace falta un botón.
            if ctx.interaction:
                await ctx.interaction.response.send_modal(BossTimerModal(self))
            else:
                await ctx.send(embed=boss_setup_embed(), view=BossSetupView(self))
            return

        parts = tiempos.replace(",", " ").split()
        values = [parse_duration(p) for p in parts]
        if len(values) not in (3, 4) or None in values:
            await ctx.send("❌ Escribe los tiempos separados por espacio (usa `1m30s` todo junto, sin espacio):\n"
                           "• `!bosstimer 20m 1m30s 1m10s 15s` → total, primera ronda, siguientes rondas, mecánica\n"
                           "• `!bosstimer 20m 2m 15s` → si todas las rondas duran lo mismo\n"
                           "O escribe solo `!bosstimer` para usar el formulario.")
            return
        if len(values) == 3:  # total, ronda, mecánica: todas las rondas iguales
            values.insert(2, values[1])
        await self.start_boss(ctx.guild, ctx.author, ctx.send, *values)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        # Si sacaron al bot del canal de voz, se detiene el temporizador y se libera la conexión
        if member.id == self.bot.user.id and before.channel and after.channel is None:
            if member.guild.id in self.timers:
                logger.info(f"⏱️ Sacaron al bot de {before.channel}, deteniendo temporizador")
                await self.stop_timer(member.guild.id)
            else:
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
