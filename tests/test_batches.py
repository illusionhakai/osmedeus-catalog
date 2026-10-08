import json
import threading

import httpx
import pytest
from fastapi.testclient import TestClient

from bugbounty_catalog.db import CatalogDB
from bugbounty_catalog.models import Program
from bugbounty_catalog.normalize import assets_from_values
from bugbounty_catalog.query import ProgramQuery
from catalog_companion.api import create_app
from catalog_companion.batch_models import BatchAction, BatchPreview
from catalog_companion.batches import BatchConflict, BatchManager
from catalog_companion.delivery import bundle_files
from catalog_companion.engine import EngineError
from catalog_companion.state import State
from test_companion import seed, engine as auth_engine

ORG = "00000000-0000-0000-0000-000000000001"


def payload(**updates):
    body = {"selection": {"mode": "ids", "program_ids": [1, 3], "filters": {"scope": "in_scope", "kind": "domains"}},
            "org_uuid": ORG, "workflows": {"domains": {"name": "fixture", "kind": "module", "params": {}},
                "ips": None, "cidrs": None, "discovery-roots": None}, **updates}
    return BatchPreview.model_validate(body)


class Delivery:
    engine = "http://engine"
    def __init__(self):
        self.receipts = {}
        self.calls = 0
    def get(self, export_id):
        return self.receipts.get(export_id)
    def submit(self, path, authorization, retry=False):
        self.calls += 1
        files = [{**info, "path": "/engine/" + info["member"].replace("/", "-"),
                  "lines": content.count(b"\n")} for info, content in bundle_files(path) if info["is_target_file"]]
        self.receipts[path.stem] = {"status": "ready", "files": files}
        return self.receipts[path.stem]


class Engine:
    def __init__(self):
        self.posts, self.cancels, self.jobs = [], [], {}
        self.error = None
        self.auth_expired = False
        self.workflow_requests = []
        self.definitions = {name: {"name": name, "kind": kind, "params": [{"name": "threads", "default": "1"}]}
                            for name, kind in (("fixture", "module"), ("enum-subdomain", "module"),
                                               ("cidr", "flow"), ("domain-standard", "flow"))}
    def __call__(self, request):
        path = request.url.path
        if path == "/osm/api/runs" and request.method == "GET":
            return auth_engine(request)
        if self.auth_expired:
            return httpx.Response(401)
        if path == "/osm/api/orgs/" + ORG:
            return httpx.Response(200, json={"data": {"uuid": ORG, "name": "Fixture organization"}})
        if path.startswith("/osm/api/workflows/"):
            name = path.rsplit("/", 1)[-1]
            self.workflow_requests.append(name)
            return httpx.Response(200, json=self.definitions[name]) if name in self.definitions else httpx.Response(404)
        if path == "/osm/api/workflows":
            return httpx.Response(200, json={"data": [{"name": "fixture", "kind": "module"}], "pagination": {"total": 1}})
        if path == "/osm/api/runs":
            body = json.loads(request.content)
            self.posts.append(body)
            if self.error == "timeout":
                raise httpx.ReadTimeout("secret response")
            if self.error == "rejected":
                return httpx.Response(422)
            job = "job-" + str(len(self.posts))
            self.jobs[job] = []
            return httpx.Response(202, json={"job_id": job, "target_count": 1, "status": "queued"})
        if path.startswith("/osm/api/jobs/"):
            job = path.rsplit("/", 1)[1]
            return httpx.Response(200, json={"run_group_id": job, "progress": {"completed": 1},
                                             "status": "completed", "runs": self.jobs[job]})
        if request.method == "DELETE":
            self.cancels.append(path.rsplit("/", 1)[1])
            return httpx.Response(200, json={"run_uuid": self.cancels[-1], "note": "No active processes found; database status updated"})
        raise AssertionError(str(request.url))


@pytest.fixture
def setup(tmp_path):
    seed(tmp_path / "catalog.sqlite")
    delivery, engine = Delivery(), Engine()
    manager = BatchManager(State(tmp_path / "control.sqlite"), tmp_path, delivery, httpx.MockTransport(engine))
    yield manager, delivery, engine
    manager.close()


def start(manager, body=None):
    batch = manager.prepare(body or payload(), "Bearer valid")
    manager.action(batch["id"], "start", BatchAction(confirmed=True), "Bearer valid")
    manager.tick()  # Upload.
    manager.tick()  # First dispatch.
    return manager.get(batch["id"])


def test_program_filters_apply_to_same_asset_and_count(tmp_path):
    seed(tmp_path / "catalog.sqlite")
    with CatalogDB(tmp_path / "catalog.sqlite") as db:
        db.upsert_programs([Program(source="test", source_id="c", name="Misleading", assets=
            assets_from_values(["exact.fixture.example"], "in_scope") + assets_from_values(["*.fixture.example"], "out_of_scope"))])
        rows = db.list_programs(ProgramQuery(scope="in_scope", kind="wildcards", scope_text="example")).items
        assert [row["name"] for row in rows] == ["Alpha"]
        assert rows[0]["matching_asset_count"] == 1 and rows[0]["wildcard_count"] == 1 and rows[0]["domain_count"] == 0
        assert db.list_programs(ProgramQuery(scope="out_of_scope", kind="wildcards")).items[0]["name"] == "Misleading"


def test_preview_has_no_delivery_or_runs_and_freezes_organization(setup):
    manager, delivery, engine = setup
    batch = manager.prepare(payload(), "Bearer valid")
    assert batch["status"] == "prepared" and len(batch["items"]) == 2
    assert delivery.calls == 0 and not engine.posts
    assert batch["organization"]["uuid"] == ORG
    assert batch["items"][0]["workflow"]["params"] == {"threads": "1"}
    assert "Bearer valid" not in json.dumps(manager.list())


def test_filtered_selection_exports_every_program_page(tmp_path):
    seed(tmp_path / "catalog.sqlite")
    with CatalogDB(tmp_path / "catalog.sqlite") as db:
        db.upsert_programs([Program(source="fixture", source_id=str(i), name=f"Program {i}",
            assets=assets_from_values([f"host{i}.example"], "in_scope")) for i in range(205)])
    manager = BatchManager(State(tmp_path / "control.sqlite"), tmp_path, Delivery(), httpx.MockTransport(Engine()))
    batch = manager.prepare(payload(selection={"mode": "filtered", "filters": {"source": "fixture", "scope": "in_scope", "kind": "domains"}}), "Bearer valid")
    assert len(batch["items"]) == 205
    manager.close()


def test_duplicate_starts_do_not_repeat_upload_or_submission(setup):
    manager, delivery, engine = setup
    batch = start(manager)
    for _ in range(3):
        manager.action(batch["id"], "start", BatchAction(confirmed=True), "Bearer valid")
        manager.tick()
    assert len(engine.posts) == 1 and delivery.calls == 1
    assert engine.posts[0]["params"]["org_uuid"] == ORG
    assert engine.posts[0]["concurrency"] == 1


def test_delayed_records_do_not_prematurely_advance(setup):
    manager, _, engine = setup
    batch = start(manager)
    batch["items"][0]["accepted_count"] = 2
    manager.save(batch)
    engine.jobs["job-1"] = [{"run_uuid": "run-1", "status": "completed"}]
    manager.tick()
    assert len(engine.posts) == 1
    engine.jobs["job-1"].append({"run_uuid": "run-2", "status": "completed"})
    manager.tick()
    assert len(engine.posts) == 2


def test_parallelism_bound_and_failure_pause(setup):
    manager, _, engine = setup
    batch = start(manager, payload(parallelism=2))
    assert len(engine.posts) == 2
    engine.jobs["job-1"] = [{"run_uuid": "run-1", "status": "failed"}]
    manager.tick()
    assert manager.get(batch["id"])["status"] == "paused"
    assert manager.get(batch["id"])["items"][1]["status"] == "running"


def test_unknown_submission_requires_explicit_resolution(setup):
    manager, _, engine = setup
    engine.error = "timeout"
    batch = start(manager)
    assert batch["status"] == "paused" and batch["items"][0]["status"] == "uncertain"
    for _ in range(3):
        manager.tick()
    assert len(engine.posts) == 1 and "secret" not in json.dumps(batch)
    with pytest.raises(BatchConflict, match="acknowledge"):
        manager.item_action(batch["id"], batch["items"][0]["id"], "retry", BatchAction(confirmed=True))
    manager.item_action(batch["id"], batch["items"][0]["id"], "skip", BatchAction(confirmed=True, acknowledge_uncertain=True))
    assert manager.get(batch["id"])["items"][0]["status"] == "skipped"


def test_auth_expiry_drops_credentials_and_requires_resume(setup):
    manager, _, engine = setup
    batch = start(manager)
    engine.auth_expired = True
    manager.tick()
    assert batch["id"] not in manager.tokens and manager.get(batch["id"])["status"] == "paused"
    engine.auth_expired = False
    engine.jobs["job-1"] = [{"run_uuid": "run-1", "status": "completed"}]
    manager.tick()
    assert len(engine.posts) == 1
    manager.action(batch["id"], "resume", BatchAction(confirmed=True), "Bearer valid")
    manager.tick()
    assert len(engine.posts) == 2


def test_restart_preserves_submitting_as_uncertain(setup):
    manager, delivery, engine = setup
    batch = start(manager)
    batch["items"][0]["status"] = "submitting"
    manager.save(batch)
    restarted = BatchManager(manager.state, manager.data, delivery, httpx.MockTransport(engine))
    assert restarted.get(batch["id"])["items"][0]["status"] == "uncertain"
    assert not restarted.tokens
    restarted.tick()
    assert len(engine.posts) == 1
    restarted.close()


def test_cancel_stops_queue_and_waits_for_late_runs(setup):
    manager, _, engine = setup
    batch = start(manager)
    batch["items"][0]["accepted_count"] = 2
    manager.save(batch)
    manager.action(batch["id"], "cancel", BatchAction(confirmed=True), "Bearer valid")
    engine.jobs["job-1"] = [{"run_uuid": "run-1", "status": "running"}]
    manager.tick()
    assert engine.cancels == ["run-1"] and len(engine.posts) == 1
    engine.jobs["job-1"] = [{"run_uuid": "run-1", "status": "cancelled"}, {"run_uuid": "run-2", "status": "running"}]
    manager.tick()
    assert engine.cancels == ["run-1", "run-2"]
    assert manager.get(batch["id"])["status"] == "cancel_requested"
    engine.jobs["job-1"][1]["status"] = "cancelled"
    manager.tick()
    assert manager.get(batch["id"])["status"] == "cancelled"
    assert manager.get(batch["id"])["items"][1]["status"] == "skipped"


def test_new_snapshot_invalidates_unconfirmed_preview(setup):
    manager, delivery, engine = setup
    batch = manager.prepare(payload(), "Bearer valid")
    with CatalogDB(manager.data / "catalog.sqlite") as db:
        db.upsert_programs([Program(source="fixture", source_id="new", name="New", assets=assets_from_values(["new.example"], "in_scope"))])
    with pytest.raises(BatchConflict, match="Catalog changed"):
        manager.action(batch["id"], "start", BatchAction(confirmed=True), "Bearer valid")
    assert delivery.calls == 0 and not engine.posts


@pytest.mark.parametrize("params", [{"Target": "outside.example"}, {"org_uuid": ORG}, {"target_file": "/etc/passwd"}, {"token": "secret"}])
def test_protected_parameters_rejected(params):
    with pytest.raises(ValueError):
        payload(workflows={"domains": {"name": "fixture", "kind": "module", "params": params}})


def test_unsupported_or_empty_scopes_cannot_scan(setup):
    manager, _, engine = setup
    with pytest.raises(ValueError, match="No scan-eligible"):
        manager.prepare(payload(selection={"mode": "ids", "program_ids": [1], "filters": {"scope": "candidate"}}), "Bearer valid")
    assert not engine.posts


def test_batch_routes_auth_validation_and_no_implicit_start(tmp_path):
    seed(tmp_path / "catalog.sqlite")
    engine = Engine()
    with TestClient(create_app(tmp_path, "http://engine", transport=httpx.MockTransport(engine), background=False)) as client:
        assert client.get("/catalog-api/v1/workflows").status_code == 401
        assert client.post("/catalog-api/v1/scan-batches", json=payload().model_dump(mode="json")).status_code == 401
        client.headers["Authorization"] = "Bearer valid"
        assert client.get("/catalog-api/v1/workflows").status_code == 200
        result = client.post("/catalog-api/v1/scan-batches", json=payload().model_dump(mode="json"))
        assert result.status_code == 201
        batch_id = result.json()["id"]
        assert client.post(f"/catalog-api/v1/scan-batches/{batch_id}/start", json={}).status_code == 422
        assert client.get(f"/catalog-api/v1/scan-batches/{batch_id}").json()["status"] == "prepared"
        assert client.get("/catalog-api/v1/scan-batches").json()["items"][0]["id"] == batch_id
        assert not engine.posts


def test_delivery_failure_requires_explicit_retry(setup):
    manager, delivery, engine = setup
    batch = manager.prepare(payload(), "Bearer valid")
    delivery.receipts[batch["export_id"]] = {"status": "failed", "files": []}
    manager.action(batch["id"], "start", BatchAction(confirmed=True), "Bearer valid")
    manager.tick()
    assert manager.get(batch["id"])["status"] == "paused" and not engine.posts
    manager.tick()
    assert delivery.calls == 0
    manager.action(batch["id"], "resume", BatchAction(confirmed=True, retry_delivery=True), "Bearer valid")
    manager.tick()
    assert len(engine.posts) == 1 and delivery.calls == 1


def test_mappings_must_cover_every_category_and_wildcards_need_ack(setup):
    manager, _, engine = setup
    with pytest.raises(ValueError, match="Map or explicitly exclude"):
        manager.prepare(payload(workflows={}, selection={"mode": "ids", "program_ids": [1]}), "Bearer valid")
    with pytest.raises(ValueError, match="Acknowledge wildcard"):
        payload(convert_wildcards=True)
    batch = manager.prepare(payload(convert_wildcards=True, acknowledge_wildcards=True,
        selection={"mode": "ids", "program_ids": [1, 3]}, workflows={k: {"name": "fixture", "kind": "module", "params": {}} for k in ("domains", "ips", "cidrs", "discovery-roots")}), "Bearer valid")
    a, b = batch["manifest"]["programs"]
    assert a["counts"]["domains"] == b["counts"]["domains"] == 1
    assert len(a["exclusions"]) == 2 and b["exclusions"] == []
    assert a["conversions"][0]["apex_authorized"] is False
    assert not engine.posts


def test_confirmed_inputs_remain_immutable_after_refresh(setup):
    manager, delivery, _ = setup
    batch = start(manager)
    before = (manager.data / "exports" / (batch["export_id"] + ".zip")).read_bytes()
    with CatalogDB(manager.data / "catalog.sqlite") as db:
        db.upsert_programs([Program(source="new", source_id="new", name="Another", assets=assets_from_values(["new.example"], "in_scope"))])
    manager.tick()
    assert manager.view(manager.get(batch["id"]))["catalog_changed"]
    assert before == (manager.data / "exports" / (batch["export_id"] + ".zip")).read_bytes()
    assert delivery.calls == 1


def test_rejected_submission_does_not_dispatch_next_item(setup):
    manager, _, engine = setup
    engine.error = "rejected"
    batch = start(manager, payload(parallelism=3))
    assert batch["status"] == "paused" and batch["items"][0]["status"] == "failed"
    assert len(engine.posts) == 1
    engine.error = None
    manager.item_action(batch["id"], batch["items"][0]["id"], "skip", BatchAction(confirmed=True))
    manager.action(batch["id"], "resume", BatchAction(confirmed=True), "Bearer valid")
    manager.tick()
    assert len(engine.posts) == 2


def test_cancel_uncertain_submission_stays_incomplete(setup):
    manager, _, engine = setup
    engine.error = "timeout"
    batch = start(manager)
    manager.action(batch["id"], "cancel", BatchAction(confirmed=True), "Bearer valid")
    manager.tick()
    assert manager.get(batch["id"])["status"] == "cancel_requested"
    assert manager.get(batch["id"])["items"][0]["status"] == "uncertain"
    assert len(engine.posts) == 1


def test_accepted_count_mismatch_pauses_but_preserves_tracking(setup):
    manager, _, engine = setup
    original = manager.engine.submit
    def mismatch(*args):
        result = original(*args)
        return {**result, "target_count": result["target_count"] + 1}
    manager.engine.submit = mismatch
    batch = start(manager, payload(parallelism=3))
    assert batch["status"] == "paused"
    assert batch["items"][0]["status"] == "running" and batch["items"][0]["job_id"]
    assert len(engine.posts) == 1


def test_terminal_cancellation_releases_credentials(setup):
    manager, _, _ = setup
    batch = manager.prepare(payload(), "Bearer valid")
    manager.action(batch["id"], "cancel", BatchAction(confirmed=True), "Bearer valid")
    manager.tick()
    assert manager.get(batch["id"])["status"] == "cancelled"
    assert batch["id"] not in manager.tokens and batch["id"] not in manager.cancel_flags


@pytest.mark.parametrize("bad", [{"target_file": "/tmp/client-path"}, {"authorization": "Bearer secret"}])
def test_client_paths_and_credentials_cannot_enter_requests(bad):
    with pytest.raises(ValueError):
        payload(**bad)


@pytest.mark.parametrize("mode,domain", [("automatic", "enum-subdomain"), ("full", "domain-standard")])
def test_automatic_routing_preserves_mixed_inputs_and_wildcard_discovery(setup, mode, domain):
    manager, delivery, engine = setup
    body = payload(scan_mode=mode, workflows=None,
                   selection={"mode": "ids", "program_ids": [1, 3], "filters": {"scope": "in_scope"}})
    assert body.convert_wildcards and not body.acknowledge_wildcards
    batch = manager.prepare(body, "Bearer valid")
    assert batch["request"]["scan_mode"] == mode
    mappings = batch["request"]["workflows"]
    assert mappings["domains"]["name"] == domain
    assert mappings["ips"]["name"] == mappings["cidrs"]["name"] == "cidr"
    assert mappings["discovery-roots"]["name"] == "enum-subdomain"
    assert mappings["discovery-roots"]["kind"] == "module"
    assert all(item["workflow"]["params"] == {"threads": "1"} for item in batch["items"])
    import zipfile
    with zipfile.ZipFile(manager.data / "exports" / (batch["export_id"] + ".zip")) as archive:
        assert archive.read("program-1/domains.txt").decode().splitlines() == ["example.com"]
        assert archive.read("program-3/domains.txt").decode().splitlines() == ["denied.example.com"]
        assert archive.read("program-1/ips.txt").decode().splitlines() == ["192.0.2.1"]
        assert archive.read("program-1/cidrs.txt").decode().splitlines() == ["2001:db8::/32"]
        assert archive.read("program-1/discovery-roots.txt").decode().splitlines() == ["example.com"]
    assert batch["manifest"]["programs"][0]["conversions"][0]["apex_authorized"] is False
    assert delivery.calls == 0 and not engine.posts and not manager.tokens


def test_discovery_opt_out_and_unused_missing_workflow(setup):
    manager, _, engine = setup
    del engine.definitions["cidr"]
    batch = manager.prepare(payload(scan_mode="automatic", workflows=None, convert_wildcards=False), "Bearer valid")
    assert set(batch["request"]["workflows"]) == {"domains"}
    assert all(item["kind"] == "domains" for item in batch["items"])
    assert "cidr" not in engine.workflow_requests
    assert payload().convert_wildcards is False  # Legacy manual default.


@pytest.mark.parametrize("failure", ["missing", "required", "wrong_kind"])
def test_automatic_routing_blocks_unavailable_or_invalid_workflows(setup, failure):
    manager, delivery, engine = setup
    if failure == "missing":
        del engine.definitions["enum-subdomain"]
    elif failure == "required":
        engine.definitions["enum-subdomain"]["params"] = [{"name": "required_value", "required": True}]
    else:
        engine.definitions["enum-subdomain"]["kind"] = "flow"
    with pytest.raises((ValueError, EngineError), match="enum-subdomain"):
        manager.prepare(payload(scan_mode="automatic", workflows=None), "Bearer valid")
    assert not manager.list() and not engine.posts and delivery.calls == 0


@pytest.mark.parametrize("updates", [{"scan_mode": "automatic"}, {"workflows": None},
                                    {"scan_mode": "unknown", "workflows": None}])
def test_scan_mode_and_manual_mapping_are_exclusive(updates):
    with pytest.raises(ValueError):
        payload(**updates)


def test_automatic_preview_needs_explicit_launch_and_freezes_routing(setup):
    manager, delivery, engine = setup
    batch = manager.prepare(payload(scan_mode="automatic", workflows=None), "Bearer valid")
    frozen = json.dumps(batch["request"]["workflows"], sort_keys=True)
    with pytest.raises(ValueError, match="confirmation"):
        manager.action(batch["id"], "start", BatchAction(), "Bearer valid")
    manager.tick()
    assert not engine.posts and delivery.calls == 0
    for _ in range(2):
        manager.action(batch["id"], "start", BatchAction(confirmed=True), "Bearer valid")
        manager.tick()
    assert delivery.calls == 1 and len(engine.posts) == 1
    assert engine.posts[0]["module"] == "enum-subdomain"
    assert json.dumps(manager.get(batch["id"])["request"]["workflows"], sort_keys=True) == frozen


def test_automatic_snapshot_change_blocks_launch(setup):
    manager, delivery, engine = setup
    batch = manager.prepare(payload(scan_mode="automatic", workflows=None), "Bearer valid")
    with CatalogDB(manager.data / "catalog.sqlite") as db:
        db.upsert_programs([Program(source="new", source_id="new", name="New", assets=assets_from_values(["new.example"], "in_scope"))])
    with pytest.raises(BatchConflict, match="Catalog changed"):
        manager.action(batch["id"], "start", BatchAction(confirmed=True), "Bearer valid")
    assert not engine.posts and delivery.calls == 0


def test_automatic_api_default_discovery_and_manual_compatibility(tmp_path):
    seed(tmp_path / "catalog.sqlite")
    engine = Engine()
    with TestClient(create_app(tmp_path, "http://engine", transport=httpx.MockTransport(engine), background=False),
                    headers={"Authorization": "Bearer valid"}) as client:
        request = {"selection": {"mode": "ids", "program_ids": [1]}, "org_uuid": ORG, "scan_mode": "automatic"}
        result = client.post("/catalog-api/v1/scan-batches", json=request)
        assert result.status_code == 201
        batch = result.json()
        assert batch["request"]["convert_wildcards"] is True
        assert batch["request"]["workflows"]["discovery-roots"]["name"] == "enum-subdomain"
        assert client.post(f"/catalog-api/v1/scan-batches/{batch['id']}/start", json={}).status_code == 422
        assert not engine.posts
        assert client.post("/catalog-api/v1/scan-batches", json={**request, "workflows": {}}).status_code == 422
        assert client.post("/catalog-api/v1/scan-batches", json=payload().model_dump(mode="json")).status_code == 201
