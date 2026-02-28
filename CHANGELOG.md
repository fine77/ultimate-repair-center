# Changelog

All notable changes to URC are documented in this file.

## [0.1.0] - 2026-02-27
### Added
- Initial project scaffold
- Clear scope and non-scope for URC
- Restore-only operational principles
- Ticket lifecycle definition
- Milestone roadmap

## [0.2.0] - 2026-02-27
### Added
- Initial URC runtime code (`src/urc`) published
- Control API with queue-aware plan submission and status
- Worker runtime with priority handling and one-ticket-at-a-time execution
- Sanitized config set without KB/security/firewall modules
- Systemd templates for URC API and workers

## [0.2.1] - 2026-02-28
### Added
- Worker stale-lock recovery for `runtime/queues/*/inbox/*.json.<agent>.lock` with configurable threshold (`URC_LOCK_STALE_SEC`, default 180s)
- Worker event logging entry `stale_lock_recovered` in `runtime/logs/events.jsonl`
- Dedicated incident runbook: `docs/INCIDENT_LOCK_RECOVERY.md`

### Changed
- Runtime documentation now includes lock-file path and event log path
- README and operating model now reference lock-recovery incident documentation
