"""
Acceso a MongoDB. Los datos de cada servidor de Discord quedan aislados por `guild_id`:
para leer o escribir personajes hay que pedir primero `for_guild(guild_id)`, así no se
puede hacer una consulta que olvide el servidor.
"""
from pymongo import ASCENDING, DeleteOne, ReplaceOne

_INTERNAL_FIELDS = ("_id", "guild_id", "key")


def _clean(document):
    return {field: value for field, value in document.items() if field not in _INTERNAL_FIELDS}


class GuildCharacters:
    """Personajes de un solo servidor, identificados por `key` (nombre en minúsculas)."""

    def __init__(self, collection, guild_id):
        self._collection = collection
        self._guild_id = guild_id

    def _id(self, key):
        return {"guild_id": self._guild_id, "key": key}

    def _document(self, key, data):
        # Los datos pueden venir de un respaldo: nunca deciden a qué servidor o personaje pertenecen
        return {**_clean(data), **self._id(key)}

    async def all(self):
        """{ key: datos }"""
        documents = await self._collection.find({"guild_id": self._guild_id}).to_list(None)
        return {document["key"]: _clean(document) for document in documents}

    async def get(self, key):
        document = await self._collection.find_one(self._id(key))
        return _clean(document) if document else None

    async def count(self):
        return await self._collection.count_documents({"guild_id": self._guild_id})

    async def put(self, key, data):
        """Crea el personaje o reemplaza todos sus datos."""
        await self._collection.replace_one(self._id(key), self._document(key, data), upsert=True)

    async def update(self, key, **fields):
        """Cambia solo esos campos. Si el personaje ya no está, no lo vuelve a crear."""
        await self._collection.update_one(self._id(key), {"$set": fields})

    async def remove(self, key):
        """Devuelve los datos del personaje eliminado, o None si no estaba."""
        document = await self._collection.find_one_and_delete(self._id(key))
        return _clean(document) if document else None

    async def replace_all(self, characters):
        """Deja exactamente estos personajes. Primero escribe y luego borra, para no quedar sin datos si algo falla."""
        writes = [ReplaceOne(self._id(key), self._document(key, data), upsert=True) for key, data in characters.items()]
        current = await self.all()
        writes += [DeleteOne(self._id(key)) for key in current if key not in characters]
        if writes:
            await self._collection.bulk_write(writes)


class CharacterRepository:
    def __init__(self, collection):
        self._collection = collection

    async def create_indexes(self):
        await self._collection.create_index([("guild_id", ASCENDING), ("key", ASCENDING)], unique=True)

    def for_guild(self, guild_id):
        return GuildCharacters(self._collection, guild_id)


class GuildRepository:
    """Configuración de cada servidor: { guild_id, world, team_name (opcional) }."""

    def __init__(self, collection):
        self._collection = collection

    async def create_indexes(self):
        await self._collection.create_index("guild_id", unique=True)

    async def world(self, guild_id):
        """Mundo de Tibia del servidor, o None si todavía no lo configuró."""
        document = await self._collection.find_one({"guild_id": guild_id})
        return document.get("world") if document else None

    async def worlds(self):
        """{ guild_id: mundo } de los servidores configurados."""
        documents = await self._collection.find({"world": {"$ne": None}}).to_list(None)
        return {document["guild_id"]: document["world"] for document in documents}

    async def set_world(self, guild_id, world):
        await self._collection.update_one({"guild_id": guild_id}, {"$set": {"world": world}}, upsert=True)

    async def team_name(self, guild_id):
        document = await self._collection.find_one({"guild_id": guild_id})
        return document.get("team_name") if document else None

    async def set_team_name(self, guild_id, team_name):
        await self._collection.update_one({"guild_id": guild_id}, {"$set": {"team_name": team_name}}, upsert=True)
