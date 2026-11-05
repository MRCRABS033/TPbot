"""Piezas de embeds que usan varios canales."""
import discord

ZERO_WIDTH_SPACE = "​"  # Título "invisible" para los campos de continuación


def error_embed(text):
    return discord.Embed(description=f"⚠️ {text}", color=discord.Color.dark_grey())


def chunk_fields(embed, title, entries, separator="\n\n"):
    """Agrega las entradas en uno o más campos (límite de Discord: 1024 caracteres por campo)."""
    chunk = []
    for entry in entries:
        if len(separator.join(chunk + [entry])) > 1020:
            embed.add_field(name=title, value=separator.join(chunk), inline=False)
            chunk, title = [], ZERO_WIDTH_SPACE
        chunk.append(entry)
    if chunk:
        embed.add_field(name=title, value=separator.join(chunk), inline=False)
