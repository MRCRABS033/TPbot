"""
Constantes del Char Bazaar de tibia.com: direcciones, tamaños del catálogo y opciones de los filtros.
Los valores de los filtros son los nombres de los enums de tibia.py (tibiapy.enums).
"""

# --- DIRECCIONES ---
BAZAAR_URL = "https://www.tibia.com/charactertrade/?subtopic=currentcharactertrades"
AUCTION_URL = BAZAAR_URL + "&page=details&auctionid={auction_id}&source=overview"

# --- CATÁLOGO ---
CARDS_PER_IMAGE = 12            # 2 páginas del libro x 2 filas x 3 cartas
TIBIA_PAGE_SIZE = 25            # Subastas por página en tibia.com
# El precio no es un filtro de tibia.com: para filtrar por precio el bot descarga todas las páginas
# de la búsqueda (en paralelo) y filtra él mismo. Solo se permite si son como máximo estas páginas
# (25 subastas cada una), para no sobrecargar tibia.com; si son más, se pide acotar los filtros.
MAX_PAGES_FOR_PRICE_FILTER = 20
PARALLEL_REQUESTS = 5           # Peticiones simultáneas a tibia.com
SEARCH_TIMEOUT = 2 * 60 * 60    # Los botones de un resultado funcionan durante 2 horas
IMAGE_CACHE_SECONDS = 5 * 60    # Una página ya dibujada se reutiliza durante 5 minutos
REQUEST_TIMEOUT = 20            # Segundos máximos por petición a tibia.com
PAGE_LOAD_TIMEOUT = 45          # Segundos máximos para cargar una página del catálogo
DETAILS_CACHE_SECONDS = 10 * 60  # La ficha detallada de una subasta se recuerda 10 minutos

# --- OPCIONES DE LOS FILTROS: (texto a mostrar, valor) ---
VOCATION_OPTIONS = [
    ("Todas las vocaciones", None),
    ("Knight", "KNIGHT"),
    ("Paladin", "PALADIN"),
    ("Sorcerer", "SORCERER"),
    ("Druid", "DRUID"),
    ("Monk", "MONK"),
    ("Sin vocación", "NONE"),
]

PVP_OPTIONS = [
    ("Todos los tipos de PvP", None),
    ("Open PvP", "OPEN_PVP"),
    ("Optional PvP", "OPTIONAL_PVP"),
    ("Hardcore PvP", "HARDCORE_PVP"),
    ("Retro Open PvP", "RETRO_OPEN_PVP"),
    ("Retro Hardcore PvP", "RETRO_HARDCORE_PVP"),
]

# Discord admite 25 opciones por menú y Tibia tiene ~100 mundos: el menú de mundo muestra los
# resultados del buscador (deja sitio para "Todos los mundos" y el mundo ya elegido)
MAX_WORLD_RESULTS = 23

# (texto, (campo AuctionOrderBy, dirección AuctionOrderDirection))
ORDER_OPTIONS = [
    ("Terminan antes", ("END_DATE", "LOWEST_EARLIEST")),
    ("Recién publicadas", ("START_DATE", "HIGHEST_LATEST")),
    ("Mayor oferta", ("BID", "HIGHEST_LATEST")),
    ("Menor oferta", ("BID", "LOWEST_EARLIEST")),
    ("Mayor nivel", ("LEVEL", "HIGHEST_LATEST")),
    ("Menor nivel", ("LEVEL", "LOWEST_EARLIEST")),
]
