# Ultimate Repair Center (URC)

URC is the operational repair framework for PlanetOnyx runtime stability.

## Mission
Keep systems in their documented current state ("IST-Zustand") with fast, deterministic incident handling and minimal human escalation.

## Scope
- Service health detection (containers, system services, endpoint checks)
- Automated incident ticket creation and prioritization
- Host-aware routing of incidents to the correct execution target
- Controlled repair execution with guardrails (restore-only, no redesign)
- Queue-based worker execution (one ticket at a time per worker)
- Runtime observability integration (status, queues, failed/open/completed)

## Explicitly Out of Scope
- Knowledgebase content and learning datasets
- Security strategy/compliance frameworks and security hardening policies
- Firewall design, firewall rule management, or network policy redesign

## Design Principles
- Restore-only: repairs return services to the latest accepted run-state
- No surprise changes: no ad-hoc port/policy redesign during incidents
- Full-chain recovery for coupled stacks (example: gluetun-dependent chains)
- Host ownership first: incidents must be handled on the owning host
- Priority aware, but finish current work before preemption

## Core Components
- `tickets/`: normalized incident/task definitions
- `workers/`: worker profiles and execution contracts
- `runbooks/`: deterministic repair procedures
- `ops/`: host/service ownership maps and queue policy
- `reports/`: execution outputs and operational logs

## Ticket Lifecycle
1. Detect event from health checks, logs, or SLO/SLI triggers
2. Classify and assign severity/priority
3. Route to owning host and responsible worker
4. Execute repair plan (restore-only)
5. Verify service recovery
6. Close ticket or escalate to human operator

## Update Policy
All platform changes are tracked in:
- `CHANGELOG.md` (human-readable release stream)
- Git commit history (atomic technical deltas)

## Initial Milestones
- M1: Baseline queue/worker model and host ownership routing
- M2: Deterministic runbooks per service class
- M3: Grafana-facing operational status model (open/failed/completed)
- M4: Cross-host consistency and failure-domain isolation
