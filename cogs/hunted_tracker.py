import asyncio
import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

from channels import CHANNEL_HUNTEDS
from cogs.death_tracker import DeathTracker
from cogs.panel import HuntedPanelView
from config import HUNTED_PLAYERS, WORLD, logger
from services.task_utils import resilient
from services.discord_utils import broadcast_embed, repost_embed, send_or_update_embed
from services.online_embeds import build_grouped_online_embeds, player_url
from services.roster import add_hunted, build_hunted_list_embed, remove_hunted
from services.storage import save_hunteds
from services.tibia_api import fetch_player_data, fetch_world_online_players
from services.tibia_wiki import vocation_image_url

# Consultas simultáneas a la API al revisar muertes de hunteds
MAX_CONCURRENT_LOOKUPS = 5


class HuntedTracker(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.list_message = {}
        self.previous_online = None
        self.track_hunteds.start()

    def cog_unload(self):
        self.track_hunteds.cancel()

    # --- COMANDOS ---
    @commands.hybrid_group(name="hunted", fallback="list", invoke_without_command=True,
                           description="Muestra la lista de hunteds")
    async def hunted(self, ctx):
        """!hunted -> muestra todos los hunteds, separados en conectados y desconectados."""
        logger.info(f"📩 Comando !hunted ejecutado por {ctx.author}")
        await ctx.send(embed=build_hunted_list_embed(self.previous_online))

    @hunted.command(name="add", description="Agrega un personaje a la lista de hunteds")
    @app_commands.describe(character_name="Nombre del personaje en Tibia")
    async def hunted_add(self, ctx, *, character_name: str):
        await ctx.defer()
        logger.info(f"📩 Comando !hunted add ejecutado por {ctx.author} para: '{character_name}'")
        await ctx.send(embed=await add_hunted(character_name, str(ctx.author)))

    @hunted.command(name="remove", description="Elimina un personaje de la lista de hunteds")
    @app_commands.describe(character_name="Nombre del personaje")
    async def hunted_remove(self, ctx, *, character_name: str):
        logger.info(f"📩 Comando !hunted remove ejecutado por {ctx.author} para: '{character_name}'")
        await ctx.send(embed=remove_hunted(character_name))

    # --- TAREA AUTOMÁTICA ---
    @resilient
    @tasks.loop(seconds=60)
    async def track_hunteds(self):
        if not HUNTED_PLAYERS:
            # Sin hunteds se publica igual la lista vacía, para que estén los botones de agregar
            await send_or_update_embed(
                self.bot, self.list_message, CHANNEL_HUNTEDS, self._build_list_embeds([]), view=HuntedPanelView(), keep_last=True
            )
            return
        logger.info(f"🎯 Verificando hunteds en {WORLD}...")

        semaphore = asyncio.Semaphore(MAX_CONCURRENT_LOOKUPS)

        async def lookup(name):
            async with semaphore:
                return await fetch_player_data(session, name)

        entries = list(HUNTED_PLAYERS.items())
        async with aiohttp.ClientSession() as session:
            online_world = await fetch_world_online_players(session, WORLD)
            # Se consulta a cada hunted para detectar muertes (aunque esté desconectado)
            details = await asyncio.gather(*(lookup(d["char_name"]) for _, d in entries))

        online_lookup = {p.get("name", "").lower(): p for p in online_world}
        alerts = []
        online_list = []
        online_keys = set()
        changed = False

        for (key, data), res in zip(entries, details):
            if key not in HUNTED_PLAYERS:
                continue  # Lo eliminaron mientras se revisaba

            # --- MUERTES ---
            deaths = (res or {}).get("deaths", [])
            if res and deaths and deaths[0].get("time") != data.get("last_death_time"):
                data["last_death_time"] = deaths[0]["time"]
                changed = True
                alerts.append(self._build_death_embed(data, deaths[0], res.get("character_info", {})))

            player = online_lookup.get(key)
            if not player:
                continue

            # --- CONEXIÓN ---
            lvl = player.get("level", 0)
            voc = player.get("vocation", data.get("vocation", "N/A"))
            online_keys.add(key)
            if self.previous_online is not None and key not in self.previous_online:
                alerts.append(self._build_login_embed(data, lvl, voc))

            # --- NIVEL ---
            old_level = data.get("level")
            if old_level and lvl != old_level:
                alerts.append(self._build_level_embed(data, old_level, lvl, voc))
            if lvl != old_level or voc != data.get("vocation"):
                data["level"], data["vocation"] = lvl, voc
                changed = True

            online_list.append((
                lvl, voc,
                f"**[{data['char_name']}]({player_url(data['char_name'])})**\n  Nivel {lvl} · {voc}",
            ))

        if changed:
            save_hunteds()
        # Si la API del mundo falló (lista vacía), no se actualiza el estado de conexión
        if online_world:
            self.previous_online = online_keys

        list_embeds = self._build_list_embeds(online_list)

        if alerts:
            for alert in alerts:
                await broadcast_embed(self.bot, CHANNEL_HUNTEDS, alert)
            # La lista se vuelve a publicar para que quede debajo de los avisos
            await repost_embed(self.bot, self.list_message, CHANNEL_HUNTEDS, list_embeds, view=HuntedPanelView())
        else:
            await send_or_update_embed(
                self.bot, self.list_message, CHANNEL_HUNTEDS, list_embeds, view=HuntedPanelView(), keep_last=True
            )

    @staticmethod
    def _build_list_embeds(online_list):
        """Lista de hunteds online, con los botones del panel debajo (agregar, buscar, listar, eliminar)."""
        return build_grouped_online_embeds(
            online_list,
            title="Hunteds online",
            footer=f"Mundo: {WORLD} · Conectados: {len(online_list)} de {len(HUNTED_PLAYERS)}",
            color=discord.Color.dark_red(),
            empty_text=(
                "Ningún hunted está conectado."
                if HUNTED_PLAYERS
                else "La lista está vacía. Usa ＋ Agregar para añadir un hunted."
            ),
        )

    @track_hunteds.before_loop
    async def before_track(self):
        await self.bot.wait_until_ready()

    # --- AVISOS ---
    @staticmethod
    def _build_login_embed(data, level, vocation):
        logger.info(f"🎯 Hunted conectado: {data['char_name']}")
        embed = discord.Embed(
            title=f"{data['char_name']} se conectó",
            url=player_url(data["char_name"]),
            description=f"Hunted · Nivel {level} · {vocation}",
            color=discord.Color.dark_red(),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_thumbnail(url=vocation_image_url(vocation))
        embed.set_footer(text=f"Mundo: {WORLD}")
        return embed

    @staticmethod
    def _build_level_embed(data, old_level, new_level, vocation):
        logger.info(f"🎯 Hunted {data['char_name']}: nivel {old_level} -> {new_level}")
        up = new_level > old_level
        embed = discord.Embed(
            title=f"Hunted {data['char_name']} {'subió' if up else 'bajó'} a nivel {new_level}",
            url=player_url(data["char_name"]),
            color=discord.Color.dark_orange(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Vocación", value=vocation, inline=True)
        embed.add_field(name="Nivel", value=f"{old_level} → **{new_level}**", inline=True)
        embed.set_thumbnail(url=vocation_image_url(vocation))
        embed.set_footer(text=f"Mundo: {WORLD}")
        return embed

    @staticmethod
    def _build_death_embed(data, death, char_info):
        logger.info(f"🎯 Hunted muerto: {data['char_name']}")
        killers = death.get("killers", [])
        embed = discord.Embed(
            title=f"Hunted {data['char_name']} murió a nivel {death.get('level', '?')}",
            url=player_url(data["char_name"]),
            color=discord.Color.dark_green(),
        )
        embed.add_field(name="Fecha", value=DeathTracker._format_death_time(death.get("time")), inline=False)
        if killers:
            embed.add_field(name="Asesinos", value=DeathTracker._format_killers(killers, {}), inline=False)
        else:
            embed.add_field(name="Causa", value=death.get("reason", "Desconocida"), inline=False)
        if death.get("assists"):
            embed.add_field(name="Asistencias", value=DeathTracker._format_killers(death["assists"], {}), inline=False)
        embed.set_thumbnail(url=vocation_image_url(char_info.get("vocation", data.get("vocation", ""))))
        embed.set_footer(text=f"Mundo: {char_info.get('world', data.get('world', 'Desconocido'))}")
        return embed


async def setup(bot):
    await bot.add_cog(HuntedTracker(bot))
