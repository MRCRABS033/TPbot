"""
tibia.com: Char Bazaar.

- El listado se obtiene con tibia.py (fetch_current_auctions + AuctionFilters).
- La ficha detallada se lee directamente de la página de la subasta, porque el lector de
  detalles de tibia.py no funciona con el diseño actual de tibia.com.
- tibia.com bloquea muchas IPs de hosting: si está configurado BAZAAR_PROXY_URL, todo sale por el proxy.
"""
import asyncio
import logging
import re
import time

import aiohttp
import tibiapy
import tibiapy.errors
from aiohttp_socks import ProxyConnectionError, ProxyConnector, ProxyError, ProxyTimeoutError
from bs4 import BeautifulSoup
from tibiapy import enums
from tibiapy.models import AuctionFilters

from config import BAZAAR_PROXY_URL, logger
from constants.bazaar import AUCTION_URL, DETAILS_CACHE_SECONDS, REQUEST_TIMEOUT

HEADERS = {"User-Agent": "Mozilla/5.0 (TPbot)"}
# Skills que se muestran en la ficha (la página también trae bonus de tareas que no interesan)
MAIN_SKILLS = ("Magic Level", "Fist Fighting", "Club Fighting", "Sword Fighting", "Axe Fighting",
               "Distance Fighting", "Shielding", "Fishing")

# Tipos de error que devuelve classify_error
ERROR_FORBIDDEN, ERROR_PROXY, ERROR_MAINTENANCE, ERROR_NETWORK = "forbidden", "proxy", "maintenance", "network"

# tibia.py registra cada petición; solo interesan sus avisos y errores
logging.getLogger("tibiapy").setLevel(logging.WARNING)


def create_session():
    """
    Sesión HTTP para todo lo del bazaar (listado, fichas e imágenes): con tiempo límite y, si está
    configurado BAZAAR_PROXY_URL, saliendo por el proxy (tibia.com bloquea muchas IPs de hosting).
    """
    connector = None
    if BAZAAR_PROXY_URL:
        try:
            connector = ProxyConnector.from_url(BAZAAR_PROXY_URL)
        except ValueError as e:
            logger.error(f"❌ BAZAAR_PROXY_URL no es una dirección válida ({e}). Formato esperado: "
                         f"http://usuario:clave@host:puerto (sin comillas). El bazaar seguirá SIN proxy.")
    return aiohttp.ClientSession(headers=HEADERS, connector=connector,
                                 timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT))


# --- CLIENTE DE TIBIA.PY ---
def create_client():
    """Cliente de tibia.py con sesión propia: tiempo límite por petición y, si está configurado, el proxy."""
    return tibiapy.Client(session=create_session())


def uses_proxy(client):
    return isinstance(client.session.connector, ProxyConnector)


async def close_client(client):
    await client.session.close()


def classify_error(error):
    """Tipo de fallo al consultar tibia.com (ERROR_*), o None si no es uno conocido."""
    if isinstance(error, tibiapy.errors.ForbiddenError):
        return ERROR_FORBIDDEN
    if isinstance(error, (ProxyError, ProxyConnectionError, ProxyTimeoutError)):
        return ERROR_PROXY
    if isinstance(error, tibiapy.errors.SiteMaintenanceError):
        return ERROR_MAINTENANCE
    if isinstance(error, tibiapy.errors.NetworkError):
        return ERROR_NETWORK
    return None


# --- LISTADO ---
def build_auction_filters(vocation=None, pvp_type=None, world=None, min_level=None, max_level=None,
                          order_by=None, direction=None):
    """Filtros de tibia.py. vocation, pvp_type, order_by y direction son nombres de sus enums."""
    return AuctionFilters(
        vocation=enums.AuctionVocationFilter[vocation] if vocation else None,
        pvp_type=enums.PvpTypeFilter[pvp_type] if pvp_type else None,
        world=world,
        min_level=min_level,
        max_level=max_level,
        order_by=enums.AuctionOrderBy[order_by],
        order=enums.AuctionOrderDirection[direction],
    )


async def fetch_auctions_page(client, number, filters):
    """Página `number` del listado de subastas (objeto de tibia.py con entries, results_count, total_pages)."""
    response = await client.fetch_current_auctions(number, filters)
    return response.data


async def fetch_available_worlds(client):
    """Mundos que ofrece el filtro del bazaar."""
    response = await client.fetch_current_auctions(1)
    filters = response.data.filters
    return set(filters.available_worlds or []) if filters else set()


# --- FICHA DETALLADA ---
_details_cache = {}  # { auction_id: (hora, datos) }


async def fetch_auction_details(auction_id):
    """
    Datos de la página de la subasta: {"general": {etiqueta: valor}, "skills": [(skill, nivel)]}.
    None si no se pudo leer.
    """
    cached = _details_cache.get(auction_id)
    if cached and time.monotonic() - cached[0] < DETAILS_CACHE_SECONDS:
        return cached[1]

    try:
        async with create_session() as session:
            async with session.get(AUCTION_URL.format(auction_id=auction_id)) as response:
                if response.status != 200:
                    logger.warning(f"⚠️ HTTP {response.status} al leer la subasta {auction_id}")
                    return None
                html = await response.text()
    except Exception as e:
        logger.error(f"❌ Error al leer la subasta {auction_id}: {e}")
        return None

    soup = BeautifulSoup(html, "lxml")
    general, skills = {}, []
    for table in soup.select("table.TableContent"):
        for row in table.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in row.find_all("td")]
            if len(cells) == 3 and cells[0] in MAIN_SKILLS and re.fullmatch(r"\d+", cells[1]):
                skills.append((cells[0], int(cells[1])))  # Fila de skills: nombre | nivel | %
                continue
            for cell in cells:
                label, sep, value = cell.partition(":")
                # Solo pares "Etiqueta: valor" cortos (la cabecera de la página es un bloque largo)
                if sep and value.strip() and len(label) < 40 and len(value) < 80:
                    general.setdefault(label.strip(), value.replace("\xa0", " ").strip())

    data = {"general": general, "skills": skills} if general else None
    _details_cache[auction_id] = (time.monotonic(), data)
    return data


# --- IMÁGENES ---
async def fetch_images(urls):
    """{url: bytes} de las imágenes (las que no se puedan descargar quedan fuera)."""
    async with create_session() as session:
        async def fetch(url):
            # Dos intentos: con un proxy que cambia de IP, alguna petición suelta puede fallar
            for attempt in range(2):
                try:
                    async with session.get(url) as response:
                        if response.status == 200 and response.content_type.startswith("image/"):
                            return url, await response.read()
                except Exception as e:
                    if attempt:
                        logger.warning(f"⚠️ No se pudo descargar la imagen {url}: {e}")
            return url, None

        results = await asyncio.gather(*(fetch(u) for u in urls))
    return {url: data for url, data in results if data is not None}
