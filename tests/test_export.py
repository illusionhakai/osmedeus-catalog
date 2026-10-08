import unittest

from bugbounty_catalog.export import serialize_rows


class ExportTests(unittest.TestCase):
    def test_unique_targets_keep_apex_and_wildcard_distinct(self):
        rows = [
            {"normalized_value": "example.com", "asset_type": "domain", "is_wildcard": False},
            {"normalized_value": "example.com", "asset_type": "website", "is_wildcard": True},
            {"normalized_value": "example.com", "asset_type": "domain", "is_wildcard": True},
        ]
        self.assertEqual(serialize_rows(rows, fmt="txt"), "example.com\n*.example.com\n")

    def test_text_export_deduplicates_values_and_preserves_first_seen_order(self):
        rows = [
            {"normalized_value": "example.com", "raw_value": "EXAMPLE.COM"},
            {"normalized_value": "example.com", "raw_value": "example.com"},
            {"normalized_value": None, "raw_value": "*.example.net"},
            {"normalized_value": "", "raw_value": "*.example.net"},
            {"normalized_value": "api.example.org", "raw_value": "api.example.org"},
            {"normalized_value": "  ", "raw_value": "ignored.example"},
        ]

        rendered = serialize_rows(rows, fmt="txt")

        self.assertEqual(rendered, "example.com\n*.example.net\napi.example.org\n")

    def test_text_export_can_use_raw_values_for_wildcards(self):
        rows = [
            {"normalized_value": "example.com", "raw_value": "*.example.com"},
            {"normalized_value": "example.com", "raw_value": "*.example.com"},
        ]

        self.assertEqual(
            serialize_rows(rows, fmt="txt", value_field="raw_value"),
            "*.example.com\n",
        )


if __name__ == "__main__":
    unittest.main()
