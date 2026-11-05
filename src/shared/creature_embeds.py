"""Embeds de habilidades y loot (datos de TibiaWiki). Los usan #bosses y #boosted-creature."""
import discord

from src.shared.embed_helpers import chunk_fields


def _base_embed(title, image_url, url, color):
    embed = discord.Embed(title=title, url=url, color=color)
    if image_url:
        embed.set_thumbnail(url=image_url)
    embed.set_footer(text="Fuente: TibiaWiki")
    return embed


def _no_info(embed, subject):
    embed.description = f"No se encontró información de {subject} en TibiaWiki."
    return embed


def build_abilities_embed(name, info, image_url, url, color, subject):
    """`subject` es cómo se nombra en los avisos: 'este boss', 'esta criatura'."""
    embed = _base_embed(f"{name} · Habilidades y debilidades", image_url, url, color)
    if not info:
        return _no_info(embed, subject)

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


def build_loot_embed(name, info, image_url, url, color, subject):
    embed = _base_embed(f"{name} · Loot", image_url, url, color)
    if not info:
        return _no_info(embed, subject)
    if not info.get("loot"):
        embed.description = f"TibiaWiki no tiene la lista de loot de {subject}."
        return embed
    total = sum(len(items) for items in info["loot"].values())
    embed.description = f"{total} objetos posibles."
    for rarity, items in info["loot"].items():
        chunk_fields(embed, f"{rarity} · {len(items)}", items, separator=", ")
    return embed
