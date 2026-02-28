# Ultimate Repair Center (URC)

URC is a **restore-only** incident handling framework for runtime operations.  
Its goal is not redesign, but fast and deterministic recovery to the accepted current run state.

## Target Operating Model
- Create and route incident tickets into queues.
- Process **one ticket at a time per worker**.
- Run cloud-model diagnostics through a controlled orchestration layer.
- Allow only whitelisted restore actions via the executor.
- Persist outcomes as auditable `done` or `failed` artifacts.

## Explicitly Out of Scope
- Knowledgebase modules and KB lifecycle.
- Security/compliance automation workflows.
- Firewall/policy management and network redesign.

## Architecture in 30 Seconds
1. A plan is submitted via Control API (`POST /v1/plan`).
2. URC creates agent tickets in `runtime/queues/<agent>/inbox/`.
3. Workers consume tickets by priority (`critical` -> `low`).
4. The orchestrator selects model fallback chain and output schema.
5. Results are written to agent-local and global `done` stores.
6. Errors are written to agent-local and global `failed` stores.

## Repository Layout
- `src/urc/`: runtime code (`control_api`, `worker`, `orchestrator`, `ollama_client`, `executor`, `cli`)
- `configs/`: agent, issue, model, endpoint, and response schema configs
- `scripts/`: operator helpers (`submit_plan.sh`)
- `ops/systemd/`: systemd unit templates (API + workers)
- `runtime/`: runtime state (queues, plans, done/failed, heartbeat)

## Configuration Model
- `configs/agent_profiles.json`:
  - roles, model aliases, token limits, executor allowed actions
- `configs/issue_profiles.json`:
  - issue type -> agents, model strategy, suggested executor action
- `configs/model_policy.json`:
  - alias -> concrete cloud model
- `configs/ollama_endpoints.json`:
  - endpoint fallback chain and `OLLAMA_API_KEY` integration
- `configs/response_schemas.json`:
  - expected JSON output shape per agent function

## Local Startup (Manual)
Start Control API:
```bash
cd /root/ultimate-repair-center
PYTHONPATH=src python3 -m urc.control_api --base-dir /root/ultimate-repair-center --bind 127.0.0.1 --port 8765
```

Start one worker:
```bash
cd /root/ultimate-repair-center
PYTHONPATH=src python3 -m urc.worker --agent sre_diagnoser --base-dir /root/ultimate-repair-center --interval-sec 10
```

Start another worker:
```bash
cd /root/ultimate-repair-center
PYTHONPATH=src python3 -m urc.worker --agent performance_analyst --base-dir /root/ultimate-repair-center --interval-sec 10
```

## Submit Tickets
Via helper script:
```bash
cd /root/ultimate-repair-center
URC_API_URL=http://127.0.0.1:8765 ISSUE_TYPE=manual_plan ./scripts/submit_plan.sh "Test restore plan"
```

Via CLI:
```bash
cd /root/ultimate-repair-center
PYTHONPATH=src python3 -m urc.cli submit \
  --url http://127.0.0.1:8765 \
  --type tailnet_degraded \
  --summary "Tailnet route unstable" \
  --priority high \
  --target-agents sre_diagnoser,performance_analyst,documentarian
```

## API Endpoints
- `GET /healthz`: control API health
- `GET /v1/status`: queue and worker status
- `POST /v1/plan`: create plan and distribute agent tickets

## Systemd Operation
Unit templates:
- `ops/systemd/urc-control-api.service`
- `ops/systemd/urc-worker@.service`

Typical activation:
```bash
sudo cp ops/systemd/urc-control-api.service /etc/systemd/system/
sudo cp ops/systemd/urc-worker@.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now urc-control-api.service
sudo systemctl enable --now urc-worker@sre_diagnoser.service
sudo systemctl enable --now urc-worker@performance_analyst.service
sudo systemctl enable --now urc-worker@documentarian.service
```

## Executor Rules
- The executor runs only actions listed in `allowed_actions`.
- Executor actions are driven by issue profile mapping.
- `apply` must be explicit and never implicit.
- Operating contract remains restore-only.

## Runtime Data and Monitoring Paths
- Open tickets: `runtime/queues/*/inbox/*.json`
- Claimed/locked: `runtime/queues/*/inbox/*.json.<agent>.lock`
- Completed: `runtime/done/*.json`
- Failed: `runtime/failed/*.json`
- Heartbeats: `runtime/heartbeat/*.json`
- Worker events: `runtime/logs/events.jsonl`

Worker stale-lock self-heal:
- `URC_LOCK_STALE_SEC` (default `180`) controls when old `*.lock` files are recovered back to queue JSON.

## Release and Update Policy
Every change must be reflected in:
- `CHANGELOG.md`
- Git commit history on `main`
