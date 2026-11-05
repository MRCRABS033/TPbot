"""
Panel de control con botones (como los bots de música) para gestionar
los personajes del grupo y los hunteds sin escribir comandos.

- #tpbot: el panel del grupo siempre queda al final del canal. La lista (con nivel de ingreso
  y actual) se ve al pulsar Listar. El canal se limpia cada 12 horas (ver cogs/cleanup.py).
- #hunteds tiene su propio panel: ver channels/hunteds/page.py.
Los botones siguen funcionando aunque el bot se reinicie (vistas persistentes).
"""
import discord
from discord.ext import commands, tasks

from src.core.channel_names import CHANNEL_TPBOT, find_channel
from src.core.config import WATCHED_PLAYERS, logger
from src.shared.task_utils import resilient
from src.shared import roster
from src.shared.discord_utils import send_or_update_embed
from src.shared.panel_ui import BUTTON_STYLE, ICON_ADD, ICON_LIST, ICON_REMOVE, ICON_SEARCH, MAX_SELECT_OPTIONS, remove_view

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


# --- PANELES ---
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
                view=remove_view(WATCHED_PLAYERS, roster.remove_group_member, "Elige el personaje a eliminar"),
                ephemeral=True,
            )


class Panel(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.group_list_message = {}
        # Registra la vista para que los botones ya publicados sigan funcionando tras reiniciar
        bot.add_view(GroupPanelView())
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
