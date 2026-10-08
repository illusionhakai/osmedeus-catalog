# Osmedeus Catalog companion

Trafalgar's Rix Scope, Chaos and Arkadiyt catalog, independently packaged for
Osmedeus. It gathers program scope, displays a **Bug Bounty** page in an overlaid
Osmedeus dashboard, and delivers scan-ready inputs directly to the engine through
its existing authenticated file-upload API. ZIP download remains available.

Refresh and delivery alone never submit scans. The browser scan control center
and separate run CLI submit only with explicit confirmation. Neither Trafalgar nor the existing Osmedeus checkout is
a runtime dependency. No Osmedeus database, engine source, settings or signing
secret is changed or copied. A fresh SQLite catalog lives in its own Docker volume.

For a complete installation, see [Deploy Osmedeus and the catalog together](#deploy-osmedeus-and-the-catalog-together).

For development, open this companion as the workspace and start with
[AI scope](.ai/SCOPE.md). The [integration contract](docs/integration/CONTRACT.md),
[phase plan](docs/integration/PLAN.md) and [status](docs/integration/STATUS.md)
provide compact context; this README remains the operational guide.

## Ownership and compatibility

- Engine/dashboard baseline: `v5.1.1`, commit
  `9a02ed0f9dbd3d9fd7f006610264016058fd8a0e`, recorded in `upstream.json`.
- Extracted Trafalgar revision and licensing limitations: [NOTICE.md](NOTICE.md).
- `bugbounty_catalog/`: extracted normalization, merging, providers and SQLite queries.
- `catalog_companion/`: authentication, API, scheduler, persisted jobs, immutable exports.
- `overlay/`: companion-owned page and one sidebar patch. Builds use a clean,
  detached, disposable upstream checkout under `build/`; never your checkout.
- `deploy/`, `scripts/`, `tests/`: gateway, deployment, compatibility gate and fixtures.

The upstream project is independently maintained. Future compatibility is not
guaranteed. An upgrade must pass the gate and be explicitly promoted. There is no
automatic upstream-main tracking, deployment, publishing or scan submission.

## Direct delivery pipeline

```text
Provider refresh -> catalog snapshot -> scope-filtered export
  -> POST /osm/api/upload-file -> engine-side target files + delivery receipt
```

In **Bug Bounty**, the program list shows only active programs with an in-scope domain,
wildcard, IP or CIDR that survives its own exclusions. Programs without usable
inputs remain stored; they are hidden from this list, not deleted. Wildcard
visibility does not enable discovery-root conversion or authorize the apex.

Open a program, choose filters/assets, create an export preview and click
**Send preview to Osmedeus**. The companion uploads nonempty domain,
IP, CIDR and opted-in discovery-root files, separately for each canonical program,
plus scope rules, exclusions, manifest and instructions. **Ready in Osmedeus**
means every file was acknowledged by the engine, with validated path, size and
line-count responses. Its files now exist in the engine's own `data/uploads/`.
There is no ZIP unpacking, shared volume or manual Docker copy step.

The delivery panel provides server paths, `-T` CLI templates, API run-request
templates and a downloadable receipt with source-file checksums. Run the CLI
inside the engine environment; do not pass its Linux paths to a Windows binary.
For the API, use the returned `target_file` with a reviewed `flow` or `module`.
Templates are **not executed**. For browser-based scans, use the companion's
**Send to scan** panel below; the upstream New Scan page remains unchanged.
No programs are inserted directly into discovered-asset tables.

Delivery reuses your login bearer token in memory only. Jobs and confirmed file
paths persist across restarts, but credentials do not. Duplicate requests for a
completed export return its receipt; one delivery runs at a time. On partial
failure/restart, retry is explicit and confirmed files are skipped. Osmedeus's
upload endpoint has no idempotency/readback/delete contract: a response lost
after acceptance can leave an unconfirmed duplicate on retry. The UI warns about
this; the companion never claims a partially delivered export is ready and never
deletes uploads. Checksums describe sent bytes; the upload API does not provide
a server-side checksum/readback guarantee.

Uploaded snapshots do not change under a later refresh, and old inputs may no
longer reflect current scope. Generate and deliver a fresh preview after a scope
change. If the engine's storage is replaced/cleaned, old receipts are historical:
deliver a new preview. Manual delivery requires discovery-root opt-in; roots do not authorize the apex.

## Browser scan control center (0.4.0)

1. Select programs across pages, **Select all matching programs**, or specific assets.
2. Keep **Automatic**, or choose **Full recon** in the single **Scan mode** menu.
3. Click **Send to scan**. Review the organization, targets, workflows and omissions.
4. Click **Launch scans** to confirm authorization, scope and destination; delivery starts automatically.
5. Follow progress below. Reloads restore active batches; older batches are under **Scan history**.

| Target | Automatic | Full recon |
| --- | --- | --- |
| Domain/subdomain | `enum-subdomain` module | `domain-standard` flow |
| IP / CIDR | `cidr` flow | `cidr` flow |
| Wildcard discovery root | `enum-subdomain` module | `enum-subdomain` module |

The active organization is used, otherwise Default. Wildcard discovery is included
by default; it does not authorize scanning the apex. **Advanced settings** provides
an opt-out and concurrency controls (both default to one). **Export tools** and
**Exports and delivery** contain ZIP/receipt alternatives.

Selection, mode, organization or catalog changes invalidate an unconfirmed review.
Workflow mappings/defaults are frozen in the batch; missing workflows or required
parameters block preparation. Preparing a review performs no upload or scan.

Failures pause new dispatch. Resolve failed/uncertain items explicitly and Resume;
expired login or service restart requires authenticated resume. **Cancel batch**
stops unsent work and requests cancellation of known active runs. Missing or
uncertain jobs leave cancellation incomplete. Scope exclusions remain available
in the review; arbitrary workflows do not automatically enforce them.

### Deliver from the CLI

```powershell
# Deliver one program; prompts for the password and starts no scan.
.\.venv\Scripts\python.exe -m catalog_companion.ship --program-id 1 --username YOUR_USER
# Refresh first, or retry an existing export.
.\.venv\Scripts\python.exe -m catalog_companion.ship --program-id 1 --refresh --username YOUR_USER
.\.venv\Scripts\python.exe -m catalog_companion.ship --export-id EXPORT_ID --retry --username YOUR_USER
```

Use `--all-programs` for bulk delivery, `--source` / `--platform` to filter, and
`--convert-wildcards` to opt into discovery. Retry skips confirmed uploads;
inspect uncertain attempts before retrying.

### List programs and prepare or submit a workflow

```powershell
.\.venv\Scripts\python.exe -m catalog_companion.programs --search example --json --username YOUR_USER
.\.venv\Scripts\python.exe -m catalog_companion.run --list-workflows --search recon --username YOUR_USER
# Pipe a delivery receipt into a reviewed request; starts no scan.
.\.venv\Scripts\python.exe -m catalog_companion.ship --program-id 1 --username YOUR_USER |
  .\.venv\Scripts\python.exe -m catalog_companion.run --receipt-stdin --flow domain-standard --target-kind domains --username YOUR_USER
# Explicitly submit a prepared export.
.\.venv\Scripts\python.exe -m catalog_companion.run --export-id EXPORT_ID --flow domain-standard --target-kind domains --submit --acknowledge-scope --username YOUR_USER
```

For multi-program receipts, select `--program-id`. Discovery targets require
`--allow-discovery-roots`. Submission is not idempotent: inspect Scans after an
uncertain response before submitting again. Receipt input re-fetches the saved
export; it does not trust supplied paths.

## Deploy Osmedeus and the catalog together

### Deploy code changes with one command

After initial engine setup, deploy changed catalog/UI code and open the dashboard:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/deploy.ps1
```

Keep watching code changes and refreshing open dashboard pages:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/deploy.ps1 -Watch
```

Add `-NoOpen` to keep the current browser tab; add `-Force` to recreate services
from checked images. Press `Ctrl+C` to stop watching.

Use steps 1–3 for a fresh engine. For an existing engine, keep its credentials and volumes, verify its pinned revision and authentication, then start at step 4.

### 1. Prepare Windows / WSL

Install Docker Desktop (Linux containers), Git, WSL Ubuntu, Python 3.11+ and Node.js 22/npm.

```powershell
docker version
docker compose version
wsl -d Ubuntu -- python3 --version
wsl -d Ubuntu -- node --version
wsl -d Ubuntu -- npm --version
```

Change the checkout path for your machine. Clone the pinned engine:

```powershell
Set-Location C:\Users\Hakai\Documents\bbcat\osmedeus-catalog
$catalogRoot = (Get-Location).Path
$pin = Get-Content -LiteralPath upstream.json -Raw | ConvertFrom-Json
$engineSource = Join-Path $catalogRoot 'build/engine-source'
New-Item -ItemType Directory -Path build -Force | Out-Null
if (Test-Path -LiteralPath $engineSource) { throw 'Use a new disposable engine-source directory, or verify the existing checkout before proceeding.' }
git clone --no-checkout $pin.osmedeus_repository $engineSource
if ($LASTEXITCODE -ne 0) { throw 'Engine clone failed' }
git -C $engineSource checkout --detach $pin.osmedeus_revision
if ($LASTEXITCODE -ne 0) { throw 'Pinned engine checkout failed' }
git -C $engineSource rev-parse HEAD
```

### 2. Configure the engine

Generate credentials once in `build/engine.env`:

```powershell
$engineEnv = Join-Path $catalogRoot 'build/engine.env'
if (Test-Path -LiteralPath $engineEnv) { throw 'Keep the existing engine.env; do not overwrite deployment secrets.' }
function New-DeploymentSecret {
    $bytes = New-Object byte[] 32
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $generator.GetBytes($bytes) } finally { $generator.Dispose() }
    return ([BitConverter]::ToString($bytes)).Replace('-', '').ToLowerInvariant()
}
@(
    'POSTGRES_USER=osmedeus'
    "POSTGRES_PASSWORD=$(New-DeploymentSecret)"
    'POSTGRES_DB=osmedeus'
    "ENGINE_ADMIN_PASSWORD=$(New-DeploymentSecret)"
    "ENGINE_JWT_SECRET=$(New-DeploymentSecret)"
    'OSM_SERVER_PORT=127.0.0.1:8002'
    'WORKER_REPLICAS=2'
    'TZ=America/Sao_Paulo'
) | Set-Content -LiteralPath $engineEnv -Encoding ASCII
```

Save as `build/engine.override.yaml`:

```yaml
services:
  server:
    image: osmedeus:v5.1.1-9a02ed0f9dbd
    build:
      args:
        COMMIT_HASH: 9a02ed0f9dbd3d9fd7f006610264016058fd8a0e
    environment: &engine-environment
      TZ: ${TZ:-America/Sao_Paulo}
      OSM_DATABASE_DB_ENGINE: postgresql
      OSM_DATABASE_HOST: postgres
      OSM_DATABASE_PORT: "5432"
      OSM_DATABASE_USERNAME: ${POSTGRES_USER:-osmedeus}
      OSM_DATABASE_PASSWORD: ${POSTGRES_PASSWORD:?required}
      OSM_DATABASE_DB_NAME: ${POSTGRES_DB:-osmedeus}
      OSM_DATABASE_SSL_MODE: disable
      OSM_REDIS_HOST: redis
      OSM_REDIS_PORT: "6379"
      OSM_REDIS_PASSWORD: ""
      OSM_ENVIRONMENTS_WORKSPACES: /root/workspaces-osmedeus
      OSM_SERVER_SIMPLE_USER_MAP_KEY_ADMIN: ${ENGINE_ADMIN_PASSWORD:?required}
      OSM_SERVER_JWT_SECRET_SIGNING_KEY: ${ENGINE_JWT_SECRET:?required}
  worker:
    image: osmedeus:v5.1.1-9a02ed0f9dbd
    build:
      args:
        COMMIT_HASH: 9a02ed0f9dbd3d9fd7f006610264016058fd8a0e
    environment: *engine-environment
```

### 3. Build and start Osmedeus

```powershell
$engineDocker = Join-Path $engineSource 'build/docker'
$engineCompose = @(
    '--project-name', 'osmedeus'
    '--project-directory', $engineDocker
    '--env-file', $engineEnv
    '-f', (Join-Path $engineDocker 'docker-compose.production.yaml')
    '-f', (Join-Path $catalogRoot 'build/engine.override.yaml')
)
docker compose @engineCompose config --quiet
if ($LASTEXITCODE -ne 0) { throw 'Engine Compose configuration is invalid' }
docker compose @engineCompose build server worker
if ($LASTEXITCODE -ne 0) { throw 'Engine build failed' }
docker compose @engineCompose up -d --wait
if ($LASTEXITCODE -ne 0) { throw 'Engine startup failed; inspect service status and logs' }
docker compose @engineCompose ps
docker compose @engineCompose exec -T server osmedeus version
Invoke-RestMethod http://localhost:8002/health
```

Open `http://localhost:8002`. Log in as `admin` with `ENGINE_ADMIN_PASSWORD` from `build/engine.env`.

### 4. Check the catalog release

Adjust the WSL paths, then run:

```powershell
if (-not (Test-Path -LiteralPath .env)) { Copy-Item .env.example .env }
wsl -d Ubuntu --cd /mnt/c/Users/Hakai/Documents/bbcat/osmedeus-catalog -- python3 -m venv .venv-linux
wsl -d Ubuntu --cd /mnt/c/Users/Hakai/Documents/bbcat/osmedeus-catalog -- .venv-linux/bin/python -m pip install -r requirements.lock -e '.[test]'
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/check-upgrade.ps1
if ($LASTEXITCODE -ne 0) { throw 'Compatibility gate failed; do not deploy the catalog' }
```

On Linux, run the gate directly:

```bash
python3 -m venv .venv-linux
.venv-linux/bin/python -m pip install -r requirements.lock -e '.[test]'
npx --yes --package=bun@1.3.11 -- .venv-linux/bin/python scripts/check_upgrade.py
```

### 5. Start the catalog — Docker Desktop

Set catalog `.env`:

```dotenv
OSMEDEUS_URL=http://host.docker.internal:8002
CATALOG_PORT=8080
CATALOG_RELEASE=0.4.0-osm-9a02ed0f9dbd
```

Validate the passing report and start the tested images:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/deploy-checked.ps1 -ValidateOnly
if ($LASTEXITCODE -ne 0) { throw 'Report, pin or tested images do not match' }
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/deploy-checked.ps1
docker compose ps
docker compose logs --tail 50 catalog gateway
```

### Optional: use the shared Docker network

Set `OSMEDEUS_URL=http://osmedeus-server:8002` in catalog `.env`. Save as `build/catalog-network.override.yaml`:

```yaml
services:
  catalog:
    networks: [default, engine]
  gateway:
    networks: [default, engine]
networks:
  engine:
    external: true
    name: osmedeus-network
```

Use this instead of the normal startup command. On Linux, replace `powershell` with `pwsh`:

```powershell
$checked = Get-Content -LiteralPath build/compatibility.json -Raw | ConvertFrom-Json
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/deploy-checked.ps1 -ValidateOnly
if ($LASTEXITCODE -ne 0) { throw 'Checked deployment validation failed' }
$previousRelease = $env:CATALOG_RELEASE
$env:CATALOG_RELEASE = $checked.release
try {
    docker compose -f compose.yaml -f build/catalog-network.override.yaml up -d --no-build
    if ($LASTEXITCODE -ne 0) { throw 'Companion startup failed' }
} finally { $env:CATALOG_RELEASE = $previousRelease }
```

Include both Compose files in subsequent catalog `up` commands.

### 6. Verify

Open `http://localhost:8080`, log in with the engine credentials, and open **Scans** and **Bug Bounty**.

```powershell
Invoke-RestMethod http://localhost:8080/health
docker compose exec -T catalog python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8090/health').status)"
curl.exe -i http://localhost:8080/catalog-api/v1/overview
```

Confirm both health checks succeed and the unauthenticated overview returns `401`.

### Operate

Keep using the variables from step 3; recreate them in a new PowerShell session.

```powershell
docker compose @engineCompose logs --tail 50 postgres redis server worker
docker compose @engineCompose up -d --no-build --scale worker=3
docker compose @engineCompose exec -T server osmedeus worker status
```

Back up both deployments before upgrades. Follow [Upgrades, backup and rollback](#upgrades-backup-and-rollback).

Stop both stacks while keeping their data:

```powershell
docker compose stop
docker compose @engineCompose stop
```

## Configuration and refresh

`.env.example` documents `OSMEDEUS_URL`, `CATALOG_PORT` and the image release tag.
The checked deployment command selects the release from the passing report.
Runtime schedule settings are stored in `control.sqlite`, not the Osmedeus DB.
Use **Refresh schedule** on the Bug Bounty page to change the time, IANA timezone,
enabled state, and VDP inclusion. Defaults: daily **03:00 America/Sao_Paulo**,
VDPs excluded where the provider supports this setting.

An empty catalog triggers an initial refresh. The scheduler checks every 30
seconds and catches up once after today's scheduled time if the service was
offline. Manual and scheduled refreshes share one serialized worker. Overlapping
requests return the current job instead of starting another; interrupted jobs
are marked failed at the next startup. Run exactly one service process/replica;
a data-directory ownership lock rejects a second owner.

Each provider's valid snapshot commits independently. Failed, malformed or empty
responses keep the provider's last successful snapshot. The page shows the last
success, age and latest error. Successful removal of a program/scope updates its
active state/provenance without borrowing exclusions from unrelated programs.
Provider failures do not make old data current: always review the live policy.

The catalog is shared among authenticated engine users. It is **not** scoped by
Osmedeus's organization selector and is separate from discovered scan assets.
Any authenticated user can refresh, change schedules and create/download exports.

Authentication forwards the browser's bearer token to the fixed read-only
`/osm/api/runs?limit=1` endpoint. A deliberately invalid-token probe must first be
rejected by the engine. No-auth mode, redirects, unexpected response bodies,
timeouts and engine failures fail closed; no signing key is present here.

## Exports and subsequent scans

Open a program, filter its assets, select individual entries across pages or
choose **Preview all matching inputs**. The latter includes every matching page,
not only the visible 50 rows. Wildcard conversion is off by default. Preview
creates an immutable ZIP; downloading that preview cannot change under a refresh.

Each `program-ID/` directory contains:

- `domains.txt`, `ips.txt`, `cidrs.txt`: separate UTF-8 concrete inputs.
- `discovery-roots.txt`: only explicitly requested `*.example.com` to `example.com`
  conversions. This **does not authorize the apex**.
- `scope.json`, `exclusions.txt`: preserved rules, wildcards and scope evidence.

`manifest.json` has schema version 1, program identity, source records, snapshot
timestamps, filters, counts, conversions, omissions and SHA-256 checksums for all
payload files (not the manifest itself). Inactive/closed programs and candidate
entries are omitted from scan-input files. Explicit exclusions override matching
inclusions. A CIDR overlapping an excluded IP/network is omitted wholesale,
with a reason; it is not silently narrowed. Exclusions stay within each canonical
program. Unsupported targets remain metadata rather than becoming scan seeds.

After extracting a ZIP, use the documented
[`-T` target-file interface](https://docs.osmedeus.org/getting-started/cli):

```text
osmedeus run -f YOUR_REVIEWED_WORKFLOW -T /inputs/program-1/domains.txt
osmedeus run -f YOUR_REVIEWED_IP_WORKFLOW -T /inputs/program-1/ips.txt
osmedeus run -f YOUR_REVIEWED_CIDR_WORKFLOW -T /inputs/program-1/cidrs.txt
# Separate, optional discovery inputs; review wildcard policy first:
osmedeus run -f YOUR_REVIEWED_DISCOVERY_WORKFLOW -T /inputs/program-1/discovery-roots.txt
```

Those names are placeholders for your reviewed workflows, not bundled scans.
Copy/mount inputs into the engine's filesystem if it runs in Docker; a Windows
download path is not automatically available inside the engine container.
Scope metadata is **not automatically enforced** by arbitrary workflows. DNS,
redirects, enumeration and follow-up scans may leave scope. Verify current policy
and enforce restrictions in your chosen workflows before scanning.

## Companion API v1

Every route below requires `Authorization: Bearer <Osmedeus token>` and is beneath
`/catalog-api/v1/`. `/health` on the internal service is the only public probe.

| Method/path | Purpose |
| --- | --- |
| GET `overview` | Counts, provider status, last successful snapshots, age/errors |
| GET `programs` | `q`, `source`, `platform`, `scope`, `scope_text`, `kind`, `scannable_only`, `status`, `active`, `limit`, `offset` |
| GET `programs/{id}` | Canonical details, scope, source records and policy links |
| GET `assets` | `program_id`, `source`, `scope`, `kind`, `q`, `target_text`, `active`, `limit`, `offset` |
| POST `refresh` | `{ "source": "all", "include_vdp": false }`; returns job ID |
| GET `jobs` | Latest 50 persisted jobs, status, parameters and results |
| GET/PUT `schedule` | `enabled`, `time`, `timezone`, `include_vdp` |
| POST `exports` | Create immutable bundle and return preview manifest |
| GET `exports/{id}` | The same preview manifest |
| GET `exports/{id}/download` | The same ZIP bytes |
| POST `exports/{id}/deliver` | Start/resume engine upload; `{ "retry": false }`, returns receipt/status |
| GET `exports/{id}/delivery` | Persisted delivery status and engine paths |
| GET `deliveries` | Latest 50 receipts for the configured engine |
| GET `workflows`, `workflows/{name}` | Workflow listing and editable declared parameters |
| POST/GET `scan-batches`; GET `scan-batches/{id}` | Prepare immutable review; list/read persisted batches |
| POST `scan-batches/{id}/{start,resume,cancel}` | Explicit confirmed batch actions |
| POST `scan-batches/{id}/items/{item}/{retry,skip}` | Explicit failed/uncertain item resolution |

Program/asset pagination: `items`, `total`, `limit`, `offset`, `has_more` (maximum
page size 200). Asset kinds include `domains`, `wildcards`, `ip`, `cidr`.
Export example: `{ "filters": { "program_id": 1, "scope": "in_scope" },
"asset_ids": [], "convert_wildcards": false }`. Empty asset IDs means all matching
rows; specified IDs must still match the filters or the request fails. The API
also supports exports across programs and a top-level `platform` filter.

## Upgrades, backup and rollback

1. Keep the running engine and companion unchanged. Choose an explicit full
   upstream SHA. For a new public version, update `companion_version` in
   `upstream.json` and the Python package version. Code updates receive distinct
   build-suffixed image tags. Save the current report/images before starting the gate. Then run
   `check-upgrade.ps1 -Revision FULL_SHA` (optionally `-Source`).
   Patch conflicts, route collisions, compilation, auth or test failures stop
   the gate. Review upstream changes before adapting the companion overlay.
2. Inspect `build/compatibility.json`. It binds the companion version, engine SHA,
   release and tested Docker image IDs. Never overwrite a previously promoted
   release tag or prune images needed by its saved report.
3. Save the current report, static artifact, image IDs/images and configuration.
   The build archives prior static output in `build/artifacts/`. Use `docker image
   save --output previous-images.tar IMAGE1 IMAGE2` or a private registry for
   retention. This project does not push anything. Do not prune rollback images.
4. Before promotion, stop the catalog and copy its entire data directory; for example:

   `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/backup-companion.ps1`

   This helper retains the exact running image IDs under rollback tags, writes a
   `rollback.compose.json` image override, stops/copies all companion data, then
   restarts the same previous catalog container. It does not touch the engine.
   Alternatively, perform the stopped copy manually:

   ```powershell
   docker compose stop catalog
   $backupDirectory = Join-Path (Get-Location) ('build/backup-' + (Get-Date -Format yyyyMMdd-HHmmss))
   New-Item -ItemType Directory -Path $backupDirectory
   $catalogContainer = docker compose ps --all --quiet catalog
   docker cp "${catalogContainer}:/data/." $backupDirectory
   ```

   This is a stopped-service copy, so both SQLite files and any WAL files are
   consistent. Each service startup also uses SQLite's backup API to capture the
   catalog into `/data/backups/` before initialization/migrations. Keep an
   off-volume backup too. Backups and immutable exports have no automatic expiry;
   monitor disk space and retain/remove them according to your own policy.
5. After checks and review, update the explicit pin/version in `upstream.json`,
   match your separately managed engine to that revision, and run
   `deploy-checked.ps1`. Candidate checking itself never changes the pin or engine.
6. Rollback: stop the companion, restore the prior engine using your engine's
   deployment procedure, select the saved release images and prior pin/report.
   If schema compatibility requires it, restore the stopped-service backup into
   a **new named Docker volume** and point a Compose override at that volume.
   Retain the failed volume for recovery; do not layer old DB files over live WAL
   files. Set ownership to UID/GID 10001 for the catalog container. Then deploy
   with the saved passing report (`deploy-checked.ps1 -Report PATH`).
   To restore the exact previously running companion without changing the engine
   or compatible schema, use `docker compose -f compose.yaml -f
   build/backup-pre-deploy-TIMESTAMP/rollback.compose.json up -d --no-build`.
   This rollback override restores captured images, not a claim of new compatibility
   checks. Resuming old scan batches still requires an authenticated operator.

## Development and verification

Python-only checks on Windows:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock -e '.[test]'
.\.venv\Scripts\python.exe -m pytest -q
```

The full PowerShell gate above is required for upstream upgrades. On Linux with
Python dependencies, Bun, Node/npm and Docker installed, run
`python scripts/check_upgrade.py`. The browser runner is pinned to Playwright's
Linux Docker image, so it does not depend on host browser packages. Browser tests
mock provider/engine data; separate real-container tests verify actual login and
gateway boundaries. The harmless fixture only logs two reserved `.example`
targets, under `--network none`; no external scan is performed.
The compatibility gate also seeds a disposable catalog, delivers through the
real upload API, reads back the engine-side file and consumes those exact bytes
with the offline fixture. It confirms exclusions are absent and delivery itself
created no runs. The 0.3.0 gate also checks listing and receipt piping, then
explicitly submits only the log-only fixture module through the run API on an
internal Docker network. Prior release images/reports are retained.
The 0.4.0 gate additionally checks browser review/launch/monitor/cancel, then submits
and cancels a sleep-only fixture through the actual batch API in disposable
containers on an internal network. It verifies organization assignment and waits
for all accepted target records instead of trusting early aggregate completion.

`scripts/container_smoke.py` can test explicitly supplied local image tags, and
`scripts/check_upgrade.py --smoke-image IMAGE` runs only the target-file fixture.
Neither shortcut produces a promotion report. For API-only development set
`CATALOG_BACKGROUND=false` to suppress automatic provider requests, and run
`uvicorn catalog_companion.api:app --host 127.0.0.1 --port 8090 --workers 1`.
