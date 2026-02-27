from __future__ import annotations

import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
CONFIG_DIR = BASE_DIR / "configs"


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f) or {}


def load_configs() -> tuple[dict, dict, dict, dict, dict]:
    endpoints = load_json(CONFIG_DIR / "ollama_endpoints.json")
    policy = load_json(CONFIG_DIR / "model_policy.json")
    agents = load_json(CONFIG_DIR / "agent_profiles.json")
    issues = load_json(CONFIG_DIR / "issue_profiles.json")
    schemas = load_json(CONFIG_DIR / "response_schemas.json")
    return endpoints, policy, agents, issues, schemas
