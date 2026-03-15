# Ollama Free Multi-Agent Control Plane

This project provides a safe multi-agent orchestration layer using **only free Ollama models**.
No paid cloud model APIs are used.

Operation mode: **Issue-driven**, not continuous automation.
Agents react to explicit incidents/tickets.
Execution policy: **IST-state restore only**. No architecture or policy changes via agents.

## Goals

- Separate responsibilities across multiple agents.
- Keep one strict write path (`executor`) for container operations.
- Use only approved host actions (`ops-repair` targets).
- Keep incident behavior restore-only.
- Keep agents locked to current baseline (`IST`) and prevent change execution.

## IST-Only Policy

- Agent mission is strictly: recover to current known-good baseline.
- No policy/routing/port/schema redesign by agent output.
- CLI `--apply` is blocked by policy in all commands.
- Agents can diagnose, correlate, propose restore steps, and emit CMDB evidence.
- Approved changes from `cmdb-templates/changes.csv` (`validated`/`implemented`) are injected as baseline context for every agent run.
- Agents must treat these approved changes as part of IST and must not propose rollback of them.

## Agent Responsibilities and Learning Material

Defined in:

- `configs/agent_profiles.json` (role/functions/guardrails)
- `configs/agent_materials.json` (mission contract, responsibilities, learning material, learning loops)

The orchestrator injects this material into prompts so each agent stays in scope and baseline-safe.

## Persistent Agent Services

Five persistent workers can be run as systemd services:

- `planetonyx-agent@sre_diagnoser.service`
- `planetonyx-agent@security_analyst.service`
- `planetonyx-agent@performance_analyst.service`
- `planetonyx-agent@documentarian.service`
- `planetonyx-agent@executor.service`

Install/start:

```bash
cd /root/about-site/projects/ollama-free-multi-agent
chmod 750 scripts/install_agent_services.sh
sudo ./scripts/install_agent_services.sh
```

Health:

```bash
systemctl --no-pager --full status 'planetonyx-agent@*'
ls -lah /root/about-site/projects/ollama-free-multi-agent/runtime/heartbeat
```

Agent activity log:

```bash
chmod 750 scripts/agent_activity.sh
./scripts/agent_activity.sh tail 80
./scripts/agent_activity.sh summary
./scripts/agent_activity.sh follow 50
```

## Autonomous Repair Dispatch (No Sleep Mode)

To avoid idle behavior when no operator is connected, a dispatcher can create
critical repair plans automatically.

- Script: `scripts/autonomous_repair_dispatch.sh`
- Units:
  - `ops/systemd/planetonyx-autonomous-repair-dispatch.service`
  - `ops/systemd/planetonyx-autonomous-repair-dispatch.timer` (every 5 minutes)
- Trigger source:
  - parses `repair-health-check` output for `tailnet` / `observability` / `crowdsec` failures
- Action:
  - creates critical plans via `POST /v1/plan`
  - includes `executor` and sets `run_executor=true`, `apply=true`
- Flood protection:
  - cooldown via `COOLDOWN_SEC` (default `1800`)

Install:

```bash
chmod 750 scripts/install_autonomous_repair_dispatch.sh
sudo ./scripts/install_autonomous_repair_dispatch.sh
```

Event stream file:

- `runtime/logs/events.jsonl`

## Control API (Plan Handoff)

Purpose:
- You submit plans/instructions.
- Models receive tickets and execute analysis/recovery planning.
- No direct manual execution path is required for routine plan handoff.

Service:
- `planetonyx-agent-control-api.service`
- bind: `127.0.0.1:8765`

Endpoints:
- `GET /healthz`
- `GET /v1/status`
- `POST /v1/plan`
- `GET /v1/knowledge/search?q=<query>&limit=8`
- `POST /v1/knowledge/upsert`

Example:

```bash
curl -sS -X POST http://127.0.0.1:8765/v1/plan \
  -H 'Content-Type: application/json' \
  -d '{
    "issue_type":"observability_nodata",
    "summary":"Grafana panel data stale for 20 minutes",
    "context":"keep IST-state; full-chain only",
    "requested_by":"manager",
    "target_agents":["sre_diagnoser","security_analyst","performance_analyst","documentarian"]
  }'
```

Helper:

```bash
chmod 750 scripts/agent_plan_submit.sh
./scripts/agent_plan_submit.sh "Grafana panel data stale for 20 minutes"
# executor-based self-repair trigger:
ISSUE_TYPE=pbs_unreachable TARGET_AGENTS=sre_diagnoser,executor,documentarian RUN_EXECUTOR=true APPLY=true PRIORITY=critical \
  ./scripts/agent_plan_submit.sh "Auto repair: PBS endpoint unreachable"
```

## Ollama Cloud Timeout/500 Shield

To reduce `429/500/503/timeout` bursts from cloud endpoints, workers can use a
local stabilizing proxy first.

- Proxy script: `scripts/ollama_cloud_proxy.py`
- Unit: `ops/systemd/planetonyx-ollama-cloud-proxy.service`
- Listen: `127.0.0.1:11435`
- Upstream: `https://ollama.com/api`
- Behavior: retry + exponential backoff on transient upstream failures.

Runtime endpoint order is configured in:

- `configs/ollama_endpoints.json`

Recommended order:
1. `cloud-proxy-primary` (`http://127.0.0.1:11435`)
2. `cloud-direct-fallback` (`https://ollama.com/api`)

Knowledge base helper (SQLite-backed, shared for all workers):

```bash
python3 -m src.cli kb-add \
  --source baseline \
  --category runbook \
  --title "Observability No-Data Restore Rule" \
  --content "Validate scrape path, datasource, and endpoint contract before applying minimal restore fix."

python3 -m src.cli kb-search --query "observability nodata restore contract" --limit 5
python3 -m src.cli kb-seed-baseline
```

Database path:

- default: `runtime/kb/worker_knowledge.db`
- override with env: `AGENT_KB_DB_PATH`

Network/Power resilience behavior:

- Agents ping `AGENT_NET_PING_TARGET` (default `1.1.1.1`) every cycle.
- If ping fails, worker status becomes `waiting_network` and the agent waits (no ticket execution).
- When network returns, an automatic stabilization run is triggered by executor:
  - action: `repair-health-check`
  - apply mode: enabled by default for restore-only stabilization.
- On boot, executor also triggers one stabilization run automatically.
- Queue lock recovery is automatic: stale `*.lock` files in inbox are recovered after `AGENT_LOCK_STALE_SEC` (default `180`).
- Worker timeout guard: model calls time out at `AGENT_TICKET_TIMEOUT_SEC` (default `75`) and are written to `failed` with explicit `completion_reason`.
- `done` is now strict: only verified success is written to `done`; any unresolved/failed executor/action/analysis result is written to `failed` with `unresolved_signature` for monitoring.

Failed-ticket learning loop:

- Script: `scripts/failed_ticket_learning.py`
- Purpose: analyze `runtime/failed/*.json`, detect recurring causes, and write lessons into shared SQLite KB.
- Includes detection of issue-profile gaps (unknown `issue_type`) so workers can be taught before requeue.

```bash
python3 scripts/failed_ticket_learning.py
```

Requeue + Monitoring reset behavior:

- `scripts/requeue_failed_tickets.sh` requeues failed tickets and (default) removes them from monitoring counters.
- Controlled by `CLEAN_REQUEUED_FAILED=1` (default).
- Requeued failed artifacts are moved from:
  - `runtime/failed/*.json`
  - `runtime/queues/<agent>/failed/*.json`
  into:
  - `runtime/history/requeued-failed/*.json`
- Result: failed panels/counters can start from zero after requeue.

Service env defaults are set in:

- `ops/systemd/planetonyx-agent@.service`

## Morning Check Standard

The morning check is now standardized and versioned in:

- `/root/about-site/ops/morning-check.standard.env`

This baseline is executed by:

- `/root/about-site/tools/repair-health-check.sh`
- `/root/about-site/projects/ollama-free-multi-agent/scripts/morning_system_check_and_ticket.sh`

Mandatory morning checks:

- required VPS containers: `headscale`, `grafana`, `loki`, `influxdb`, `promtail`, `traefik`, `crowdsec`, `bouncer-traefik`
- required systemd units: morning-check timer, agent export timer, control API service
- required TCP endpoints: `127.0.0.1:3000`, `127.0.0.1:8080`, `10.188.50.9:8007`
- required tailnet peers visible: `100.64.0.8`, `100.64.0.1`, `100.64.0.3`

Outputs:

- state file: `/root/about-site/reports/.state/repair-health-check.state`
- latest standard report: `/root/about-site/reports/morning-check-standard-latest.log`

Ticketing behavior:

- if morning status is `fail`, a `morning_check_failed` plan is created
- ticket context includes standard version and exact failed check keys
- debounce enabled: ticket submission starts only from `MORNING_TICKET_MIN_FAIL_COUNT` (default `2`)
- noise suppression: if only transient standard failures match `MORNING_TICKET_NOISE_REGEX` (default key-exchange / tailnet-peer), no ticket is opened

## Security Compliance Drift Check

To keep `security_analyst` active and standards-focused, a dedicated daily compliance check is installed:

- Script:
  - `scripts/security_compliance_check_and_ticket.sh`
- systemd:
  - `planetonyx-security-compliance-check.service`
  - `planetonyx-security-compliance-check.timer` (`04:20 UTC` daily)

What it does:

- runs `tools/bsi_quick_audit.sh`
- runs `tools/hardening_compliance_gate.sh`
- verifies freshness of:
  - `docs/BSI-200-2-GAP-ANALYSIS.md`
  - `docs/BSI-HARDENING-MAPPING.md`
  - `docs/CONTROL-EVIDENCE-MATRIX.md`
- opens `security_compliance_drift` plan when drift is persistent
  - debounce: `SECURITY_TICKET_MIN_FAIL_COUNT` (default `2`)
  - cooldown: `SECURITY_TICKET_COOLDOWN_SEC` (default `21600`)

Output artifacts:

- `reports/security-compliance-check-<timestamp>.log`
- `reports/security-compliance-check-latest.log`
- state: `runtime/.state/security-compliance.state`

## Evidence Refresh (Daily)

To keep compliance evidence artifacts fresh before security checks:

- Script:
  - `scripts/evidence_refresh.sh`
- systemd:
  - `planetonyx-evidence-refresh.service`
  - `planetonyx-evidence-refresh.timer` (`04:05 UTC` daily)

Behavior:

- regenerates governance review via `tools/generate_governance_review.py` (if available)
- refreshes latest pointers for:
  - `reports/restore-drill-latest.md`
  - `reports/dns-split-check-latest.md`
- writes:
  - `reports/evidence-refresh-<timestamp>.log`
  - `reports/evidence-refresh-latest.log`

## Grafana: Agent Control Plane

Export path:
- `scripts/agent_influx_export.sh` writes runtime metrics/events/model usage to Influx.

Systemd:
- `planetonyx-agent-influx-export.timer` (every minute)
- `planetonyx-agent-influx-export.service`

Dashboard:
- `PlanetOnyx Agent Control Plane`
- UID: `planetonyx-agent-control`
- provision JSON: `ops/grafana/planetonyx-agent-control-plane.json`

Heartbeats are written as JSON files in:

- `runtime/heartbeat/<agent>.json`

Ticket inbox for workers (per-agent queues):

- `runtime/queues/<agent>/inbox/*.json`
- Priority order per agent queue: `critical > high > medium > low` (non-preemptive: current ticket is always finished first)
- Retry behavior: retryable failures/timeouts are requeued automatically (`AGENT_TICKET_MAX_RETRIES`, default `3`)
- Ready templates: `runtime/inbox-templates/*.json`

## Communication Tactic

Policy file:

- `configs/communication_policy.json`

Routing model:

- `low`: local analysis only, no Telegram, no remote replication
- `medium`: local analysis + replicate ticket to citizen-stor
- `high`: local analysis + replicate ticket to citizen-stor
- `fatal`: local analysis + remote replication + Telegram (external alert pipeline)

Ticket submission helper:

```bash
chmod 750 scripts/agent_ticket.sh
./scripts/agent_ticket.sh --issue-type tailnet_degraded --summary "Tailnet flaps" --severity high
```

By default this writes to local per-agent queue and tries replication to:

- `root@100.64.0.8:/root/about-site/projects/ollama-free-multi-agent/runtime/queues/<agent>/inbox`

Override with:

- `REMOTE_HOST`
- `REMOTE_INBOX`

## citizen-stor Integration

The direct SSH deploy can fail if auth is not available from VPS. In that case run on citizen-stor:

```bash
cd /root/about-site/projects/ollama-free-multi-agent
chmod 750 scripts/install_citizen_worker_node.sh
./scripts/install_citizen_worker_node.sh
```

This installs the same 5 worker services and heartbeat/per-agent queue directories on citizen-stor.

Minimal ticket examples:

```json
{"mode":"ask","target_agent":"sre_diagnoser","task":"Check tailnet health assumptions","structured":true}
```

```json
{"mode":"issue","issue_type":"observability_nodata","summary":"Grafana logs panel empty","context":"Since 03:20 UTC","structured":true}
```

Use a template quickly:

```bash
cp runtime/inbox-templates/01-tailnet_degraded.json runtime/queues/sre_diagnoser/inbox/$(date -u +%Y%m%dT%H%M%SZ)-tailnet-01-sre_diagnoser.json
cp runtime/inbox-templates/03-observability_nodata.json runtime/queues/sre_diagnoser/inbox/$(date -u +%Y%m%dT%H%M%SZ)-observability-01-sre_diagnoser.json
```

German templates are also available:

- `runtime/inbox-templates/11-tailnet_degraded-de.json`
- `runtime/inbox-templates/13-observability_nodata-de.json`

## Agent Roles

- `sre_diagnoser`: runtime triage and incident analysis.
- `security_analyst`: security event triage, baseline control verification, and standards-drift analysis (BSI/CIS/OWASP-aligned).
- `performance_analyst`: performance and capacity recommendations.
- `documentarian`: CMDB/release/report drafting.
- `executor`: single write agent, limited to whitelisted recovery actions.

## Free Model Policy

Only open/free Ollama models are configured:

- `qwen3.5:397b`
- `qwen3-coder-next`
- `deepseek-v3.2`
- `glm-5`
- `kimi-k2.5`

These run via cloud endpoint (`https://ollama.com/api`) with API key auth; no local model pulls are required.

## Quick Start

1. Create venv and install dependencies.

```bash
cd /root/about-site/projects/ollama-free-multi-agent
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

2. Configure Ollama endpoints in `configs/ollama_endpoints.json`.

3. Validate model policy bootstrap (cloud-only no-op):

```bash
./scripts/bootstrap_models.sh
```

4. Run an agent task:

```bash
python -m src.cli ask --agent sre_diagnoser --task "Check tailnet stability assumptions"
```

5. Run multi-agent triage:

```bash
python -m src.cli triage --task "CrowdSec false-ban impact analysis"
```

6. Run issue-profile workflow (recommended):

```bash
python -m src.cli agents
python -m src.cli issues
```

7. Execute a safe repair action (single writer path, dry-run):

```bash
python -m src.cli exec --action observability
```

`--apply` is blocked by policy (`IST-only restore mode`).

## Executor Safety

The executor can only run these actions:

- `tailnet`
- `observability`
- `crowdsec`
- `backup-all`
- `repair-health-check`
- `run-state-reconcile`

All execution maps to:

```bash
/root/about-site/tools/ops-repair.sh [--apply] <action>
```

No arbitrary shell command execution is allowed.
Apply mode is blocked by policy in IST-only restore mode.

## Suggested Operations Pattern

- Open an issue (`issue --type ...`) first.
- Run read-only analysis from assigned agents.
- Let human approve action.
- Run `exec` with or without `--apply`.
- Record output in CMDB evidence.

## Issue Profiles

Defined in `configs/issue_profiles.json`:

- `tailnet_degraded` -> `tailnet`
- `observability_nodata` -> `observability`
- `security_signal_spike` -> `repair-health-check`
- `security_compliance_drift` -> no direct executor action (analysis-first)
- `security_standard_review` -> no direct executor action (scheduled monthly standards delta review)
- `pbs_unreachable` -> `tailnet`
- `crowdsec_false_ban` -> `repair-health-check`
- `runstate_drift` -> `run-state-reconcile`
- `syslog_ingest_drop` -> `observability`
- `cert_renewal_failure` -> no direct executor action (analysis-first)

Each issue profile defines a model strategy:

- `default_alias_chain`: global fallback chain
- `per_agent_alias_chain`: per-agent primary/fallback sequence

The response includes `model_used` for each agent so model routing is auditable.

## Worker Tools (Operator Shortcuts)

Quick helper script for common restore tickets:

```bash
scripts/worker_tools.sh reconcile --apply
scripts/worker_tools.sh observability --apply
scripts/worker_tools.sh backup --apply
```

The helper creates issue-profile tickets with `executor` included and uses
the configured whitelisted action path.

## Virtual Agent Profiles

Defined in `configs/agent_profiles.json` with:

- role
- write access flag
- function catalog per agent
- guardrails
- (executor only) allowed actions

Quick view:

```bash
python -m src.cli agents
python -m src.cli baseline
```

## Structured Outputs

Schemas are defined in:

- `configs/response_schemas.json`

Use structured mode for machine-readable output:

```bash
python -m src.cli ask --agent sre_diagnoser --function detect_failure_domain --task "Tailnet flap at 05:40 UTC" --structured
python -m src.cli triage --task "syslog ingest drop" --structured
python -m src.cli issue --type syslog_ingest_drop --summary "No data in Grafana logs panels" --structured
```

Issue profiles now map agent -> function (`function_plan`) and include model fallback defaults.
If a model returns invalid JSON or schema mismatch in structured mode, the next fallback model is tried automatically.

## CMDB Payload Export

Generate CMDB task/evidence CSV payloads directly from issue handling:

```bash
python -m src.cli cmdb-export \
  --change-id CHG-2026-9999 \
  --type syslog_ingest_drop \
  --summary "No logs in Grafana" \
  --context "Loki panel empty since 03:20 UTC"
```

Append directly to staging CSVs (`cmdb-templates`) if needed:

```bash
python -m src.cli cmdb-export \
  --change-id CHG-2026-9999 \
  --type syslog_ingest_drop \
  --summary "No logs in Grafana" \
  --append-staging
```

Append and immediately sync to live CMDB:

```bash
python -m src.cli cmdb-export \
  --change-id CHG-2026-9999 \
  --type syslog_ingest_drop \
  --summary "No logs in Grafana" \
  --append-staging \
  --sync
```

Strict mode for CI (non-zero exit if auto-sync fails):

```bash
python -m src.cli cmdb-export \
  --change-id CHG-2026-9999 \
  --type syslog_ingest_drop \
  --summary "No logs in Grafana" \
  --append-staging \
  --sync \
  --sync-strict
```

Offline export from a previously saved issue payload:

```bash
python -m src.cli cmdb-export \
  --change-id CHG-2026-9999 \
  --input-payload /root/about-site/reports/cmdb-issue-payload-<timestamp>.json
```
