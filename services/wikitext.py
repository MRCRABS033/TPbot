"""
Lectura del formato de texto de TibiaWiki (wikitext): plantillas {{...}}, enlaces [[...]]
y coordenadas de mapa. No sabe nada de bosses: sirve para cualquier página de la wiki.
"""
import re

from constants.tibia import TIBIAMAPS_URL


def split_params(text):
    """Separa por '|' respetando {{plantillas}} y [[enlaces]] anidados."""
    params, depth, current, i = [], 0, [], 0
    while i < len(text):
        pair = text[i:i + 2]
        if pair in ("{{", "[["):
            depth += 1
            current.append(pair)
            i += 2
            continue
        if pair in ("}}", "]]") and depth > 0:
            depth -= 1
            current.append(pair)
            i += 2
            continue
        if text[i] == "|" and depth == 0:
            params.append("".join(current))
            current = []
        else:
            current.append(text[i])
        i += 1
    params.append("".join(current))
    return params


def find_template(text, name):
    """Contenido de la primera {{name|...}} (sin las llaves), respetando anidación."""
    start = text.find("{{" + name)
    if start < 0:
        return None
    depth, i = 0, start
    while i < len(text):
        if text.startswith("{{", i):
            depth += 1
            i += 2
        elif text.startswith("}}", i):
            depth -= 1
            i += 2
            if depth == 0:
                return text[start + 2:i - 2]
        else:
            i += 1
    return None


def parse_template(text):
    """'{{Ability|Ice Wave|1200+|Ice|scene=...}}' -> ('Ability', ['Ice Wave', '1200+', 'Ice'], {'scene': ...})."""
    text = text.strip()
    if text.startswith("{{") and text.endswith("}}"):
        text = text[2:-2]
    parts = split_params(text)
    positional, named = [], {}
    for part in parts[1:]:
        key, sep, value = part.partition("=")
        if sep and re.fullmatch(r"\s*[\w ]+\s*", key) and not key.strip().isdigit():
            named[key.strip().lower()] = value.strip()
        else:
            positional.append(part.strip())
    return parts[0].strip(), positional, named


def parse_fields(template_content):
    """Contenido de una plantilla tipo infobox -> { campo: valor } (campos en minúscula)."""
    fields = {}
    for part in split_params(template_content)[1:]:
        key, sep, value = part.partition("=")
        if sep:
            fields[key.strip().lower()] = value.strip()
    return fields


def _mapper_link(params):
    """{{Mapper Coords|text=aquí|132.94|122.246|6|...}} -> enlace al mapa de tibiamaps.io."""
    _, positional, named = parse_template("{{Mapper Coords|" + params + "}}")
    text = named.get("text", "mapa")
    try:
        # Formato de TibiaWiki: "sector.posición" (256 posiciones por sector)
        x_sector, x_pos = positional[0].split(".")
        y_sector, y_pos = positional[1].split(".")
        x, y, z = int(x_sector) * 256 + int(x_pos), int(y_sector) * 256 + int(y_pos), int(positional[2])
        return f"[{text}]({TIBIAMAPS_URL.format(x=x, y=y, z=z)})"
    except (IndexError, ValueError):
        return text


def clean_wikitext(text):
    """Convierte texto de la wiki en texto de Discord: enlaces internos -> texto, mapas -> enlace."""
    text = re.sub(r"\{\{Mapper Coords\|([^{}]*)\}\}", lambda m: _mapper_link(m.group(1)), text)
    while re.search(r"\{\{[^{}]*\}\}", text):
        text = re.sub(r"\{\{[^{}]*\}\}", "", text)                   # Otras plantillas (también anidadas)
    text = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", text)     # [[Página|texto]] -> texto
    text = re.sub(r"<[^>]+>", " ", text)                             # Etiquetas HTML
    text = text.replace("'''", "").replace("''", "")
    return re.sub(r"\s+", " ", text).strip(" ,.;")
