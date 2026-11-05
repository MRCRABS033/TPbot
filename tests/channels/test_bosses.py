"""Pruebas de #bosses sin internet ni Discord."""
import io
import os
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from PIL import Image

from src.channels.bosses.service import LOCAL_TZ, BossService, parse_activity_time
from src.shared.pixel_art import IMAGE_SCALE, upscale_image

BOSS = {"name": "Dragon Lord", "image_url": "http://api/boss.gif"}
NOW = LOCAL_TZ.localize(datetime(2026, 11, 4, 15, 0))


class FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def png_bytes(size=(4, 4)):
    out = io.BytesIO()
    Image.new("RGBA", size, (255, 0, 0, 255)).save(out, format="PNG")
    return out.getvalue()


def gif_bytes():
    out = io.BytesIO()
    frames = [Image.new("RGB", (4, 4), color) for color in ((255, 0, 0), (0, 0, 255))]
    frames[0].save(out, format="GIF", save_all=True, append_images=frames[1:], duration=50)
    return out.getvalue()


class FakeWiki:
    """`images` = imágenes que entrega en orden."""

    def __init__(self, images=None):
        self.images = images if images is not None else [png_bytes()]
        self.calls = 0

    async def iter_boss_images(self, name, fallback_url=None):
        self.calls += 1
        for image in self.images:
            yield image


def player(name, owner="Vale"):
    return {"char_name": name, "owner": owner, "current_level": 100, "vocation": "Knight"}


def make_service(boss=BOSS, wiki=None, info=None, players=None):
    api = SimpleNamespace(fetch_boosted_boss=AsyncMock(return_value=boss))
    fetch_info = AsyncMock(return_value=info if info is not None else {"hp": "28000"})
    players = players if players is not None else {"ana": player("Ana")}
    return BossService(api, wiki or FakeWiki(), fetch_info, players, session_factory=FakeSession)


class ActivityTimeTests(unittest.TestCase):
    def test_formatos_validos(self):
        for text, expected in (("9:30 pm", (21, 30)), ("9pm", (21, 0)), ("21:30", (21, 30)), ("21", (21, 0)),
                               ("12am", (0, 0)), ("12 p.m.", (12, 0))):
            when = parse_activity_time(text, now=NOW)
            self.assertEqual((when.hour, when.minute), expected, text)

    def test_formatos_invalidos(self):
        for text in ("mañana", "25", "13pm", "9:75", ""):
            self.assertIsNone(parse_activity_time(text, now=NOW), text)

    def test_hora_que_ya_paso_es_manana(self):
        self.assertEqual(parse_activity_time("9am", now=NOW).day, 5)
        self.assertEqual(parse_activity_time("16:00", now=NOW).day, 4)


class ImageTests(unittest.TestCase):
    def test_png_se_agranda(self):
        data, filename = upscale_image(png_bytes(), "boss")
        self.assertEqual(filename, "boss.png")
        self.assertEqual(Image.open(io.BytesIO(data)).size, (4 * IMAGE_SCALE, 4 * IMAGE_SCALE))

    def test_gif_conserva_la_animacion(self):
        data, filename = upscale_image(gif_bytes(), "boss")
        image = Image.open(io.BytesIO(data))
        self.assertEqual(filename, "boss.gif")
        self.assertEqual(image.n_frames, 2)
        self.assertEqual(image.size, (4 * IMAGE_SCALE, 4 * IMAGE_SCALE))


class RefreshTests(unittest.IsolatedAsyncioTestCase):
    async def test_boss_nuevo_guarda_todo(self):
        service = make_service()
        self.assertTrue(await service.refresh())
        self.assertEqual(service.current_boss, BOSS)
        self.assertEqual(service.image[1], "boss.png")
        self.assertEqual(service.info, {"hp": "28000"})

    async def test_mismo_boss_no_cuenta_como_cambio_ni_descarga(self):
        wiki = FakeWiki()
        service = make_service(wiki=wiki)
        await service.refresh()
        self.assertFalse(await service.refresh())
        self.assertEqual(wiki.calls, 1)

    async def test_si_la_imagen_fallo_se_reintenta_y_avisa_al_conseguirla(self):
        wiki = FakeWiki(images=[])
        service = make_service(wiki=wiki)
        self.assertTrue(await service.refresh())
        self.assertIsNone(service.image)
        self.assertFalse(await service.refresh())
        wiki.images = [png_bytes()]
        self.assertTrue(await service.refresh())
        self.assertIsNotNone(service.image)

    async def test_imagen_danada_prueba_la_siguiente(self):
        service = make_service(wiki=FakeWiki(images=[b"no es imagen", png_bytes()]))
        await service.refresh()
        self.assertEqual(service.image[1], "boss.png")

    async def test_api_caida_conserva_el_boss_anterior(self):
        service = make_service()
        await service.refresh()
        service._api.fetch_boosted_boss.return_value = None
        self.assertFalse(await service.refresh())
        self.assertEqual(service.current_boss, BOSS)

    async def test_ensure_info_solo_consulta_si_falta(self):
        service = make_service()
        await service.refresh()
        await service.ensure_info()
        service._fetch_info.assert_awaited_once()
        service.info = None
        await service.ensure_info()
        self.assertEqual(service._fetch_info.await_count, 2)

    def test_participantes_y_orden(self):
        players = {"b": player("Beto", "luis"), "a": player("Ana", "Vale"), "c": player("Caro", "Luis")}
        service = make_service(players=players)
        self.assertEqual([k for k, _ in service.sorted_players()], ["b", "c", "a"])
        self.assertEqual([d["char_name"] for d in service.participants(["a", "ya_no_esta"])], ["Ana"])


class PageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        os.environ.setdefault("DISCORD_TOKEN", "test")  # config.py lo exige al importarse
        from src.channels.bosses import embeds, page
        self.page, self.embeds = page, embeds

    def interaction(self):
        return MagicMock(
            response=MagicMock(defer=AsyncMock(), send_message=AsyncMock(), send_modal=AsyncMock()),
            followup=MagicMock(send=AsyncMock()),
        )

    async def ready_service(self, **kwargs):
        service = make_service(**kwargs)
        await service.refresh()
        return service

    async def test_media_usa_el_adjunto_o_la_url_de_la_api(self):
        service = await self.ready_service()
        url, file = self.embeds.boss_media(service)
        self.assertEqual(url, "attachment://boss.png")
        self.assertIsNotNone(file)
        service.image = None
        self.assertEqual(self.embeds.boss_media(service), (BOSS["image_url"], None))

    async def test_embeds_de_informacion(self):
        info = {"abilities": ["x" * 600, "y" * 600], "weak": ["fuego"], "strong": [], "immune": [], "traits": [],
                "loot": {"Raro": ["Espada", "Escudo"]}}
        abilities = self.embeds.build_abilities_embed(BOSS, info, "http://img")
        self.assertEqual(abilities.fields[0].name, "Habilidades")
        self.assertEqual(abilities.fields[1].name, "​")  # Se reparte en dos campos
        self.assertEqual(abilities.fields[2].value, "fuego")
        loot = self.embeds.build_loot_embed(BOSS, info, None)
        self.assertEqual(loot.description, "2 objetos posibles.")
        self.assertEqual(loot.fields[0].value, "Espada, Escudo")
        self.assertIn("No se encontró", self.embeds.build_loot_embed(BOSS, None, None).description)

    async def test_embed_de_actividad(self):
        participants = [player(f"P{i}") for i in range(30)]
        embed = self.embeds.build_activity_embed(BOSS, "hora rara", "Llevar supplies", participants,
                                                 MagicMock(mention="@Vale"), None, {"location": "Thais"})
        self.assertEqual(embed.fields[0].value, "hora rara")
        self.assertEqual(embed.fields[3].value, "Thais")
        self.assertEqual(embed.fields[4].name, "Participantes · 30")
        self.assertEqual(embed.fields[-1].value, "Llevar supplies")
        self.assertTrue(all(len(f.value) <= 1024 for f in embed.fields))

    async def test_boton_grupo_sin_boss_o_sin_personajes(self):
        view = self.page.BossCardView(make_service())
        interaction = self.interaction()
        await view.group.callback(interaction)
        self.assertIn("Aún no se conoce", interaction.response.send_message.await_args.args[0])

        view = self.page.BossCardView(await self.ready_service(players={}))
        interaction = self.interaction()
        await view.group.callback(interaction)
        self.assertIn("no tiene personajes", interaction.response.send_message.await_args.args[0])

    async def test_flujo_completo_de_actividad(self):
        service = await self.ready_service()
        interaction = self.interaction()
        await self.page.BossCardView(service).group.callback(interaction)
        activity_view = interaction.response.send_message.await_args.kwargs["view"]
        self.assertEqual(activity_view.selects[0].options[0].value, "ana")

        no_selection = self.interaction()
        await activity_view.create.callback(no_selection)
        self.assertIn("al menos un participante", no_selection.response.send_message.await_args.args[0])

        activity_view.selects[0]._values = ["ana"]
        activity_view.selected_keys = lambda: ["ana"]
        modal = self.page.ActivityModal(activity_view)
        modal.time._value, modal.note._value = "9pm", "Nota"
        activity_view.origin = MagicMock(edit_original_response=AsyncMock())
        channel = MagicMock(send=AsyncMock(), mention="#anuncios")
        submit = self.interaction()
        with patch.object(self.page, "find_channel", return_value=channel):
            await modal.on_submit(submit)
        embed = channel.send.await_args.kwargs["embed"]
        self.assertEqual(embed.title, "Actividad en grupo: Dragon Lord")
        self.assertIn("Ana", str(embed.to_dict()))
        self.assertIn("file", channel.send.await_args.kwargs)
        activity_view.origin.edit_original_response.assert_awaited_once()

    async def test_actividad_sin_canal_avisa(self):
        service = await self.ready_service()
        view = self.page.GroupActivityView(service, BOSS, self.interaction())
        modal = self.page.ActivityModal(view)
        modal.time._value, modal.note._value = "9pm", ""
        submit = self.interaction()
        with patch.object(self.page, "find_channel", return_value=None):
            await modal.on_submit(submit)
        self.assertIn("No se encontró el canal", submit.response.send_message.await_args.args[0])

    async def test_botones_de_informacion_reintentan_si_falta(self):
        service = await self.ready_service()
        service.info = None
        interaction = self.interaction()
        await self.page.BossCardView(service).loot.callback(interaction)
        self.assertEqual(service._fetch_info.await_count, 2)
        self.assertEqual(interaction.followup.send.await_args.kwargs["embed"].title, "Dragon Lord · Loot")

    async def test_los_botones_conservan_sus_ids_para_seguir_funcionando_tras_reiniciar(self):
        ids = {child.custom_id for child in self.page.BossCardView(make_service()).children}
        self.assertEqual(ids, self.page.CARD_BUTTON_IDS)

    def card_message(self, title, button_ids, image="https://cdn.discordapp.com/x.png", message_id=7):
        return MagicMock(
            id=message_id,
            delete=AsyncMock(),
            embeds=[SimpleNamespace(title=title, image=SimpleNamespace(url=image))],
            components=[SimpleNamespace(children=[SimpleNamespace(custom_id=i) for i in button_ids])],
        )

    async def run_loop(self, service, message, last_message_id=7):
        cog = self.page.BossTracker.__new__(self.page.BossTracker)  # Sin __init__: no arranca la tarea
        channel = MagicMock(send=AsyncMock(return_value=MagicMock(id=8)), last_message_id=last_message_id)
        cog.bot, cog.service, cog.card_message = MagicMock(guilds=[MagicMock(id=1)]), service, {}
        with patch.object(self.page, "find_channel", return_value=channel), \
                patch.object(self.page, "_get_previous_message", new=AsyncMock(return_value=message)):
            await self.page.BossTracker.update_boss_card.coro(cog)
        return cog, channel

    async def test_loop_boss_nuevo_reemplaza_y_notifica(self):
        message = self.card_message("Viejo", self.page.CARD_BUTTON_IDS)
        cog, channel = await self.run_loop(make_service(), message)
        message.delete.assert_awaited_once()
        self.assertFalse(channel.send.await_args.kwargs["silent"])
        self.assertEqual(cog.card_message, {1: 8})

    async def test_loop_misma_tarjeta_al_final_no_hace_nada(self):
        service = await self.ready_service()
        message = self.card_message("Dragon Lord", self.page.CARD_BUTTON_IDS)
        _, channel = await self.run_loop(service, message)
        channel.send.assert_not_awaited()

    async def test_loop_tarjeta_no_al_final_se_republica_en_silencio(self):
        service = await self.ready_service()
        message = self.card_message("Dragon Lord", self.page.CARD_BUTTON_IDS)
        _, channel = await self.run_loop(service, message, last_message_id=99)
        self.assertTrue(channel.send.await_args.kwargs["silent"])

    async def test_loop_tarjeta_de_version_anterior_se_reemplaza(self):
        service = await self.ready_service()
        message = self.card_message("Dragon Lord", {"boss:group"})
        _, channel = await self.run_loop(service, message)
        channel.send.assert_awaited_once()

    async def test_comando_boss_sin_datos(self):
        cog = self.page.BossTracker.__new__(self.page.BossTracker)
        cog.service = make_service(boss=None)
        ctx = MagicMock(defer=AsyncMock(), send=AsyncMock())
        await self.page.BossTracker.boss_command.callback(cog, ctx)
        self.assertIn("No se pudo consultar", ctx.send.await_args.args[0])


if __name__ == "__main__":
    unittest.main()
