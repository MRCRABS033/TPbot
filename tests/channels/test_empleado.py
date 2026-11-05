"""Pruebas de #empleado-del-mes sin Discord."""
import os
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from src.channels.empleado.service import EmpleadoService, is_server_save, is_weekly_reset_day

SUNDAY_SAVE = datetime(2026, 11, 8, 10, 0)
SUNDAY_LATER = datetime(2026, 11, 8, 10, 1)
MONDAY_SAVE = datetime(2026, 11, 9, 10, 0)


def player(name, level, start=None, owner="Vale"):
    data = {"char_name": name, "owner": owner, "current_level": level}
    if start is not None:
        data["weekly_start_level"] = start
    return data


def make_players(count):
    return {f"p{i}": player(f"P{i}", 100 + i, start=100) for i in range(count)}


class EmpleadoServiceTests(unittest.TestCase):
    def test_ranking_ordena_por_niveles_ganados(self):
        players = {"a": player("Ana", 110, 100), "b": player("Beto", 300, 280), "c": player("Caro", 90, 90)}
        ranking = EmpleadoService(players, MagicMock()).ranking()
        self.assertEqual([(e.char, e.gained) for e in ranking], [("Beto", 20), ("Ana", 10), ("Caro", 0)])

    def test_niveles_perdidos_cuentan_cero_y_sin_inicio_tambien(self):
        players = {"a": player("Ana", 90, 100), "b": player("Beto", 200)}
        self.assertEqual([e.gained for e in EmpleadoService(players, MagicMock()).ranking()], [0, 0])

    def test_ranking_no_modifica_nada(self):
        players = {"a": player("Ana", 110, 100)}
        save = MagicMock()
        EmpleadoService(players, save).ranking()
        self.assertEqual(players["a"]["weekly_start_level"], 100)
        save.assert_not_called()

    def test_cerrar_semana_devuelve_ganador_reinicia_y_guarda(self):
        players = {"a": player("Ana", 110, 100), "b": player("Beto", 300, 280)}
        save = MagicMock()
        service = EmpleadoService(players, save)
        self.assertEqual(service.close_week()[0].char, "Beto")
        self.assertEqual(players["a"]["weekly_start_level"], 110)
        self.assertEqual(players["b"]["weekly_start_level"], 300)
        save.assert_called_once()
        self.assertEqual([e.gained for e in service.ranking()], [0, 0])

    def test_hora_de_server_save(self):
        self.assertTrue(is_server_save(SUNDAY_SAVE))
        self.assertFalse(is_server_save(SUNDAY_LATER))
        self.assertTrue(is_weekly_reset_day(SUNDAY_SAVE))
        self.assertFalse(is_weekly_reset_day(MONDAY_SAVE))


class PageTests(unittest.IsolatedAsyncioTestCase):
    def make_cog(self, players, now=SUNDAY_SAVE):
        os.environ.setdefault("DISCORD_TOKEN", "test")  # config.py lo exige al importarse
        from src.channels.empleado import page

        self.save = MagicMock()
        cog = page.WatchedPlayers.__new__(page.WatchedPlayers)  # Sin __init__: no arrancan las tareas
        cog.bot = MagicMock()
        cog.service = EmpleadoService(players, self.save, now=lambda: now)
        cog.ranking_messages, cog.winner_messages = {}, {}
        return cog, page

    async def test_podio_con_tres_y_resto_del_ranking(self):
        cog, _ = self.make_cog(make_players(5))
        embed = await cog._build_ranking_embed()
        self.assertIn("P3", embed.fields[0].value)  # 2.º a la izquierda
        self.assertIn("P4", embed.fields[1].value)  # 1.º al centro
        self.assertIn("P2", embed.fields[2].value)  # 3.º a la derecha
        self.assertEqual(embed.fields[4].name, "Resto del ranking")
        self.assertIn("`4.` **P1**", embed.fields[4].value)

    async def test_sin_podio_con_menos_de_tres(self):
        cog, _ = self.make_cog(make_players(2))
        embed = await cog._build_ranking_embed()
        self.assertEqual(len(embed.fields), 2)
        self.assertIn("🥇", embed.fields[0].value)
        self.assertIn("P1", embed.fields[0].value)

    async def test_resto_largo_se_reparte_en_campos_validos(self):
        cog, _ = self.make_cog(make_players(40))
        embed = await cog._build_ranking_embed()
        self.assertTrue(all(len(f.value) <= 1024 for f in embed.fields))

    async def test_comando_sin_personajes(self):
        cog, page = self.make_cog({})
        ctx = MagicMock(send=AsyncMock())
        await page.WatchedPlayers.show_weekly_top.callback(cog, ctx)
        ctx.send.assert_awaited_once_with("📋 La lista de seguimiento está vacía.")

    async def test_anuncio_sin_canal_no_reinicia_la_semana(self):
        players = {"a": player("Ana", 110, 100)}
        cog, page = self.make_cog(players)
        with patch.object(page, "find_channel", return_value=None):
            await cog.announce_weekly_winner()
        self.assertEqual(players["a"]["weekly_start_level"], 100)
        self.save.assert_not_called()

    async def test_anuncio_publica_al_ganador_y_guarda_el_mensaje(self):
        players = {"a": player("Ana", 110, 100), "b": player("Beto", 300, 280)}
        cog, page = self.make_cog(players)
        channel = MagicMock(send=AsyncMock(return_value=MagicMock(id=99)), guild=MagicMock(id=1))
        with patch.object(page, "find_channel", return_value=channel):
            await cog.announce_weekly_winner()
        kwargs = channel.send.await_args.kwargs
        self.assertEqual(kwargs["content"], "@everyone")
        self.assertIn("Beto", kwargs["embed"].description)
        self.assertEqual(kwargs["embed"].fields[0].value, "**+20 Niveles**")
        self.assertEqual(cog.winner_messages, {1: 99})

    async def test_el_loop_solo_cierra_la_semana_el_domingo_en_server_save(self):
        for now, expected in ((SUNDAY_SAVE, 1), (SUNDAY_LATER, 0), (MONDAY_SAVE, 0)):
            cog, page = self.make_cog(make_players(1), now=now)
            cog.announce_weekly_winner = AsyncMock()
            with patch.object(page.asyncio, "sleep", new=AsyncMock()):
                await page.WatchedPlayers.check_server_save.coro(cog)
            self.assertEqual(cog.announce_weekly_winner.await_count, expected, now)


if __name__ == "__main__":
    unittest.main()
