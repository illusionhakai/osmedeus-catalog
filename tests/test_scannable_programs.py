import pytest

from bugbounty_catalog.db import CatalogDB
from bugbounty_catalog.models import Program
from bugbounty_catalog.normalize import assets_from_values
from bugbounty_catalog.query import ProgramQuery
from test_companion import client


def program(name, targets=(), exclusions=(), *, status="active", scope="in_scope"):
    return Program(source="fixture", source_id=name, name=name, status=status,
        platform="hackerone", assets=assets_from_values(targets, scope) + assets_from_values(exclusions, "out_of_scope"))


def test_supported_program_types_and_retention(tmp_path):
    with CatalogDB(tmp_path / "catalog.sqlite") as db:
        db.init_schema()
        db.upsert_programs([
            program("Domain", ["domain.example"]), program("Wildcard", ["*.wild.example"]),
            program("IPv4", ["192.0.2.1"]), program("IPv6", ["2001:db8::1"]),
            program("CIDR", ["198.51.100.0/24"]), program("Empty"),
            program("URL only", ["https://example.com/private"]),
            program("Repository only", ["https://github.com/example/project"]),
            program("Candidate", ["candidate.example"], scope="candidate"),
            program("Out of scope", ["excluded.example"], scope="out_of_scope"),
            program("Inactive", ["inactive.example"], status="inactive"),
        ])
        before = db.conn.execute("SELECT COUNT(*) FROM programs").fetchone()[0]
        page = db.list_programs(ProgramQuery(scannable_only=True))
        assert {row["name"] for row in page.items} == {"Domain", "Wildcard", "IPv4", "IPv6", "CIDR"}
        assert page.total == 5
        assert db.conn.execute("SELECT COUNT(*) FROM programs").fetchone()[0] == before == 11
        assert db.list_programs(ProgramQuery(active=None)).total == 11


def test_exclusions_are_program_local_and_override_scan_targets(tmp_path):
    with CatalogDB(tmp_path / "catalog.sqlite") as db:
        db.init_schema()
        db.upsert_programs([
            program("Denied exact", ["denied.example"], ["denied.example"]),
            program("Allowed elsewhere", ["denied.example"]),
            program("Denied subdomain", ["api.denied.example"], ["*.denied.example"]),
            program("Overlapping CIDR", ["192.0.2.0/24"], ["192.0.2.20"]),
            program("Contained IP", ["198.51.100.10"], ["198.51.100.0/24"]),
            program("Denied wildcard", ["*.child.example.com"], ["*.example.com"]),
            program("Remaining input", ["blocked.example", "usable.example"], ["blocked.example"]),
        ])
        assert {row["name"] for row in db.list_programs(ProgramQuery(scannable_only=True)).items} == {
            "Allowed elsewhere", "Remaining input"}


def test_wildcard_and_apex_are_not_equivalent(tmp_path):
    with CatalogDB(tmp_path / "catalog.sqlite") as db:
        db.init_schema()
        db.upsert_programs([
            program("Exact apex", ["example.com"], ["*.example.com"]),
            program("Wildcard root denied", ["*.example.org"], ["example.org"]),
            program("Wildcard survives leaf denial", ["*.example.net"], ["private.example.net"]),
        ])
        assert {row["name"] for row in db.list_programs(ProgramQuery(scannable_only=True)).items} == {
            "Exact apex", "Wildcard survives leaf denial"}


def test_scannable_filter_applies_before_pagination(tmp_path):
    with CatalogDB(tmp_path / "catalog.sqlite") as db:
        db.init_schema()
        db.upsert_programs([program("A hidden"), program("B visible", ["b.example"]),
                            program("C hidden"), program("D visible", ["*.d.example"])])
        first = db.list_programs(ProgramQuery(scannable_only=True, limit=1))
        second = db.list_programs(ProgramQuery(scannable_only=True, limit=1, offset=1))
        assert first.total == second.total == 2
        assert first.items[0]["name"] == "B visible" and first.to_dict()["has_more"]
        assert second.items[0]["name"] == "D visible" and not second.to_dict()["has_more"]
        assert db.list_programs(ProgramQuery(scannable_only=True, text="hidden")).total == 0
        assert db.list_programs(ProgramQuery(scannable_only=True, source="other")).total == 0


def test_scannable_flag_is_opt_in_and_validated():
    assert not ProgramQuery.from_mapping({}).scannable_only
    assert ProgramQuery.from_mapping({"scannable_only": "true"}).scannable_only
    assert not ProgramQuery.from_mapping({"scannable_only": "false"}).scannable_only
    with pytest.raises(ValueError):
        ProgramQuery.from_mapping({"scannable_only": "sometimes"})


def test_api_filters_without_removing_stored_programs(client):
    with CatalogDB(client.app.state.catalog.db_path) as db:
        db.upsert_programs([program("Hidden"), program("Visible", ["visible.example"])])
    assert client.get("/catalog-api/v1/programs").json()["total"] == 2
    response = client.get("/catalog-api/v1/programs?scannable_only=true&limit=1").json()
    assert response["total"] == 1 and response["items"][0]["name"] == "Visible"
    assert client.get("/catalog-api/v1/programs").json()["total"] == 2
