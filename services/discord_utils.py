import discord
from channels import find_channel
from config import logger
from services.vocation_thumbnails import ATTACHMENT_PREFIX, thumbnail_files

# Cuántos mensajes recientes se revisan al buscar la lista que el bot publicó antes de reiniciarse
HISTORY_LOOKUP_LIMIT = 30


def _embed_marker(embed: discord.Embed):
    """Texto que identifica a una lista (el autor o, si no tiene, el título del primer embed)."""
    marker = (embed.author.name if embed.author else None) or embed.title
    # Sin el relleno de espacios invisibles que se usa para dar ancho a los recuadros
    return marker.rstrip("⠀ ") if marker else marker


def _reuse_uploaded_thumbnails(msg, embeds):
    uploaded = {}
    for old in msg.embeds:
        url = old.thumbnail.url if old.thumbnail else None
        if url:
            uploaded[url.split("?")[0].rsplit("/", 1)[-1]] = url

    result = []
    for embed in embeds:
        url = embed.thumbnail.url if embed.thumbnail else None
        if url and url.startswith(ATTACHMENT_PREFIX):
            name = url[len(ATTACHMENT_PREFIX):]
            if name not in uploaded:
                return None
            embed = embed.copy()
            embed.set_thumbnail(url=uploaded[name])
        result.append(embed)
    return result


async def _get_previous_message(bot, channel, message_cache, guild_id, marker):
    """
    Devuelve el mensaje de la lista publicado antes: el registrado en message_cache o,
    si el bot se reinició y no lo recuerda, el último mensaje del bot con la misma lista.
    """
    msg_id = message_cache.get(guild_id)
    if msg_id:
        try:
            return await channel.fetch_message(msg_id)
        except discord.NotFound:
            message_cache.pop(guild_id, None)

    try:
        async for msg in channel.history(limit=HISTORY_LOOKUP_LIMIT):
            if msg.author == bot.user and msg.embeds and _embed_marker(msg.embeds[0]) == marker:
                message_cache[guild_id] = msg.id
                return msg
    except discord.HTTPException as e:
        logger.error(f"Error al revisar el historial de #{channel.name}: {e}")
    return None


async def send_or_update_embed(bot: discord.Client, message_cache: dict, channel_name: str, embed, view=None,
                               keep_last=False):
    """
    Busca el canal especificado en cada servidor, verifica si hay un mensaje
    previo registrado en message_cache y lo edita; de lo contrario envía uno nuevo.
    `embed` puede ser un Embed o una lista de Embeds (máx. 10).
    `view` (opcional) agrega botones debajo del mensaje.
    Se publica en silencio (sin notificación): es una lista que se actualiza sola.
    `keep_last=True`: si alguien escribió después, el mensaje se vuelve a publicar al final del canal.
    """
    embeds = embed if isinstance(embed, list) else [embed]
    marker = _embed_marker(embeds[0])
    for guild in bot.guilds:
        channel = find_channel(guild.text_channels, channel_name)
        if not channel:
            continue

        msg = await _get_previous_message(bot, channel, message_cache, guild.id, marker)
        if msg and keep_last and channel.last_message_id != msg.id:
            # Ya no es el último mensaje: se borra para publicarlo de nuevo al final
            try:
                await msg.delete()
            except discord.HTTPException:
                pass
            msg = None
        if msg:
            try:
                reused = _reuse_uploaded_thumbnails(msg, embeds)
                if reused is not None:
                    # Las imágenes ya están subidas en el mensaje: no se vuelven a subir
                    await msg.edit(embeds=reused, view=view)
                else:
                    await msg.edit(embeds=embeds, view=view, attachments=thumbnail_files(embeds))
                continue
            except discord.NotFound:
                logger.warning(f"Mensaje previo en #{channel_name} ({guild.name}) no existe. Se enviará uno nuevo.")
            except Exception as e:
                logger.error(f"Error al actualizar Embed en #{channel_name}: {e}")

        try:
            new_msg = await channel.send(embeds=embeds, view=view, files=thumbnail_files(embeds), silent=True)
            message_cache[guild.id] = new_msg.id
            logger.info(f"📢 Nuevo Embed enviado a #{channel_name} en {guild.name}")
        except Exception as e:
            logger.error(f"Error enviando mensaje a #{channel_name}: {e}")


async def broadcast_embed(bot: discord.Client, channel_name: str, embed: discord.Embed, silent=False):
    """Envía un Embed al canal especificado en cada servidor. `silent=True`: sin notificación."""
    for guild in bot.guilds:
        channel = find_channel(guild.text_channels, channel_name)
        if not channel:
            continue
        try:
            await channel.send(embed=embed, silent=silent)
        except Exception as e:
            logger.error(f"Error enviando mensaje a #{channel_name}: {e}")


async def repost_embed(bot: discord.Client, message_cache: dict, channel_name: str, embed, view=None):
    """
    Borra el mensaje previo registrado en message_cache y envía el Embed de nuevo,
    para que quede al final del canal (por ejemplo, después de un aviso).
    `embed` puede ser un Embed o una lista de Embeds (máx. 10).
    `view` (opcional) agrega botones debajo del mensaje.
    """
    embeds = embed if isinstance(embed, list) else [embed]
    marker = _embed_marker(embeds[0])
    for guild in bot.guilds:
        channel = find_channel(guild.text_channels, channel_name)
        if not channel:
            continue

        msg = await _get_previous_message(bot, channel, message_cache, guild.id, marker)
        if msg:
            try:
                await msg.delete()
            except discord.NotFound:
                pass
            except Exception as e:
                logger.error(f"Error al borrar el Embed previo en #{channel_name}: {e}")

        try:
            new_msg = await channel.send(embeds=embeds, view=view, files=thumbnail_files(embeds), silent=True)
            message_cache[guild.id] = new_msg.id
        except Exception as e:
            logger.error(f"Error enviando mensaje a #{channel_name}: {e}")


async def move_list_to_bottom(ctx, bot: discord.Client, message_cache: dict, channel_name: str, embed):
    """
    Para comandos usados en el propio canal de la lista (ej. !online en #onlines):
    borra el comando y vuelve a publicar la lista actualizada al final del canal,
    sin mensajes de confirmación.
    """
    if ctx.interaction:
        # Comando de barra: no hay mensaje que borrar; se avisa solo a quien lo pidió
        await ctx.send("La lista actualizada quedó al final del canal.", ephemeral=True)
    else:
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass
    await repost_embed(bot, message_cache, channel_name, embed)
