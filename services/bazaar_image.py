"""
Imagen del catálogo del Char Bazaar: un libro abierto con 12 cartas (2 páginas x 2 filas x 3).
Se dibuja con Pillow en coordenadas de píxeles. Las fuentes van en assets/fonts.
"""
import io
import os

from PIL import Image, ImageDraw, ImageFont

from services.bazaar import enum_text, format_tc, format_time_left

FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "fonts")

# --- COLORES ---
BACKGROUND = "#100c17"
WATERMARK = "#1b1526"
WATERMARK_TEXT = "3 puntos"
WATERMARK_ON_BOOK = "#1e1630"  # Muy tenue sobre las páginas
BOOK = "#1a1326"
BOOK_BORDER = "#a8843f"
BOOK_INNER = "#4a3b22"
SPINE = "#130d1c"
GOLD = "#d8b56a"
GOLD_DARK = "#8c6c33"
CARD = "#211833"
CARD_BORDER = "#4b3866"
CARD_ART = "#2a1f3d"
TEXT = "#f1ebfa"
MUTED = "#9a8fb3"

# --- MEDIDAS (px) ---
WIDTH, HEIGHT = 1600, 900
BOOK_BOX = (40, 30, WIDTH - 40, HEIGHT - 30)
PAGE_LEFT = (75, WIDTH // 2 - 30)          # x inicial y final de la página izquierda
PAGE_RIGHT = (WIDTH // 2 + 30, WIDTH - 75)
BANNER_Y = 78
CARD_W, CARD_H = 205, 330
CARD_GAP_X = 32
ROWS_Y = (115, 470)                         # y de la primera y segunda fila de cartas
OUTFIT_SCALE = 2
COLUMNS = 3


def _font(name, size):
    try:
        return ImageFont.truetype(os.path.join(FONT_DIR, name), size)
    except OSError:
        return ImageFont.load_default(size)


TITLE_FONT = _font("Cinzel.ttf", 24)
WATERMARK_FONT = _font("Cinzel.ttf", 22)
CODE_FONT = _font("Lato-Bold.ttf", 16)
NAME_FONT = _font("Lato-Bold.ttf", 15)
LABEL_FONT = _font("Lato-Regular.ttf", 12)
VALUE_FONT = _font("Lato-Bold.ttf", 12)
SMALL_FONT = _font("Lato-Regular.ttf", 11)
PRICE_FONT = _font("Lato-Bold.ttf", 16)
FOOTER_FONT = _font("Lato-Regular.ttf", 14)
FOOTER_BOLD = _font("Lato-Bold.ttf", 14)


def _fit(draw, text, font, max_width):
    """Recorta el texto con '…' para que quepa en max_width."""
    if draw.textlength(text, font=font) <= max_width:
        return text
    while text and draw.textlength(text + "…", font=font) > max_width:
        text = text[:-1]
    return text + "…"


# --- FONDO Y LIBRO ---
def _draw_watermark(image, color):
    """Marca de agua muy tenue ("3 puntos"), repetida en diagonal por toda la imagen."""
    layer = Image.new("RGBA", (WIDTH * 2, HEIGHT * 2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    text = WATERMARK_TEXT
    step_x, step_y = 260, 110
    for row, y in enumerate(range(0, HEIGHT * 2, step_y)):
        for x in range(-step_x + (row % 2) * step_x // 2, WIDTH * 2, step_x):
            draw.text((x, y), text, font=WATERMARK_FONT, fill=color)
    layer = layer.rotate(20, resample=Image.BICUBIC)
    left, top = (layer.width - WIDTH) // 2, (layer.height - HEIGHT) // 2
    image.alpha_composite(layer.crop((left, top, left + WIDTH, top + HEIGHT)))


def _draw_corner(draw, x, y, dx, dy, size=42, width=5):
    """Esquinero dorado en forma de L. dx/dy indican hacia dónde se abre (1 o -1)."""
    draw.line([(x, y), (x + dx * size, y)], fill=GOLD, width=width)
    draw.line([(x, y), (x, y + dy * size)], fill=GOLD, width=width)


def _draw_book(image):
    draw = ImageDraw.Draw(image)
    x0, y0, x1, y1 = BOOK_BOX
    draw.rounded_rectangle(BOOK_BOX, radius=18, fill=BOOK, outline=BOOK_BORDER, width=3)
    draw.rounded_rectangle((x0 + 14, y0 + 14, x1 - 14, y1 - 14), radius=12, outline=BOOK_INNER, width=1)

    # Lomo central
    mid = WIDTH // 2
    draw.rectangle((mid - 16, y0 + 3, mid + 16, y1 - 3), fill=SPINE)
    for offset in (-16, 16):
        draw.line([(mid + offset, y0 + 3), (mid + offset, y1 - 3)], fill="#2a1f38", width=2)

    # Esquineros
    for x, y, dx, dy in ((x0 + 6, y0 + 6, 1, 1), (x1 - 6, y0 + 6, -1, 1),
                         (x0 + 6, y1 - 6, 1, -1), (x1 - 6, y1 - 6, -1, -1)):
        _draw_corner(draw, x, y, dx, dy)


def _draw_diamond(draw, cx, cy, size, fill, outline=None):
    draw.polygon([(cx, cy - size), (cx + size, cy), (cx, cy + size), (cx - size, cy)], fill=fill, outline=outline)


def _draw_banner(draw, page, text):
    """Título de la página: texto dorado con líneas y rombos a los lados."""
    x0, x1 = page
    cx = (x0 + x1) // 2
    text_w = draw.textlength(text, font=TITLE_FONT)
    draw.text((cx, BANNER_Y), text, font=TITLE_FONT, fill=GOLD, anchor="mm")
    for side in (-1, 1):
        start = cx + side * (text_w / 2 + 18)
        end = x0 + 10 if side < 0 else x1 - 10
        draw.line([(start, BANNER_Y), (end, BANNER_Y)], fill=GOLD_DARK, width=2)
        _draw_diamond(draw, start, BANNER_Y, 5, GOLD)
        _draw_diamond(draw, end, BANNER_Y, 4, GOLD_DARK)


# --- CARTA ---
def _draw_coin(draw, cx, cy, r=7):
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=GOLD, outline=GOLD_DARK, width=2)


def _draw_clock(draw, cx, cy, r=6):
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=MUTED, width=2)
    draw.line([(cx, cy), (cx, cy - r + 2)], fill=MUTED, width=2)
    draw.line([(cx, cy), (cx + r - 2, cy)], fill=MUTED, width=2)


def _draw_card(image, x, y, code, auction, outfit):
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((x, y, x + CARD_W, y + CARD_H), radius=10, fill=CARD, outline=CARD_BORDER, width=2)
    _draw_diamond(draw, x + CARD_W // 2, y, 7, CARD_ART, outline="#8a6fc0")

    # Outfit
    art = (x + 12, y + 16, x + CARD_W - 12, y + 150)
    draw.rounded_rectangle(art, radius=6, fill=CARD_ART)
    if outfit is not None:
        sprite = outfit.resize((outfit.width * OUTFIT_SCALE, outfit.height * OUTFIT_SCALE), Image.NEAREST)
        sx = (art[0] + art[2]) // 2 - sprite.width // 2
        sy = (art[1] + art[3]) // 2 - sprite.height // 2
        image.alpha_composite(sprite, (sx, sy))

    # Código y nombre
    cx = x + CARD_W // 2
    draw.text((cx, y + 166), f"[{code:02d}]", font=CODE_FONT, fill=GOLD, anchor="mm")
    draw.text((cx, y + 188), _fit(draw, auction.name.upper(), NAME_FONT, CARD_W - 20),
              font=NAME_FONT, fill=TEXT, anchor="mm")
    draw.line([(x + 14, y + 203), (x + CARD_W - 14, y + 203)], fill=CARD_BORDER, width=1)

    # Datos
    rows = (("LVL", str(auction.level)), ("VOC", enum_text(auction.vocation)), ("WORLD", auction.world))
    for i, (label, value) in enumerate(rows):
        ry = y + 218 + i * 18
        draw.text((x + 14, ry), label, font=LABEL_FONT, fill=MUTED, anchor="lm")
        draw.text((x + CARD_W - 14, ry), _fit(draw, value, VALUE_FONT, CARD_W - 70),
                  font=VALUE_FONT, fill=TEXT, anchor="rm")

    # Precio y tiempo restante
    bid_label = "OFERTA ACTUAL" if "current" in str(enum_text(auction.bid_type)).lower() else "OFERTA MÍNIMA"
    draw.text((x + 14, y + 276), bid_label, font=SMALL_FONT, fill=MUTED, anchor="lm")
    _draw_coin(draw, x + 22, y + 296)
    draw.text((x + 35, y + 296), format_tc(auction.bid), font=PRICE_FONT, fill=GOLD, anchor="lm")
    _draw_clock(draw, x + 22, y + 316)
    draw.text((x + 35, y + 316), format_time_left(auction.auction_end), font=LABEL_FONT, fill=MUTED, anchor="lm")


# --- CATÁLOGO ---
def render_catalog(entries, outfits, page_text):
    """
    entries: [(código, subasta)] (hasta 12). outfits: {url: Image RGBA}.
    page_text: texto del título de la página derecha (ej. 'PÁGINA 1 DE 20').
    Devuelve el PNG en bytes.
    """
    image = Image.new("RGBA", (WIDTH, HEIGHT), BACKGROUND)
    _draw_watermark(image, WATERMARK)
    _draw_book(image)
    # Sobre las páginas, aún más tenue; las cartas la tapan
    book_mask = Image.new("L", (WIDTH, HEIGHT), 0)
    ImageDraw.Draw(book_mask).rounded_rectangle(
        (BOOK_BOX[0] + 16, BOOK_BOX[1] + 16, BOOK_BOX[2] - 16, BOOK_BOX[3] - 16), radius=12, fill=255)
    pages = image.copy()
    _draw_watermark(pages, WATERMARK_ON_BOOK)
    image.paste(pages, (0, 0), book_mask)
    draw = ImageDraw.Draw(image)
    _draw_banner(draw, PAGE_LEFT, "BAZAR DE PERSONAJES")
    _draw_banner(draw, PAGE_RIGHT, page_text)

    per_page = COLUMNS * len(ROWS_Y)
    for index, (code, auction) in enumerate(entries):
        page = PAGE_LEFT if index < per_page else PAGE_RIGHT
        slot = index % per_page
        row, col = divmod(slot, COLUMNS)
        grid_w = COLUMNS * CARD_W + (COLUMNS - 1) * CARD_GAP_X
        x = page[0] + ((page[1] - page[0]) - grid_w) // 2 + col * (CARD_W + CARD_GAP_X)
        _draw_card(image, x, ROWS_Y[row], code, auction, outfits.get(auction.outfit.image_url))

    # Indicación al pie
    footer_y = HEIGHT - 62
    parts = [("Pulsa ", FOOTER_FONT, MUTED), ("VER CÓDIGO", FOOTER_BOLD, GOLD), (" o escribe ", FOOTER_FONT, MUTED),
             ("!ver <código>", FOOTER_BOLD, GOLD), (" para ver la ficha completa", FOOTER_FONT, MUTED)]
    total = sum(draw.textlength(t, font=f) for t, f, _ in parts)
    fx = PAGE_RIGHT[1] - total
    for text, font, color in parts:
        draw.text((fx, footer_y), text, font=font, fill=color, anchor="lm")
        fx += draw.textlength(text, font=font)

    out = io.BytesIO()
    image.convert("RGB").save(out, format="PNG", optimize=True)
    return out.getvalue()
