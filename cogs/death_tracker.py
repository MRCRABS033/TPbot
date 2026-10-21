import asyncio
import io
import re
from datetime import datetime, timedelta, timezone
import aiohttp
import discord
from discord.ext import commands, tasks

from channels import CHANNEL_DEATHS, find_channel
from config import PLAYERS_STATE, WATCHED_PLAYERS, logger
from services.task_utils import resilient
from services.tibia_api import fetch_player_data
from services.tibia_wiki import creature_file_name, fetch_wiki_image, vocation_file_name
from services.http import new_session

# Imagen del asesino (monstruo u outfit por vocación) en la esquina del aviso de muerte
SHOW_DEATH_THUMBNAIL = True
# Máximo de jugadores (asesinos + asistencias) a consultar en la API por cada muerte
MAX_PLAYER_LOOKUPS = 10
# Zona horaria para decidir qué es "hoy" en !muertes (Colombia, UTC-5)
LOCAL_TZ = timezone(timedelta(hours=-5))
# Consultas simultáneas a la API en !muertes
MAX_CONCURRENT_LOOKUPS = 5
# Máximo de avisos de muerte que muestra !muertes (los más recientes)
MAX_DEATHS_LISTED = 10


class DeathTracker(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.track_tibia_players.start()

    def cog_unload(self):
        self.track_tibia_players.cancel()

    @resilient

    @tasks.loop(seconds=60)
    async def track_tibia_players(self):
        logger.info("🔄 === Verificando niveles y muertes de personajes monitoreados ===")

        if not WATCHED_PLAYERS:
            return

        current_entries = list(WATCHED_PLAYERS.items())

        async with new_session() as session:
            for char_key, info in current_entries:
                char_name = info["char_name"]
                owner_name = info["owner"]

                res = await fetch_player_data(session, char_name)
                if not res or not res.get("character_info"):
                    continue

                char_info = res["character_info"]
                current_level = char_info.get("level", 0)
                deaths = res.get("deaths", [])
                tibia_link = res.get("tibia_link", char_name)

                # Registro Inicial
                if char_key not in PLAYERS_STATE:
                    PLAYERS_STATE[char_key] = {
                        "level": current_level,
                        "last_death_time": deaths[0]["time"] if deaths else None,
                    }
                    # Registro inicial silencioso: solo se guarda el punto de partida
                    continue

                last_state = PLAYERS_STATE[char_key]

                # Los avisos de nivel los publica watched_tracker en el canal de niveles
                PLAYERS_STATE[char_key]["level"] = current_level

                # Detección de Muerte
                if deaths and deaths[0]["time"] != last_state["last_death_time"]:
                    latest_death = deaths[0]
                    await self._notify_death(tibia_link, owner_name, current_level, char_info, latest_death)
                    PLAYERS_STATE[char_key]["last_death_time"] = latest_death["time"]

                await asyncio.sleep(0.5)

    # --- COMANDO: MUERTES DE HOY ---
    @commands.hybrid_command(name="muertes", description="Muestra las muertes de hoy de los personajes del grupo")
    async def deaths_today(self, ctx):
        logger.info(f"📩 Comando !muertes ejecutado por {ctx.author}")
        await ctx.defer()

        today = datetime.now(LOCAL_TZ).date()
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_LOOKUPS)
        players = list(WATCHED_PLAYERS.values())

        async with new_session() as session:
            async def lookup(name):
                async with semaphore:
                    return await fetch_player_data(session, name)

            results = await asyncio.gather(*(lookup(p["char_name"]) for p in players))

        deaths = []
        details = {}
        failed = 0
        for data, res in zip(players, results):
            if not res or not res.get("character_info"):
                failed += 1
                continue
            details[data["char_name"]] = res
            for death in res.get("deaths", []):
                when = self._parse_time(death.get("time"))
                if when and when.astimezone(LOCAL_TZ).date() == today:
                    deaths.append((when, data, death))
        deaths.sort(key=lambda d: d[0], reverse=True)

        footer = f"{len(players)} personajes revisados"
        if failed:
            footer += f" · {failed} sin respuesta de la API"

        if not deaths:
            embed = discord.Embed(
                title="Muertes de hoy", description="Nadie del grupo ha muerto hoy.", color=discord.Color.dark_grey()
            )
            embed.set_footer(text=footer)
            await ctx.send(embed=embed)
            return

        header = discord.Embed(
            title=f"Muertes de hoy · {len(deaths)}",
            description=(
                f"Se muestran las {MAX_DEATHS_LISTED} más recientes."
                if len(deaths) > MAX_DEATHS_LISTED
                else "De la más reciente a la más antigua."
            ),
            color=discord.Color.dark_red(),
        )
        header.set_footer(text=footer)
        await ctx.send(embed=header)

        # Cada muerte con el mismo aviso que se publica en #cementerio
        for _, data, death in deaths[:MAX_DEATHS_LISTED]:
            res = details[data["char_name"]]
            embed, thumbnail = await self._build_death_message(
                data["tibia_link"], data["owner"], data.get("current_level", 0), res["character_info"], death
            )
            await ctx.send(**self._message_kwargs(embed, thumbnail))

    @staticmethod
    def _parse_time(time_str):
        try:
            return datetime.fromisoformat(time_str.replace("Z", "+00:00"))
        except (AttributeError, ValueError):
            return None

    @track_tibia_players.before_loop
    async def before_track(self):
        await self.bot.wait_until_ready()

    # --- Notificaciones auxiliares ---
    @staticmethod
    def _message_kwargs(embed: discord.Embed, thumbnail: bytes = None):
        """Argumentos para enviar el embed con su imagen adjunta (un discord.File solo se puede enviar una vez)."""
        if not thumbnail:
            return {"embed": embed}
        embed.set_thumbnail(url="attachment://thumbnail.gif")
        return {"embed": embed, "file": discord.File(io.BytesIO(thumbnail), filename="thumbnail.gif")}

    async def _broadcast_embed(self, embed: discord.Embed, channel_name: str = CHANNEL_DEATHS, thumbnail: bytes = None):
        for guild in self.bot.guilds:
            channel = find_channel(guild.text_channels, channel_name)
            if channel:
                await channel.send(**self._message_kwargs(embed, thumbnail))

    async def _notify_death(self, tibia_link, owner, level, char_info, death_info):
        embed, thumbnail = await self._build_death_message(tibia_link, owner, level, char_info, death_info)
        await self._broadcast_embed(embed, CHANNEL_DEATHS, thumbnail)

    async def _build_death_message(self, tibia_link, owner, level, char_info, death_info):
        """Aviso de muerte (embed + imagen del asesino). Lo usan #cementerio y !muertes."""
        killers = death_info.get("killers", [])
        assists = death_info.get("assists", [])
        player_killers = [k for k in killers if k.get("player")]
        is_pvp = bool(player_killers)
        # Nivel al momento de morir (puede ser distinto al actual si se detectó tarde)
        death_level = death_info.get("level", level)

        # Título corto (es lo que se ve en la notificación); el nombre enlaza a Tibia.com
        char_name, char_url = self._parse_link(tibia_link, char_info)
        embed = discord.Embed(
            title=f"{char_name} murió{' en PvP' if is_pvp else ''} a nivel {death_level}",
            url=char_url,
            color=discord.Color.dark_red() if is_pvp else discord.Color.dark_grey(),
        )
        # Vocación y nivel de los jugadores que participaron (asesinos y asistencias)
        player_infos = await self._fetch_players_info(killers + assists)

        embed.add_field(name="Dueño", value=owner, inline=True)
        embed.add_field(name="Fecha", value=self._format_death_time(death_info.get("time")), inline=True)
        if killers:
            embed.add_field(name="Asesinos", value=self._format_killers(killers, player_infos), inline=False)
        else:
            embed.add_field(name="Causa", value=death_info.get("reason", "Desconocida"), inline=False)
        if assists:
            embed.add_field(name="Asistencias", value=self._format_killers(assists, player_infos), inline=False)
        embed.set_footer(text=f"Mundo: {char_info.get('world', 'Desconocido')}")

        thumbnail = None
        if SHOW_DEATH_THUMBNAIL:
            thumbnail = await self._get_killer_image(
                player_killers[0] if is_pvp else (killers[0] if killers else None), player_infos
            )
        return embed, thumbnail

    async def _fetch_players_info(self, participants):
        """Consulta en la API a los jugadores de la lista. Devuelve { nombre: character_info }."""
        names = list(dict.fromkeys(k["name"] for k in participants if k.get("player") and k.get("name")))
        names = names[:MAX_PLAYER_LOOKUPS]
        if not names:
            return {}

        async with new_session() as session:
            results = await asyncio.gather(*(fetch_player_data(session, name) for name in names))
        return {
            name: res["character_info"]
            for name, res in zip(names, results)
            if res and res.get("character_info")
        }

    async def _get_killer_image(self, killer, player_infos):
        """Imagen del monstruo, o un outfit genérico según la vocación si es un jugador."""
        if not killer:
            return None

        async with new_session() as session:
            if killer.get("player"):
                vocation = player_infos.get(killer["name"], {}).get("vocation", "")
                return await fetch_wiki_image(session, vocation_file_name(vocation))
            return await fetch_wiki_image(session, creature_file_name(killer["name"]))

    @staticmethod
    def _format_killers(killers, player_infos):
        lines = []
        for k in killers:
            name = k.get("name", "?")
            if k.get("player"):
                url = f"https://www.tibia.com/community/?subtopic=characters&name={name.replace(' ', '+')}"
                line = f"[{name}]({url})"
                info = player_infos.get(name)
                if info:
                    line += f" · {info.get('vocation', 'Sin vocación')} · Nivel {info.get('level', '?')}"
            else:
                line = name.title()
            if k.get("summon"):
                line += f" (invocación: {k['summon']})"
            # Límite de Discord: 1024 caracteres por campo
            if len("\n".join(lines + [line])) > 1020:
                lines.append("…")
                break
            lines.append(line)
        return "\n".join(lines)

    @staticmethod
    def _parse_link(tibia_link, char_info):
        """'[Nombre](url)' -> ('Nombre', 'url')."""
        match = re.match(r"\[(.+?)\]\((.+?)\)", tibia_link or "")
        if match:
            return match.group(1), match.group(2)
        name = char_info.get("name", tibia_link)
        return name, f"https://www.tibia.com/community/?subtopic=characters&name={name.replace(' ', '+')}"

    @staticmethod
    def _format_death_time(time_str):
        """Convierte la hora de la API en un timestamp que Discord muestra en la hora local de cada usuario."""
        try:
            ts = int(datetime.fromisoformat(time_str.replace("Z", "+00:00")).timestamp())
            return f"<t:{ts}:f> (<t:{ts}:R>)"
        except (AttributeError, ValueError):
            return time_str or "N/A"


async def setup(bot):
    await bot.add_cog(DeathTracker(bot))