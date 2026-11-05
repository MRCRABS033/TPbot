"""Conexión a MongoDB. El nombre de la base sale de la URI (mongodb://host:puerto/nombre)."""
from pymongo import AsyncMongoClient

from src.data.repositories import CharacterRepository, GuildRepository


class Database:
    def __init__(self, uri):
        self._client = AsyncMongoClient(uri, serverSelectionTimeoutMS=5000)
        self._db = self._client.get_default_database()
        self.players = CharacterRepository(self._db["players"])
        self.hunteds = CharacterRepository(self._db["hunteds"])
        self.guilds = GuildRepository(self._db["guilds"])

    async def prepare(self):
        """Comprueba la conexión y crea los índices. Falla enseguida si la base no responde."""
        await self._client.admin.command("ping")
        for repository in (self.players, self.hunteds, self.guilds):
            await repository.create_indexes()

    async def close(self):
        await self._client.close()
