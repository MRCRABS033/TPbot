"""
Lógica para gestionar los personajes del grupo.
La usan tanto los comandos (!add, ...) como los botones del panel (!panel).
Cada función devuelve un Embed listo para enviar.
"""
import discord

from src.core.config import PLAYERS_STATE, WATCHED_PLAYERS, logger
from src.shared.charts import build_group_progress_chart
from src.shared.online_embeds import player_url
from services.storage import save_data
from src.shared.embed_helpers import chunk_fields as _chunk_fields, error_embed as _error
from src.api.http import create_session
from src.api.tibiadata import fetch_player_data
from src.api.tibiawiki import vocation_image_url

COLOR_GROUP = discord.Color.dark_teal()


def _niveles(n):
    return "nivel" if abs(n) == 1 else "niveles"


# --- PERSONAJES DEL GRUPO ---
async def add_group_member(owner: str, character_name: str):
    owner = owner.strip()
    async with create_session() as session:
        res = await fetch_player_data(session, character_name.strip())
    if not res or not res.get("character_info"):
        return _error(f"No se encontró al personaje **{character_name}** en Tibia.com.")

    char_info = res["character_info"]
    level = char_info.get("level", 0)
    vocation = char_info.get("vocation", "N/A")
    key = res["official_name"].lower()

    # Si el personaje ya existía, se conservan sus niveles históricos
    existing = WATCHED_PLAYERS.get(key, {})
    WATCHED_PLAYERS[key] = {
        "char_name": res["official_name"],
        "tibia_link": res["tibia_link"],
        "owner": owner,
        "initial_level": existing.get("initial_level", level),
        "weekly_start_level": existing.get("weekly_start_level", level),
        "current_level": level,
        "vocation": vocation,
    }
    save_data()
    data = WATCHED_PLAYERS[key]
    logger.info(f"✅ Personaje '{data['char_name']}' registrado para {owner}")

    embed = discord.Embed(
        title=f"{data['char_name']} {'actualizado' if existing else 'agregado al grupo'}",
        url=player_url(data["char_name"]),
        description=f"Nivel {level} · {vocation}",
        color=COLOR_GROUP,
    )
    embed.add_field(name="Dueño", value=owner, inline=True)
    embed.add_field(name="Nivel de ingreso", value=str(data["initial_level"]), inline=True)
    embed.set_thumbnail(url=vocation_image_url(vocation))
    embed.set_footer(text=f"Mundo: {char_info.get('world', 'N/A')}")
    return embed


def remove_group_member(character_name: str):
    key = character_name.strip().lower()
    data = WATCHED_PLAYERS.pop(key, None)
    if not data:
        return _error(f"El personaje **{character_name}** no está en el grupo.")
    PLAYERS_STATE.pop(key, None)
    save_data()
    logger.info(f"🗑️ Personaje '{data['char_name']}' eliminado del grupo")
    return discord.Embed(
        title=f"{data['char_name']} eliminado del grupo",
        description=f"Dueño: {data['owner']}",
        color=discord.Color.dark_grey(),
    )


def build_group_list_embed():
    """Lista del grupo agrupada por dueño, con el nivel actual y el de ingreso de cada personaje."""
    if not WATCHED_PLAYERS:
        return discord.Embed(
            title="Personajes del grupo",
            description="La lista está vacía. Usa ＋ Agregar para añadir un personaje.",
            color=discord.Color.dark_grey(),
        )

    embed = discord.Embed(title="Personajes del grupo", color=COLOR_GROUP)
    grouped = {}
    for data in sorted(WATCHED_PLAYERS.values(), key=lambda d: d.get("current_level", 0), reverse=True):
        grouped.setdefault(data["owner"], []).append(
            f"**[{data['char_name']}]({player_url(data['char_name'])})** · {data.get('vocation', '')}\n"
            f"  Nivel actual {data.get('current_level', '?')} · ingresó en {data.get('initial_level', '?')}"
        )
    for owner in sorted(grouped, key=str.lower):
        if len(embed.fields) >= 24:  # Límite de Discord: 25 campos por embed
            embed.add_field(name="…", value=f"y {len(grouped) - 24} dueños más", inline=False)
            break
        _chunk_fields(embed, owner, grouped[owner])

    embed.set_footer(text=f"Total: {len(WATCHED_PLAYERS)} personajes · {len(grouped)} dueños")
    return embed


def build_group_progress():
    """
    Progreso del grupo como gráfica: (embed, archivo PNG). Si el grupo está vacío, (embed, None).
    """
    chart = build_group_progress_chart()
    if not chart:
        return build_group_list_embed(), None

    owners = {d["owner"] for d in WATCHED_PLAYERS.values()}
    embed = discord.Embed(
        title="Progreso del grupo",
        description="Niveles ganados por cada personaje desde que ingresó al grupo.",
        color=COLOR_GROUP,
    )
    embed.set_image(url=f"attachment://{chart.filename}")
    embed.set_footer(text=f"Total: {len(WATCHED_PLAYERS)} personajes · {len(owners)} dueños")
    return embed, chart


def build_group_panel_embed():
    """Tarjeta fija del panel del grupo en #tpbot (la información se ve al pulsar los botones)."""
    owners = {d["owner"] for d in WATCHED_PLAYERS.values()}
    embed = discord.Embed(
        title="Panel del grupo",
        description=(
            "Gestiona los personajes del grupo con los botones.\n"
            "**Listar** muestra el nivel de ingreso y el actual de cada uno."
        ),
        color=COLOR_GROUP,
    )
    embed.set_footer(text=f"{len(WATCHED_PLAYERS)} personajes · {len(owners)} dueños")
    return embed


def build_group_member_embed(character_name: str):
    data = WATCHED_PLAYERS.get(character_name.strip().lower())
    if not data:
        return _error(f"El personaje **{character_name}** no está en el grupo.")

    curr = data.get("current_level", 0)
    initial = data.get("initial_level", curr)
    weekly = curr - data.get("weekly_start_level", curr)
    total = curr - initial
    embed = discord.Embed(
        title=data["char_name"],
        url=player_url(data["char_name"]),
        description=f"Dueño: {data['owner']}",
        color=COLOR_GROUP,
    )
    embed.add_field(name="Vocación", value=data.get("vocation", "N/A"), inline=True)
    embed.add_field(name="Nivel actual", value=str(curr), inline=True)
    embed.add_field(name="Nivel de ingreso", value=str(initial), inline=True)
    embed.add_field(
        name="Progreso",
        value=f"Esta semana: **{weekly:+}** {_niveles(weekly)}\nDesde que ingresó: **{total:+}** {_niveles(total)}",
        inline=False,
    )
    embed.set_thumbnail(url=vocation_image_url(data.get("vocation", "")))
    return embed
