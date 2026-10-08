import unittest
import tempfile
from pathlib import Path
from unittest import mock

from bugbounty_catalog.application import CatalogService
from bugbounty_catalog.sources.arkadiyt import ARKADIYT_FILES, ArkadiytSource
from bugbounty_catalog.sources.chaos import ChaosSource
from bugbounty_catalog.sources.rix_scope import RixScopeSource


class ChaosSourceTests(unittest.TestCase):
    def test_parses_current_top_level_list_payload(self):
        payload = [
            {
                "name": "Example",
                "url": "https://hackerone.com/example",
                "bounty": True,
                "domains": ["example.com"],
            }
        ]
        with mock.patch("bugbounty_catalog.sources.chaos.fetch_json", return_value=payload):
            programs = ChaosSource().fetch()

        self.assertEqual(len(programs), 1)
        self.assertEqual(programs[0].name, "Example")
        self.assertEqual(programs[0].assets[0].normalized_value, "example.com")

    def test_retains_legacy_programs_envelope_compatibility(self):
        payload = {
            "programs": [
                {
                    "name": "Legacy",
                    "url": "https://bugcrowd.com/legacy",
                    "bounty": True,
                    "domains": ["legacy.example"],
                }
            ]
        }
        with mock.patch("bugbounty_catalog.sources.chaos.fetch_json", return_value=payload):
            programs = ChaosSource().fetch()

        self.assertEqual([program.name for program in programs], ["Legacy"])


class ArkadiytSourceTests(unittest.TestCase):
    def test_yeswehack_id_supplies_policy_url_without_changing_source_key(self):
        from bugbounty_catalog.normalize import stable_id

        raw = {"id": "datadome-bot-bounty", "name": "DataDome Bot Bounty", "max_bounty": 1000}
        parsed = ArkadiytSource()._parse_program("yeswehack", raw)
        self.assertEqual(parsed.program_url, "https://yeswehack.com/programs/datadome-bot-bounty")
        self.assertEqual(parsed.source_id, stable_id("arkadiyt", "yeswehack", None, raw["name"]))

    def test_fetch_drops_non_recon_targets(self):
        payload = [{
            "name": "Example",
            "max_payout": 1000,
            "targets": {
                "in_scope": [{"target": "example.com"}, {"target": "https://[customer].example.com"}],
                "out_of_scope": [{"target": "/api/*"}],
            },
        }]
        with mock.patch("bugbounty_catalog.sources.arkadiyt.fetch_json", return_value=payload):
            programs = ArkadiytSource(platforms=["bugcrowd"]).fetch()
        self.assertEqual(len(programs), 1)
        self.assertEqual([asset.normalized_value for asset in programs[0].assets], ["example.com"])

    def test_default_platforms_only_include_current_published_datasets(self):
        source = ArkadiytSource()

        self.assertEqual(source.platforms, tuple(ARKADIYT_FILES))
        self.assertNotIn("hackenproof", source.platforms)


class RixScopeSourceTests(unittest.TestCase):
    def test_refresh_retires_anonymous_placeholders_and_preserves_named_programs(self):
        from bugbounty_catalog.models import Program
        from bugbounty_catalog.db import CatalogDB
        from bugbounty_catalog.normalize import stable_id

        url = "https://hackerone.com/example-id/embedded_submissions/new"
        placeholder = {"name": "-", "program_url": url, "reward": "-",
                       "inscope_domains": [], "outofscope_domains": []}
        named = dict(placeholder, name="Example", program_url="https://example.com/bounty")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.sqlite"
            with CatalogDB(path) as db:
                db.init_schema()
                db.upsert_programs([Program(source="rix-scope",
                    source_id=stable_id("rix-scope", url, "-"), name="-", program_url=url)])
            service = CatalogService(path, source_factory=lambda *a, **kw: [RixScopeSource()])
            with mock.patch("bugbounty_catalog.sources.rix_scope.fetch_json",
                            return_value=[placeholder, named]):
                self.assertEqual(service.refresh("rix-scope")["status"], "completed")
            rows = service.programs()["items"]
            self.assertEqual([row["name"] for row in rows], ["Example"])
            self.assertIsNone(rows[0]["reward"])
            self.assertIsNone(rows[0]["offers_bounty"])

    def test_refresh_uses_newest_duplicate_snapshot_in_either_order(self):
        current = {
            "name": "Example", "program_url": "https://example.com/bounty",
            "last_updated": "2026-09-05", "reward": "$2500",
            "inscope_domains": ["example.com"],
            "outofscope_domains": ["admin.example.com"],
        }
        for old_date in ("2025-11-24", None, "invalid"):
            old = dict(current, last_updated=old_date, reward="$10",
                       inscope_domains=["stale.example.com"], outofscope_domains=[])
            for payload in ([old, current], [current, old]):
                with self.subTest(old_date=old_date, newest_first=payload[0] is current):
                    with tempfile.TemporaryDirectory() as directory:
                        service = CatalogService(
                            Path(directory) / "catalog.sqlite",
                            source_factory=lambda *args, **kwargs: [RixScopeSource()],
                        )
                        with mock.patch("bugbounty_catalog.sources.rix_scope.fetch_json", return_value=payload):
                            result = service.refresh("rix-scope")
                        self.assertEqual(result["status"], "completed", result)
                        self.assertEqual(result["records"], 1)
                        rows = service.export_rows("programs")
                        self.assertEqual(rows[0]["reward"], "$2500")
                        self.assertTrue(service.search("example.com")["allowed"])
                        self.assertTrue(service.search("admin.example.com")["excluded"])
                        self.assertFalse(service.search("stale.example.com")["allowed"])

    def test_identical_duplicates_keep_existing_id(self):
        row = {"name": "Example", "program_url": "https://example.com/bounty"}
        with mock.patch("bugbounty_catalog.sources.rix_scope.fetch_json", return_value=[row]):
            original = RixScopeSource().fetch()
        with mock.patch("bugbounty_catalog.sources.rix_scope.fetch_json", return_value=[row, row]):
            self.assertEqual(RixScopeSource().fetch(), original)

    def test_fetch_drops_non_recon_targets(self):
        payload = [{
            "name": "Example",
            "inscope_domains": ["example.com", "https://[customer].example.com"],
            "outofscope_domains": ["/api/*"],
        }]
        with mock.patch("bugbounty_catalog.sources.rix_scope.fetch_json", return_value=payload):
            programs = RixScopeSource().fetch()
        self.assertEqual(len(programs), 1)
        self.assertEqual([asset.normalized_value for asset in programs[0].assets], ["example.com"])


if __name__ == "__main__":
    unittest.main()
