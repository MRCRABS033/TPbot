import discord
from config import logger

async def send_or_update_embed(bot: discord.Client, message_cache: dict, channel_name: str, embed: discord.Embed):
    """
    Busca el canal especificado en cada servidor, verifica si hay un mensaje 
    previo registrado en message_cache y lo edita; de lo contrario envía uno nuevo.
    """
    for guild in bot.guilds:
        channel = discord.utils.get(guild.text_channels, name=channel_name)
        if not channel:
            continue

        msg_id = message_cache.get(guild.id)
        msg_updated = False

        if msg_id:
            try:
                msg = await channel.fetch_message(msg_id)
                await msg.edit(embed=embed)
                msg_updated = True
            except discord.NotFound:
                logger.warning(f"Mensaje previo en #{channel_name} ({guild.name}) no existe. Se enviará uno nuevo.")
            except Exception as e:
                logger.error(f"Error al actualizar Embed en #{channel_name}: {e}")

        if not msg_updated:
            try:
                new_msg = await channel.send(embed=embed)
                message_cache[guild.id] = new_msg.id
                logger.info(f"📢 Nuevo Embed enviado a #{channel_name} en {guild.name}")
            except Exception as e:
                logger.error(f"Error enviando mensaje a #{channel_name}: {e}")