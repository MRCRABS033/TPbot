import io
import re
from datetime import datetime, timedelta
from utils import Utils

import aiohttp
import discord
from discord.ext import commands, tasks
import pytz


from channels import CHANNEL_BOOSTED_CREATURE, find_channel
from services.discord_utils import get_previous_message

LOCAL_TZ = pytz.timezone("America/Mexico_City")
IMAGE_SCALE = 3
MAX_SELECT_OPTIONS = 25
MAX_SELECTS = 4
BOOSTED_CREATURE_URL = "https://www.tibia.com/library/?subtopic=creatures"
BOOSTED_CREATURE_COLOR = discord.Color.dark_gold()
WIKI_FILE_URL = "https://www.tibiawiki.com.br/wiki/Special:FilePath/{}"
CARD_MARKER = "Criatura boosted de hoy"

imagen_escalada = Utils().upscale(None, None, IMAGE_SCALE, MAX_SELECT_OPTIONS, MAX_SELECTS)

def add_creature_info_fields(embed, creature_info, with_location=True):
    """Agrega los campos de información de la criatura al embed."""
    if not creature_info:
        return
    if creature_info.get("name"):
        embed.add_field(name="Nombre", value=creature_info["name"], inline=True)
    if creature_info.get("race"):
        embed.add_field(name="Tipo", value=creature_info["race"], inline=True)


def build_creature_card(creature, image_url, info=None):
    embed = discord.Embed(
        title=creature["name"],
        url = image_url,
        color=BOOSTED_CREATURE_COLOR,
        timestamp= discord.utils.utcnow(),
        )
    embed.set_author(name=CARD_MARKER)
    _add_list_fields(embed, "Información", info)
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

class CreatureBoostedTracker(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.current_creature = None
        self.image = None
        self.creature_info = None
        self.card_message = {}
        self.update_creature.start()

    def cog_unload(self):
        self.update_creature.cancel()

    async def _refresh_creature(self):
        """Actualiza la criatura boosted y su imagen, si es necesario."""
        creature = await self.bot.tibia_api.get_boosted_creature()
        if not creature:
            return
        if self.current_creature and self.current_creature["name"] == creature["name"]:
            return  # No hay cambios
        self.current_creature = creature
        self.image = await Utils.download_creature_image(creature, WIKI_FILE_URL)
        self.creature_info = await self.bot.tibia_api.get_creature_info(creature["name"])