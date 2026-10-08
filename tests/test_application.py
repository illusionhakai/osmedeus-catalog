import tempfile
import unittest
from pathlib import Path

from bugbounty_catalog.application import CatalogService
from bugbounty_catalog.db import CatalogDB
from bugbounty_catalog.models import (
    Program,
    SCOPE_CANDIDATE,
    SCOPE_IN,
    SCOPE_OUT,
)
from bugbounty_catalog.normalize import assets_from_values
from bugbounty_catalog.query import ProgramQuery


class CatalogServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "catalog.sqlite"
        assets = (
            assets_from_values(["*.example.com"], SCOPE_IN)
            + assets_from_values(["admin.example.com"], SCOPE_OUT)
            + assets_from_values(["admin.example.com"], SCOPE_CANDIDATE)
        )
        with CatalogDB(self.db_path) as db:
            db.init_schema()
            db.upsert_programs(
                [
                    Program(
                        source="provider-a",
                        source_id="conflicting",
                        name="Conflicting Program",
                        assets=assets,
                    )
                ]
            )
        self.service = CatalogService(self.db_path)

    def test_search_surfaces_conflicts_and_authorization_warnings(self):
        result = self.service.search("  admin.example.com  ")

        self.assertEqual(result["target"], "admin.example.com")
        self.assertEqual(result["normalized_target"], "admin.example.com")
        self.assertEqual(result["target_type"], "domain")
        self.assertTrue(result["conflict"])
        self.assertEqual(len(result["allowed"]), 1)
        self.assertEqual(len(result["excluded"]), 1)
        self.assertEqual(len(result["candidate"]), 1)
        self.assertTrue(
            any("explicit exclusions" in warning for warning in result["warnings"])
        )
        self.assertTrue(
            any("both allowed and excluded" in warning for warning in result["warnings"])
        )
        self.assertTrue(
            any("evidence only" in warning for warning in result["warnings"])
        )

    def test_search_warns_when_catalog_has_no_match(self):
        result = self.service.search("unlisted.example.net")

        self.assertFalse(result["conflict"])
        self.assertEqual(result["allowed"], [])
        self.assertEqual(result["excluded"], [])
        self.assertEqual(result["candidate"], [])
        self.assertEqual(len(result["warnings"]), 1)
        self.assertIn("No local match", result["warnings"][0])

    def test_search_requires_a_non_empty_target(self):
        with self.assertRaisesRegex(ValueError, "target is required"):
            self.service.search("   ")

    def test_refresh_applies_snapshot_and_marks_missing_program_inactive(self):
        initial = [
            Program(source="fixture", source_id="kept", name="Kept"),
            Program(source="fixture", source_id="removed", name="Removed"),
        ]
        service = CatalogService(
            self.db_path,
            source_factory=lambda _name, **_kwargs: [_FixtureSource("fixture", initial)],
        )
        first = service.refresh("fixture")
        self.assertEqual(first["status"], "completed")

        service = CatalogService(
            self.db_path,
            source_factory=lambda _name, **_kwargs: [
                _FixtureSource("fixture", [Program(source="fixture", source_id="kept", name="Kept")])
            ],
        )
        second = service.refresh("fixture")
        all_programs = service.programs(ProgramQuery(source="fixture", active=None))

        self.assertEqual(second["status"], "completed")
        states = {item["source_id"]: item["is_active"] for item in all_programs["items"]}
        self.assertEqual(states, {"kept": 1, "removed": 0})

    def test_multi_source_refresh_continues_and_reports_partial_failure(self):
        service = CatalogService(
            self.db_path,
            source_factory=lambda _name, **_kwargs: [
                _FixtureSource("broken", error=RuntimeError("provider unavailable")),
                _FixtureSource(
                    "working",
                    [Program(source="working", source_id="one", name="Working Program")],
                ),
            ],
        )

        report = service.refresh("all")

        self.assertEqual(report["status"], "partial")
        self.assertEqual([item["status"] for item in report["sources"]], ["failed", "completed"])
        self.assertEqual(service.programs(ProgramQuery(source="working"))["total"], 1)

    def test_refresh_rejects_cross_source_and_duplicate_programs(self):
        invalid_payloads = (
            [Program(source="other", source_id="one", name="Wrong Source")],
            [
                Program(source="fixture", source_id="duplicate", name="First"),
                Program(source="fixture", source_id="duplicate", name="Second"),
            ],
        )
        for programs in invalid_payloads:
            with self.subTest(programs=programs):
                service = CatalogService(
                    self.db_path,
                    source_factory=lambda _name, **_kwargs: [
                        _FixtureSource("fixture", programs)
                    ],
                )
                report = service.refresh("fixture")
                self.assertEqual(report["status"], "failed")

        self.assertEqual(
            self.service.programs(ProgramQuery(active=None))["total"],
            1,
        )

    def test_refresh_rolls_back_snapshot_when_finalization_fails(self):
        service = CatalogService(
            self.db_path,
            store_factory=lambda: _FailingSnapshotDB(self.db_path),
            source_factory=lambda _name, **_kwargs: [
                _FixtureSource(
                    "fixture",
                    [Program(source="fixture", source_id="new", name="New Program")],
                )
            ],
        )

        report = service.refresh("fixture")

        self.assertEqual(report["status"], "failed")
        with CatalogDB(self.db_path) as db:
            db.init_schema()
            self.assertEqual(db.list_programs(ProgramQuery(source="fixture", active=None)).total, 0)
            self.assertEqual(db.recent_source_runs(limit=1)[0]["status"], "failed")

    def test_refresh_rejects_an_empty_source_selection(self):
        service = CatalogService(
            self.db_path,
            source_factory=lambda _name, **_kwargs: [],
        )
        with self.assertRaisesRegex(ValueError, "no sources selected"):
            service.refresh("missing")

    def test_manual_program_rejects_descriptions_and_urls(self):
        with self.assertRaisesRegex(ValueError, "Unsupported catalog target"):
            self.service.add_program(
                name="Invalid scope",
                in_scope=("3CX Phone System",),
            )

        with self.assertRaisesRegex(ValueError, "Unsupported catalog target"):
            self.service.add_program(
                name="URL scope",
                in_scope=("https://portal.example.org/admin/login?source=policy",),
            )

        result = self.service.add_program(
            name="Canonical scope",
            in_scope=("PORTAL.EXAMPLE.ORG.",),
        )
        self.assertEqual(result["program"]["assets"][0]["raw_value"], "portal.example.org")
        self.assertEqual(result["program"]["assets"][0]["asset_type"], "domain")

class _FixtureSource:
    def __init__(self, name, programs=None, error=None):
        self.name = name
        self.programs = programs or []
        self.error = error

    def collect_programs(self):
        if self.error:
            raise self.error
        return self.programs


class _FailingSnapshotDB(CatalogDB):
    def _finalize_source_snapshot(self, source, seen_source_ids, *, source_run_id):
        raise RuntimeError("simulated finalization failure")


if __name__ == "__main__":
    unittest.main()
