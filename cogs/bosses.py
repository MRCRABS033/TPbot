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

from utils import Utils
import aiohttp
import discord
from discord.ext import commands, tasks
import pytz
from PIL import Image, ImageSequence

from channels import CHANNEL_ANUNCIOS, CHANNEL_BOSSES, find_channel
from config import WATCHED_PLAYERS, logger
from services.discord_utils import _get_previous_message
from services.online_embeds import player_url
from services.task_utils import resilient
from services.tibia_api import fetch_boosted_boss
from services.boss_info import fetch_boss_info
from services.tibia_wiki import wiki_file_names_for
from utils import Utils
# La hora que se escribe al crear la actividad se interpreta en hora de México (Ciudad de México).
# Discord no informa la zona horaria de cada usuario; en el anuncio cada quien la ve en su hora local.
# La imagen del boss (64x64) se agranda sin suavizar para que se vea nítida
IMAGE_SCALE = 3
MAX_SELECT_OPTIONS = 25  # Límite de Discord por menú desplegable
MAX_SELECTS = 4          # Una fila queda para el botón "Crear actividad"
BOSS_COLOR = discord.Color.dark_gold()
CARD_MARKER = "Boss boosted de hoy"  # Autor de la tarjeta (sirve para encontrarla al reiniciar)
BOSSES_URL = "https://www.tibia.com/library/?subtopic=boostablebosses"


# --- IMAGEN ---
# Fuente principal: TibiaWiki BR (imágenes animadas). static.tibia.com bloquea muchas descargas
# automáticas (depende del servidor donde corra el bot), así que queda como respaldo.
WIKI_FILE_URL = "https://www.tibiawiki.com.br/wiki/Special:FilePath/{}"


def add_boss_info_fields(embed, info, with_location=True):
    """Clase, vida, experiencia, cooldown y zona del boss (datos de TibiaWiki)."""
    if not info:
        return
    if info.get("bosstiaryclass"):
        embed.add_field(name="Clase", value=info["bosstiaryclass"], inline=True)
    if info.get("hp"):
        embed.add_field(name="Vida", value=Utils._number(info["hp"]), inline=True)
    if info.get("exp"):
        embed.add_field(name="Experiencia", value=Utils._number(info["exp"]), inline=True)
    if info.get("cooldown"):
        embed.add_field(name="Cooldown", value=f"{info['cooldown']} h", inline=True)
    if with_location and info.get("location"):
        embed.add_field(name="Zona", value=info["location"][:1024], inline=False)
    if with_location and info.get("notes"):
        embed.add_field(name="Notas", value=info["notes"][:1024], inline=False)


def build_boss_card(boss, image_url, info=None):
    embed = discord.Embed(
        title=boss["name"],
        url=BOSSES_URL,
        description="Este es el boss con boost de hoy en Tibia.\n¿Lo hacemos en grupo? Pulsa el botón de abajo.",
        color=BOSS_COLOR,
        timestamp=discord.utils.utcnow(),
    )
    embed.set_author(name=CARD_MARKER)
    add_boss_info_fields(embed, info)
    if image_url:
        embed.set_image(url=image_url)
    embed.set_footer(text="Cambia en cada Server Save (10:00 CET)")
    return embed


def _add_list_fields(embed, title, items, separator="\n"):
    """Agrega una lista en uno o más campos (límite de Discord: 1024 caracteres por campo)."""
    chunk = []
    for item in items:
        if len(separator.join(chunk + [item])) > 1020:
            embed.add_field(name=title, value=separator.join(chunk), inline=False)
            chunk, title = [], "\u200b"
        chunk.append(item)
    if chunk:
        embed.add_field(name=title, value=separator.join(chunk), inline=False)




def build_abilities_embed(boss, info, image_url):
    embed = discord.Embed(title=f"{boss['name']} · Habilidades y debilidades", url=BOSSES_URL, color=BOSS_COLOR)
    if image_url:
        embed.set_thumbnail(url=image_url)
    embed.set_footer(text="Fuente: TibiaWiki")
    if not info:
        return Utils._no_info(embed)

    if info.get("max_damage"):
        embed.add_field(name="Daño máximo", value=info["max_damage"], inline=False)
    if info.get("abilities"):
        _add_list_fields(embed, "Habilidades", [f"• {a}" for a in info["abilities"]])
    else:
        embed.add_field(name="Habilidades", value="TibiaWiki no tiene sus habilidades registradas.", inline=False)
    embed.add_field(name="Débil a", value=", ".join(info.get("weak") or []) or "—", inline=True)
    embed.add_field(name="Resiste", value=", ".join(info.get("strong") or []) or "—", inline=True)
    embed.add_field(name="Inmune a", value=", ".join(info.get("immune") or []) or "—", inline=True)
    if info.get("heals"):
        embed.add_field(name="Se cura con", value=", ".join(info["heals"]), inline=False)
    if info.get("traits"):
        _add_list_fields(embed, "Rasgos", [f"• {t}" for t in info["traits"]])
    return embed


def build_loot_embed(boss, info, image_url):
    embed = discord.Embed(title=f"{boss['name']} · Loot", url=BOSSES_URL, color=BOSS_COLOR)
    if image_url:
        embed.set_thumbnail(url=image_url)
    embed.set_footer(text="Fuente: TibiaWiki")
    if not info:
        return Utils._no_info(embed)
    if not info.get("loot"):
        embed.description = "TibiaWiki no tiene la lista de loot de este boss."
        return embed
    total = sum(len(items) for items in info["loot"].values())
    embed.description = f"{total} objetos posibles."
    for rarity, items in info["loot"].items():
        _add_list_fields(embed, f"{rarity} · {len(items)}", items, separator=", ")
    return embed


def build_activity_embed(boss, time_text, note, participants, organizer, image_url, info=None):
    embed = discord.Embed(
        title=f"Actividad en grupo: {boss['name']}",
        url=BOSSES_URL,
        color=BOSS_COLOR,
        timestamp=discord.utils.utcnow(),
    )
    embed.set_author(name="Nueva actividad")
    embed.add_field(name="Hora", value=Utils.format_activity_time(time_text), inline=True)
    embed.add_field(name="Boss boosted", value=boss["name"], inline=True)
    embed.add_field(name="Organiza", value=organizer.mention, inline=True)
    if info and info.get("location"):
        embed.add_field(name="Zona", value=info["location"][:1024], inline=False)

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
    if image_url:
        embed.set_thumbnail(url=image_url)
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

        image_url, file = Utils.creature_media(cog)
        embed = build_activity_embed(view.boss, self.time.value, self.note.value, participants,
                                     interaction.user, image_url, cog.boss_info if cog else None)
        await channel.send(**Utils._send_kwargs(embed, file))
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

    @discord.ui.button(label="◆  Habilidades y debilidades", style=discord.ButtonStyle.secondary,
                       custom_id="boss:abilities")
    async def abilities(self, interaction: discord.Interaction, button):
        await self._send_info(interaction, build_abilities_embed)

    @discord.ui.button(label="◇  Loot", style=discord.ButtonStyle.secondary, custom_id="boss:loot")
    async def loot(self, interaction: discord.Interaction, button):
        await self._send_info(interaction, build_loot_embed)

    @staticmethod
    async def _send_info(interaction, build_embed):
        """Responde solo a quien pulsó el botón, con la información del boss de hoy."""
        cog = interaction.client.get_cog("BossTracker")
        if not cog or not cog.current_boss:
            await interaction.response.send_message("⚠️ Aún no se conoce el boss de hoy. Intenta en un momento.",
                                                    ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        if cog.boss_info is None:
            async with aiohttp.ClientSession() as session:
                cog.boss_info = await fetch_boss_info(session, cog.current_boss["name"])
        image_url, file = Utils.creature_media(cog)
        await interaction.followup.send(
            **Utils._send_kwargs(build_embed(cog.current_boss, cog.boss_info, image_url), file), ephemeral=True
        )


# custom_id de los botones de la tarjeta actual (para detectar tarjetas de versiones anteriores)
CARD_BUTTON_IDS = {"boss:group", "boss:abilities", "boss:loot"}


# --- COG ---
class BossTracker(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.current_boss = None
        self.image = None  # (bytes, nombre_de_archivo) de la imagen agrandada del boss
        self.boss_info = None  # Clase, vida, experiencia, cooldown y zona (TibiaWiki)
        self.card_message = {}  # { guild_id: id de la tarjeta en #bosses }
        bot.add_view(BossCardView())
        self.update_boss_card.start()

    def cog_unload(self):
        self.update_boss_card.cancel()

    async def _refresh_boss(self):
        """Consulta el boss del día. Devuelve True si cambió (o si se consiguió la imagen que faltaba)."""
        async with aiohttp.ClientSession() as session:
            boss = await fetch_boosted_boss(session)
        if not boss:
            return False
        if self.current_boss and boss["name"] == self.current_boss["name"]:
            if not self.image:
                # La imagen falló antes: se reintenta (si ahora funciona, la tarjeta se republica)
                self.image = await Utils.download_creature_image(boss, WIKI_FILE_URL)
                return self.image is not None
            return False
        self.current_boss = boss
        self.image = await Utils.download_creature_image(boss, WIKI_FILE_URL)
        async with aiohttp.ClientSession() as session:
            self.boss_info = await fetch_boss_info(session, boss["name"])
        logger.info(f"👑 Boss boosted de hoy: {boss['name']}")
        return True

    @resilient
    @tasks.loop(minutes=5)
    async def update_boss_card(self):
        # True si cambió el boss o si se consiguió la imagen que antes falló
        refreshed = await self._refresh_boss()
        if not self.current_boss:
            return

        for guild in self.bot.guilds:
            channel = find_channel(guild.text_channels, CHANNEL_BOSSES)
            if not channel:
                continue
            msg = await _get_previous_message(self.bot, channel, self.card_message, guild.id, CARD_MARKER)

            # Se compara con la tarjeta que ya está en el canal (no con la memoria del bot), así un
            # reinicio no cuenta como "boss nuevo" ni vuelve a publicar la misma tarjeta
            card_boss = msg.embeds[0].title if msg and msg.embeds else None
            new_boss = card_boss != self.current_boss["name"]
            is_last = msg is not None and channel.last_message_id == msg.id
            # ¿La tarjeta salió sin imagen propia y ahora sí la tenemos? (la imagen del embed queda en el CDN de
            # Discord; ojo: msg.attachments viene vacío cuando el archivo se usa dentro del embed)
            card_image = (msg.embeds[0].image.url or "") if msg and msg.embeds else ""
            image_fixed = (refreshed and not new_boss and card_boss is not None and self.image is not None
                           and "discordapp" not in card_image)
            # ¿La tarjeta es de una versión anterior del bot (otros botones)? Se reemplaza por la actual
            card_buttons = {c.custom_id for row in (msg.components if msg else []) for c in row.children}
            outdated = msg is not None and card_buttons != CARD_BUTTON_IDS
            if msg and not new_boss and is_last and not image_fixed and not outdated:
                continue  # Misma tarjeta, al día y al final del canal: no hay nada que hacer

            if msg:
                try:
                    await msg.delete()
                except discord.HTTPException:
                    pass
            image_url, file = Utils.creature_media(self)
            new_msg = await channel.send(
                **Utils._send_kwargs(build_boss_card(self.current_boss, image_url, self.boss_info), file,
                               view=BossCardView()),
                silent=not new_boss,  # Solo notifica cuando hay un boss nuevo
            )
            self.card_message[guild.id] = new_msg.id
            if new_boss:
                logger.info(f"👑 Tarjeta del boss {self.current_boss['name']} publicada en {guild.name}")

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
        image_url, file = Utils.creature_media(self)
        await ctx.send(**Utils._send_kwargs(build_boss_card(self.current_boss, image_url, self.boss_info), file,
                                      view=BossCardView()))


async def setup(bot):
    await bot.add_cog(BossTracker(bot))
