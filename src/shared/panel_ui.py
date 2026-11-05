"""Piezas de los paneles con botones de #tpbot y #hunteds."""
import discord

from src.core.config import logger

MAX_SELECT_OPTIONS = 25  # Límite de Discord por menú desplegable

# Símbolos monocromáticos (blancos) en vez de emojis de color
ICON_ADD, ICON_SEARCH, ICON_LIST, ICON_REMOVE = "＋", "⚲", "≡", "✕"
BUTTON_STYLE = discord.ButtonStyle.secondary


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


def remove_view(players, remove_func, placeholder):
    view = discord.ui.View(timeout=120)
    view.add_item(RemoveSelect(players, remove_func, placeholder))
    return view
