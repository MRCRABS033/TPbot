"""
Hora de ingreso de los jugadores conectados, para las listas de #onlines y #hunteds.
"""
from datetime import datetime, timedelta

import discord
import pytz

# Server Save diario: todos los jugadores se desconectan a esta hora (hora de Alemania)
SERVER_SAVE_TZ = pytz.timezone("Europe/Berlin")
SERVER_SAVE_HOUR = 10


def last_server_save(now):
    """Hora (UTC) del último Server Save: nadie puede llevar conectado desde antes."""
    berlin = now.astimezone(SERVER_SAVE_TZ)
    save = SERVER_SAVE_TZ.localize(datetime(berlin.year, berlin.month, berlin.day, SERVER_SAVE_HOUR))
    if berlin < save:
        save -= timedelta(days=1)
    return save


def parse_api_time(value):
    """'2026-10-14T16:28:04Z' -> datetime (UTC), o None."""
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


class LoginTracker:
    def __init__(self):
        self.since = {}           # { clave del jugador: hora de ingreso (UTC) }
        self.approximate = set()  # Jugadores cuya hora de ingreso es aproximada (la del arranque del bot)
        self.initialized = False  # False hasta la primera revisión con datos

    @property
    def needs_api_times(self):
        """True en la primera revisión: hay que pasar los last_login de la API a update()."""
        return not self.initialized

    def update(self, online_keys, last_logins=None):
        """
        Actualiza con los jugadores conectados ahora. `last_logins` ({clave: datetime}) solo se usa
        en la primera revisión, para no perder el tiempo de juego de quienes ya estaban conectados.
        """
        now = discord.utils.utcnow()
        oldest_possible = last_server_save(now)
        for key in list(self.since):
            if key not in online_keys:
                del self.since[key]  # Se desconectó
                self.approximate.discard(key)
        for key in online_keys:
            if key not in self.since:
                if self.initialized:
                    self.since[key] = now  # Recién conectado (se detecta en menos de un minuto)
                    continue
                # Arranque del bot: la hora de la API, si es creíble. A veces la API trae un last_login
                # viejo (de antes del Server Save, imposible para alguien conectado): se descarta.
                api_time = (last_logins or {}).get(key)
                if api_time is not None and oldest_possible <= api_time <= now:
                    self.since[key] = api_time
                else:
                    self.since[key] = now
                    self.approximate.add(key)
        self.initialized = True

    def text(self, key):
        """'en línea desde 14:32 (hace 2 horas)' en la hora local de quien lo mira (Discord lo convierte)."""
        since = self.since.get(key)
        if not since:
            return ""
        ts = int(since.timestamp())
        if key in self.approximate:
            return f"en línea desde antes de <t:{ts}:t>"  # Ya estaba conectado cuando arrancó el bot
        return f"en línea desde <t:{ts}:t> (<t:{ts}:R>)"
