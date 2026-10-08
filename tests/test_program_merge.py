import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from bugbounty_catalog.db import CatalogDB
from bugbounty_catalog.models import Program
from bugbounty_catalog.normalize import assets_from_values
from bugbounty_catalog.program_merge import policy_identity, select_policy_url
from bugbounty_catalog.query import AssetQuery, ProgramQuery


def observation(source, *, url="https://hackerone.com/acronis", targets=("*.example.com",), excluded=()):
    return Program(source=source, source_id=source + "-id", name="Acronis", platform="HackerOne",
                   program_url=url, assets=assets_from_values(targets, "in_scope")
                   + assets_from_values(excluded, "out_of_scope"), raw={"provider": source})


class ProgramMergeTests(unittest.TestCase):
    def test_policy_selection_canonicalizes_platforms_and_retains_provider_urls(self):
        urls = ("http://www.bugcrowd.com/agilislt/scope", "https://bugcrowd.com/engagements/agilislt")
        self.db.upsert_programs([observation("a", url=urls[0]), observation("b", url=urls[1])])
        detail = self.db.get_program(self.db.list_programs(ProgramQuery()).items[0]["id"])
        self.assertEqual(detail["program_url"], "https://bugcrowd.com/agilislt")
        self.assertEqual({row["program_url"] for row in detail["source_records"]}, set(urls))
        self.assertNotIn("payload_json", detail["source_records"][0])
        self.assertEqual(select_policy_url([observation("a", url="http://custom.example/policy")]), "http://custom.example/policy")
        for url in ("javascript:alert(1)", "https://user@hackerone.com/example", "https://hackerone.com:bad/example"):
            self.assertIsNone(select_policy_url([observation("a", url=url)]))

    def test_target_migration_recovers_provider_scope_and_keeps_history_inactive(self):
        active = replace(
            observation("rix-scope", targets=("example.com",)),
            raw={
                "inscope_domains": ["https://example.com/API", "Scope notes"],
                "outofscope_domains": [],
            },
        )
        historical = replace(
            observation(
                "chaos", url="https://hackerone.com/historical", targets=("old.example",)
            ),
            raw={"domains": ["https://old.example/API"], "bounty": True},
        )
        self.db.upsert_programs([active, historical])
        self.refresh("chaos", [])
        with self.db.conn:
            self.db.conn.execute("DELETE FROM schema_migrations WHERE version=6")
        self.db.init_schema()
        active = self.db.list_assets(AssetQuery()).items
        self.assertEqual(active, [])
        historical = self.db.list_assets(AssetQuery(active=False)).items
        self.assertEqual(historical, [])
        self.assertEqual(self.db.list_programs(ProgramQuery(active=False)).total, 1)
        counts = self.db.conn.execute("SELECT COUNT(*) FROM asset_events").fetchone()[0]
        self.db.init_schema()
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM asset_events").fetchone()[0], counts)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "catalog.sqlite"
        self.db = CatalogDB(self.path)
        self.addCleanup(self.db.close)
        self.db.init_schema()

    def refresh(self, source, programs):
        run_id = self.db.start_run(source)
        return self.db.apply_source_snapshot(source, programs, source_run_id=run_id)

    def test_three_sources_merge_scope_keep_exclusions_and_filter_by_each_source(self):
        self.refresh("chaos", [observation("chaos", targets=("*.example.com", "chaos.example"))])
        first_id = self.db.list_programs(ProgramQuery()).items[0]["id"]
        self.refresh("arkadiyt", [observation("arkadiyt", targets=("*.example.com", "api.example"))])
        self.refresh("rix-scope", [observation("rix-scope", targets=("*.example.com",), excluded=("admin.example.com",))])
        page = self.db.list_programs(ProgramQuery())
        self.assertEqual(page.total, 1)
        self.assertEqual(page.items[0]["id"], first_id)
        detail = self.db.get_program(first_id)
        self.assertEqual(len(detail["assets"]), 4)
        self.assertEqual(len(detail["source_records"]), 3)
        self.assertEqual(detail["platform"], "hackerone")
        for source in ("chaos", "arkadiyt", "rix-scope"):
            self.assertEqual(self.db.list_programs(ProgramQuery(source=source)).total, 1)
            self.assertEqual(self.db.list_assets(AssetQuery(source=source)).total, 4)
        self.assertEqual(len(self.db.search_target("admin.example.com")["excluded"]), 1)

    def test_refresh_replaces_only_that_sources_snapshot(self):
        a = observation("a", targets=("shared.example", "a.example"))
        b = observation("b", targets=("shared.example", "b.example"))
        self.refresh("a", [a])
        self.refresh("b", [b])
        self.refresh("a", [replace(a, assets=assets_from_values(["new.example"], "in_scope"))])
        values = {row["raw_value"] for row in self.db.list_assets(AssetQuery()).items}
        self.assertEqual(values, {"new.example", "shared.example", "b.example"})
        self.refresh("b", [])
        self.assertEqual(self.db.list_programs(ProgramQuery()).total, 1)
        self.assertEqual({row["raw_value"] for row in self.db.list_assets(AssetQuery()).items}, {"new.example"})
        self.assertEqual(self.db.list_programs(ProgramQuery(source="b")).total, 0)
        self.refresh("a", [])
        self.assertEqual(self.db.list_programs(ProgramQuery()).total, 0)
        self.assertEqual(self.db.list_programs(ProgramQuery(active=None)).total, 1)
        self.refresh("b", [b])
        self.assertEqual(self.db.list_programs(ProgramQuery()).total, 1)

    def test_refresh_failure_rolls_back_source_and_merged_scope(self):
        self.refresh("a", [observation("a")])
        with patch.object(self.db, "_sync_assets", side_effect=RuntimeError("disk failure")):
            with self.assertRaises(RuntimeError):
                self.refresh("b", [observation("b", targets=("other.example",))])
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM program_sources").fetchone()[0], 1)
        self.assertEqual(self.db.list_assets(AssetQuery()).items[0]["raw_value"], "*.example.com")

    def test_different_policies_and_missing_urls_do_not_merge_on_name_or_assets(self):
        self.db.upsert_programs([observation("a", url="https://hackerone.com/policy-a"),
                                 observation("b", url="https://hackerone.com/policy-b"),
                                 observation("c", url=None), observation("d", url=None)])
        self.assertEqual(self.db.list_programs(ProgramQuery()).total, 4)

    def test_url_aliases_and_asset_spellings_are_deduplicated(self):
        self.db.upsert_programs([
            observation("a", url="http://www.bugcrowd.com/agilislt/", targets=("*.EXAMPLE.com",)),
            observation("b", url="https://bugcrowd.com/engagements/agilislt", targets=("*.example.com",)),
        ])
        self.assertEqual(self.db.list_programs(ProgramQuery()).total, 1)
        self.assertEqual(self.db.list_assets(AssetQuery()).total, 1)
        self.assertNotEqual(policy_identity(observation("a", url="https://hackerone.com/reports/1")),
                            policy_identity(observation("b", url="https://hackerone.com/reports/2")))

    def test_cross_provider_dedupe_keeps_scope_wildcards_and_rejects_urls(self):
        self.db.upsert_programs([
            observation("a", targets=("EXAMPLE.com", "*.example.com", "https://example.com/API",
                                      "https://example.com/API/"), excluded=("*.example.com",)),
            observation("b", targets=("https://example.com/", "https://*.example.com/"),
                        excluded=("*.EXAMPLE.com",)),
        ])
        assets = self.db.list_assets(AssetQuery()).items
        self.assertEqual(len(assets), 3)
        host_rules = {(asset["scope"], bool(asset["is_wildcard"])) for asset in assets
                      if asset["normalized_value"] == "example.com"}
        self.assertEqual(host_rules, {("in_scope", False), ("in_scope", True), ("out_of_scope", True)})
        self.assertFalse(any(asset["asset_type"] == "url" for asset in assets))

    def test_migration_merges_existing_rows_preserves_old_ids_and_is_idempotent(self):
        # Recreate v1 storage through the original row operations, without observations.
        with self.db.conn:
            ids = []
            for source in ("chaos", "arkadiyt", "rix-scope"):
                program = observation(source, targets=("*.example.com", source + ".example"))
                program_id = self.db._upsert_program(program)
                self.db._sync_assets(program_id, program)
                ids.append(program_id)
            self.db.conn.execute("DELETE FROM schema_migrations WHERE version=2")
        self.db.init_schema()
        self.assertEqual(self.db.list_programs(ProgramQuery()).total, 1)
        for old_id in ids:
            self.assertEqual(self.db.get_program(old_id)["id"], min(ids))
            self.assertEqual(self.db.list_assets(AssetQuery(program_id=old_id)).total, 4)
        from bugbounty_catalog.application import CatalogService
        service = CatalogService(self.path)
        resolved_ids = {service.program(old_id)["id"] for old_id in ids}
        self.assertEqual(resolved_ids, {min(ids)})
        self.assertEqual(self.db.conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.db.init_schema()
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM program_sources").fetchone()[0], 3)
        self.refresh("rix-scope", [observation("rix-scope", targets=("fresh.example",))])
        values = {row["raw_value"] for row in self.db.list_assets(AssetQuery()).items}
        self.assertEqual(values, {"*.example.com", "chaos.example", "arkadiyt.example", "fresh.example"})

    def test_exclusion_and_exact_wildcard_grants_never_collapse_together(self):
        self.db.upsert_programs([observation("a", targets=("*.example.com", "example.com")),
                                 observation("b", targets=(), excluded=("*.example.com",))])
        self.assertEqual(self.db.list_assets(AssetQuery()).total, 3)

    def test_yeswehack_missing_url_merges_by_slug_not_company_name(self):
        for slug, name in (("datadome-bot-bounty", "DataDome Bot Bounty"),
                           ("datadome-bug-bounty", "DataDome Bug Bounty")):
            a = replace(observation("arkadiyt", url=None), name=name, platform="yeswehack", source_id=slug,
                        raw={"id": slug})
            b = replace(observation("rix-scope", url="https://yeswehack.com/programs/" + slug),
                        name=name, platform="YesWeHack", source_id=slug)
            self.db.upsert_programs([a, b])
        self.assertEqual(self.db.list_programs(ProgramQuery()).total, 2)
        self.assertTrue(all(len(self.db.get_program(p["id"])["source_records"]) == 2
                            for p in self.db.list_programs(ProgramQuery()).items))

    def test_version_three_repairs_all_reported_yeswehack_pairs(self):
        slugs = ("datadome-bot-bounty", "datadome-bug-bounty", "cyberghost-bug-bounty-program",
                 "doctolib-public-bug-bounty-program", "expressvpn-bug-bounty-program", "ezviz-bug-bounty-program")
        ids = []
        # Emulate version 2, when URL-less snapshots had source-only identities.
        with patch("bugbounty_catalog.db.policy_identity", side_effect=lambda p:
                   f"source:{p.source}:{p.source_id}" if not p.program_url else policy_identity(p)):
            for slug in slugs:
                a = replace(observation("arkadiyt", url=None, targets=("a.example",)),
                            name=slug, platform="yeswehack", source_id=slug, raw={"id": slug})
                b = replace(observation("rix-scope", url="https://yeswehack.com/programs/" + slug,
                                        targets=("*.example.com",)), name=slug, platform="yeswehack", source_id=slug)
                self.db.upsert_programs([a, b])
                ids.append([r["id"] for r in self.db.list_programs(ProgramQuery(text=slug)).items])
        self.assertEqual(self.db.list_programs(ProgramQuery()).total, 12)
        with self.db.conn:
            self.db.conn.execute("DELETE FROM schema_migrations WHERE version=3")
        self.db.init_schema()
        for pair in ids:
            details = [self.db.get_program(pid) for pid in pair]
            self.assertEqual(details[0]["id"], details[1]["id"])
            self.assertEqual(len(details[0]["assets"]), 2)
            self.assertEqual(len(details[0]["source_records"]), 2)
        self.assertEqual(self.db.list_programs(ProgramQuery()).total, 6)
        self.db.init_schema()
        self.assertEqual(self.db.list_programs(ProgramQuery()).total, 6)
        self.assertEqual(self.db.conn.execute("PRAGMA foreign_key_check").fetchall(), [])


if __name__ == "__main__":
    unittest.main()
