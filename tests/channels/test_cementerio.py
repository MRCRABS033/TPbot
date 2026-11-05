"""Pruebas de #cementerio sin internet ni Discord."""
import os
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from src.channels.cementerio.service import LOCAL_TZ, MAX_PLAYER_LOOKUPS, DeathService
from src.shared.death_format import format_death_time, format_killers
from tests.fakes import GUILD_ID, FakeCharacterRepository, FakeGuildRepository, FakeSession

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=LOCAL_TZ)
TODAY = "2026-11-04T15:00:00Z"
LATER_TODAY = "2026-11-04T16:00:00Z"
YESTERDAY = "2026-11-03T15:00:00Z"
# 02:00 UTC del día 4 todavía es el día 3 en Colombia
LATE_NIGHT_YESTERDAY = "2026-11-04T02:00:00Z"


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


def player(name="Ana", owner="Vale", **extra):
    return {"char_name": name, "owner": owner, "tibia_link": f"[{name}](http://x)", **extra}


def make_service(responses, players=None, other_guilds=None):
    players = players if players is not None else {"ana": player()}
    repository = FakeCharacterRepository(players, other_guilds)
    service = DeathService(FakeApi(responses), FakeWiki(), repository, session_factory=FakeSession,
                           now=lambda: NOW, pause_seconds=0)
    return service, players


class CheckPlayersTests(unittest.IsolatedAsyncioTestCase):
    async def test_primer_registro_no_avisa_muertes_viejas(self):
        service, players = make_service({"Ana": response([{"time": TODAY}])})
        on_death = AsyncMock()
        await service.check_players(GUILD_ID, on_death)
        on_death.assert_not_awaited()
        self.assertEqual(players["ana"]["last_death_time"], TODAY)

    async def test_primer_registro_sin_muertes_no_avisa_despues(self):
        service, players = make_service({"Ana": response()})
        await service.check_players(GUILD_ID, AsyncMock())
        self.assertIsNone(players["ana"]["last_death_time"])

    async def test_muerte_nueva_avisa_una_sola_vez(self):
        service, players = make_service({"Ana": response([{"time": TODAY}])}, {"ana": player(last_death_time=YESTERDAY)})
        on_death = AsyncMock()
        await service.check_players(GUILD_ID, on_death)
        await service.check_players(GUILD_ID, on_death)
        on_death.assert_awaited_once()
        self.assertEqual(players["ana"]["last_death_time"], TODAY)

    async def test_la_primera_muerte_despues_de_registrarse_avisa(self):
        service, _ = make_service({"Ana": response([{"time": TODAY}])}, {"ana": player(last_death_time=None)})
        on_death = AsyncMock()
        await service.check_players(GUILD_ID, on_death)
        on_death.assert_awaited_once()

    async def test_si_el_aviso_falla_la_muerte_se_reintenta(self):
        service, players = make_service({"Ana": response([{"time": TODAY}])}, {"ana": player(last_death_time=YESTERDAY)})
        with self.assertRaises(RuntimeError):
            await service.check_players(GUILD_ID, AsyncMock(side_effect=RuntimeError("Discord caído")))
        self.assertEqual(players["ana"]["last_death_time"], YESTERDAY)

        on_death = AsyncMock()
        await service.check_players(GUILD_ID, on_death)
        on_death.assert_awaited_once()

    async def test_sin_respuesta_de_la_api_no_cambia_nada(self):
        service, players = make_service({}, {"ana": player(last_death_time=YESTERDAY)})
        await service.check_players(GUILD_ID, AsyncMock())
        self.assertEqual(players["ana"]["last_death_time"], YESTERDAY)

    async def test_sin_personajes_no_consulta_la_api(self):
        service, _ = make_service({}, {})
        await service.check_players(GUILD_ID, AsyncMock())
        self.assertEqual(service._api.queries, [])

    async def test_cada_servidor_lleva_su_propia_ultima_muerte(self):
        other = {"ana": player(last_death_time=TODAY)}
        service, players = make_service({"Ana": response([{"time": TODAY}])}, {"ana": player(last_death_time=YESTERDAY)}, {2: other})
        on_death = AsyncMock()
        await service.check_players(2, on_death)  # El servidor 2 ya la conocía
        on_death.assert_not_awaited()
        self.assertEqual(players["ana"]["last_death_time"], YESTERDAY)
        await service.check_players(GUILD_ID, on_death)  # El 1 todavía no
        on_death.assert_awaited_once()


class DeathsTodayTests(unittest.IsolatedAsyncioTestCase):
    async def test_solo_hoy_en_hora_colombia_y_mas_reciente_primero(self):
        deaths = [{"time": t} for t in (TODAY, LATER_TODAY, YESTERDAY, LATE_NIGHT_YESTERDAY, "fecha-mala")]
        service, _ = make_service({"Ana": response(deaths)})
        result = await service.deaths_today(GUILD_ID)
        self.assertEqual([e[2]["time"] for e in result.entries], [LATER_TODAY, TODAY])
        self.assertEqual((result.checked, result.failed), (1, 0))

    async def test_cuenta_las_consultas_fallidas(self):
        service, _ = make_service({"Ana": response()}, {"a": player(), "b": player("Beto")})
        result = await service.deaths_today(GUILD_ID)
        self.assertEqual((result.checked, result.failed), (2, 1))
        self.assertEqual(list(result.details), ["Ana"])

    async def test_solo_revisa_el_grupo_de_ese_servidor(self):
        service, _ = make_service({"Ana": response(), "Beto": response()}, {"a": player()}, {2: {"b": player("Beto")}})
        await service.deaths_today(2)
        self.assertEqual(service._api.queries, ["Beto"])


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
    def make_cog(self, service, worlds=None):
        os.environ.setdefault("DISCORD_TOKEN", "test")  # config.py lo exige al importarse
        from src.channels.cementerio import page

        # Sin __init__: así no arranca la tarea automática real
        cog = page.DeathTracker.__new__(page.DeathTracker)
        cog.bot, cog.service, cog.guilds = MagicMock(), service, FakeGuildRepository(worlds)
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

    async def test_el_aviso_solo_llega_al_servidor_del_personaje(self):
        death = {"time": TODAY, "level": 80, "killers": [{"name": "a dragon"}], "assists": []}
        service, players = make_service({"Ana": response([death])}, {"ana": player(last_death_time=YESTERDAY)}, {2: {}})
        cog, page = self.make_cog(service, {GUILD_ID: "Quidera", 2: "Antica", 3: None})
        channels = {guild_id: MagicMock(send=AsyncMock()) for guild_id in (GUILD_ID, 2)}
        cog.bot.guilds = [MagicMock(id=guild_id, text_channels=channels[guild_id]) for guild_id in channels]
        with patch.object(page, "find_channel", side_effect=lambda found, name: found):
            await page.DeathTracker.track_tibia_players.coro(cog)
        channels[GUILD_ID].send.assert_awaited_once()
        self.assertEqual(channels[GUILD_ID].send.await_args.kwargs["embed"].title, "Ana murió a nivel 80")
        channels[2].send.assert_not_awaited()
        self.assertEqual(players["ana"]["last_death_time"], TODAY)

    async def test_comando_muertes_sin_muertes(self):
        service, _ = make_service({"Ana": response()})
        cog, page = self.make_cog(service)
        ctx = MagicMock(defer=AsyncMock(), send=AsyncMock(), guild=MagicMock(id=GUILD_ID))
        await page.DeathTracker.deaths_today.callback(cog, ctx)
        ctx.send.assert_awaited_once()
        self.assertEqual(ctx.send.await_args.kwargs["embed"].description, "Nadie del grupo ha muerto hoy.")

    async def test_comando_muertes_con_muerte(self):
        death = {"time": TODAY, "level": 80, "killers": [{"name": "a dragon"}], "assists": []}
        service, _ = make_service({"Ana": response([death])})
        cog, page = self.make_cog(service)
        ctx = MagicMock(defer=AsyncMock(), send=AsyncMock(), guild=MagicMock(id=GUILD_ID))
        await page.DeathTracker.deaths_today.callback(cog, ctx)
        self.assertEqual(ctx.send.await_count, 2)  # Encabezado + aviso
        self.assertEqual(ctx.send.await_args_list[0].kwargs["embed"].title, "Muertes de hoy · 1")

    async def test_comando_muertes_deja_la_mas_reciente_de_ultima(self):
        deaths = [{"time": t, "level": n, "killers": [], "assists": []} for n, t in ((1, TODAY), (2, LATER_TODAY))]
        service, _ = make_service({"Ana": response(deaths)})
        cog, page = self.make_cog(service)
        ctx = MagicMock(defer=AsyncMock(), send=AsyncMock(), guild=MagicMock(id=GUILD_ID))
        await page.DeathTracker.deaths_today.callback(cog, ctx)
        titles = [call.kwargs["embed"].title for call in ctx.send.await_args_list[1:]]
        self.assertEqual(titles, ["Ana murió a nivel 1", "Ana murió a nivel 2"])
        self.assertEqual(ctx.send.await_args_list[0].kwargs["embed"].description, "De la más antigua a la más reciente.")

    async def test_comando_con_muchas_muertes_muestra_las_mas_recientes(self):
        deaths = [{"time": f"2026-11-04T15:{n:02d}:00Z", "level": n, "killers": [], "assists": []} for n in range(12)]
        service, _ = make_service({"Ana": response(deaths)})
        cog, page = self.make_cog(service)
        ctx = MagicMock(defer=AsyncMock(), send=AsyncMock(), guild=MagicMock(id=GUILD_ID))
        await page.DeathTracker.deaths_today.callback(cog, ctx)
        titles = [call.kwargs["embed"].title for call in ctx.send.await_args_list[1:]]
        self.assertEqual(titles, [f"Ana murió a nivel {n}" for n in range(2, 12)])

    async def test_comando_sin_configurar_pide_config(self):
        service, _ = make_service({})
        cog, page = self.make_cog(service, {})
        ctx = MagicMock(defer=AsyncMock(), send=AsyncMock(), guild=MagicMock(id=GUILD_ID))
        await page.DeathTracker.deaths_today.callback(cog, ctx)
        self.assertIn("/config", ctx.send.await_args.args[0])
        self.assertEqual(service._api.queries, [])


if __name__ == "__main__":
    unittest.main()
