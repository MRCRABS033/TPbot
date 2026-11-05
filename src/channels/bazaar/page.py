"""
Char Bazaar de tibia.com.

- #bazaar: panel con el botón "Buscar personajes" (siempre al final del canal).
- Al pulsarlo: filtros (vocación, PvP, mundo, orden y, en "Más filtros", nivel y precio).
- "Buscar" publica el catálogo como imagen (12 personajes por página) con botones para cambiar
  de página y para ver la ficha completa de un personaje por su código.
- !ver <código> muestra la ficha de la última búsqueda del canal. !bazaar muestra el panel.
"""
import asyncio
import io

import discord
from discord import app_commands
from discord.ext import commands, tasks

from src.api import tibiacom
from src.channels.bazaar.embeds import COLOR, PANEL_TITLE, build_list_text, build_panel_embed
from src.channels.bazaar.image import render_catalog
from src.channels.bazaar.service import BazaarFilters, BazaarService, TooManyResults
from src.channels.bazaar.views import CARDS, LIST, BazaarPanelView, FiltersView, ResultsView, send_details
from src.core.channel_names import CHANNEL_BAZAAR, find_channel
from src.core.config import BAZAAR_PROXY_URL, logger
from src.core.constants.bazaar import BAZAAR_URL, MAX_PAGES_FOR_PRICE_FILTER, PAGE_LOAD_TIMEOUT
from src.shared.discord_utils import send_or_update_embed
from src.shared.task_utils import resilient


# --- COG ---
class Bazaar(commands.Cog):
    def __init__(self, bot, service):
        self.bot = bot
        self.service = service
        self.panel_message = {}      # { guild_id: id del panel en #bazaar }
        bot.add_view(BazaarPanelView(self))

    async def cog_load(self):
        if self.service.start():
            logger.info("🛒 Bazaar: las consultas a tibia.com saldrán por el proxy configurado")
        else:
            logger.info("Bazaar: sin proxy; las consultas a tibia.com salen directo desde este servidor")
        self.keep_panel.start()

    async def cog_unload(self):
        self.keep_panel.cancel()
        await self.service.close()

    async def render_page(self, search, page, mode=CARDS):
        """(embed, archivo o None) de una página del catálogo, o (None, None) si no hay resultados."""
        count = await search.count()
        if not count:
            return None, None
        total = search.total_pages()
        header = (f"{search.filters.describe()}\n**{f'{count:,}'.replace(',', '.')}** subastas · "
                  f"orden: {search.filters.order}")

        embed = discord.Embed(title=PANEL_TITLE, url=BAZAAR_URL, description=header, color=COLOR)
        if search.requested_by:
            embed.set_author(name=f"Buscado por {search.requested_by}")
        embed.set_footer(text=f"Página {page + 1} de {total} · Usa Ver código o !ver <código> para la ficha")

        if mode == LIST:
            entries = await search.page(page)
            if not entries:
                return None, None
            embed.description = f"{header}\n\n{build_list_text(entries)}"
            return embed, None

        png = await self.service.catalog_png(search, page, total)
        if png is None:
            return None, None

        embed.set_image(url="attachment://bazaar.png")
        return embed, discord.File(io.BytesIO(png), filename="bazaar.png")

    async def publish_search(self, channel, filters, user, mode=CARDS):
        """Publica el catálogo de una búsqueda. Devuelve el mensaje, o un texto si falla o no hay resultados."""
        search = self.service.new_search(filters, user.display_name)
        try:
            embed, file = await asyncio.wait_for(self.render_page(search, 0, mode), timeout=PAGE_LOAD_TIMEOUT)
        except TooManyResults as e:
            limit = MAX_PAGES_FOR_PRICE_FILTER * 25
            return (f"⚠️ Hay **{e.results:,}** subastas con estos filtros. Para filtrar por precio la búsqueda "
                    f"debe tener como máximo **{limit}**: acótala con vocación, nivel o mundo.").replace(",", ".")
        except Exception as e:
            logger.error(f"❌ Error al buscar en el bazaar: {e!r}")
            return f"⚠️ No se pudo consultar el bazaar de tibia.com: {self.service.describe_error(e)}"
        if not embed:
            return f"No se encontraron subastas con estos filtros: {filters.describe()}"

        view = ResultsView(self, search, mode=mode)
        message = await channel.send(embed=embed, view=view, **({"file": file} if file else {}))
        view.message = message
        self.service.remember(message.id, channel.id, search)
        logger.info(f"🛒 Búsqueda en el bazaar de {user}: {filters.describe()}")
        return message

    # --- PANEL SIEMPRE AL FINAL DE #bazaar ---
    @resilient
    @tasks.loop(minutes=1)
    async def keep_panel(self):
        if not self.service.worlds:
            await self.service.load_worlds()
        await send_or_update_embed(self.bot, self.panel_message, CHANNEL_BAZAAR, build_panel_embed(),
                                   view=BazaarPanelView(self), keep_last=True)

    @keep_panel.before_loop
    async def before_keep_panel(self):
        await self.bot.wait_until_ready()

    # --- COMANDOS ---
    @commands.hybrid_command(name="bazaar", description="Muestra el panel para buscar personajes en el Char Bazaar")
    @app_commands.describe(mundo="Mundo para filtrar (escribe para buscar entre todos)")
    async def bazaar_command(self, ctx, *, mundo: str = None):
        logger.info(f"📩 Comando !bazaar ejecutado por {ctx.author} (mundo: {mundo})")
        if not mundo:
            if find_channel([ctx.channel], CHANNEL_BAZAAR):
                # En #bazaar ya está el panel: solo se mueve al final (sin crear otro)
                if ctx.interaction:
                    await ctx.send("El panel está al final del canal.", ephemeral=True)
                else:
                    try:
                        await ctx.message.delete()
                    except discord.HTTPException:
                        pass
                await self.keep_panel()
                return
            await ctx.send(embed=build_panel_embed(), view=BazaarPanelView(self))
            return

        # Con mundo: se abren los filtros con ese mundo ya elegido
        await ctx.defer(ephemeral=True)
        if not self.service.worlds:
            await self.service.load_worlds()
        world = self.service.find_world(mundo)
        if not world:
            await ctx.send(f"⚠️ No existe el mundo **{mundo}** en Tibia.", ephemeral=True)
            return
        view = FiltersView(self, BazaarFilters(world=world))
        await ctx.send(view.summary(), view=view, ephemeral=True)

    @bazaar_command.autocomplete("mundo")
    async def world_autocomplete(self, interaction: discord.Interaction, current: str):
        """Buscador de /bazaar: muestra los mundos que coinciden con lo que se va escribiendo."""
        if not self.service.worlds:
            await self.service.load_worlds()
        return [app_commands.Choice(name=w, value=w) for w in self.service.worlds_matching(current)[:25]]

    @commands.hybrid_command(name="ver", description="Muestra la ficha de un personaje de la última búsqueda del bazaar")
    @app_commands.describe(code="Código del personaje en el catálogo (ej: 03)")
    async def view_command(self, ctx, code: str):
        logger.info(f"📩 Comando !ver {code} ejecutado por {ctx.author}")
        search = self.service.last_search.get(ctx.channel.id)
        if not search:
            await ctx.send("⚠️ No hay ninguna búsqueda reciente del bazaar en este canal. Usa **!bazaar** para buscar.")
            return
        await ctx.defer()

        async def send(**kwargs):
            kwargs.pop("ephemeral", None)
            await ctx.send(**kwargs)

        await send_details(send, self.service, search, code, ephemeral=False)


async def setup(bot):
    service = BazaarService(tibiacom, render_catalog, proxy_configured=bool(BAZAAR_PROXY_URL))
    await bot.add_cog(Bazaar(bot, service))
