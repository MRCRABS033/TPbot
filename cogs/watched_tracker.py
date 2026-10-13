import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

from channels import CHANNEL_LEVEL, CHANNEL_LEVEL_ALT, CHANNEL_ONLINES, find_channel
from config import WATCHED_PLAYERS, logger
from services.discord_utils import broadcast_embed, repost_embed, send_or_update_embed
from services.storage import save_data
from services.tibia_api import fetch_player_data, fetch_world_online_players
from services.online_embeds import build_grouped_online_embeds
from services.tibia_wiki import vocation_image_url


class WatchedTracker(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.watched_embed_message = {}
        # Personajes online en la revisión anterior. None = aún no hay revisión previa
        # (así no se avisa de todos los conectados cada vez que el bot arranca).
        self.previous_online = None
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

        info = WATCHED_PLAYERS.get(character_name.strip().lower())
        if not info:
            await ctx.send(f"⚠️ El personaje **{character_name}** no está en la lista de monitoreo.")
            return

        async with aiohttp.ClientSession() as session:
            res = await fetch_player_data(session, info["char_name"])
        char_info = (res or {}).get("character_info")
        if not char_info:
            await ctx.send(f"⚠️ No se pudo consultar a **{info['char_name']}** en la API de Tibia. Intenta de nuevo en un rato.")
            return

        level = char_info.get("level", 0)
        vocation = char_info.get("vocation", "N/A")
        world = char_info.get("world", "Desconocido")
        old_level = info.get("current_level", level)

        if level != old_level:
            # Subió o bajó y el aviso automático aún no lo había detectado: se avisa en el canal de niveles
            info["current_level"] = level
            save_data()
            embed = self._build_level_embed(info, old_level, level, vocation, world)
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
        url = f"https://www.tibia.com/community/?subtopic=characters&name={char_name.replace(' ', '+')}"

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

    @tasks.loop(seconds=60)
    async def update_online_watched_players(self):
        world = "Quidera"
        logger.info(f"🟢 Verificando personajes monitoreados en {world}...")

        if not WATCHED_PLAYERS:
            return

        # Buscar el canal para alertas de nivel
        level_channel = self._get_level_channel()

        async with aiohttp.ClientSession() as session:
            online_world_players = await fetch_world_online_players(session, world)
            online_lookup = {
                p.get("name", "").lower(): p for p in (online_world_players or [])
            }

            online_list = []
            online_keys = set()
            logins = []
            for char_key, info in WATCHED_PLAYERS.items():
                char_name = info["char_name"]
                owner_name = info["owner"]

                if char_name.lower() in online_lookup:
                    player_data = online_lookup[char_name.lower()]
                    lvl = player_data.get("level", 0)
                    voc = player_data.get("vocation", "N/A")
                    url = f"https://www.tibia.com/community/?subtopic=characters&name={char_name.replace(' ', '+')}"

                    # --- DETECCIÓN DE CONEXIÓN ---
                    online_keys.add(char_key)
                    if self.previous_online is not None and char_key not in self.previous_online:
                        logins.append(self._build_login_embed(char_name, url, owner_name, lvl, voc))

                    # --- DETECCIÓN Y ALERTA DE SUBIDA DE NIVEL ---
                    old_level = info.get("current_level")

                    if old_level is None:
                        # Inicializar si no estaba registrado
                        info["current_level"] = lvl
                        save_data()
                    elif lvl != old_level:
                        # Subió o bajó de nivel (por ejemplo, al morir)
                        info["current_level"] = lvl
                        save_data()

                        # Enviar alerta si el canal existe
                        if level_channel:
                            await level_channel.send(embed=self._build_level_embed(info, old_level, lvl, voc, world))

                        logger.info(f"🎉 Alerta de nivel para {char_name}: {old_level} -> {lvl}")

                    online_list.append((lvl, voc, f"**[{char_name}]({url})** · {owner_name}\n  Nivel {lvl} · {voc}"))

            embed = self._build_online_embeds(online_list, world)
            self.last_online_embeds = embed

            # Si la API falló (lista vacía) no se actualiza el estado, para no avisar
            # de todos como "recién conectados" en la siguiente revisión.
            if online_world_players:
                self.previous_online = online_keys

            if logins:
                for login_embed in logins:
                    await broadcast_embed(self.bot, CHANNEL_ONLINES, login_embed)
                # La lista se vuelve a publicar para que quede debajo de los avisos
                await repost_embed(self.bot, self.watched_embed_message, CHANNEL_ONLINES, embed)
            else:
                await send_or_update_embed(
                    self.bot, self.watched_embed_message, CHANNEL_ONLINES, embed
                )

    @staticmethod
    def _build_online_embeds(online_list, world):
        return build_grouped_online_embeds(
            online_list,
            title="Personajes online",
            footer=f"Mundo: {world} · Conectados: {len(online_list)} de {len(WATCHED_PLAYERS)}",
            color=discord.Color.green(),
            empty_text="Ningún personaje monitoreado está conectado.",
        )

    @staticmethod
    def _build_login_embed(char_name, url, owner, level, vocation):
        logger.info(f"🟢 {char_name} se conectó")
        embed = discord.Embed(
            title=f"{char_name} se conectó",
            url=url,
            description=f"{owner} · Nivel {level} · {vocation}",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow(),
        )
        return embed

    @update_online_watched_players.before_loop
    async def before_update(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(WatchedTracker(bot))