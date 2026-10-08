import tempfile
import unittest
from pathlib import Path

from bugbounty_catalog.db import CatalogDB
from bugbounty_catalog.models import Asset, Program, SCOPE_CANDIDATE, SCOPE_IN, SCOPE_OUT
from bugbounty_catalog.normalize import assets_from_values
from bugbounty_catalog.query import ProgramQuery


def make_program(
    source_id: str,
    name: str,
    *,
    source: str = "provider-a",
    organization: str | None = None,
    platform: str = "hackerone",
    status: str = "active",
    in_scope: tuple[str, ...] = (),
    out_of_scope: tuple[str, ...] = (),
    candidate: tuple[str, ...] = (),
) -> Program:
    assets = (
        assets_from_values(in_scope, SCOPE_IN)
        + assets_from_values(out_of_scope, SCOPE_OUT)
        + assets_from_values(candidate, SCOPE_CANDIDATE)
    )
    return Program(
        source=source,
        source_id=source_id,
        name=name,
        organization=organization,
        platform=platform,
        program_url=f"https://programs.example/{source_id}",
        status=status,
        assets=assets,
    )


class CatalogRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "catalog.sqlite"
        self.db = CatalogDB(self.db_path)
        self.addCleanup(self.db.close)
        self.db.init_schema()

    def test_program_filters_and_pagination(self):
        self.db.upsert_programs(
            [
                make_program(
                    "alpha",
                    "Alpha Program",
                    organization="Example Org",
                    platform="hackerone",
                ),
                make_program(
                    "beta",
                    "Beta Program",
                    organization="Beta Org",
                    platform="bugcrowd",
                    status="paused",
                ),
                make_program(
                    "gamma",
                    "Gamma Program",
                    source="provider-b",
                    organization="Example Org",
                    platform="hackerone",
                ),
            ]
        )

        first_page = self.db.list_programs(
            ProgramQuery(source="provider-a", limit=1, offset=0)
        ).to_dict()
        second_page = self.db.list_programs(
            ProgramQuery(source="provider-a", limit=1, offset=1)
        ).to_dict()

        self.assertEqual(first_page["total"], 2)
        self.assertEqual([item["name"] for item in first_page["items"]], ["Alpha Program"])
        self.assertTrue(first_page["has_more"])
        self.assertEqual([item["name"] for item in second_page["items"]], ["Beta Program"])
        self.assertFalse(second_page["has_more"])

        by_text = self.db.list_programs(ProgramQuery(text="Example Org"))
        by_platform = self.db.list_programs(ProgramQuery(platform="hackerone"))
        by_status = self.db.list_programs(ProgramQuery(status="paused"))
        combined = self.db.list_programs(
            ProgramQuery(source="provider-a", platform="bugcrowd", status="paused")
        )

        self.assertEqual({item["name"] for item in by_text.items}, {"Alpha Program", "Gamma Program"})
        self.assertEqual(by_platform.total, 2)
        self.assertEqual([item["name"] for item in by_status.items], ["Beta Program"])
        self.assertEqual([item["source_id"] for item in combined.items], ["beta"])

    def test_snapshot_finalization_marks_missing_program_inactive_and_hides_it(self):
        run_id = self.db.start_run("provider-a")
        self.db.upsert_programs(
            [
                make_program("kept", "Kept Program", in_scope=("kept.example",)),
                make_program("removed", "Removed Program", in_scope=("removed.example",)),
                make_program(
                    "other-source",
                    "Other Source Program",
                    source="provider-b",
                    in_scope=("other.example",),
                ),
            ],
            source_run_id=run_id,
        )
        removed_id = self.db.conn.execute(
            "SELECT id FROM programs WHERE source = ? AND source_id = ?",
            ("provider-a", "removed"),
        ).fetchone()["id"]

        self.db.finalize_source_snapshot("provider-a", {"kept"}, source_run_id=run_id)

        active = self.db.list_programs(ProgramQuery(source="provider-a"))
        all_provider_programs = self.db.list_programs(
            ProgramQuery(source="provider-a", active=None)
        )
        hidden_search = self.db.search_target("removed.example")
        historical_search = self.db.search_target("removed.example", include_inactive=True)

        self.assertEqual([item["source_id"] for item in active.items], ["kept"])
        self.assertEqual(
            {item["source_id"] for item in all_provider_programs.items},
            {"kept", "removed"},
        )
        self.assertEqual(hidden_search, {"allowed": [], "excluded": [], "candidate": []})
        self.assertEqual(len(historical_search["allowed"]), 1)
        self.assertEqual(historical_search["allowed"][0]["program_id"], removed_id)
        self.assertEqual(self.db.get_program(removed_id)["is_active"], 0)

        removed_events = [
            event
            for event in self.db.recent_events(limit=20)
            if event["category"] == "program"
            and event["program_id"] == removed_id
            and event["event_type"] == "removed"
        ]
        self.assertEqual(len(removed_events), 1)

    def test_upsert_preserves_program_and_asset_identity_and_first_seen(self):
        original_asset = Asset(
            scope=SCOPE_IN,
            raw_value="stable.example",
            asset_type="domain",
            normalized_value="stable.example",
            notes="initial",
        )
        original = Program(
            source="provider-a",
            source_id="stable",
            name="Original Name",
            assets=(original_asset,),
            raw={"revision": 1},
        )
        self.db.upsert_programs([original])
        before_program = self.db.conn.execute(
            "SELECT id, first_seen_at FROM programs WHERE source = ? AND source_id = ?",
            ("provider-a", "stable"),
        ).fetchone()
        before_asset = self.db.conn.execute(
            "SELECT id, first_seen_at FROM assets WHERE program_id = ?",
            (before_program["id"],),
        ).fetchone()

        updated = Program(
            source="provider-a",
            source_id="stable",
            name="Updated Name",
            assets=(
                Asset(
                    scope=SCOPE_IN,
                    raw_value="stable.example",
                    asset_type="domain",
                    normalized_value="stable.example",
                    notes="updated",
                ),
            ),
            raw={"revision": 2},
        )
        self.db.upsert_programs([updated])

        after_program = self.db.conn.execute(
            "SELECT id, first_seen_at, name FROM programs WHERE source = ? AND source_id = ?",
            ("provider-a", "stable"),
        ).fetchone()
        after_asset = self.db.conn.execute(
            "SELECT id, first_seen_at, notes FROM assets WHERE program_id = ?",
            (after_program["id"],),
        ).fetchone()

        self.assertEqual(after_program["id"], before_program["id"])
        self.assertEqual(after_program["first_seen_at"], before_program["first_seen_at"])
        self.assertEqual(after_program["name"], "Updated Name")
        self.assertEqual(after_asset["id"], before_asset["id"])
        self.assertEqual(after_asset["first_seen_at"], before_asset["first_seen_at"])
        self.assertEqual(after_asset["notes"], "updated")

    def test_catalog_export_excludes_mobile_store_records(self):
        android = "https://play.google.com/store/apps/details?id=com.example.android"
        ios = "https://apps.apple.com/us/app/example/id123456789"
        excluded_ios = "https://apps.apple.com/us/app/internal/id987654321"
        program = make_program(
            "mobile",
            "Mobile Program",
            in_scope=(android, ios, "example.com"),
            out_of_scope=(excluded_ios,),
        )
        self.db.upsert_programs([program])

        rows = [dict(row) for row in self.db.export_assets(kind="mobile_app")]

        self.assertEqual(rows, [])
        self.assertEqual(
            {row["raw_value"] for row in self.db.export_assets()},
            {"example.com"},
        )


if __name__ == "__main__":
    unittest.main()
