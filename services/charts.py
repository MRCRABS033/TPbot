"""
Gráficas que el bot envía como imagen (PNG) en Discord.

Se dibujan en coordenadas de píxeles (1 unidad = 1 px) para que las barras redondeadas,
los círculos y los textos queden exactos. Las fuentes van incluidas en assets/fonts para
que la imagen se vea igual en Windows y en el servidor (Docker no trae fuentes).
"""
import io
import os

import discord
import matplotlib

matplotlib.use("Agg")  # Sin ventana: solo genera la imagen
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import Circle, FancyBboxPatch

from config import TEAM_NAME, WATCHED_PLAYERS

# --- FUENTES ---
FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "fonts")
for _file in ("Cinzel.ttf", "Lato-Regular.ttf", "Lato-Bold.ttf"):
    _path = os.path.join(FONT_DIR, _file)
    if os.path.exists(_path):
        font_manager.fontManager.addfont(_path)
TITLE_FONT = "Cinzel"
BODY_FONT = "Lato"

# --- COLORES ---
BACKGROUND = "#120e1a"
BAR = "#8b5cf6"
MVP = "#e8c46a"
TEXT_PRIMARY = "#f4f1fb"
TEXT_SECONDARY = "#9a90b5"
TEXT_MUTED = "#6d6585"
GRID = "#2a2438"
WATERMARK = "#1c1629"

# --- MEDIDAS (px) ---
WIDTH = 1200
LEFT = 48              # Margen izquierdo del título
NAME_RIGHT = 340       # Los nombres se alinean a la derecha en esta x
BARS_LEFT = 360        # Inicio de las barras
TRACK_WIDTH = 580      # Ancho máximo de la zona de barras
LEVELS_RIGHT = 1158    # Columna "ingreso → actual"
HEADER = 130           # Alto de la cabecera (título y subtítulo)
ROW = 70               # Alto de cada fila
BAR_HEIGHT = 36
FOOTER = 40
DPI = 100
PT = 72 / DPI          # Convierte px a puntos tipográficos


def _draw_watermark(ax, cx, cy, scale):
    """Tres círculos y el nombre del team, muy tenues, al fondo."""
    radius = 75 * scale
    for dx, dy in ((-92, -92), (92, -92), (0, 92)):
        ax.add_patch(Circle((cx + dx * scale, cy + dy * scale), radius, color=WATERMARK, zorder=0, linewidth=0))
    ax.text(cx, cy + 250 * scale, TEAM_NAME.upper(), ha="center", va="center", color=WATERMARK,
            fontfamily=TITLE_FONT, fontsize=40 * scale * PT, zorder=0)


def build_group_progress_chart():
    """
    Niveles ganados por cada personaje desde que ingresó al grupo, de mayor a menor.
    El primero (si ganó niveles) se destaca como MVP. Devuelve un discord.File o None si no hay grupo.
    """
    if not WATCHED_PLAYERS:
        return None

    rows = []
    for data in WATCHED_PLAYERS.values():
        curr = data.get("current_level", 0)
        initial = data.get("initial_level", curr)
        rows.append((data["char_name"], data["owner"], curr - initial, initial, curr))
    rows.sort(key=lambda r: (r[2], r[4]), reverse=True)

    height = HEADER + ROW * len(rows) + FOOTER
    fig = plt.figure(figsize=(WIDTH / DPI, height / DPI), dpi=DPI)
    fig.patch.set_facecolor(BACKGROUND)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, WIDTH)
    ax.set_ylim(height, 0)  # y crece hacia abajo, como en una página
    ax.axis("off")

    # Marca de agua centrada en la zona de las barras
    rows_top, rows_bottom = HEADER, HEADER + ROW * len(rows)
    scale = min(1.0, (rows_bottom - rows_top) / 650)
    if scale >= 0.5:  # Con pocos personajes no cabe y se vería como una mancha
        _draw_watermark(ax, BARS_LEFT + TRACK_WIDTH * 0.66, rows_top + (rows_bottom - rows_top) * 0.4, scale)

    # Cabecera
    ax.text(LEFT, 45, "NIVELES GANADOS", color=TEXT_PRIMARY, fontfamily=TITLE_FONT,
            fontsize=32 * PT, va="center", ha="left")
    ax.text(LEFT, 86, f"Team {TEAM_NAME}  ·  desde que ingresó cada uno", color=TEXT_SECONDARY,
            fontfamily=BODY_FONT, fontsize=16 * PT, va="center", ha="left")
    ax.text(LEVELS_RIGHT, 86, "ingreso → actual", color=TEXT_MUTED, fontfamily=BODY_FONT,
            fontsize=14 * PT, va="center", ha="right")

    # Cuadrícula vertical tenue
    for k in range(1, 5):
        x = BARS_LEFT + TRACK_WIDTH * k / 4
        ax.plot([x, x], [rows_top - 5, rows_bottom + 10], color=GRID, linewidth=1.2, zorder=1)

    max_gain = max(max(r[2] for r in rows), 1)
    max_bar = TRACK_WIDTH * 0.95
    for i, (char, owner, gain, initial, curr) in enumerate(rows):
        cy = HEADER + ROW * i + ROW / 2
        is_mvp = i == 0 and gain > 0

        # Nombre y dueño
        ax.text(NAME_RIGHT, cy - 12, char, color=TEXT_PRIMARY, fontfamily=BODY_FONT, fontweight="bold",
                fontsize=17 * PT, va="center", ha="right", zorder=3)
        ax.text(NAME_RIGHT, cy + 12, owner, color=TEXT_SECONDARY, fontfamily=BODY_FONT,
                fontsize=14 * PT, va="center", ha="right", zorder=3)

        if gain > 0:
            width = max(max_bar * gain / max_gain, 20)
            ax.add_patch(FancyBboxPatch(
                (BARS_LEFT, cy - BAR_HEIGHT / 2), width, BAR_HEIGHT,
                boxstyle="round,pad=0,rounding_size=8",
                facecolor=MVP if is_mvp else BAR, linewidth=0, zorder=2,
            ))
            ax.text(BARS_LEFT + width + 10, cy, f"+{gain}", color=TEXT_PRIMARY, fontfamily=BODY_FONT,
                    fontweight="bold", fontsize=17 * PT, va="center", ha="left", zorder=3)
            if is_mvp:
                # La estrella se dibuja como figura (Lato no trae el carácter ★)
                label_y = cy - BAR_HEIGHT / 2 - 12
                ax.plot(BARS_LEFT + 6, label_y, marker="*", markersize=10, color=MVP, linewidth=0, zorder=3)
                ax.text(BARS_LEFT + 17, label_y, "MVP", color=MVP, fontfamily=BODY_FONT,
                        fontweight="bold", fontsize=13 * PT, va="center", ha="left", zorder=3)
        else:
            # Sin progreso: un punto en lugar de barra
            ax.add_patch(Circle((BARS_LEFT + 2, cy), 4, color=TEXT_MUTED, zorder=2, linewidth=0))
            ax.text(BARS_LEFT + 17, cy, f"{gain:+d}", color=TEXT_MUTED, fontfamily=BODY_FONT,
                    fontsize=15 * PT, va="center", ha="left", zorder=3)

        # Nivel de ingreso → actual
        ax.text(LEVELS_RIGHT, cy, f"{initial} → {curr}", color=TEXT_SECONDARY, fontfamily=BODY_FONT,
                fontsize=16 * PT, va="center", ha="right", zorder=3)

    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", facecolor=BACKGROUND)
    plt.close(fig)
    buffer.seek(0)
    return discord.File(buffer, filename="progreso_grupo.png")
