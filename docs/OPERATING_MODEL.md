# URC Operating Model

## Purpose
URC controls incident handling for runtime operations and restores service availability to the documented accepted state.

## What URC Does
- Detects incidents from health checks, endpoint tests, and runtime logs.
- Creates standardized tickets with severity and priority.
- Routes tickets to the owning host and responsible worker profile.
- Executes deterministic restore runbooks.
- Verifies recovery and closes or escalates tickets.

## What URC Never Does
- No security governance or compliance management.
- No knowledgebase generation or KB lifecycle.
- No firewall changes, redesigns, or rule tuning.
- No ad-hoc architecture changes during incidents.

## Worker Roles
- `sre_diagnoser`: classify and scope incidents.
- `executor`: perform restore runbook actions.
- `performance_analyst`: analyze load/performance regressions.
- `documentarian`: update change/release trail.

## Ticket Priority Model
- `P1`: service outage / fatal business impact.
- `P2`: degraded critical service.
- `P3`: non-critical degradation with workaround.
- `P4`: maintenance / optimization.

## Execution Contract
- One worker handles one ticket at a time.
- A ticket is either `open`, `in_progress`, `resolved`, `escalated`, or `failed`.
- A worker must finish or escalate before starting a new ticket.

## Host Ownership Contract
- Services are mapped to owning hosts.
- Tickets are always routed to the owner host queue.
- Cross-host forwarding is allowed only through routing policy files.

## Update Contract
- Every operational change must be recorded in `CHANGELOG.md`.
- Release notes summarize impacts, rollback path, and verification checks.
