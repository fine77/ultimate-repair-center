from __future__ import annotations

import argparse
import json
import socket
import time
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=True, indent=2)
    tmp.replace(path)


class Server(ThreadingHTTPServer):
    def __init__(self, addr: tuple[str, int], base_dir: Path) -> None:
        super().__init__(addr, Handler)
        self.base_dir = base_dir
        self.runtime = base_dir / "runtime"
        self.plans = self.runtime / "plans"
        self.queues = self.runtime / "queues"
        self.done = self.runtime / "done"
        self.failed = self.runtime / "failed"
        self.heartbeat = self.runtime / "heartbeat"
        for p in (self.runtime, self.plans, self.queues, self.done, self.failed, self.heartbeat):
            p.mkdir(parents=True, exist_ok=True)


class Handler(BaseHTTPRequestHandler):
    server: Server

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    def respond(self, code: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def body(self) -> dict[str, Any]:
        n = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(n) if n > 0 else b"{}"
        try:
            p = json.loads(raw.decode("utf-8"))
            return p if isinstance(p, dict) else {}
        except Exception:
            return {}

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/healthz":
            self.respond(HTTPStatus.OK, {"ok": True, "service": "urc-control-api", "host": socket.gethostname(), "ts_utc": utc_now()})
            return
        if parsed.path == "/v1/status":
            queue_by_agent: dict[str, dict[str, int]] = {}
            total_inbox = 0
            for d in sorted(self.server.queues.glob("*")):
                if not d.is_dir():
                    continue
                q_inbox = len(list((d / "inbox").glob("*.json")))
                q_done = len(list((d / "done").glob("*.json")))
                q_failed = len(list((d / "failed").glob("*.json")))
                queue_by_agent[d.name] = {"inbox": q_inbox, "done": q_done, "failed": q_failed}
                total_inbox += q_inbox
            self.respond(HTTPStatus.OK, {
                "ok": True,
                "ts_utc": utc_now(),
                "host": socket.gethostname(),
                "queue": {
                    "inbox": total_inbox,
                    "done": len(list(self.server.done.glob("*.json"))),
                    "failed": len(list(self.server.failed.glob("*.json"))),
                    "plans": len(list(self.server.plans.glob("*.json"))),
                },
                "queue_by_agent": queue_by_agent,
            })
            return
        self.respond(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/v1/plan":
            self.respond(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"})
            return
        p = self.body()
        summary = str(p.get("summary") or "").strip()
        if not summary:
            self.respond(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "summary_required"})
            return

        while True:
            plan_id = datetime.now(timezone.utc).strftime("PLAN-%Y%m%dT%H%M%SZ")
            plan_file = self.server.plans / f"{plan_id}.json"
            if not plan_file.exists():
                break
            time.sleep(1)

        priority = str(p.get("priority") or "medium").strip().lower()
        if priority not in {"low", "medium", "high", "critical"}:
            priority = "medium"

        agents = p.get("target_agents")
        if not isinstance(agents, list) or not agents:
            agents = ["sre_diagnoser", "performance_analyst", "documentarian"]

        doc = {
            "plan_id": plan_id,
            "created_at_utc": utc_now(),
            "requested_by": str(p.get("requested_by") or "operator"),
            "issue_type": str(p.get("issue_type") or "manual_plan"),
            "summary": summary,
            "context": str(p.get("context") or ""),
            "priority": priority,
            "run_executor": bool(p.get("run_executor", False)),
            "apply": bool(p.get("apply", False)),
            "target_agents": agents,
        }
        write_json(plan_file, doc)

        created: list[str] = []
        for idx, a in enumerate(agents, start=1):
            ticket_name = f"{plan_id}-{idx:02d}-{a}.json"
            tpath = self.server.queues / a / "inbox" / ticket_name
            write_json(tpath, {
                "mode": "issue",
                "plan_id": plan_id,
                "issue_type": doc["issue_type"],
                "summary": summary,
                "context": doc["context"],
                "priority": priority,
                "structured": True,
                "target_agent": a,
                "run_executor": bool(doc["run_executor"] and a == "executor"),
                "apply": bool(doc["apply"] and a == "executor"),
            })
            created.append(str(tpath))

        self.respond(HTTPStatus.OK, {"ok": True, "plan_id": plan_id, "tickets": created})


def main() -> None:
    p = argparse.ArgumentParser(description="URC control API")
    p.add_argument("--base-dir", default=str(Path(__file__).resolve().parents[2]))
    p.add_argument("--bind", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    a = p.parse_args()
    srv = Server((a.bind, a.port), Path(a.base_dir))
    srv.serve_forever()


if __name__ == "__main__":
    main()
