import hashlib
import json
import re
import threading
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from catalog_companion.api import create_app
from catalog_companion.delivery import DeliveryManager, DeliveryBusy
from catalog_companion.exports import create_bundle
from catalog_companion.state import State
from test_companion import seed, engine


def upload_parts(request):
    assert request.url.path == "/osm/api/upload-file"
    assert request.method == "POST"  # No API run submission is ever used.
    assert request.headers["authorization"] == "Bearer valid"
    payload = request.read()
    filename = re.search(rb'filename="([^"]+)"', payload)[1].decode()
    content = payload.split(b"\r\n\r\n", 1)[1].rsplit(b"\r\n--", 1)[0]
    return filename, content


def accepted(filename, content):
    remote = "123_" + filename
    return httpx.Response(200, json={"filename": remote, "path": "/engine/data/uploads/" + remote,
        "size": len(content), "lines": content.count(b"\n") + int(bool(content) and not content.endswith(b"\n"))})


def wait(manager, export_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        receipt = manager.get(export_id)
        if receipt["status"] != "uploading":
            # The task's final lock release follows its last persisted update.
            while manager.active and time.monotonic() < deadline:
                time.sleep(.01)
            return receipt
        time.sleep(.01)
    raise AssertionError("Delivery did not complete")


def bundle(tmp_path):
    seed(tmp_path / "catalog.sqlite")
    manifest = create_bundle(tmp_path / "catalog.sqlite", tmp_path / "exports", {})
    return tmp_path / "exports" / (manifest["export_id"] + ".zip")


def test_delivery_uses_only_upload_api_and_preserves_program_separation(tmp_path):
    path = bundle(tmp_path)
    uploads = {}
    def handler(request):
        filename, content = upload_parts(request)
        uploads[filename] = content
        return accepted(filename, content)
    manager = DeliveryManager(State(tmp_path / "control.sqlite"), "http://engine", httpx.MockTransport(handler))
    try:
        manager.submit(path, "Bearer valid")
        result = wait(manager, path.stem)
        assert result["status"] == "ready"
        targets = [item for item in result["files"] if item["is_target_file"]]
        assert {item["kind"] for item in targets} == {"domains", "ips", "cidrs"}
        for item in result["files"]:
            content = uploads[Path(item["path"]).name.removeprefix("123_")]
            assert hashlib.sha256(content).hexdigest() == item["sha256"]
            if item["member"] == "program-1/domains.txt":
                assert content == b"example.com\n"
            if item["member"] == "program-2/domains.txt":
                assert content == b"denied.example.com\n"  # Other program's exclusion does not leak.
        count = len(uploads)
        assert manager.submit(path, "Bearer valid")["status"] == "ready"
        assert len(uploads) == count
        assert "Bearer valid" not in json.dumps(manager.list())
    finally:
        manager.close()


@pytest.mark.parametrize("failure", ["timeout", "unauthorized", "redirect", "malformed", "wrong_size", "bad_path"])
def test_failure_is_persisted_and_explicit_retry_skips_confirmed_files(tmp_path, failure):
    path = bundle(tmp_path)
    calls = []
    broken = True
    def handler(request):
        filename, content = upload_parts(request)
        calls.append(filename)
        if broken and len(calls) == 2:
            if failure == "timeout":
                raise httpx.ReadTimeout("do-not-persist Bearer valid")
            if failure == "unauthorized":
                return httpx.Response(401)
            if failure == "redirect":
                return httpx.Response(302, headers={"location": "http://elsewhere"})
            body = accepted(filename, content).json()
            if failure == "malformed":
                return httpx.Response(200, text="invalid")
            body["size" if failure == "wrong_size" else "path"] = -1 if failure == "wrong_size" else "/tmp/../escape"
            return httpx.Response(200, json=body)
        return accepted(filename, content)
    manager = DeliveryManager(State(tmp_path / "control.sqlite"), "http://engine", httpx.MockTransport(handler))
    try:
        manager.submit(path, "Bearer valid")
        receipt = wait(manager, path.stem)
        assert receipt["status"] == "failed" and len(receipt["files"]) == 1
        assert receipt["pending_member"]
        assert "Bearer valid" not in json.dumps(receipt)
        manager.submit(path, "Bearer valid")
        assert len(calls) == 2  # No automatic retry of an ambiguous upstream write.
        broken = False
        manager.submit(path, "Bearer valid", retry=True)
        assert wait(manager, path.stem)["status"] == "ready"
        assert calls.count(calls[0]) == 1
    finally:
        manager.close()


def test_delivery_recovery_and_engine_isolation(tmp_path):
    state = State(tmp_path / "control.sqlite")
    manager = DeliveryManager(state, "http://engine")
    manager.save({"export_id": "a" * 32, "status": "uploading", "files": [], "pending_member": "domains.txt"})
    manager.close()
    manager = DeliveryManager(state, "http://engine")
    other = DeliveryManager(state, "http://another-engine")
    try:
        assert manager.get("a" * 32)["status"] == "interrupted"
        assert other.list() == []
    finally:
        manager.close()
        other.close()


def test_delivery_serialization(tmp_path):
    path = bundle(tmp_path)
    another = create_bundle(tmp_path / "catalog.sqlite", tmp_path / "exports", {})["export_id"]
    entered, release = threading.Event(), threading.Event()
    def handler(request):
        entered.set()
        release.wait(3)
        return accepted(*upload_parts(request))
    manager = DeliveryManager(State(tmp_path / "control.sqlite"), "http://engine", httpx.MockTransport(handler))
    try:
        manager.submit(path, "Bearer valid")
        assert entered.wait(2)
        assert manager.submit(path, "Bearer valid")["status"] == "uploading"
        with pytest.raises(DeliveryBusy):
            manager.submit(path.with_name(another + ".zip"), "Bearer valid")
    finally:
        release.set()
        manager.close()


def test_delivery_api_auth_status_and_no_scan_submission(tmp_path):
    path = bundle(tmp_path)
    def handler(request):
        if request.url.path == "/osm/api/runs":
            assert request.method == "GET"
            return engine(request)
        return accepted(*upload_parts(request))
    app = create_app(tmp_path, "http://engine", transport=httpx.MockTransport(handler), background=False)
    with TestClient(app) as client:
        route = f"/catalog-api/v1/exports/{path.stem}"
        assert client.post(route + "/deliver", json={}).status_code == 401
        client.headers["Authorization"] = "Bearer valid"
        assert client.get(route + "/delivery").status_code == 404
        assert client.post(route + "/deliver", json={}).status_code == 202
        assert wait(app.state.delivery, path.stem)["status"] == "ready"
        assert client.get(route + "/delivery").json()["status"] == "ready"
        assert len(client.get("/catalog-api/v1/deliveries").json()["items"]) == 1


def test_empty_target_bundle_cannot_be_delivered(tmp_path):
    seed(tmp_path / "catalog.sqlite")
    manifest = create_bundle(tmp_path / "catalog.sqlite", tmp_path / "exports", {"filters": {"scope": "candidate"}})
    manager = DeliveryManager(State(tmp_path / "control.sqlite"), "http://engine")
    try:
        with pytest.raises(ValueError, match="No usable targets"):
            manager.submit(tmp_path / "exports" / (manifest["export_id"] + ".zip"), "Bearer valid")
        assert manager.list() == []
    finally:
        manager.close()


def test_corrupt_bundle_is_rejected_before_upstream_write(tmp_path):
    import zipfile
    path = bundle(tmp_path)
    with zipfile.ZipFile(path) as source:
        entries = {name: source.read(name) for name in source.namelist()}
    entries["program-1/domains.txt"] = b"tampered.example\n"
    with zipfile.ZipFile(path, "w") as target:
        for name, content in entries.items():
            target.writestr(name, content)
    manager = DeliveryManager(State(tmp_path / "control.sqlite"), "http://engine")
    try:
        with pytest.raises(ValueError, match="checksum"):
            manager.submit(path, "Bearer valid")
        assert manager.list() == []
    finally:
        manager.close()


@pytest.mark.parametrize("args", [
    ["--program-id", "0"], ["--program-id", "-1"],
    ["--program-id", "1", "--retry"], ["--export-id", "../bad"],
    ["--export-id", "a" * 32, "--refresh"], ["--program-id", "1", "--timeout", "0"],
])
def test_cli_rejects_ambiguous_selection_before_network(monkeypatch, args):
    from catalog_companion.ship import main
    monkeypatch.setattr("sys.argv", ["ship", *args])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
