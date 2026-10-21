"""
Criatura boosted del día (TibiaData /v4/creatures).

- #boosted-creature: tarjeta con la imagen de la criatura y los botones
  "Habilidades y debilidades" y "Loot". Se actualiza sola cuando cambia
  la criatura (en cada Server Save).
- !boosted muestra la tarjeta en cualquier canal.

Es el espejo de cogs/bosses.py, pero con services/creature_boosted.py
(fetch_creature_info) en vez de services/boss_info.py.
"""
import discord
from discord.ext import commands, tasks

from channels import CHANNEL_BOOSTED_CREATURE, find_channel
from cogs.utils import Utils
from config import logger
from services.creature_boosted import fetch_creature_info
from services.discord_utils import _get_previous_message
from services.http import new_session
from services.task_utils import resilient
from services.tibia_api import fetch_boosted_creature

BOOSTED_CREATURE_URL = "https://www.tibia.com/library/?subtopic=creatures"
BOOSTED_CREATURE_COLOR = discord.Color.dark_gold()
# La imagen de la criatura (64x64) se agranda sin suavizar para que se vea nítida
WIKI_FILE_URL = "https://www.tibiawiki.com.br/wiki/Special:FilePath/{}"
CARD_MARKER = "Criatura boosted de hoy"  # Autor de la tarjeta (sirve para encontrarla al reiniciar)


def add_creature_info_fields(embed, creature_info, with_location=True):
    """Vida, experiencia, cooldown y zona de la criatura (datos de TibiaWiki)."""
    if not creature_info:
        return
    if creature_info.get("hp"):
        embed.add_field(name="Vida", value=Utils._number(creature_info["hp"]), inline=True)
    if creature_info.get("exp"):
        embed.add_field(name="Experiencia", value=Utils._number(creature_info["exp"]), inline=True)
    if creature_info.get("cooldown"):
        embed.add_field(name="Cooldown", value=f"{creature_info['cooldown']} h", inline=True)
    if with_location and creature_info.get("location"):
        embed.add_field(name="Zona", value=creature_info["location"][:1024], inline=False)
    if with_location and creature_info.get("notes"):
        embed.add_field(name="Notas", value=creature_info["notes"][:1024], inline=False)


def build_creature_card(creature, image_url, info=None):
    embed = discord.Embed(
        title=creature["name"],
        url=BOOSTED_CREATURE_URL,
        description="Esta es la criatura con boost de hoy en Tibia.",
        color=BOOSTED_CREATURE_COLOR,
        timestamp=discord.utils.utcnow(),
    )
    embed.set_author(name=CARD_MARKER)
    add_creature_info_fields(embed, info)
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
            chunk, title = [], "​"
        chunk.append(item)
    if chunk:
        embed.add_field(name=title, value=separator.join(chunk), inline=False)


def build_abilities_embed(creature, info, image_url):
    embed = discord.Embed(title=f"{creature['name']} · Habilidades y debilidades", url=BOOSTED_CREATURE_URL, color=BOOSTED_CREATURE_COLOR)
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


def build_loot_embed(creature, info, image_url):
    embed = discord.Embed(title=f"{creature['name']} · Loot", url=BOOSTED_CREATURE_URL, color=BOOSTED_CREATURE_COLOR)
    if image_url:
        embed.set_thumbnail(url=image_url)
    embed.set_footer(text="Fuente: TibiaWiki")
    if not info:
        return Utils._no_info(embed)
    if not info.get("loot"):
        embed.description = "TibiaWiki no tiene la lista de loot de esta criatura."
        return embed
    total = sum(len(items) for items in info["loot"].values())
    embed.description = f"{total} objetos posibles."
    for rarity, items in info["loot"].items():
        _add_list_fields(embed, f"{rarity} · {len(items)}", items, separator=", ")
    return embed


class CreatureCardView(discord.ui.View):
    """Botones de la tarjeta de la criatura (persistente: funciona aunque el bot se reinicie)."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="◆  Habilidades y debilidades", style=discord.ButtonStyle.secondary,
                       custom_id="creature:abilities")
    async def abilities(self, interaction: discord.Interaction, button):
        await self._send_info(interaction, build_abilities_embed)

    @discord.ui.button(label="◇  Loot", style=discord.ButtonStyle.secondary, custom_id="creature:loot")
    async def loot(self, interaction: discord.Interaction, button):
        await self._send_info(interaction, build_loot_embed)

    @staticmethod
    async def _send_info(interaction, build_embed):
        """Responde solo a quien pulsó el botón, con la información de la criatura de hoy."""
        cog = interaction.client.get_cog("CreatureBoostedTracker")
        if not cog or not cog.current_creature:
            await interaction.response.send_message("⚠️ Aún no se conoce la criatura de hoy. Intenta en un momento.",
                                                    ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        if cog.creature_info is None:
            async with new_session() as session:
                cog.creature_info = await fetch_creature_info(session, cog.current_creature["name"])
        image_url, file = Utils.creature_media(cog)
        await interaction.followup.send(
            **Utils._send_kwargs(build_embed(cog.current_creature, cog.creature_info, image_url), file), ephemeral=True
        )


# custom_id de los botones de la tarjeta actual (para detectar tarjetas de versiones anteriores)
CARD_BUTTON_IDS = {"creature:abilities", "creature:loot"}


# --- COG ---
class CreatureBoostedTracker(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.current_creature = None
        self.image = None  # (bytes, nombre_de_archivo) de la imagen agrandada de la criatura
        self.creature_info = None  # Vida, experiencia, habilidades, loot (TibiaWiki)
        self.card_message = {}  # { guild_id: id de la tarjeta en #boosted-creature }
        bot.add_view(CreatureCardView())
        self.update_creature.start()

    def cog_unload(self):
        self.update_creature.cancel()

    async def _refresh_creature(self):
        """Consulta la criatura del día. Devuelve True si cambió (o si se consiguió la imagen que faltaba)."""
        async with new_session() as session:
            creature = await fetch_boosted_creature(session)
        if not creature:
            return False
        if self.current_creature and creature["name"] == self.current_creature["name"]:
            if not self.image:
                # La imagen falló antes: se reintenta (si ahora funciona, la tarjeta se republica)
                self.image = await Utils.download_creature_image(creature, WIKI_FILE_URL)
                return self.image is not None
            return False
        self.current_creature = creature
        self.image = await Utils.download_creature_image(creature, WIKI_FILE_URL)
        async with new_session() as session:
            self.creature_info = await fetch_creature_info(session, creature["name"])
        logger.info(f"🐾 Criatura boosted de hoy: {creature['name']}")
        return True

    @resilient
    @tasks.loop(minutes=5)
    async def update_creature(self):
        # True si cambió la criatura o si se consiguió la imagen que antes falló
        refreshed = await self._refresh_creature()
        if not self.current_creature:
            return

        for guild in self.bot.guilds:
            channel = find_channel(guild.text_channels, CHANNEL_BOOSTED_CREATURE)
            if not channel:
                continue
            msg = await _get_previous_message(self.bot, channel, self.card_message, guild.id, CARD_MARKER)

            # Se compara con la tarjeta que ya está en el canal (no con la memoria del bot), así un
            # reinicio no cuenta como "criatura nueva" ni vuelve a publicar la misma tarjeta
            card_creature = msg.embeds[0].title if msg and msg.embeds else None
            new_creature = card_creature != self.current_creature["name"]
            is_last = msg is not None and channel.last_message_id == msg.id
            # ¿La tarjeta salió sin imagen propia y ahora sí la tenemos? (la imagen del embed queda en el CDN de
            # Discord; ojo: msg.attachments viene vacío cuando el archivo se usa dentro del embed)
            card_image = (msg.embeds[0].image.url or "") if msg and msg.embeds else ""
            image_fixed = (refreshed and not new_creature and card_creature is not None and self.image is not None
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
            image_url, file = Utils.creature_media(self)
            new_msg = await channel.send(
                **Utils._send_kwargs(build_creature_card(self.current_creature, image_url, self.creature_info), file,
                               view=CreatureCardView()),
                silent=not new_creature,  # Solo notifica cuando hay una criatura nueva
            )
            self.card_message[guild.id] = new_msg.id
            if new_creature:
                logger.info(f"🐾 Tarjeta de {self.current_creature['name']} publicada en {guild.name}")

    @update_creature.before_loop
    async def before_update(self):
        await self.bot.wait_until_ready()

    @commands.hybrid_command(name="boosted", description="Muestra la criatura boosted de hoy")
    async def boosted_command(self, ctx):
        logger.info(f"📩 Comando !boosted ejecutado por {ctx.author}")
        await ctx.defer()
        await self._refresh_creature()
        if not self.current_creature:
            await ctx.send("⚠️ No se pudo consultar la criatura de hoy. Intenta en un momento.")
            return
        image_url, file = Utils.creature_media(self)
        await ctx.send(**Utils._send_kwargs(build_creature_card(self.current_creature, image_url, self.creature_info), file,
                                      view=CreatureCardView()))


async def setup(bot):
    await bot.add_cog(CreatureBoostedTracker(bot))
