"""#bazaar: embeds del panel, del catálogo en lista y de la ficha de un personaje."""
import discord

from src.channels.bazaar.service import enum_text, format_tc
from src.core.constants.bazaar import AUCTION_URL, BAZAAR_URL

COLOR = discord.Color.from_rgb(139, 92, 246)
PANEL_TITLE = "Bazar de personajes"


# --- VISTA EN LISTA ---
def build_list_text(entries):
    """Catálogo en texto: dos líneas por personaje, con enlace a la subasta y tiempo restante en vivo."""
    lines = []
    for code, auction in entries:
        bid = "actual" if "current" in str(enum_text(auction.bid_type)).lower() else "mínima"
        end = int(auction.auction_end.timestamp())
        lines.append(
            f"**[{code:02d}]** · [{auction.name}]({AUCTION_URL.format(auction_id=auction.auction_id)}) · "
            f"Nivel {auction.level} · {enum_text(auction.vocation)}\n"
            f"\u2003 {auction.world} · **{format_tc(auction.bid)}** ({bid}) · termina <t:{end}:R>"
        )
    return "\n\n".join(lines)


# --- FICHA DETALLADA ---
def build_details_embed(code, auction, details, has_outfit):
    bid_label = "Oferta actual" if "current" in str(enum_text(auction.bid_type)).lower() else "Oferta mínima"
    end = int(auction.auction_end.timestamp())
    embed = discord.Embed(
        title=f"[{code:02d}] {auction.name}",
        url=AUCTION_URL.format(auction_id=auction.auction_id),
        description=f"Nivel **{auction.level}** · {enum_text(auction.vocation)} · {auction.world}",
        color=COLOR,
    )
    embed.add_field(name=bid_label, value=format_tc(auction.bid), inline=True)
    embed.add_field(name="Termina", value=f"<t:{end}:f>\n<t:{end}:R>", inline=True)

    if details:
        g = details["general"]
        embed.add_field(name="Vida / Maná", value=f"{g.get('Hit Points', '?')} / {g.get('Mana', '?')}", inline=True)
        embed.add_field(name="Capacidad", value=g.get("Capacity", "?"), inline=True)
        embed.add_field(name="Velocidad", value=g.get("Speed", "?"), inline=True)
        embed.add_field(name="Blessings", value=g.get("Blessings", "?"), inline=True)

        if details["skills"]:
            skills = sorted(details["skills"], key=lambda s: s[1], reverse=True)
            embed.add_field(name="Skills", value="\n".join(f"{name}: **{level}**" for name, level in skills),
                            inline=True)

        extras = [
            ("Experiencia", g.get("Experience")),
            ("Achievement Points", g.get("Achievement Points")),
            ("Charm Points", f"{g.get('Available Charm Points', '?')} libres · {g.get('Spent Charm Points', '?')} usados"
             if g.get("Available Charm Points") else None),
            ("Boss Points", g.get("Boss Points")),
            ("Mounts / Outfits", f"{g.get('Mounts', '?')} / {g.get('Outfits', '?')}"),
            ("Gold", g.get("Gold")),
            ("Creado", g.get("Creation Date")),
            ("Transferencia", g.get("Regular World Transfer")),
        ]
        embed.add_field(name="Extras", value="\n".join(f"{k}: **{v}**" for k, v in extras if v), inline=True)
        embed.set_footer(text="Datos de tibia.com al momento de pedir la ficha")
    else:
        embed.set_footer(text="No se pudo leer la ficha completa en tibia.com; pulsa el título para verla allí")

    if has_outfit:
        embed.set_thumbnail(url="attachment://outfit.png")
    return embed


def build_panel_embed():
    embed = discord.Embed(
        title=PANEL_TITLE,
        url=BAZAAR_URL,
        description=(
            "Busca personajes en venta en el Char Bazaar de Tibia.\n"
            "Filtra por vocación, nivel, precio, mundo y tipo de PvP.\n\n"
            "Los resultados salen como catálogo: cada personaje tiene un **código** para ver su ficha completa."
        ),
        color=COLOR,
    )
    return embed
