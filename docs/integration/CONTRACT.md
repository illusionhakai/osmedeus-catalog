# Trafalgar catalog / Osmedeus contract

## Ownership and compatibility

This companion owns gathering, catalog storage, exports, upload delivery and the
dashboard overlay. Neither sibling repository is a runtime dependency. No engine
source edits or direct Osmedeus database writes. Refresh/export/delivery do not
scan; the run CLI and browser batch adapter submit only on explicit confirmation.
The catalog is shared, independent of the dashboard organization selector.

`upstream.json` is the version authority: Osmedeus v5.1.1 at
`9a02ed0f9dbd3d9fd7f006610264016058fd8a0e`; companion currently 0.4.0.
Trafalgar extraction attribution is in `NOTICE.md`. Compatibility means this
tested pin, not arbitrary future upstream versions.

## External boundary

`api.py:EngineAuth` and `delivery.py:DeliveryManager` are the engine adapters.
They use HTTP contracts, not upstream Python/Go objects or internal storage.

| Operation | Contract |
| --- | --- |
| Authenticate each catalog request | Browser `osmedeus_token` as `Authorization: Bearer ...`; fixed GET `/osm/api/runs?limit=1`. An invalid-token probe must return 401/403; actual token must return 200 with valid pagination and data list (null only with total zero). Fail closed: missing/invalid token 401; unavailable, no-auth or unexpected engine response 503. No signing secret copied. |
| Deliver a file | POST `/osm/api/upload-file`, same authorization, multipart field `file`. Require HTTP 200 with `filename`, absolute Linux `path`, matching byte `size` and newline `lines`. Filename must be the engine's numeric prefix plus submitted safe filename. Reject redirects and malformed acknowledgements. |
| Consume later, manually | `osmedeus run -f YOUR_REVIEWED_WORKFLOW -T /engine/path/from/receipt`. Receipt also exposes `{flow, target_file}` as a request template, never submits it. Paths belong to the engine filesystem, not the browser/Windows host. |
| Explicit workflow handoff | `run.py` lists GET `/osm/api/workflows` (search/limit/offset), checks GET `/osm/api/workflows/{name}?json=true` (name/kind), and only with `--submit --acknowledge-scope` POSTs `/osm/api/runs` with `{flow|module,target_file}`. Require 202 with nonempty job_id and status queued; acceptance is not completion. No POST retries; uncertain outcome requires checking engine Scans/jobs. |

`ship.py` can obtain a token with POST `/osm/api/login` using username/password;
otherwise use process environment `OSMEDEUS_TOKEN`. Tokens/passwords are not stored
in receipts or catalog state. HTTP clients disable redirects and environment proxies.
Official target-file usage: [CLI reference](https://docs.osmedeus.org/getting-started/cli).

## Configuration and companion API

Gateway defaults to loopback `http://localhost:8080` (`CATALOG_PORT`).
`OSMEDEUS_URL` defaults to `http://host.docker.internal:8002`, an HTTP(S) origin
without credentials/path/query. `CATALOG_DATA_DIR` defaults to local `data/`,
Docker `/data` in `catalog-data`. `CATALOG_RELEASE` selects versioned images.
`CATALOG_BACKGROUND=false` disables bootstrap/scheduling for tests, not manual jobs.
See `.env.example`, `compose.yaml` and README for deployment commands.

All routes below have prefix `/catalog-api/v1` and require authentication:

| Routes | Inputs / outputs |
| --- | --- |
| GET `/overview`, `/programs`, `/programs/{id}`, `/assets` | Program/asset queries defined by `ProgramQuery`/`AssetQuery`; lists retain `{items,total,limit,offset,has_more}`, limit <=200. Overview includes source last-success, age and latest error. |
| POST `/refresh`; GET `/jobs` | `{source:"all",include_vdp:false}` -> 202 `{job_id}`; jobs wrapper `{items}` reports parameters, status, timestamps, result. Sources: all, rix-scope, chaos, arkadiyt. |
| GET/PUT `/schedule` | `{enabled:true,time:"03:00",timezone:"America/Sao_Paulo",include_vdp:false}`; time/timezone validated. |
| POST `/exports`; GET `/exports/{id}` and `/exports/{id}/download` | Request `{filters,platform,asset_ids,convert_wildcards:false}` -> 201 manifest; GET returns same immutable preview or ZIP. Filters: q,target_text,program_id,source,scope,kind,active. Empty selection means all matching assets across pages. |
| POST `/exports/{id}/deliver`; GET `/exports/{id}/delivery`, `/deliveries` | `{retry:false}` -> 202 receipt; GET receipt or latest 50 `{items}` for current engine. No scan endpoint is called. |
| GET `/workflows`, `/workflows/{name}` | Fixed engine workflow listing (search/limit/offset) and declared parameters, with target/org/credential parameters removed. |
| POST `/scan-batches`; GET `/scan-batches`, `/scan-batches/{id}` | Create immutable prepared review; list latest 50 or read persisted batch. Request selects program IDs or all filtered programs, optional asset IDs, either scan_mode or legacy category mappings/null exclusions, org UUID, parallelism 1–3 and target concurrency 1–10 (both default 1). No client target paths; no upload/scan on preparation. |
| POST `/scan-batches/{id}/start`, `/resume`, `/cancel` | `{confirmed:true}` required; resume optionally `retry_delivery:true`. Duplicate start returns existing state; changed snapshot before start returns 409. |
| POST `/scan-batches/{id}/items/{item}/retry`, `/skip` | Explicit resolution of failed/uncertain entries while paused; uncertain requires `acknowledge_uncertain:true`. Resume is a separate action. |

IDs for exports are 32 lowercase hex characters. Unknown resources: 404; invalid
requests: 422; conflicting delivery/retry requirement: 409. Worker failures appear
in job/receipt state after acceptance; 202 is not completion. Internal `/health`
is unauthenticated. nginx routes `/catalog-api/` to the companion and `/osm/` plus
required engine endpoints to Osmedeus; serves the overlaid static dashboard.

The Bug Bounty program list requests `scannable_only=true`. This opt-in program
query flag applies before pagination: require an active/open/public program with
at least one normalized in-scope domain (including wildcard), IP or CIDR that is
not overridden by that program's exclusions. Overlapping excluded networks/IPs
disqualify a CIDR. Hidden programs remain stored and available to ordinary API
queries; this is a presentation filter, not deletion or a permission boundary.
Manual exports require opt-in wildcard conversion; automatic scan previews include
discovery roots by default and retain the apex-scope limitation.
Program `scope`, `scope_text` and `kind` predicates match the same asset; counts
(`matching_asset_count`, `domain_count`, `wildcard_count`) describe matching
records, not final scan eligibility. The dashboard clears selections on filter
changes. All-filtered selection resolves every page inside the export snapshot.

## Scan batches (0.4.0)

Browser flow is selection -> one Automatic/Full recon menu -> Send to scan review
-> Launch scans. Active dashboard organization is used, otherwise Default. Extra
confirmation checkboxes are replaced by the explicit launch action. Concurrency
(defaults 1/1), wildcard opt-out, history and export/delivery tools are collapsed.
Active batch progress is selected automatically, including after page reload.
Batch listings omit manifest/request/history; fetch the batch detail before
rendering restored progress. Preparation displays a loading state; gateway
timeouts remain visible and retryable without implicitly launching scans.

POST /scan-batches accepts scan_mode="automatic"|"full" instead of workflows;
exactly one nonnull input is required. Legacy manual requests/batches remain valid.
Automatic routes domains/subdomains to enum-subdomain (module), IPs/CIDRs to cidr
(flow); full routes domains to domain-standard (flow). Discovery roots always use
enum-subdomain. Resolve only nonempty eligible export categories, verify installed
workflow kinds/required parameters, merge defaults and freeze resolved mappings in
the persisted request. Missing/invalid workflows block preparation. Hostnames and
exclusions are preserved; there is no apex derivation or guessing workflow names.

Automatic preview defaults convert_wildcards=true (explicit false opts out), without
requiring acknowledge_wildcards; preparation performs no upload/scan. confirmed=true
on launch acknowledges the displayed organization, authorization, exclusions and
discovery-only wildcard conversion together. Legacy manual converted previews still
require acknowledge_wildcards=true. A wildcard root never uses a full-recon flow.

`engine.py` and `batches.py` use only fixed authenticated public endpoints:
GET `/osm/api/orgs/{uuid}`, `/workflows`, `/workflows/{name}?json=true`,
POST `/runs`, GET `/jobs/{job_id}`, DELETE `/runs/{run_uuid}`. Run submission is
`{flow|module,target_file,params:{...reviewed_params,org_uuid},concurrency}`.
Require 202 `{status:"queued",job_id,target_count}`. Monitor actual
`{run_group_id,runs,progress}`, never assume an early aggregate completion means
all accepted targets have materialized. Accepted-count mismatch pauses dispatch
without discarding the accepted job. Cancellation acknowledgements identify the run.

Control SQLite settings hold engine-scoped batch records, frozen export/mappings/
organization, item submission intents, accepted IDs, observations and history.
Bearer tokens exist only in memory. Restart turns submitting items uncertain,
pauses active batches and requires authenticated resume. Expired auth does the same.
Persist intent before POST; timeout/lost response is uncertain and never resent.
Delivery/submission/scan/tracking failures pause new dispatch while accepted jobs
continue and remain monitored when credentials are available. Parallelism is per
batch; multiple deliberately started batches execute independently.

Cancel stops unsent work and requests cancellation of known active runs, polling
for late records until accepted target counts are accounted for. There is no atomic
engine whole-job cancellation or process-termination guarantee. Unknown submissions
remain visibly incomplete; inspect engine Scans. Engine-reported cancellation is
not proof that all side effects stopped. No scheduled scans or provider-triggered
submissions. Browser confirmation includes organization, authorization and workflow
scope suitability and the discovery-only wildcard limitation in the launch review.

At the pinned engine revision, an accepted cancellation can finish with run
status `failed`. The isolated gate verifies cancellation acknowledgements,
reported process termination and terminal records for every accepted target;
it does not require a durable `cancelled` status for individual engine runs.

## Data and export semantics

`bugbounty_catalog/sources/` converts Rix Scope, Chaos and Arkadiyt responses to
owned models; normalization/merging preserve canonical program identity,
provenance, active state and in/out/candidate scope. Successful provider snapshots
commit independently. Failed, empty or malformed responses retain that provider's
last successful snapshot; a valid new snapshot retires removed scope.

`exports.py` reads one SQLite snapshot, groups by canonical
program and atomically publishes an immutable ZIP. Manifest schema_version=1
records export_id, filters, program/source provenance and timestamps, counts,
conversions, omissions and SHA-256 payload checksums (not its own checksum).
Selected program/asset IDs are constrained in SQL. Export streams omit browser
statistics and pagination recounts; shared asset filters retain scope semantics.
Each program has UTF-8 `domains.txt`, `ips.txt`, `cidrs.txt`, `discovery-roots.txt`,
`scope.json`, `exclusions.txt`; archive includes instructions and manifest.
Inactive/candidate assets never become targets. Exclusions override inclusions
only within their program; an excluded address/network overlap omits the entire CIDR.
Wildcards remain scope metadata. Opt-in `*.example.com` -> `example.com` goes
only into discovery-roots; the apex is not thereby authorized. Arbitrary workflows
do not automatically enforce scope metadata; review workflow and authorization.

## Lifecycle, progress and persistence

One service process with a data-directory lock. `state.py` owns `control.sqlite`
(jobs/settings/receipts); `bugbounty_catalog/db.py` owns `catalog.sqlite`.
`data/exports/` stores ZIPs and `data/backups/` catalog backups before schema init.
These are runtime data, never AI context or fixtures.

Refreshes serialize; concurrent requests return the active job. Empty catalogs
bootstrap; scheduled refresh checks every 30 seconds, at most once per local day.
Provider failures can produce partial status; restart marks running jobs failed.
Delivery separately serializes uploads; refresh cannot alter an existing ZIP.
All checksums are validated before uploading; empty target files are skipped.

Receipt states: uploading -> ready/failed; restart -> interrupted. Receipts include
export_id, engine, snapshot_at, attempts, total_files, pending_member, error,
timestamps and confirmed files with member/program/kind/path/size/lines/sha256.
Target entries include CLI/API templates. Polling drives UI progress. Same export
already uploading/ready returns its receipt; other active exports conflict.
Failed/interrupted work requires explicit retry and skips confirmed files.
The upstream upload API provides no remote idempotency/readback/delete contract:
a lost acknowledgement may leave an unconfirmed duplicate on retry.

No upload cancellation API. Graceful shutdown waits for workers; forced interruption is
reconciled on restart. Client timeout/disconnect does not cancel server work.
`python -m catalog_companion.ship` selects exactly one of `--program-id`,
`--all-programs`, `--export-id`; accepts `--gateway`, `--username`, `--source`,
`--platform`, `--refresh`, `--convert-wildcards`, `--retry`, `--timeout` (1800s).
Existing exports cannot change filters; retry requires export ID. Optional refresh
must fully succeed before export. Receipt JSON goes to stdout, failures to stderr
with nonzero exit; preserve export ID to poll/retry after timeout.

`python -m catalog_companion.programs` lists active programs with --search,
--source, --platform, --limit/--offset, --all-pages and --json. Listing is read-only;
multi-page browsing is not an immutable snapshot (exports are).
`python -m catalog_companion.run` selects --export-id, --receipt-stdin or
--list-workflows. Stdin supplies only an ID; re-fetch the trusted ready receipt.
Require --flow/--module and --target-kind; --program-id resolves multi-program
ambiguity. Default output is a read-only plan with identity, count, CLI and request;
--request-only emits just the request. Discovery roots need --allow-discovery-roots.
Run/list commands share gateway login in cli.py; no saved credentials. Engine paths
are not writable through these commands. Submission is deliberately non-idempotent;
no scheduler invokes it. The separate batch routes use the adapter described above.

## Upgrade boundary and file lookup

`scripts/deploy.ps1` runs the changed-code gate, retains a pre-update backup,
starts managed Compose dependencies/workers and the named osmedeus-server, then
deploys the tested companion images. Engine version is verified; its revision is
verified when the binary reports a nonempty commit in its build metadata.
Backup retains existing images by ID. If an image was pruned, it exports the
container filesystem and imports a rollback image preserving and verifying its
startup configuration; mounted catalog data is copied separately while stopped.
Recovery method and original/retained IDs are recorded in running-images.json.
It never edits/rebuilds engine source or submits scans. `-Watch` repeats on owned
source/config changes. Build identities hash owned code/config/test inputs;
runtime data, dependencies and documentation are excluded. Gate reports and
static `/catalog-release.json` include `build_id`; image release tags include
its prefix so code edits do not overwrite previously promoted release tags.
HTML loads a stamped refresh script that polls the uncached public release
metadata and reloads once when a new build is available. Failed/outage responses
leave the current page intact. Deployment state is saved only after health and
build identity checks; concurrent deployment and edits during checks fail closed.

Relevant owned/upstream interface paths are indexed once in `.ai/SCOPE.md`.
The overlay adds owned route files and one sidebar patch only to a clean pinned
disposable checkout. Collision, patch, compile or compatibility failures block
promotion. Version images/dashboard/pin together, retain previous artifacts and
back up catalog state before migration. `scripts/check-upgrade.ps1` exercises
auth, real upload acknowledgements, browser outage behavior, harmless offline
`-T` consumption and isolated batch submission/cancellation with a sleep-only
workflow. `scripts/deploy-checked.ps1` is a separate promotion
step; never auto-advance upstream or deploy merely because checks passed.
