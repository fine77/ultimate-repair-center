from __future__ import annotations

import asyncio
import ast
import json
import re
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

    @staticmethod
    def _default_function_for_agent(agent: str, functions: dict[str, Any]) -> str | None:
        preferred: dict[str, str] = {
            "sre_diagnoser": "build_restore_plan",
            "security_analyst": "classify_signal",
            "performance_analyst": "find_bottleneck",
            "documentarian": "build_incident_summary",
            "executor": "run_whitelisted_action",
        }
        pref = preferred.get(agent)
        if pref and pref in functions:
            return pref
        if functions:
            return next(iter(functions.keys()))
        return None

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
    def _extract_balanced_json_objects(text: str) -> list[str]:
        objs: list[str] = []
        depth = 0
        start = -1
        in_str = False
        esc = False
        for i, ch in enumerate(text):
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == "\"":
                    in_str = False
                continue
            if ch == "\"":
                in_str = True
                continue
            if ch == "{":
                if depth == 0:
                    start = i
                depth += 1
                continue
            if ch == "}":
                if depth > 0:
                    depth -= 1
                    if depth == 0 and start != -1:
                        objs.append(text[start : i + 1])
                        start = -1
        return objs

    @staticmethod
    def _parse_loose_json_object(text: str) -> dict[str, Any] | None:
        candidate = (text or "").strip()
        if not candidate:
            return None

        def _as_object(payload: Any) -> dict[str, Any] | None:
            if isinstance(payload, dict):
                return payload
            if isinstance(payload, list):
                for item in payload:
                    if isinstance(item, dict):
                        return item
            if isinstance(payload, str):
                nested = payload.strip()
                if nested and nested != candidate:
                    return AgentOrchestrator._parse_loose_json_object(nested)
            return None

        try:
            parsed = json.loads(candidate)
            as_obj = _as_object(parsed)
            if as_obj is not None:
                return as_obj
        except json.JSONDecodeError:
            pass

        normalized = candidate
        normalized = re.sub(r"^\s*```(?:json)?\s*", "", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"\s*```\s*$", "", normalized, flags=re.IGNORECASE)
        normalized = normalized.replace("\u201c", "\"").replace("\u201d", "\"")
        normalized = normalized.replace("\u2018", "'").replace("\u2019", "'")
        normalized = re.sub(r",(\s*[}\]])", r"\1", normalized)
        try:
            parsed = json.loads(normalized)
            as_obj = _as_object(parsed)
            if as_obj is not None:
                return as_obj
        except json.JSONDecodeError:
            pass

        try:
            lit = ast.literal_eval(normalized)
            as_obj = _as_object(lit)
            if as_obj is not None:
                return as_obj
        except Exception:
            pass

        return None

    @staticmethod
    def _extract_json_object(text: str) -> dict[str, Any] | None:
        text = text.strip()
        if not text:
            return None
        parsed_root = AgentOrchestrator._parse_loose_json_object(text)
        if parsed_root is not None:
            return parsed_root

        for m in re.finditer(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL | re.IGNORECASE):
            parsed = AgentOrchestrator._parse_loose_json_object(m.group(1))
            if parsed is not None:
                return parsed

        for candidate in AgentOrchestrator._extract_balanced_json_objects(text):
            parsed = AgentOrchestrator._parse_loose_json_object(candidate)
            if parsed is not None:
                return parsed
        return None

    @staticmethod
    def _default_for_schema(schema: Any) -> Any:
        if isinstance(schema, dict):
            return {k: AgentOrchestrator._default_for_schema(v) for k, v in schema.items()}
        if isinstance(schema, list):
            return []
        if isinstance(schema, str):
            token = schema.lower()
            if token == "boolean":
                return False
            if token == "0.0-1.0":
                return 0.5
            if token == "string":
                return ""
            if "|" in token:
                first = token.split("|")[0].strip()
                return first
            return ""
        return None

    def _synthesize_structured_fallback(
        self, *, function_name: str | None, schema: dict[str, Any], issue_type: str
    ) -> dict[str, Any] | None:
        base = self._default_for_schema(schema)
        if not isinstance(base, dict):
            return None
        fn = (function_name or "").strip()
        issue = issue_type.strip() or "manual_plan"
        if not fn:
            if "summary" in base and not base.get("summary"):
                base["summary"] = f"Fallback structured output for {issue}"
            if "risk" in base and not base.get("risk"):
                base["risk"] = "medium"
            if "confidence" in base and not base.get("confidence"):
                base["confidence"] = 0.4
            return base

        if fn == "build_restore_plan":
            if "ordered_actions" in base and isinstance(base.get("ordered_actions"), list):
                base["ordered_actions"] = [
                    {
                        "step": f"Re-check {issue} via baseline restore chain",
                        "target": "dns->sni->router->endpoint",
                        "validation": "Observed and expected endpoint behavior aligned",
                    }
                ]
            if "rollback_hint" in base:
                base["rollback_hint"] = "Restore-only rollback of latest endpoint delta if needed."
            if "risk_class" in base:
                base["risk_class"] = "medium"
            return base

        if fn == "build_cmdb_payload":
            if "change_summary" in base:
                base["change_summary"] = f"Fallback CMDB payload for {issue} after invalid model JSON."
            if "task_lines" in base and isinstance(base.get("task_lines"), list):
                base["task_lines"] = [
                    "Validate contract chain and baseline alignment.",
                    "Apply minimal restore-only correction.",
                ]
            if "evidence_lines" in base and isinstance(base.get("evidence_lines"), list):
                base["evidence_lines"] = [
                    "Before/after endpoint checks with host header",
                    "Router and upstream evidence snapshot",
                ]
            if "risk_tag" in base:
                base["risk_tag"] = "medium"
            return base

        if fn == "build_incident_summary":
            if "summary" in base:
                base["summary"] = (
                    f"Restore-only fallback summary for {issue} after invalid structured model output."
                )
            if "timeline" in base and isinstance(base.get("timeline"), list):
                base["timeline"] = [
                    "Detection confirmed from ticket context.",
                    "Restore-chain validation executed (DNS->SNI->router->endpoint).",
                    "Minimal baseline-safe remediation prepared.",
                ]
            if "resolution" in base:
                base["resolution"] = "Pending/partial: continue restore-only remediation and re-validate."
            return base

        if fn == "find_bottleneck":
            if "bottleneck" in base:
                base["bottleneck"] = "network"
            if "impact_scope" in base:
                base["impact_scope"] = "stack"
            if "confidence" in base:
                base["confidence"] = 0.4
            if "signals" in base and isinstance(base.get("signals"), list):
                base["signals"] = [
                    f"Fallback bottleneck analysis for {issue} due invalid structured model output.",
                    "Conservative assumption: validate endpoint contract before tuning.",
                ]
            return base

        if isinstance(base, dict):
            return base

        return None

    async def ask(self, *, agent: str, task: str, context: str = "", function_name: str | None = None, structured: bool = True, model_alias_chain: list[str] | None = None) -> AgentResult:
        cfg = self._resolve_agent(agent)
        aliases = model_alias_chain or [cfg["model_alias"]]
        models = [self._resolve_model(a) for a in aliases]
        selected_function = function_name
        if structured and not selected_function:
            selected_function = self._default_function_for_agent(
                agent, cfg.get("functions", {})
            )
        schema = self.schemas.get("default", {})
        if selected_function:
            schema = self.schemas.get("agent_function", {}).get(
                f"{agent}.{selected_function}", schema
            )

        prompt = (
            f"Role: {cfg.get('role','')}.\n"
            f"Task: {task}\n"
            f"Context: {context}\n"
            "Constraints: restore-only; no architecture/policy/firewall/security redesign.\n"
        )
        if structured:
            prompt += (
                "Return ONLY JSON object following schema:\n"
                + json.dumps(schema, ensure_ascii=True)
                + "\nOutput discipline rules: exactly one object; double-quoted keys/strings; "
                + "no markdown fences; no comments; no trailing commas."
            )

        out, model_used = await self.router.chat_with_fallback(models=models, prompt=prompt, temperature=float(cfg.get("temperature", 0.2)), max_tokens=int(cfg.get("max_tokens", 900)))
        parsed = self._extract_json_object(out) if structured else None
        if structured and parsed is None:
            parsed = self._synthesize_structured_fallback(
                function_name=selected_function, schema=schema, issue_type=task
            )
        return AgentResult(agent=agent, ok=True, output=out, model_used=model_used, function_used=selected_function, structured_output=parsed)

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
