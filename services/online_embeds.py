import discord

from services.tibia_wiki import VOCATION_OUTFITS, vocation_key
from services.vocation_thumbnails import vocation_thumbnail

VOCATION_GROUP_NAMES = {
    "knight": "Knights",
    "paladin": "Paladins",
    "sorcerer": "Sorcerers",
    "druid": "Druids",
    "monk": "Monks",
    "none": "Sin vocación",
}

LIST_MIN_WIDTH_CHARS = 42
BLANK = "⠀"  # Espacio invisible que Discord no recorta
NBSP = " "  # Espacio que no parte la línea: el número queda junto al nombre del rol


def _wide(title):
    """Rellena el título con espacios invisibles hasta LIST_MIN_WIDTH_CHARS (alineado a la izquierda)."""
    return title + BLANK * max(0, LIST_MIN_WIDTH_CHARS - len(title))


def player_url(name: str) -> str:
    return f"https://www.tibia.com/community/?subtopic=characters&name={name.replace(' ', '+')}"


def build_grouped_online_embeds(online_list, title, footer, color, empty_text):
    """
    Lista de personajes conectados agrupada por vocación: un embed por vocación (todos en
    el mismo mensaje), cada uno con el outfit de su vocación en la esquina.
    online_list: [(nivel, vocación, texto_de_la_entrada), ...]. Dentro de cada grupo, de mayor a menor nivel.
    """
    if not online_list:
        embed = discord.Embed(
            title=_wide(title),
            description=empty_text,
            color=discord.Color.dark_grey(),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text=footer)
        return [embed]

    groups = {}
    for lvl, voc, entry in online_list:
        groups.setdefault(vocation_key(voc), []).append((lvl, entry))

    embeds = []
    total_chars = 0
    for key in list(VOCATION_OUTFITS) + ["none"]:
        if key not in groups:
            continue
        members = sorted(groups[key], key=lambda x: x[0], reverse=True)
        entries = []
        for _, entry in members:
            # Límite de Discord: 6000 caracteres sumando todos los embeds del mensaje
            if total_chars + len(entry) > 5000:
                entries.append("…")
                break
            entries.append(entry)
            total_chars += len(entry) + 2

        embed = discord.Embed(
            title=_wide(f"{VOCATION_GROUP_NAMES[key]}{NBSP}·{NBSP}{len(members)}"),
            description="\n\n".join(entries),
            color=color,
        )
        embed.set_thumbnail(url=vocation_thumbnail(key))
        embeds.append(embed)

    embeds[0].set_author(name=title)
    embeds[-1].set_footer(text=footer)
    embeds[-1].timestamp = discord.utils.utcnow()
    return embeds
