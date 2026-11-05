"""Pruebas de #tpbot sin internet ni Discord."""
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from src.channels.tpbot.service import GroupService


class FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeApi:
    def __init__(self, characters=None):
        self.characters = characters or {}

    async def fetch_player_data(self, session, name):
        return self.characters.get(name)


def character(name="Ana", level=120, vocation="Knight", world="Quidera"):
    return {
        "official_name": name,
        "tibia_link": f"[{name}](http://x)",
        "character_info": {"level": level, "vocation": vocation, "world": world},
    }


def member(name="Ana", owner="Vale", level=120, initial=100, weekly=110, vocation="Knight"):
    return {"char_name": name, "owner": owner, "current_level": level, "initial_level": initial,
            "weekly_start_level": weekly, "vocation": vocation}


def make_service(players=None, characters=None, state=None):
    save = MagicMock()
    players = players if players is not None else {"ana": member()}
    state = state if state is not None else {}
    service = GroupService(FakeApi(characters), players, state, save, session_factory=FakeSession)
    return service, players, state, save


class GroupServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_agregar_nuevo(self):
        service, players, _, save = make_service({}, {"ana": character()})
        added = await service.add(" Vale ", " ana ")
        self.assertFalse(added.existed)
        self.assertEqual(players["ana"]["owner"], "Vale")
        self.assertEqual(players["ana"]["initial_level"], 120)
        self.assertEqual(players["ana"]["weekly_start_level"], 120)
        save.assert_called_once()

    async def test_agregar_existente_conserva_niveles_historicos(self):
        service, players, _, _ = make_service(characters={"Ana": character(level=130)})
        added = await service.add("Otro", "Ana")
        self.assertTrue(added.existed)
        self.assertEqual(players["ana"]["initial_level"], 100)
        self.assertEqual(players["ana"]["weekly_start_level"], 110)
        self.assertEqual(players["ana"]["current_level"], 130)
        self.assertEqual(players["ana"]["owner"], "Otro")

    async def test_agregar_no_encontrado_no_guarda(self):
        service, _, _, save = make_service()
        self.assertIsNone(await service.add("Vale", "Nadie"))
        save.assert_not_called()

    def test_eliminar_borra_tambien_el_estado(self):
        service, players, state, save = make_service(state={"ana": {"level": 1}})
        self.assertEqual(service.remove(" ANA ")["char_name"], "Ana")
        self.assertEqual((players, state), ({}, {}))
        save.assert_called_once()

    def test_eliminar_inexistente(self):
        service, _, _, save = make_service()
        self.assertIsNone(service.remove("Nadie"))
        save.assert_not_called()

    def test_agrupa_por_dueno_de_mayor_a_menor_nivel(self):
        players = {"a": member("A", "Vale", 10), "b": member("B", "Luis", 30), "c": member("C", "Vale", 20)}
        service, _, _, _ = make_service(players)
        grouped = service.by_owner()
        self.assertEqual([d["char_name"] for d in grouped["Vale"]], ["C", "A"])
        self.assertEqual(service.owner_count(), 2)


class PageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        os.environ.setdefault("DISCORD_TOKEN", "test")  # config.py lo exige al importarse
        from src.channels.tpbot import page
        self.page = page

    def interaction(self):
        return MagicMock(
            response=MagicMock(defer=AsyncMock(), send_message=AsyncMock(), send_modal=AsyncMock()),
            followup=MagicMock(send=AsyncMock()),
        )

    async def test_embed_agregado_y_actualizado(self):
        service, _, _, _ = make_service({}, {"Ana": character()})
        first = await self.page.add_group_member(service, " Vale ", "Ana")
        second = await self.page.add_group_member(service, "Vale", "Ana")
        self.assertEqual(first.title, "Ana agregado al grupo")
        self.assertEqual(first.fields[0].value, "Vale")
        self.assertEqual(first.footer.text, "Mundo: Quidera")
        self.assertEqual(second.title, "Ana actualizado")

    async def test_embed_no_encontrado(self):
        service, _, _, _ = make_service()
        self.assertIn("No se encontró", (await self.page.add_group_member(service, "V", "Nadie")).description)

    async def test_ficha_con_progreso(self):
        service, _, _, _ = make_service()
        embed = self.page.build_group_member_embed(service, "ana")
        self.assertEqual(embed.fields[3].value, "Esta semana: **+10** niveles\nDesde que ingresó: **+20** niveles")

    async def test_lista_vacia(self):
        service, _, _, _ = make_service({})
        self.assertIn("La lista está vacía", self.page.build_group_list_embed(service).description)
        embed, chart = self.page.build_group_progress(service)
        self.assertIsNone(chart)
        self.assertEqual(embed.title, "Personajes del grupo")

    async def test_grafica_real_con_fuentes_del_proyecto(self):
        players = {"a": member("Ana", "Vale"), "b": member("Beto", "Luis", 300, 300), "c": member("Caro", "Vale", 50, 90)}
        service, _, _, _ = make_service(players)
        embed, chart = self.page.build_group_progress(service)
        self.assertTrue(chart.fp.read(8).startswith(b"\x89PNG"))
        self.assertEqual(embed.image.url, f"attachment://{chart.filename}")
        self.assertEqual(embed.footer.text, "Total: 3 personajes · 2 dueños")

    async def test_el_loop_publica_el_panel_al_final(self):
        service, _, _, _ = make_service()
        cog = self.page.Panel.__new__(self.page.Panel)  # Sin __init__: no arranca la tarea
        cog.bot, cog.service, cog.group_list_message = MagicMock(), service, {}
        with patch.object(self.page, "send_or_update_embed", new=AsyncMock()) as send:
            await self.page.Panel.update_group_list.coro(cog)
        args, kwargs = send.await_args
        self.assertEqual(args[2], self.page.CHANNEL_TPBOT)
        self.assertEqual(args[3].footer.text, "1 personajes · 1 dueños")
        self.assertTrue(kwargs["keep_last"])
        self.assertIsInstance(kwargs["view"], self.page.GroupPanelView)

    async def test_formularios(self):
        service, players, _, _ = make_service({}, {"Beto": character("Beto")})
        add = self.page.GroupAddModal(service)
        add.owner._value, add.character._value = "Vale", "Beto"
        await add.on_submit(self.interaction())
        self.assertIn("beto", players)

        search = self.page.GroupSearchModal(service)
        search.character._value = "beto"
        interaction = self.interaction()
        await search.on_submit(interaction)
        self.assertEqual(interaction.response.send_message.await_args.kwargs["embed"].title, "Beto")

        remove = self.page.GroupRemoveModal(service)
        remove.character._value = "beto"
        await remove.on_submit(self.interaction())
        self.assertEqual(players, {})

    async def test_boton_eliminar_segun_la_cantidad(self):
        service, players, _, _ = make_service()
        view = self.page.GroupPanelView(service)
        interaction = self.interaction()
        await view.remove.callback(interaction)
        select = interaction.response.send_message.await_args.kwargs["view"].children[0]
        self.assertEqual(select.remove_func("ana").title, "Ana eliminado del grupo")

        interaction = self.interaction()
        await view.remove.callback(interaction)
        self.assertIn("La lista está vacía", interaction.response.send_message.await_args.kwargs["embed"].description)

        players.update({f"p{i}": member(f"P{i}") for i in range(26)})
        interaction = self.interaction()
        await view.remove.callback(interaction)
        self.assertIsInstance(interaction.response.send_modal.await_args.args[0], self.page.GroupRemoveModal)

    async def test_boton_listar_envia_la_grafica(self):
        service, _, _, _ = make_service()
        interaction = self.interaction()
        await self.page.GroupPanelView(service).list.callback(interaction)
        self.assertIn("file", interaction.followup.send.await_args.kwargs)

    async def test_los_botones_conservan_sus_ids_para_seguir_funcionando_tras_reiniciar(self):
        service, _, _, _ = make_service()
        ids = {child.custom_id for child in self.page.GroupPanelView(service).children}
        self.assertEqual(ids, {"group_panel:add", "group_panel:search", "group_panel:list", "group_panel:remove"})

    async def test_comandos(self):
        service, players, _, _ = make_service(characters={"Beto": character("Beto")})
        ctx = MagicMock(defer=AsyncMock(), send=AsyncMock())

        add = self.page.AddCommand(MagicMock(), service)
        await self.page.AddCommand.add_player.callback(add, ctx, "Vale", character_name="Beto")
        self.assertIn("beto", players)

        whois = self.page.WhoisCommands(MagicMock(), service)
        await self.page.WhoisCommands.whois_char.callback(whois, ctx, character_name="beto")
        self.assertEqual(ctx.send.await_args.kwargs["embed"].title, "Beto")

        listing = self.page.ListCommands(MagicMock(), service)
        await self.page.ListCommands.list_players.callback(listing, ctx)
        self.assertIn("file", ctx.send.await_args.kwargs)

        remove = self.page.RemoveCommand(MagicMock(), service)
        await self.page.RemoveCommand.remove_player.callback(remove, ctx, character_name="beto")
        self.assertNotIn("beto", players)


if __name__ == "__main__":
    unittest.main()
