"""Pruebas de #onlines y #level-up sin internet ni Discord."""
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from src.channels.onlines.service import OnlineService


class FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeApi:
    def __init__(self, online=(), characters=None):
        self.online = list(online)
        self.characters = characters or {}
        self.queries = []

    async def fetch_world_online_players(self, session, world):
        self.world = world
        return self.online

    async def fetch_player_data(self, session, name):
        self.queries.append(name)
        return self.characters.get(name)


class FakeLogins:
    def __init__(self, needs_api_times=False):
        self.needs_api_times = needs_api_times
        self.updates = []

    def update(self, keys, last_logins=None):
        self.updates.append((set(keys), last_logins))

    def text(self, key):
        return f"desde {key}"


def online_player(name, level=100, vocation="Knight"):
    return {"name": name, "level": level, "vocation": vocation}


def watched(name="Ana", level=100, **extra):
    data = {"char_name": name, "owner": "Vale", **extra}
    if level is not None:
        data["current_level"] = level
    return data


def make_service(api, players=None, logins=None):
    save = MagicMock()
    players = players if players is not None else {"ana": watched()}
    service = OnlineService(api, players, save, logins or FakeLogins(), parse_time=lambda v: v, session_factory=FakeSession)
    return service, players, save


class CheckOnlineTests(unittest.IsolatedAsyncioTestCase):
    async def test_devuelve_solo_monitoreados_conectados_sin_importar_mayusculas(self):
        api = FakeApi([online_player("ANA"), online_player("Otro")])
        service, _, _ = make_service(api, {"ana": watched(), "beto": watched("Beto")})
        online = await service.check_online("Quidera", AsyncMock())
        self.assertEqual([p.key for p in online], ["ana"])
        self.assertEqual(online[0].name, "Ana")
        self.assertEqual(api.world, "Quidera")

    async def test_primer_nivel_se_guarda_sin_avisar(self):
        service, players, save = make_service(FakeApi([online_player("Ana", 50)]), {"ana": watched(level=None)})
        notify = AsyncMock()
        await service.check_online("Quidera", notify)
        notify.assert_not_awaited()
        self.assertEqual(players["ana"]["current_level"], 50)
        save.assert_called_once()

    async def test_cambio_de_nivel_guarda_y_avisa_una_vez(self):
        service, players, save = make_service(FakeApi([online_player("Ana", 101)]))
        notify = AsyncMock()
        await service.check_online("Quidera", notify)
        await service.check_online("Quidera", notify)
        notify.assert_awaited_once_with(players["ana"], 100, 101, "Knight")
        save.assert_called_once()

    async def test_bajar_de_nivel_tambien_avisa(self):
        service, players, _ = make_service(FakeApi([online_player("Ana", 95)]))
        notify = AsyncMock()
        await service.check_online("Quidera", notify)
        notify.assert_awaited_once_with(players["ana"], 100, 95, "Knight")

    async def test_sin_cambio_no_guarda(self):
        service, _, save = make_service(FakeApi([online_player("Ana", 100)]))
        await service.check_online("Quidera", AsyncMock())
        save.assert_not_called()

    async def test_lista_vacia_de_la_api_no_toca_las_horas_de_ingreso(self):
        logins = FakeLogins()
        service, _, _ = make_service(FakeApi([]), logins=logins)
        self.assertEqual(await service.check_online("Quidera", AsyncMock()), [])
        self.assertEqual(logins.updates, [])

    async def test_primera_revision_pide_last_login_a_la_api(self):
        logins = FakeLogins(needs_api_times=True)
        api = FakeApi([online_player("Ana")], {"Ana": {"character_info": {"last_login": "ayer"}}})
        service, _, _ = make_service(api, logins=logins)
        await service.check_online("Quidera", AsyncMock())
        self.assertEqual(logins.updates, [({"ana"}, {"ana": "ayer"})])

    async def test_revisiones_siguientes_no_consultan_personajes(self):
        logins = FakeLogins()
        api = FakeApi([online_player("Ana")])
        service, _, _ = make_service(api, logins=logins)
        await service.check_online("Quidera", AsyncMock())
        self.assertEqual(api.queries, [])
        self.assertEqual(logins.updates, [({"ana"}, None)])


class CheckCharacterTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_monitoreado(self):
        service, _, _ = make_service(FakeApi())
        self.assertIsNone((await service.check_character("Nadie")).info)

    async def test_busca_sin_mayusculas_ni_espacios(self):
        service, _, _ = make_service(FakeApi())
        self.assertIsNotNone((await service.check_character("  ANA ")).info)

    async def test_api_caida(self):
        service, _, save = make_service(FakeApi())
        check = await service.check_character("ana")
        self.assertIsNone(check.char_info)
        save.assert_not_called()

    async def test_nivel_igual(self):
        api = FakeApi(characters={"Ana": {"character_info": {"level": 100}}})
        service, _, save = make_service(api)
        self.assertIsNone((await service.check_character("ana")).changed_from)
        save.assert_not_called()

    async def test_nivel_distinto_guarda_e_informa_el_anterior(self):
        api = FakeApi(characters={"Ana": {"character_info": {"level": 105}}})
        service, players, save = make_service(api)
        self.assertEqual((await service.check_character("ana")).changed_from, 100)
        self.assertEqual(players["ana"]["current_level"], 105)
        save.assert_called_once()


class PageTests(unittest.IsolatedAsyncioTestCase):
    def make_cog(self, service):
        os.environ.setdefault("DISCORD_TOKEN", "test")  # config.py lo exige al importarse
        from src.channels.onlines import page

        cog = page.WatchedTracker.__new__(page.WatchedTracker)  # Sin __init__: no arranca la tarea
        cog.bot, cog.service = MagicMock(), service
        cog.watched_embed_message, cog.last_online_embeds = {}, None
        return cog, page

    async def run_loop(self, service, level_channel):
        cog, page = self.make_cog(service)
        with patch.object(page, "find_channel", return_value=level_channel), \
                patch.object(page, "send_or_update_embed", new=AsyncMock()) as send:
            await page.WatchedTracker.update_online_watched_players.coro(cog)
        return cog, page, send

    async def test_el_loop_publica_lista_y_alerta(self):
        service, _, _ = make_service(FakeApi([online_player("Ana", 101)]))
        level_channel = MagicMock(send=AsyncMock())
        cog, page, send = await self.run_loop(service, level_channel)
        self.assertEqual(level_channel.send.await_args.kwargs["embed"].title, "Ana subió a nivel 101")
        self.assertEqual(send.await_args.args[2], page.CHANNEL_ONLINES)
        text = str([e.to_dict() for e in cog.last_online_embeds])
        self.assertIn("name=Ana", text)
        self.assertIn("Vale", text)
        self.assertIn("desde ana", text)
        self.assertIn("Conectados: 1 de 1", text)

    async def test_sin_canal_de_niveles_igual_guarda_y_publica_la_lista(self):
        service, players, _ = make_service(FakeApi([online_player("Ana", 101)]))
        _, _, send = await self.run_loop(service, None)
        self.assertEqual(players["ana"]["current_level"], 101)
        send.assert_awaited_once()

    async def test_sin_personajes_no_consulta(self):
        api = FakeApi()
        service, _, _ = make_service(api, {})
        _, _, send = await self.run_loop(service, None)
        send.assert_not_awaited()
        self.assertFalse(hasattr(api, "world"))

    async def test_nivel_con_cambio_avisa_en_el_canal_de_niveles(self):
        api = FakeApi(characters={"Ana": {"character_info": {"level": 105, "vocation": "Knight", "world": "Quidera"}}})
        service, _, _ = make_service(api)
        cog, page = self.make_cog(service)
        level_channel = MagicMock(send=AsyncMock())
        ctx = MagicMock(defer=AsyncMock(), send=AsyncMock())
        with patch.object(page, "find_channel", return_value=level_channel):
            await page.WatchedTracker.level_command.callback(cog, ctx, character_name="Ana")
        self.assertEqual(level_channel.send.await_args.kwargs["embed"].title, "Ana subió a nivel 105")
        self.assertEqual(ctx.send.await_args.kwargs["embed"].fields[2].value, "100 → **105**")

    async def test_nivel_sin_cambio_muestra_ficha_solo_a_quien_pregunta(self):
        api = FakeApi(characters={"Ana": {"character_info": {"level": 100, "vocation": "Knight", "world": "Quidera"}}})
        service, _, _ = make_service(api)
        cog, page = self.make_cog(service)
        level_channel = MagicMock(send=AsyncMock())
        ctx = MagicMock(defer=AsyncMock(), send=AsyncMock())
        with patch.object(page, "find_channel", return_value=level_channel):
            await page.WatchedTracker.level_command.callback(cog, ctx, character_name="Ana")
        level_channel.send.assert_not_awaited()
        self.assertEqual(ctx.send.await_args.kwargs["embed"].title, "Ana · Nivel 100")

    async def test_nivel_mensajes_de_error(self):
        service, _, _ = make_service(FakeApi())
        cog, page = self.make_cog(service)
        ctx = MagicMock(defer=AsyncMock(), send=AsyncMock())
        await page.WatchedTracker.level_command.callback(cog, ctx, character_name="Nadie")
        self.assertIn("no está en la lista", ctx.send.await_args.args[0])
        await page.WatchedTracker.level_command.callback(cog, ctx, character_name="Ana")
        self.assertIn("No se pudo consultar a **Ana**", ctx.send.await_args.args[0])

    async def test_comando_online_fuera_de_onlines_muestra_la_lista(self):
        service, _, _ = make_service(FakeApi([online_player("Ana")]))
        tracker, page = self.make_cog(service)
        online_cog = page.OnlineCommands.__new__(page.OnlineCommands)
        online_cog.bot = MagicMock(get_cog=MagicMock(return_value=tracker))
        ctx = MagicMock(defer=AsyncMock(), send=AsyncMock())
        with patch.object(page, "find_channel", return_value=None), \
                patch.object(page, "send_or_update_embed", new=AsyncMock()):
            async def run_loop_once():
                await page.WatchedTracker.update_online_watched_players.coro(tracker)

            tracker.update_online_watched_players = run_loop_once
            await page.OnlineCommands.list_online_members.callback(online_cog, ctx)
        self.assertIs(ctx.send.await_args.kwargs["embeds"], tracker.last_online_embeds)


if __name__ == "__main__":
    unittest.main()
