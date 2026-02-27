# Update Process

## Goal
Publish all URC changes in one place (GitHub repository history + changelog).

## Required Steps per Update
1. Implement technical change.
2. Run verification checks.
3. Add a changelog entry in `CHANGELOG.md`.
4. Commit with a scoped message.
5. Push to `main`.

## Commit Message Convention
- `feat:` new capability
- `fix:` bug or regression repair
- `ops:` operational process/workflow change
- `docs:` documentation-only change

## Changelog Entry Template
```md
## [X.Y.Z] - YYYY-MM-DD
### Added
- ...
### Changed
- ...
### Fixed
- ...
```

## Minimal Verification Block
- Service status check
- Ticket flow check
- Queue processing check
- Post-check summary in commit body or PR description
