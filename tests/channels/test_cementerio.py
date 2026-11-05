"""Pruebas de #cementerio sin internet ni Discord."""
import os
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

from src.channels.cementerio.service import LOCAL_TZ, MAX_PLAYER_LOOKUPS, DeathService
from src.shared.death_format import format_death_time, format_killers

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=LOCAL_TZ)
TODAY = "2026-11-04T15:00:00Z"
LATER_TODAY = "2026-11-04T16:00:00Z"
YESTERDAY = "2026-11-03T15:00:00Z"
# 02:00 UTC del día 4 todavía es el día 3 en Colombia
LATE_NIGHT_YESTERDAY = "2026-11-04T02:00:00Z"


class FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeApi:
    """`responses` = { nombre: respuesta de fetch_player_data } (sin clave = la API falló)."""

    def __init__(self, responses):
        self.responses = responses
        self.queries = []

    async def fetch_player_data(self, session, name):
        self.queries.append(name)
        return self.responses.get(name)


class FakeWiki:
    def vocation_file_name(self, vocation):
        return f"voc:{vocation}"

    def creature_file_name(self, name):
        return f"creature:{name}"

    async def fetch_wiki_image(self, session, file_name):
        return file_name.encode()


def response(deaths=(), level=100):
    return {"character_info": {"level": level, "world": "Quidera"}, "deaths": list(deaths), "tibia_link": "[Ana](http://x)"}


def make_service(responses, players=None, state=None):
    players = players if players is not None else {"ana": {"char_name": "Ana", "owner": "Vale", "tibia_link": "[Ana](http://x)"}}
    state = state if state is not None else {}
    return DeathService(FakeApi(responses), FakeWiki(), players, state, session_factory=FakeSession,
                        now=lambda: NOW, pause_seconds=0), state


class CheckPlayersTests(unittest.IsolatedAsyncioTestCase):
    async def test_primer_registro_no_avisa_muertes_viejas(self):
        service, state = make_service({"Ana": response([{"time": TODAY}], level=50)})
        on_death = AsyncMock()
        await service.check_players(on_death)
        on_death.assert_not_awaited()
        self.assertEqual(state["ana"], {"level": 50, "last_death_time": TODAY})

    async def test_muerte_nueva_avisa_una_sola_vez(self):
        state = {"ana": {"level": 90, "last_death_time": YESTERDAY}}
        service, state = make_service({"Ana": response([{"time": TODAY}])}, state=state)
        on_death = AsyncMock()
        await service.check_players(on_death)
        await service.check_players(on_death)
        on_death.assert_awaited_once()
        self.assertEqual(state["ana"], {"level": 100, "last_death_time": TODAY})

    async def test_si_el_aviso_falla_la_muerte_se_reintenta(self):
        state = {"ana": {"level": 90, "last_death_time": YESTERDAY}}
        service, state = make_service({"Ana": response([{"time": TODAY}])}, state=state)
        with self.assertRaises(RuntimeError):
            await service.check_players(AsyncMock(side_effect=RuntimeError("Discord caído")))
        self.assertEqual(state["ana"]["last_death_time"], YESTERDAY)

        on_death = AsyncMock()
        await service.check_players(on_death)
        on_death.assert_awaited_once()

    async def test_sin_respuesta_de_la_api_no_cambia_el_estado(self):
        original = {"level": 90, "last_death_time": YESTERDAY}
        service, state = make_service({}, state={"ana": dict(original)})
        await service.check_players(AsyncMock())
        self.assertEqual(state["ana"], original)

    async def test_sin_personajes_no_consulta_la_api(self):
        service, _ = make_service({}, players={})
        await service.check_players(AsyncMock())
        self.assertEqual(service._api.queries, [])


class DeathsTodayTests(unittest.IsolatedAsyncioTestCase):
    async def test_solo_hoy_en_hora_colombia_y_mas_reciente_primero(self):
        deaths = [{"time": t} for t in (TODAY, LATER_TODAY, YESTERDAY, LATE_NIGHT_YESTERDAY, "fecha-mala")]
        service, _ = make_service({"Ana": response(deaths)})
        result = await service.deaths_today()
        self.assertEqual([e[2]["time"] for e in result.entries], [LATER_TODAY, TODAY])
        self.assertEqual((result.checked, result.failed), (1, 0))

    async def test_cuenta_las_consultas_fallidas(self):
        players = {"a": {"char_name": "Ana"}, "b": {"char_name": "Beto"}}
        service, _ = make_service({"Ana": response()}, players=players)
        result = await service.deaths_today()
        self.assertEqual((result.checked, result.failed), (2, 1))
        self.assertEqual(list(result.details), ["Ana"])


class ParticipantsTests(unittest.IsolatedAsyncioTestCase):
    async def test_consulta_solo_jugadores_sin_repetir_y_con_limite(self):
        people = [{"name": f"P{i}", "player": True} for i in range(MAX_PLAYER_LOOKUPS + 3)]
        people += [dict(people[0]), {"name": "Dragon", "player": False}]
        service, _ = make_service({f"P{i}": response() for i in range(30)})
        infos = await service.fetch_players_info(people)
        self.assertEqual(len(infos), MAX_PLAYER_LOOKUPS)
        self.assertEqual(len(service._api.queries), MAX_PLAYER_LOOKUPS)
        self.assertNotIn("Dragon", service._api.queries)

    async def test_imagen_de_jugador_monstruo_y_sin_asesino(self):
        service, _ = make_service({})
        infos = {"Beto": {"vocation": "Knight"}}
        self.assertEqual(await service.killer_image({"name": "Beto", "player": True}, infos), b"voc:Knight")
        self.assertEqual(await service.killer_image({"name": "dragon"}, {}), b"creature:dragon")
        self.assertIsNone(await service.killer_image(None, {}))


class DeathFormatTests(unittest.TestCase):
    def test_killers_jugador_monstruo_e_invocacion(self):
        text = format_killers(
            [{"name": "Beto Bad", "player": True}, {"name": "a dragon", "summon": "a demon"}],
            {"Beto Bad": {"vocation": "Knight", "level": 300}},
        )
        self.assertIn("name=Beto+Bad", text)
        self.assertIn("Knight · Nivel 300", text)
        self.assertIn("A Dragon (invocación: a demon)", text)

    def test_killers_respeta_el_limite_de_discord(self):
        text = format_killers([{"name": "x" * 400} for _ in range(10)], {})
        self.assertLessEqual(len(text), 1024)
        self.assertTrue(text.endswith("…"))

    def test_hora(self):
        self.assertTrue(format_death_time(TODAY).startswith("<t:"))
        self.assertEqual(format_death_time("rara"), "rara")
        self.assertEqual(format_death_time(None), "N/A")


class PageTests(unittest.IsolatedAsyncioTestCase):
    def make_cog(self, service):
        os.environ.setdefault("DISCORD_TOKEN", "test")  # config.py lo exige al importarse
        from src.channels.cementerio import page

        # Sin __init__: así no arranca la tarea automática real
        cog = page.DeathTracker.__new__(page.DeathTracker)
        cog.bot, cog.service = MagicMock(), service
        return cog, page

    async def test_aviso_pvp(self):
        service, _ = make_service({"Beto": response(level=300)})
        cog, _ = self.make_cog(service)
        death = {"time": TODAY, "level": 80, "killers": [{"name": "Beto", "player": True}], "assists": []}
        embed, thumbnail = await cog._build_death_message("[Ana](http://x)", "Vale", 100, {"world": "Quidera"}, death)
        self.assertEqual(embed.title, "Ana murió en PvP a nivel 80")
        self.assertEqual(embed.fields[0].value, "Vale")
        self.assertIn("Beto", embed.fields[2].value)
        self.assertIsNotNone(thumbnail)

    async def test_aviso_sin_asesinos_muestra_la_causa(self):
        service, _ = make_service({})
        cog, _ = self.make_cog(service)
        death = {"time": TODAY, "killers": [], "reason": "Se cayó"}
        embed, thumbnail = await cog._build_death_message("[Ana](http://x)", "Vale", 100, {}, death)
        self.assertEqual(embed.title, "Ana murió a nivel 100")
        self.assertEqual(embed.fields[2].name, "Causa")
        self.assertIsNone(thumbnail)

    async def test_el_loop_publica_la_muerte_detectada(self):
        state = {"ana": {"level": 90, "last_death_time": YESTERDAY}}
        death = {"time": TODAY, "level": 80, "killers": [{"name": "a dragon"}], "assists": []}
        service, state = make_service({"Ana": response([death])}, state=state)
        cog, page = self.make_cog(service)
        channel = MagicMock(send=AsyncMock())
        guild = MagicMock(text_channels=[channel])
        cog.bot.guilds = [guild]
        original = page.find_channel
        page.find_channel = lambda channels, name: channel if name == page.CHANNEL_DEATHS else None
        try:
            await page.DeathTracker.track_tibia_players.coro(cog)
        finally:
            page.find_channel = original
        channel.send.assert_awaited_once()
        self.assertEqual(channel.send.await_args.kwargs["embed"].title, "Ana murió a nivel 80")
        self.assertEqual(state["ana"]["last_death_time"], TODAY)

    async def test_comando_muertes_sin_muertes(self):
        service, _ = make_service({"Ana": response()})
        cog, page = self.make_cog(service)
        ctx = MagicMock(defer=AsyncMock(), send=AsyncMock())
        await page.DeathTracker.deaths_today.callback(cog, ctx)
        ctx.send.assert_awaited_once()
        self.assertEqual(ctx.send.await_args.kwargs["embed"].description, "Nadie del grupo ha muerto hoy.")

    async def test_comando_muertes_con_muerte(self):
        death = {"time": TODAY, "level": 80, "killers": [{"name": "a dragon"}], "assists": []}
        service, _ = make_service({"Ana": response([death])})
        cog, page = self.make_cog(service)
        ctx = MagicMock(defer=AsyncMock(), send=AsyncMock())
        await page.DeathTracker.deaths_today.callback(cog, ctx)
        self.assertEqual(ctx.send.await_count, 2)  # Encabezado + aviso
        self.assertEqual(ctx.send.await_args_list[0].kwargs["embed"].title, "Muertes de hoy · 1")


if __name__ == "__main__":
    unittest.main()
