"""#bosses: embeds de la tarjeta del boss, su información y las actividades en grupo."""
import io

import discord

from src.channels.bosses.service import parse_activity_time
from src.shared.embed_helpers import chunk_fields
from src.shared.online_embeds import player_url

BOSS_COLOR = discord.Color.dark_gold()
CARD_MARKER = "Boss boosted de hoy"  # Autor de la tarjeta (sirve para encontrarla al reiniciar)
BOSSES_URL = "https://www.tibia.com/library/?subtopic=boostablebosses"


def boss_media(service):
    """(url para el embed, archivo adjunto o None) según la imagen disponible."""
    if service.image:
        data, filename = service.image
        return f"attachment://{filename}", discord.File(io.BytesIO(data), filename=filename)
    if service.current_boss and service.current_boss.get("image_url"):
        # Último recurso: que Discord cargue la imagen directamente desde la API
        return service.current_boss["image_url"], None
    return None, None


def _send_kwargs(embed, file, **extra):
    kwargs = {"embed": embed, **extra}
    if file:
        kwargs["file"] = file
    return kwargs


def format_activity_time(text):
    when = parse_activity_time(text)
    if not when:
        return text  # No se entendió: se muestra tal cual se escribió
    ts = int(when.timestamp())
    # Discord lo muestra en la hora local de cada persona
    return f"<t:{ts}:t> · <t:{ts}:R>\n<t:{ts}:D>"


def _number(value):
    """'28000' -> '28.000'."""
    try:
        return f"{int(value):,}".replace(",", ".")
    except (TypeError, ValueError):
        return value


def add_boss_info_fields(embed, info, with_location=True):
    """Clase, vida, experiencia, cooldown y zona del boss (datos de TibiaWiki)."""
    if not info:
        return
    if info.get("bosstiaryclass"):
        embed.add_field(name="Clase", value=info["bosstiaryclass"], inline=True)
    if info.get("hp"):
        embed.add_field(name="Vida", value=_number(info["hp"]), inline=True)
    if info.get("exp"):
        embed.add_field(name="Experiencia", value=_number(info["exp"]), inline=True)
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


def _no_info(embed):
    embed.description = "No se encontró información de este boss en TibiaWiki."
    return embed


def build_abilities_embed(boss, info, image_url):
    embed = discord.Embed(title=f"{boss['name']} · Habilidades y debilidades", url=BOSSES_URL, color=BOSS_COLOR)
    if image_url:
        embed.set_thumbnail(url=image_url)
    embed.set_footer(text="Fuente: TibiaWiki")
    if not info:
        return _no_info(embed)

    if info.get("max_damage"):
        embed.add_field(name="Daño máximo", value=info["max_damage"], inline=False)
    if info.get("abilities"):
        chunk_fields(embed, "Habilidades", [f"• {a}" for a in info["abilities"]], separator="\n")
    else:
        embed.add_field(name="Habilidades", value="TibiaWiki no tiene sus habilidades registradas.", inline=False)
    embed.add_field(name="Débil a", value=", ".join(info.get("weak") or []) or "—", inline=True)
    embed.add_field(name="Resiste", value=", ".join(info.get("strong") or []) or "—", inline=True)
    embed.add_field(name="Inmune a", value=", ".join(info.get("immune") or []) or "—", inline=True)
    if info.get("heals"):
        embed.add_field(name="Se cura con", value=", ".join(info["heals"]), inline=False)
    if info.get("traits"):
        chunk_fields(embed, "Rasgos", [f"• {t}" for t in info["traits"]], separator="\n")
    return embed


def build_loot_embed(boss, info, image_url):
    embed = discord.Embed(title=f"{boss['name']} · Loot", url=BOSSES_URL, color=BOSS_COLOR)
    if image_url:
        embed.set_thumbnail(url=image_url)
    embed.set_footer(text="Fuente: TibiaWiki")
    if not info:
        return _no_info(embed)
    if not info.get("loot"):
        embed.description = "TibiaWiki no tiene la lista de loot de este boss."
        return embed
    total = sum(len(items) for items in info["loot"].values())
    embed.description = f"{total} objetos posibles."
    for rarity, items in info["loot"].items():
        chunk_fields(embed, f"{rarity} · {len(items)}", items, separator=", ")
    return embed


def build_activity_embed(boss, time_text, note, participants, organizer, image_url, info=None):
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
    if info and info.get("location"):
        embed.add_field(name="Zona", value=info["location"][:1024], inline=False)

    lines = [
        f"**[{d['char_name']}]({player_url(d['char_name'])})** · {d['owner']} · "
        f"Nivel {d.get('current_level', '?')} · {d.get('vocation', '')}"
        for d in participants
    ]
    chunk_fields(embed, f"Participantes · {len(participants)}", lines, separator="\n")

    if note:
        embed.add_field(name="Nota", value=note[:1024], inline=False)
    if image_url:
        embed.set_thumbnail(url=image_url)
    return embed
