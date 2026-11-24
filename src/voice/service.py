"""Lógica del temporizador de voz que no depende de Discord: intervalos y sonido del pitido."""
import math
import re
import struct

MIN_INTERVAL = 5          # segundos
MAX_INTERVAL = 6 * 3600   # 6 horas

_DURATION_RE = re.compile(r"(\d+)\s*([hms]?)", re.IGNORECASE)
_UNIT_SECONDS = {"h": 3600, "m": 60, "s": 1, "": 60}  # número solo = minutos

BEEP_VOLUME = 0.95  # 0.0 a 1.0
BEEP_DRIVE = 3.0    # cuanto más alto, onda más "cuadrada" y se percibe más fuerte


def parse_duration(text):
    """'90s', '5m', '1h30m', '1m 30s' o '5' (minutos) -> segundos. None si no es válido."""
    text = text.strip().lower().replace("min", "m").replace("seg", "s")
    if not text or not re.fullmatch(r"(\s*\d+\s*[hms]?)+\s*", text):
        return None
    return sum(int(n) * _UNIT_SECONDS[u] for n, u in _DURATION_RE.findall(text))


def format_duration(seconds):
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    parts = [f"{h}h" if h else "", f"{m}m" if m else "", f"{s}s" if s else ""]
    return " ".join(p for p in parts if p) or "0s"


def build_beep():
    """PCM crudo (48 kHz, 16 bits, estéreo) con tres pitidos. No necesita FFmpeg."""
    rate = 48000
    frames = bytearray()
    norm = math.tanh(BEEP_DRIVE)

    def tone(freq, ms):
        n = int(rate * ms / 1000)
        fade = int(rate * 0.005)  # 5 ms de fundido para que no haga "clic"
        for i in range(n):
            env = min(1.0, i / fade, (n - i) / fade)
            wave = math.tanh(BEEP_DRIVE * math.sin(2 * math.pi * freq * i / rate)) / norm
            v = int(32767 * BEEP_VOLUME * env * wave)
            frames.extend(struct.pack("<hh", v, v))

    def silence(ms):
        frames.extend(b"\x00" * (int(rate * ms / 1000) * 4))

    for _ in range(3):
        tone(1000, 250)
        silence(120)
    return bytes(frames)
