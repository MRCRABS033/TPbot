"""#bosses: boss boosted del día, su imagen e información, y la hora de las actividades. No sabe nada de Discord."""
import logging
import re
from contextlib import aclosing
from datetime import datetime, timedelta

import pytz

from src.api.http import create_session
from src.shared.pixel_art import upscale_image

logger = logging.getLogger("TibiaBot")

# La hora que se escribe al crear la actividad se interpreta en hora de México (Ciudad de México).
# Discord no informa la zona horaria de cada usuario; en el anuncio cada quien la ve en su hora local.
LOCAL_TZ = pytz.timezone("America/Mexico_City")


def parse_activity_time(text, now=None):
    """
    '9:30 pm', '9pm', '21:30', '21' -> datetime de hoy (o mañana si esa hora ya pasó), hora de México.
    Devuelve None si no se entiende.
    """
    match = re.fullmatch(r"\s*(\d{1,2})(?::(\d{2}))?\s*(a|p)?\.?\s*(m\.?)?\s*", text.lower())
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2) or 0)
    period = match.group(3)
    if period:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if period == "p" else 0)
    if hour > 23 or minute > 59:
        return None

    now = now or datetime.now(LOCAL_TZ)
    day = now.date()
    if (hour, minute) < (now.hour, now.minute - 5):
        day += timedelta(days=1)  # Esa hora ya pasó hoy: es mañana
    return LOCAL_TZ.localize(datetime(day.year, day.month, day.day, hour, minute))


class BossService:
    def __init__(self, api, wiki, fetch_info, players, session_factory=create_session):
        self._api = api
        self._wiki = wiki
        self._fetch_info = fetch_info
        self._players = players
        self._session_factory = session_factory
        self.current_boss = None
        self.image = None  # (bytes, nombre_de_archivo) de la imagen agrandada del boss
        self.info = None   # Clase, vida, experiencia, cooldown y zona (TibiaWiki)

    # --- BOSS DEL DÍA ---
    async def refresh(self):
        """Consulta el boss del día. Devuelve True si cambió (o si se consiguió la imagen que faltaba)."""
        async with self._session_factory() as session:
            boss = await self._api.fetch_boosted_boss(session)
        if not boss:
            return False
        if self.current_boss and boss["name"] == self.current_boss["name"]:
            if not self.image:
                # La imagen falló antes: se reintenta (si ahora funciona, la tarjeta se republica)
                self.image = await self._download_image(boss)
                return self.image is not None
            return False
        self.current_boss = boss
        self.image = await self._download_image(boss)
        async with self._session_factory() as session:
            self.info = await self._fetch_info(session, boss["name"])
        logger.info(f"👑 Boss boosted de hoy: {boss['name']}")
        return True

    async def ensure_info(self):
        """Reintenta consultar la información del boss si antes no se pudo."""
        if self.info is None:
            async with self._session_factory() as session:
                self.info = await self._fetch_info(session, self.current_boss["name"])

    async def _download_image(self, boss):
        """Descarga la imagen del boss y la agranda. Devuelve (bytes, nombre_de_archivo) o None."""
        async with aclosing(self._wiki.iter_boss_images(boss["name"], boss.get("image_url"))) as images:
            async for data in images:
                try:
                    return upscale_image(data, "boss")
                except Exception as e:
                    logger.warning(f"⚠️ No se pudo usar una imagen de {boss['name']}: {e}")
        logger.warning(f"⚠️ No se pudo descargar la imagen de {boss['name']}; Discord la cargará desde la API")
        return None

    # --- PERSONAJES DEL GRUPO ---
    def has_players(self):
        return bool(self._players)

    def sorted_players(self):
        """[(clave, datos)] ordenados por dueño y personaje."""
        return sorted(self._players.items(), key=lambda kv: (kv[1]["owner"].lower(), kv[1]["char_name"].lower()))

    def participants(self, keys):
        """Datos de los personajes elegidos que siguen en el grupo."""
        return [self._players[k] for k in keys if k in self._players]
