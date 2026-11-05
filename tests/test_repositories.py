"""
Pruebas de los repositorios contra un MongoDB real, en una base aparte (tibia_test) que se borra al terminar.
Se omiten si no hay MongoDB en localhost:27017 (o en TEST_MONGODB_URI).
"""
import os
import unittest

from pymongo import AsyncMongoClient
from pymongo.errors import DuplicateKeyError, PyMongoError

from src.data.database import Database

URI = os.environ.get("TEST_MONGODB_URI", "mongodb://localhost:27017/tibia_test?directConnection=true")
GUILD_A, GUILD_B = 1557247827065835601, 1557247827065835602  # IDs de 64 bits, como los de Discord


class RepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.database = Database(URI)
        self.assertEqual(self.database._db.name, "tibia_test", "Las pruebas no deben tocar otra base")
        try:
            await self.database._client.drop_database("tibia_test")
            await self.database.prepare()
        except PyMongoError as error:
            await self.database.close()
            self.skipTest(f"Sin MongoDB de pruebas: {error}")
        self.a = self.database.players.for_guild(GUILD_A)
        self.b = self.database.players.for_guild(GUILD_B)

    async def asyncTearDown(self):
        await self.database._client.drop_database("tibia_test")
        await self.database.close()

    async def test_los_servidores_no_ven_los_datos_de_otros(self):
        await self.a.put("ana", {"char_name": "Ana", "owner": "Vale"})
        await self.b.put("ana", {"char_name": "Ana", "owner": "Luis"})
        await self.b.put("beto", {"char_name": "Beto", "owner": "Luis"})

        self.assertEqual(await self.a.all(), {"ana": {"char_name": "Ana", "owner": "Vale"}})
        self.assertEqual(await self.a.count(), 1)
        self.assertIsNone(await self.a.get("beto"))

        await self.a.update("beto", owner="Intruso")
        self.assertEqual((await self.b.get("beto"))["owner"], "Luis")
        self.assertIsNone(await self.a.remove("beto"))
        self.assertEqual(await self.b.count(), 2)

        await self.a.remove("ana")
        self.assertEqual((await self.b.get("ana"))["owner"], "Luis")

    async def test_datos_devueltos_sin_campos_internos(self):
        await self.a.put("ana", {"char_name": "Ana", "last_death_time": None})
        self.assertEqual(await self.a.get("ana"), {"char_name": "Ana", "last_death_time": None})

    async def test_put_reemplaza_y_update_solo_cambia_esos_campos(self):
        await self.a.put("ana", {"char_name": "Ana", "level": 1, "world": "Antica"})
        await self.a.update("ana", level=2)
        self.assertEqual(await self.a.get("ana"), {"char_name": "Ana", "level": 2, "world": "Antica"})
        await self.a.put("ana", {"char_name": "Ana"})
        self.assertEqual(await self.a.get("ana"), {"char_name": "Ana"})

    async def test_update_no_revive_a_un_personaje_eliminado(self):
        await self.a.update("ana", level=2)
        self.assertIsNone(await self.a.get("ana"))

    async def test_remove_devuelve_los_datos(self):
        await self.a.put("ana", {"char_name": "Ana"})
        self.assertEqual(await self.a.remove("ana"), {"char_name": "Ana"})
        self.assertIsNone(await self.a.remove("ana"))

    async def test_replace_all_solo_afecta_a_ese_servidor(self):
        await self.a.put("vieja", {"char_name": "Vieja"})
        await self.a.put("ana", {"char_name": "Ana", "level": 1})
        await self.b.put("otra", {"char_name": "Otra"})
        await self.a.replace_all({"ana": {"char_name": "Ana", "level": 9}, "nuevo": {"char_name": "Nuevo"}})
        self.assertEqual(set(await self.a.all()), {"ana", "nuevo"})
        self.assertEqual((await self.a.get("ana"))["level"], 9)
        self.assertEqual(set(await self.b.all()), {"otra"})

    async def test_replace_all_vacio_borra_los_de_ese_servidor(self):
        await self.a.put("ana", {"char_name": "Ana"})
        await self.a.replace_all({})
        self.assertEqual(await self.a.count(), 0)
        await self.a.replace_all({})  # Sin nada que escribir tampoco falla

    async def test_un_respaldo_manipulado_no_escribe_en_otro_servidor(self):
        await self.b.put("beto", {"char_name": "Beto"})
        await self.a.replace_all({"ana": {"char_name": "Ana", "guild_id": GUILD_B, "key": "beto", "_id": "x"}})
        await self.a.put("caro", {"char_name": "Caro", "guild_id": GUILD_B})
        self.assertEqual(await self.b.all(), {"beto": {"char_name": "Beto"}})
        self.assertEqual(set(await self.a.all()), {"ana", "caro"})

    async def test_el_indice_impide_duplicados_en_un_servidor(self):
        collection = self.database._db["players"]
        await collection.insert_one({"guild_id": GUILD_A, "key": "ana"})
        await collection.insert_one({"guild_id": GUILD_B, "key": "ana"})
        with self.assertRaises(DuplicateKeyError):
            await collection.insert_one({"guild_id": GUILD_A, "key": "ana"})

    async def test_players_y_hunteds_estan_separados(self):
        await self.a.put("ana", {"char_name": "Ana"})
        self.assertEqual(await self.database.hunteds.for_guild(GUILD_A).count(), 0)

    async def test_configuracion_del_servidor(self):
        guilds = self.database.guilds
        self.assertIsNone(await guilds.world(GUILD_A))
        self.assertIsNone(await guilds.team_name(GUILD_A))
        await guilds.set_team_name(GUILD_B, "Tres Puntos")
        self.assertEqual(await guilds.worlds(), {})  # Tener team no es estar configurado
        await guilds.set_world(GUILD_A, "Antica")
        await guilds.set_world(GUILD_A, "Quidera")
        await guilds.set_world(GUILD_B, "Antica")
        self.assertEqual(await guilds.worlds(), {GUILD_A: "Quidera", GUILD_B: "Antica"})
        self.assertEqual(await guilds.team_name(GUILD_B), "Tres Puntos")
        self.assertEqual(await guilds.world(GUILD_B), "Antica")

    async def test_cierre_de_semana_por_servidor(self):
        guilds = self.database.guilds
        await guilds.set_world(GUILD_A, "Quidera")
        self.assertIsNone(await guilds.week_closed_on(GUILD_A))
        await guilds.set_week_closed_on(GUILD_A, "2026-11-08")
        self.assertEqual(await guilds.week_closed_on(GUILD_A), "2026-11-08")
        self.assertIsNone(await guilds.week_closed_on(GUILD_B))
        self.assertEqual(await guilds.world(GUILD_A), "Quidera")  # No pisa lo demás


class IsolationTests(unittest.TestCase):
    def test_la_uri_de_pruebas_no_apunta_a_la_base_real(self):
        self.assertEqual(AsyncMongoClient(URI, connect=False).get_default_database().name, "tibia_test")


if __name__ == "__main__":
    unittest.main()
