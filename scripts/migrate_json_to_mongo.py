"""
Carga los JSON antiguos en MongoDB para un servidor de Discord. Se usa una sola vez por servidor.

Uso (desde la carpeta del proyecto, con MONGODB_URI en el .env):
    python scripts/migrate_json_to_mongo.py <guild_id> [mundo] [nombre_del_team]
    python scripts/migrate_json_to_mongo.py 123456789 Quidera "Tres Puntos"

Opciones: --players ruta.json  --hunteds ruta.json  (por defecto, los JSON de la raíz; en producción
usa los que envía !respaldo) y --force para reemplazar a un servidor que ya tiene datos.
"""
import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

from src.data.database import Database


def read_json(path):
    if not os.path.exists(path):
        print(f"⚠️  No existe {path}: se omite.")
        return {}
    with open(path, encoding="utf-8") as file:
        return json.load(file)


async def migrate(args):
    database = Database(os.environ["MONGODB_URI"])
    await database.prepare()
    try:
        players = database.players.for_guild(args.guild_id)
        hunteds = database.hunteds.for_guild(args.guild_id)
        if not args.force and (await players.count() or await hunteds.count()):
            print("⛔ Este servidor ya tiene datos en MongoDB. Usa --force si de verdad quieres reemplazarlos.")
            return 1

        group = read_json(args.players)
        hunted = read_json(args.hunteds)
        await players.replace_all(group)
        await hunteds.replace_all(hunted)
        await database.guilds.set_world(args.guild_id, args.world)
        if args.team_name:
            await database.guilds.set_team_name(args.guild_id, args.team_name)
        print(f"✅ Servidor {args.guild_id}: {len(group)} personajes, {len(hunted)} hunteds, mundo {args.world}.")
        return 0
    finally:
        await database.close()


def parse_args():
    parser = argparse.ArgumentParser(description="Carga los JSON antiguos en MongoDB.")
    parser.add_argument("guild_id", type=int)
    parser.add_argument("world", nargs="?", default="Quidera")
    parser.add_argument("team_name", nargs="?", default=None)
    parser.add_argument("--players", default="watched_players.json")
    parser.add_argument("--hunteds", default="hunteds.json")
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    load_dotenv()
    sys.exit(asyncio.run(migrate(parse_args())))
