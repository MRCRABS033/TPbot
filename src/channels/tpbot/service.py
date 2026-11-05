"""#tpbot: administra los personajes del grupo. No sabe nada de Discord."""
from dataclasses import dataclass

from src.api.http import create_session


@dataclass
class AddedMember:
    data: dict
    char_info: dict
    existed: bool  # True si el personaje ya estaba en el grupo y solo se actualizó


class GroupService:
    def __init__(self, api, players, state, save, session_factory=create_session):
        self._api = api
        self._players = players
        self._state = state
        self._save = save
        self._session_factory = session_factory

    @property
    def players(self):
        return self._players

    def has_players(self):
        return bool(self._players)

    def player_count(self):
        return len(self._players)

    def owner_count(self):
        return len({d["owner"] for d in self._players.values()})

    def get(self, character_name):
        return self._players.get(character_name.strip().lower())

    def by_owner(self):
        """{ dueño: [personajes de mayor a menor nivel] }."""
        grouped = {}
        for data in sorted(self._players.values(), key=lambda d: d.get("current_level", 0), reverse=True):
            grouped.setdefault(data["owner"], []).append(data)
        return grouped

    async def add(self, owner, character_name):
        """Agrega o actualiza un personaje. Devuelve None si no existe en Tibia.com."""
        owner = owner.strip()
        async with self._session_factory() as session:
            res = await self._api.fetch_player_data(session, character_name.strip())
        if not res or not res.get("character_info"):
            return None

        char_info = res["character_info"]
        level = char_info.get("level", 0)
        key = res["official_name"].lower()

        # Si el personaje ya existía, se conservan sus niveles históricos
        existing = self._players.get(key, {})
        self._players[key] = {
            "char_name": res["official_name"],
            "tibia_link": res["tibia_link"],
            "owner": owner,
            "initial_level": existing.get("initial_level", level),
            "weekly_start_level": existing.get("weekly_start_level", level),
            "current_level": level,
            "vocation": char_info.get("vocation", "N/A"),
        }
        self._save()
        return AddedMember(self._players[key], char_info, existed=bool(existing))

    def remove(self, character_name):
        """Devuelve los datos del personaje eliminado, o None si no estaba en el grupo."""
        key = character_name.strip().lower()
        data = self._players.pop(key, None)
        if not data:
            return None
        self._state.pop(key, None)
        self._save()
        return data
