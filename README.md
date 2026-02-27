# Ultimate Repair Center (URC)

URC is a restore-only incident handling framework for runtime operations.

## Mission
Return services to the accepted current run-state quickly and deterministically.

## Included in this repository
- Queue-based worker engine (one ticket at a time per worker)
- Control API for plan submission and status
- Multi-model cloud routing via Ollama API-compatible endpoints
- Restore-only executor integration hook
- Systemd unit templates for API and workers

## Explicitly excluded
- Knowledgebase modules and KB data flows
- Security/compliance automation modules
- Firewall/policy management and network redesign

## Layout
- `src/urc/`: runtime code (`control_api`, `worker`, `orchestrator`, `ollama_client`)
- `configs/`: model, agent, issue and schema config
- `scripts/`: helper scripts (plan submit)
- `ops/systemd/`: service templates
- `runtime/`: queue, plans, done/failed, heartbeat

## Quick Start
```bash
cd /root/ultimate-repair-center
PYTHONPATH=src python3 -m urc.control_api --base-dir /root/ultimate-repair-center --bind 127.0.0.1 --port 8765
```

In another shell:
```bash
cd /root/ultimate-repair-center
PYTHONPATH=src python3 -m urc.worker --agent sre_diagnoser --base-dir /root/ultimate-repair-center --interval-sec 10
```

Submit a ticket:
```bash
cd /root/ultimate-repair-center
URC_API_URL=http://127.0.0.1:8765 ISSUE_TYPE=manual_plan ./scripts/submit_plan.sh "Test restore plan"
```

## Update Policy
All updates must be posted in:
- `CHANGELOG.md`
- Git commit history on `main`
