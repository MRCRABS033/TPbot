"""#tpbot: administra los personajes del grupo. No sabe nada de Discord."""
from dataclasses import dataclass

from src.api.http import create_session


@dataclass
class AddedMember:
    data: dict
    char_info: dict
    existed: bool  # True si el personaje ya estaba en el grupo y solo se actualizó


class GroupService:
    def __init__(self, api, players, guilds, session_factory=create_session):
        self._api = api
        self._players = players
        self._guilds = guilds
        self._session_factory = session_factory

    async def all(self, guild_id):
        return await self._players.for_guild(guild_id).all()

    async def player_count(self, guild_id):
        return await self._players.for_guild(guild_id).count()

    async def owner_count(self, guild_id):
        return len({d["owner"] for d in (await self.all(guild_id)).values()})

    async def get(self, guild_id, character_name):
        return await self._players.for_guild(guild_id).get(character_name.strip().lower())

    async def team_name(self, guild_id, default):
        """Nombre del equipo en las gráficas: el configurado o, si no hay, `default`."""
        return await self._guilds.team_name(guild_id) or default

    async def by_owner(self, guild_id):
        """{ dueño: [personajes de mayor a menor nivel] }."""
        grouped = {}
        players = await self.all(guild_id)
        for data in sorted(players.values(), key=lambda d: d.get("current_level", 0), reverse=True):
            grouped.setdefault(data["owner"], []).append(data)
        return grouped

    async def add(self, guild_id, owner, character_name):
        """Agrega o actualiza un personaje. Devuelve None si no existe en Tibia.com."""
        owner = owner.strip()
        async with self._session_factory() as session:
            res = await self._api.fetch_player_data(session, character_name.strip())
        if not res or not res.get("character_info"):
            return None

        repository = self._players.for_guild(guild_id)
        char_info = res["character_info"]
        level = char_info.get("level", 0)
        key = res["official_name"].lower()

        # Si el personaje ya existía, se conservan sus niveles históricos y su última muerte conocida
        existing = await repository.get(key) or {}
        data = {
            "char_name": res["official_name"],
            "tibia_link": res["tibia_link"],
            "owner": owner,
            "initial_level": existing.get("initial_level", level),
            "weekly_start_level": existing.get("weekly_start_level", level),
            "current_level": level,
            "vocation": char_info.get("vocation", "N/A"),
        }
        if "last_death_time" in existing:
            data["last_death_time"] = existing["last_death_time"]
        await repository.put(key, data)
        return AddedMember(data, char_info, existed=bool(existing))

    async def remove(self, guild_id, character_name):
        """Devuelve los datos del personaje eliminado, o None si no estaba en el grupo."""
        return await self._players.for_guild(guild_id).remove(character_name.strip().lower())
