"""#bazaar: botones, menús y formularios del panel, los filtros y el catálogo."""
import asyncio
import io

import discord

from src.channels.bazaar.embeds import build_details_embed
from src.channels.bazaar.service import BazaarFilters, parse_number
from src.core.config import logger
from src.core.constants.bazaar import (
    MAX_WORLD_RESULTS,
    ORDER_OPTIONS,
    PAGE_LOAD_TIMEOUT,
    PVP_OPTIONS,
    SEARCH_TIMEOUT,
    VOCATION_OPTIONS,
)

NO_VALUE = "_todos"  # Valor de las opciones "Todos/Todas" en los menús (Discord no admite valores vacíos)
# Ancho del botón de mundo en CARACTERES (no píxeles), para igualarlo a los menús.
# Cada carácter son ~8 px. Máximo 80 (límite de Discord). Tras cambiarlo hay que reiniciar el bot.
WIDE_BUTTON_CHARS = 46
BLANK = "⠀"  # Espacio invisible que Discord no recorta
MAX_BUTTON_LABEL = 80
CARDS, LIST = "cards", "list"  # Vistas del catálogo: imagen con cartas o lista de texto


# --- UTILIDADES ---
def _options(options, current):
    return [
        discord.SelectOption(label=label, value=value or NO_VALUE, default=(value == current))
        for label, value in options
    ]


def _wide_label(text):
    """
    Rellena el texto del botón con espacios invisibles a ambos lados para que se vea tan ancho como
    los menús (Discord no permite fijar el ancho de un botón; se ajusta al texto).
    """
    width = min(WIDE_BUTTON_CHARS, MAX_BUTTON_LABEL)  # Discord rechaza textos de más de 80 caracteres
    padding = max(0, width - len(text)) // 2
    return f"{BLANK * padding}{text}{BLANK * padding}"


def _select_value(select):
    value = select.values[0]
    return None if value == NO_VALUE else value


# --- FICHA DETALLADA ---
async def send_details(send, service, search, code_text, ephemeral):
    """Busca el código en la búsqueda y envía su ficha con `send` (ctx.send o followup.send)."""
    try:
        code = int(code_text.strip().lstrip("#[").rstrip("]"))
    except ValueError:
        await send(content="⚠️ Escribe solo el número del código, por ejemplo **03**.", ephemeral=ephemeral)
        return
    auction = await search.by_code(code)
    if not auction:
        await send(content=f"⚠️ No hay ningún personaje con el código **{code:02d}** en esta búsqueda.",
                   ephemeral=ephemeral)
        return

    details = await service.auction_details(auction.auction_id)
    outfit = await service.outfit_png(auction.outfit.image_url)
    kwargs = {"embed": build_details_embed(code, auction, details, outfit is not None), "ephemeral": ephemeral}
    if outfit is not None:
        kwargs["file"] = discord.File(io.BytesIO(outfit), filename="outfit.png")
    await send(**kwargs)


# --- CATÁLOGO (RESULTADOS) ---
async def _with_timeout(coro, what, seconds=15):
    """Espera una llamada a Discord como máximo `seconds`; si falla o tarda, lo registra y sigue."""
    try:
        return await asyncio.wait_for(coro, timeout=seconds)
    except asyncio.TimeoutError:
        logger.error(f"❌ Bazaar: «{what}» tardó más de {seconds}s")
    except discord.HTTPException as e:
        logger.error(f"❌ Bazaar: «{what}» falló: {e!r}")


class CodeModal(discord.ui.Modal, title="Ver ficha"):
    code = discord.ui.TextInput(label="Código del personaje", placeholder="Ej: 03", max_length=6)

    def __init__(self, service, search):
        super().__init__()
        self.service = service
        self.search = search

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        await send_details(interaction.followup.send, self.service, self.search, self.code.value, ephemeral=True)


class PageModal(discord.ui.Modal, title="Ir a página"):
    def __init__(self, view):
        super().__init__()
        self.results_view = view
        total = view.search.total_pages()
        self.page = discord.ui.TextInput(label=f"Número de página (1 a {total})", placeholder="Ej: 5", max_length=5)
        self.add_item(self.page)

    async def on_submit(self, interaction: discord.Interaction):
        total = self.results_view.search.total_pages()
        try:
            page = int(self.page.value.strip())
        except ValueError:
            page = 0
        if not 1 <= page <= total:
            await interaction.response.send_message(f"⚠️ Escribe un número de página entre 1 y {total}.",
                                                    ephemeral=True)
            return
        await self.results_view.go_to(interaction, page - 1, self.results_view.jump)


class ResultsView(discord.ui.View):
    """Botones del catálogo: página anterior/siguiente y ver la ficha de un código."""

    def __init__(self, cog, search, page=0, mode=CARDS):
        super().__init__(timeout=SEARCH_TIMEOUT)
        self.cog = cog
        self.search = search
        self.page = page
        self.mode = mode
        self.message = None
        self.lock = asyncio.Lock()
        self.labels = {item.custom_id: item.label for item in self.children}  # Textos originales
        self._update_buttons()

    def _update_buttons(self):
        """Estado normal: todos activos salvo Anterior en la primera página y Siguiente en la última."""
        for item in self.children:
            item.disabled = False
            item.label = self.labels[item.custom_id]
        self.previous.disabled = self.page == 0
        self.next.disabled = not self.search.has_page(self.page + 1)
        self.switch.label = "≡  Ver en lista" if self.mode == CARDS else "▦  Ver en cartas"

    def _set_loading(self, button):
        """Mientras carga: todos los botones desactivados y el pulsado dice "Cargando…"."""
        for item in self.children:
            item.disabled = True
        button.label = "↻  Cargando…"

    async def go_to(self, interaction, page, button, mode=None):
        mode = mode or self.mode
        if self.lock.locked() or (page == self.page and mode == self.mode):
            await interaction.response.defer()
            return
        async with self.lock:
            message = interaction.message or self.message
            label = f"página {page + 1} ({'lista' if mode == LIST else 'cartas'})"
            logger.info(f"🛒 Bazaar: cargando {label}")

            # Aviso de carga: solo cambian los botones (si se toca el embed, Discord redibuja la imagen)
            self._set_loading(button)
            await _with_timeout(interaction.response.edit_message(view=self), f"aviso de carga de {label}")

            embed, file = None, None
            try:
                embed, file = await asyncio.wait_for(self.cog.render_page(self.search, page, mode),
                                                     timeout=PAGE_LOAD_TIMEOUT)
                logger.info(f"🛒 Bazaar: {label} lista")
            except asyncio.TimeoutError:
                logger.error(f"❌ La {label} del bazaar tardó más de {PAGE_LOAD_TIMEOUT}s; se cancela")
            except Exception as e:
                logger.error(f"❌ Error al cargar la {label} del bazaar: {e!r}")
            finally:
                # Pase lo que pase, los botones vuelven a estar activos
                if embed is not None:
                    self.page, self.mode = page, mode
                self._update_buttons()
                if embed is not None:
                    # En lista no hay imagen: se quita la del catálogo
                    await _with_timeout(
                        message.edit(embed=embed, attachments=[file] if file else [], view=self), f"mostrar {label}"
                    )
                else:
                    await _with_timeout(message.edit(view=self), "reactivar botones")
                    await _with_timeout(
                        interaction.followup.send("⚠️ No se pudo cargar la página. Intenta de nuevo.", ephemeral=True),
                        "aviso de error",
                    )
                logger.info(f"🛒 Bazaar: {label} mostrada")

    @discord.ui.button(label="◀  Anterior", style=discord.ButtonStyle.secondary, custom_id="results:previous")
    async def previous(self, interaction: discord.Interaction, button):
        await self.go_to(interaction, self.page - 1, button)

    @discord.ui.button(label="Siguiente  ▶", style=discord.ButtonStyle.secondary, custom_id="results:next")
    async def next(self, interaction: discord.Interaction, button):
        await self.go_to(interaction, self.page + 1, button)

    @discord.ui.button(label="Ir a página", style=discord.ButtonStyle.secondary, custom_id="results:jump")
    async def jump(self, interaction: discord.Interaction, button):
        if self.lock.locked():
            await interaction.response.defer()
            return
        await interaction.response.send_modal(PageModal(self))

    @discord.ui.button(label="⚲  Ver código", style=discord.ButtonStyle.primary, custom_id="results:code")
    async def view_code(self, interaction: discord.Interaction, button):
        await interaction.response.send_modal(CodeModal(self.cog.service, self.search))

    @discord.ui.button(label="≡  Ver en lista", style=discord.ButtonStyle.secondary, custom_id="results:switch")
    async def switch(self, interaction: discord.Interaction, button):
        await self.go_to(interaction, self.page, button, mode=LIST if self.mode == CARDS else CARDS)

    async def on_timeout(self):
        # La búsqueda expiró: se desactivan los botones
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


# --- FILTROS ---
class MoreFiltersModal(discord.ui.Modal, title="Más filtros"):
    def __init__(self, view):
        super().__init__()
        self.filters_view = view
        f = view.filters
        self.min_level = discord.ui.TextInput(label="Nivel mínimo", placeholder="Ej: 300", required=False,
                                              default=str(f.min_level or ""), max_length=5)
        self.max_level = discord.ui.TextInput(label="Nivel máximo", placeholder="Ej: 600", required=False,
                                              default=str(f.max_level or ""), max_length=5)
        self.min_price = discord.ui.TextInput(label="Precio mínimo (Tibia Coins)", placeholder="Ej: 1000 o 1k",
                                              required=False, default=str(f.min_price or ""), max_length=10)
        self.max_price = discord.ui.TextInput(label="Precio máximo (Tibia Coins)", placeholder="Ej: 5000 o 5k",
                                              required=False, default=str(f.max_price or ""), max_length=10)
        for item in (self.min_level, self.max_level, self.min_price, self.max_price):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            values = {
                "min_level": parse_number(self.min_level.value),
                "max_level": parse_number(self.max_level.value),
                "min_price": parse_number(self.min_price.value),
                "max_price": parse_number(self.max_price.value),
            }
        except ValueError:
            await interaction.response.send_message("⚠️ Nivel y precio deben ser números (ej: 300, 5000, 5k).",
                                                    ephemeral=True)
            return
        view = self.filters_view
        view.filters = view.filters.with_changes(**values)
        await interaction.response.edit_message(content=view.summary(), view=view)


class WorldSearchModal(discord.ui.Modal, title="Buscar mundo"):
    query = discord.ui.TextInput(label="Nombre del mundo o una parte", placeholder="Ej: anti, bra, sec (vacío = todos)",
                                 required=False, max_length=30)

    def __init__(self, view):
        super().__init__()
        self.filters_view = view

    async def on_submit(self, interaction: discord.Interaction):
        view = self.filters_view
        found = view.search_worlds(self.query.value)
        content = view.summary()
        if self.query.value.strip() and not found:
            content += f"\n\n⚠️ Ningún mundo contiene «{self.query.value.strip()}»."
        elif len(found) > MAX_WORLD_RESULTS:
            content += (f"\n\nHay {len(found)} mundos con «{view.world_query}»; se muestran {MAX_WORLD_RESULTS}. "
                        f"Escribe más letras para acotar.")
        await interaction.response.edit_message(content=content, view=view)


class FiltersView(discord.ui.View):
    """Filtros de la búsqueda (solo los ve quien pulsó "Buscar personajes")."""

    def __init__(self, cog, filters=None):
        super().__init__(timeout=15 * 60)
        self.cog = cog
        self.filters = filters or BazaarFilters()
        self.worlds = cog.service.sorted_worlds()
        self.world_query = None    # Texto buscado con "⚲ Mundo"
        self.world_matches = None  # Mundos que coinciden con la búsqueda (None = aún no se buscó)
        self._build_selects()

    def _build_selects(self):
        for item in [c for c in self.children if isinstance(c, discord.ui.Select) or c.row == 2]:
            self.remove_item(item)
        f = self.filters
        specs = [
            (0, "vocation", "Vocación", VOCATION_OPTIONS, f.vocation),
            (1, "pvp_type", "Tipo de PvP", PVP_OPTIONS, f.pvp_type),
            (3, "order", "Ordenar por", [(label, label) for label, _ in ORDER_OPTIONS], f.order),
        ]
        for row, field, placeholder, options, current in specs:
            select = discord.ui.Select(placeholder=placeholder, options=_options(options, current), row=row)
            select.callback = self._make_callback(field, select)
            self.add_item(select)
        self.add_item(self._world_choices() if self.world_matches else self._world_button())

    def _world_button(self):
        """Fila del mundo: un botón que muestra el mundo elegido y abre el buscador."""
        world = self.filters.world
        # Morado (blurple) sin mundo elegido, verde con mundo elegido (Discord no tiene botones naranjas)
        button = discord.ui.Button(label=_wide_label(f"⚲  Mundo: {world or 'Todos'}"),
                                   style=discord.ButtonStyle.success if world else discord.ButtonStyle.primary,
                                   row=2)

        async def callback(interaction: discord.Interaction):
            await interaction.response.send_modal(WorldSearchModal(self))

        button.callback = callback
        return button

    def _world_choices(self):
        """Tras buscar, si hay varios mundos: se elige uno de los resultados y vuelve el botón."""
        options = [discord.SelectOption(label=w, value=w) for w in self.world_matches]
        options.append(discord.SelectOption(label="« Cancelar", value=NO_VALUE))
        select = discord.ui.Select(
            placeholder=f"Elige el mundo ({len(self.world_matches)} con «{self.world_query}»)",
            options=options, row=2,
        )

        async def callback(interaction: discord.Interaction):
            value = _select_value(select)
            if value:
                self.filters = self.filters.with_changes(world=value)
            self.world_matches = None
            self._build_selects()
            await interaction.response.edit_message(content=self.summary(), view=self)

        select.callback = callback
        return select

    def search_worlds(self, query):
        """
        Busca mundos por nombre o parte del nombre (los que empiezan por el texto van primero).
        Vacío = todos los mundos. Un solo resultado se elige directo; varios se ofrecen en un menú.
        """
        self.world_query = query.strip()
        self.world_matches = None
        if not self.world_query:
            self.filters = self.filters.with_changes(world=None)
            self._build_selects()
            return []
        q = self.world_query.lower()
        starts = [w for w in self.worlds if w.lower().startswith(q)]
        contains = [w for w in self.worlds if q in w.lower() and w not in starts]
        found = starts + contains
        if len(found) == 1:
            self.filters = self.filters.with_changes(world=found[0])
        elif found:
            self.world_matches = found[:MAX_WORLD_RESULTS]
        self._build_selects()
        return found

    def _make_callback(self, field, select):
        async def callback(interaction: discord.Interaction):
            value = _select_value(select)
            if field == "order":
                value = value or ORDER_OPTIONS[0][0]
            self.filters = self.filters.with_changes(**{field: value})
            self._build_selects()
            await interaction.response.edit_message(content=self.summary(), view=self)
        return callback

    def summary(self):
        return (f"**Filtros:** {self.filters.describe()}\n**Orden:** {self.filters.order}\n"
                f"Ajusta los filtros y elige cómo verlos: **en cartas** (imagen) o **en lista** (texto). "
                f"El nivel y el precio están en **Más filtros**.")

    @discord.ui.button(label="Más filtros", style=discord.ButtonStyle.secondary, row=4)
    async def more(self, interaction: discord.Interaction, button):
        await interaction.response.send_modal(MoreFiltersModal(self))

    @discord.ui.button(label="Limpiar", style=discord.ButtonStyle.secondary, row=4)
    async def clear(self, interaction: discord.Interaction, button):
        self.filters = BazaarFilters()
        self.world_query = self.world_matches = None
        self._build_selects()
        await interaction.response.edit_message(content=self.summary(), view=self)

    @discord.ui.button(label="▦  Ver en cartas", style=discord.ButtonStyle.primary, row=4)
    async def search_cards(self, interaction: discord.Interaction, button):
        await self._search(interaction, CARDS)

    @discord.ui.button(label="≡  Ver en lista", style=discord.ButtonStyle.primary, row=4)
    async def search_list(self, interaction: discord.Interaction, button):
        await self._search(interaction, LIST)

    async def _search(self, interaction, mode):
        await interaction.response.edit_message(content=f"🔍 Buscando: {self.filters.describe()}…", view=None)
        message = await self.cog.publish_search(interaction.channel, self.filters, interaction.user, mode)
        if isinstance(message, str):
            await interaction.edit_original_response(content=message, view=self)  # Error o sin resultados
        else:
            await interaction.edit_original_response(content=f"✅ Resultados publicados: {message.jump_url}")


class BazaarPanelView(discord.ui.View):
    """Botón del panel (persistente: funciona aunque el bot se reinicie)."""

    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="⚲  Buscar personajes", style=discord.ButtonStyle.primary, custom_id="bazaar:search")
    async def open_filters(self, interaction: discord.Interaction, button):
        cog = self.cog
        if not cog.service.worlds:
            await interaction.response.defer(ephemeral=True, thinking=True)
            await cog.service.load_worlds()
            view = FiltersView(cog)
            await interaction.followup.send(view.summary(), view=view, ephemeral=True)
            return
        view = FiltersView(cog)
        await interaction.response.send_message(view.summary(), view=view, ephemeral=True)
