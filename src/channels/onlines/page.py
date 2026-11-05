import discord
from discord import app_commands
from discord.ext import commands, tasks

from src.core.channel_names import CHANNEL_LEVEL, CHANNEL_LEVEL_ALT, CHANNEL_ONLINES, find_channel
from src.core.config import WATCHED_PLAYERS, WORLD, logger
from src.shared.task_utils import resilient
from src.shared.discord_utils import move_list_to_bottom, send_or_update_embed
from services.storage import save_data
from src.api import tibiadata
from src.shared.login_times import LoginTracker, parse_api_time
from src.shared.online_embeds import build_grouped_online_embeds
from src.shared.vocation_thumbnails import thumbnail_files
from src.api.tibiawiki import vocation_image_url
from src.channels.onlines.service import OnlineService


def _character_url(char_name):
    return f"https://www.tibia.com/community/?subtopic=characters&name={char_name.replace(' ', '+')}"


class WatchedTracker(commands.Cog):
    def __init__(self, bot, service):
        self.bot = bot
        self.service = service
        self.watched_embed_message = {}
        self.last_online_embeds = None  # Última lista generada (la usa !online)
        self.update_online_watched_players.start()

    def cog_unload(self):
        self.update_online_watched_players.cancel()

    def _get_level_channel(self):
        channel = find_channel(self.bot.get_all_channels(), CHANNEL_LEVEL)
        if not channel:
            channel = find_channel(self.bot.get_all_channels(), CHANNEL_LEVEL_ALT)
        return channel

    # --- COMANDO MANUAL DE NIVELES ---
    @commands.hybrid_command(name="nivel", aliases=["level", "lvl"], description="Revisa niveles ahora o muestra la ficha de un personaje")
    @app_commands.describe(character_name="Nombre del personaje (vacío = revisar a todos)")
    async def level_command(self, ctx, *, character_name: str = None):
        """
        !nivel              -> Revisa ahora mismo si alguien subió de nivel (sin esperar al minuto).
        !nivel <personaje>  -> Muestra el nivel y progreso de ese personaje, y avisa si subió o bajó.
        """
        await ctx.defer()
        logger.info(f"📩 Comando !nivel ejecutado por {ctx.author} ({character_name or 'todos'})")

        if not character_name:
            await ctx.send("🔍 Revisando niveles de los personajes monitoreados...")
            await self.update_online_watched_players()
            await ctx.send("✅ Revisión completada. Si alguien subió de nivel, el aviso ya está en el canal de niveles.")
            return

        check = await self.service.check_character(character_name)
        if not check.info:
            await ctx.send(f"⚠️ El personaje **{character_name}** no está en la lista de monitoreo.")
            return

        info, char_info = check.info, check.char_info
        if not char_info:
            await ctx.send(f"⚠️ No se pudo consultar a **{info['char_name']}** en la API de Tibia. Intenta de nuevo en un rato.")
            return

        level = char_info.get("level", 0)
        vocation = char_info.get("vocation", "N/A")
        world = char_info.get("world", "Desconocido")

        if check.changed_from is not None:
            # Subió o bajó y el aviso automático aún no lo había detectado: se avisa en el canal de niveles
            embed = self._build_level_embed(info, check.changed_from, level, vocation, world)
            level_channel = self._get_level_channel()
            if level_channel and level_channel != ctx.channel:
                await level_channel.send(embed=embed)
            await ctx.send(embed=embed)
        else:
            await ctx.send(embed=self._build_level_embed(info, None, level, vocation, world))

    @staticmethod
    def _build_level_embed(info, old_level, new_level, vocation, world):
        """
        Aviso de subida o bajada de nivel (old_level distinto de new_level),
        o ficha de nivel actual (old_level = None).
        """
        changed = old_level is not None and new_level != old_level
        leveled_up = changed and new_level > old_level
        char_name = info["char_name"]
        url = _character_url(char_name)

        # Título corto (es lo que se ve en la notificación); el nombre enlaza a Tibia.com
        if not changed:
            title, color = f"{char_name} · Nivel {new_level}", discord.Color.dark_teal()
        elif leveled_up:
            title, color = f"{char_name} subió a nivel {new_level}", discord.Color.dark_green()
        else:
            title, color = f"{char_name} bajó a nivel {new_level}", discord.Color.dark_orange()

        embed = discord.Embed(
            title=title,
            url=url,
            color=color,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Dueño", value=info.get("owner", "N/A"), inline=True)
        embed.add_field(name="Vocación", value=vocation, inline=True)
        if changed:
            embed.add_field(name="Nivel", value=f"{old_level} → **{new_level}**", inline=True)

        weekly_start = info.get("weekly_start_level", new_level)
        initial = info.get("initial_level", new_level)
        weekly = new_level - weekly_start
        total = new_level - initial
        embed.add_field(
            name="Progreso",
            value=(
                f"Esta semana: **{weekly:+}** {'nivel' if abs(weekly) == 1 else 'niveles'}\n"
                f"Desde que ingresó: **{total:+}** {'nivel' if abs(total) == 1 else 'niveles'} (nivel inicial {initial})"
            ),
            inline=False,
        )
        embed.set_thumbnail(url=vocation_image_url(vocation))
        embed.set_footer(text=f"Mundo: {world}")
        return embed

    @resilient
    @tasks.loop(seconds=60)
    async def update_online_watched_players(self):
        logger.info(f"🟢 Verificando personajes monitoreados en {WORLD}...")

        if not self.service.has_players():
            return

        # Buscar el canal para alertas de nivel
        level_channel = self._get_level_channel()

        async def notify_level_change(info, old_level, new_level, vocation):
            # Enviar alerta si el canal existe
            if level_channel:
                await level_channel.send(embed=self._build_level_embed(info, old_level, new_level, vocation, WORLD))
            logger.info(f"🎉 Alerta de nivel para {info['char_name']}: {old_level} -> {new_level}")

        online_list = await self.service.check_online(WORLD, notify_level_change)

        entries = [
            (
                player.level,
                player.vocation,
                f"**[{player.name}]({_character_url(player.name)})** · {player.owner}\n"
                f"  Nivel {player.level} · {player.vocation} · {self.service.login_text(player.key)}",
            )
            for player in online_list
        ]
        embed = self._build_online_embeds(entries, WORLD)
        self.last_online_embeds = embed
        # Una sola lista que se edita (sin un mensaje por cada conexión)
        # Siempre se edita el mismo mensaje (entre o salga alguien)
        await send_or_update_embed(self.bot, self.watched_embed_message, CHANNEL_ONLINES, embed)

    def _build_online_embeds(self, online_list, world):
        return build_grouped_online_embeds(
            online_list,
            title="Personajes online",
            footer=f"Mundo: {world} · Conectados: {len(online_list)} de {self.service.player_count()}",
            color=discord.Color.green(),
            empty_text="Ningún personaje monitoreado está conectado.",
        )

    @update_online_watched_players.before_loop
    async def before_update(self):
        await self.bot.wait_until_ready()


class OnlineCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name="online", description="Muestra qué personajes monitoreados están conectados")
    async def list_online_members(self, ctx):
        """Muestra la lista de conectados donde se pide. En #onlines, la mueve al final del canal."""
        logger.info(f"📩 Comando !online ejecutado por {ctx.author}")
        in_onlines = find_channel([ctx.channel], CHANNEL_ONLINES) is not None
        await ctx.defer(ephemeral=in_onlines)

        tracker = self.bot.get_cog("WatchedTracker")
        if not tracker:
            await ctx.send("⚠️ El seguimiento de conectados no está activo.")
            return

        await tracker.update_online_watched_players()
        if in_onlines:
            # En #onlines: la lista actualizada se mueve al final del canal
            await move_list_to_bottom(
                ctx, self.bot, tracker.watched_embed_message, CHANNEL_ONLINES, tracker.last_online_embeds
            )
        else:
            await ctx.send(embeds=tracker.last_online_embeds, files=thumbnail_files(tracker.last_online_embeds))


async def setup(bot):
    service = OnlineService(tibiadata, WATCHED_PLAYERS, save_data, LoginTracker(), parse_api_time)
    await bot.add_cog(WatchedTracker(bot, service))
    await bot.add_cog(OnlineCommands(bot))
