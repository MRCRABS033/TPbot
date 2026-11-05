"""
#hunteds: la lista de hunteds online lleva los botones del panel (agregar, buscar, listar, eliminar).
Los botones siguen funcionando aunque el bot se reinicie (vistas persistentes).
"""
import discord
from discord import app_commands
from discord.ext import commands, tasks

from src.api import tibiadata
from src.api.tibiawiki import vocation_image_url
from src.channels.hunteds.service import ALREADY_LISTED, NOT_FOUND, DeathAlert, HuntedService
from services.storage import save_hunteds
from src.core.channel_names import CHANNEL_HUNTEDS
from src.core.config import HUNTED_PLAYERS, WORLD, logger
from src.shared.death_format import format_death_time, format_killers
from src.shared.discord_utils import broadcast_embed, send_or_update_embed
from src.shared.embed_helpers import chunk_fields, error_embed
from src.shared.login_times import LoginTracker, parse_api_time
from src.shared.online_embeds import build_grouped_online_embeds, player_url
from src.shared.panel_ui import BUTTON_STYLE, ICON_ADD, ICON_LIST, ICON_REMOVE, ICON_SEARCH, MAX_SELECT_OPTIONS, remove_view
from src.shared.task_utils import resilient

COLOR_HUNTED = discord.Color.dark_red()


# --- EMBEDS DE LA LISTA ---
async def add_hunted(service, character_name, added_by):
    result = await service.add(character_name, added_by)
    if result.status == NOT_FOUND:
        return error_embed(f"No se encontró al personaje **{character_name}** en Tibia.com.")
    if result.status == ALREADY_LISTED:
        return error_embed(f"**{result.name}** ya está en la lista de hunteds.")

    name, data = result.name, result.data
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


def remove_hunted(service, character_name):
    data = service.remove(character_name)
    if not data:
        return error_embed(f"**{character_name}** no está en la lista de hunteds.")
    logger.info(f"🗑️ Hunted eliminado: {data['char_name']}")
    return discord.Embed(
        title=f"{data['char_name']} eliminado de hunteds",
        color=discord.Color.dark_grey(),
    )


def build_hunted_list_embed(service):
    if not service.has_players():
        return error_embed("La lista de hunteds está vacía.")

    def entry(data):
        return (
            f"**[{data['char_name']}]({player_url(data['char_name'])})**\n"
            f"  Nivel {data.get('level', '?')} · {data.get('vocation', 'N/A')}"
        )

    embed = discord.Embed(title="Lista de hunteds", color=COLOR_HUNTED)
    online, offline = service.split_by_connection()
    if online:
        chunk_fields(embed, f"Conectados · {len(online)}", [entry(d) for d in online])
    if offline:
        chunk_fields(embed, f"Desconectados · {len(offline)}", [entry(d) for d in offline])
    embed.set_footer(text=f"Mundo vigilado: {WORLD} · Total: {service.player_count()}")
    return embed


def build_hunted_embed(service, character_name):
    key, data = service.get(character_name)
    if not data:
        return error_embed(f"**{character_name}** no está en la lista de hunteds.")

    is_online = key in service.online_keys()
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


# --- FORMULARIOS ---
class HuntedAddModal(discord.ui.Modal, title="Agregar hunted"):
    character = discord.ui.TextInput(label="Personaje", placeholder="Nombre exacto en Tibia", max_length=40)

    def __init__(self, service):
        super().__init__()
        self.service = service

    async def on_submit(self, interaction: discord.Interaction):
        logger.info(f"🎛️ Panel: {interaction.user} agrega hunted '{self.character.value}'")
        await interaction.response.defer(thinking=True)
        await interaction.followup.send(embed=await add_hunted(self.service, self.character.value, str(interaction.user)))


class HuntedSearchModal(discord.ui.Modal, title="Buscar hunted"):
    character = discord.ui.TextInput(label="Personaje", placeholder="Nombre del hunted", max_length=40)

    def __init__(self, service):
        super().__init__()
        self.service = service

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            embed=build_hunted_embed(self.service, self.character.value), ephemeral=True
        )


class HuntedRemoveModal(discord.ui.Modal, title="Eliminar hunted"):
    character = discord.ui.TextInput(label="Personaje", placeholder="Nombre del hunted", max_length=40)

    def __init__(self, service):
        super().__init__()
        self.service = service

    async def on_submit(self, interaction: discord.Interaction):
        logger.info(f"🎛️ Panel: {interaction.user} elimina hunted '{self.character.value}'")
        await interaction.response.send_message(embed=remove_hunted(self.service, self.character.value))


# --- PANEL ---
class HuntedPanelView(discord.ui.View):
    """Panel de hunteds (se publica en #hunteds)."""

    def __init__(self, service):
        super().__init__(timeout=None)
        self.service = service

    @discord.ui.button(label=f"{ICON_ADD}  Agregar", style=BUTTON_STYLE, custom_id="hunted_panel:add")
    async def add(self, interaction: discord.Interaction, button):
        await interaction.response.send_modal(HuntedAddModal(self.service))

    @discord.ui.button(label=f"{ICON_SEARCH}  Buscar", style=BUTTON_STYLE, custom_id="hunted_panel:search")
    async def search(self, interaction: discord.Interaction, button):
        await interaction.response.send_modal(HuntedSearchModal(self.service))

    @discord.ui.button(label=f"{ICON_LIST}  Listar", style=BUTTON_STYLE, custom_id="hunted_panel:list")
    async def list(self, interaction: discord.Interaction, button):
        await interaction.response.send_message(embed=build_hunted_list_embed(self.service), ephemeral=True)

    @discord.ui.button(label=f"{ICON_REMOVE}  Eliminar", style=BUTTON_STYLE, custom_id="hunted_panel:remove")
    async def remove(self, interaction: discord.Interaction, button):
        if not self.service.has_players():
            await interaction.response.send_message(embed=build_hunted_list_embed(self.service), ephemeral=True)
        elif self.service.player_count() > MAX_SELECT_OPTIONS:
            await interaction.response.send_modal(HuntedRemoveModal(self.service))
        else:
            await interaction.response.send_message(
                view=remove_view(
                    self.service.players,
                    lambda name: remove_hunted(self.service, name),
                    "Elige el hunted a eliminar",
                ),
                ephemeral=True,
            )


class HuntedTracker(commands.Cog):
    def __init__(self, bot, service):
        self.bot = bot
        self.service = service
        self.list_message = {}
        # Registra la vista para que los botones ya publicados sigan funcionando tras reiniciar
        bot.add_view(HuntedPanelView(service))
        self.track_hunteds.start()

    def cog_unload(self):
        self.track_hunteds.cancel()

    # --- COMANDOS ---
    @commands.hybrid_group(name="hunted", fallback="list", invoke_without_command=True,
                           description="Muestra la lista de hunteds")
    async def hunted(self, ctx):
        """!hunted -> muestra todos los hunteds, separados en conectados y desconectados."""
        logger.info(f"📩 Comando !hunted ejecutado por {ctx.author}")
        await ctx.send(embed=build_hunted_list_embed(self.service))

    @hunted.command(name="add", description="Agrega un personaje a la lista de hunteds")
    @app_commands.describe(character_name="Nombre del personaje en Tibia")
    async def hunted_add(self, ctx, *, character_name: str):
        await ctx.defer()
        logger.info(f"📩 Comando !hunted add ejecutado por {ctx.author} para: '{character_name}'")
        await ctx.send(embed=await add_hunted(self.service, character_name, str(ctx.author)))

    @hunted.command(name="remove", description="Elimina un personaje de la lista de hunteds")
    @app_commands.describe(character_name="Nombre del personaje")
    async def hunted_remove(self, ctx, *, character_name: str):
        logger.info(f"📩 Comando !hunted remove ejecutado por {ctx.author} para: '{character_name}'")
        await ctx.send(embed=remove_hunted(self.service, character_name))

    # --- TAREA AUTOMÁTICA ---
    @resilient
    @tasks.loop(seconds=60)
    async def track_hunteds(self):
        if not self.service.has_players():
            # Sin hunteds se publica igual la lista vacía, para que estén los botones de agregar
            await send_or_update_embed(
                self.bot, self.list_message, CHANNEL_HUNTEDS, self._build_list_embeds([]), view=HuntedPanelView(self.service)
            )
            return
        logger.info(f"🎯 Verificando hunteds en {WORLD}...")

        result = await self.service.check(WORLD)

        alerts = [
            self._build_death_embed(alert.data, alert.death, alert.char_info)
            if isinstance(alert, DeathAlert)
            else self._build_level_embed(alert.data, alert.old_level, alert.new_level, alert.vocation)
            for alert in result.alerts
        ]
        online_list = [
            (
                player.level,
                player.vocation,
                f"**[{player.name}]({player_url(player.name)})**\n"
                f"  Nivel {player.level} · {player.vocation} · {self.service.login_text(player.key)}",
            )
            for player in result.online
        ]
        list_embeds = self._build_list_embeds(online_list)

        # Los avisos de nivel y muerte van como mensajes aparte; la lista siempre se edita en el mismo mensaje
        for alert in alerts:
            await broadcast_embed(self.bot, CHANNEL_HUNTEDS, alert)
        await send_or_update_embed(self.bot, self.list_message, CHANNEL_HUNTEDS, list_embeds, view=HuntedPanelView(self.service))

    def _build_list_embeds(self, online_list):
        """Lista de hunteds online, con los botones del panel debajo (agregar, buscar, listar, eliminar)."""
        return build_grouped_online_embeds(
            online_list,
            title="Hunteds online",
            footer=f"Mundo: {WORLD} · Conectados: {len(online_list)} de {self.service.player_count()}",
            color=discord.Color.dark_red(),
            empty_text=(
                "Ningún hunted está conectado."
                if self.service.has_players()
                else "La lista está vacía. Usa ＋ Agregar para añadir un hunted."
            ),
        )

    @track_hunteds.before_loop
    async def before_track(self):
        await self.bot.wait_until_ready()

    # --- AVISOS ---
    @staticmethod
    def _build_level_embed(data, old_level, new_level, vocation):
        logger.info(f"🎯 Hunted {data['char_name']}: nivel {old_level} -> {new_level}")
        up = new_level > old_level
        embed = discord.Embed(
            title=f"Hunted {data['char_name']} {'subió' if up else 'bajó'} a nivel {new_level}",
            url=player_url(data["char_name"]),
            color=discord.Color.dark_orange(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Vocación", value=vocation, inline=True)
        embed.add_field(name="Nivel", value=f"{old_level} → **{new_level}**", inline=True)
        embed.set_thumbnail(url=vocation_image_url(vocation))
        embed.set_footer(text=f"Mundo: {WORLD}")
        return embed

    @staticmethod
    def _build_death_embed(data, death, char_info):
        logger.info(f"🎯 Hunted muerto: {data['char_name']}")
        killers = death.get("killers", [])
        embed = discord.Embed(
            title=f"Hunted {data['char_name']} murió a nivel {death.get('level', '?')}",
            url=player_url(data["char_name"]),
            color=discord.Color.dark_green(),
        )
        embed.add_field(name="Fecha", value=format_death_time(death.get("time")), inline=False)
        if killers:
            embed.add_field(name="Asesinos", value=format_killers(killers, {}), inline=False)
        else:
            embed.add_field(name="Causa", value=death.get("reason", "Desconocida"), inline=False)
        if death.get("assists"):
            embed.add_field(name="Asistencias", value=format_killers(death["assists"], {}), inline=False)
        embed.set_thumbnail(url=vocation_image_url(char_info.get("vocation", data.get("vocation", ""))))
        embed.set_footer(text=f"Mundo: {char_info.get('world', data.get('world', 'Desconocido'))}")
        return embed


async def setup(bot):
    service = HuntedService(tibiadata, HUNTED_PLAYERS, save_hunteds, LoginTracker(), parse_api_time)
    await bot.add_cog(HuntedTracker(bot, service))
