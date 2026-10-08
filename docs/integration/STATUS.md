# Integration status

## Completed

- Backup recovers pruned images from surviving container filesystems, verifies
  startup settings and retains catalog data separately; seven regression tests.

- One-command changed-code deployment and watch mode added, with rollback backup,
  checked build identities, existing engine startup and dashboard auto-refresh.
- README documents combined engine/catalog deployment, pinned builds, runtime
  configuration, network options, verification and operations.
- Independent provider catalog, scope-safe immutable exports, authenticated API,
  scheduled refresh, dashboard overlay and checked Compose deployment tooling.
- Explicit engine file delivery, persisted receipts/retry and shipping CLI;
  no engine source/database changes.
- 0.3.0 adds program/workflow listing and piped receipt handoff. Run preparation
  is read-only; scan submission requires explicit submit and scope confirmation.
- 143 Python tests and two browser tests passed. Full 0.3.0 gate passed, including
  real upload, offline target-file consumption and explicit log-only API handoff
  on an internal Docker network; no external-target scans.
- Persistent contract, scoped phase plan, retrieval map, AI entry instructions and
  generated/runtime search exclusions added without functional changes.

## Current

- Last verified deployed companion/UI: 0.4.0 (2026-10-06), localhost:8080.
  Checked release: `0.4.0-osm-9a02ed0f9dbd-build-59bb7f7e64d003c6`.
- Send-to-scan preparation constrains selected IDs in SQL and streams exports
  without browser statistics. Loading/timeout feedback and progress restore fixed.
- Read-only timing on the real catalog: selected program 0.02s; all 947 matching
  programs / 34,785 targets 21.2s. Export benchmarks performed no uploads/scans.
- Automatic/Full recon routing and Send to scan -> Launch scans are deployed.
  Backend mappings/defaults are frozen; changed inputs/catalog invalidate review.
  Active organization falls back to Default; wildcard discovery defaults on,
  with optional opt-out. Extra controls are collapsed and progress restores.
- Legacy manual requests, persisted batches and delivery/recovery remain supported.
- 212 Python tests, ten browser tests and all ten compatibility checks pass.
  Mixed routing, exclusions, missing workflows/defaults and stale reviews are
  covered; engine checks use harmless fixtures on an internal Docker network.
- Health, login/Bug Bounty/Scans pages, missing-token rejection, uncached refresh
  assets and the deployed source/build identity verified against running services.
- Engine remains at v5.1.1 with both managed workers running. Its binary reports
  an empty commit field; deployment checks revision only when metadata supplies it.
- Existing catalog volume retained. Stopped-service data backup, previous report
  and recovered rollback images retained at
  `build/backup-pre-deploy-20261006-133352-906/`.
- Windows deployment handles WSL interpreter symlinks/argument passing, avoids
  redundant editable-package builds and excludes unmanaged engine containers.

## Pending

- Future upstream upgrades must pass the compatibility gate before promotion.

## Known Issues

- Lost upload acknowledgements can leave unconfirmed remote duplicates on retry.
- Uploads have no cancellation endpoint. Scan cancellation is per known run,
  not atomic for a whole job; uncertain/missing runs leave cancellation incomplete.
- The pinned engine can finalize an acknowledged cancellation as a failed run.
  The gate checks acknowledgements and terminal records for all accepted targets.
- Run submission has no idempotency guarantee; inspect engine jobs after an
  uncertain response before manually submitting again.
- Existing engine scan form is not auto-prefilled; 0.4.0 adds companion browser
  review/launch/monitoring. Submission remains explicit, not triggered by refresh.
- Trafalgar licensing must be reviewed before publishing; see NOTICE.md.
- Search ignores/instructions do not enforce filesystem read isolation. Open this
  companion as the workspace for automatic project instruction discovery.

## Important Decisions

- CONTRACT owns interface facts; PLAN owns phase scope; README owns operations.
- Keep existing adapter boundaries; no speculative refactoring or large-file splitting.
- Preserve Osmedeus/Trafalgar checkouts and existing local deployment/data changes.
- No automatic delivery on refresh, publishing, deployment or scan submission.
