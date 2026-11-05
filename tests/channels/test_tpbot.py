"""Pruebas de #tpbot sin internet ni Discord."""
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from src.channels.tpbot.service import GroupService
from tests.fakes import GUILD_ID, FakeCharacterRepository, FakeGuildRepository, FakeSession


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


def make_service(players=None, characters=None, other_guilds=None, team_names=None):
    players = players if players is not None else {"ana": member()}
    guilds = FakeGuildRepository(team_names=team_names)
    service = GroupService(FakeApi(characters), FakeCharacterRepository(players, other_guilds), guilds, session_factory=FakeSession)
    return service, players


class GroupServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_agregar_nuevo(self):
        service, players = make_service({}, {"ana": character()})
        added = await service.add(GUILD_ID, " Vale ", " ana ")
        self.assertFalse(added.existed)
        self.assertEqual(players["ana"]["owner"], "Vale")
        self.assertEqual(players["ana"]["initial_level"], 120)
        self.assertEqual(players["ana"]["weekly_start_level"], 120)
        self.assertNotIn("last_death_time", players["ana"])  # La primera revisión la registra sin avisar

    async def test_agregar_existente_conserva_niveles_historicos_y_ultima_muerte(self):
        service, players = make_service({"ana": {**member(), "last_death_time": "t1"}}, {"Ana": character(level=130)})
        added = await service.add(GUILD_ID, "Otro", "Ana")
        self.assertTrue(added.existed)
        self.assertEqual(players["ana"]["initial_level"], 100)
        self.assertEqual(players["ana"]["weekly_start_level"], 110)
        self.assertEqual(players["ana"]["current_level"], 130)
        self.assertEqual(players["ana"]["owner"], "Otro")
        self.assertEqual(players["ana"]["last_death_time"], "t1")

    async def test_agregar_no_encontrado(self):
        service, _ = make_service()
        self.assertIsNone(await service.add(GUILD_ID, "Vale", "Nadie"))

    async def test_el_mismo_personaje_es_independiente_en_cada_servidor(self):
        other = {"ana": member(owner="Luis", initial=50)}
        service, players = make_service(characters={"Ana": character(level=130)}, other_guilds={2: other})
        await service.add(GUILD_ID, "Vale", "Ana")
        self.assertEqual(other["ana"]["owner"], "Luis")
        self.assertEqual(other["ana"]["initial_level"], 50)

    async def test_eliminar(self):
        service, players = make_service()
        self.assertEqual((await service.remove(GUILD_ID, " ANA "))["char_name"], "Ana")
        self.assertEqual(players, {})
        self.assertIsNone(await service.remove(GUILD_ID, "Nadie"))

    async def test_agrupa_por_dueno_de_mayor_a_menor_nivel(self):
        players = {"a": member("A", "Vale", 10), "b": member("B", "Luis", 30), "c": member("C", "Vale", 20)}
        service, _ = make_service(players)
        grouped = await service.by_owner(GUILD_ID)
        self.assertEqual([d["char_name"] for d in grouped["Vale"]], ["C", "A"])
        self.assertEqual(await service.owner_count(GUILD_ID), 2)

    async def test_nombre_del_equipo(self):
        service, _ = make_service(team_names={GUILD_ID: "Tres Puntos"})
        self.assertEqual(await service.team_name(GUILD_ID, "Hello"), "Tres Puntos")
        self.assertEqual(await service.team_name(2, "Otro servidor"), "Otro servidor")


class PageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        os.environ.setdefault("DISCORD_TOKEN", "test")  # config.py lo exige al importarse
        from src.channels.tpbot import page
        self.page = page
        self.guild = MagicMock(id=GUILD_ID)
        self.guild.name = "Hello"

    def interaction(self):
        return MagicMock(
            guild_id=GUILD_ID,
            guild=self.guild,
            response=MagicMock(defer=AsyncMock(), send_message=AsyncMock(), send_modal=AsyncMock()),
            followup=MagicMock(send=AsyncMock()),
        )

    async def test_embed_agregado_y_actualizado(self):
        service, _ = make_service({}, {"Ana": character()})
        first = await self.page.add_group_member(service, GUILD_ID, " Vale ", "Ana")
        second = await self.page.add_group_member(service, GUILD_ID, "Vale", "Ana")
        self.assertEqual(first.title, "Ana agregado al grupo")
        self.assertEqual(first.fields[0].value, "Vale")
        self.assertEqual(first.footer.text, "Mundo: Quidera")
        self.assertEqual(second.title, "Ana actualizado")

    async def test_embed_no_encontrado(self):
        service, _ = make_service()
        self.assertIn("No se encontró", (await self.page.add_group_member(service, GUILD_ID, "V", "Nadie")).description)

    async def test_ficha_con_progreso(self):
        service, _ = make_service()
        embed = await self.page.build_group_member_embed(service, GUILD_ID, "ana")
        self.assertEqual(embed.fields[3].value, "Esta semana: **+10** niveles\nDesde que ingresó: **+20** niveles")

    async def test_lista_vacia(self):
        service, _ = make_service({})
        self.assertIn("La lista está vacía", (await self.page.build_group_list_embed(service, GUILD_ID)).description)
        embed, chart = await self.page.build_group_progress(service, self.guild)
        self.assertIsNone(chart)
        self.assertEqual(embed.title, "Personajes del grupo")

    async def test_grafica_real_con_fuentes_del_proyecto(self):
        players = {"a": member("Ana", "Vale"), "b": member("Beto", "Luis", 300, 300), "c": member("Caro", "Vale", 50, 90)}
        service, _ = make_service(players)
        embed, chart = await self.page.build_group_progress(service, self.guild)
        self.assertTrue(chart.fp.read(8).startswith(b"\x89PNG"))
        self.assertEqual(embed.image.url, f"attachment://{chart.filename}")
        self.assertEqual(embed.footer.text, "Total: 3 personajes · 2 dueños")

    async def test_la_grafica_usa_el_equipo_del_servidor(self):
        service, _ = make_service(team_names={GUILD_ID: "Tres Puntos"})
        with patch.object(self.page, "build_group_progress_chart", return_value=None) as chart:
            await self.page.build_group_progress(service, self.guild)
        self.assertEqual(chart.call_args.args[1], "Tres Puntos")

        service, _ = make_service()
        with patch.object(self.page, "build_group_progress_chart", return_value=None) as chart:
            await self.page.build_group_progress(service, self.guild)
        self.assertEqual(chart.call_args.args[1], "Hello")

    async def test_el_loop_publica_el_panel_al_final(self):
        service, _ = make_service()
        cog = self.page.Panel.__new__(self.page.Panel)  # Sin __init__: no arranca la tarea
        cog.bot, cog.service, cog.group_list_message = MagicMock(guilds=[self.guild]), service, {}
        cog.guilds = FakeGuildRepository()
        with patch.object(self.page, "send_or_update_embed", new=AsyncMock()) as send:
            await self.page.Panel.update_group_list.coro(cog)
        args, kwargs = send.await_args
        self.assertEqual(args[3], self.page.CHANNEL_TPBOT)
        self.assertEqual(args[4].footer.text, "1 personajes · 1 dueños")
        self.assertTrue(kwargs["keep_last"])
        self.assertIsInstance(kwargs["view"], self.page.GroupPanelView)

    async def test_formularios(self):
        service, players = make_service({}, {"Beto": character("Beto")})
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
        service, players = make_service()
        view = self.page.GroupPanelView(service)
        interaction = self.interaction()
        await view.remove.callback(interaction)
        select = interaction.response.send_message.await_args.kwargs["view"].children[0]
        self.assertEqual((await select.remove_func("ana")).title, "Ana eliminado del grupo")

        interaction = self.interaction()
        await view.remove.callback(interaction)
        self.assertIn("La lista está vacía", interaction.response.send_message.await_args.kwargs["embed"].description)

        players.update({f"p{i}": member(f"P{i}") for i in range(26)})
        interaction = self.interaction()
        await view.remove.callback(interaction)
        self.assertIsInstance(interaction.response.send_modal.await_args.args[0], self.page.GroupRemoveModal)

    async def test_boton_listar_envia_la_grafica(self):
        service, _ = make_service()
        interaction = self.interaction()
        await self.page.GroupPanelView(service).list.callback(interaction)
        self.assertIn("file", interaction.followup.send.await_args.kwargs)

    async def test_los_botones_conservan_sus_ids_para_seguir_funcionando_tras_reiniciar(self):
        service, _ = make_service()
        ids = {child.custom_id for child in self.page.GroupPanelView(service).children}
        self.assertEqual(ids, {"group_panel:add", "group_panel:search", "group_panel:list", "group_panel:remove"})

    async def test_comandos(self):
        service, players = make_service(characters={"Beto": character("Beto")})
        guilds = FakeGuildRepository()
        ctx = MagicMock(defer=AsyncMock(), send=AsyncMock(), guild=self.guild)

        add = self.page.AddCommand(MagicMock(), service, guilds)
        await self.page.AddCommand.add_player.callback(add, ctx, "Vale", character_name="Beto")
        self.assertIn("beto", players)

        whois = self.page.WhoisCommands(MagicMock(), service, guilds)
        await self.page.WhoisCommands.whois_char.callback(whois, ctx, character_name="beto")
        self.assertEqual(ctx.send.await_args.kwargs["embed"].title, "Beto")

        listing = self.page.ListCommands(MagicMock(), service, guilds)
        await self.page.ListCommands.list_players.callback(listing, ctx)
        self.assertIn("file", ctx.send.await_args.kwargs)

        remove = self.page.RemoveCommand(MagicMock(), service, guilds)
        await self.page.RemoveCommand.remove_player.callback(remove, ctx, character_name="beto")
        self.assertNotIn("beto", players)

    async def test_comando_sin_configurar_pide_config(self):
        service, players = make_service({}, {"Beto": character("Beto")})
        ctx = MagicMock(defer=AsyncMock(), send=AsyncMock(), guild=self.guild)
        add = self.page.AddCommand(MagicMock(), service, FakeGuildRepository({}))
        await self.page.AddCommand.add_player.callback(add, ctx, "Vale", character_name="Beto")
        self.assertIn("/config", ctx.send.await_args.args[0])
        self.assertEqual(players, {})


if __name__ == "__main__":
    unittest.main()
