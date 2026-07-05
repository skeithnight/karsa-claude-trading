"""Agent Registry — tracks available agent types and their config."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class AgentConfig:
    agent_type: str
    max_retries: int = 3
    timeout_seconds: float = 120.0
    combo_name: str = ""  # 9Router combo name


class AgentRegistry:
    """Registry of agent types and their configurations."""

    def __init__(self):
        self._agents: Dict[str, AgentConfig] = {}

    def register(self, config: AgentConfig):
        self._agents[config.agent_type] = config

    def get(self, agent_type: str) -> Optional[AgentConfig]:
        return self._agents.get(agent_type)

    def all_types(self) -> list[str]:
        return list(self._agents.keys())
