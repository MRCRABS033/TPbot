import io
import aiohttp
import re

from datetime import datetime, timedelta

import discord
from PIL import Image, ImageSequence
import pytz

from config import logger
from services.http import new_session
from services.tibia_wiki import wiki_file_names_for
LOCAL_TZ = pytz.timezone("America/Mexico_City")
class Utils:
    def __init__(self):
        pass

    @staticmethod
    def _upscale(data, image_scale):
        """Agranda la imagen sin suavizar (pixel art nítido). Mantiene la animación si la tiene."""
        image = Image.open(io.BytesIO(data))
        out = io.BytesIO()
        if getattr(image, "n_frames", 1) > 1:
            frames, durations = [], []
            for frame in ImageSequence.Iterator(image):
                rgba = frame.convert("RGBA")
                frames.append(rgba.resize((rgba.width * image_scale, rgba.height * image_scale), Image.NEAREST))
                durations.append(frame.info.get("duration", 100))
            frames[0].save(out, format="GIF", save_all=True, append_images=frames[1:],
                            duration=durations, loop=0, disposal=2)
            return out.getvalue(), "boss.gif"
        rgba = image.convert("RGBA")
        rgba.resize((rgba.width * image_scale, rgba.height * image_scale), Image.NEAREST).save(out, format="PNG")
        return out.getvalue(), "boss.png"

    @staticmethod
    async def download_creature_image(creature, wiki_file_name):
        """Descarga la imagen del boss o creature (TibiaWiki BR y, si falla, static.tibia.com). y la agranda.
        Devueelve (bytes, nombre_de_archivo) o None
        """
        urls = [wiki_file_name.format(name) for name in wiki_file_names_for(creature["name"])]
        if creature.get("image_url"):
            urls.append(creature["image_url"])

        async with new_session(headers={"User-Agent": "Mozilla/5.0 (TPbot)"}) as session:
            for url in urls:
                try:
                    async with session.get(url) as response:
                        if response.status != 200 or not response.content_type.startswith("image/"):
                            continue
                        data = await response.read()
                    return Utils._upscale(data, image_scale=3)
                except Exception as e:
                    logger.error(f"⚠️ No se pudo usar la imagen {url}: {e}")
        logger.warning(f"⚠️ No se pudo descargar ninguna imagen para {creature['name']}; Discord la cargara desde la API")
        return None

    @staticmethod
    def creature_media(cog):
        """(url para el embed, archivo abjunto o None) segun la imagen disponible."""
        if cog and cog.image:
            data, filename = cog.image
            return f"attachment://{filename}", discord.File(io.BytesIO(data), filename=filename)
        creature = getattr(cog, "current_creature", None) or getattr(cog, "current_boss", None)
        if cog and creature and creature.get("image_url"):
            #Ultimo recurso: que Discord cargue la imagen directamente desde la API
            return creature["image_url"], None
        return None, None

# --- HORA DE LA ACTIVIDAD ---

    @staticmethod
    def parse_activity_time(text):
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

        now = datetime.now(LOCAL_TZ)
        day = now.date()
        if (hour, minute) < (now.hour, now.minute - 5):
            day += timedelta(days=1)  # Esa hora ya pasó hoy: es mañana
        return LOCAL_TZ.localize(datetime(day.year, day.month, day.day, hour, minute))

    @staticmethod
    def _send_kwargs(embed, file, **extra):
        kwargs = {"embed": embed, **extra}
        if file:
            kwargs["file"] = file
        return kwargs


# --- EMBEDS ---
    @staticmethod
    def _number(value):
        """'28000' -> '28.000'."""
        try:
            return f"{int(value):,}".replace(",", ".")
        except (TypeError, ValueError):
            return value

    @staticmethod
    def format_activity_time(text):
        when = Utils.parse_activity_time(text)
        if not when:
            return text  # No se entendió: se muestra tal cual se escribió
        ts = int(when.timestamp())
        # Discord lo muestra en la hora local de cada persona
        return f"<t:{ts}:t> · <t:{ts}:R>\n<t:{ts}:D>"

    @staticmethod
    def _no_info(embed):
        embed.description = "No se encontró información de este boss en TibiaWiki."
        return embed
