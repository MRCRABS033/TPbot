from discord.ext import commands
from config import WATCHED_PLAYERS, PLAYERS_STATE,logger
from services.storage import save_data
from services.tibia_api import fetch_player_data 

class RemoveCommand(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="remove", help="Remove a player from the watchlist.")
    async def remove_player(self, ctx, *, character_name: str):
        char_key = character_name.strip().lower()
        logger.info(f"📩 Comando !remove ejecutado por {ctx.author} para: '{character_name}'")

        if char_key in WATCHED_PLAYERS:
            removed_data = WATCHED_PLAYERS.pop(char_key)
            if char_key in PLAYERS_STATE:
                del PLAYERS_STATE[char_key]

            # Guardar en archivo JSON
            save_data()

            logger.info(f"🗑️ Personaje '{removed_data['char_name']}' eliminado de la lista.")
            await ctx.send(
                f"🗑️ Personaje {removed_data['tibia_link']} (Dueño: **{removed_data['owner']}**) eliminado de la lista."
            )
        else:
            await ctx.send(f"⚠️ El personaje **{character_name}** no está registrado.")

async def setup(bot):
    await bot.add_cog(RemoveCommand(bot))