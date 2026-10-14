"""
Char Bazaar de tibia.com.

- #bazaar: panel con el botón "Buscar personajes" (siempre al final del canal).
- Al pulsarlo: filtros (vocación, PvP, mundo, orden y, en "Más filtros", nivel y precio).
- "Buscar" publica el catálogo como imagen (12 personajes por página) con botones para cambiar
  de página y para ver la ficha completa de un personaje por su código.
- !ver <código> muestra la ficha de la última búsqueda del canal. !bazaar muestra el panel.
"""
import asyncio
import io
import time

import discord
import tibiapy
import tibiapy.errors
from aiohttp_socks import ProxyConnectionError, ProxyConnector, ProxyError, ProxyTimeoutError
from discord import app_commands
from discord.ext import commands, tasks
from PIL import Image

from channels import CHANNEL_BAZAAR, find_channel
from config import BAZAAR_PROXY_URL, logger
from constants.bazaar import (
    AUCTION_URL,
    BAZAAR_URL,
    IMAGE_CACHE_SECONDS,
    PAGE_LOAD_TIMEOUT,
    MAX_PAGES_FOR_PRICE_FILTER,
    ORDER_OPTIONS,
    PVP_OPTIONS,
    SEARCH_TIMEOUT,
    VOCATION_OPTIONS,
    MAX_WORLD_RESULTS,
)
from services.bazaar import (
    BazaarFilters,
    BazaarSearch,
    TooManyResults,
    create_session,
    download_outfits,
    enum_text,
    fetch_auction_details,
    format_tc,
)
from services.bazaar_image import render_catalog
from services.discord_utils import send_or_update_embed
from services.task_utils import resilient

COLOR = discord.Color.from_rgb(139, 92, 246)
PANEL_TITLE = "Bazar de personajes"
MAX_STORED_SEARCHES = 50
NO_VALUE = "_todos"
# Ancho del botón de mundo en CARACTERES (no píxeles), para igualarlo a los menús.
# Cada carácter son ~8 px. Máximo 80 (límite de Discord). Tras cambiarlo hay que reiniciar el bot.
WIDE_BUTTON_CHARS = 46
BLANK = "⠀"  # Espacio invisible que Discord no recorta
MAX_BUTTON_LABEL = 80
CARDS, LIST = "cards", "list"  # Vistas del catálogo: imagen con cartas o lista de texto  # Valor de las opciones "Todos/Todas" en los menús (Discord no admite valores vacíos)


# --- UTILIDADES ---
def parse_number(text):
    """'5000', '5.000', '5k', '1,5k' -> entero. Vacío -> None. Inválido -> ValueError."""
    text = (text or "").strip().lower().replace(" ", "")
    if not text:
        return None
    multiplier = 1000 if text.endswith("k") else 1
    text = text.rstrip("k")
    if multiplier == 1000:
        text = text.replace(",", ".")
        return int(float(text) * 1000)
    return int(text.replace(".", "").replace(",", ""))


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


async def send_details(send, search, code_text, ephemeral):
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

    details = await fetch_auction_details(auction.auction_id)
    outfits = await download_outfits([auction.outfit.image_url])
    outfit = outfits.get(auction.outfit.image_url)
    kwargs = {"embed": build_details_embed(code, auction, details, outfit is not None), "ephemeral": ephemeral}
    if outfit is not None:
        big = outfit.resize((outfit.width * 2, outfit.height * 2), Image.NEAREST)
        buffer = io.BytesIO()
        big.save(buffer, format="PNG")
        buffer.seek(0)
        kwargs["file"] = discord.File(buffer, filename="outfit.png")
    await send(**kwargs)


def describe_error(error):
    """Explicación corta del fallo al consultar tibia.com (se muestra a quien buscó)."""
    if isinstance(error, (asyncio.TimeoutError, TimeoutError)):
        return "tibia.com tardó demasiado en responder. Intenta de nuevo en un momento."
    if isinstance(error, tibiapy.errors.ForbiddenError):
        if BAZAAR_PROXY_URL:
            return ("tibia.com bloqueó la conexión (error 403) **aunque se usó el proxy**. "
                    "Puede ser una IP del proxy bloqueada: intenta de nuevo; si sigue, revisa el proxy.")
        return ("tibia.com bloqueó la conexión desde el servidor donde corre el bot (error 403) y **no hay "
                "proxy configurado**: el bot no encontró la variable BAZAAR_PROXY_URL.")
    if isinstance(error, (ProxyError, ProxyConnectionError, ProxyTimeoutError)):
        return "no se pudo conectar con el proxy configurado (BAZAAR_PROXY_URL). Revisa que esté activo."
    if isinstance(error, tibiapy.errors.SiteMaintenanceError):
        return "tibia.com está en mantenimiento. Intenta más tarde."
    if isinstance(error, tibiapy.errors.NetworkError):
        return f"error de conexión con tibia.com ({error}). Intenta de nuevo en un momento."
    return f"error inesperado ({type(error).__name__}). Intenta de nuevo en un momento."


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

    def __init__(self, search):
        super().__init__()
        self.search = search

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        await send_details(interaction.followup.send, self.search, self.code.value, ephemeral=True)


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
        await interaction.response.send_modal(CodeModal(self.search))

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
        self.worlds = sorted(cog.worlds) if cog and cog.worlds else []
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

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="⚲  Buscar personajes", style=discord.ButtonStyle.primary, custom_id="bazaar:search")
    async def open_filters(self, interaction: discord.Interaction, button):
        cog = interaction.client.get_cog("Bazaar")
        if not cog.worlds:
            await interaction.response.defer(ephemeral=True, thinking=True)
            await cog._load_worlds()
            view = FiltersView(cog)
            await interaction.followup.send(view.summary(), view=view, ephemeral=True)
            return
        view = FiltersView(cog)
        await interaction.response.send_message(view.summary(), view=view, ephemeral=True)


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


# --- COG ---
class Bazaar(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.client = None
        self.worlds = set()          # Mundos de Tibia (para validar el filtro)
        self.panel_message = {}      # { guild_id: id del panel en #bazaar }
        self.searches = {}           # { message_id: BazaarSearch }
        self.last_search = {}        # { channel_id: BazaarSearch } para !ver
        bot.add_view(BazaarPanelView())

    async def cog_load(self):
        # Con sesión propia: tiempo límite por petición y, si está configurado, el proxy
        self.client = tibiapy.Client(session=create_session())
        if isinstance(self.client.session.connector, ProxyConnector):
            logger.info("🛒 Bazaar: las consultas a tibia.com saldrán por el proxy configurado")
        else:
            logger.info("Bazaar: sin proxy; las consultas a tibia.com salen directo desde este servidor")
        self.keep_panel.start()

    async def cog_unload(self):
        self.keep_panel.cancel()
        if self.client:
            await self.client.session.close()

    async def render_page(self, search, page, mode=CARDS):
        """(embed, archivo o None) de una página del catálogo, o (None, None) si no hay resultados."""
        count = await search.count()
        if not count:
            return None, None
        total = search.total_pages()
        header = (f"{search.filters.describe()}\n**{f'{count:,}'.replace(',', '.')}** subastas · "
                  f"orden: {search.filters.order}")

        embed = discord.Embed(title=PANEL_TITLE, url=BAZAAR_URL, description=header, color=COLOR)
        if search.requested_by:
            embed.set_author(name=f"Buscado por {search.requested_by}")
        embed.set_footer(text=f"Página {page + 1} de {total} · Usa Ver código o !ver <código> para la ficha")

        if mode == LIST:
            entries = await search.page(page)
            if not entries:
                return None, None
            embed.description = f"{header}\n\n{build_list_text(entries)}"
            return embed, None

        # Una página ya dibujada se reutiliza (no se vuelve a generar la imagen)
        cached = search.images.get(page)
        if cached and time.monotonic() - cached[0] < IMAGE_CACHE_SECONDS:
            png = cached[1]
        else:
            entries = await search.page(page)
            if not entries:
                return None, None
            outfits = await download_outfits([a.outfit.image_url for _, a in entries])
            png = await asyncio.to_thread(render_catalog, entries, outfits, f"PÁGINA {page + 1} DE {total}")
            search.images[page] = (time.monotonic(), png)

        embed.set_image(url="attachment://bazaar.png")
        return embed, discord.File(io.BytesIO(png), filename="bazaar.png")

    async def publish_search(self, channel, filters, user, mode=CARDS):
        """Publica el catálogo de una búsqueda. Devuelve el mensaje, o un texto si falla o no hay resultados."""
        search = BazaarSearch(self.client, filters)
        search.requested_by = user.display_name
        try:
            embed, file = await asyncio.wait_for(self.render_page(search, 0, mode), timeout=PAGE_LOAD_TIMEOUT)
        except TooManyResults as e:
            limit = MAX_PAGES_FOR_PRICE_FILTER * 25
            return (f"⚠️ Hay **{e.results:,}** subastas con estos filtros. Para filtrar por precio la búsqueda "
                    f"debe tener como máximo **{limit}**: acótala con vocación, nivel o mundo.").replace(",", ".")
        except Exception as e:
            logger.error(f"❌ Error al buscar en el bazaar: {e!r}")
            return f"⚠️ No se pudo consultar el bazaar de tibia.com: {describe_error(e)}"
        if not embed:
            return f"No se encontraron subastas con estos filtros: {filters.describe()}"

        view = ResultsView(self, search, mode=mode)
        message = await channel.send(embed=embed, view=view, **({"file": file} if file else {}))
        view.message = message
        self.searches[message.id] = search
        self.last_search[channel.id] = search
        if len(self.searches) > MAX_STORED_SEARCHES:
            self.searches.pop(next(iter(self.searches)))
        logger.info(f"🛒 Búsqueda en el bazaar de {user}: {filters.describe()}")
        return message

    # --- PANEL SIEMPRE AL FINAL DE #bazaar ---
    @resilient
    @tasks.loop(minutes=1)
    async def keep_panel(self):
        if not self.worlds:
            await self._load_worlds()
        await send_or_update_embed(self.bot, self.panel_message, CHANNEL_BAZAAR, build_panel_embed(),
                                   view=BazaarPanelView(), keep_last=True)

    @keep_panel.before_loop
    async def before_keep_panel(self):
        await self.bot.wait_until_ready()

    async def _load_worlds(self):
        try:
            response = await self.client.fetch_current_auctions(1)
            filters = response.data.filters
            self.worlds = set(filters.available_worlds or []) if filters else set()
        except Exception as e:
            logger.warning(f"⚠️ No se pudo cargar la lista de mundos del bazaar: {e}")

    # --- COMANDOS ---
    @commands.hybrid_command(name="bazaar", description="Muestra el panel para buscar personajes en el Char Bazaar")
    @app_commands.describe(mundo="Mundo para filtrar (escribe para buscar entre todos)")
    async def bazaar_command(self, ctx, *, mundo: str = None):
        logger.info(f"📩 Comando !bazaar ejecutado por {ctx.author} (mundo: {mundo})")
        if not mundo:
            if find_channel([ctx.channel], CHANNEL_BAZAAR):
                # En #bazaar ya está el panel: solo se mueve al final (sin crear otro)
                if ctx.interaction:
                    await ctx.send("El panel está al final del canal.", ephemeral=True)
                else:
                    try:
                        await ctx.message.delete()
                    except discord.HTTPException:
                        pass
                await self.keep_panel()
                return
            await ctx.send(embed=build_panel_embed(), view=BazaarPanelView())
            return

        # Con mundo: se abren los filtros con ese mundo ya elegido
        await ctx.defer(ephemeral=True)
        if not self.worlds:
            await self._load_worlds()
        world = next((w for w in self.worlds if w.lower() == mundo.strip().lower()), None)
        if not world:
            await ctx.send(f"⚠️ No existe el mundo **{mundo}** en Tibia.", ephemeral=True)
            return
        view = FiltersView(self, BazaarFilters(world=world))
        await ctx.send(view.summary(), view=view, ephemeral=True)

    @bazaar_command.autocomplete("mundo")
    async def world_autocomplete(self, interaction: discord.Interaction, current: str):
        """Buscador de /bazaar: muestra los mundos que coinciden con lo que se va escribiendo."""
        if not self.worlds:
            await self._load_worlds()
        q = current.strip().lower()
        worlds = sorted(self.worlds)
        found = [w for w in worlds if w.lower().startswith(q)] + [w for w in worlds if q in w.lower() and not w.lower().startswith(q)]
        return [app_commands.Choice(name=w, value=w) for w in found[:25]]

    @commands.hybrid_command(name="ver", description="Muestra la ficha de un personaje de la última búsqueda del bazaar")
    @app_commands.describe(code="Código del personaje en el catálogo (ej: 03)")
    async def view_command(self, ctx, code: str):
        logger.info(f"📩 Comando !ver {code} ejecutado por {ctx.author}")
        search = self.last_search.get(ctx.channel.id)
        if not search:
            await ctx.send("⚠️ No hay ninguna búsqueda reciente del bazaar en este canal. Usa **!bazaar** para buscar.")
            return
        await ctx.defer()

        async def send(**kwargs):
            kwargs.pop("ephemeral", None)
            await ctx.send(**kwargs)

        await send_details(send, search, code, ephemeral=False)


async def setup(bot):
    await bot.add_cog(Bazaar(bot))
