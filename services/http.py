"""Creación de sesiones HTTP con un timeout por defecto.

Antes cada tracker abría `aiohttp.ClientSession()` sin timeout: si un request
se colgaba (TibiaData o tibia.com sin responder), el loop del tracker se
quedaba esperando para siempre y la función moría en silencio.

Todas las sesiones del bot se crean con `new_session()` para que el timeout
viva en un solo lugar: si hay que ajustarlo, se cambia aquí.
"""

import aiohttp

# Timeout total por request. TibiaData suele responder en 1-3 s; 20 s da
# margen de sobra sin dejar loops colgados.
DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=20)


def new_session(**kwargs):
    """Crea una ClientSession con el timeout por defecto del bot."""
    kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
    return aiohttp.ClientSession(**kwargs)
