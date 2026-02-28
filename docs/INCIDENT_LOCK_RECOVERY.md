# Incident Runbook: Stuck Queue Lock Recovery

## Scope
This runbook documents how URC handles and verifies stale queue lock recovery for worker tickets.

## Typical Symptom
- Ticket remains in queue as:
  - `runtime/queues/<agent>/inbox/*.json.<agent>.lock`
- No matching `ticket_done` or `ticket_failed` event appears for the same ticket.
- Queue appears blocked for the affected worker.

## Root Cause Pattern
- Worker claimed ticket and created lock file.
- Worker process terminated, timed out, or failed before lock cleanup.
- Queue item remained locked without progress.

## Implemented Standard
- Worker performs automatic stale-lock recovery each cycle.
- Recovery threshold is controlled by:
  - `URC_LOCK_STALE_SEC` (default: `180`)
- Recovery target:
  - `*.json.<agent>.lock` -> `*.json`
- Recovery audit:
  - Writes `stale_lock_recovered` event into `runtime/logs/events.jsonl`

## Validation Checklist
1. Check queue lock inventory:
   - `find runtime/queues -type f -name '*.lock'`
2. Check event stream:
   - `rg "stale_lock_recovered|ticket_done|ticket_failed" runtime/logs/events.jsonl`
3. Check affected worker queue:
   - Ticket should move to `done` or `failed`.
4. Check heartbeat freshness:
   - `runtime/heartbeat/<agent>.json`

## Operational SOP
1. Confirm stale lock age exceeds threshold.
2. Confirm no active processing exists for that ticket.
3. Allow worker auto-recovery to reclaim lock.
4. Verify ticket reaches terminal state (`done` or `failed`).
5. Record outcome in changelog and incident note.

## Manual Fallback (Only if Auto-Recovery Stalls)
1. Rename lock back to queue json:
   - `mv <ticket>.json.<agent>.lock <ticket>.json`
2. Recheck worker processing and terminal state.
3. Keep restore-only contract (no architecture change during incident).

## Evidence Artifacts
- Queue state:
  - `runtime/queues/<agent>/inbox|done|failed/`
- Events:
  - `runtime/logs/events.jsonl`
- Worker output:
  - `runtime/logs/<agent>.jsonl`

