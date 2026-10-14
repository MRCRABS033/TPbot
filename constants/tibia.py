"""
Constantes del juego y de las fuentes de datos de Tibia.
Traducciones al español de los términos que vienen en inglés desde TibiaWiki.
"""

# --- FUENTES DE DATOS ---
TIBIAWIKI_API = "https://tibia.fandom.com/api.php"
# Mapa interactivo: {x},{y},{z} en coordenadas absolutas del juego
TIBIAMAPS_URL = "https://tibiamaps.io/map#{x},{y},{z}:2"

# --- ELEMENTOS Y DAÑO ---
# Nombre del elemento en la wiki -> nombre a mostrar
ELEMENTS = {
    "physical": "físico", "earth": "tierra", "fire": "fuego", "energy": "energía", "ice": "hielo",
    "holy": "sagrado", "death": "muerte", "poison": "veneno", "drown": "ahogamiento",
    "manadrain": "drenaje de maná", "mana drain": "drenaje de maná",
    "lifedrain": "drenaje de vida", "life drain": "drenaje de vida", "hpdrain": "drenaje de vida",
}

# Campo del infobox con el % de daño que recibe el boss -> nombre del elemento
DAMAGE_MODS = {
    "physicalDmgMod": "Físico", "earthDmgMod": "Tierra", "fireDmgMod": "Fuego", "energyDmgMod": "Energía",
    "iceDmgMod": "Hielo", "holyDmgMod": "Sagrado", "deathDmgMod": "Muerte", "hpDrainDmgMod": "Drenaje de vida",
    "drownDmgMod": "Ahogamiento",
}

# --- LOOT ---
# (rareza en la wiki, nombre a mostrar), en el orden en que se muestran
RARITIES = [
    ("always", "Siempre"), ("very common", "Muy común"), ("common", "Común"), ("uncommon", "Poco común"),
    ("semi-rare", "Semi-raro"), ("rare", "Raro"), ("very rare", "Muy raro"),
]
# Grupo para los objetos cuya rareza no indica la wiki
NO_RARITY = "Posible loot"
