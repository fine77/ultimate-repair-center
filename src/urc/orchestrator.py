from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

from .config import load_configs
from .executor import run_action
from .ollama_client import OllamaRouter


@dataclass
class AgentResult:
    agent: str
    ok: bool
    output: str
    model_used: str | None = None
    function_used: str | None = None
    structured_output: dict[str, Any] | None = None


class AgentOrchestrator:
    def __init__(self) -> None:
        endpoints, policy, agents, issues, schemas = load_configs()
        self.router = OllamaRouter(endpoints)
        self.model_aliases = policy.get("allowed_models", {})
        self.agents = agents.get("agents", {})
        self.issue_profiles = issues.get("issue_profiles", {})
        self.schemas = schemas

    def _resolve_agent(self, name: str) -> dict[str, Any]:
        cfg = self.agents.get(name)
        if not cfg:
            raise ValueError(f"unknown agent: {name}")
        return cfg

    def _resolve_model(self, alias: str) -> str:
        model = self.model_aliases.get(alias)
        if not model:
            raise ValueError(f"unknown model alias: {alias}")
        return model

    @staticmethod
    def _extract_json_object(text: str) -> dict[str, Any] | None:
        text = text.strip()
        if not text:
            return None
        try:
            payload = json.loads(text)
            return payload if isinstance(payload, dict) else None
        except json.JSONDecodeError:
            return None

    async def ask(self, *, agent: str, task: str, context: str = "", function_name: str | None = None, structured: bool = True, model_alias_chain: list[str] | None = None) -> AgentResult:
        cfg = self._resolve_agent(agent)
        aliases = model_alias_chain or [cfg["model_alias"]]
        models = [self._resolve_model(a) for a in aliases]
        schema = self.schemas.get("default", {})
        if function_name:
            schema = self.schemas.get("agent_function", {}).get(f"{agent}.{function_name}", schema)

        prompt = (
            f"Role: {cfg.get('role','')}.\n"
            f"Task: {task}\n"
            f"Context: {context}\n"
            "Constraints: restore-only; no architecture/policy/firewall/security redesign.\n"
        )
        if structured:
            prompt += "Return ONLY JSON object following schema:\n" + json.dumps(schema, ensure_ascii=True)

        out, model_used = await self.router.chat_with_fallback(models=models, prompt=prompt, temperature=float(cfg.get("temperature", 0.2)), max_tokens=int(cfg.get("max_tokens", 900)))
        parsed = self._extract_json_object(out) if structured else None
        return AgentResult(agent=agent, ok=True, output=out, model_used=model_used, function_used=function_name, structured_output=parsed)

    async def handle_issue_for_agent(self, *, issue_type: str, summary: str, agent: str, context: str = "", structured: bool = True) -> dict[str, Any]:
        profile = self.issue_profiles.get(issue_type) or self.issue_profiles.get("manual_plan")
        if not profile:
            raise ValueError("no issue profiles configured")
        function_name = (profile.get("function_plan") or {}).get(agent)
        aliases = ((profile.get("model_strategy") or {}).get("per_agent_alias_chain") or {}).get(agent) or (profile.get("model_strategy") or {}).get("default_alias_chain")
        result = await self.ask(agent=agent, task=f"IssueType: {issue_type}\nSummary: {summary}", context=context, function_name=function_name, structured=structured, model_alias_chain=aliases)
        return {
            "issue_type": issue_type,
            "summary": summary,
            "selected_agents": [agent],
            "analysis": [{
                "agent": result.agent,
                "ok": result.ok,
                "model_used": result.model_used,
                "function_used": result.function_used,
                "structured_output": result.structured_output,
                "output": result.output,
            }],
            "suggested_executor_action": profile.get("suggested_executor_action"),
        }

    def exec_action(self, action: str, apply: bool = False) -> AgentResult:
        exec_cfg = self._resolve_agent("executor")
        allowed = set(exec_cfg.get("allowed_actions", []))
        if action not in allowed:
            return AgentResult(agent="executor", ok=False, output=f"action '{action}' not allowed")
        code, stdout, stderr = run_action(action=action, apply=apply)
        return AgentResult(agent="executor", ok=(code == 0), output=(stdout + "\n" + stderr).strip())
