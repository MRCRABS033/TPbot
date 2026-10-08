import aiohttp
from discord.ext import commands

from config import WATCHED_PLAYERS,logger
from services.tibia_api import fetch_player_data
from services.storage import save_data

class AddCommand(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="add", help="Agrega un personaje a la lista de monitoreo.")
    async def add_player(self, ctx, owner: str, *, character_name: str):
        char_key = character_name.strip().lower()
        logger.info(f"📩 Comando !add ejecutado por {ctx.author} para: '{character_name}' (Dueño: {owner})")

        async with aiohttp.ClientSession() as session:
            res = await fetch_player_data(session, character_name)

        if not res or not res.get("official_name"):
            await ctx.send(f"⚠️ No se encontró al personaje **{character_name}** en Tibia.com.")
            return

        # Extraer nivel actual y vocación desde la API de Tibia
        char_info = res.get("character_info", {})
        level = char_info.get("level", 0)
        vocation = char_info.get("vocation", "N/A")

        # Si el personaje ya existía, conservamos sus niveles históricos
        existing_data = WATCHED_PLAYERS.get(char_key, {})
        existing_initial_level = existing_data.get("initial_level", level)
        existing_weekly_start = existing_data.get("weekly_start_level", level)

        WATCHED_PLAYERS[char_key] = {
            "char_name": res["official_name"],
            "tibia_link": res["tibia_link"],
            "owner": owner.strip(),
            "initial_level": existing_initial_level,
            "weekly_start_level": existing_weekly_start,
            "current_level": level,
            "vocation": vocation,
        }

        # Guardar cambios en watched_players.json
        save_data()

        logger.info(f"✅ Personaje '{res['official_name']}' registrado (Nivel de Ingreso: {existing_initial_level}, Inicio Semana: {existing_weekly_start}).")
        await ctx.send(
            f"✅ Personaje {res['tibia_link']} vinculado a **{owner.strip()}**.\n"
            f"📊 **Nivel de ingreso al team:** {existing_initial_level} ({vocation})"
        )

async def setup(bot):
    await bot.add_cog(AddCommand(bot))