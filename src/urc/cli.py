from __future__ import annotations

import argparse
import asyncio
import json
from urllib.request import Request, urlopen

from .orchestrator import AgentOrchestrator


def printj(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=True, indent=2))


async def cmd_issue(args: argparse.Namespace) -> None:
    orch = AgentOrchestrator()
    out = await orch.handle_issue_for_agent(issue_type=args.type, summary=args.summary, agent=args.agent, context=args.context, structured=True)
    printj(out)


def cmd_submit(args: argparse.Namespace) -> None:
    body = {
        "issue_type": args.type,
        "summary": args.summary,
        "context": args.context,
        "priority": args.priority,
        "requested_by": args.requested_by,
        "target_agents": [x.strip() for x in args.target_agents.split(",") if x.strip()],
        "run_executor": args.run_executor,
        "apply": args.apply,
    }
    req = Request(args.url.rstrip("/") + "/v1/plan", data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=15) as resp:  # noqa: S310
        print(resp.read().decode("utf-8"))


def main() -> None:
    p = argparse.ArgumentParser(description="URC CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("issue")
    i.add_argument("--type", required=True)
    i.add_argument("--summary", required=True)
    i.add_argument("--context", default="")
    i.add_argument("--agent", default="sre_diagnoser")

    s = sub.add_parser("submit")
    s.add_argument("--url", default="http://127.0.0.1:8765")
    s.add_argument("--type", required=True)
    s.add_argument("--summary", required=True)
    s.add_argument("--context", default="")
    s.add_argument("--priority", default="medium")
    s.add_argument("--requested-by", default="operator")
    s.add_argument("--target-agents", default="sre_diagnoser,performance_analyst,documentarian")
    s.add_argument("--run-executor", action="store_true")
    s.add_argument("--apply", action="store_true")

    a = p.parse_args()
    if a.cmd == "issue":
        asyncio.run(cmd_issue(a))
    else:
        cmd_submit(a)


if __name__ == "__main__":
    main()
