"""
Información de criaturas desde TibiaWiki (en inglés). La lectura del wikitext está en
services/wikitext.py y las traducciones de términos en constants/tibia.py.

fetch_creature_info(session, nombre) devuelve un dict con:
  hp, exp, bosstiaryclass, cooldown, location, notes   (texto listo para Discord)
  abilities      lista de habilidades ("Ice Wave: 1200+ hielo")
  max_damage     daño máximo ("2.265 (1.000 físico, 1.265 muerte)")
  weak / strong / immune / heals   elementos según cuánto daño recibe (heals: lo curan)
  traits         rasgos (ve invisibles, inmune a parálisis, no huye...)
  loot           { rareza: [objetos] }
"""

import re

from config import logger
from constants.tibia import DAMAGE_MODS, ELEMENTS, NO_RARITY, RARITIES, TIBIAWIKI_API
from services.tibia_wiki import WIKI_HEADERS, wiki_file_names_for
from services.wikitext import clean_wikitext, find_template, parse_fields, parse_template, split_params

_cache = {}  # { nombre: dict | None }

def _element(text):
    return ELEMENTS.get(text.strip().lower(), text.strip().lower())

def _number(value):
    try:
        return f"{int(value):,}".replace(",", ".")
    except (TypeError, ValueError):
        return value

def _parse_abilities(value):
    content = find_template(value, "Ability List")
    if content is None:
        return []
    abilities = []
    for item in split_params(content)[1:]:
        item = item.strip()
        if not item:
            continue
        if not item.startswith("{{"):
            text = clean_wikitext(item)
            if text:
                abilities.append(text)
            continue

        name, pos, named = parse_template(item)
        name = name.lower()
        if name == "melee":
            damage = pos[0] if pos else named.get("damage", "?")
            abilities.append(f"Cuerpo a cuerpo: {damage}")
        elif name == "summon":
            if pos:
                limit = f" (máx. {pos[1]})" if len(pos) > 1 and pos[1] else ""
                abilities.append(f"Invoca: {clean_wikitext(pos[0])}{limit}")
        elif name == "healing":
            label = clean_wikitext(pos[0]) if pos else "Se cura"
            amount = named.get("range") or (pos[1] if len(pos) > 1 else "")
            abilities.append(f"Curación: {label}" + (f" ({clean_wikitext(amount)})" if amount else ""))
        elif name == "ability":
            label = clean_wikitext(pos[0]) if pos else "Habilidad"
            damage = pos[1] if len(pos) > 1 else named.get("damage", "")
            element = pos[2] if len(pos) > 2 else named.get("element", "")
            condition = named.get("condition", "")
            element = _element(element) if element else ""
            if element == label.lower():
                element = ""  # Ej: "Invisible: invisible"
            detail = " ".join(x for x in (clean_wikitext(damage), element, condition) if x)
            abilities.append(f"{label}: {detail}" if detail else label)
    return abilities


def _parse_max_damage(value):
    content = find_template(value, "Max Damage")
    if content is None:
        return None
    _, _, named = parse_template("{{" + content + "}}")
    parts, total, exact = [], 0, True
    for element, amount in named.items():
        parts.append(f"{_number(amount.rstrip('+'))}{'+' if amount.endswith('+') else ''} {_element(element)}")
        try:
            total += int(amount.rstrip("+"))
        except ValueError:
            exact = False
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    return (f"{_number(total)} ({', '.join(parts)})" if exact else ", ".join(parts))

def _parse_loot(wikitext):
    content = find_template(wikitext, "Loot Table")
    if content is None:
        return {}
    loot = {}
    rarity_words = {word: label for word, label in RARITIES}
    for item in split_params(content)[1:]:
        if "Loot Item" not in item:
            continue
        _, pos, _ = parse_template(item.strip())
        rarity, name = NO_RARITY, None
        for p in pos:
            low = p.lower().strip()
            if low in rarity_words:
                rarity = rarity_words[low]
            elif not re.fullmatch(r"[\d\-?+ ]*", low) and name is None:
                name = clean_wikitext(p)
        if name:
            loot.setdefault(rarity, []).append(name)
    order = [label for _, label in RARITIES] + [NO_RARITY]
    return {r: loot[r] for r in order if r in loot}

def _parse_traits(fields):
    traits = []
    if fields.get("senseinvis", "").lower() in ("yes", "y"):
        traits.append("Ve la invisibilidad")
    if fields.get("paraimmune", "").lower() in ("yes", "y"):
        traits.append("Inmune a parálisis")
    runs_at = fields.get("runsat", "").strip()
    if runs_at == "0":
        traits.append("No huye con poca vida")
    elif runs_at.isdigit():
        traits.append(f"Huye con {_number(runs_at)} HP")
    walks = clean_wikitext(fields.get("walksthrough", ""))
    if walks and walks not in ("--", "?", "None"):
        traits.append("Camina sobre campos de: " + ", ".join(_element(w) for w in walks.split(",")))
    if fields.get("pushable", "").lower() in ("no", "n"):
        traits.append("No se puede empujar")
    return traits

def parse_infobox(wikitext):
    content = find_template(wikitext, "Infobox Creature")
    if content is None:
        return None
    fields = parse_fields(content)

    info = {}
    for field in ("hp", "exp", "bosstiaryclass", "cooldown", "location", "notes"):
        value = clean_wikitext(fields.get(field, ""))
        if value and value not in ("?", "--"):
            info[field] = value
    info["abilities"] = _parse_abilities(fields.get("abilities", ""))
    info["max_damage"] = _parse_max_damage(fields.get("maxdmg", ""))
    info["traits"] = _parse_traits(fields)
    info["weak"], info["strong"], info["immune"], info["heals"] = _parse_damage_mods(fields)
    info["loot"] = _parse_loot(wikitext)
    return info

async def fetch_creature_info(session, creature_name):
    """Información de la criatura (ver docstring del módulo). None si no se encuentra. Se guarda en memoria."""
    if creature_name in _cache:
        return _cache[creature_name]

    for page in dict.fromkeys(n[:-4] for n in wiki_file_names_for(creature_name)):
        params = {"action": "parse", "page": page, "prop": "wikitext", "format": "json", "redirects": 1}
        try:
            async with session.get(TIBIAWIKI_API, params=params, headers=WIKI_HEADERS) as response:
                if response.status != 200:
                    continue
                data = await response.json(content_type=None)
        except Exception as e:
            logger.warning(f"⚠️ Error al consultar TibiaWiki para {creature_name}: {e}")
            return None  # No se cachea: se reintenta la próxima vez
        wikitext = data.get("parse", {}).get("wikitext", {}).get("*")
        info = parse_infobox(wikitext) if wikitext else None
        if info is not None:
            _cache[creature_name] = info
            return info

    _cache[creature_name] = None
    return None