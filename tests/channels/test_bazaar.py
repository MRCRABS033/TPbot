"""Pruebas de #bazaar sin internet ni Discord."""
import asyncio
import io
import os
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from PIL import Image

from src.channels.bazaar.service import (
    MAX_STORED_SEARCHES,
    BazaarFilters,
    BazaarSearch,
    BazaarService,
    TooManyResults,
    format_tc,
    format_time_left,
    parse_number,
)
from src.core.constants.bazaar import CARDS_PER_IMAGE, IMAGE_CACHE_SECONDS, MAX_PAGES_FOR_PRICE_FILTER, TIBIA_PAGE_SIZE


def auction(number, bid=1000):
    return SimpleNamespace(
        name=f"Personaje {number}", auction_id=number, level=100 + number, vocation="Knight", world="Antica", bid=bid,
        bid_type="current", auction_end=datetime.now(timezone.utc) + timedelta(days=1),
        outfit=SimpleNamespace(image_url=f"http://outfit/{number}.gif"),
    )


def png_bytes():
    out = io.BytesIO()
    Image.new("RGBA", (8, 8), (255, 0, 0, 255)).save(out, format="PNG")
    return out.getvalue()


class FakeApi:
    ERROR_FORBIDDEN, ERROR_PROXY, ERROR_MAINTENANCE, ERROR_NETWORK = "403", "proxy", "maint", "net"

    def __init__(self, auctions=(), worlds=()):
        self.auctions = list(auctions)
        self.worlds = set(worlds)
        self.page_requests = []
        self.images = {}
        self.error_kind = None
        self.closed = False

    def build_auction_filters(self, **kwargs):
        return kwargs

    async def fetch_auctions_page(self, client, number, filters):
        self.page_requests.append(number)
        start = (number - 1) * TIBIA_PAGE_SIZE
        total_pages = -(-len(self.auctions) // TIBIA_PAGE_SIZE)
        return SimpleNamespace(
            entries=self.auctions[start:start + TIBIA_PAGE_SIZE],
            results_count=len(self.auctions),
            total_pages=total_pages,
        )

    async def fetch_images(self, urls):
        return {url: self.images.get(url, png_bytes()) for url in urls}

    async def fetch_available_worlds(self, client):
        if self.worlds == {"falla"}:
            raise RuntimeError("caído")
        return self.worlds

    async def fetch_auction_details(self, auction_id):
        return None

    def create_client(self):
        return "cliente"

    def uses_proxy(self, client):
        return True

    async def close_client(self, client):
        self.closed = True

    def classify_error(self, error):
        return self.error_kind


def make_service(api=None, render=None, **kwargs):
    render = render or MagicMock(return_value=b"png")
    service = BazaarService(api or FakeApi(), render, **kwargs)
    service.start()
    return service


class UtilsTests(unittest.TestCase):
    def test_parse_number(self):
        for text, expected in (("5000", 5000), ("5.000", 5000), ("5,000", 5000), ("5k", 5000), ("1,5k", 1500),
                               (" 2 k ", 2000), ("", None), (None, None)):
            self.assertEqual(parse_number(text), expected, text)
        with self.assertRaises(ValueError):
            parse_number("abc")

    def test_formatos(self):
        self.assertEqual(format_tc(22500), "22.500 TC")
        now = 1000
        end = lambda seconds: SimpleNamespace(timestamp=lambda: now + seconds)
        self.assertEqual(format_time_left(end(-1), now), "terminada")
        self.assertEqual(format_time_left(end(90000), now), "1d 1h")
        self.assertEqual(format_time_left(end(5400), now), "1h 30m")
        self.assertEqual(format_time_left(end(10), now), "1m")

    def test_filtros(self):
        self.assertEqual(BazaarFilters().describe(), "Sin filtros")
        filters = BazaarFilters(min_level=300, max_level=500, world="Antica", max_price=5000)
        self.assertEqual(filters.describe(), "Nivel 300–500 · Antica · hasta 5.000 TC")
        self.assertTrue(filters.matches_price(5000))
        self.assertFalse(filters.matches_price(5001))
        self.assertEqual(filters.with_changes(world=None).world, None)
        self.assertEqual(filters.world, "Antica")  # El original no cambia


class SearchTests(unittest.IsolatedAsyncioTestCase):
    def search(self, api, **filters):
        return BazaarSearch(api, "cliente", BazaarFilters(**filters))

    async def test_sin_precio_solo_pide_las_paginas_necesarias(self):
        api = FakeApi([auction(i) for i in range(TIBIA_PAGE_SIZE * 3)])
        search = self.search(api)
        self.assertEqual(await search.count(), TIBIA_PAGE_SIZE * 3)
        self.assertEqual((await search.by_code(TIBIA_PAGE_SIZE + 1)).auction_id, TIBIA_PAGE_SIZE)
        self.assertEqual(api.page_requests, [1, 2])

    async def test_codigo_inexistente(self):
        search = self.search(FakeApi([auction(1)]))
        self.assertIsNone(await search.by_code(0))
        self.assertIsNone(await search.by_code(2))

    async def test_paginas_del_catalogo(self):
        search = self.search(FakeApi([auction(i) for i in range(CARDS_PER_IMAGE + 1)]))
        await search.count()
        self.assertEqual(search.total_pages(), 2)
        self.assertTrue(search.has_page(1))
        self.assertFalse(search.has_page(2))
        self.assertEqual(len(await search.page(0)), CARDS_PER_IMAGE)
        self.assertEqual([code for code, _ in await search.page(1)], [CARDS_PER_IMAGE + 1])

    async def test_con_precio_filtra_todas_las_paginas(self):
        auctions = [auction(i, bid=i * 100) for i in range(TIBIA_PAGE_SIZE + 5)]
        search = self.search(FakeApi(auctions), min_price=500, max_price=1000)
        self.assertEqual(await search.count(), 6)
        self.assertEqual((await search.by_code(1)).bid, 500)

    async def test_con_precio_y_demasiados_resultados(self):
        auctions = [auction(i) for i in range(TIBIA_PAGE_SIZE * (MAX_PAGES_FOR_PRICE_FILTER + 1))]
        with self.assertRaises(TooManyResults):
            await self.search(FakeApi(auctions), max_price=10).count()


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_arranque_y_cierre(self):
        api = FakeApi()
        service = BazaarService(api, MagicMock())
        await service.close()
        self.assertFalse(api.closed)  # Sin cliente no hay nada que cerrar
        self.assertTrue(service.start())
        await service.close()
        self.assertTrue(api.closed)

    async def test_mundos(self):
        service = make_service(FakeApi(worlds={"Antica", "Secura", "Vunira"}))
        await service.load_worlds()
        self.assertEqual(service.find_world(" ANTICA "), "Antica")
        self.assertIsNone(service.find_world("Nada"))
        self.assertEqual(service.worlds_matching("ra"), ["Secura", "Vunira"])
        self.assertEqual(service.worlds_matching("se"), ["Secura"])

    async def test_fallo_de_mundos_no_rompe(self):
        service = make_service(FakeApi(worlds={"falla"}))
        await service.load_worlds()
        self.assertEqual(service.worlds, set())

    def test_conserva_solo_las_busquedas_recientes(self):
        service = make_service()
        for i in range(MAX_STORED_SEARCHES + 2):
            service.remember(i, 1, f"busqueda {i}")
        self.assertEqual(len(service.searches), MAX_STORED_SEARCHES)
        self.assertNotIn(0, service.searches)
        self.assertEqual(service.last_search[1], f"busqueda {MAX_STORED_SEARCHES + 1}")

    async def test_imagen_se_reutiliza_hasta_que_vence(self):
        now = [0]
        render = MagicMock(return_value=b"png")
        service = make_service(FakeApi([auction(1)]), render, clock=lambda: now[0])
        search = service.new_search(BazaarFilters(), "Vale")
        self.assertEqual(await service.catalog_png(search, 0, 1), b"png")
        await service.catalog_png(search, 0, 1)
        render.assert_called_once()
        self.assertEqual(render.call_args.args[2], "PÁGINA 1 DE 1")
        now[0] = IMAGE_CACHE_SECONDS
        await service.catalog_png(search, 0, 1)
        self.assertEqual(render.call_count, 2)

    async def test_pagina_sin_subastas_no_dibuja(self):
        render = MagicMock()
        service = make_service(FakeApi(), render)
        self.assertIsNone(await service.catalog_png(service.new_search(BazaarFilters(), "V"), 0, 1))
        render.assert_not_called()

    async def test_outfit_al_doble(self):
        service = make_service()
        png = await service.outfit_png("http://outfit/doble.gif")
        self.assertEqual(Image.open(io.BytesIO(png)).size, (16, 16))

    async def test_outfit_danado(self):
        api = FakeApi()
        api.images["http://outfit/malo.gif"] = b"no es imagen"
        self.assertIsNone(await make_service(api).outfit_png("http://outfit/malo.gif"))

    def test_mensajes_de_error(self):
        api = FakeApi()
        service = make_service(api)
        self.assertIn("tardó demasiado", service.describe_error(asyncio.TimeoutError()))
        api.error_kind = api.ERROR_FORBIDDEN
        self.assertIn("no hay", service.describe_error(RuntimeError()))
        self.assertIn("aunque se usó el proxy", make_service(api, proxy_configured=True).describe_error(RuntimeError()))
        api.error_kind = api.ERROR_PROXY
        self.assertIn("proxy", service.describe_error(RuntimeError()))
        api.error_kind = api.ERROR_MAINTENANCE
        self.assertIn("mantenimiento", service.describe_error(RuntimeError()))
        api.error_kind = api.ERROR_NETWORK
        self.assertIn("error de conexión", service.describe_error(RuntimeError("x")))
        api.error_kind = None
        self.assertIn("RuntimeError", service.describe_error(RuntimeError()))


class PageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        os.environ.setdefault("DISCORD_TOKEN", "test")  # config.py lo exige al importarse
        from src.channels.bazaar import embeds, page, views
        self.page, self.views, self.embeds = page, views, embeds

    def make_cog(self, service):
        cog = self.page.Bazaar.__new__(self.page.Bazaar)  # Sin __init__: no registra vistas ni tareas
        cog.bot, cog.service, cog.panel_message = MagicMock(), service, {}
        return cog

    def interaction(self):
        return MagicMock(
            response=MagicMock(defer=AsyncMock(), send_message=AsyncMock(), send_modal=AsyncMock(), edit_message=AsyncMock()),
            followup=MagicMock(send=AsyncMock()),
            edit_original_response=AsyncMock(),
            channel=MagicMock(send=AsyncMock(return_value=MagicMock(id=5, jump_url="http://msg"))),
        )

    async def test_catalogo_en_cartas_y_lista(self):
        service = make_service(FakeApi([auction(1)]))
        cog = self.make_cog(service)
        search = service.new_search(BazaarFilters(world="Antica"), "Vale")
        embed, file = await cog.render_page(search, 0, self.views.CARDS)
        self.assertEqual(embed.image.url, "attachment://bazaar.png")
        self.assertEqual(file.filename, "bazaar.png")
        self.assertEqual(embed.author.name, "Buscado por Vale")
        embed, file = await cog.render_page(search, 0, self.views.LIST)
        self.assertIsNone(file)
        self.assertIn("Personaje 1", embed.description)
        self.assertIn("(actual)", embed.description)

    async def test_sin_resultados(self):
        service = make_service()
        cog = self.make_cog(service)
        self.assertEqual(await cog.render_page(service.new_search(BazaarFilters(), "V"), 0), (None, None))
        text = await cog.publish_search(MagicMock(), BazaarFilters(world="Antica"), MagicMock(display_name="V"))
        self.assertEqual(text, "No se encontraron subastas con estos filtros: Antica")

    async def test_publicar_guarda_la_busqueda_para_ver(self):
        service = make_service(FakeApi([auction(1)]))
        channel = MagicMock(id=9, send=AsyncMock(return_value=MagicMock(id=5)))
        message = await self.make_cog(service).publish_search(channel, BazaarFilters(), MagicMock(display_name="V"))
        self.assertEqual(message.id, 5)
        self.assertIn(5, service.searches)
        self.assertIs(service.last_search[9], service.searches[5])
        self.assertIsInstance(channel.send.await_args.kwargs["view"], self.views.ResultsView)
        self.assertIn("file", channel.send.await_args.kwargs)

    async def test_publicar_con_demasiados_resultados_o_error(self):
        auctions = [auction(i) for i in range(TIBIA_PAGE_SIZE * (MAX_PAGES_FOR_PRICE_FILTER + 1))]
        cog = self.make_cog(make_service(FakeApi(auctions)))
        text = await cog.publish_search(MagicMock(), BazaarFilters(max_price=1), MagicMock(display_name="V"))
        self.assertIn("acótala", text)

        api = FakeApi()
        api.fetch_auctions_page = AsyncMock(side_effect=RuntimeError("caído"))
        text = await self.make_cog(make_service(api)).publish_search(MagicMock(), BazaarFilters(), MagicMock(display_name="V"))
        self.assertIn("No se pudo consultar", text)
        self.assertIn("RuntimeError", text)

    async def test_ficha_por_codigo(self):
        service = make_service(FakeApi([auction(1)]))
        search = service.new_search(BazaarFilters(), "V")
        send = AsyncMock()
        await self.views.send_details(send, service, search, "abc", ephemeral=True)
        self.assertIn("solo el número", send.await_args.kwargs["content"])
        await self.views.send_details(send, service, search, "99", ephemeral=True)
        self.assertIn("**99**", send.await_args.kwargs["content"])
        await self.views.send_details(send, service, search, "[01]", ephemeral=True)
        kwargs = send.await_args.kwargs
        self.assertEqual(kwargs["embed"].title, "[01] Personaje 1")
        self.assertEqual(kwargs["file"].filename, "outfit.png")
        self.assertEqual(kwargs["embed"].thumbnail.url, "attachment://outfit.png")

    async def test_paginas_y_botones_del_catalogo(self):
        service = make_service(FakeApi([auction(i) for i in range(CARDS_PER_IMAGE + 1)]))
        cog = self.make_cog(service)
        search = service.new_search(BazaarFilters(), "V")
        await search.count()
        view = self.views.ResultsView(cog, search)
        self.assertTrue(view.previous.disabled)
        self.assertFalse(view.next.disabled)

        interaction = self.interaction()
        interaction.message = MagicMock(edit=AsyncMock())
        await view.next.callback(interaction)
        self.assertEqual(view.page, 1)
        self.assertTrue(view.next.disabled)
        self.assertFalse(view.previous.disabled)
        interaction.message.edit.assert_awaited_once()

    async def test_fallo_al_cargar_pagina_reactiva_botones_y_avisa(self):
        service = make_service(FakeApi([auction(i) for i in range(CARDS_PER_IMAGE + 1)]))
        cog = self.make_cog(service)
        search = service.new_search(BazaarFilters(), "V")
        await search.count()
        view = self.views.ResultsView(cog, search)
        cog.render_page = AsyncMock(side_effect=RuntimeError("caído"))
        interaction = self.interaction()
        interaction.message = MagicMock(edit=AsyncMock())
        await view.next.callback(interaction)
        self.assertEqual(view.page, 0)
        self.assertFalse(view.next.disabled)
        self.assertIn("No se pudo cargar", interaction.followup.send.await_args.args[0])

    async def test_filtros_y_buscador_de_mundos(self):
        service = make_service(FakeApi(worlds={"Antica", "Secura", "Vunira"}))
        await service.load_worlds()
        view = self.views.FiltersView(self.make_cog(service))
        self.assertEqual(view.search_worlds("anti"), ["Antica"])
        self.assertEqual(view.filters.world, "Antica")
        self.assertEqual(view.search_worlds("ra"), ["Secura", "Vunira"])
        self.assertEqual(view.world_matches, ["Secura", "Vunira"])
        self.assertEqual(view.search_worlds(""), [])
        self.assertIsNone(view.filters.world)

    async def test_mas_filtros_valida_numeros(self):
        view = self.views.FiltersView(self.make_cog(make_service()))
        modal = self.views.MoreFiltersModal(view)
        modal.min_level._value, modal.max_level._value, modal.min_price._value, modal.max_price._value = "300", "", "", "5k"
        interaction = self.interaction()
        await modal.on_submit(interaction)
        self.assertEqual((view.filters.min_level, view.filters.max_price), (300, 5000))
        modal.min_level._value = "abc"
        interaction = self.interaction()
        await modal.on_submit(interaction)
        self.assertIn("deben ser números", interaction.response.send_message.await_args.args[0])

    async def test_panel_carga_mundos_si_faltan(self):
        service = make_service(FakeApi(worlds={"Antica"}))
        view = self.views.BazaarPanelView(self.make_cog(service))
        interaction = self.interaction()
        await view.open_filters.callback(interaction)
        self.assertEqual(service.worlds, {"Antica"})
        self.assertIsInstance(interaction.followup.send.await_args.kwargs["view"], self.views.FiltersView)

        interaction = self.interaction()
        await view.open_filters.callback(interaction)
        interaction.response.defer.assert_not_awaited()
        interaction.response.send_message.assert_awaited_once()

    async def test_buscar_desde_los_filtros_publica(self):
        service = make_service(FakeApi([auction(1)]))
        view = self.views.FiltersView(self.make_cog(service))
        interaction = self.interaction()
        interaction.user.display_name = "Vale"
        await view.search_cards.callback(interaction)
        self.assertIn("http://msg", interaction.edit_original_response.await_args.kwargs["content"])

    async def test_comando_bazaar_con_mundo(self):
        service = make_service(FakeApi(worlds={"Antica"}))
        cog = self.make_cog(service)
        ctx = MagicMock(defer=AsyncMock(), send=AsyncMock())
        await self.page.Bazaar.bazaar_command.callback(cog, ctx, mundo="nada")
        self.assertIn("No existe el mundo", ctx.send.await_args.args[0])
        await self.page.Bazaar.bazaar_command.callback(cog, ctx, mundo="antica")
        self.assertEqual(ctx.send.await_args.kwargs["view"].filters.world, "Antica")

    async def test_comando_bazaar_fuera_del_canal_muestra_panel(self):
        cog = self.make_cog(make_service())
        ctx = MagicMock(send=AsyncMock())
        with patch.object(self.page, "find_channel", return_value=None):
            await self.page.Bazaar.bazaar_command.callback(cog, ctx)
        self.assertEqual(ctx.send.await_args.kwargs["embed"].title, self.embeds.PANEL_TITLE)

    async def test_comando_ver(self):
        service = make_service(FakeApi([auction(1)]))
        cog = self.make_cog(service)
        ctx = MagicMock(defer=AsyncMock(), send=AsyncMock(), channel=MagicMock(id=9))
        await self.page.Bazaar.view_command.callback(cog, ctx, "1")
        self.assertIn("No hay ninguna búsqueda", ctx.send.await_args.args[0])
        service.last_search[9] = service.new_search(BazaarFilters(), "V")
        await self.page.Bazaar.view_command.callback(cog, ctx, "1")
        self.assertEqual(ctx.send.await_args.kwargs["embed"].title, "[01] Personaje 1")
        self.assertNotIn("ephemeral", ctx.send.await_args.kwargs)

    async def test_autocompletar_mundos(self):
        service = make_service(FakeApi(worlds={"Antica", "Secura"}))
        cog = self.make_cog(service)
        choices = await cog.world_autocomplete(MagicMock(), "sec")
        self.assertEqual([c.value for c in choices], ["Secura"])

    async def test_el_boton_del_panel_conserva_su_id_para_seguir_funcionando_tras_reiniciar(self):
        view = self.views.BazaarPanelView(self.make_cog(make_service()))
        self.assertEqual([c.custom_id for c in view.children], ["bazaar:search"])


if __name__ == "__main__":
    unittest.main()
