"""Pruebas de #onlines y #level-up sin internet ni Discord."""
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from src.channels.onlines.service import OnlineService
from tests.fakes import GUILD_ID, FakeCharacterRepository, FakeGuildRepository, FakeSession


class FakeApi:
    def __init__(self, online=(), characters=None):
        self.online = list(online)
        self.characters = characters or {}
        self.queries = []
        self.worlds = []

    async def fetch_world_online_players(self, session, world):
        self.worlds.append(world)
        return self.online

    async def fetch_player_data(self, session, name):
        self.queries.append(name)
        return self.characters.get(name)


class FakeLogins:
    needs_api_times = False

    def __init__(self):
        self.updates = []

    def update(self, keys, last_logins=None):
        self.updates.append((set(keys), last_logins))

    def text(self, key):
        return f"desde {key}"


class FirstCheckLogins(FakeLogins):
    needs_api_times = True


def online_player(name, level=100, vocation="Knight"):
    return {"name": name, "level": level, "vocation": vocation}


def watched(name="Ana", level=100, **extra):
    data = {"char_name": name, "owner": "Vale", **extra}
    if level is not None:
        data["current_level"] = level
    return data


def make_service(api, players=None, logins=FakeLogins, other_guilds=None):
    players = players if players is not None else {"ana": watched()}
    repository = FakeCharacterRepository(players, other_guilds)
    return OnlineService(api, repository, logins, parse_time=lambda v: v, session_factory=FakeSession), players


class CheckOnlineTests(unittest.IsolatedAsyncioTestCase):
    async def test_devuelve_solo_monitoreados_conectados_sin_importar_mayusculas(self):
        api = FakeApi([online_player("ANA"), online_player("Otro")])
        service, _ = make_service(api, {"ana": watched(), "beto": watched("Beto")})
        online = await service.check_online(GUILD_ID, "Quidera", AsyncMock())
        self.assertEqual([p.key for p in online], ["ana"])
        self.assertEqual(online[0].name, "Ana")
        self.assertEqual(api.worlds, ["Quidera"])

    async def test_primer_nivel_se_guarda_sin_avisar(self):
        service, players = make_service(FakeApi([online_player("Ana", 50)]), {"ana": watched(level=None)})
        notify = AsyncMock()
        await service.check_online(GUILD_ID, "Quidera", notify)
        notify.assert_not_awaited()
        self.assertEqual(players["ana"]["current_level"], 50)

    async def test_cambio_de_nivel_guarda_y_avisa_una_vez(self):
        service, players = make_service(FakeApi([online_player("Ana", 101)]))
        notify = AsyncMock()
        await service.check_online(GUILD_ID, "Quidera", notify)
        await service.check_online(GUILD_ID, "Quidera", notify)
        notify.assert_awaited_once()
        self.assertEqual(notify.await_args.args[1:], (100, 101, "Knight"))
        self.assertEqual(players["ana"]["current_level"], 101)

    async def test_bajar_de_nivel_tambien_avisa(self):
        service, _ = make_service(FakeApi([online_player("Ana", 95)]))
        notify = AsyncMock()
        await service.check_online(GUILD_ID, "Quidera", notify)
        self.assertEqual(notify.await_args.args[1:], (100, 95, "Knight"))

    async def test_lista_vacia_de_la_api_no_toca_las_horas_de_ingreso(self):
        service, _ = make_service(FakeApi([]))
        self.assertEqual(await service.check_online(GUILD_ID, "Quidera", AsyncMock()), [])
        self.assertEqual(service._logins[GUILD_ID].updates, [])

    async def test_primera_revision_pide_last_login_a_la_api(self):
        api = FakeApi([online_player("Ana")], {"Ana": {"character_info": {"last_login": "ayer"}}})
        service, _ = make_service(api, logins=FirstCheckLogins)
        await service.check_online(GUILD_ID, "Quidera", AsyncMock())
        self.assertEqual(service._logins[GUILD_ID].updates, [({"ana"}, {"ana": "ayer"})])

    async def test_revisiones_siguientes_no_consultan_personajes(self):
        api = FakeApi([online_player("Ana")])
        service, _ = make_service(api)
        await service.check_online(GUILD_ID, "Quidera", AsyncMock())
        self.assertEqual(api.queries, [])
        self.assertEqual(service._logins[GUILD_ID].updates, [({"ana"}, None)])

    async def test_cada_servidor_tiene_su_grupo_y_sus_horas_de_ingreso(self):
        other = {"ana": watched(level=90)}
        service, players = make_service(FakeApi([online_player("Ana", 100)]), other_guilds={2: other})
        notify = AsyncMock()
        await service.check_online(GUILD_ID, "Quidera", notify)
        notify.assert_not_awaited()
        await service.check_online(2, "Quidera", notify)
        notify.assert_awaited_once()  # En el servidor 2 sí cambió
        self.assertEqual(other["ana"]["current_level"], 100)
        self.assertIsNot(service._logins[GUILD_ID], service._logins[2])


class CheckCharacterTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_monitoreado(self):
        service, _ = make_service(FakeApi())
        self.assertIsNone((await service.check_character(GUILD_ID, "Nadie")).info)

    async def test_busca_sin_mayusculas_ni_espacios(self):
        service, _ = make_service(FakeApi())
        self.assertIsNotNone((await service.check_character(GUILD_ID, "  ANA ")).info)

    async def test_personaje_de_otro_servidor_no_existe_aqui(self):
        service, _ = make_service(FakeApi(), other_guilds={2: {"zoe": watched("Zoe")}})
        self.assertIsNone((await service.check_character(GUILD_ID, "Zoe")).info)

    async def test_api_caida(self):
        service, players = make_service(FakeApi())
        check = await service.check_character(GUILD_ID, "ana")
        self.assertIsNone(check.char_info)
        self.assertEqual(players["ana"]["current_level"], 100)

    async def test_nivel_igual(self):
        api = FakeApi(characters={"Ana": {"character_info": {"level": 100}}})
        service, _ = make_service(api)
        self.assertIsNone((await service.check_character(GUILD_ID, "ana")).changed_from)

    async def test_nivel_distinto_guarda_e_informa_el_anterior(self):
        api = FakeApi(characters={"Ana": {"character_info": {"level": 105}}})
        service, players = make_service(api)
        check = await service.check_character(GUILD_ID, "ana")
        self.assertEqual(check.changed_from, 100)
        self.assertEqual(check.info["current_level"], 105)
        self.assertEqual(players["ana"]["current_level"], 105)


class PageTests(unittest.IsolatedAsyncioTestCase):
    def make_cog(self, service, worlds=None):
        os.environ.setdefault("DISCORD_TOKEN", "test")  # config.py lo exige al importarse
        from src.channels.onlines import page

        cog = page.WatchedTracker.__new__(page.WatchedTracker)  # Sin __init__: no arranca la tarea
        cog.bot, cog.service, cog.guilds = MagicMock(), service, FakeGuildRepository(worlds)
        cog.watched_embed_message, cog.last_online_embeds = {}, {}
        return cog, page

    async def run_loop(self, service, level_channel, worlds=None, guild_ids=(GUILD_ID,)):
        cog, page = self.make_cog(service, worlds)
        cog.bot.guilds = [MagicMock(id=guild_id, text_channels=[]) for guild_id in guild_ids]
        with patch.object(page, "find_channel", return_value=level_channel), \
                patch.object(page, "send_or_update_embed", new=AsyncMock()) as send:
            await page.WatchedTracker.update_online_watched_players.coro(cog)
        return cog, page, send

    async def test_el_loop_publica_lista_y_alerta(self):
        service, _ = make_service(FakeApi([online_player("Ana", 101)]))
        level_channel = MagicMock(send=AsyncMock())
        cog, page, send = await self.run_loop(service, level_channel)
        self.assertEqual(level_channel.send.await_args.kwargs["embed"].title, "Ana subió a nivel 101")
        self.assertEqual(send.await_args.args[3], page.CHANNEL_ONLINES)
        text = str([e.to_dict() for e in cog.last_online_embeds[GUILD_ID]])
        self.assertIn("name=Ana", text)
        self.assertIn("Vale", text)
        self.assertIn("desde ana", text)
        self.assertIn("Mundo: Quidera · Conectados: 1 de 1", text)

    async def test_cada_servidor_consulta_su_mundo(self):
        api = FakeApi([online_player("Ana")])
        service, _ = make_service(api, other_guilds={2: {"ana": watched()}})
        cog, _, _ = await self.run_loop(service, None, {GUILD_ID: "Quidera", 2: "Antica"}, (GUILD_ID, 2))
        self.assertEqual(api.worlds, ["Quidera", "Antica"])
        self.assertIn("Mundo: Antica", str([e.to_dict() for e in cog.last_online_embeds[2]]))

    async def test_servidor_sin_configurar_no_consulta_nada(self):
        api = FakeApi([online_player("Ana")])
        service, _ = make_service(api)
        _, _, send = await self.run_loop(service, None, worlds={})
        self.assertEqual(api.worlds, [])
        send.assert_not_awaited()

    async def test_sin_canal_de_niveles_igual_guarda_y_publica_la_lista(self):
        service, players = make_service(FakeApi([online_player("Ana", 101)]))
        _, _, send = await self.run_loop(service, None)
        self.assertEqual(players["ana"]["current_level"], 101)
        send.assert_awaited_once()

    async def test_sin_personajes_no_consulta(self):
        api = FakeApi()
        service, _ = make_service(api, {})
        _, _, send = await self.run_loop(service, None)
        send.assert_not_awaited()
        self.assertEqual(api.worlds, [])

    def ctx(self, guild_id=GUILD_ID):
        return MagicMock(defer=AsyncMock(), send=AsyncMock(), guild=MagicMock(id=guild_id, text_channels=[]))

    async def test_nivel_con_cambio_avisa_en_el_canal_de_niveles(self):
        api = FakeApi(characters={"Ana": {"character_info": {"level": 105, "vocation": "Knight", "world": "Quidera"}}})
        service, _ = make_service(api)
        cog, page = self.make_cog(service)
        level_channel = MagicMock(send=AsyncMock())
        ctx = self.ctx()
        with patch.object(page, "find_channel", return_value=level_channel):
            await page.WatchedTracker.level_command.callback(cog, ctx, character_name="Ana")
        self.assertEqual(level_channel.send.await_args.kwargs["embed"].title, "Ana subió a nivel 105")
        self.assertEqual(ctx.send.await_args.kwargs["embed"].fields[2].value, "100 → **105**")

    async def test_nivel_sin_cambio_muestra_ficha_solo_a_quien_pregunta(self):
        api = FakeApi(characters={"Ana": {"character_info": {"level": 100, "vocation": "Knight", "world": "Quidera"}}})
        service, _ = make_service(api)
        cog, page = self.make_cog(service)
        level_channel = MagicMock(send=AsyncMock())
        ctx = self.ctx()
        with patch.object(page, "find_channel", return_value=level_channel):
            await page.WatchedTracker.level_command.callback(cog, ctx, character_name="Ana")
        level_channel.send.assert_not_awaited()
        self.assertEqual(ctx.send.await_args.kwargs["embed"].title, "Ana · Nivel 100")

    async def test_nivel_mensajes_de_error(self):
        service, _ = make_service(FakeApi())
        cog, page = self.make_cog(service)
        ctx = self.ctx()
        await page.WatchedTracker.level_command.callback(cog, ctx, character_name="Nadie")
        self.assertIn("no está en la lista", ctx.send.await_args.args[0])
        await page.WatchedTracker.level_command.callback(cog, ctx, character_name="Ana")
        self.assertIn("No se pudo consultar a **Ana**", ctx.send.await_args.args[0])

    def online_cog(self, tracker, page):
        online_cog = page.OnlineCommands.__new__(page.OnlineCommands)
        online_cog.bot = MagicMock(get_cog=MagicMock(return_value=tracker))
        online_cog.guilds = tracker.guilds
        return online_cog

    async def test_comando_online_fuera_de_onlines_muestra_la_lista(self):
        service, _ = make_service(FakeApi([online_player("Ana")]))
        tracker, page = self.make_cog(service)
        ctx = self.ctx()
        with patch.object(page, "find_channel", return_value=None), \
                patch.object(page, "send_or_update_embed", new=AsyncMock()):
            await page.OnlineCommands.list_online_members.callback(self.online_cog(tracker, page), ctx)
        self.assertIs(ctx.send.await_args.kwargs["embeds"], tracker.last_online_embeds[GUILD_ID])

    async def test_comando_online_sin_personajes_no_falla(self):
        service, _ = make_service(FakeApi(), {})
        tracker, page = self.make_cog(service)
        ctx = self.ctx()
        with patch.object(page, "find_channel", return_value=None):
            await page.OnlineCommands.list_online_members.callback(self.online_cog(tracker, page), ctx)
        self.assertIn("vacía", ctx.send.await_args.args[0])

    async def test_comando_online_sin_configurar_pide_config(self):
        service, _ = make_service(FakeApi())
        tracker, page = self.make_cog(service, {})
        ctx = self.ctx()
        await page.OnlineCommands.list_online_members.callback(self.online_cog(tracker, page), ctx)
        self.assertIn("/config", ctx.send.await_args.args[0])


if __name__ == "__main__":
    unittest.main()
