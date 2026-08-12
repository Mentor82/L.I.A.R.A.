# SRC Wrapper Removal Checklist

Last updated: 2026-04-14

## Status: COMPLETED

`src/` has been deleted. All canonical implementations live in `services/*`.

## Completed Steps

- [x] All runtime and test imports migrated from `src.*` to `services.*`
- [x] All `src/*` files annotated as compatibility wrappers
- [x] `src/` folder removed — `112 passed` after deletion confirmed zero regressions
- [x] Documentation updated to reflect canonical `services/*` paths

## Canonical Package Map

| Old (removed) | Canonical |
|---|---|
| `src/config` | `services/config` |
| `src/contracts` | `services/contracts` |
| `src/shared` | `services/shared` |
| `src/memory` | `services/memory` |
| `src/tools` | `services/tools` |
| `src/core` | `services/orchestrator` |
| `src/services/inference` | `services/inference` |
| `src/services/memory_adapter` | `services/memory_adapter` |
