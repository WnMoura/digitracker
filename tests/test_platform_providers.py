from __future__ import annotations

import json

import platform_providers as providers


class Client:
    def validate(self): self.validated = True
    def get_user_completion_progress(self):
        return [{"GameID": 7, "Title": "Jogo", "ConsoleName": "Console"}]
    def get_game_info_and_user_progress(self, game_id): return {"ID": game_id}


def test_retroachievements_implementa_contrato_neutro():
    provider = providers.RetroAchievementsProvider(Client())
    game = provider.list_played_games()[0]
    assert game.external_id == "7" and game.platform == "Console"
    assert provider.capabilities.achievements is True


def test_registry_expõe_capacidades_sem_credenciais():
    registry = providers.ProviderRegistry()
    registry.register(providers.RetroAchievementsProvider(Client()))
    description = registry.describe()[0]
    assert description["id"] == "retroachievements"
    assert "api_key" not in json.dumps(description)
