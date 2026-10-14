"""
Respaldo manual de los datos del bot (personajes del grupo y hunteds), sin tocar el hosting.

- !respaldo   envía los JSON como archivos adjuntos (guárdalos antes de cada despliegue).
- !restaurar  carga esos archivos (adjúntalos al mensaje). Antes guarda una copia .bak de lo actual.
Solo administradores, desarrolladores y programadores.
"""
import io
import json
import os
import shutil

import discord
from discord import app_commands
from discord.ext import commands

from config import HUNTED_PLAYERS, WATCHED_PLAYERS, logger
from constants.roles import NO_PERMISSION_MESSAGE
from services.permissions import is_bot_admin
from services.storage import DATA_FILE, HUNTED_FILE, save_data, save_hunteds

MAX_BACKUP_SIZE = 5 * 1024 * 1024  # 5 MB

# tipo -> (nombre del archivo, datos en memoria, función que los guarda, descripción)
DATASETS = {
    "group": (DATA_FILE, WATCHED_PLAYERS, save_data, "personajes del grupo"),
    "hunteds": (HUNTED_FILE, HUNTED_PLAYERS, save_hunteds, "hunteds"),
}


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
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name="respaldo", description="Envía una copia de los datos del bot (solo administración)")
    async def backup_command(self, ctx):
        if not is_bot_admin(ctx):
            await ctx.send(NO_PERMISSION_MESSAGE, ephemeral=True)
            return
        logger.info(f"📩 Comando !respaldo ejecutado por {ctx.author}")

        files, lines = [], []
        for filename, data, _, label in DATASETS.values():
            content = json.dumps(data, indent=4, ensure_ascii=False).encode("utf-8")
            name = os.path.basename(filename)
            files.append(discord.File(io.BytesIO(content), filename=name))
            lines.append(f"• **{name}**: {len(data)} {label}")

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

        results = []
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

            filename, target, save, label = DATASETS[kind]
            # Copia de lo que había antes, por si hay que deshacer la restauración
            if os.path.exists(filename):
                shutil.copyfile(filename, filename + ".bak")
            target.clear()
            target.update(data)
            save()
            results.append(f"✅ **{attachment.filename}**: {len(data)} {label} restaurados.")
            logger.info(f"♻️ {ctx.author} restauró {len(data)} {label} desde {attachment.filename}")

        embed = discord.Embed(title="Restauración de datos", description="\n".join(results),
                              color=discord.Color.dark_teal())
        embed.set_footer(text="Lo anterior quedó guardado como .bak en el servidor")
        await ctx.send(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Backup(bot))
