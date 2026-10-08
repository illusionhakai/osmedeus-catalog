# Integration maintenance plan

Phases 1-9 are implemented, checked and deployed at 0.3.0; do not rebuild them as
a backlog. Read only the phase relevant to the current task; CONTRACT defines behavior,
STATUS records readiness, README owns operational commands.

Each task specifies **Objective / Relevant files / Allowed scope / Acceptance
criteria**. Work on one feature/phase; expand through references only if necessary.

## Phase 1 - External interface contract

- Objective: preserve the black-box engine boundary.
- Files: `docs/integration/CONTRACT.md`, `upstream.json`, `.ai/SCOPE.md`.
- Changes: only missing/changed interface facts; no routine upstream exploration.
- Depends on: a specific compatibility question or proposed pin.
- Complete when: facts match the relevant public interface and paths exist.

## Phase 2 - Upload adapter

- Objective: deliver immutable inputs without engine changes or run submission.
- Files: `catalog_companion/delivery.py`, `tests/test_delivery.py`.
- Changes: narrowly scoped upload validation or receipt mapping, if requested.
- Depends on: phase 1 and existing export contract (phase 4).
- Complete when: auth, malformed responses, checksums, retries and no-run tests pass.

## Phase 3 - Execution lifecycle

- Objective: serialized refresh/delivery, restart recovery and visible progress.
- Files: `catalog_companion/state.py`, `catalog_companion/delivery.py`, `catalog_companion/ship.py`,
  `catalog_companion/programs.py`, `catalog_companion/run.py`, `catalog_companion/cli.py`, `tests/test_cli_handoff.py`.
- Changes: only requested scheduler, retry or command lifecycle behavior.
- Depends on: phase 2; persisted state in phase 5.
- Complete when: overlap, interruption, timeout and explicit-retry tests pass;
  program listing, receipt piping and opt-in run submission pass with no default scan.

## Phase 4 - Normalization and exports

- Objective: scope-safe inputs with program-local provenance/exclusions.
- Files: `bugbounty_catalog/sources/`, `normalize.py`, `program_merge.py` in that
  package; `catalog_companion/exports.py`; matching catalog/companion tests.
- Changes: only a specified provider or normalization/export rule; synthetic fixtures.
- Depends on: phase 1 scope/export contract.
- Complete when: duplicate identity, removals, stale snapshots, wildcard/apex,
  exclusion/CIDR overlap and all-page consistent export regressions pass.

## Phase 5 - Persistence

- Objective: preserve owned catalog and control state, never engine DB access.
- Files: `bugbounty_catalog/db.py`, `catalog_companion/state.py`, lifecycle section
  of `catalog_companion/api.py`; `tests/test_db.py`, `tests/test_repository.py`.
- Changes: migrations only for a concrete requirement; backup/rollback first.
- Depends on: phase 4 models and phase 3 lifecycle.
- Complete when: snapshot atomicity, fresh/reopened DB and migration tests pass.

## Phase 6 - Backend API

- Objective: authenticated versioned queries/export/delivery routes.
- Files: `catalog_companion/api.py`, `tests/test_companion.py`, `tests/test_delivery.py`.
- Changes: requested route/schema only; update CONTRACT if externally observable.
- Depends on: phases 2-5.
- Complete when: auth failures fail closed and validation/pagination/status tests pass.

## Phase 7 - Dashboard overlay

- Objective: catalog browsing and explicit delivery within the existing theme.
- Files: `overlay/bug-bounty/`, `overlay/sidebar.patch`, `tests/browser/catalog.spec.ts`.
- Changes: owned components; do not patch the user's upstream checkout.
- Depends on: phase 6.
- Complete when: types/lint/build and login/filter/export/delivery/outage tests pass;
  existing engine pages remain usable with catalog unavailable.

## Phase 8 - Compatibility verification

- Objective: gate each interface change or proposed upstream pin without external scans.
- Files: `scripts/check_upgrade.py`, `scripts/check-upgrade.ps1`,
  `scripts/container_smoke.py`, `scripts/container_probe.py`, `tests/smoke-input.yaml`,
  `tests/smoke-targets.txt`, `.github/workflows/compatibility.yaml`.
- Changes: smallest failing-contract regression first; no production datasets.
- Depends on: affected phases above.
- Complete when: `scripts/check-upgrade.ps1` passes at the exact proposed pin,
  including real upload and offline delivered-input consumption; inspect the small
  resulting `build/compatibility.json` only when deciding promotion.
- For documentation/ignore-only changes: validate links/search scope and run
  `.venv\Scripts\python.exe -m pytest -q`; no dashboard/Go/Docker rebuild required.

## Phase 9 - Packaging and deployment

- Objective: promote checked companion releases with one command and refresh the dashboard.
- Files: `compose.yaml`, `.env.example`, `upstream.json`, `deploy/`,
  `scripts/deploy-checked.ps1`, `scripts/deploy.ps1`, `scripts/deploy.py`,
  `scripts/deployment.py`, `deploy/live-reload.js`, setup/rollback sections of `README.md`.
- Changes: deployment configuration only if needed; do not change the engine.
- Depends on: phase 8 evidence matching source/images plus an explicit deployment task.
- Complete when: previous artifacts retained, catalog backed up, checked companion
  images promoted and health/login/catalog verified; record deployed version in STATUS.
- Deployment of 0.3.0 completed with user approval; future promotions require a
  separate task. Publishing, real scan execution and unattended credential storage
  remain separate decisions. Never send real program data just to validate docs.

## Phase 10 - Browser scan control center (0.4.0)

- Objective: select scope -> one scan mode with backend routing -> immutable review
  -> one confirmed launch -> persistent monitoring/cancellation.
- Files: `overlay/bug-bounty/page.tsx`, `scan-control.tsx`, owned `batch_models.py`,
  `batch_api.py`, `batches.py`, `engine.py`, export/query changes and matching tests.
- Allowed scope: companion only, authenticated public engine APIs, no credentials
  in persistence, no implicit scans or scan schedules, unchanged pinned engine.
- Acceptance: same-asset filters/counts and all-page selection; one mode menu,
  active/default org and two-action review/launch; frozen automatic workflow mapping;
  previews invalidated before approval on selection/settings/snapshot changes;
  persisted intents and uncertain outcomes without automatic resubmission;
  bounded per-batch execution, failure pause/resume, progressive run monitoring,
  honest non-atomic cancellation; browser and isolated real-engine gate pass.
- Rollout: retain the previously deployed 0.4.0 artifacts and stopped-service backup;
  promote the simplified flow through the checked 0.4.0
  deployment at localhost:8080. User authorized this local deployment in the plan.
