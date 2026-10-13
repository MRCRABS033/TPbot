import asyncio
import time
import aiohttp
import discord
from discord.ext import commands, tasks

from channels import CHANNEL_FREES, find_channel
from config import logger
from services.discord_utils import broadcast_embed, move_list_to_bottom, repost_embed, send_or_update_embed
from services.tibia_api import fetch_player_data, fetch_world_online_players
from services.tibia_wiki import vocation_image_url

# Cuánto tiempo se recuerda si una cuenta es free (evita consultar a todos los jugadores cada minuto)
ACCOUNT_STATUS_TTL = 60 * 60  # 1 hora
# Si se conectan más frees que esto a la vez, se avisa en un solo mensaje en vez de uno por jugador
MAX_INDIVIDUAL_ALERTS = 5
# Máximo de jugadores que se muestran en la lista
MAX_LIST_ENTRIES = 25


class FreeTracker(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.free_embed_message = {}
        self.account_cache = {}  # { nombre: (es_free, hora_de_consulta) }
        # Frees online en la revisión anterior. None = aún no hay revisión previa
        # (así no se avisa de todos los conectados cada vez que el bot arranca).
        self.previous_free = None
        self.last_list_embed = None  # Última lista generada (la usa !free)
        self.update_free_players.start()

    def cog_unload(self):
        self.update_free_players.cancel()

    async def _is_free(self, session, semaphore, name):
        """True/False si es free, o None si no se pudo consultar."""
        cached = self.account_cache.get(name)
        if cached and time.monotonic() - cached[1] < ACCOUNT_STATUS_TTL:
            return cached[0]

        async with semaphore:
            res = await fetch_player_data(session, name)
        if not res or not res.get("character_info"):
            return None  # No se cachea: se reintenta en la siguiente revisión

        is_free = "free" in res["character_info"].get("account_status", "").lower()
        self.account_cache[name] = (is_free, time.monotonic())
        return is_free

    @tasks.loop(seconds=60)
    async def update_free_players(self):
        world = "Quidera"
        logger.info(f"🔄 Consultando Free Accounts online en {world}...")
        semaphore = asyncio.Semaphore(8)

        async with aiohttp.ClientSession() as session:
            online_players = await fetch_world_online_players(session, world)
            statuses = await asyncio.gather(
                *(self._is_free(session, semaphore, p.get("name")) for p in online_players)
            )

        free_players = [p for p, is_free in zip(online_players, statuses) if is_free]
        free_names = {p["name"] for p in free_players}
        unknown_names = {p["name"] for p, is_free in zip(online_players, statuses) if is_free is None}

        # Limpia del caché a los que ya no están online
        online_names = {p.get("name") for p in online_players}
        for name in list(self.account_cache):
            if name not in online_names:
                del self.account_cache[name]

        # --- DETECCIÓN DE CONEXIONES ---
        new_frees = []
        if online_players:  # Si la API falló, no se actualiza el estado
            if self.previous_free is not None:
                new_frees = [p for p in free_players if p["name"] not in self.previous_free]
            # Los que no se pudieron consultar esta vez se mantienen como estaban,
            # para no avisar de ellos como "recién conectados" en la siguiente revisión
            self.previous_free = free_names | (unknown_names & (self.previous_free or set()))

        list_embed = self._build_list_embed(free_players, world)
        self.last_list_embed = list_embed

        if new_frees:
            for alert in self._build_login_embeds(new_frees, world):
                # En silencio: con tantos frees, una notificación por conexión sería demasiado
                await broadcast_embed(self.bot, CHANNEL_FREES, alert, silent=True)
            # La lista se vuelve a publicar para que quede debajo de los avisos
            await repost_embed(self.bot, self.free_embed_message, CHANNEL_FREES, list_embed)
        else:
            await send_or_update_embed(self.bot, self.free_embed_message, CHANNEL_FREES, list_embed)

    @staticmethod
    def _player_url(name):
        return f"https://www.tibia.com/community/?subtopic=characters&name={name.replace(' ', '+')}"

    def _build_login_embeds(self, new_frees, world):
        for p in new_frees:
            logger.info(f"🆓 Free conectado: {p['name']}")

        if len(new_frees) > MAX_INDIVIDUAL_ALERTS:
            # Muchos a la vez: un solo aviso con la lista
            lines = [
                f"**[{p['name']}]({self._player_url(p['name'])})** · Nivel {p.get('level', 0)} · {p.get('vocation', '')}"
                for p in sorted(new_frees, key=lambda p: p.get("level", 0), reverse=True)
            ]
            embed = discord.Embed(
                title=f"{len(new_frees)} jugadores free se conectaron",
                description="\n".join(lines)[:4000],
                color=discord.Color.dark_gold(),
                timestamp=discord.utils.utcnow(),
            )
            embed.set_footer(text=f"Mundo: {world}")
            return [embed]

        embeds = []
        for p in new_frees:
            name = p["name"]
            embed = discord.Embed(
                title=f"{name} se conectó",
                url=self._player_url(name),
                description=f"Free Account · Nivel {p.get('level', 0)} · {p.get('vocation', '')}",
                color=discord.Color.dark_gold(),
                timestamp=discord.utils.utcnow(),
            )
            embed.set_thumbnail(url=vocation_image_url(p.get("vocation", "")))
            embed.set_footer(text=f"Mundo: {world}")
            embeds.append(embed)
        return embeds

    def _build_list_embed(self, free_players, world):
        players = sorted(free_players, key=lambda p: p.get("level", 0), reverse=True)
        entries = [
            f"**[{p['name']}]({self._player_url(p['name'])})**\n  Nivel {p.get('level', 0)} · {p.get('vocation', '')}"
            for p in players[:MAX_LIST_ENTRIES]
        ]
        if len(players) > MAX_LIST_ENTRIES:
            entries.append(f"… y {len(players) - MAX_LIST_ENTRIES} más")

        embed = discord.Embed(
            title="Jugadores free online",
            description="\n\n".join(entries) if entries else "No hay jugadores free conectados.",
            color=discord.Color.dark_gold() if players else discord.Color.dark_grey(),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text=f"Mundo: {world} · Conectados: {len(players)}")
        return embed

    @commands.hybrid_command(name="free", description="Muestra la lista de jugadores free online")
    async def free_command(self, ctx):
        """Muestra la lista de frees donde se pide. En #frees, la mueve al final del canal."""
        in_frees = find_channel([ctx.channel], CHANNEL_FREES) is not None
        await ctx.defer(ephemeral=in_frees)
        await self.update_free_players()
        if in_frees:
            await move_list_to_bottom(ctx, self.bot, self.free_embed_message, CHANNEL_FREES, self.last_list_embed)
        else:
            await ctx.send(embed=self.last_list_embed)

    @update_free_players.before_loop
    async def before_update(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(FreeTracker(bot))
