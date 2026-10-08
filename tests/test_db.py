import tempfile
import unittest
from pathlib import Path

from bugbounty_catalog.db import CatalogDB
from bugbounty_catalog.models import Program, SCOPE_IN, SCOPE_OUT
from bugbounty_catalog.normalize import assets_from_values


class DatabaseTests(unittest.TestCase):
    def test_upsert_and_search_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "catalog.sqlite"
            with CatalogDB(db_path) as db:
                db.init_schema()
                run_id = db.start_run("test")
                program = Program(
                    source="test",
                    source_id="one",
                    name="Example",
                    platform="unit",
                    program_url="https://example.test/program",
                    assets=assets_from_values(["*.example.com"], SCOPE_IN)
                    + assets_from_values(["admin.example.com"], SCOPE_OUT),
                )
                db.upsert_programs([program], source_run_id=run_id)
                db.finish_run(run_id, status="completed", records_seen=1)

                allowed = db.search_target("api.example.com")
                excluded = db.search_target("admin.example.com")

            self.assertEqual(len(allowed["allowed"]), 1)
            self.assertEqual(len(excluded["allowed"]), 1)
            self.assertEqual(len(excluded["excluded"]), 1)


if __name__ == "__main__":
    unittest.main()

