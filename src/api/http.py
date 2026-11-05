"""Sesiones HTTP de las APIs: así el resto del bot no depende directamente de aiohttp."""
import aiohttp


def create_session(**kwargs):
    """Sesión HTTP para pasar a las funciones de src/api."""
    return aiohttp.ClientSession(**kwargs)
