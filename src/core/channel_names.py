# --- NOMBRES DE LOS CANALES DE DISCORD ---
# El bot busca los canales por nombre exacto.
# Si renombras un canal en Discord, actualízalo también aquí.

# Panel del grupo (channels/tpbot)
CHANNEL_TPBOT = "tpbot"

# Avisos de muertes (channels/cementerio)
CHANNEL_DEATHS = "cementerio"

# Lista automática de jugadores Free Account online (channels/frees, !free)
CHANNEL_FREES = "frees"

# Lista automática de personajes monitoreados online (channels/onlines)
CHANNEL_ONLINES = "onlines"

# Alertas de subida de nivel con progreso en el team (channels/onlines)
CHANNEL_LEVEL = "level-up"
CHANNEL_LEVEL_ALT = "levels"  # Alternativo si no existe CHANNEL_LEVEL

# Anuncio semanal del empleado del mes (channels/empleado)
CHANNEL_EMPLEADO = "empleado-del-mes"
CHANNEL_EMPLEADO_ALT = "empleado"  # Alternativo si no existe CHANNEL_EMPLEADO

# Lista de hunteds online y avisos de conexión, nivel y muerte de hunteds (channels/hunteds)
CHANNEL_HUNTEDS = "hunteds"

# Boss boosted del día con el botón para organizar una actividad en grupo (channels/bosses)
CHANNEL_BOSSES = "bosses"

# Anuncios de las actividades en grupo creadas desde #bosses
CHANNEL_ANUNCIOS = "anuncios"

# Catálogo del Char Bazaar de tibia.com con filtros y fichas detalladas (bazaar)
CHANNEL_BAZAAR = "bazaar"


# --- BÚSQUEDA DE CANALES ---
def _normalize(name: str) -> str:
    """'💀・Cementerio' -> 'cementerio'. Quita emojis, símbolos y mayúsculas."""
    return "".join(c for c in name if c.isalnum() or c in "-_").strip("-_").lower()


def find_channel(channels, name: str):
    """
    Busca un canal de texto por nombre ignorando los íconos/emojis que tenga.
    Así '💀・cementerio', '🪦-cementerio' y 'cementerio' cuentan como el mismo canal.
    """
    import discord

    target = _normalize(name)
    return next(
        (c for c in channels if isinstance(c, discord.TextChannel) and _normalize(c.name) == target),
        None,
    )
