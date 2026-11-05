"""Texto de los avisos de muerte. Lo usan #cementerio y #hunteds."""
from datetime import datetime


def format_killers(killers, player_infos):
    lines = []
    for k in killers:
        name = k.get("name", "?")
        if k.get("player"):
            url = f"https://www.tibia.com/community/?subtopic=characters&name={name.replace(' ', '+')}"
            line = f"[{name}]({url})"
            info = player_infos.get(name)
            if info:
                line += f" · {info.get('vocation', 'Sin vocación')} · Nivel {info.get('level', '?')}"
        else:
            line = name.title()
        if k.get("summon"):
            line += f" (invocación: {k['summon']})"
        # Límite de Discord: 1024 caracteres por campo
        if len("\n".join(lines + [line])) > 1020:
            lines.append("…")
            break
        lines.append(line)
    return "\n".join(lines)


def format_death_time(time_str):
    """Convierte la hora de la API en un timestamp que Discord muestra en la hora local de cada usuario."""
    try:
        ts = int(datetime.fromisoformat(time_str.replace("Z", "+00:00")).timestamp())
        return f"<t:{ts}:f> (<t:{ts}:R>)"
    except (AttributeError, ValueError):
        return time_str or "N/A"
