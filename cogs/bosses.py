"""
Boss boosted del día (TibiaData /v4/boostablebosses).

- #bosses: tarjeta con la imagen del boss y el botón "Hacerlo en grupo". Se actualiza sola
  cuando cambia el boss (en cada Server Save).
- Al pulsar el botón: se eligen los personajes del grupo que participan, se pulsa
  "Crear actividad", se escribe la hora y la actividad se anuncia en #anuncios.
- !boss muestra la tarjeta en cualquier canal.
"""
import io
import re
from datetime import datetime, timedelta

import aiohttp
import discord
from discord.ext import commands, tasks
import pytz
from PIL import Image

from channels import CHANNEL_ANUNCIOS, CHANNEL_BOSSES, find_channel
from config import WATCHED_PLAYERS, logger
from services.discord_utils import _get_previous_message
from services.online_embeds import player_url
from services.task_utils import resilient
from services.tibia_api import fetch_boosted_boss

# La hora que se escribe al crear la actividad se interpreta en hora de México (Ciudad de México).
# Discord no informa la zona horaria de cada usuario; en el anuncio cada quien la ve en su hora local.
LOCAL_TZ = pytz.timezone("America/Mexico_City")
# La imagen del boss (64x64) se agranda sin suavizar para que se vea nítida
IMAGE_SCALE = 3
MAX_SELECT_OPTIONS = 25  # Límite de Discord por menú desplegable
MAX_SELECTS = 4          # Una fila queda para el botón "Crear actividad"
BOSS_COLOR = discord.Color.dark_gold()
CARD_MARKER = "Boss boosted de hoy"  # Autor de la tarjeta (sirve para encontrarla al reiniciar)
BOSSES_URL = "https://www.tibia.com/library/?subtopic=boostablebosses"


# --- IMAGEN ---
async def download_boss_image(image_url):
    """Descarga la imagen del boss y la agranda (PNG). Devuelve bytes o None."""
    if not image_url:
        return None
    try:
        async with aiohttp.ClientSession(headers={"User-Agent": "Mozilla/5.0 (TPbot)"}) as session:
            async with session.get(image_url) as response:
                if response.status != 200:
                    logger.warning(f"⚠️ HTTP {response.status} al descargar la imagen del boss")
                    return None
                data = await response.read()
        image = Image.open(io.BytesIO(data))
        if getattr(image, "n_frames", 1) > 1:
            return data  # Animada: se deja tal cual para no perder la animación
        image = image.convert("RGBA")
        image = image.resize((image.width * IMAGE_SCALE, image.height * IMAGE_SCALE), Image.NEAREST)
        out = io.BytesIO()
        image.save(out, format="PNG")
        return out.getvalue()
    except Exception as e:
        logger.error(f"❌ Error al preparar la imagen del boss: {e}")
        return None


def _image_file(image_bytes):
    return discord.File(io.BytesIO(image_bytes), filename="boss.png") if image_bytes else None


# --- HORA DE LA ACTIVIDAD ---
def parse_activity_time(text):
    """
    '9:30 pm', '9pm', '21:30', '21' -> datetime de hoy (o mañana si esa hora ya pasó), hora de México.
    Devuelve None si no se entiende.
    """
    match = re.fullmatch(r"\s*(\d{1,2})(?::(\d{2}))?\s*(a|p)?\.?\s*(m\.?)?\s*", text.lower())
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2) or 0)
    period = match.group(3)
    if period:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if period == "p" else 0)
    if hour > 23 or minute > 59:
        return None

    now = datetime.now(LOCAL_TZ)
    day = now.date()
    if (hour, minute) < (now.hour, now.minute - 5):
        day += timedelta(days=1)  # Esa hora ya pasó hoy: es mañana
    return LOCAL_TZ.localize(datetime(day.year, day.month, day.day, hour, minute))


def format_activity_time(text):
    when = parse_activity_time(text)
    if not when:
        return text  # No se entendió: se muestra tal cual se escribió
    ts = int(when.timestamp())
    # Discord lo muestra en la hora local de cada persona
    return f"<t:{ts}:t> · <t:{ts}:R>\n<t:{ts}:D>"


# --- EMBEDS ---
def build_boss_card(boss, has_image):
    embed = discord.Embed(
        title=boss["name"],
        url=BOSSES_URL,
        description="Este es el boss con boost de hoy en Tibia.\n¿Lo hacemos en grupo? Pulsa el botón de abajo.",
        color=BOSS_COLOR,
        timestamp=discord.utils.utcnow(),
    )
    embed.set_author(name=CARD_MARKER)
    if has_image:
        embed.set_image(url="attachment://boss.png")
    embed.set_footer(text="Cambia en cada Server Save (10:00 CET)")
    return embed


def build_activity_embed(boss, time_text, note, participants, organizer, has_image):
    embed = discord.Embed(
        title=f"Actividad en grupo: {boss['name']}",
        url=BOSSES_URL,
        color=BOSS_COLOR,
        timestamp=discord.utils.utcnow(),
    )
    embed.set_author(name="Nueva actividad")
    embed.add_field(name="Hora", value=format_activity_time(time_text), inline=True)
    embed.add_field(name="Boss boosted", value=boss["name"], inline=True)
    embed.add_field(name="Organiza", value=organizer.mention, inline=True)

    lines = [
        f"**[{d['char_name']}]({player_url(d['char_name'])})** · {d['owner']} · "
        f"Nivel {d.get('current_level', '?')} · {d.get('vocation', '')}"
        for d in participants
    ]
    chunk, title = [], f"Participantes · {len(participants)}"
    for line in lines:
        # Límite de Discord: 1024 caracteres por campo
        if len("\n".join(chunk + [line])) > 1020:
            embed.add_field(name=title, value="\n".join(chunk), inline=False)
            chunk, title = [], "​"
        chunk.append(line)
    if chunk:
        embed.add_field(name=title, value="\n".join(chunk), inline=False)

    if note:
        embed.add_field(name="Nota", value=note[:1024], inline=False)
    if has_image:
        embed.set_thumbnail(url="attachment://boss.png")
    return embed


# --- FLUJO "HACERLO EN GRUPO" ---
class ActivityModal(discord.ui.Modal, title="Crear actividad"):
    time = discord.ui.TextInput(label="¿A qué hora? (hora de México)", placeholder="Ej: 9:30 pm o 21:30", max_length=30)
    note = discord.ui.TextInput(
        label="Nota (opcional)", placeholder="Ej: llevar supplies, punto de encuentro...",
        style=discord.TextStyle.paragraph, required=False, max_length=300,
    )

    def __init__(self, view):
        super().__init__()
        self.activity_view = view

    async def on_submit(self, interaction: discord.Interaction):
        view = self.activity_view
        cog = interaction.client.get_cog("BossTracker")
        participants = [WATCHED_PLAYERS[k] for k in view.selected_keys() if k in WATCHED_PLAYERS]

        channel = find_channel(interaction.guild.text_channels, CHANNEL_ANUNCIOS) if interaction.guild else None
        if not channel:
            await interaction.response.send_message(
                f"⚠️ No se encontró el canal **#{CHANNEL_ANUNCIOS}**.", ephemeral=True
            )
            return

        image = cog.image_bytes if cog else None
        embed = build_activity_embed(view.boss, self.time.value, self.note.value, participants,
                                     interaction.user, bool(image))
        kwargs = {"embed": embed}
        if image:
            kwargs["file"] = _image_file(image)
        await channel.send(**kwargs)
        logger.info(f"📣 Actividad de {view.boss['name']} creada por {interaction.user} "
                    f"({len(participants)} participantes, hora: {self.time.value})")

        await interaction.response.send_message(f"✅ Actividad publicada en {channel.mention}.", ephemeral=True)
        # El menú de selección ya no hace falta
        try:
            await view.origin.edit_original_response(content="Actividad creada.", view=None)
        except discord.HTTPException:
            pass


class ParticipantSelect(discord.ui.Select):
    def __init__(self, options, index, total):
        placeholder = "Elige los participantes" + (f" ({index + 1}/{total})" if total > 1 else "")
        super().__init__(placeholder=placeholder, options=options, min_values=0, max_values=len(options), row=index)

    async def callback(self, interaction: discord.Interaction):
        # La selección queda guardada en el propio menú (self.values); no hace falta responder nada
        await interaction.response.defer()


class GroupActivityView(discord.ui.View):
    """Menús con los personajes del grupo + botón para crear la actividad (solo lo ve quien lo pidió)."""

    def __init__(self, boss, origin: discord.Interaction):
        super().__init__(timeout=600)
        self.boss = boss
        self.origin = origin

        players = sorted(WATCHED_PLAYERS.items(), key=lambda kv: (kv[1]["owner"].lower(), kv[1]["char_name"].lower()))
        options = [
            discord.SelectOption(
                label=data["char_name"][:100],
                value=key,
                description=f"{data['owner']} · Nivel {data.get('current_level', '?')} · {data.get('vocation', '')}"[:100],
            )
            for key, data in players[:MAX_SELECT_OPTIONS * MAX_SELECTS]
        ]
        chunks = [options[i:i + MAX_SELECT_OPTIONS] for i in range(0, len(options), MAX_SELECT_OPTIONS)]
        self.selects = [ParticipantSelect(chunk, i, len(chunks)) for i, chunk in enumerate(chunks)]
        for select in self.selects:
            self.add_item(select)

    def selected_keys(self):
        return [key for select in self.selects for key in select.values]

    @discord.ui.button(label="Crear actividad", style=discord.ButtonStyle.primary, row=4)
    async def create(self, interaction: discord.Interaction, button):
        if not self.selected_keys():
            await interaction.response.send_message("Elige al menos un participante.", ephemeral=True)
            return
        await interaction.response.send_modal(ActivityModal(self))


class BossCardView(discord.ui.View):
    """Botón de la tarjeta del boss (persistente: funciona aunque el bot se reinicie)."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="＋  Hacerlo en grupo", style=discord.ButtonStyle.secondary, custom_id="boss:group")
    async def group(self, interaction: discord.Interaction, button):
        cog = interaction.client.get_cog("BossTracker")
        boss = cog.current_boss if cog else None
        if not boss:
            await interaction.response.send_message("⚠️ Aún no se conoce el boss de hoy. Intenta en un momento.",
                                                    ephemeral=True)
            return
        if not WATCHED_PLAYERS:
            await interaction.response.send_message("⚠️ El grupo no tiene personajes todavía.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"**{boss['name']}** · Elige quiénes participan y pulsa **Crear actividad**.",
            view=GroupActivityView(boss, interaction),
            ephemeral=True,
        )


# --- COG ---
class BossTracker(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.current_boss = None
        self.image_bytes = None
        self.card_message = {}  # { guild_id: id de la tarjeta en #bosses }
        bot.add_view(BossCardView())
        self.update_boss_card.start()

    def cog_unload(self):
        self.update_boss_card.cancel()

    async def _refresh_boss(self):
        """Consulta el boss del día. Devuelve True si cambió."""
        async with aiohttp.ClientSession() as session:
            boss = await fetch_boosted_boss(session)
        if not boss:
            return False
        if self.current_boss and boss["name"] == self.current_boss["name"]:
            return False
        self.current_boss = boss
        self.image_bytes = await download_boss_image(boss.get("image_url"))
        logger.info(f"👑 Boss boosted de hoy: {boss['name']}")
        return True

    @resilient
    @tasks.loop(minutes=5)
    async def update_boss_card(self):
        changed = await self._refresh_boss()
        if not self.current_boss:
            return

        for guild in self.bot.guilds:
            channel = find_channel(guild.text_channels, CHANNEL_BOSSES)
            if not channel:
                continue
            msg = await _get_previous_message(self.bot, channel, self.card_message, guild.id, CARD_MARKER)
            # Se vuelve a publicar si cambió el boss, si no existe o si ya no es el último mensaje
            if msg and not changed and channel.last_message_id == msg.id:
                continue
            if msg:
                try:
                    await msg.delete()
                except discord.HTTPException:
                    pass

            kwargs = {"embed": build_boss_card(self.current_boss, bool(self.image_bytes)), "view": BossCardView()}
            if self.image_bytes:
                kwargs["file"] = _image_file(self.image_bytes)
            new_msg = await channel.send(**kwargs, silent=not changed)
            self.card_message[guild.id] = new_msg.id

    @update_boss_card.before_loop
    async def before_update(self):
        await self.bot.wait_until_ready()

    @commands.hybrid_command(name="boss", description="Muestra el boss boosted de hoy")
    async def boss_command(self, ctx):
        logger.info(f"📩 Comando !boss ejecutado por {ctx.author}")
        await ctx.defer()
        await self._refresh_boss()
        if not self.current_boss:
            await ctx.send("⚠️ No se pudo consultar el boss de hoy. Intenta en un momento.")
            return
        kwargs = {"embed": build_boss_card(self.current_boss, bool(self.image_bytes)), "view": BossCardView()}
        if self.image_bytes:
            kwargs["file"] = _image_file(self.image_bytes)
        await ctx.send(**kwargs)


async def setup(bot):
    await bot.add_cog(BossTracker(bot))
