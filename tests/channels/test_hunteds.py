"""Pruebas de #hunteds sin internet ni Discord."""
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from src.channels.hunteds.service import ADDED, ALREADY_LISTED, NOT_FOUND, DeathAlert, HuntedService, LevelAlert
from tests.fakes import GUILD_ID, FakeCharacterRepository, FakeGuildRepository, FakeSession


class FakeApi:
    def __init__(self, online=(), characters=None):
        self.online = list(online)
        self.characters = characters or {}
        self.worlds = []

    async def fetch_world_online_players(self, session, world):
        self.worlds.append(world)
        return self.online

    async def fetch_player_data(self, session, name):
        return self.characters.get(name)


class FakeLogins:
    def __init__(self):
        self.since = {}
        self.updates = []

    def update(self, keys, last_logins=None):
        self.updates.append((set(keys), last_logins))
        self.since = {key: "ahora" for key in keys}

    def text(self, key):
        return f"desde {key}"


def hunted(name="Ana", level=100, vocation="Knight", last_death=None):
    return {"char_name": name, "world": "Quidera", "level": level, "vocation": vocation, "last_death_time": last_death}


def api_character(deaths=(), name="Ana", world="Quidera"):
    return {
        "official_name": name,
        "tibia_link": f"[{name}](http://x)",
        "deaths": list(deaths),
        "character_info": {"level": 100, "vocation": "Knight", "world": world, "last_login": "ayer"},
    }


def online(name="Ana", level=100, vocation="Knight"):
    return {"name": name, "level": level, "vocation": vocation}


def make_service(api, players=None, other_guilds=None, worlds=None):
    players = players if players is not None else {"ana": hunted()}
    repository = FakeCharacterRepository(players, other_guilds)
    service = HuntedService(api, repository, FakeGuildRepository(worlds), FakeLogins, parse_time=lambda v: v,
                            session_factory=FakeSession)
    return service, players


class CheckTests(unittest.IsolatedAsyncioTestCase):
    async def check(self, service):
        return await service.check(GUILD_ID, "Quidera")

    async def test_muerte_nueva_alerta_una_vez_y_guarda(self):
        api = FakeApi(characters={"Ana": api_character([{"time": "t2"}])})
        service, players = make_service(api, {"ana": hunted(last_death="t1")})
        first = await self.check(service)
        second = await self.check(service)
        self.assertIsInstance(first.alerts[0], DeathAlert)
        self.assertEqual(second.alerts, [])
        self.assertEqual(players["ana"]["last_death_time"], "t2")

    async def test_desconectado_igual_detecta_muertes_pero_no_sale_online(self):
        api = FakeApi([online("Otro")], {"Ana": api_character([{"time": "t2"}])})
        service, players = make_service(api)
        result = await self.check(service)
        self.assertEqual(len(result.alerts), 1)
        self.assertEqual(result.online, [])
        self.assertEqual(players["ana"]["last_death_time"], "t2")

    async def test_cambio_de_nivel_alerta_y_actualiza(self):
        api = FakeApi([online("ANA", 105)], {"Ana": api_character()})
        service, players = make_service(api)
        result = await self.check(service)
        alert = result.alerts[0]
        self.assertIsInstance(alert, LevelAlert)
        self.assertEqual((alert.old_level, alert.new_level), (100, 105))
        self.assertEqual(players["ana"]["level"], 105)
        self.assertEqual(result.online[0].key, "ana")

    async def test_sin_nivel_previo_guarda_sin_alertar(self):
        api = FakeApi([online("Ana", 105)], {"Ana": api_character()})
        service, players = make_service(api, {"ana": hunted(level=0)})
        self.assertEqual((await self.check(service)).alerts, [])
        self.assertEqual(players["ana"]["level"], 105)

    async def test_el_aviso_de_muerte_conserva_los_datos_de_ese_momento(self):
        api = FakeApi([online("Ana", 100, "Elite Knight")], {"Ana": api_character([{"time": "t2"}])})
        service, players = make_service(api)
        alert = (await self.check(service)).alerts[0]
        self.assertEqual(alert.data["vocation"], "Knight")
        self.assertEqual(players["ana"]["vocation"], "Elite Knight")

    async def test_eliminado_durante_la_revision_se_ignora(self):
        players = {"ana": hunted()}
        api = FakeApi([online("Ana", 105)])

        async def remove_while_checking(session, name):
            players.pop("ana")
            return api_character([{"time": "t2"}])

        api.fetch_player_data = remove_while_checking
        service, _ = make_service(api, players)
        result = await self.check(service)
        self.assertEqual((result.alerts, result.online), ([], []))
        self.assertEqual(players, {})

    async def test_lista_vacia_de_la_api_no_toca_las_conexiones(self):
        service, _ = make_service(FakeApi([], {"Ana": api_character()}))
        await self.check(service)
        self.assertEqual(service._logins[GUILD_ID].updates, [])

    async def test_pasa_las_horas_de_ingreso(self):
        service, _ = make_service(FakeApi([online("Ana")], {"Ana": api_character()}))
        await self.check(service)
        self.assertEqual(service._logins[GUILD_ID].updates, [({"ana"}, {"ana": "ayer"})])

    async def test_cada_servidor_tiene_su_lista_su_mundo_y_sus_conexiones(self):
        api = FakeApi([online("Zoe", 200)], {"Zoe": api_character(name="Zoe", world="Antica")})
        service, players = make_service(api, {}, {2: {"zoe": hunted("Zoe", 150)}})
        result = await service.check(2, "Antica")
        self.assertEqual(api.worlds, ["Antica"])
        self.assertEqual(len(result.alerts), 1)
        self.assertEqual(service.online_keys(2), {"zoe"})
        self.assertEqual(service.online_keys(GUILD_ID), set())
        self.assertEqual(players, {})


class ListTests(unittest.IsolatedAsyncioTestCase):
    async def test_agregar_guarda_con_la_ultima_muerte_conocida(self):
        api = FakeApi(characters={"ana": api_character([{"time": "t1"}])})
        service, players = make_service(api, {})
        result = await service.add(GUILD_ID, " ana ", "Vale")
        self.assertEqual(result.status, ADDED)
        self.assertEqual(result.world, "Quidera")
        self.assertEqual(players["ana"]["last_death_time"], "t1")
        self.assertEqual(players["ana"]["added_by"], "Vale")

    async def test_agregar_no_encontrado_y_duplicado(self):
        api = FakeApi(characters={"Ana": api_character()})
        service, _ = make_service(api)
        self.assertEqual((await service.add(GUILD_ID, "Nadie", "Vale")).status, NOT_FOUND)
        self.assertEqual((await service.add(GUILD_ID, "Ana", "Vale")).status, ALREADY_LISTED)

    async def test_el_mismo_personaje_puede_estar_en_dos_servidores(self):
        api = FakeApi(characters={"Ana": api_character()})
        service, _ = make_service(api, other_guilds={2: {}})
        self.assertEqual((await service.add(2, "Ana", "Luis")).status, ADDED)
        self.assertEqual((await service.add(GUILD_ID, "Ana", "Luis")).status, ALREADY_LISTED)

    async def test_eliminar(self):
        service, players = make_service(FakeApi())
        self.assertIsNone(await service.remove(GUILD_ID, "Nadie"))
        self.assertEqual((await service.remove(GUILD_ID, " ANA "))["char_name"], "Ana")
        self.assertEqual(players, {})

    async def test_eliminar_no_toca_otros_servidores(self):
        other = {"ana": hunted()}
        service, _ = make_service(FakeApi(), {}, {2: other})
        self.assertIsNone(await service.remove(GUILD_ID, "ana"))
        self.assertIn("ana", other)

    async def test_separa_conectados_y_ordena_por_nivel(self):
        players = {"a": hunted("A", 10), "b": hunted("B", 30), "c": hunted("C", 20)}
        service, _ = make_service(FakeApi(), players)
        service._logins[GUILD_ID].since = {"a": "ahora"}
        online_list, offline = await service.split_by_connection(GUILD_ID)
        self.assertEqual([d["char_name"] for d in online_list], ["A"])
        self.assertEqual([d["char_name"] for d in offline], ["B", "C"])


class PageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        os.environ.setdefault("DISCORD_TOKEN", "test")  # config.py lo exige al importarse
        from src.channels.hunteds import page
        self.page = page

    def make_cog(self, service, worlds=None):
        cog = self.page.HuntedTracker.__new__(self.page.HuntedTracker)  # Sin __init__: no arranca la tarea
        cog.bot, cog.service, cog.list_message = MagicMock(guilds=[MagicMock(id=GUILD_ID)]), service, {}
        cog.guilds = FakeGuildRepository(worlds)
        return cog

    async def run_loop(self, service, worlds=None):
        cog = self.make_cog(service, worlds)
        with patch.object(self.page, "broadcast_embed", new=AsyncMock()) as broadcast, \
                patch.object(self.page, "send_or_update_embed", new=AsyncMock()) as send:
            await self.page.HuntedTracker.track_hunteds.coro(cog)
        return broadcast, send

    async def test_loop_publica_avisos_y_luego_la_lista_con_botones(self):
        api = FakeApi([online("Ana", 105)], {"Ana": api_character([{"time": "t2", "level": 90, "killers": []}])})
        service, _ = make_service(api)
        broadcast, send = await self.run_loop(service)
        titles = [call.args[2].title for call in broadcast.await_args_list]
        self.assertEqual(titles, ["Hunted Ana murió a nivel 90", "Hunted Ana subió a nivel 105"])
        self.assertEqual(broadcast.await_args_list[0].args[0].id, GUILD_ID)
        args, kwargs = send.await_args
        self.assertEqual(args[3], self.page.CHANNEL_HUNTEDS)
        self.assertIsInstance(kwargs["view"], self.page.HuntedPanelView)
        text = str([e.to_dict() for e in args[4]])
        self.assertIn("desde ana", text)
        self.assertIn("Mundo: Quidera · Conectados: 1 de 1", text)

    async def test_lista_vacia_publica_el_panel_sin_consultar(self):
        api = FakeApi()
        service, _ = make_service(api, {})
        _, send = await self.run_loop(service)
        self.assertEqual(api.worlds, [])
        self.assertIn("La lista está vacía", str([e.to_dict() for e in send.await_args.args[4]]))

    async def test_sin_configurar_no_publica_ni_consulta(self):
        api = FakeApi()
        service, _ = make_service(api)
        _, send = await self.run_loop(service, worlds={})
        send.assert_not_awaited()
        self.assertEqual(api.worlds, [])

    async def test_embed_de_lista_y_ficha(self):
        service, _ = make_service(FakeApi())
        service._logins[GUILD_ID].since = {"ana": "ahora"}
        listing = await self.page.build_hunted_list_embed(service, GUILD_ID)
        self.assertEqual(listing.fields[0].name, "Conectados · 1")
        self.assertEqual(listing.footer.text, "Mundo vigilado: Quidera · Total: 1")
        self.assertEqual((await self.page.build_hunted_embed(service, GUILD_ID, "ana")).description, "Conectado ahora")
        self.assertIn("no está en la lista", (await self.page.build_hunted_embed(service, GUILD_ID, "Nadie")).description)

    async def test_aviso_si_el_personaje_es_de_otro_mundo(self):
        api = FakeApi(characters={"Beto": api_character(name="Beto", world="Otro")})
        service, _ = make_service(api, {})
        embed = await self.page.add_hunted(service, GUILD_ID, "Beto", "Vale")
        self.assertEqual(embed.fields[0].name, "Atención")
        self.assertIn("**Quidera**", embed.fields[0].value)

    async def test_sin_aviso_si_es_del_mundo_del_servidor(self):
        api = FakeApi(characters={"Beto": api_character(name="Beto", world="Antica")})
        service, _ = make_service(api, {}, worlds={GUILD_ID: "Antica"})
        self.assertEqual((await self.page.add_hunted(service, GUILD_ID, "Beto", "Vale")).fields, [])

    def interaction(self, guild_id=GUILD_ID):
        return MagicMock(
            guild_id=guild_id,
            response=MagicMock(defer=AsyncMock(), send_message=AsyncMock(), send_modal=AsyncMock()),
            followup=MagicMock(send=AsyncMock()),
        )

    async def test_formularios_usan_el_servidor_de_quien_los_envia(self):
        other = {}
        api = FakeApi(characters={"Beto": api_character(name="Beto")})
        service, players = make_service(api, other_guilds={2: other})
        add = self.page.HuntedAddModal(service)
        add.character._value = "Beto"
        interaction = self.interaction(2)
        await add.on_submit(interaction)
        self.assertIn("beto", other)
        self.assertNotIn("beto", players)
        self.assertEqual(interaction.followup.send.await_args.kwargs["embed"].title, "Beto agregado a hunteds")

        remove = self.page.HuntedRemoveModal(service)
        remove.character._value = "Beto"
        await remove.on_submit(self.interaction(2))
        self.assertNotIn("beto", other)

    async def test_boton_eliminar_abre_menu_que_elimina(self):
        service, players = make_service(FakeApi())
        view = self.page.HuntedPanelView(service)
        interaction = self.interaction()
        await view.remove.callback(interaction)
        select = interaction.response.send_message.await_args.kwargs["view"].children[0]
        self.assertEqual(select.options[0].label, "Ana")
        self.assertEqual((await select.remove_func("ana")).title, "Ana eliminado de hunteds")
        self.assertEqual(players, {})

    async def test_los_botones_conservan_sus_ids_para_seguir_funcionando_tras_reiniciar(self):
        service, _ = make_service(FakeApi())
        ids = {child.custom_id for child in self.page.HuntedPanelView(service).children}
        self.assertEqual(ids, {"hunted_panel:add", "hunted_panel:search", "hunted_panel:list", "hunted_panel:remove"})


if __name__ == "__main__":
    unittest.main()
