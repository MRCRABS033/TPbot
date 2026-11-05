"""Reemplazos en memoria de MongoDB y de las sesiones HTTP para las pruebas."""
import copy

GUILD_ID = 1


class FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeGuildCharacters:
    """Igual que GuildCharacters: lo que devuelve son copias, como al leer de la base."""

    def __init__(self, characters):
        self.characters = characters

    async def all(self):
        return copy.deepcopy(self.characters)

    async def get(self, key):
        return copy.deepcopy(self.characters.get(key))

    async def count(self):
        return len(self.characters)

    async def put(self, key, data):
        self.characters[key] = copy.deepcopy(data)

    async def update(self, key, **fields):
        if key in self.characters:
            self.characters[key].update(fields)

    async def remove(self, key):
        return self.characters.pop(key, None)

    async def replace_all(self, characters):
        self.characters.clear()
        self.characters.update(copy.deepcopy(characters))


class FakeCharacterRepository:
    """`characters` son los personajes de GUILD_ID. Se pueden seguir revisando en el dict original."""

    def __init__(self, characters=None, other_guilds=None):
        self._by_guild = {GUILD_ID: characters if characters is not None else {}, **(other_guilds or {})}

    def for_guild(self, guild_id):
        return FakeGuildCharacters(self._by_guild.setdefault(guild_id, {}))


class FakeGuildRepository:
    def __init__(self, worlds=None, team_names=None):
        self._worlds = worlds if worlds is not None else {GUILD_ID: "Quidera"}
        self._team_names = team_names or {}
        self._weeks_closed = {}

    async def world(self, guild_id):
        return self._worlds.get(guild_id)

    async def worlds(self):
        return dict(self._worlds)

    async def set_world(self, guild_id, world):
        self._worlds[guild_id] = world

    async def week_closed_on(self, guild_id):
        return self._weeks_closed.get(guild_id)

    async def set_week_closed_on(self, guild_id, date):
        self._weeks_closed[guild_id] = date

    async def team_name(self, guild_id):
        return self._team_names.get(guild_id)

    async def set_team_name(self, guild_id, team_name):
        self._team_names[guild_id] = team_name


class FakeDatabase:
    def __init__(self, players=None, hunteds=None, worlds=None):
        self.players = FakeCharacterRepository(players)
        self.hunteds = FakeCharacterRepository(hunteds)
        self.guilds = FakeGuildRepository(worlds)
