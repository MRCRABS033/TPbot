"""
#tpbot: panel de control con botones (como los bots de música) para gestionar
los personajes del grupo sin escribir comandos, y los comandos equivalentes.

El panel siempre queda al final del canal. La lista (con nivel de ingreso y actual)
se ve al pulsar Listar. El canal se limpia cada 12 horas (ver src/admin/cleanup.py).
Los botones siguen funcionando aunque el bot se reinicie (vistas persistentes).
"""
import discord
from discord import app_commands
from discord.ext import commands, tasks

from src.api import tibiadata
from src.api.tibiawiki import vocation_image_url
from src.channels.tpbot.chart import build_group_progress_chart
from src.channels.tpbot.service import GroupService
from services.storage import save_data
from src.core.channel_names import CHANNEL_TPBOT, find_channel
from src.core.config import PLAYERS_STATE, WATCHED_PLAYERS, logger
from src.shared.discord_utils import send_or_update_embed
from src.shared.embed_helpers import chunk_fields, error_embed
from src.shared.online_embeds import player_url
from src.shared.panel_ui import BUTTON_STYLE, ICON_ADD, ICON_LIST, ICON_REMOVE, ICON_SEARCH, MAX_SELECT_OPTIONS, remove_view
from src.shared.task_utils import resilient

COLOR_GROUP = discord.Color.dark_teal()


def _niveles(n):
    return "nivel" if abs(n) == 1 else "niveles"


# --- EMBEDS ---
async def add_group_member(service, owner, character_name):
    added = await service.add(owner, character_name)
    if not added:
        return error_embed(f"No se encontró al personaje **{character_name}** en Tibia.com.")

    data = added.data
    logger.info(f"✅ Personaje '{data['char_name']}' registrado para {data['owner']}")
    embed = discord.Embed(
        title=f"{data['char_name']} {'actualizado' if added.existed else 'agregado al grupo'}",
        url=player_url(data["char_name"]),
        description=f"Nivel {data['current_level']} · {data['vocation']}",
        color=COLOR_GROUP,
    )
    embed.add_field(name="Dueño", value=data["owner"], inline=True)
    embed.add_field(name="Nivel de ingreso", value=str(data["initial_level"]), inline=True)
    embed.set_thumbnail(url=vocation_image_url(data["vocation"]))
    embed.set_footer(text=f"Mundo: {added.char_info.get('world', 'N/A')}")
    return embed


def remove_group_member(service, character_name):
    data = service.remove(character_name)
    if not data:
        return error_embed(f"El personaje **{character_name}** no está en el grupo.")
    logger.info(f"🗑️ Personaje '{data['char_name']}' eliminado del grupo")
    return discord.Embed(
        title=f"{data['char_name']} eliminado del grupo",
        description=f"Dueño: {data['owner']}",
        color=discord.Color.dark_grey(),
    )


def build_group_list_embed(service):
    """Lista del grupo agrupada por dueño, con el nivel actual y el de ingreso de cada personaje."""
    if not service.has_players():
        return discord.Embed(
            title="Personajes del grupo",
            description="La lista está vacía. Usa ＋ Agregar para añadir un personaje.",
            color=discord.Color.dark_grey(),
        )

    embed = discord.Embed(title="Personajes del grupo", color=COLOR_GROUP)
    grouped = service.by_owner()
    for owner in sorted(grouped, key=str.lower):
        if len(embed.fields) >= 24:  # Límite de Discord: 25 campos por embed
            embed.add_field(name="…", value=f"y {len(grouped) - 24} dueños más", inline=False)
            break
        chunk_fields(embed, owner, [
            f"**[{data['char_name']}]({player_url(data['char_name'])})** · {data.get('vocation', '')}\n"
            f"  Nivel actual {data.get('current_level', '?')} · ingresó en {data.get('initial_level', '?')}"
            for data in grouped[owner]
        ])

    embed.set_footer(text=f"Total: {service.player_count()} personajes · {len(grouped)} dueños")
    return embed


def build_group_progress(service):
    """Progreso del grupo como gráfica: (embed, archivo PNG). Si el grupo está vacío, (embed, None)."""
    chart = build_group_progress_chart(service.players)
    if not chart:
        return build_group_list_embed(service), None

    embed = discord.Embed(
        title="Progreso del grupo",
        description="Niveles ganados por cada personaje desde que ingresó al grupo.",
        color=COLOR_GROUP,
    )
    embed.set_image(url=f"attachment://{chart.filename}")
    embed.set_footer(text=f"Total: {service.player_count()} personajes · {service.owner_count()} dueños")
    return embed, chart


def build_group_panel_embed(service):
    """Tarjeta fija del panel del grupo en #tpbot (la información se ve al pulsar los botones)."""
    embed = discord.Embed(
        title="Panel del grupo",
        description=(
            "Gestiona los personajes del grupo con los botones.\n"
            "**Listar** muestra el nivel de ingreso y el actual de cada uno."
        ),
        color=COLOR_GROUP,
    )
    embed.set_footer(text=f"{service.player_count()} personajes · {service.owner_count()} dueños")
    return embed


def build_group_member_embed(service, character_name):
    data = service.get(character_name)
    if not data:
        return error_embed(f"El personaje **{character_name}** no está en el grupo.")

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


# --- FORMULARIOS ---
class GroupAddModal(discord.ui.Modal, title="Agregar personaje al grupo"):
    owner = discord.ui.TextInput(label="Dueño", placeholder="Ej: Miguel", max_length=50)
    character = discord.ui.TextInput(label="Personaje", placeholder="Nombre exacto en Tibia", max_length=40)

    def __init__(self, service):
        super().__init__()
        self.service = service

    async def on_submit(self, interaction: discord.Interaction):
        logger.info(f"🎛️ Panel: {interaction.user} agrega '{self.character.value}' al grupo")
        await interaction.response.defer(thinking=True)
        await interaction.followup.send(
            embed=await add_group_member(self.service, self.owner.value, self.character.value)
        )


class GroupSearchModal(discord.ui.Modal, title="Buscar personaje del grupo"):
    character = discord.ui.TextInput(label="Personaje", placeholder="Nombre del personaje", max_length=40)

    def __init__(self, service):
        super().__init__()
        self.service = service

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            embed=build_group_member_embed(self.service, self.character.value), ephemeral=True
        )


class GroupRemoveModal(discord.ui.Modal, title="Eliminar personaje del grupo"):
    character = discord.ui.TextInput(label="Personaje", placeholder="Nombre del personaje", max_length=40)

    def __init__(self, service):
        super().__init__()
        self.service = service

    async def on_submit(self, interaction: discord.Interaction):
        logger.info(f"🎛️ Panel: {interaction.user} elimina '{self.character.value}' del grupo")
        await interaction.response.send_message(embed=remove_group_member(self.service, self.character.value))


# --- PANEL ---
class GroupPanelView(discord.ui.View):
    """Panel de personajes del grupo (se publica en #tpbot)."""

    def __init__(self, service):
        super().__init__(timeout=None)  # Persistente: los botones no caducan
        self.service = service

    @discord.ui.button(label=f"{ICON_ADD}  Agregar", style=BUTTON_STYLE, custom_id="group_panel:add")
    async def add(self, interaction: discord.Interaction, button):
        await interaction.response.send_modal(GroupAddModal(self.service))

    @discord.ui.button(label=f"{ICON_SEARCH}  Buscar", style=BUTTON_STYLE, custom_id="group_panel:search")
    async def search(self, interaction: discord.Interaction, button):
        await interaction.response.send_modal(GroupSearchModal(self.service))

    @discord.ui.button(label=f"{ICON_LIST}  Listar", style=BUTTON_STYLE, custom_id="group_panel:list")
    async def list(self, interaction: discord.Interaction, button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        embed, chart = build_group_progress(self.service)
        if chart:
            await interaction.followup.send(embed=embed, file=chart, ephemeral=True)
        else:
            await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label=f"{ICON_REMOVE}  Eliminar", style=BUTTON_STYLE, custom_id="group_panel:remove")
    async def remove(self, interaction: discord.Interaction, button):
        if not self.service.has_players():
            await interaction.response.send_message(embed=build_group_list_embed(self.service), ephemeral=True)
        elif self.service.player_count() > MAX_SELECT_OPTIONS:
            await interaction.response.send_modal(GroupRemoveModal(self.service))
        else:
            await interaction.response.send_message(
                view=remove_view(
                    self.service.players,
                    lambda name: remove_group_member(self.service, name),
                    "Elige el personaje a eliminar",
                ),
                ephemeral=True,
            )


class Panel(commands.Cog):
    def __init__(self, bot, service):
        self.bot = bot
        self.service = service
        self.group_list_message = {}
        # Registra la vista para que los botones ya publicados sigan funcionando tras reiniciar
        bot.add_view(GroupPanelView(service))
        self.update_group_list.start()

    def cog_unload(self):
        self.update_group_list.cancel()

    # --- PANEL DEL GRUPO (siempre al final de #tpbot) ---
    @resilient
    @tasks.loop(seconds=60)
    async def update_group_list(self):
        await send_or_update_embed(
            self.bot,
            self.group_list_message,
            CHANNEL_TPBOT,
            build_group_panel_embed(self.service),
            view=GroupPanelView(self.service),
            keep_last=True,
        )

    @update_group_list.before_loop
    async def before_update_group_list(self):
        await self.bot.wait_until_ready()

    @commands.hybrid_command(name="panel", description="Vuelve a poner el panel del grupo al final de #tpbot")
    async def panel(self, ctx):
        logger.info(f"📩 Comando !panel ejecutado por {ctx.author}")
        await self.update_group_list()
        channel = find_channel(ctx.guild.text_channels, CHANNEL_TPBOT)
        if channel:
            await ctx.send(f"✅ Panel del grupo actualizado en {channel.mention}.")
        else:
            await ctx.send(f"⚠️ No se encontró el canal **#{CHANNEL_TPBOT}**.")


# --- COMANDOS ---
class AddCommand(commands.Cog):
    def __init__(self, bot, service):
        self.bot = bot
        self.service = service

    @commands.hybrid_command(name="add", description="Agrega un personaje a la lista de monitoreo")
    @app_commands.describe(owner="Dueño del personaje", character_name="Nombre del personaje en Tibia")
    async def add_player(self, ctx, owner: str, *, character_name: str):
        await ctx.defer()
        logger.info(f"📩 Comando !add ejecutado por {ctx.author} para: '{character_name}' (Dueño: {owner})")
        await ctx.send(embed=await add_group_member(self.service, owner, character_name))


class RemoveCommand(commands.Cog):
    def __init__(self, bot, service):
        self.bot = bot
        self.service = service

    @commands.hybrid_command(name="remove", description="Elimina un personaje de la lista de monitoreo")
    @app_commands.describe(character_name="Nombre del personaje")
    async def remove_player(self, ctx, *, character_name: str):
        logger.info(f"📩 Comando !remove ejecutado por {ctx.author} para: '{character_name}'")
        await ctx.send(embed=remove_group_member(self.service, character_name))


class ListCommands(commands.Cog):
    def __init__(self, bot, service):
        self.bot = bot
        self.service = service

    @commands.hybrid_command(name="list", description="Muestra todos los personajes monitoreados")
    async def list_players(self, ctx):
        logger.info(f"📩 Comando !list ejecutado por {ctx.author}")
        embed, chart = build_group_progress(self.service)
        if chart:
            await ctx.send(embed=embed, file=chart)
        else:
            await ctx.send(embed=embed)


class WhoisCommands(commands.Cog):
    def __init__(self, bot, service):
        self.bot = bot
        self.service = service

    @commands.hybrid_command(name="whois", description="Muestra el dueño y el progreso de un personaje")
    @app_commands.describe(character_name="Nombre del personaje")
    async def whois_char(self, ctx, *, character_name: str):
        logger.info(f"📩 Comando !whois ejecutado por {ctx.author} para el personaje: '{character_name}'")
        await ctx.send(embed=build_group_member_embed(self.service, character_name))


async def setup(bot):
    service = GroupService(tibiadata, WATCHED_PLAYERS, PLAYERS_STATE, save_data)
    await bot.add_cog(Panel(bot, service))
    await bot.add_cog(AddCommand(bot, service))
    await bot.add_cog(RemoveCommand(bot, service))
    await bot.add_cog(ListCommands(bot, service))
    await bot.add_cog(WhoisCommands(bot, service))
