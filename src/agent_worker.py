from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import re
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .orchestrator import AgentOrchestrator
from .executor import run_action


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=True, indent=2)
    tmp.replace(path)


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=True) + "\n")


def _load_telegram_creds() -> tuple[str, str]:
    candidates = [
        Path("/root/.config/planetonyx/telegram.env"),
        Path("/root/about-site/ops/governance-alert.env"),
    ]
    token = ""
    chat_id = ""
    for p in candidates:
        if not p.exists():
            continue
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                raw = line.strip()
                if not raw or raw.startswith("#") or "=" not in raw:
                    continue
                k, v = raw.split("=", 1)
                key = k.strip()
                val = v.strip().strip('"').strip("'")
                if key in {"BOT_TOKEN", "TG_TOKEN"} and val:
                    token = val
                if key in {"CHAT_ID", "TG_CHAT_ID"} and val:
                    chat_id = val
        except Exception:
            continue
    return token, chat_id


def _notify_telegram(agent: str, event: str, ticket_file: str, detail: str = "") -> None:
    # Disabled by default for noise control; enable explicitly in worker units.
    if os.getenv("AGENT_TELEGRAM_NOTIFY_ALL", "0").strip() != "1":
        return
    token, chat_id = _load_telegram_creds()
    if not token or not chat_id:
        return
    text = f"[URC][{event}] agent={agent} ticket={ticket_file}"
    if detail:
        text = f"{text} detail={detail[:800]}"
    body = urlencode({"chat_id": chat_id, "text": text}).encode("utf-8")
    req = Request(
        url=f"https://api.telegram.org/bot{token}/sendMessage",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=8):  # noqa: S310
            return
    except Exception:
        return


def _ping_ok(ping_target: str, timeout_sec: int) -> bool:
    try:
        proc = subprocess.run(
            ["ping", "-c", "1", "-W", str(timeout_sec), ping_target],
            capture_output=True,
            text=True,
            check=False,
        )
        return proc.returncode == 0
    except Exception:
        return False


def _parse_targets(raw: str, default: list[str]) -> list[str]:
    targets = [x.strip() for x in raw.split(",") if x.strip()]
    return targets or default


def _count_reachable(targets: list[str], timeout_sec: int) -> tuple[int, list[str]]:
    ok_targets: list[str] = []
    for target in targets:
        if _ping_ok(target, timeout_sec):
            ok_targets.append(target)
    return len(ok_targets), ok_targets


def _uplink_iface_ok(expected_iface: str, probe_target: str) -> bool | None:
    iface = expected_iface.strip()
    if not iface:
        return None
    try:
        proc = subprocess.run(
            ["ip", "route", "get", probe_target],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            return False
        return f" dev {iface} " in f" {proc.stdout.strip()} "
    except Exception:
        return False


def _auto_stabilize(
    *,
    agent: str,
    logs_dir: Path,
    reason: str,
    action: str,
    apply: bool,
) -> None:
    # Only executor is allowed to run stabilization actions.
    if agent != "executor":
        return
    ts = _utc_now()
    code, stdout, stderr = run_action(action=action, apply=apply)
    _append_jsonl(
        logs_dir / "events.jsonl",
        {
            "ts_utc": ts,
            "agent": agent,
            "event": "auto_stabilize",
            "reason": reason,
            "action": action,
            "apply": apply,
            "ok": code == 0,
        },
    )
    _append_jsonl(
        logs_dir / f"{agent}.jsonl",
        {
            "ts_utc": ts,
            "event": "auto_stabilize",
            "reason": reason,
            "action": action,
            "apply": apply,
            "ok": code == 0,
            "stdout": (stdout or "").strip()[-4000:],
            "stderr": (stderr or "").strip()[-4000:],
        },
    )


def _claim_ticket(path: Path, agent: str) -> Path | None:
    lock = path.with_name(path.name + f".{agent}.lock")
    try:
        path.rename(lock)
        # Refresh mtime so stale-lock recovery won't reclaim an active lock
        # that originated from an old ticket timestamp.
        os.utime(lock, None)
        return lock
    except FileNotFoundError:
        return None
    except OSError:
        return None


def _evaluate_completion(
    *,
    processing_ok: bool,
    payload: dict[str, Any] | None,
) -> tuple[bool, str]:
    if not processing_ok:
        return (False, "processing_timeout_or_degraded_result")

    if not isinstance(payload, dict):
        return (False, "invalid_result_payload")

    if payload.get("ok") is False:
        return (False, "result_ok_false")

    # Reject false-positive "done" tickets that contain no actionable payload.
    # At least analysis output or executor result must be present.
    has_analysis = isinstance(payload.get("analysis"), list) and len(payload.get("analysis") or []) > 0
    has_executor = isinstance(payload.get("executor"), dict)
    if not has_analysis and not has_executor:
        return (False, "empty_result_payload")

    executor = payload.get("executor")
    if isinstance(executor, dict) and executor.get("ok") is False:
        action = str(executor.get("action") or "").strip() or "unknown_action"
        return (False, f"executor_action_failed:{action}")

    analysis = payload.get("analysis")
    if isinstance(analysis, list):
        for item in analysis:
            if isinstance(item, dict) and item.get("ok") is False:
                agent = str(item.get("agent") or "unknown_agent").strip()
                return (False, f"analysis_failed:{agent}")

    return (True, "verified_success")


def _derive_issue_type(
    *,
    ticket: dict[str, Any] | None,
    payload: dict[str, Any] | None,
    plans_dir: Path,
) -> str:
    if isinstance(ticket, dict):
        raw = str(ticket.get("issue_type") or "").strip()
        if raw:
            return raw
    if isinstance(payload, dict):
        raw = str(payload.get("issue_type") or "").strip()
        if raw:
            return raw
    if isinstance(ticket, dict):
        plan_id = str(ticket.get("plan_id") or "").strip()
        if plan_id:
            plan_file = plans_dir / f"{plan_id}.json"
            try:
                plan = _read_json(plan_file)
                raw = str(plan.get("issue_type") or "").strip()
                if raw:
                    return raw
            except Exception:
                pass
    return "unknown"


_PLAN_TICKET_RE = re.compile(r"^(PLAN-\d{8}T\d{6}Z)-\d{2}-([a-z_]+)\.json$")
_PRIO_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _recover_stale_locks(inbox: Path, stale_sec: int) -> int:
    now = time.time()
    recovered = 0
    for lock in inbox.glob("*.json.*.lock"):
        try:
            age = now - lock.stat().st_mtime
            if age < stale_sec:
                continue
            # foo.json.agent.lock -> foo.json
            base_name = lock.name.rsplit(".", 2)[0]
            target = lock.with_name(base_name)
            lock.rename(target)
            recovered += 1
        except Exception:
            continue
    return recovered


def _ticket_priority_key(path: Path) -> tuple[int, float, str]:
    priority = "medium"
    try:
        payload = _read_json(path)
        if isinstance(payload, dict):
            raw = str(payload.get("priority", "medium")).strip().lower()
            if raw in _PRIO_ORDER:
                priority = raw
    except Exception:
        priority = "medium"
    try:
        ts = path.stat().st_mtime
    except Exception:
        ts = time.time()
    return (_PRIO_ORDER.get(priority, _PRIO_ORDER["medium"]), ts, path.name)


def run_worker(agent: str, base_dir: Path, interval_sec: int) -> int:
    runtime = base_dir / "runtime"
    plans_dir = runtime / "plans"
    queue_root = runtime / "queues" / agent
    inbox = queue_root / "inbox"
    done = queue_root / "done"
    failed = queue_root / "failed"
    global_done = runtime / "done"
    global_failed = runtime / "failed"
    heartbeat = runtime / "heartbeat"
    logs = runtime / "logs"
    for p in (inbox, done, failed, global_done, global_failed, heartbeat, logs):
        p.mkdir(parents=True, exist_ok=True)

    orch = AgentOrchestrator()
    host = socket.gethostname()
    pid = os.getpid()
    net_targets = _parse_targets(
        os.getenv("AGENT_NET_TARGETS", "1.1.1.1,8.8.8.8"),
        ["1.1.1.1", "8.8.8.8"],
    )
    tailnet_targets = _parse_targets(
        os.getenv("AGENT_TAILNET_TARGETS", "100.64.0.1,100.64.0.3"),
        ["100.64.0.1", "100.64.0.3"],
    )
    ping_timeout = int(os.getenv("AGENT_NET_PING_TIMEOUT", "2"))
    min_ok = int(os.getenv("AGENT_NET_MIN_OK", "1"))
    block_if_firewalls_down = (
        os.getenv("AGENT_BLOCK_IF_FIREWALLS_DOWN", "0").strip() == "1"
    )
    firewall_targets = _parse_targets(
        os.getenv("AGENT_FIREWALL_TARGETS", "100.64.0.1,100.64.0.3"),
        ["100.64.0.1", "100.64.0.3"],
    )
    firewall_min_ok = int(os.getenv("AGENT_FIREWALL_MIN_OK", "1"))
    lan_firewall_targets = _parse_targets(
        os.getenv("AGENT_LAN_FIREWALL_TARGETS", "10.10.10.4,10.10.10.5"),
        ["10.10.10.4", "10.10.10.5"],
    )
    lan_firewall_min_ok = int(os.getenv("AGENT_LAN_FIREWALL_MIN_OK", "1"))
    expected_uplink_iface = os.getenv("AGENT_EXPECT_UPLINK_IF", "").strip()
    uplink_probe_target = (
        os.getenv("AGENT_UPLINK_PROBE_TARGET", "1.1.1.1").strip() or "1.1.1.1"
    )
    wait_on_net_down = os.getenv("AGENT_WAIT_ON_NET_DOWN", "1").strip() == "1"
    auto_stabilize_on_boot = os.getenv("AGENT_AUTO_STABILIZE_ON_BOOT", "1").strip() == "1"
    auto_stabilize_on_recovery = os.getenv("AGENT_AUTO_STABILIZE_ON_RECOVERY", "1").strip() == "1"
    auto_stabilize_action = os.getenv("AGENT_AUTO_STABILIZE_ACTION", "repair-health-check").strip()
    auto_stabilize_apply = os.getenv("AGENT_AUTO_STABILIZE_APPLY", "1").strip() == "1"
    lock_stale_sec = int(os.getenv("AGENT_LOCK_STALE_SEC", "180"))
    ticket_timeout_sec = int(os.getenv("AGENT_TICKET_TIMEOUT_SEC", "180"))
    max_retries = int(os.getenv("AGENT_TICKET_MAX_RETRIES", "3"))
    retry_backoff_sec = int(os.getenv("AGENT_TICKET_RETRY_BACKOFF_SEC", "20"))
    network_down_prev = False
    boot_stabilized = False

    while True:
        # Hot-reload configs each cycle so new issue profiles/materials become active
        # without requiring a service restart.
        try:
            orch = AgentOrchestrator()
        except Exception as exc:  # noqa: BLE001
            _append_jsonl(
                logs / "events.jsonl",
                {
                    "ts_utc": _utc_now(),
                    "agent": agent,
                    "event": "orchestrator_reload_failed",
                    "error": str(exc),
                },
            )

        net_ok_count, net_ok_targets = _count_reachable(net_targets, ping_timeout)
        internet_ok = net_ok_count >= max(1, min_ok)
        tailnet_ok_count, tailnet_ok_targets = _count_reachable(
            tailnet_targets, ping_timeout
        )
        tailnet_ok = tailnet_ok_count >= 1
        firewall_ok_count, firewall_ok_targets = _count_reachable(
            firewall_targets, ping_timeout
        )
        firewall_quorum_ok = firewall_ok_count >= max(1, firewall_min_ok)
        lan_firewall_ok_count, lan_firewall_ok_targets = _count_reachable(
            lan_firewall_targets, ping_timeout
        )
        lan_firewall_quorum_ok = lan_firewall_ok_count >= max(1, lan_firewall_min_ok)
        uplink_iface_ok = _uplink_iface_ok(expected_uplink_iface, uplink_probe_target)
        effective_network_ok = internet_ok and (
            not block_if_firewalls_down or firewall_quorum_ok
        )
        connectivity_hint = "ok"
        if internet_ok and not firewall_quorum_ok and lan_firewall_quorum_ok:
            connectivity_hint = "tailscale_or_vps_down_likely"
        if (
            uplink_iface_ok is True
            and not firewall_quorum_ok
            and not tailnet_ok
            and lan_firewall_quorum_ok
        ):
            connectivity_hint = "pppoe0_down_likely"
        hb = {
            "agent": agent,
            "host": host,
            "pid": pid,
            "status": "idle" if effective_network_ok else "waiting_network",
            "timestamp_utc": _utc_now(),
            "baseline_mode": "ist_only_restore",
            "approved_baseline_changes_count": len(orch.baseline_changes),
            "internet_ok": internet_ok,
            "effective_network_ok": effective_network_ok,
            "net_targets": net_targets,
            "net_min_ok": min_ok,
            "net_ok_count": net_ok_count,
            "net_ok_targets": net_ok_targets,
            "tailnet_ok": tailnet_ok,
            "tailnet_targets": tailnet_targets,
            "tailnet_ok_count": tailnet_ok_count,
            "tailnet_ok_targets": tailnet_ok_targets,
            "block_if_firewalls_down": block_if_firewalls_down,
            "firewall_targets": firewall_targets,
            "firewall_min_ok": firewall_min_ok,
            "firewall_ok_count": firewall_ok_count,
            "firewall_ok_targets": firewall_ok_targets,
            "firewall_quorum_ok": firewall_quorum_ok,
            "lan_firewall_targets": lan_firewall_targets,
            "lan_firewall_min_ok": lan_firewall_min_ok,
            "lan_firewall_ok_count": lan_firewall_ok_count,
            "lan_firewall_ok_targets": lan_firewall_ok_targets,
            "lan_firewall_quorum_ok": lan_firewall_quorum_ok,
            "expected_uplink_iface": expected_uplink_iface,
            "uplink_probe_target": uplink_probe_target,
            "uplink_iface_ok": uplink_iface_ok,
            "connectivity_hint": connectivity_hint,
        }

        if wait_on_net_down and not effective_network_ok:
            if not network_down_prev:
                _append_jsonl(
                    logs / "events.jsonl",
                    {
                        "ts_utc": _utc_now(),
                        "agent": agent,
                        "event": "network_down_wait",
                        "net_targets": net_targets,
                        "net_min_ok": min_ok,
                        "net_ok_count": net_ok_count,
                        "block_if_firewalls_down": block_if_firewalls_down,
                        "firewall_targets": firewall_targets,
                        "firewall_min_ok": firewall_min_ok,
                        "firewall_ok_count": firewall_ok_count,
                    },
                )
            network_down_prev = True
            hb["processed_in_cycle"] = 0
            _write_json(heartbeat / f"{agent}.json", hb)
            time.sleep(max(3, interval_sec))
            continue

        if network_down_prev and effective_network_ok:
            _append_jsonl(
                logs / "events.jsonl",
                {
                    "ts_utc": _utc_now(),
                    "agent": agent,
                    "event": "network_restored",
                    "net_targets": net_targets,
                    "net_ok_count": net_ok_count,
                    "block_if_firewalls_down": block_if_firewalls_down,
                    "firewall_ok_count": firewall_ok_count,
                },
            )
            if auto_stabilize_on_recovery:
                _auto_stabilize(
                    agent=agent,
                    logs_dir=logs,
                    reason="network_restored",
                    action=auto_stabilize_action,
                    apply=auto_stabilize_apply,
                )
        network_down_prev = False

        if not boot_stabilized and auto_stabilize_on_boot:
            _auto_stabilize(
                agent=agent,
                logs_dir=logs,
                reason="boot",
                action=auto_stabilize_action,
                apply=auto_stabilize_apply,
            )
            boot_stabilized = True

        recovered = _recover_stale_locks(inbox, lock_stale_sec)
        if recovered > 0:
            _append_jsonl(
                logs / "events.jsonl",
                {
                    "ts_utc": _utc_now(),
                    "agent": agent,
                    "event": "stale_lock_recovered",
                    "count": recovered,
                    "baseline_mode": "ist_only_restore",
                },
            )

        ticket_files = sorted(inbox.glob("*.json"), key=_ticket_priority_key)
        processed = 0
        for ticket_file in ticket_files:
            # Prevent cross-claim churn: PLAN tickets contain target agent in filename.
            m = _PLAN_TICKET_RE.match(ticket_file.name)
            if m and m.group(2) != agent:
                continue
            claimed = _claim_ticket(ticket_file, agent)
            if not claimed:
                continue
            processed += 1
            started = _utc_now()
            processing_ok = True
            _append_jsonl(
                logs / "events.jsonl",
                {
                    "ts_utc": started,
                    "agent": agent,
                    "event": "ticket_claimed",
                    "ticket_file": ticket_file.name,
                    "priority": _read_json(claimed).get("priority", "medium"),
                    "baseline_mode": "ist_only_restore",
                },
            )
            try:
                ticket = _read_json(claimed)
                target = str(ticket.get("target_agent") or "").strip()
                if target and target != agent:
                    # Not for this agent; put it back untouched.
                    claimed.rename(inbox / ticket_file.name)
                    continue
                priority = str(ticket.get("priority") or "medium").strip().lower()
                if priority not in _PRIO_ORDER:
                    priority = "medium"

                mode_raw = str(ticket.get("mode") or "").strip().lower()
                ticket_issue_type = str(ticket.get("issue_type") or "").strip()
                ticket_summary = str(ticket.get("summary") or "").strip()
                has_issue_shape = bool(ticket_issue_type) and bool(ticket_summary)
                # Compatibility path for reconstructed/legacy tickets:
                # recover missing issue fields from plan payload when possible.
                plan_issue_type = ""
                plan_summary = ""
                if not has_issue_shape:
                    plan_id = str(ticket.get("plan_id") or "").strip()
                    if plan_id:
                        try:
                            plan = _read_json(plans_dir / f"{plan_id}.json")
                            if isinstance(plan, dict):
                                plan_issue_type = str(plan.get("issue_type") or "").strip()
                                plan_summary = str(plan.get("summary") or "").strip()
                        except Exception:
                            pass
                effective_issue_shape = has_issue_shape or (bool(plan_issue_type) and bool(plan_summary))
                if mode_raw in {"issue", "ist_only_restore", "restore", "incident"}:
                    mode = "issue"
                elif mode_raw in {"ask", "question", "task"}:
                    mode = "ask"
                elif effective_issue_shape and not str(ticket.get("task") or "").strip():
                    # Defensive compatibility for malformed/reconstructed tickets.
                    mode = "issue"
                else:
                    mode = "ask"
                if mode == "issue":
                    issue_type = ticket_issue_type or plan_issue_type
                    summary = ticket_summary or plan_summary
                    if not issue_type or not summary:
                        raise ValueError("malformed_issue_ticket_missing_issue_type_or_summary")
                    context = str(ticket.get("context") or "").strip()
                    # Execution is only allowed via explicit ticket flags and only by executor worker.
                    run_executor = bool(ticket.get("run_executor", False)) and agent == "executor"
                    apply = bool(ticket.get("apply", False)) and agent == "executor"
                    if run_executor:
                        profile = orch.issue_profiles.get(issue_type) or {}
                        action = str(profile.get("suggested_executor_action") or "").strip()
                        if not action:
                            raise ValueError(
                                f"issue_type '{issue_type}' has no suggested_executor_action for executor run"
                            )
                        exec_res = orch.exec_action(action=action, apply=apply)
                        payload = {
                            "issue_type": issue_type,
                            "summary": summary,
                            "priority": priority,
                            "baseline_mode": "ist_only_restore",
                            "approved_baseline_changes_count": len(orch.baseline_changes),
                            "executor": {
                                "action": action,
                                "apply": apply,
                                "ok": exec_res.ok,
                                "output": exec_res.output,
                            },
                        }
                    else:
                        coro = orch.handle_issue_for_agent(
                            issue_type=issue_type,
                            summary=summary,
                            agent=agent,
                            context=context,
                            structured=bool(ticket.get("structured", True)),
                        )
                        try:
                            payload = asyncio.run(
                                asyncio.wait_for(coro, timeout=ticket_timeout_sec)
                            )
                        except TimeoutError:
                            processing_ok = False
                            payload = {
                                "issue_type": issue_type,
                                "summary": summary,
                                "selected_agents": [agent],
                                "baseline_mode": "ist_only_restore",
                                "approved_baseline_changes_count": len(orch.baseline_changes),
                                "analysis": [
                                    {
                                        "agent": agent,
                                        "ok": False,
                                        "model_used": None,
                                        "function_used": None,
                                        "structured_output": None,
                                        "output": json.dumps(
                                            {
                                                "error": "ticket_timeout_degraded_done",
                                                "timeout_sec": ticket_timeout_sec,
                                                "action": "fallback_written_and_queue_continues",
                                            },
                                            ensure_ascii=True,
                                        ),
                                    }
                                ],
                                "suggested_executor_action": None,
                            }
                else:
                    task = str(ticket.get("task") or "").strip()
                    if not task:
                        raise ValueError("ask ticket requires task")
                    function_name = ticket.get("function")
                    structured = bool(ticket.get("structured", True))
                    try:
                        result = asyncio.run(
                            asyncio.wait_for(
                                orch.ask(
                                    agent=agent,
                                    task=task,
                                    context=str(ticket.get("context") or "").strip(),
                                    function_name=function_name,
                                    structured=structured,
                                ),
                                timeout=ticket_timeout_sec,
                            )
                        )
                        payload = {
                            "agent": result.agent,
                            "priority": priority,
                            "ok": result.ok,
                            "model_used": result.model_used,
                            "function_used": result.function_used,
                            "structured_output": result.structured_output,
                            "output": result.output,
                            "baseline_mode": "ist_only_restore",
                            "approved_baseline_changes_count": len(orch.baseline_changes),
                        }
                    except TimeoutError:
                        processing_ok = False
                        payload = {
                            "agent": agent,
                            "priority": priority,
                            "ok": False,
                            "model_used": None,
                            "function_used": function_name,
                            "structured_output": None,
                            "output": json.dumps(
                                {
                                    "error": "ticket_timeout_degraded_done",
                                    "timeout_sec": ticket_timeout_sec,
                                    "action": "fallback_written_and_queue_continues",
                                },
                                ensure_ascii=True,
                            ),
                            "baseline_mode": "ist_only_restore",
                            "approved_baseline_changes_count": len(orch.baseline_changes),
                        }

                out = {
                    "ticket_file": ticket_file.name,
                    "agent": agent,
                    "plan_id": str(ticket.get("plan_id", "")) if isinstance(ticket, dict) else "",
                    "issue_type": str(ticket.get("issue_type", "")) if isinstance(ticket, dict) else "",
                    "priority": str(ticket.get("priority", "medium")) if isinstance(ticket, dict) else "medium",
                    "retry_count": int(ticket.get("retry_count", 0)) if isinstance(ticket, dict) else 0,
                    "started_at_utc": started,
                    "finished_at_utc": _utc_now(),
                    "ok": processing_ok,
                    "result": payload,
                }
                completion_ok, completion_reason = _evaluate_completion(
                    processing_ok=processing_ok,
                    payload=payload if isinstance(payload, dict) else None,
                )
                out["ok"] = completion_ok
                out["completion_reason"] = completion_reason
                issue_type_for_signature = _derive_issue_type(
                    ticket=ticket if isinstance(ticket, dict) else None,
                    payload=payload if isinstance(payload, dict) else None,
                    plans_dir=plans_dir,
                )
                out["issue_type"] = issue_type_for_signature
                out["unresolved_signature"] = f"{issue_type_for_signature}::{completion_reason}"
                if completion_ok:
                    _write_json(done / ticket_file.name, out)
                    _write_json(global_done / ticket_file.name, out)
                    _append_jsonl(
                        logs / "events.jsonl",
                        {
                            "ts_utc": _utc_now(),
                            "agent": agent,
                            "event": "ticket_done",
                            "ticket_file": ticket_file.name,
                            "priority": priority,
                            "ok": True,
                            "baseline_mode": "ist_only_restore",
                            "model_used": (
                                payload.get("model_used")
                                if isinstance(payload, dict)
                                else None
                            ),
                            "completion_reason": completion_reason,
                        },
                    )
                    _append_jsonl(
                        logs / f"{agent}.jsonl",
                        {
                            "ts_utc": _utc_now(),
                            "event": "ticket_done",
                            "ticket_file": ticket_file.name,
                            "result": out.get("result", {}),
                            "completion_reason": completion_reason,
                        },
                    )
                    _notify_telegram(agent, "ticket_done", ticket_file.name)
                    hb["status"] = "processed"
                else:
                    _write_json(failed / ticket_file.name, out)
                    _write_json(global_failed / ticket_file.name, out)
                    _append_jsonl(
                        logs / "events.jsonl",
                        {
                            "ts_utc": _utc_now(),
                            "agent": agent,
                            "event": "ticket_failed",
                            "ticket_file": ticket_file.name,
                            "priority": priority,
                            "ok": False,
                            "error": completion_reason,
                            "baseline_mode": "ist_only_restore",
                            "unresolved_signature": out["unresolved_signature"],
                        },
                    )
                    _append_jsonl(
                        logs / f"{agent}.jsonl",
                        {
                            "ts_utc": _utc_now(),
                            "event": "ticket_failed",
                            "ticket_file": ticket_file.name,
                            "error": completion_reason,
                            "unresolved_signature": out["unresolved_signature"],
                            "result": out.get("result", {}),
                        },
                    )
                    _notify_telegram(agent, "ticket_failed", ticket_file.name, completion_reason)
                    hb["status"] = "error"
                claimed.unlink(missing_ok=True)
                # Non-preemptive queue: finish current ticket first.
                break
            except Exception as exc:  # noqa: BLE001
                err = str(exc).strip()
                if not err:
                    err = f"{exc.__class__.__name__}: no detail message"
                retryable = any(
                    s in err.lower()
                    for s in (
                        "timed out",
                        "timeout",
                        "http 429",
                        "too many concurrent requests",
                        "temporar",
                        "connection reset",
                    )
                )
                current_retry = int(ticket.get("retry_count", 0)) if "ticket" in locals() and isinstance(ticket, dict) else 0
                if retryable and current_retry < max_retries and "ticket" in locals() and isinstance(ticket, dict):
                    ticket["retry_count"] = current_retry + 1
                    ticket["last_error"] = err
                    ticket["last_retry_at_utc"] = _utc_now()
                    if retry_backoff_sec > 0:
                        time.sleep(retry_backoff_sec)
                    _write_json(inbox / ticket_file.name, ticket)
                    _append_jsonl(
                        logs / "events.jsonl",
                        {
                            "ts_utc": _utc_now(),
                            "agent": agent,
                            "event": "ticket_requeued",
                            "ticket_file": ticket_file.name,
                            "priority": ticket.get("priority", "medium"),
                            "retry_count": ticket["retry_count"],
                            "max_retries": max_retries,
                            "error": err,
                            "baseline_mode": "ist_only_restore",
                        },
                    )
                    _append_jsonl(
                        logs / f"{agent}.jsonl",
                        {
                            "ts_utc": _utc_now(),
                            "event": "ticket_requeued",
                            "ticket_file": ticket_file.name,
                            "retry_count": ticket["retry_count"],
                            "error": err,
                        },
                    )
                    _notify_telegram(agent, "ticket_requeued", ticket_file.name, err)
                    claimed.unlink(missing_ok=True)
                    hb["status"] = "requeued"
                    break
                out = {
                    "ticket_file": ticket_file.name,
                    "agent": agent,
                    "plan_id": str(ticket.get("plan_id", "")) if "ticket" in locals() and isinstance(ticket, dict) else "",
                    "issue_type": str(ticket.get("issue_type", "")) if "ticket" in locals() and isinstance(ticket, dict) else "",
                    "priority": str(ticket.get("priority", "medium")) if "ticket" in locals() and isinstance(ticket, dict) else "medium",
                    "retry_count": int(ticket.get("retry_count", 0)) if "ticket" in locals() and isinstance(ticket, dict) else 0,
                    "started_at_utc": started,
                    "finished_at_utc": _utc_now(),
                    "ok": False,
                    "error": err,
                }
                _write_json(failed / ticket_file.name, out)
                _write_json(global_failed / ticket_file.name, out)
                _append_jsonl(
                    logs / "events.jsonl",
                    {
                        "ts_utc": _utc_now(),
                        "agent": agent,
                        "event": "ticket_failed",
                        "ticket_file": ticket_file.name,
                        "priority": priority if "priority" in locals() else "medium",
                        "ok": False,
                        "error": err,
                        "baseline_mode": "ist_only_restore",
                    },
                )
                _append_jsonl(
                    logs / f"{agent}.jsonl",
                    {
                        "ts_utc": _utc_now(),
                        "event": "ticket_failed",
                        "ticket_file": ticket_file.name,
                        "error": err,
                    },
                )
                _notify_telegram(agent, "ticket_failed", ticket_file.name, err)
                claimed.unlink(missing_ok=True)
                hb["status"] = "error"
                break

        hb["processed_in_cycle"] = processed
        _write_json(heartbeat / f"{agent}.json", hb)
        time.sleep(max(3, interval_sec))


def main() -> None:
    p = argparse.ArgumentParser(description="Persistent worker for one virtual agent profile")
    p.add_argument("--agent", required=True)
    p.add_argument(
        "--base-dir",
        default="/root/about-site/projects/ollama-free-multi-agent",
        help="Project base directory",
    )
    p.add_argument("--interval-sec", type=int, default=15)
    args = p.parse_args()

    run_worker(agent=args.agent, base_dir=Path(args.base_dir), interval_sec=args.interval_sec)


if __name__ == "__main__":
    main()
