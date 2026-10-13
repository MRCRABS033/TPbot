"""
Panel de control con botones (como los bots de música) para gestionar
los personajes del grupo y los hunteds sin escribir comandos.

- #tpbot: el panel del grupo siempre queda al final del canal. La lista (con nivel de ingreso
  y actual) se ve al pulsar Listar. El canal se limpia cada 12 horas (ver cogs/cleanup.py).
- #hunteds: la lista de hunteds online lleva los botones de hunteds (ver hunted_tracker).
Los botones siguen funcionando aunque el bot se reinicie (vistas persistentes).
"""
import discord
from discord.ext import commands, tasks

from channels import CHANNEL_TPBOT, find_channel
from config import HUNTED_PLAYERS, WATCHED_PLAYERS, logger
from services.task_utils import resilient
from services import roster
from services.discord_utils import send_or_update_embed

MAX_SELECT_OPTIONS = 25  # Límite de Discord por menú desplegable


def _hunted_online_keys(interaction: discord.Interaction):
    cog = interaction.client.get_cog("HuntedTracker")
    return cog.previous_online if cog else set()


# --- FORMULARIOS ---
class GroupAddModal(discord.ui.Modal, title="Agregar personaje al grupo"):
    owner = discord.ui.TextInput(label="Dueño", placeholder="Ej: Miguel", max_length=50)
    character = discord.ui.TextInput(label="Personaje", placeholder="Nombre exacto en Tibia", max_length=40)

    async def on_submit(self, interaction: discord.Interaction):
        logger.info(f"🎛️ Panel: {interaction.user} agrega '{self.character.value}' al grupo")
        await interaction.response.defer(thinking=True)
        await interaction.followup.send(embed=await roster.add_group_member(self.owner.value, self.character.value))


class GroupSearchModal(discord.ui.Modal, title="Buscar personaje del grupo"):
    character = discord.ui.TextInput(label="Personaje", placeholder="Nombre del personaje", max_length=40)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            embed=roster.build_group_member_embed(self.character.value), ephemeral=True
        )


class GroupRemoveModal(discord.ui.Modal, title="Eliminar personaje del grupo"):
    character = discord.ui.TextInput(label="Personaje", placeholder="Nombre del personaje", max_length=40)

    async def on_submit(self, interaction: discord.Interaction):
        logger.info(f"🎛️ Panel: {interaction.user} elimina '{self.character.value}' del grupo")
        await interaction.response.send_message(embed=roster.remove_group_member(self.character.value))


class HuntedAddModal(discord.ui.Modal, title="Agregar hunted"):
    character = discord.ui.TextInput(label="Personaje", placeholder="Nombre exacto en Tibia", max_length=40)

    async def on_submit(self, interaction: discord.Interaction):
        logger.info(f"🎛️ Panel: {interaction.user} agrega hunted '{self.character.value}'")
        await interaction.response.defer(thinking=True)
        await interaction.followup.send(embed=await roster.add_hunted(self.character.value, str(interaction.user)))


class HuntedSearchModal(discord.ui.Modal, title="Buscar hunted"):
    character = discord.ui.TextInput(label="Personaje", placeholder="Nombre del hunted", max_length=40)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            embed=roster.build_hunted_embed(self.character.value, _hunted_online_keys(interaction)), ephemeral=True
        )


class HuntedRemoveModal(discord.ui.Modal, title="Eliminar hunted"):
    character = discord.ui.TextInput(label="Personaje", placeholder="Nombre del hunted", max_length=40)

    async def on_submit(self, interaction: discord.Interaction):
        logger.info(f"🎛️ Panel: {interaction.user} elimina hunted '{self.character.value}'")
        await interaction.response.send_message(embed=roster.remove_hunted(self.character.value))


# --- MENÚS PARA ELIMINAR ---
class RemoveSelect(discord.ui.Select):
    """Menú desplegable con los personajes; al elegir uno se elimina y se avisa en el canal."""

    def __init__(self, players: dict, remove_func, placeholder: str):
        options = [
            discord.SelectOption(
                label=data["char_name"],
                value=key,
                description=(
                    f"Dueño: {data['owner']} · Nivel {data.get('current_level', '?')}"
                    if "owner" in data
                    else f"Nivel {data.get('level', '?')} · {data.get('vocation', '')}"
                )[:100],
            )
            for key, data in sorted(players.items(), key=lambda kv: kv[1]["char_name"].lower())
        ]
        super().__init__(placeholder=placeholder, options=options[:MAX_SELECT_OPTIONS])
        self.remove_func = remove_func

    async def callback(self, interaction: discord.Interaction):
        logger.info(f"🎛️ Panel: {interaction.user} elimina '{self.values[0]}'")
        result = self.remove_func(self.values[0])
        await interaction.response.edit_message(content="Listo.", view=None)
        await interaction.followup.send(embed=result)


def _remove_view(players, remove_func, placeholder):
    view = discord.ui.View(timeout=120)
    view.add_item(RemoveSelect(players, remove_func, placeholder))
    return view


# --- PANELES ---
# Símbolos monocromáticos (blancos) en vez de emojis de color
ICON_ADD, ICON_SEARCH, ICON_LIST, ICON_REMOVE = "＋", "⚲", "≡", "✕"
BUTTON_STYLE = discord.ButtonStyle.secondary


class GroupPanelView(discord.ui.View):
    """Panel de personajes del grupo (se publica en #tpbot)."""

    def __init__(self):
        super().__init__(timeout=None)  # Persistente: los botones no caducan

    @discord.ui.button(label=f"{ICON_ADD}  Agregar", style=BUTTON_STYLE, custom_id="group_panel:add")
    async def add(self, interaction: discord.Interaction, button):
        await interaction.response.send_modal(GroupAddModal())

    @discord.ui.button(label=f"{ICON_SEARCH}  Buscar", style=BUTTON_STYLE, custom_id="group_panel:search")
    async def search(self, interaction: discord.Interaction, button):
        await interaction.response.send_modal(GroupSearchModal())

    @discord.ui.button(label=f"{ICON_LIST}  Listar", style=BUTTON_STYLE, custom_id="group_panel:list")
    async def list(self, interaction: discord.Interaction, button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        embed, chart = roster.build_group_progress()
        if chart:
            await interaction.followup.send(embed=embed, file=chart, ephemeral=True)
        else:
            await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label=f"{ICON_REMOVE}  Eliminar", style=BUTTON_STYLE, custom_id="group_panel:remove")
    async def remove(self, interaction: discord.Interaction, button):
        if not WATCHED_PLAYERS:
            await interaction.response.send_message(embed=roster.build_group_list_embed(), ephemeral=True)
        elif len(WATCHED_PLAYERS) > MAX_SELECT_OPTIONS:
            await interaction.response.send_modal(GroupRemoveModal())
        else:
            await interaction.response.send_message(
                view=_remove_view(WATCHED_PLAYERS, roster.remove_group_member, "Elige el personaje a eliminar"),
                ephemeral=True,
            )


class HuntedPanelView(discord.ui.View):
    """Panel de hunteds (se publica en #hunteds)."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label=f"{ICON_ADD}  Agregar", style=BUTTON_STYLE, custom_id="hunted_panel:add")
    async def add(self, interaction: discord.Interaction, button):
        await interaction.response.send_modal(HuntedAddModal())

    @discord.ui.button(label=f"{ICON_SEARCH}  Buscar", style=BUTTON_STYLE, custom_id="hunted_panel:search")
    async def search(self, interaction: discord.Interaction, button):
        await interaction.response.send_modal(HuntedSearchModal())

    @discord.ui.button(label=f"{ICON_LIST}  Listar", style=BUTTON_STYLE, custom_id="hunted_panel:list")
    async def list(self, interaction: discord.Interaction, button):
        await interaction.response.send_message(
            embed=roster.build_hunted_list_embed(_hunted_online_keys(interaction)), ephemeral=True
        )

    @discord.ui.button(label=f"{ICON_REMOVE}  Eliminar", style=BUTTON_STYLE, custom_id="hunted_panel:remove")
    async def remove(self, interaction: discord.Interaction, button):
        if not HUNTED_PLAYERS:
            await interaction.response.send_message(embed=roster.build_hunted_list_embed(set()), ephemeral=True)
        elif len(HUNTED_PLAYERS) > MAX_SELECT_OPTIONS:
            await interaction.response.send_modal(HuntedRemoveModal())
        else:
            await interaction.response.send_message(
                view=_remove_view(HUNTED_PLAYERS, roster.remove_hunted, "Elige el hunted a eliminar"),
                ephemeral=True,
            )


class Panel(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.group_list_message = {}
        # Registra las vistas para que los botones ya publicados sigan funcionando tras reiniciar
        bot.add_view(GroupPanelView())
        bot.add_view(HuntedPanelView())
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
            roster.build_group_panel_embed(),
            view=GroupPanelView(),
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


async def setup(bot):
    await bot.add_cog(Panel(bot))
