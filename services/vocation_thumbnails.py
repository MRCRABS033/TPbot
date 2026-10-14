"""
Outfits de vocación en grande para las listas de #onlines, #frees y #hunteds.

Los GIF de TibiaWiki miden 64x64 pero el personaje ocupa solo la esquina (~35 px), así que en Discord
se ve diminuto. Aquí se recortan los bordes vacíos y se amplían x2 (pixel art nítido); el mensaje los
lleva adjuntos y los embeds los referencian con attachment://. Si no se pudieron preparar, se usa la URL
de TibiaWiki como antes.
"""
import io
import logging

import aiohttp
import discord
from PIL import Image, ImageSequence

from services.tibia_wiki import VOCATION_OUTFITS, fetch_wiki_image, vocation_image_url

THUMBNAIL_MAX_SIZE = 80  # Discord muestra las miniaturas a 80x80 como máximo
ATTACHMENT_PREFIX = "attachment://"

logger = logging.getLogger("TibiaBot")

_thumbnails = {}  # { vocación: bytes del GIF ampliado }


def _file_name(key):
    return f"voc_{key}.gif"


def _enlarge(gif_bytes):
    """Recorta el espacio vacío de todos los cuadros y amplía el GIF (sin suavizar) conservando la animación."""
    source = Image.open(io.BytesIO(gif_bytes))
    frames = [frame.convert("RGBA") for frame in ImageSequence.Iterator(source)]
    durations = [frame.info.get("duration", 100) for frame in ImageSequence.Iterator(source)]

    boxes = [f.getbbox() for f in frames if f.getbbox()]
    if not boxes:
        return None
    left, top = min(b[0] for b in boxes), min(b[1] for b in boxes)
    right, bottom = max(b[2] for b in boxes), max(b[3] for b in boxes)
    side = max(right - left, bottom - top)
    scale = max(2, THUMBNAIL_MAX_SIZE // side)

    enlarged = []
    for frame in frames:
        # Cuadrado, con el personaje centrado y apoyado abajo
        square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        crop = frame.crop((left, top, right, bottom))
        square.paste(crop, ((side - crop.width) // 2, side - crop.height))
        enlarged.append(square.resize((side * scale, side * scale), Image.NEAREST))

    out = io.BytesIO()
    enlarged[0].save(
        out, format="GIF", save_all=True, append_images=enlarged[1:],
        duration=durations, loop=0, disposal=2,
    )
    return out.getvalue()


async def load_vocation_thumbnails():
    """Descarga y prepara los outfits (una vez, al arrancar). Los que fallen usan la URL de TibiaWiki."""
    async with aiohttp.ClientSession() as session:
        for key, file_name in VOCATION_OUTFITS.items():
            if key in _thumbnails:
                continue
            data = await fetch_wiki_image(session, file_name)
            try:
                image = _enlarge(data) if data else None
            except Exception as e:
                image = None
                logger.warning(f"No se pudo ampliar {file_name}: {e}")
            if image:
                _thumbnails[key] = image


def vocation_thumbnail(key):
    """URL para set_thumbnail: el outfit ampliado (adjunto) o, si no está, el de TibiaWiki."""
    if key in _thumbnails:
        return ATTACHMENT_PREFIX + _file_name(key)
    return vocation_image_url(key)


def thumbnail_files(embeds):
    """Archivos que hay que adjuntar al mensaje para las miniaturas attachment:// de estos embeds."""
    files = []
    for embed in embeds:
        url = embed.thumbnail.url if embed.thumbnail else None
        if url and url.startswith(ATTACHMENT_PREFIX):
            name = url[len(ATTACHMENT_PREFIX):]
            key = name[len("voc_"):-len(".gif")]
            if key in _thumbnails:
                files.append(discord.File(io.BytesIO(_thumbnails[key]), filename=name))
    return files
