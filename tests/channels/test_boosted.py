"""Pruebas de #boosted-creature sin internet ni Discord."""
import io
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from PIL import Image

from src.channels.boosted.service import CreatureService

CREATURE = {"name": "Frost Dragon", "image_url": "http://api/creature.gif"}
INFO = {"hp": "1800", "exp": "2100", "location": "Svargrond", "abilities": ["Ice Wave: 200"], "weak": ["fuego"],
        "strong": [], "immune": [], "traits": [], "loot": {"Raro": ["Espada"]}}


class FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def png_bytes():
    out = io.BytesIO()
    Image.new("RGBA", (4, 4), (255, 0, 0, 255)).save(out, format="PNG")
    return out.getvalue()


class FakeWiki:
    def __init__(self, images=None):
        self.images = images if images is not None else [png_bytes()]
        self.calls = 0

    async def iter_boss_images(self, name, fallback_url=None):
        self.calls += 1
        for image in self.images:
            yield image


def make_service(creature=CREATURE, wiki=None, info=INFO):
    api = SimpleNamespace(fetch_boosted_creature=AsyncMock(return_value=creature))
    return CreatureService(api, wiki or FakeWiki(), AsyncMock(return_value=info), session_factory=FakeSession)


class CreatureServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_criatura_nueva_guarda_todo(self):
        service = make_service()
        self.assertTrue(await service.refresh())
        self.assertEqual(service.current_creature, CREATURE)
        self.assertEqual(service.image[1], "creature.png")
        self.assertEqual(service.info, INFO)

    async def test_misma_criatura_no_cuenta_como_cambio(self):
        wiki = FakeWiki()
        service = make_service(wiki=wiki)
        await service.refresh()
        self.assertFalse(await service.refresh())
        self.assertEqual(wiki.calls, 1)

    async def test_si_la_imagen_fallo_se_reintenta(self):
        wiki = FakeWiki(images=[])
        service = make_service(wiki=wiki)
        await service.refresh()
        self.assertIsNone(service.image)
        wiki.images = [png_bytes()]
        self.assertTrue(await service.refresh())
        self.assertIsNotNone(service.image)

    async def test_api_caida(self):
        service = make_service(creature=None)
        self.assertFalse(await service.refresh())
        self.assertIsNone(service.current_creature)

    async def test_ensure_info_solo_consulta_si_falta(self):
        service = make_service()
        await service.refresh()
        await service.ensure_info()
        service._fetch_info.assert_awaited_once()
        service.info = None
        await service.ensure_info()
        self.assertEqual(service._fetch_info.await_count, 2)


class CreatureInfoParserTests(unittest.TestCase):
    def test_lee_debilidades_de_la_wiki(self):
        # En la versión de la rama revision/creature-boosted esta función no existía y parse_infobox fallaba
        os.environ.setdefault("DISCORD_TOKEN", "test")  # config.py lo exige al importarse
        from src.shared.creature_info import parse_infobox

        info = parse_infobox("{{Infobox Creature\n| hp = 1800\n| exp = 2100\n| fireDmgMod = 110%\n| iceDmgMod = 0%\n}}")
        self.assertEqual(info["hp"], "1800")
        self.assertEqual(len(info["weak"]), 1)
        self.assertEqual(len(info["immune"]), 1)


class PageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        os.environ.setdefault("DISCORD_TOKEN", "test")  # config.py lo exige al importarse
        from src.channels.boosted import page
        self.page = page

    def interaction(self):
        return MagicMock(
            response=MagicMock(defer=AsyncMock(), send_message=AsyncMock()),
            followup=MagicMock(send=AsyncMock()),
        )

    async def ready_service(self, **kwargs):
        service = make_service(**kwargs)
        await service.refresh()
        return service

    async def test_tarjeta(self):
        embed = self.page.build_creature_card(CREATURE, "http://img", INFO)
        self.assertEqual(embed.author.name, self.page.CARD_MARKER)
        self.assertEqual(embed.fields[0].value, "1.800")
        self.assertEqual(embed.fields[2].value, "Svargrond")

    async def test_embeds_nombran_a_la_criatura(self):
        self.assertIn("esta criatura", self.page.build_loot_embed(CREATURE, None, None).description)
        self.assertEqual(self.page.build_abilities_embed(CREATURE, INFO, None).title, "Frost Dragon · Habilidades y debilidades")

    async def test_media_usa_el_adjunto_o_la_url_de_la_api(self):
        service = await self.ready_service()
        self.assertEqual(self.page.creature_media(service)[0], "attachment://creature.png")
        service.image = None
        self.assertEqual(self.page.creature_media(service), (CREATURE["image_url"], None))

    async def test_botones_sin_criatura_y_con_criatura(self):
        interaction = self.interaction()
        await self.page.CreatureCardView(make_service()).loot.callback(interaction)
        self.assertIn("Aún no se conoce", interaction.response.send_message.await_args.args[0])

        interaction = self.interaction()
        await self.page.CreatureCardView(await self.ready_service()).loot.callback(interaction)
        self.assertEqual(interaction.followup.send.await_args.kwargs["embed"].title, "Frost Dragon · Loot")

    async def test_los_botones_conservan_sus_ids_para_seguir_funcionando_tras_reiniciar(self):
        ids = {child.custom_id for child in self.page.CreatureCardView(make_service()).children}
        self.assertEqual(ids, self.page.CARD_BUTTON_IDS)

    def card_message(self, title, button_ids):
        return MagicMock(
            id=7,
            delete=AsyncMock(),
            embeds=[SimpleNamespace(title=title, image=SimpleNamespace(url="https://cdn.discordapp.com/x.png"))],
            components=[SimpleNamespace(children=[SimpleNamespace(custom_id=i) for i in button_ids])],
        )

    async def run_loop(self, service, message, last_message_id=7, channel_found=True):
        cog = self.page.CreatureBoostedTracker.__new__(self.page.CreatureBoostedTracker)  # Sin __init__: no arranca la tarea
        channel = MagicMock(send=AsyncMock(return_value=MagicMock(id=8)), last_message_id=last_message_id)
        cog.bot, cog.service, cog.card_message = MagicMock(guilds=[MagicMock(id=1)]), service, {}
        with patch.object(self.page, "find_channel", return_value=channel if channel_found else None), \
                patch.object(self.page, "_get_previous_message", new=AsyncMock(return_value=message)):
            await self.page.CreatureBoostedTracker.update_creature.coro(cog)
        return cog, channel

    async def test_loop_criatura_nueva_reemplaza_y_notifica(self):
        message = self.card_message("Vieja", self.page.CARD_BUTTON_IDS)
        cog, channel = await self.run_loop(make_service(), message)
        message.delete.assert_awaited_once()
        self.assertFalse(channel.send.await_args.kwargs["silent"])
        self.assertEqual(cog.card_message, {1: 8})

    async def test_loop_misma_tarjeta_al_final_no_hace_nada(self):
        service = await self.ready_service()
        _, channel = await self.run_loop(service, self.card_message("Frost Dragon", self.page.CARD_BUTTON_IDS))
        channel.send.assert_not_awaited()

    async def test_loop_sin_canal_no_falla_ni_afecta_a_otros_canales(self):
        _, channel = await self.run_loop(make_service(), None, channel_found=False)
        channel.send.assert_not_awaited()

    async def test_comando(self):
        cog = self.page.CreatureBoostedTracker.__new__(self.page.CreatureBoostedTracker)
        cog.service = make_service(creature=None)
        ctx = MagicMock(defer=AsyncMock(), send=AsyncMock())
        await self.page.CreatureBoostedTracker.boosted_command.callback(cog, ctx)
        self.assertIn("No se pudo consultar", ctx.send.await_args.args[0])

        cog.service = make_service()
        await self.page.CreatureBoostedTracker.boosted_command.callback(cog, ctx)
        self.assertEqual(ctx.send.await_args.kwargs["embed"].title, "Frost Dragon")
        self.assertIn("file", ctx.send.await_args.kwargs)


if __name__ == "__main__":
    unittest.main()
