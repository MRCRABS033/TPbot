"""
Lógica compartida para gestionar los personajes del grupo y los hunteds.
La usan tanto los comandos (!add, !hunted add, ...) como los botones del panel (!panel).
Cada función devuelve un Embed listo para enviar.
"""
import aiohttp
import discord

from config import HUNTED_PLAYERS, PLAYERS_STATE, WATCHED_PLAYERS, WORLD, logger
from services.charts import build_group_progress_chart
from services.online_embeds import player_url
from services.storage import save_data, save_hunteds
from services.tibia_api import fetch_player_data
from services.tibia_wiki import vocation_image_url

COLOR_GROUP = discord.Color.dark_teal()
COLOR_HUNTED = discord.Color.dark_red()


def _error(text):
    return discord.Embed(description=f"⚠️ {text}", color=discord.Color.dark_grey())


def _niveles(n):
    return "nivel" if abs(n) == 1 else "niveles"


def _chunk_fields(embed, title, entries, separator="\n\n"):
    """Agrega las entradas en uno o más campos (límite de Discord: 1024 caracteres por campo)."""
    chunk = []
    for entry in entries:
        if len(separator.join(chunk + [entry])) > 1020:
            embed.add_field(name=title, value=separator.join(chunk), inline=False)
            chunk, title = [], "​"
        chunk.append(entry)
    if chunk:
        embed.add_field(name=title, value=separator.join(chunk), inline=False)


# --- PERSONAJES DEL GRUPO ---
async def add_group_member(owner: str, character_name: str):
    owner = owner.strip()
    async with aiohttp.ClientSession() as session:
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


# --- HUNTEDS ---
async def add_hunted(character_name: str, added_by: str):
    async with aiohttp.ClientSession() as session:
        res = await fetch_player_data(session, character_name.strip())
    char_info = (res or {}).get("character_info")
    if not char_info:
        return _error(f"No se encontró al personaje **{character_name}** en Tibia.com.")

    name = res["official_name"]
    key = name.lower()
    if key in HUNTED_PLAYERS:
        return _error(f"**{name}** ya está en la lista de hunteds.")

    deaths = res.get("deaths", [])
    HUNTED_PLAYERS[key] = {
        "char_name": name,
        "tibia_link": res["tibia_link"],
        "world": char_info.get("world", "N/A"),
        "level": char_info.get("level", 0),
        "vocation": char_info.get("vocation", "N/A"),
        "added_by": added_by,
        # Última muerte conocida: solo se avisarán las muertes posteriores
        "last_death_time": deaths[0]["time"] if deaths else None,
    }
    save_hunteds()
    data = HUNTED_PLAYERS[key]
    logger.info(f"🎯 Hunted agregado: {name} (por {added_by})")

    embed = discord.Embed(
        title=f"{name} agregado a hunteds",
        url=player_url(name),
        description=f"Nivel {data['level']} · {data['vocation']}",
        color=COLOR_HUNTED,
    )
    embed.set_thumbnail(url=vocation_image_url(data["vocation"]))
    embed.set_footer(text=f"Mundo: {data['world']} · Agregado por {added_by}")
    if data["world"] != WORLD:
        embed.add_field(
            name="Atención",
            value=f"Está en **{data['world']}**. Los avisos de conexión y nivel solo funcionan en **{WORLD}**; "
                  f"las muertes sí se avisan.",
            inline=False,
        )
    return embed


def remove_hunted(character_name: str):
    data = HUNTED_PLAYERS.pop(character_name.strip().lower(), None)
    if not data:
        return _error(f"**{character_name}** no está en la lista de hunteds.")
    save_hunteds()
    logger.info(f"🗑️ Hunted eliminado: {data['char_name']}")
    return discord.Embed(
        title=f"{data['char_name']} eliminado de hunteds",
        color=discord.Color.dark_grey(),
    )


def build_hunted_list_embed(online_keys):
    if not HUNTED_PLAYERS:
        return _error("La lista de hunteds está vacía.")

    online_keys = online_keys or set()
    hunteds = sorted(HUNTED_PLAYERS.items(), key=lambda kv: kv[1].get("level", 0), reverse=True)

    def entry(data):
        return (
            f"**[{data['char_name']}]({player_url(data['char_name'])})**\n"
            f"  Nivel {data.get('level', '?')} · {data.get('vocation', 'N/A')}"
        )

    embed = discord.Embed(title="Lista de hunteds", color=COLOR_HUNTED)
    online = [entry(d) for k, d in hunteds if k in online_keys]
    offline = [entry(d) for k, d in hunteds if k not in online_keys]
    if online:
        _chunk_fields(embed, f"Conectados · {len(online)}", online)
    if offline:
        _chunk_fields(embed, f"Desconectados · {len(offline)}", offline)
    embed.set_footer(text=f"Mundo vigilado: {WORLD} · Total: {len(HUNTED_PLAYERS)}")
    return embed


def build_hunted_embed(character_name: str, online_keys):
    key = character_name.strip().lower()
    data = HUNTED_PLAYERS.get(key)
    if not data:
        return _error(f"**{character_name}** no está en la lista de hunteds.")

    is_online = key in (online_keys or set())
    embed = discord.Embed(
        title=data["char_name"],
        url=player_url(data["char_name"]),
        description="Conectado ahora" if is_online else "Desconectado",
        color=COLOR_HUNTED if is_online else discord.Color.dark_grey(),
    )
    embed.add_field(name="Vocación", value=data.get("vocation", "N/A"), inline=True)
    embed.add_field(name="Nivel", value=str(data.get("level", "?")), inline=True)
    embed.add_field(name="Mundo", value=data.get("world", "N/A"), inline=True)
    embed.set_thumbnail(url=vocation_image_url(data.get("vocation", "")))
    embed.set_footer(text=f"Agregado por {data.get('added_by', 'N/A')}")
    return embed
