import unittest

from bugbounty_catalog.query import AssetQuery, MAX_PAGE_SIZE, Page, ProgramQuery


class ProgramQueryTests(unittest.TestCase):
    def test_from_mapping_normalizes_filters_and_pagination(self):
        query = ProgramQuery.from_mapping(
            {
                "q": "  Example Org  ",
                "source": "  provider-a ",
                "platform": " hackerone ",
                "status": " active ",
                "active": "all",
                "limit": str(MAX_PAGE_SIZE + 500),
                "offset": "12",
            }
        )

        self.assertEqual(query.text, "Example Org")
        self.assertEqual(query.source, "provider-a")
        self.assertEqual(query.platform, "hackerone")
        self.assertEqual(query.status, "active")
        self.assertIsNone(query.active)
        self.assertEqual(query.limit, MAX_PAGE_SIZE)
        self.assertEqual(query.offset, 12)

    def test_from_mapping_defaults_to_active_first_page(self):
        query = ProgramQuery.from_mapping({})

        self.assertTrue(query.active)
        self.assertEqual(query.limit, 50)
        self.assertEqual(query.offset, 0)

    def test_from_mapping_rejects_invalid_pagination_and_active_values(self):
        invalid = (
            ({"limit": "many"}, "limit must be an integer"),
            ({"limit": "0"}, "limit must be at least 1"),
            ({"offset": "-1"}, "offset cannot be negative"),
            ({"active": "perhaps"}, "invalid boolean value"),
        )

        for values, message in invalid:
            with self.subTest(values=values):
                with self.assertRaisesRegex(ValueError, message):
                    ProgramQuery.from_mapping(values)

    def test_page_reports_when_more_results_are_available(self):
        page = Page(items=[{"id": 2}, {"id": 3}], total=5, limit=2, offset=1)

        self.assertEqual(
            page.to_dict(),
            {
                "items": [{"id": 2}, {"id": 3}],
                "total": 5,
                "limit": 2,
                "offset": 1,
                "has_more": True,
            },
        )

    def test_asset_active_filter_distinguishes_inactive_from_all(self):
        self.assertIs(AssetQuery.from_mapping({"active": "false"}).active, False)
        self.assertIsNone(AssetQuery.from_mapping({"active": "all"}).active)

if __name__ == "__main__":
    unittest.main()
