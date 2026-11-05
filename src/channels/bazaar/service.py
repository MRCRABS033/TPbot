"""
Búsqueda en el Char Bazaar de tibia.com.

- Las consultas a tibia.com están en src/api/tibiacom.py (se reciben como parámetro `api`).
- El precio no es un filtro de tibia.com: se filtra en el bot sobre los resultados.
"""
import asyncio
import io
import logging
import math
import time
from dataclasses import dataclass, replace

from PIL import Image

from src.core.constants.bazaar import (
    CARDS_PER_IMAGE,
    IMAGE_CACHE_SECONDS,
    MAX_PAGES_FOR_PRICE_FILTER,
    PARALLEL_REQUESTS,
    TIBIA_PAGE_SIZE,
    ORDER_OPTIONS,
    PVP_OPTIONS,
    VOCATION_OPTIONS,
)

logger = logging.getLogger("TibiaBot")
MAX_STORED_SEARCHES = 50


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


def enum_text(value):
    """Vocation.KNIGHT -> 'Knight' (tibia.py devuelve enums)."""
    return getattr(value, "value", value)


def _label(options, value):
    return next((label for label, v in options if v == value), None)


def format_tc(amount):
    """22500 -> '22.500 TC'."""
    return f"{amount:,} TC".replace(",", ".")


def format_time_left(end, now=None):
    """Tiempo hasta el fin de la subasta: '2d 4h', '1h 30m', '45m'."""
    seconds = int(end.timestamp() - (now or time.time()))
    if seconds <= 0:
        return "terminada"
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{max(minutes, 1)}m"


# --- FILTROS ---
@dataclass(frozen=True)
class BazaarFilters:
    vocation: str = None     # Nombre en AuctionVocationFilter
    pvp_type: str = None     # Nombre en PvpTypeFilter
    world: str = None
    min_level: int = None
    max_level: int = None
    min_price: int = None    # En Tibia Coins (filtro del bot)
    max_price: int = None
    order: str = ORDER_OPTIONS[0][0]  # Texto de ORDER_OPTIONS

    def with_changes(self, **changes):
        return replace(self, **changes)

    def to_api(self, api):
        order_by, direction = dict(ORDER_OPTIONS)[self.order]
        return api.build_auction_filters(
            vocation=self.vocation,
            pvp_type=self.pvp_type,
            world=self.world,
            min_level=self.min_level,
            max_level=self.max_level,
            order_by=order_by,
            direction=direction,
        )

    def matches_price(self, bid):
        if self.min_price is not None and bid < self.min_price:
            return False
        if self.max_price is not None and bid > self.max_price:
            return False
        return True

    def describe(self):
        """Resumen corto de los filtros activos: 'Knight · Nivel 300–500 · Antica · hasta 5.000 TC'."""
        parts = [_label(VOCATION_OPTIONS, self.vocation) if self.vocation else None]
        if self.min_level and self.max_level:
            parts.append(f"Nivel {self.min_level}–{self.max_level}")
        elif self.min_level:
            parts.append(f"Nivel {self.min_level}+")
        elif self.max_level:
            parts.append(f"Hasta nivel {self.max_level}")
        parts.append(self.world)
        if self.pvp_type:
            parts.append(_label(PVP_OPTIONS, self.pvp_type))
        if self.min_price is not None and self.max_price is not None:
            parts.append(f"{format_tc(self.min_price)} – {format_tc(self.max_price)}")
        elif self.min_price is not None:
            parts.append(f"desde {format_tc(self.min_price)}")
        elif self.max_price is not None:
            parts.append(f"hasta {format_tc(self.max_price)}")
        parts = [p for p in parts if p]
        return " · ".join(parts) if parts else "Sin filtros"


# --- BÚSQUEDA PAGINADA ---
class TooManyResults(Exception):
    """Hay demasiadas subastas para filtrar por precio; hay que acotar la búsqueda."""

    def __init__(self, results):
        super().__init__(results)
        self.results = results


class BazaarSearch:
    """
    Resultados de una búsqueda, con acceso a cualquier página del catálogo. Cada subasta recibe un
    código (1, 2, 3...) según su posición.

    - Sin filtro de precio: la posición i está en la página i // 25 de tibia.com y solo se piden
      las páginas que hacen falta.
    - Con filtro de precio (tibia.com no lo tiene): se descargan todas las páginas de la búsqueda
      y se filtran en el bot. Así el total es exacto. Si el orden es por oferta, se ordena aquí,
      porque tibia.com solo ordena las subastas que ya tienen ofertas (las de oferta mínima van
      al final, sin ordenar).
    """

    def __init__(self, api, client, filters: BazaarFilters):
        self._api = api
        self.client = client
        self.filters = filters
        self._tibia_pages = {}       # { número de página de tibia.com: [subastas] }
        self.tibia_results = None    # Total según tibia.com (sin el filtro de precio)
        self.total_tibia_pages = None
        self._filtered = None        # Con filtro de precio: todas las subastas que lo cumplen
        self.images = {}             # { página del catálogo: (hora, png) }
        self.requested_by = None     # Nombre de quien hizo la búsqueda (se muestra en el catálogo)

    @property
    def filters_price(self):
        return self.filters.min_price is not None or self.filters.max_price is not None

    async def _tibia_page(self, number):
        if number not in self._tibia_pages:
            bazaar = await self._api.fetch_auctions_page(self.client, number, self.filters.to_api(self._api))
            self._tibia_pages[number] = list(bazaar.entries)
            self.tibia_results = bazaar.results_count
            self.total_tibia_pages = bazaar.total_pages or 0
        return self._tibia_pages[number]

    async def _load_filtered(self):
        """Con filtro de precio: descarga todas las páginas y filtra (una sola vez)."""
        if self._filtered is not None:
            return
        await self._tibia_page(1)
        if self.total_tibia_pages > MAX_PAGES_FOR_PRICE_FILTER:
            raise TooManyResults(self.tibia_results)

        semaphore = asyncio.Semaphore(PARALLEL_REQUESTS)

        async def fetch(number):
            async with semaphore:
                return await self._tibia_page(number)

        pages = await asyncio.gather(*(fetch(n) for n in range(1, self.total_tibia_pages + 1)))
        filtered = [a for page in pages for a in page if self.filters.matches_price(a.bid)]
        order_by, direction = dict(ORDER_OPTIONS)[self.filters.order]
        if order_by == "BID":
            filtered.sort(key=lambda a: a.bid, reverse=direction == "HIGHEST_LATEST")
        self._filtered = filtered
        logger.info(f"🛒 Bazaar: {self.total_tibia_pages} páginas revisadas, {len(filtered)} subastas en el rango de precio")

    async def count(self):
        """Total de resultados. Puede lanzar TooManyResults si hay filtro de precio."""
        if self.filters_price:
            await self._load_filtered()
            return len(self._filtered)
        await self._tibia_page(1)
        return self.tibia_results or 0

    async def get(self, index):
        """Subasta con código index + 1, o None si no existe."""
        if not 0 <= index < await self.count():
            return None
        if self.filters_price:
            return self._filtered[index]
        page = await self._tibia_page(index // TIBIA_PAGE_SIZE + 1)
        offset = index % TIBIA_PAGE_SIZE
        return page[offset] if offset < len(page) else None

    async def page(self, index):
        """[(código, subasta)] de la página `index` (desde 0) del catálogo."""
        first = index * CARDS_PER_IMAGE
        entries = []
        for i in range(first, first + CARDS_PER_IMAGE):
            auction = await self.get(i)
            if auction is None:
                break
            entries.append((i + 1, auction))
        return entries

    def total_pages(self):
        total = len(self._filtered) if self._filtered is not None else (self.tibia_results or 0)
        return max(1, math.ceil(total / CARDS_PER_IMAGE))

    def has_page(self, index):
        return 0 <= index < self.total_pages()

    async def by_code(self, code):
        return await self.get(code - 1)


# --- IMÁGENES DE LOS OUTFITS ---
_outfit_cache = {}  # { url: Image RGBA | None }


async def download_outfits(api, urls):
    """{url: Image RGBA} de los outfits (los que no se puedan descargar quedan fuera)."""
    pending = [u for u in dict.fromkeys(urls) if u and u not in _outfit_cache]
    if pending:
        for url, data in (await api.fetch_images(pending)).items():
            try:
                image = Image.open(io.BytesIO(data))
                image.seek(0)  # Primer fotograma si es animado
                _outfit_cache[url] = image.convert("RGBA")
            except Exception as e:
                logger.warning(f"⚠️ No se pudo leer el outfit {url}: {e}")
    return {u: _outfit_cache[u] for u in urls if u in _outfit_cache}


# --- SERVICIO DEL CANAL ---
class BazaarService:
    def __init__(self, api, render_catalog, proxy_configured=False, clock=time.monotonic):
        self._api = api
        self._render_catalog = render_catalog
        self._proxy_configured = proxy_configured
        self._clock = clock
        self.client = None
        self.worlds = set()     # Mundos de Tibia (para validar el filtro)
        self.searches = {}      # { message_id: BazaarSearch }
        self.last_search = {}   # { channel_id: BazaarSearch } para !ver

    def start(self):
        """Crea el cliente de tibia.com. Devuelve True si sale por el proxy."""
        self.client = self._api.create_client()
        return self._api.uses_proxy(self.client)

    async def close(self):
        if self.client:
            await self._api.close_client(self.client)

    # --- MUNDOS ---
    async def load_worlds(self):
        try:
            self.worlds = await self._api.fetch_available_worlds(self.client)
        except Exception as e:
            logger.warning(f"⚠️ No se pudo cargar la lista de mundos del bazaar: {e}")

    def sorted_worlds(self):
        return sorted(self.worlds)

    def find_world(self, name):
        """Nombre oficial del mundo, sin importar mayúsculas. None si no existe."""
        return next((w for w in self.worlds if w.lower() == name.strip().lower()), None)

    def worlds_matching(self, text):
        """Mundos que contienen el texto; primero los que empiezan por él."""
        q = text.strip().lower()
        worlds = self.sorted_worlds()
        return [w for w in worlds if w.lower().startswith(q)] + [w for w in worlds if q in w.lower() and not w.lower().startswith(q)]

    # --- BÚSQUEDAS ---
    def new_search(self, filters, requested_by):
        search = BazaarSearch(self._api, self.client, filters)
        search.requested_by = requested_by
        return search

    def remember(self, message_id, channel_id, search):
        """Guarda la búsqueda publicada (para !ver); se conservan solo las más recientes."""
        self.searches[message_id] = search
        self.last_search[channel_id] = search
        if len(self.searches) > MAX_STORED_SEARCHES:
            self.searches.pop(next(iter(self.searches)))

    async def catalog_png(self, search, page, total):
        """Imagen de una página del catálogo, o None si la página no tiene subastas."""
        # Una página ya dibujada se reutiliza (no se vuelve a generar la imagen)
        cached = search.images.get(page)
        if cached and self._clock() - cached[0] < IMAGE_CACHE_SECONDS:
            return cached[1]

        entries = await search.page(page)
        if not entries:
            return None
        outfits = await download_outfits(self._api, [a.outfit.image_url for _, a in entries])
        png = await asyncio.to_thread(self._render_catalog, entries, outfits, f"PÁGINA {page + 1} DE {total}")
        search.images[page] = (self._clock(), png)
        return png

    # --- FICHA ---
    async def auction_details(self, auction_id):
        return await self._api.fetch_auction_details(auction_id)

    async def outfit_png(self, url):
        """Outfit del personaje al doble de tamaño (PNG), o None si no se pudo descargar."""
        outfit = (await download_outfits(self._api, [url])).get(url)
        if outfit is None:
            return None
        big = outfit.resize((outfit.width * 2, outfit.height * 2), Image.NEAREST)
        buffer = io.BytesIO()
        big.save(buffer, format="PNG")
        return buffer.getvalue()

    # --- ERRORES ---
    def describe_error(self, error):
        """Explicación corta del fallo al consultar tibia.com (se muestra a quien buscó)."""
        if isinstance(error, (asyncio.TimeoutError, TimeoutError)):
            return "tibia.com tardó demasiado en responder. Intenta de nuevo en un momento."
        kind = self._api.classify_error(error)
        if kind == self._api.ERROR_FORBIDDEN:
            if self._proxy_configured:
                return ("tibia.com bloqueó la conexión (error 403) **aunque se usó el proxy**. "
                        "Puede ser una IP del proxy bloqueada: intenta de nuevo; si sigue, revisa el proxy.")
            return ("tibia.com bloqueó la conexión desde el servidor donde corre el bot (error 403) y **no hay "
                    "proxy configurado**: el bot no encontró la variable BAZAAR_PROXY_URL.")
        if kind == self._api.ERROR_PROXY:
            return "no se pudo conectar con el proxy configurado (BAZAAR_PROXY_URL). Revisa que esté activo."
        if kind == self._api.ERROR_MAINTENANCE:
            return "tibia.com está en mantenimiento. Intenta más tarde."
        if kind == self._api.ERROR_NETWORK:
            return f"error de conexión con tibia.com ({error}). Intenta de nuevo en un momento."
        return f"error inesperado ({type(error).__name__}). Intenta de nuevo en un momento."
