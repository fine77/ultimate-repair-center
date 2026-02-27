from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .orchestrator import AgentOrchestrator

PRIO_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=True, indent=2)
    tmp.replace(path)


def ticket_key(path: Path) -> tuple[int, float, str]:
    try:
        payload = read_json(path)
        prio = str(payload.get("priority", "medium")).lower().strip()
    except Exception:
        prio = "medium"
    try:
        mt = path.stat().st_mtime
    except Exception:
        mt = time.time()
    return (PRIO_ORDER.get(prio, 2), mt, path.name)


def run_worker(agent: str, base_dir: Path, interval_sec: int) -> int:
    runtime = base_dir / "runtime"
    inbox = runtime / "queues" / agent / "inbox"
    done = runtime / "queues" / agent / "done"
    failed = runtime / "queues" / agent / "failed"
    global_done = runtime / "done"
    global_failed = runtime / "failed"
    heartbeat = runtime / "heartbeat"
    logs = runtime / "logs"
    for p in (inbox, done, failed, global_done, global_failed, heartbeat, logs):
        p.mkdir(parents=True, exist_ok=True)

    host = socket.gethostname()

    while True:
        orch = AgentOrchestrator()
        hb = {"agent": agent, "host": host, "timestamp_utc": utc_now(), "status": "idle"}
        files = sorted(inbox.glob("*.json"), key=ticket_key)
        processed = 0
        for f in files:
            processed += 1
            try:
                t = read_json(f)
                if str(t.get("target_agent") or "").strip() not in {"", agent}:
                    continue
                mode = str(t.get("mode", "issue")).lower().strip()
                if mode == "issue":
                    issue_type = str(t.get("issue_type", "manual_plan")).strip()
                    summary = str(t.get("summary", "")).strip()
                    context = str(t.get("context", "")).strip()
                    if bool(t.get("run_executor", False)) and agent == "executor":
                        profile = orch.issue_profiles.get(issue_type, {})
                        action = str(profile.get("suggested_executor_action") or "").strip()
                        if not action:
                            raise ValueError("no executor action configured")
                        res = orch.exec_action(action=action, apply=bool(t.get("apply", False)))
                        payload = {"executor": {"action": action, "ok": res.ok, "output": res.output}}
                    else:
                        payload = asyncio.run(orch.handle_issue_for_agent(issue_type=issue_type, summary=summary, agent=agent, context=context, structured=bool(t.get("structured", True))))
                else:
                    task = str(t.get("task", "")).strip()
                    if not task:
                        raise ValueError("ask ticket missing task")
                    res = asyncio.run(orch.ask(agent=agent, task=task, context=str(t.get("context", "")), structured=bool(t.get("structured", True))))
                    payload = {"agent": res.agent, "ok": res.ok, "model_used": res.model_used, "output": res.output, "structured_output": res.structured_output}

                out = {"ticket_file": f.name, "agent": agent, "ok": True, "started_at_utc": utc_now(), "finished_at_utc": utc_now(), "result": payload}
                write_json(done / f.name, out)
                write_json(global_done / f.name, out)
                f.unlink(missing_ok=True)
                hb["status"] = "processed"
                break
            except Exception as exc:  # noqa: BLE001
                out = {"ticket_file": f.name, "agent": agent, "ok": False, "error": str(exc), "finished_at_utc": utc_now()}
                write_json(failed / f.name, out)
                write_json(global_failed / f.name, out)
                f.unlink(missing_ok=True)
                hb["status"] = "error"
                break

        hb["processed_in_cycle"] = processed
        write_json(heartbeat / f"{agent}.json", hb)
        time.sleep(max(3, interval_sec))


def main() -> None:
    p = argparse.ArgumentParser(description="URC worker")
    p.add_argument("--agent", required=True)
    p.add_argument("--base-dir", default=str(Path(__file__).resolve().parents[2]))
    p.add_argument("--interval-sec", type=int, default=15)
    a = p.parse_args()
    raise SystemExit(run_worker(a.agent, Path(a.base_dir), a.interval_sec))


if __name__ == "__main__":
    main()
