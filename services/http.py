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

_session = None

def get_session():
    """Devuelve la sesion compartida del bot, creandola si hace falta.
    
        La session se crea dentro del event loop en el primer uso y se reutiliza siempre;
        si por algo quedo cerrada, se recrea sola. No la cierres al terminar un request:
        es de todo el bot.
    """
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(timeout=DEFAULT_TIMEOUT)
    return _session

async def close_session():
    """Cierra la sesion compartida del bot (al apagarlo)."""
    global _session
    if _session is not None and not _session.closed:
        await _session.close()
    _session = None