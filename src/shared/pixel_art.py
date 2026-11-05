"""Agranda las imágenes de bosses y criaturas de Tibia sin perder la nitidez del pixel art."""
import io

from PIL import Image, ImageSequence

IMAGE_SCALE = 3  # Las imágenes (64x64) se agrandan sin suavizar para que se vean nítidas


def upscale_image(data, name):
    """Devuelve (bytes, nombre_de_archivo). Mantiene la animación si la tiene."""
    image = Image.open(io.BytesIO(data))
    out = io.BytesIO()
    if getattr(image, "n_frames", 1) > 1:
        frames, durations = [], []
        for frame in ImageSequence.Iterator(image):
            rgba = frame.convert("RGBA")
            frames.append(rgba.resize((rgba.width * IMAGE_SCALE, rgba.height * IMAGE_SCALE), Image.NEAREST))
            durations.append(frame.info.get("duration", 100))
        frames[0].save(out, format="GIF", save_all=True, append_images=frames[1:],
                       duration=durations, loop=0, disposal=2)
        return out.getvalue(), f"{name}.gif"
    rgba = image.convert("RGBA")
    rgba.resize((rgba.width * IMAGE_SCALE, rgba.height * IMAGE_SCALE), Image.NEAREST).save(out, format="PNG")
    return out.getvalue(), f"{name}.png"
