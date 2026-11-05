import io
import re
import discord
from discord.ext import commands, tasks

from src.core.channel_names import CHANNEL_DEATHS, find_channel
from src.core.config import PLAYERS_STATE, WATCHED_PLAYERS, logger
from src.shared.death_format import format_death_time, format_killers
from src.shared.task_utils import resilient
from src.api import tibiadata, tibiawiki
from src.channels.cementerio.service import DeathService

# Imagen del asesino (monstruo u outfit por vocación) en la esquina del aviso de muerte
SHOW_DEATH_THUMBNAIL = True
# Máximo de avisos de muerte que muestra !muertes (los más recientes)
MAX_DEATHS_LISTED = 10


class DeathTracker(commands.Cog):
    def __init__(self, bot, service):
        self.bot = bot
        self.service = service
        self.track_tibia_players.start()

    def cog_unload(self):
        self.track_tibia_players.cancel()

    @resilient
    @tasks.loop(seconds=60)
    async def track_tibia_players(self):
        logger.info("🔄 === Verificando niveles y muertes de personajes monitoreados ===")

        await self.service.check_players(self._notify_death)

    # --- COMANDO: MUERTES DE HOY ---
    @commands.hybrid_command(name="muertes", description="Muestra las muertes de hoy de los personajes del grupo")
    async def deaths_today(self, ctx):
        logger.info(f"📩 Comando !muertes ejecutado por {ctx.author}")
        await ctx.defer()

        result = await self.service.deaths_today()
        deaths = result.entries

        footer = f"{result.checked} personajes revisados"
        if result.failed:
            footer += f" · {result.failed} sin respuesta de la API"

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
            res = result.details[data["char_name"]]
            embed, thumbnail = await self._build_death_message(
                data["tibia_link"], data["owner"], data.get("current_level", 0), res["character_info"], death
            )
            await ctx.send(**self._message_kwargs(embed, thumbnail))

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
        player_infos = await self.service.fetch_players_info(killers + assists)

        embed.add_field(name="Dueño", value=owner, inline=True)
        embed.add_field(name="Fecha", value=format_death_time(death_info.get("time")), inline=True)
        if killers:
            embed.add_field(name="Asesinos", value=format_killers(killers, player_infos), inline=False)
        else:
            embed.add_field(name="Causa", value=death_info.get("reason", "Desconocida"), inline=False)
        if assists:
            embed.add_field(name="Asistencias", value=format_killers(assists, player_infos), inline=False)
        embed.set_footer(text=f"Mundo: {char_info.get('world', 'Desconocido')}")

        thumbnail = None
        if SHOW_DEATH_THUMBNAIL:
            thumbnail = await self.service.killer_image(
                player_killers[0] if is_pvp else (killers[0] if killers else None), player_infos
            )
        return embed, thumbnail

    @staticmethod
    def _parse_link(tibia_link, char_info):
        """'[Nombre](url)' -> ('Nombre', 'url')."""
        match = re.match(r"\[(.+?)\]\((.+?)\)", tibia_link or "")
        if match:
            return match.group(1), match.group(2)
        name = char_info.get("name", tibia_link)
        return name, f"https://www.tibia.com/community/?subtopic=characters&name={name.replace(' ', '+')}"


def build_service():
    return DeathService(tibiadata, tibiawiki, WATCHED_PLAYERS, PLAYERS_STATE)


async def setup(bot):
    await bot.add_cog(DeathTracker(bot, build_service()))
