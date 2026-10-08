"""Run inside the isolated test network; never access a deployed catalog."""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request


BASE = "http://gateway"


def request(path, token=None, data=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(BASE + path,
        data=json.dumps(data).encode() if data is not None else None, headers=headers)
    return urllib.request.urlopen(req, timeout=5)


def ready(path, token=None):
    deadline = time.monotonic() + 45
    while True:
        try:
            with request(path, token) as response:
                assert response.status == 200
                return
        except urllib.error.HTTPError as exc:
            if exc.code not in (502, 503) or time.monotonic() >= deadline:
                raise RuntimeError(f"Test endpoint {path} returned {exc.code}: {exc.read().decode()}") from exc
        except (OSError, TimeoutError):
            if time.monotonic() >= deadline:
                raise RuntimeError(f"Test endpoint did not become ready: {path}")
        time.sleep(0.5)


ready("/health")
with request("/osm/api/login", data={"username": "catalogcheck", "password": os.environ["TEST_PASSWORD"]}) as response:
    token = json.load(response)["token"]
if os.getenv("TEST_OUTAGE") == "true":
    for path in ("/scans", "/osm/api/runs?limit=1"):
        with request(path, token) as response:
            assert response.status == 200
    print("PASS: normal dashboard and engine remain available during catalog outage")
else:
    ready("/catalog-api/v1/overview", token)
    for path in ("/catalog-api/v1/overview", "/catalog-api/v1/programs", "/catalog-api/v1/schedule"):
        with request(path, token) as response:
            assert isinstance(json.load(response), dict)
    try:
        request("/catalog-api/v1/programs", "invalid")
    except urllib.error.HTTPError as exc:
        assert exc.code == 401
    else:
        raise AssertionError("Invalid token was accepted")
    with request("/bug-bounty") as response:
        assert response.status == 200 and b"/_next/" in response.read()
    print("PASS: real login, catalog authentication and gateway routes")
    with request("/catalog-api/v1/exports", token, {"filters": {"program_id": 1}}) as response:
        export = json.load(response)
    route = "/catalog-api/v1/exports/" + export["export_id"]
    with request(route + "/deliver", token, {}) as response:
        assert response.status == 202
    deadline = time.monotonic() + 45
    while True:
        with request(route + "/delivery", token) as response:
            receipt = json.load(response)
        if receipt["status"] != "uploading":
            assert receipt["status"] == "ready", receipt
            break
        assert time.monotonic() < deadline
        time.sleep(.2)
    with request("/osm/api/runs?limit=1", token) as response:
        assert json.load(response)["pagination"]["total"] == 0, "Delivery must not start scans"
    # New CLI commands use only public endpoints. The network has no external egress.
    def command(module, *args, stdin=None):
        result = subprocess.run([sys.executable, "-m", "catalog_companion." + module,
            "--gateway", BASE, *args], input=stdin, text=True, capture_output=True,
            env={**os.environ, "OSMEDEUS_TOKEN": token}, timeout=90)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)
    listed = command("programs", "--search", "Delivery fixture", "--json")
    assert listed["items"][0]["id"] == 1
    with request("/osm/api/workflows/refresh", token, {}) as response:
        assert response.status == 200
    workflows = command("run", "--list-workflows", "--search", "catalog-input-smoke")
    assert any(item["name"] == "catalog-input-smoke" for item in workflows["data"])
    prepared = command("run", "--receipt-stdin", "--module", "catalog-input-smoke",
        "--target-kind", "domains", stdin=json.dumps(receipt))
    assert prepared["status"] == "prepared" and prepared["target_count"] == 2
    with request("/osm/api/runs?limit=1", token) as response:
        assert json.load(response)["pagination"]["total"] == 0, "Preparation must not start scans"
    submitted = command("run", "--export-id", export["export_id"], "--module", "catalog-input-smoke",
        "--target-kind", "domains", "--submit", "--acknowledge-scope")
    assert submitted["status"] == "accepted" and submitted["submission"]["target_count"] == 2
    print("PASS: program/workflow listing -> receipt pipe -> read-only plan -> explicit harmless API run")
    def value(path, body=None):
        with request(path, token, body) as response:
            return json.load(response)

    def wait_batch(batch_id, predicate, timeout=90):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = value("/catalog-api/v1/scan-batches/" + batch_id)
            if predicate(result):
                return result
            assert result["status"] != "paused", result
            time.sleep(.5)
        raise AssertionError("Batch did not reach expected state: " + json.dumps(result))

    workflows = value("/catalog-api/v1/workflows")
    assert any(w["name"] == "catalog-cancel-smoke" for w in workflows["data"])
    organization = value("/osm/api/orgs", {"name": "Catalog gate destination"})["data"]
    for mode, domain in (("automatic", "enum-subdomain"), ("full", "domain-standard")):
        routed = value("/catalog-api/v1/scan-batches", {
            "selection": {"mode": "filtered", "filters": {"source": "routing-fixture", "scope": "in_scope"}},
            "org_uuid": organization["uuid"], "scan_mode": mode})
        assert routed["status"] == "prepared" and routed["delivery"] is None
        assert routed["request"]["convert_wildcards"] is True
        mappings = routed["request"]["workflows"]
        assert mappings["domains"]["name"] == domain
        assert mappings["ips"]["name"] == mappings["cidrs"]["name"] == "cidr"
        assert mappings["discovery-roots"]["name"] == "enum-subdomain"
        assert len(routed["items"]) == 4 and not routed["credentials_available"]
        assert routed["manifest"]["programs"][0]["conversions"][0]["apex_authorized"] is False
    print("PASS: automatic/full routing -> mixed hostname/IP/IPv6 CIDR/wildcard review without scans")
    preview = value("/catalog-api/v1/scan-batches", {
        "selection": {"mode": "filtered", "filters": {"source": "fixture", "scope": "in_scope", "scannable_only": True}},
        "org_uuid": organization["uuid"], "scan_mode": "automatic"})
    assert preview["status"] == "prepared" and preview["delivery"] is None
    assert len(preview["items"]) == 1 and preview["items"][0]["target_count"] == 2
    path = "/catalog-api/v1/scan-batches/" + preview["id"]
    value(path + "/start", {"confirmed": True})
    value(path + "/start", {"confirmed": True})
    running = wait_batch(preview["id"], lambda b: any(r["status"] == "running" for i in b["items"] for r in i["runs"]))
    assert running["delivery"]["status"] == "ready"
    assert running["items"][0]["accepted_count"] == 2
    value(path + "/cancel", {"confirmed": True})
    finished = wait_batch(preview["id"], lambda b: b["status"] == "cancelled")
    item = finished["items"][0]
    assert len(item["runs"]) == 2 and item["cancellation"]
    # The pinned engine can finalize an interrupted command as failed after it
    # acknowledges cancellation. Verify actual acknowledgements and all accepted
    # terminal records instead of requiring a durable cancelled status.
    assert all(r["status"] in ("completed", "failed", "cancelled") for r in item["runs"])
    assert set(item["cancellation"]).issubset({r["run_uuid"] for r in item["runs"]})
    assert any(ack.get("processes_terminated") for ack in item["cancellation"].values())
    for run in item["runs"]:
        details = value("/osm/api/runs/" + run["run_uuid"])
        assert details.get("data", details)["org_uuid"] == preview["request"]["org_uuid"]
    print("PASS: immutable batch review -> automatic delivery -> explicit launch -> progressive runs -> cancellation/organization")
    print("DELIVERY_RECEIPT=" + json.dumps(receipt))
