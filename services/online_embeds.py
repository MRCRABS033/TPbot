import discord

from services.tibia_wiki import VOCATION_OUTFITS, vocation_image_url, vocation_key

VOCATION_GROUP_NAMES = {
    "knight": "Knights",
    "paladin": "Paladins",
    "sorcerer": "Sorcerers",
    "druid": "Druids",
    "monk": "Monks",
    "none": "Sin vocación",
}


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
            title=title,
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
            title=f"{VOCATION_GROUP_NAMES[key]} · {len(members)}",
            description="\n\n".join(entries),
            color=color,
        )
        embed.set_thumbnail(url=vocation_image_url(key))
        embeds.append(embed)

    embeds[0].set_author(name=title)
    embeds[-1].set_footer(text=footer)
    embeds[-1].timestamp = discord.utils.utcnow()
    return embeds
