"""#boosted-creature: criatura boosted del día, su imagen e información. No sabe nada de Discord."""
import logging
from contextlib import aclosing

from src.api.http import create_session
from src.shared.pixel_art import upscale_image

logger = logging.getLogger("TibiaBot")


class CreatureService:
    def __init__(self, api, wiki, fetch_info, session_factory=create_session):
        self._api = api
        self._wiki = wiki
        self._fetch_info = fetch_info
        self._session_factory = session_factory
        self.current_creature = None
        self.image = None  # (bytes, nombre_de_archivo) de la imagen agrandada de la criatura
        self.info = None   # Vida, experiencia, habilidades, loot (TibiaWiki)

    async def refresh(self):
        """Consulta la criatura del día. Devuelve True si cambió (o si se consiguió la imagen que faltaba)."""
        async with self._session_factory() as session:
            creature = await self._api.fetch_boosted_creature(session)
        if not creature:
            return False
        if self.current_creature and creature["name"] == self.current_creature["name"]:
            if not self.image:
                # La imagen falló antes: se reintenta (si ahora funciona, la tarjeta se republica)
                self.image = await self._download_image(creature)
                return self.image is not None
            return False
        self.current_creature = creature
        self.image = await self._download_image(creature)
        async with self._session_factory() as session:
            self.info = await self._fetch_info(session, creature["name"])
        logger.info(f"🐾 Criatura boosted de hoy: {creature['name']}")
        return True

    async def ensure_info(self):
        """Reintenta consultar la información de la criatura si antes no se pudo."""
        if self.info is None:
            async with self._session_factory() as session:
                self.info = await self._fetch_info(session, self.current_creature["name"])

    async def _download_image(self, creature):
        """Descarga la imagen de la criatura y la agranda. Devuelve (bytes, nombre_de_archivo) o None."""
        async with aclosing(self._wiki.iter_boss_images(creature["name"], creature.get("image_url"))) as images:
            async for data in images:
                try:
                    return upscale_image(data, "creature")
                except Exception as e:
                    logger.warning(f"⚠️ No se pudo usar una imagen de {creature['name']}: {e}")
        logger.warning(f"⚠️ No se pudo descargar la imagen de {creature['name']}; Discord la cargará desde la API")
        return None
