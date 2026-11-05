"""
Respaldo manual de los datos del bot (personajes del grupo y hunteds), sin tocar el hosting.

Cada comando trabaja solo con los datos del servidor donde se usa.

- !respaldo   envía los datos como archivos JSON adjuntos.
- !restaurar  carga esos archivos (adjúntalos al mensaje) y envía una copia de lo que había antes.
Solo administradores, desarrolladores y programadores.
"""
import io
import json

import discord
from discord import app_commands
from discord.ext import commands

from src.core.config import logger
from src.core.constants.roles import NO_PERMISSION_MESSAGE
from src.shared.permissions import is_bot_admin

MAX_BACKUP_SIZE = 5 * 1024 * 1024  # 5 MB

# tipo -> (nombre del archivo, descripción)
DATASETS = {
    "group": ("watched_players.json", "personajes del grupo"),
    "hunteds": ("hunteds.json", "hunteds"),
}


def json_file(data, filename):
    content = json.dumps(data, indent=4, ensure_ascii=False).encode("utf-8")
    return discord.File(io.BytesIO(content), filename=filename)


def detect_dataset(data):
    """Reconoce el tipo de respaldo por su contenido: 'group', 'hunteds' o None si no es válido."""
    if not isinstance(data, dict):
        return None
    entries = list(data.values())
    if not all(isinstance(e, dict) and e.get("char_name") for e in entries):
        return None
    if not entries:
        return None
    if all("owner" in e for e in entries):
        return "group"
    if all("owner" not in e for e in entries):
        return "hunteds"
    return None


class Backup(commands.Cog):
    def __init__(self, bot, players, hunteds):
        self.bot = bot
        self.repositories = {"group": players, "hunteds": hunteds}

    def _characters(self, kind, guild_id):
        return self.repositories[kind].for_guild(guild_id)

    @commands.hybrid_command(name="respaldo", description="Envía una copia de los datos del bot (solo administración)")
    async def backup_command(self, ctx):
        if not is_bot_admin(ctx):
            await ctx.send(NO_PERMISSION_MESSAGE, ephemeral=True)
            return
        logger.info(f"📩 Comando !respaldo ejecutado por {ctx.author}")

        files, lines = [], []
        for kind, (filename, label) in DATASETS.items():
            data = await self._characters(kind, ctx.guild.id).all()
            files.append(json_file(data, filename))
            lines.append(f"• **{filename}**: {len(data)} {label}")

        embed = discord.Embed(
            title="Respaldo de datos",
            description="\n".join(lines) + "\n\nGuarda estos archivos. Para cargarlos usa **!restaurar** adjuntándolos.",
            color=discord.Color.dark_teal(),
            timestamp=discord.utils.utcnow(),
        )
        await ctx.send(embed=embed, files=files, ephemeral=True)

    @commands.hybrid_command(name="restaurar", description="Carga un respaldo de datos del bot (solo administración)")
    @app_commands.describe(archivo="Archivo de respaldo (.json)", archivo2="Segundo archivo de respaldo (opcional)")
    async def restore_command(self, ctx, archivo: discord.Attachment = None, archivo2: discord.Attachment = None):
        if not is_bot_admin(ctx):
            await ctx.send(NO_PERMISSION_MESSAGE, ephemeral=True)
            return
        logger.info(f"📩 Comando !restaurar ejecutado por {ctx.author}")

        attachments = [a for a in (archivo, archivo2) if a]
        if not attachments and ctx.message:
            attachments = list(ctx.message.attachments)[:2]
        if not attachments:
            await ctx.send("⚠️ Adjunta los archivos del respaldo (.json) al mensaje de **!restaurar**.", ephemeral=True)
            return

        results, previous = [], []
        for attachment in attachments:
            if attachment.size > MAX_BACKUP_SIZE:
                results.append(f"⚠️ **{attachment.filename}**: es demasiado grande.")
                continue
            try:
                data = json.loads(await attachment.read())
            except (json.JSONDecodeError, UnicodeDecodeError):
                results.append(f"⚠️ **{attachment.filename}**: no es un JSON válido.")
                continue

            kind = detect_dataset(data)
            if not kind:
                results.append(f"⚠️ **{attachment.filename}**: no parece un respaldo del bot (grupo o hunteds).")
                continue

            filename, label = DATASETS[kind]
            characters = self._characters(kind, ctx.guild.id)
            # Copia de lo que había antes, por si hay que deshacer la restauración
            previous.append(json_file(await characters.all(), f"anterior_{filename}"))
            await characters.replace_all(data)
            results.append(f"✅ **{attachment.filename}**: {len(data)} {label} restaurados.")
            logger.info(f"♻️ {ctx.author} restauró {len(data)} {label} desde {attachment.filename}")

        embed = discord.Embed(title="Restauración de datos", description="\n".join(results),
                              color=discord.Color.dark_teal())
        if previous:
            embed.set_footer(text="Lo que había antes va adjunto: guárdalo por si hay que deshacer la restauración")
        await ctx.send(embed=embed, files=previous, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Backup(bot, bot.data.players, bot.data.hunteds))
