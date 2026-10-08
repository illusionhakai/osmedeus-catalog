import io
import json
import threading
import zipfile
from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from bugbounty_catalog.db import CatalogDB
from bugbounty_catalog.models import Program
from bugbounty_catalog.normalize import assets_from_values
from catalog_companion.api import create_app
from catalog_companion.exports import create_bundle, preview_bundle
from catalog_companion.state import State, RefreshManager


def engine(request):
    assert request.url.path == "/osm/api/runs"
    if request.headers.get("authorization") != "Bearer valid":
        return httpx.Response(401)
    return httpx.Response(200, json={"data": [], "pagination": {"total": 0}})


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, "http://engine", transport=httpx.MockTransport(engine), background=False)
    with TestClient(app, headers={"Authorization": "Bearer valid"}) as client:
        yield client


def seed(path, many=0):
    with CatalogDB(path) as db:
        db.init_schema()
        db.upsert_programs([
            Program(source="test", source_id="a", name="Alpha", platform="hackerone",
                assets=assets_from_values(["example.com", "*.example.com", "denied.example.com",
                    "192.0.2.1", "192.0.2.0/24", "2001:db8::/32",
                    *[f"host{i}.example.org" for i in range(many)]], "in_scope")
                    + assets_from_values(["denied.example.com", "192.0.2.20"], "out_of_scope")
                    + assets_from_values(["candidate.example.org"], "candidate")),
            Program(source="test", source_id="b", name="Beta",
                assets=assets_from_values(["denied.example.com"], "in_scope")),
        ])


def test_bundle_exclusions_and_program_boundaries(tmp_path):
    seed(tmp_path / "catalog.sqlite")
    manifest = create_bundle(tmp_path / "catalog.sqlite", tmp_path, {"convert_wildcards": True})
    a, b = manifest["programs"]
    assert a["counts"] == {"domains": 1, "ips": 1, "cidrs": 1, "discovery-roots": 1}
    assert b["counts"]["domains"] == 1  # Alpha's exclusions do not restrict Beta.
    assert a["conversions"][0]["apex_authorized"] is False
    omitted = {r["value"]: r["reason"] for r in a["omitted"]}
    assert omitted["192.0.2.0/24"] == "explicit_exclusion_or_network_overlap"
    assert omitted["candidate.example.org"] == "candidate"
    path = tmp_path / (manifest["export_id"] + ".zip")
    assert preview_bundle(path) == manifest
    with zipfile.ZipFile(path) as archive:
        assert archive.read("program-1/domains.txt") == b"example.com\n"
        assert b"*.example.com" in archive.read("program-1/scope.json")
        import hashlib
        for name, checksum in manifest["checksums"].items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == checksum


def test_all_pages_and_default_wildcards(tmp_path):
    seed(tmp_path / "catalog.sqlite", many=230)
    manifest = create_bundle(tmp_path / "catalog.sqlite", tmp_path, {})
    assert manifest["programs"][0]["counts"]["domains"] == 231
    assert manifest["programs"][0]["counts"]["discovery-roots"] == 0


def test_immutable_preview_download_after_refresh(client, tmp_path):
    seed(tmp_path / "catalog.sqlite")
    response = client.post("/catalog-api/v1/exports", json={"filters": {"program_id": 1}})
    assert response.status_code == 201, response.text
    manifest = response.json()
    url = f"/catalog-api/v1/exports/{manifest['export_id']}"
    first = client.get(url + "/download").content
    with CatalogDB(tmp_path / "catalog.sqlite") as db:
        db.upsert_programs([Program(source="test", source_id="a", name="Alpha",
                          assets=assets_from_values(["new.example.com"], "in_scope"))])
    assert client.get(url).json() == manifest
    assert client.get(url + "/download").content == first


def test_asset_filters_preserve_program_boundary(client, tmp_path):
    seed(tmp_path / "catalog.sqlite")
    pid = next(p["id"] for p in client.get("/catalog-api/v1/programs").json()["items"] if p["name"] == "Beta")
    filters = {"program_id": pid, "scope": "in_scope", "kind": "domains"}
    rows = client.get("/catalog-api/v1/assets", params=filters).json()
    assert rows["total"] == 1
    assert rows["items"][0]["program_id"] == pid
    manifest = client.post("/catalog-api/v1/exports", json={"filters": filters}).json()
    assert [p["id"] for p in manifest["programs"]] == [pid]
    assert client.post("/catalog-api/v1/exports", json={"asset_ids": [999999]}).status_code == 422


@pytest.mark.parametrize("token", ["", "Bearer invalid", "Basic valid"])
def test_auth_rejects_bad_tokens(client, token):
    assert client.get("/catalog-api/v1/programs", headers={"Authorization": token}).status_code == 401


@pytest.mark.parametrize("mode", ["noauth", "html", "timeout", "redirect", "schema", "error"])
def test_auth_rejects_upstream_failures(tmp_path, mode):
    def handler(request):
        if mode == "timeout":
            raise httpx.ReadTimeout("timeout")
        if mode == "noauth":
            return httpx.Response(200, json={"data": [], "pagination": {}})
        if request.headers["authorization"] == "Bearer invalid-catalog-probe":
            return httpx.Response(401)
        return {"html": httpx.Response(200, text="<html>login</html>"),
                "schema": httpx.Response(200, json={"ok": True}),
                "redirect": httpx.Response(302, headers={"location": "/login"}),
                "error": httpx.Response(500)}[mode]
    with TestClient(create_app(tmp_path, "http://engine", transport=httpx.MockTransport(handler), background=False)) as client:
        assert client.get("/catalog-api/v1/programs", headers={"Authorization": "Bearer valid"}).status_code == 503


def test_schedule_validation_and_persistence(client):
    assert client.get("/catalog-api/v1/schedule").json()["timezone"] == "America/Sao_Paulo"
    assert client.put("/catalog-api/v1/schedule", json={"time": "25:00"}).status_code == 422
    assert client.put("/catalog-api/v1/schedule", json={"timezone": "not/a-zone"}).status_code == 422
    assert client.put("/catalog-api/v1/schedule", json={"enabled": False, "time": "04:15"}).status_code == 200
    assert client.get("/catalog-api/v1/schedule").json()["enabled"] is False


def test_refresh_serialization_and_daily_tick(tmp_path):
    started, release = threading.Event(), threading.Event()
    class Service:
        def refresh(self, *args, **kwargs):
            started.set()
            release.wait(5)
            return {"status": "completed"}
    state = State(tmp_path / "control.sqlite")
    manager = RefreshManager(Service(), state)
    try:
        first = manager.submit()
        assert started.wait(3)
        assert manager.submit() == first
        manager.tick(datetime(2026, 10, 5, 7, tzinfo=timezone.utc))
        manager.tick(datetime(2026, 10, 5, 8, tzinfo=timezone.utc))
        assert len(state.jobs()) == 1
        assert state.get("last_scheduled_day") == "2026-10-05"
    finally:
        release.set()
        manager.close()
    assert state.jobs()[0]["status"] == "completed"


def test_interrupted_job_recovery(tmp_path):
    state = State(tmp_path / "control.sqlite")
    with state.connect() as db:
        db.execute("INSERT INTO jobs VALUES ('interrupted','running','today',NULL,NULL,'{}')")
    assert State(state.path).jobs()[0]["status"] == "failed"


def test_backup_before_initialization(tmp_path):
    seed(tmp_path / "catalog.sqlite")
    with TestClient(create_app(tmp_path, "http://engine", background=False)):
        backups = list((tmp_path / "backups").glob("catalog-*.sqlite"))
        assert len(backups) == 1
        import sqlite3
        with sqlite3.connect(backups[0]) as db:
            assert db.execute("SELECT count(*) FROM programs").fetchone()[0] == 2


@pytest.mark.parametrize("body,status", [
    ({"data": None, "pagination": {"total": 0}}, 200),
    ({"data": None, "pagination": {"total": 1}}, 503),
    ({"pagination": {"total": 0}}, 503),
    ({"data": "", "pagination": {"total": 0}}, 503),
    ({"data": [], "pagination": {}}, 503),
    ({"data": None, "pagination": {"total": False}}, 503),
])
def test_real_engine_empty_run_shape(tmp_path, body, status):
    def handler(request):
        if request.headers["authorization"] == "Bearer invalid-catalog-probe":
            return httpx.Response(401)
        return httpx.Response(200, json=body)
    with TestClient(create_app(tmp_path, "http://engine", transport=httpx.MockTransport(handler), background=False)) as client:
        assert client.get("/catalog-api/v1/programs", headers={"Authorization": "Bearer valid"}).status_code == status


def test_overview_and_refresh_validation(client):
    overview = client.get("/catalog-api/v1/overview").json()
    assert len(overview["sources"]) == 3
    assert client.post("/catalog-api/v1/refresh", json={"source": "invalid"}).status_code == 422
    assert client.get("/catalog-api/v1/exports/invalid/download").status_code == 404
    assert client.post("/catalog-api/v1/exports", json={"filters": {"program_id": []}}).status_code == 422


def test_refresh_between_export_reads_keeps_one_snapshot(tmp_path, monkeypatch):
    path = tmp_path / "catalog.sqlite"
    seed(path, many=230)
    original = CatalogDB.iter_export_assets
    changed = False
    def read_then_refresh(db, query, **kwargs):
        nonlocal changed
        for row in original(db, query, **kwargs):
            if not changed:
                changed = True
                with CatalogDB(path) as writer:
                    writer.upsert_programs([Program(source="test", source_id="a", name="Alpha",
                        assets=assets_from_values(["replacement.example.org"], "in_scope"))])
            yield row
    monkeypatch.setattr(CatalogDB, "iter_export_assets", read_then_refresh)
    manifest = create_bundle(path, tmp_path, {})
    assert manifest["programs"][0]["counts"]["domains"] == 231
    with zipfile.ZipFile(tmp_path / (manifest["export_id"] + ".zip")) as archive:
        assert b"replacement.example.org" not in archive.read("program-1/scope.json")


@pytest.mark.parametrize("selection", ["program", "asset"])
def test_small_export_stays_within_query_budget_with_unrelated_assets(tmp_path, monkeypatch, selection):
    path = tmp_path / "catalog.sqlite"
    seed(path, many=1500)
    with CatalogDB(path) as db:
        program_id = db.conn.execute("SELECT id FROM programs WHERE name='Beta'").fetchone()[0]
        asset_id = db.conn.execute("SELECT id FROM assets WHERE program_id=?", (program_id,)).fetchone()[0]
    original = CatalogDB.__init__
    work = 0
    def bounded_reader(db, *args, **kwargs):
        original(db, *args, **kwargs)
        def progress():
            nonlocal work
            work += 1
            return int(work > 1000)
        db.conn.set_progress_handler(progress, 1000)
    monkeypatch.setattr(CatalogDB, "__init__", bounded_reader)
    request = ({"program_selection": {"mode": "ids", "program_ids": [program_id], "filters": {}}}
               if selection == "program" else {"asset_ids": [asset_id]})
    manifest = create_bundle(path, tmp_path, request)
    assert [p["id"] for p in manifest["programs"]] == [program_id]
    with zipfile.ZipFile(tmp_path / (manifest["export_id"] + ".zip")) as archive:
        assert archive.read(f"program-{program_id}/domains.txt") == b"denied.example.com\n"


def test_wildcard_parent_exclusion_and_inactive_export(tmp_path):
    path = tmp_path / "catalog.sqlite"
    with CatalogDB(path) as db:
        db.init_schema()
        db.upsert_programs([
            Program(source="test", source_id="a", name="Alpha",
                assets=assets_from_values(["*.child.example.com", "example.com"], "in_scope")
                + assets_from_values(["*.example.com"], "out_of_scope")),
            Program(source="test", source_id="b", name="Beta", status="closed",
                assets=assets_from_values(["closed.example.org"], "in_scope")),
        ])
    manifest = create_bundle(path, tmp_path, {"convert_wildcards": True})
    a, b = manifest["programs"]
    assert a["counts"]["discovery-roots"] == 0
    # A wildcard excludes children, not an independently included apex.
    assert a["counts"]["domains"] == 1
    assert b["counts"]["domains"] == 0
    assert b["omitted"][0]["reason"] == "inactive_program"


@pytest.mark.parametrize("payload", [[], {}, "malformed", [object()]])
def test_failed_source_keeps_snapshot(tmp_path, payload):
    from bugbounty_catalog.application import CatalogService
    path = tmp_path / "catalog.sqlite"
    seed(path)
    class Source:
        name = "test"
        def collect_programs(self):
            return payload
    service = CatalogService(path, source_factory=lambda *args, **kwargs: [Source()])
    assert service.refresh()["status"] == "failed"
    assert service.programs()["total"] == 2
    assert service.assets()["total"] > 0


@pytest.mark.parametrize("broken", [[], {}, [None]])
def test_arkadiyt_partial_dataset_failure_preserves_entire_provider(tmp_path, monkeypatch, broken):
    from bugbounty_catalog.application import CatalogService
    from bugbounty_catalog.sources.arkadiyt import ArkadiytSource
    path = tmp_path / "catalog.sqlite"
    good = [{"name": "Existing", "max_bounty": 1000,
             "targets": {"in_scope": [{"target": "existing.example.com", "eligible_for_bounty": True}]}}]
    source = ArkadiytSource(platforms=["bugcrowd", "hackerone"])
    monkeypatch.setattr("bugbounty_catalog.sources.arkadiyt.fetch_json", lambda *a, **k: good)
    service = CatalogService(path, source_factory=lambda *args, **kwargs: [source])
    service.initialize()
    assert service.refresh()["status"] == "completed"
    before = service.assets()["items"]
    monkeypatch.setattr("bugbounty_catalog.sources.arkadiyt.fetch_json",
        lambda url, **kwargs: broken if "hackerone" in url else good)
    assert service.refresh()["status"] == "failed"
    assert service.assets()["items"] == before
