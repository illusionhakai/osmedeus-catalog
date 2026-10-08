import io
import json

import httpx
import pytest

from catalog_companion import programs, run


EXPORT = "a" * 32


def receipt():
    return {"export_id": EXPORT, "status": "ready", "snapshot_at": "2026-10-05T12:00:00Z", "files": [
        {"program_id": 1, "program_name": "Fixture", "kind": "domains", "is_target_file": True,
         "path": "/engine/uploads/domains.txt", "member": "program-1/domains.txt", "lines": 2},
        {"program_id": 1, "kind": "scope", "is_target_file": False,
         "path": "/engine/uploads/scope.json", "lines": 10}]}


def client_mock(monkeypatch, handler):
    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    monkeypatch.setenv("OSMEDEUS_TOKEN", "fixture-token")


def handoff_args(monkeypatch, *extra):
    monkeypatch.setattr("sys.argv", ["run", "--export-id", EXPORT, "--module", "catalog-input-smoke",
                                    "--target-kind", "domains", *extra])


def test_program_search_paginates_read_only(monkeypatch, capsys):
    offsets = []
    def handler(request):
        assert request.method == "GET" and request.url.path == "/catalog-api/v1/programs"
        assert request.headers["Authorization"] == "Bearer fixture-token"
        assert request.url.params["q"] == "Fixture" and request.url.params["source"] == "chaos"
        assert request.url.params["platform"] == "hackerone"
        offset = int(request.url.params["offset"])
        offsets.append(offset)
        return httpx.Response(200, json={"items": [{"id": offset + 1, "name": "Fixture"}],
                                         "total": 2, "limit": 1, "offset": offset, "has_more": offset == 0})
    client_mock(monkeypatch, handler)
    monkeypatch.setattr("sys.argv", ["programs", "--search", "Fixture", "--source", "chaos",
        "--platform", "hackerone", "--limit", "1", "--all-pages", "--json"])
    assert programs.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert offsets == [0, 1] and [i["id"] for i in result["items"]] == [1, 2]
    assert result["has_more"] is False


def test_program_table_handles_empty_result(monkeypatch, capsys):
    client_mock(monkeypatch, lambda r: httpx.Response(200, json={"items": [], "total": 0, "has_more": False}))
    monkeypatch.setattr("sys.argv", ["programs"])
    assert programs.main() == 0
    assert "ID\tNAME" in capsys.readouterr().out


def test_listing_rejects_stalled_pagination(monkeypatch):
    client_mock(monkeypatch, lambda r: httpx.Response(200, json={"items": [], "total": 2, "has_more": True}))
    monkeypatch.setattr("sys.argv", ["programs", "--all-pages"])
    with pytest.raises(RuntimeError, match="stalled"):
        programs.main()


def test_prepare_is_read_only_and_pipe_refetches_trusted_receipt(monkeypatch, capsys):
    requests = []
    def handler(request):
        requests.append(request)
        assert request.method == "GET"
        if request.url.path.endswith("/delivery"):
            return httpx.Response(200, json=receipt())
        return httpx.Response(200, json={"name": "catalog-input-smoke", "kind": "module"})
    client_mock(monkeypatch, handler)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"export_id": EXPORT, "files": [{"path": "/etc/passwd"}]})))
    monkeypatch.setattr("sys.argv", ["run", "--receipt-stdin", "--module", "catalog-input-smoke",
                                    "--target-kind", "domains", "--request-only"])
    assert run.main() == 0
    assert json.loads(capsys.readouterr().out) == {"module": "catalog-input-smoke", "target_file": "/engine/uploads/domains.txt"}
    assert len(requests) == 2


def test_explicit_submission_calls_run_once(monkeypatch, capsys):
    posts = []
    def handler(request):
        if request.method == "POST":
            assert request.url.path == "/osm/api/runs"
            posts.append(json.loads(request.content))
            return httpx.Response(202, json={"job_id": "fixture-job", "status": "queued"})
        if request.url.path.endswith("/delivery"):
            return httpx.Response(200, json=receipt())
        return httpx.Response(200, json={"name": "catalog-input-smoke", "kind": "module"})
    client_mock(monkeypatch, handler)
    handoff_args(monkeypatch, "--submit", "--acknowledge-scope")
    assert run.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "accepted" and result["submission"]["job_id"] == "fixture-job"
    assert posts == [{"module": "catalog-input-smoke", "target_file": "/engine/uploads/domains.txt"}]


@pytest.mark.parametrize("failure", ["timeout", "bad-json", "bad-shape", "redirect", "server-error"])
def test_uncertain_submission_never_retries(monkeypatch, failure):
    calls = []
    def handler(request):
        calls.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("untrusted upstream secret")
        return {"bad-json": httpx.Response(202, text="secret"),
                "bad-shape": httpx.Response(202, json={"secret": "bad"}),
                "redirect": httpx.Response(307, headers={"Location": "https://example.com"}),
                "server-error": httpx.Response(500)}[failure]
    with httpx.Client(base_url="http://fixture", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError, match="outcome unknown") as exc:
            run.submit(client, {"module": "fixture", "target_file": "/fixture.txt"})
    assert "secret" not in str(exc.value) and len(calls) == 1


@pytest.mark.parametrize("status", [401, 403, 404, 422])
def test_rejected_submission_is_reported(status):
    with httpx.Client(base_url="http://fixture", transport=httpx.MockTransport(lambda r: httpx.Response(status))) as client:
        with pytest.raises(RuntimeError, match="rejected"):
            run.submit(client, {})


@pytest.mark.parametrize("status", ["uploading", "failed", "interrupted"])
def test_unready_delivery_blocks_handoff(status):
    data = receipt()
    data["status"] = status
    with pytest.raises(RuntimeError, match="must be ready"):
        run.make_plan(data, EXPORT, None, "domains", "fixture", "flow")


def test_multiple_programs_require_explicit_selection():
    data = receipt()
    data["files"].append({**data["files"][0], "program_id": 2, "path": "/engine/uploads/other.txt"})
    with pytest.raises(RuntimeError, match="exactly one"):
        run.make_plan(data, EXPORT, None, "domains", "fixture", "flow")
    plan = run.make_plan(data, EXPORT, 2, "domains", "fixture", "flow")
    assert plan["request"]["target_file"] == "/engine/uploads/other.txt"
    assert "-f fixture" in plan["cli"]


@pytest.mark.parametrize("extra", [["--submit"], ["--submit", "--acknowledge-scope", "--request-only"],
    ["--program-id", "0"], ["--target-kind", "discovery-roots"], ["--module", "../../bad"]])
def test_invalid_scan_options_fail_before_network(monkeypatch, extra):
    handoff_args(monkeypatch, *extra)
    with pytest.raises(SystemExit) as exc:
        run.main()
    assert exc.value.code == 2


def test_workflow_kind_mismatch_does_not_submit(monkeypatch):
    def handler(request):
        assert request.method == "GET"
        return httpx.Response(200, json=receipt() if request.url.path.endswith("/delivery") else
                              {"name": "catalog-input-smoke", "kind": "flow"})
    client_mock(monkeypatch, handler)
    handoff_args(monkeypatch, "--submit", "--acknowledge-scope")
    with pytest.raises(RuntimeError, match="does not match"):
        run.main()


def test_workflow_listing_is_read_only(monkeypatch, capsys):
    def handler(request):
        assert request.method == "GET" and request.url.path == "/osm/api/workflows"
        assert request.url.params["search"] == "smoke"
        return httpx.Response(200, json={"data": [{"name": "catalog-input-smoke", "kind": "module"}],
                                         "pagination": {"total": 1}})
    client_mock(monkeypatch, handler)
    monkeypatch.setattr("sys.argv", ["run", "--list-workflows", "--search", "smoke"])
    assert run.main() == 0
    assert json.loads(capsys.readouterr().out)["data"][0]["kind"] == "module"
