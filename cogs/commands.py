import aiohttp
import discord
from discord.ext import commands

from config import WATCHED_PLAYERS, PLAYERS_STATE, logger
from services.tibia_api import fetch_player_data, fetch_world_online_players
from services.storage import save_data


class PlayerCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # --- GESTIÓN DE JUGADORES (CON PERSISTENCIA Y NIVEL INICIAL) ---

    @commands.command(name="add")
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

    @commands.command(name="remove")
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

    # --- CONSULTAS DE LA LISTA ---

    @commands.command(name="list")
    async def list_players(self, ctx):
        logger.info(f"📩 Comando !list ejecutado por {ctx.author}")
        if not WATCHED_PLAYERS:
            await ctx.send("📋 La lista de seguimiento está vacía.")
            return

        embed = discord.Embed(title="📋 Personajes Monitoreados", color=discord.Color.blue())

        grouped = {}
        for data in WATCHED_PLAYERS.values():
            owner = data["owner"]
            link = data["tibia_link"]
            init_lvl = data.get("initial_level", "N/A")
            curr_lvl = data.get("current_level", "N/A")
            voc = data.get("vocation", "")

            entry = f"• {link} (Nivel Ingreso: **{init_lvl}** ➔ Actual: **{curr_lvl}** - {voc})"
            grouped.setdefault(owner, []).append(entry)

        for owner, entries in grouped.items():
            chars_formatted = "\n".join(entries)
            embed.add_field(name=f"👤 Dueño: {owner}", value=chars_formatted, inline=False)

        await ctx.send(embed=embed)

    @commands.command(name="owner")
    async def list_chars_by_owner(self, ctx, *, owner_name: str):
        logger.info(f"📩 Comando !owner ejecutado por {ctx.author} para el dueño: '{owner_name}'")
        search_owner = owner_name.strip().lower()

        found_chars = []
        for data in WATCHED_PLAYERS.values():
            if data["owner"].lower() == search_owner:
                found_chars.append(data["tibia_link"])

        if not found_chars:
            await ctx.send(f"⚠️ No se encontraron personajes registrados a nombre de **{owner_name}**.")
            return

        embed = discord.Embed(
            title=f"👤 Personajes de {owner_name}",
            description="\n".join([f"• {link}" for link in found_chars]),
            color=discord.Color.purple(),
        )
        await ctx.send(embed=embed)

    @commands.command(name="whois")
    async def whois_char(self, ctx, *, character_name: str):
        logger.info(f"📩 Comando !whois ejecutado por {ctx.author} para el personaje: '{character_name}'")
        char_key = character_name.strip().lower()

        if char_key in WATCHED_PLAYERS:
            p = WATCHED_PLAYERS[char_key]
            link = p["tibia_link"]
            owner = p["owner"]
            init_lvl = p.get("initial_level", "N/A")
            curr_lvl = p.get("current_level", "N/A")
            weekly_start = p.get("weekly_start_level", curr_lvl)
            voc = p.get("vocation", "")

            # Calcular diferencias de nivel
            if isinstance(curr_lvl, int):
                total_gained = curr_lvl - init_lvl if isinstance(init_lvl, int) else 0
                weekly_gained = curr_lvl - weekly_start if isinstance(weekly_start, int) else 0

                progress_str = (
                    f"📈 **Progreso total:** {total_gained:+} niveles\n"
                    f"📊 **Progreso semanal:** {weekly_gained:+} niveles"
                )
            else:
                progress_str = ""

            await ctx.send(
                f"🔎 El personaje {link} pertenece a **{owner}**.\n"
                f"🛡️ **Vocación:** {voc}\n"
                f"📍 **Nivel ingreso:** {init_lvl} | **Nivel actual:** {curr_lvl}\n"
                f"{progress_str}"
            )
        else:
            await ctx.send(f"⚠️ El personaje **{character_name}** no se encuentra en la lista de monitoreo.")

    # --- INFORMACIÓN EN TIEMPO REAL Y RANKINGS ---

    @commands.command(name="empleado", aliases=["top", "semana"])
    async def show_weekly_top(self, ctx):
        """Muestra el ranking actual de niveles ganados en la semana."""
        logger.info(f"📩 Comando !empleado ejecutado por {ctx.author}")
        if not WATCHED_PLAYERS:
            await ctx.send("📋 La lista de seguimiento está vacía.")
            return

        ranking = []
        for key, data in WATCHED_PLAYERS.items():
            curr = data.get("current_level", 0)
            start = data.get("weekly_start_level", curr)
            gained = max(0, curr - start)
            ranking.append((data["char_name"], data["owner"], gained, curr, start))

        ranking.sort(key=lambda x: x[2], reverse=True)

        embed = discord.Embed(
            title="🏆 Ranking Semanal - Empleado del Mes",
            description="Progreso de niveles acumulados durante la semana actual:",
            color=discord.Color.gold(),
        )

        medals = ["🥇", "🥈", "🥉"]
        lines = []

        for idx, (char, owner, gained, curr, start) in enumerate(ranking, start=1):
            icon = medals[idx - 1] if idx <= 3 else f"`#{idx}`"
            lines.append(
                f"{icon} **{char}** ({owner}) ➔ **+{gained} lvl** (Inicio: {start} ➔ Actual: {curr})"
            )

        embed.description = "\n".join(lines)
        embed.set_footer(text="El conteo se reinicia cada domingo en el Server Save.")
        await ctx.send(embed=embed)

    @commands.command(name="free")
    async def list_free_players(self, ctx, world: str = "Quidera"):
        logger.info(f"📩 Comando !free ejecutado por {ctx.author} para el mundo: '{world}'")

        target_channel = discord.utils.get(ctx.guild.text_channels, name="frees")
        if not target_channel:
            await ctx.send("⚠️ No se encontró el canal de texto **#frees** en este servidor.")
            return

        await ctx.send(f"🔍 Consultando jugadores Free Account en **{world}**... (El resultado se enviará a {target_channel.mention})")

        FREE_VOCATIONS = {
            "paladin",
            "knight",
            "sorcerer",
            "druid",
            "monk",
            "none",
            "none (no vocation)",
            "no vocation",
        }

        async with aiohttp.ClientSession() as session:
            online_players = await fetch_world_online_players(session, world)

            if not online_players:
                await target_channel.send(f"ℹ️ No se encontraron jugadores conectados en este momento en **{world}**.")
                return

            free_players = []
            for player in online_players:
                p_name = player.get("name")
                p_level = player.get("level", 0)
                p_vocation = player.get("vocation", "").strip()

                if p_vocation.lower() in FREE_VOCATIONS:
                    official_url = f"https://www.tibia.com/community/?subtopic=characters&name={p_name.replace(' ', '+')}"
                    tibia_link = f"[{p_name}]({official_url})"
                    free_players.append(f"• {tibia_link} (Nivel {p_level} - {p_vocation})")

            if not free_players:
                await target_channel.send(f"ℹ️ No se encontraron jugadores **Free Account** conectados en este momento en **{world}**.")
                return

            embed = discord.Embed(
                title=f"🆓 Jugadores Free Account Online en {world}",
                description="\n".join(free_players[:25]),
                color=discord.Color.gold(),
            )
            embed.set_footer(text=f"Total Free Online: {len(free_players)}")
            await target_channel.send(embed=embed)

    @commands.command(name="online")
    async def list_online_members(self, ctx):
        logger.info(f"📩 Comando !online ejecutado por {ctx.author}")

        if not WATCHED_PLAYERS:
            await ctx.send("📋 La lista de seguimiento está vacía.")
            return

        await ctx.send("🔍 Verificando qué jugadores de la lista están en línea...")

        online_members = []
        async with aiohttp.ClientSession() as session:
            for char_key, data in WATCHED_PLAYERS.items():
                res = await fetch_player_data(session, data["char_name"])
                if res and res.get("character_info"):
                    status = res["character_info"].get("status", "").lower()
                    if status == "online":
                        owner = data["owner"]
                        link = res["tibia_link"]
                        lvl = res["character_info"].get("level", "N/A")
                        voc = res["character_info"].get("vocation", "N/A")
                        
                        # Actualizar el nivel actual en memoria y guardar en disco
                        if isinstance(lvl, int):
                            data["current_level"] = lvl
                            save_data()

                        online_members.append(f"🟢 {link} (Nivel {lvl} {voc}) - **Dueño:** {owner}")

        if not online_members:
            await ctx.send("🔴 Ningún jugador de la lista monitoreada está conectado en este momento.")
            return

        embed = discord.Embed(
            title="🟢 Jugadores Monitoreados En Línea",
            description="\n".join(online_members),
            color=discord.Color.green(),
        )
        await ctx.send(embed=embed)

    # --- AYUDA ---

    @commands.command(name="help")
    async def help_command(self, ctx):
        logger.info(f"📩 Comando !help ejecutado por {ctx.author}")
        embed = discord.Embed(
            title="📖 Comandos Disponibles",
            description="Lista de comandos que puedes usar con este bot:",
            color=discord.Color.blue(),
        )
        embed.add_field(name="!add <dueño> <personaje>", value="Agrega un personaje a la lista de monitoreo.", inline=False)
        embed.add_field(name="!remove <personaje>", value="Elimina un personaje de la lista de monitoreo.", inline=False)
        embed.add_field(name="!list", value="Muestra todos los personajes actualmente monitoreados.", inline=False)
        embed.add_field(name="!empleado", value="Muestra el ranking semanal de niveles ganados.", inline=False)
        embed.add_field(name="!free [mundo]", value="Muestra jugadores Free Account online en el mundo especificado.", inline=False)
        embed.add_field(name="!online", value="Muestra qué personajes de la lista están actualmente en línea.", inline=False)
        embed.add_field(name="!owner <dueño>", value="Muestra todos los personajes registrados a nombre del dueño especificado.", inline=False)
        embed.add_field(name="!whois <personaje>", value="Muestra el dueño e información detallada de nivel.", inline=False)
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(PlayerCommands(bot))