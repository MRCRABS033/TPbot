"""
Boss boosted del día (TibiaData /v4/boostablebosses).

- #bosses: tarjeta con la imagen del boss y el botón "Hacerlo en grupo". Se actualiza sola
  cuando cambia el boss (en cada Server Save).
- Al pulsar el botón: se eligen los personajes del grupo que participan, se pulsa
  "Crear actividad", se escribe la hora y la actividad se anuncia en #anuncios.
- !boss muestra la tarjeta en cualquier canal.
"""
import discord
from discord.ext import commands, tasks

from src.api import tibiadata, tibiawiki
from src.channels.bosses.boss_info import fetch_boss_info
from src.channels.bosses.embeds import (
    CARD_MARKER,
    _send_kwargs,
    boss_media,
    build_abilities_embed,
    build_activity_embed,
    build_boss_card,
    build_loot_embed,
)
from src.channels.bosses.service import BossService
from src.core.channel_names import CHANNEL_ANUNCIOS, CHANNEL_BOSSES, find_channel
from src.core.config import WATCHED_PLAYERS, logger
from src.shared.discord_utils import _get_previous_message
from src.shared.panel_ui import MAX_SELECT_OPTIONS
from src.shared.task_utils import resilient

MAX_SELECTS = 4          # Una fila queda para el botón "Crear actividad"


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
        service = view.service
        participants = service.participants(view.selected_keys())

        channel = find_channel(interaction.guild.text_channels, CHANNEL_ANUNCIOS) if interaction.guild else None
        if not channel:
            await interaction.response.send_message(
                f"⚠️ No se encontró el canal **#{CHANNEL_ANUNCIOS}**.", ephemeral=True
            )
            return

        image_url, file = boss_media(service)
        embed = build_activity_embed(view.boss, self.time.value, self.note.value, participants,
                                     interaction.user, image_url, service.info)
        await channel.send(**_send_kwargs(embed, file))
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

    def __init__(self, service, boss, origin: discord.Interaction):
        super().__init__(timeout=600)
        self.service = service
        self.boss = boss
        self.origin = origin

        players = service.sorted_players()
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

    def __init__(self, service):
        super().__init__(timeout=None)
        self.service = service

    @discord.ui.button(label="＋  Hacerlo en grupo", style=discord.ButtonStyle.secondary, custom_id="boss:group")
    async def group(self, interaction: discord.Interaction, button):
        boss = self.service.current_boss
        if not boss:
            await interaction.response.send_message("⚠️ Aún no se conoce el boss de hoy. Intenta en un momento.",
                                                    ephemeral=True)
            return
        if not self.service.has_players():
            await interaction.response.send_message("⚠️ El grupo no tiene personajes todavía.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"**{boss['name']}** · Elige quiénes participan y pulsa **Crear actividad**.",
            view=GroupActivityView(self.service, boss, interaction),
            ephemeral=True,
        )

    @discord.ui.button(label="◆  Habilidades y debilidades", style=discord.ButtonStyle.secondary,
                       custom_id="boss:abilities")
    async def abilities(self, interaction: discord.Interaction, button):
        await self._send_info(interaction, build_abilities_embed)

    @discord.ui.button(label="◇  Loot", style=discord.ButtonStyle.secondary, custom_id="boss:loot")
    async def loot(self, interaction: discord.Interaction, button):
        await self._send_info(interaction, build_loot_embed)

    async def _send_info(self, interaction, build_embed):
        """Responde solo a quien pulsó el botón, con la información del boss de hoy."""
        service = self.service
        if not service.current_boss:
            await interaction.response.send_message("⚠️ Aún no se conoce el boss de hoy. Intenta en un momento.",
                                                    ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await service.ensure_info()
        image_url, file = boss_media(service)
        await interaction.followup.send(
            **_send_kwargs(build_embed(service.current_boss, service.info, image_url), file), ephemeral=True
        )


# custom_id de los botones de la tarjeta actual (para detectar tarjetas de versiones anteriores)
CARD_BUTTON_IDS = {"boss:group", "boss:abilities", "boss:loot"}


# --- COG ---
class BossTracker(commands.Cog):
    def __init__(self, bot, service):
        self.bot = bot
        self.service = service
        self.card_message = {}  # { guild_id: id de la tarjeta en #bosses }
        bot.add_view(BossCardView(service))
        self.update_boss_card.start()

    def cog_unload(self):
        self.update_boss_card.cancel()

    @resilient
    @tasks.loop(minutes=5)
    async def update_boss_card(self):
        # True si cambió el boss o si se consiguió la imagen que antes falló
        service = self.service
        refreshed = await service.refresh()
        if not service.current_boss:
            return

        for guild in self.bot.guilds:
            channel = find_channel(guild.text_channels, CHANNEL_BOSSES)
            if not channel:
                continue
            msg = await _get_previous_message(self.bot, channel, self.card_message, guild.id, CARD_MARKER)

            # Se compara con la tarjeta que ya está en el canal (no con la memoria del bot), así un
            # reinicio no cuenta como "boss nuevo" ni vuelve a publicar la misma tarjeta
            card_boss = msg.embeds[0].title if msg and msg.embeds else None
            new_boss = card_boss != service.current_boss["name"]
            is_last = msg is not None and channel.last_message_id == msg.id
            # ¿La tarjeta salió sin imagen propia y ahora sí la tenemos? (la imagen del embed queda en el CDN de
            # Discord; ojo: msg.attachments viene vacío cuando el archivo se usa dentro del embed)
            card_image = (msg.embeds[0].image.url or "") if msg and msg.embeds else ""
            image_fixed = (refreshed and not new_boss and card_boss is not None and service.image is not None
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
            image_url, file = boss_media(service)
            new_msg = await channel.send(
                **_send_kwargs(build_boss_card(service.current_boss, image_url, service.info), file,
                               view=BossCardView(service)),
                silent=not new_boss,  # Solo notifica cuando hay un boss nuevo
            )
            self.card_message[guild.id] = new_msg.id
            if new_boss:
                logger.info(f"👑 Tarjeta del boss {service.current_boss['name']} publicada en {guild.name}")

    @update_boss_card.before_loop
    async def before_update(self):
        await self.bot.wait_until_ready()

    @commands.hybrid_command(name="boss", description="Muestra el boss boosted de hoy")
    async def boss_command(self, ctx):
        logger.info(f"📩 Comando !boss ejecutado por {ctx.author}")
        await ctx.defer()
        service = self.service
        await service.refresh()
        if not service.current_boss:
            await ctx.send("⚠️ No se pudo consultar el boss de hoy. Intenta en un momento.")
            return
        image_url, file = boss_media(service)
        await ctx.send(**_send_kwargs(build_boss_card(service.current_boss, image_url, service.info), file,
                                      view=BossCardView(service)))


async def setup(bot):
    service = BossService(tibiadata, tibiawiki, fetch_boss_info, WATCHED_PLAYERS)
    await bot.add_cog(BossTracker(bot, service))
