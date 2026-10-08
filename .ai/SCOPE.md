# Integration AI scope

Open **osmedeus-catalog** as the project/workspace. Paths below are relative to it
unless explicitly prefixed with `../osmedeus/`. This is a retrieval map, not a
request to read every listed file. Persistent entry point: `AGENTS.md`.

## Primary files: select by task

| Concern | Files |
| --- | --- |
| Engine upload adapter and receipt lifecycle | `catalog_companion/delivery.py`, `tests/test_delivery.py` |
| Authentication and HTTP routes | `catalog_companion/api.py`, `tests/test_companion.py` |
| Refresh, schedule and control persistence | `catalog_companion/state.py`, `bugbounty_catalog/application.py` |
| Immutable scope-safe exports | `catalog_companion/exports.py`, `tests/test_companion.py`, `tests/test_delivery.py` |
| Delivery command | `catalog_companion/ship.py`, `tests/test_delivery.py` |
| Program listing / explicit workflow handoff | `catalog_companion/programs.py`, `catalog_companion/run.py`, `catalog_companion/cli.py`, `tests/test_cli_handoff.py` |
| Browser scan batches, workflow mapping, monitoring/cancellation | `catalog_companion/batches.py`, `engine.py`, `batch_models.py`, `batch_api.py`, `tests/test_batches.py` |
| Provider ingestion | `bugbounty_catalog/sources/`, `bugbounty_catalog/http.py`, `tests/test_sources.py`, `tests/test_application.py` |
| Catalog identity, normalization, queries | `bugbounty_catalog/models.py`, `bugbounty_catalog/normalize.py`, `bugbounty_catalog/program_merge.py`, `bugbounty_catalog/query.py` |
| Catalog persistence | `bugbounty_catalog/db.py`, `tests/test_db.py`, `tests/test_repository.py` |
| Dashboard overlay | `overlay/bug-bounty/page.tsx`, `scan-control.tsx`, `delivery-panel.tsx`, `overlay/sidebar.patch`, `tests/browser/catalog.spec.ts`, `control.spec.ts`, `mocks.ts` |

`db.py` is large but cohesive: search the needed method (for example
`list_assets` or `apply_source_snapshot`) and its references, not the whole file.
Use matching `tests/test_<module>.py` for other catalog modules.

## Supporting files: load only for the current phase

- Contract/progress: `docs/integration/CONTRACT.md`, the relevant heading of
  `docs/integration/PLAN.md`, `docs/integration/STATUS.md`.
- Configuration: `upstream.json`, `.env.example`, `compose.yaml`, `pyproject.toml`;
  `requirements.lock`, `package.json`, `package-lock.json` only for dependency work.
- Packaging: `deploy/`, `scripts/dashboard.py`, `scripts/build_dashboard.py`.
- Gate/deployment: `scripts/check_upgrade.py`, `scripts/check-upgrade.ps1`,
  `scripts/deploy-checked.ps1`, `.github/workflows/compatibility.yaml`.
- Engine fixtures: `scripts/container_smoke.py`, `scripts/container_probe.py`,
  `tests/Dockerfile.engine`, `tests/smoke-input.yaml`, `tests/smoke-targets.txt`.
- Operations: the relevant heading of `README.md`; attribution: `NOTICE.md`.

## Third-party interface fallback, not routine context

Only inspect these when CONTRACT lacks a needed detail, at the pinned revision:

- `../osmedeus/docs/api/uploads.mdx`, `../osmedeus/docs/api/authentication.mdx`.
- `../osmedeus/docs/api/runs.mdx`, `../osmedeus/docs/api/workflows.mdx`
  (explicit CLI handoff contracts, not automatic scan submission).
- `../osmedeus/pkg/server/server.go` (route registration),
  `../osmedeus/pkg/server/handlers/uploads.go` (upload response),
  `../osmedeus/pkg/server/handlers/auth.go`,
  `../osmedeus/pkg/server/handlers/runs.go` (read-only auth probe).
- `../osmedeus/pkg/cli/run.go` (`-T` input interface).
- `../osmedeus/platform/osmedeus-dashboard/components/layout/app-sidebar.tsx`
  (overlay patch anchor only). Trace imports for a particular UI compatibility issue.

Trafalgar is extraction provenance, not a runtime dependency or routine context.
Do not explore its assessment, BBOT, frontend or job infrastructure.

## Normally excluded

`.ignore` filters ripgrep discovery, including with `--hidden`: Git internals,
virtual environments, dependencies/caches, `build/` (including upstream clones),
`data/` (SQLite, exports, backups), logs, runtime/results/artifacts and browser
reports. Existing `.gitignore` also keeps current generated output untracked.
Avoid unrelated docs/tests and upstream internals. Do not broadly exclude JSON,
CSV, YAML or JSONL extensions: configuration and tiny fixtures must remain visible.
Do not relocate existing data; container runtime state stays in its dedicated volume.

## Retrieval and isolation

Start with a named file or a narrow command such as
`rg -n 'DeliveryManager|def submit' catalog_companion/delivery.py` or
`rg --files catalog_companion overlay tests -g '*delivery*'`.
Expand through references only when necessary; summarize new interface facts in CONTRACT.

Codex discovers project `AGENTS.md` along the project-root-to-working-directory
chain ([official guidance](https://learn.chatgpt.com/docs/agent-configuration/agents-md)).
Start a new session in this companion so its entry instructions load; a session
rooted in sibling `osmedeus` does not automatically inherit them.
No `.aiignore` enforcement was established for this environment. `.ignore` is
verified ripgrep filtering, not a Codex access policy: explicit file reads or
ignore overrides can bypass it. Hard read isolation requires host-managed
permissions/a narrower workspace; this task does not change those controls.
