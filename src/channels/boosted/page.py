"""
Criatura boosted del día (TibiaData /v4/creatures).

- #boosted-creature: tarjeta con la imagen de la criatura y los botones
  "Habilidades y debilidades" y "Loot". Se actualiza sola cuando cambia
  la criatura (en cada Server Save).
- !boosted muestra la tarjeta en cualquier canal.
"""
import io

import discord
from discord.ext import commands, tasks

from src.api import tibiadata, tibiawiki
from src.channels.boosted.service import CreatureService
from src.core.channel_names import CHANNEL_BOOSTED_CREATURE, find_channel
from src.core.config import logger
from src.shared import creature_embeds
from src.shared.creature_info import fetch_creature_info
from src.shared.discord_utils import _get_previous_message
from src.shared.task_utils import resilient

CREATURES_URL = "https://www.tibia.com/library/?subtopic=creatures"
CREATURE_COLOR = discord.Color.dark_gold()
CARD_MARKER = "Criatura boosted de hoy"  # Autor de la tarjeta (sirve para encontrarla al reiniciar)
SUBJECT = "esta criatura"
# custom_id de los botones de la tarjeta actual (para detectar tarjetas de versiones anteriores)
CARD_BUTTON_IDS = {"creature:abilities", "creature:loot"}


def _number(value):
    """'28000' -> '28.000'."""
    try:
        return f"{int(value):,}".replace(",", ".")
    except (TypeError, ValueError):
        return value


def creature_media(service):
    """(url para el embed, archivo adjunto o None) según la imagen disponible."""
    if service.image:
        data, filename = service.image
        return f"attachment://{filename}", discord.File(io.BytesIO(data), filename=filename)
    if service.current_creature and service.current_creature.get("image_url"):
        # Último recurso: que Discord cargue la imagen directamente desde la API
        return service.current_creature["image_url"], None
    return None, None


def _send_kwargs(embed, file, **extra):
    kwargs = {"embed": embed, **extra}
    if file:
        kwargs["file"] = file
    return kwargs


# --- EMBEDS ---
def add_creature_info_fields(embed, info):
    """Vida, experiencia, cooldown y zona de la criatura (datos de TibiaWiki)."""
    if not info:
        return
    if info.get("hp"):
        embed.add_field(name="Vida", value=_number(info["hp"]), inline=True)
    if info.get("exp"):
        embed.add_field(name="Experiencia", value=_number(info["exp"]), inline=True)
    if info.get("cooldown"):
        embed.add_field(name="Cooldown", value=f"{info['cooldown']} h", inline=True)
    if info.get("location"):
        embed.add_field(name="Zona", value=info["location"][:1024], inline=False)
    if info.get("notes"):
        embed.add_field(name="Notas", value=info["notes"][:1024], inline=False)


def build_creature_card(creature, image_url, info=None):
    embed = discord.Embed(
        title=creature["name"],
        url=CREATURES_URL,
        description="Esta es la criatura con boost de hoy en Tibia.",
        color=CREATURE_COLOR,
        timestamp=discord.utils.utcnow(),
    )
    embed.set_author(name=CARD_MARKER)
    add_creature_info_fields(embed, info)
    if image_url:
        embed.set_image(url=image_url)
    embed.set_footer(text="Cambia en cada Server Save (10:00 CET)")
    return embed


def build_abilities_embed(creature, info, image_url):
    return creature_embeds.build_abilities_embed(creature["name"], info, image_url, CREATURES_URL, CREATURE_COLOR, SUBJECT)


def build_loot_embed(creature, info, image_url):
    return creature_embeds.build_loot_embed(creature["name"], info, image_url, CREATURES_URL, CREATURE_COLOR, SUBJECT)


class CreatureCardView(discord.ui.View):
    """Botones de la tarjeta de la criatura (persistente: funciona aunque el bot se reinicie)."""

    def __init__(self, service):
        super().__init__(timeout=None)
        self.service = service

    @discord.ui.button(label="◆  Habilidades y debilidades", style=discord.ButtonStyle.secondary,
                       custom_id="creature:abilities")
    async def abilities(self, interaction: discord.Interaction, button):
        await self._send_info(interaction, build_abilities_embed)

    @discord.ui.button(label="◇  Loot", style=discord.ButtonStyle.secondary, custom_id="creature:loot")
    async def loot(self, interaction: discord.Interaction, button):
        await self._send_info(interaction, build_loot_embed)

    async def _send_info(self, interaction, build_embed):
        """Responde solo a quien pulsó el botón, con la información de la criatura de hoy."""
        service = self.service
        if not service.current_creature:
            await interaction.response.send_message("⚠️ Aún no se conoce la criatura de hoy. Intenta en un momento.",
                                                    ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await service.ensure_info()
        image_url, file = creature_media(service)
        await interaction.followup.send(
            **_send_kwargs(build_embed(service.current_creature, service.info, image_url), file), ephemeral=True
        )


# --- COG ---
class CreatureBoostedTracker(commands.Cog):
    def __init__(self, bot, service):
        self.bot = bot
        self.service = service
        self.card_message = {}  # { guild_id: id de la tarjeta en #boosted-creature }
        bot.add_view(CreatureCardView(service))
        self.update_creature.start()

    def cog_unload(self):
        self.update_creature.cancel()

    @resilient
    @tasks.loop(minutes=5)
    async def update_creature(self):
        # True si cambió la criatura o si se consiguió la imagen que antes falló
        service = self.service
        refreshed = await service.refresh()
        if not service.current_creature:
            return

        for guild in self.bot.guilds:
            channel = find_channel(guild.text_channels, CHANNEL_BOOSTED_CREATURE)
            if not channel:
                continue
            msg = await _get_previous_message(self.bot, channel, self.card_message, guild.id, CARD_MARKER)

            # Se compara con la tarjeta que ya está en el canal (no con la memoria del bot), así un
            # reinicio no cuenta como "criatura nueva" ni vuelve a publicar la misma tarjeta
            card_creature = msg.embeds[0].title if msg and msg.embeds else None
            new_creature = card_creature != service.current_creature["name"]
            is_last = msg is not None and channel.last_message_id == msg.id
            # ¿La tarjeta salió sin imagen propia y ahora sí la tenemos? (la imagen del embed queda en el CDN de
            # Discord; ojo: msg.attachments viene vacío cuando el archivo se usa dentro del embed)
            card_image = (msg.embeds[0].image.url or "") if msg and msg.embeds else ""
            image_fixed = (refreshed and not new_creature and card_creature is not None and service.image is not None
                           and "discordapp" not in card_image)
            # ¿La tarjeta es de una versión anterior del bot (otros botones)? Se reemplaza por la actual
            card_buttons = {c.custom_id for row in (msg.components if msg else []) for c in row.children}
            outdated = msg is not None and card_buttons != CARD_BUTTON_IDS
            if msg and not new_creature and is_last and not image_fixed and not outdated:
                continue  # Misma tarjeta, al día y al final del canal: no hay nada que hacer

            if msg:
                try:
                    await msg.delete()
                except discord.HTTPException:
                    pass
            image_url, file = creature_media(service)
            new_msg = await channel.send(
                **_send_kwargs(build_creature_card(service.current_creature, image_url, service.info), file,
                               view=CreatureCardView(service)),
                silent=not new_creature,  # Solo notifica cuando hay una criatura nueva
            )
            self.card_message[guild.id] = new_msg.id
            if new_creature:
                logger.info(f"🐾 Tarjeta de {service.current_creature['name']} publicada en {guild.name}")

    @update_creature.before_loop
    async def before_update(self):
        await self.bot.wait_until_ready()

    @commands.hybrid_command(name="boosted", description="Muestra la criatura boosted de hoy")
    async def boosted_command(self, ctx):
        logger.info(f"📩 Comando !boosted ejecutado por {ctx.author}")
        await ctx.defer()
        service = self.service
        await service.refresh()
        if not service.current_creature:
            await ctx.send("⚠️ No se pudo consultar la criatura de hoy. Intenta en un momento.")
            return
        image_url, file = creature_media(service)
        await ctx.send(**_send_kwargs(build_creature_card(service.current_creature, image_url, service.info), file,
                                      view=CreatureCardView(service)))


async def setup(bot):
    service = CreatureService(tibiadata, tibiawiki, fetch_creature_info)
    await bot.add_cog(CreatureBoostedTracker(bot, service))
