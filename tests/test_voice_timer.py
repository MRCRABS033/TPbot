"""Pruebas del temporizador de voz (sin conectarse a Discord)."""
import asyncio
import os
import unittest
from unittest.mock import AsyncMock, MagicMock

from src.voice.service import build_beep, format_duration, parse_duration


class DurationTests(unittest.TestCase):
    def test_entiende_los_formatos(self):
        self.assertEqual(parse_duration("90s"), 90)
        self.assertEqual(parse_duration("5m"), 300)
        self.assertEqual(parse_duration("5"), 300)  # número solo = minutos
        self.assertEqual(parse_duration("1h30m"), 5400)
        self.assertEqual(parse_duration("1m 30s"), 90)
        self.assertEqual(parse_duration("2 min"), 120)
        self.assertEqual(parse_duration("10seg"), 10)

    def test_rechaza_lo_que_no_es_un_intervalo(self):
        for text in ("", "   ", "abc", "5x", "m5", "-5m"):
            self.assertIsNone(parse_duration(text), text)

    def test_formato_legible(self):
        self.assertEqual(format_duration(5400), "1h 30m")
        self.assertEqual(format_duration(45), "45s")
        self.assertEqual(format_duration(0), "0s")

    def test_pitido_es_pcm_estereo_de_16_bits(self):
        self.assertEqual(len(build_beep()) % 4, 0)


def ctx_for(guild_id=1, in_voice=True):
    author = MagicMock()
    author.voice = MagicMock(channel=MagicMock()) if in_voice else None
    return MagicMock(guild=MagicMock(id=guild_id), author=author, send=AsyncMock(), defer=AsyncMock())


class TimerCommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        os.environ.setdefault("DISCORD_TOKEN", "test")  # config.py lo exige al importarse
        from src.voice import page
        self.page = page
        self.cog = page.VoiceTimer(MagicMock())

    async def run_timer(self, ctx, intervalo=None):
        await self.page.VoiceTimer.timer.callback(self.cog, ctx, intervalo=intervalo)
        return ctx.send.await_args.args[0]

    async def test_stop_sin_temporizador(self):
        self.assertIn("No hay ningún temporizador", await self.run_timer(ctx_for(), "stop"))

    async def test_stop_cancela_el_del_servidor(self):
        task = asyncio.create_task(asyncio.sleep(3600))
        self.cog.timers[1] = task
        self.assertIn("detenido", await self.run_timer(ctx_for(), "parar"))
        self.assertTrue(task.cancelled())
        self.assertNotIn(1, self.cog.timers)

    async def test_exige_estar_en_voz(self):
        self.assertIn("canal de voz", await self.run_timer(ctx_for(in_voice=False), "5m"))

    async def test_no_arranca_dos_en_el_mismo_servidor(self):
        self.cog.starting.add(1)
        self.assertIn("Ya hay un temporizador", await self.run_timer(ctx_for(), "5m"))

    async def test_intervalo_invalido_o_fuera_de_rango(self):
        self.assertIn("No entendí", await self.run_timer(ctx_for(), "abc"))
        self.assertIn("entre", await self.run_timer(ctx_for(), "2s"))
        self.assertIn("entre", await self.run_timer(ctx_for(), "7h"))
        self.assertEqual(self.cog.starting, set())  # siempre se libera el servidor

    async def test_responde_a_tiempo_con_comando_de_barra(self):
        ctx = ctx_for()
        await self.run_timer(ctx, "abc")
        ctx.defer.assert_awaited()


if __name__ == "__main__":
    unittest.main()
