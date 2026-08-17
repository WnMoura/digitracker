"""Contratos neutros para plataformas de biblioteca e conquistas.

RetroAchievements permanece o único adaptador funcional. O restante do app
consome capacidades declaradas em vez de nomes específicos de plataforma.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ProviderCapabilities:
    library: bool = False
    achievements: bool = False
    progress: bool = False
    artwork: bool = False


@dataclass
class PlatformGame:
    external_id: str
    title: str
    platform: str = ""
    artwork: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class PlatformProvider(ABC):
    id: str
    label: str
    capabilities: ProviderCapabilities

    @abstractmethod
    def validate(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def list_played_games(self) -> list[PlatformGame]:
        raise NotImplementedError

    @abstractmethod
    def game_progress(self, external_id: str) -> dict:
        raise NotImplementedError


class RetroAchievementsProvider(PlatformProvider):
    id = "retroachievements"
    label = "RetroAchievements"
    capabilities = ProviderCapabilities(
        library=True, achievements=True, progress=True, artwork=True,
    )

    def __init__(self, client):
        self.client = client

    def validate(self) -> None:
        self.client.validate()

    def list_played_games(self) -> list[PlatformGame]:
        games = self.client.get_user_completion_progress()
        output = []
        for game in games or []:
            output.append(PlatformGame(
                external_id=str(game.get("GameID") or game.get("ID") or ""),
                title=str(game.get("Title") or "Jogo"),
                platform=str(game.get("ConsoleName") or ""),
                metadata=dict(game),
            ))
        return output

    def game_progress(self, external_id: str) -> dict:
        return self.client.get_game_info_and_user_progress(int(external_id))


class ProviderRegistry:
    def __init__(self):
        self._providers: dict[str, PlatformProvider] = {}

    def register(self, provider: PlatformProvider) -> None:
        self._providers[provider.id] = provider

    def get(self, provider_id: str) -> PlatformProvider | None:
        return self._providers.get(provider_id)

    def describe(self) -> list[dict]:
        return [{
            "id": provider.id, "label": provider.label,
            "capabilities": provider.capabilities.__dict__,
        } for provider in self._providers.values()]
