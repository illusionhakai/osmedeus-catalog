import unittest

from bugbounty_catalog.models import SCOPE_IN
from bugbounty_catalog.normalize import (
    assets_from_values,
    classify_asset,
    expand_simple_alternation,
    strip_scheme_and_path,
    target_matches_asset,
)


class NormalizeTests(unittest.TestCase):
    def test_url_endpoint_slashes_remain_distinct(self):
        for path in ("/API/", "/API//users", "/API"):
            kind, normalized, wildcard = classify_asset("https://example.com" + path)
            self.assertEqual(normalized, "https://example.com" + path)
            self.assertTrue(target_matches_asset("https://example.com" + path, normalized, wildcard, kind))
            self.assertFalse(target_matches_asset("https://example.com/api/", normalized, wildcard, kind))
        self.assertFalse(target_matches_asset("https://example.com/API", "https://example.com/API/", False, "url"))

    def test_network_exclusions_match_overlap_and_individual_addresses(self):
        self.assertTrue(target_matches_asset("192.0.2.0/23", "192.0.2.0/24", False, "cidr", scope="out_of_scope"))
        self.assertFalse(target_matches_asset("192.0.2.0/23", "192.0.2.0/24", False, "cidr", scope="in_scope"))
        self.assertTrue(target_matches_asset("192.0.2.0/24", "192.0.2.42", False, "ip", scope="out_of_scope"))
        self.assertFalse(target_matches_asset("192.0.3.0/24", "192.0.2.42", False, "ip", scope="out_of_scope"))
        self.assertTrue(target_matches_asset("https://192.0.2.42/API", "192.0.2.0/24", False, "cidr", scope="out_of_scope"))
        self.assertFalse(target_matches_asset("2001:db8::/32", "192.0.2.0/24", False, "cidr", scope="out_of_scope"))

    def test_host_templates_cannot_become_broad_host_grants(self):
        for value in ("example.com/path", "example.com extra", "*example.com", "api*.example.com"):
            self.assertEqual(classify_asset(value)[0], "other", value)

    def test_equivalent_host_spellings_deduplicate_without_collapsing_wildcards(self):
        assets = assets_from_values(["EXAMPLE.com.", "https://example.com/", "example.com",
                                     "*.example.com", "https://*.example.com/"], SCOPE_IN)
        self.assertEqual(len(assets), 2)
        self.assertEqual({asset.is_wildcard for asset in assets}, {False, True})

    def test_catalog_assets_only_keep_domains_and_networks(self):
        assets = assets_from_values(
            [
                "3CX Live chat WordPress plugin",
                "Other Domains and Subdomains not specifically in scope",
                "https://portal.example.com/admin/login?source=scope",
                "https://portal.example.com/another/path",
                "https://apps.apple.com/us/app/example/id123",
                "https://play.google.com/store/apps/details?id=com.example.mobile",
                "https://github.com/example/project",
                "192.0.2.8",
                "192.0.2.0/24",
            ],
            SCOPE_IN,
        )

        self.assertEqual(
            [(asset.raw_value, asset.asset_type) for asset in assets],
            [
                ("192.0.2.8", "ip"),
                ("192.0.2.0/24", "cidr"),
            ],
        )

    def test_url_scope_preserves_path_case_and_port(self):
        kind, value, wildcard = classify_asset("https://EXAMPLE.com:443/API")
        self.assertEqual(value, "https://example.com/API")
        self.assertTrue(target_matches_asset("https://example.com/API", value, wildcard, kind))
        self.assertFalse(target_matches_asset("https://example.com/api", value, wildcard, kind))
        kind, value, wildcard = classify_asset("https://example.com:8443")
        self.assertEqual(kind, "url")
        self.assertFalse(target_matches_asset("https://example.com", value, wildcard, kind))

    def test_host_rules_match_url_targets_with_paths(self):
        self.assertTrue(target_matches_asset("https://api.example.com/API", "example.com", True, "domain"))
        self.assertFalse(target_matches_asset("https://example.com/API", "example.com", True, "domain"))

    def test_url_wildcard_does_not_cross_host_boundary_or_include_apex(self):
        kind, value, wildcard = classify_asset("https://*.example.com/api/*")
        self.assertTrue(target_matches_asset("https://api.example.com/api/users", value, wildcard, kind))
        for target in ("https://example.com/api/users", "https://evil.test/x.example.com/api/users",
                       "https://api.example.com/API/users"):
            self.assertFalse(target_matches_asset(target, value, wildcard, kind), target)

    def test_cidr_query_must_be_fully_contained(self):
        self.assertTrue(target_matches_asset("192.0.2.0/25", "192.0.2.0/24", False, "cidr"))
        self.assertFalse(target_matches_asset("192.0.2.0/23", "192.0.2.0/24", False, "cidr"))

    def test_credential_urls_and_invalid_ports_are_not_host_grants(self):
        for value in ("https://user@example.com", "https://example.com:bad", "https://example.com:99999"):
            self.assertEqual(classify_asset(value)[0], "other")

    def test_url_templates_are_preserved_as_other_assets(self):
        value = "https://[customer].example.com"
        self.assertEqual(classify_asset(value), ("other", value, False))
        self.assertEqual(strip_scheme_and_path(value), "")

    def test_targets_without_a_host_do_not_crash(self):
        for value in ("", "   ", "/", "/api/*", "https://"):
            with self.subTest(value=value):
                self.assertEqual(strip_scheme_and_path(value), "")
        self.assertEqual(classify_asset("/api/*"), ("other", "/api/*", False))

    def test_classifies_wildcard_url_path_without_discarding_the_path(self):
        asset_type, normalized, wildcard = classify_asset("https://*.example.com/path")
        self.assertEqual(asset_type, "url")
        self.assertEqual(normalized, "https://*.example.com/path")
        self.assertTrue(wildcard)

    def test_expands_simple_alternation(self):
        expanded = expand_simple_alternation("https://www.example.(com|net)")
        self.assertEqual(expanded, ["https://www.example.com", "https://www.example.net"])

    def test_preserves_play_store_app_identity(self):
        first = classify_asset("https://play.google.com/store/apps/details?id=com.example.one")
        second = classify_asset("https://play.google.com/store/apps/details?id=com.example.two&utm_source=test")
        self.assertEqual(first[0], "android_app")
        self.assertNotEqual(first[1], second[1])
        self.assertEqual(second[1], "https://play.google.com/store/apps/details?id=com.example.two")

    def test_classifies_reverse_dns_android_package(self):
        asset_type, normalized, wildcard = classify_asset("com.example.mobile")
        self.assertEqual(asset_type, "android_app")
        self.assertEqual(normalized, "com.example.mobile")
        self.assertFalse(wildcard)

    def test_asset_matching(self):
        assets = assets_from_values(["*.example.com"], SCOPE_IN)
        self.assertEqual(len(assets), 1)
        self.assertTrue(target_matches_asset("api.example.com", assets[0].normalized_value, assets[0].is_wildcard))
        self.assertTrue(target_matches_asset("*.example.com", assets[0].normalized_value, assets[0].is_wildcard))
        self.assertFalse(target_matches_asset("example.com", assets[0].normalized_value, assets[0].is_wildcard))
        self.assertFalse(target_matches_asset("api.example.net", assets[0].normalized_value, assets[0].is_wildcard))

    def test_url_path_matching_is_exact_and_ignores_query_strings(self):
        asset_type, normalized, wildcard = classify_asset("https://example.com/api/v1")

        self.assertEqual(asset_type, "url")
        self.assertTrue(
            target_matches_asset(
                "https://example.com/api/v1?trace=1#details",
                normalized,
                wildcard,
                asset_type,
            )
        )
        self.assertFalse(
            target_matches_asset(
                "https://example.com/api/v2",
                normalized,
                wildcard,
                asset_type,
            )
        )

    def test_wildcard_url_path_and_cidr_matching(self):
        asset_type, normalized, wildcard = classify_asset("https://example.com/api/*")
        self.assertEqual(asset_type, "url")
        self.assertTrue(wildcard)
        self.assertTrue(
            target_matches_asset(
                "https://example.com/api/accounts",
                normalized,
                wildcard,
                asset_type,
            )
        )
        self.assertFalse(
            target_matches_asset(
                "https://example.com/admin/accounts",
                normalized,
                wildcard,
                asset_type,
            )
        )

        self.assertTrue(target_matches_asset("192.0.2.42", "192.0.2.0/24", False, "cidr"))
        self.assertFalse(target_matches_asset("192.0.3.42", "192.0.2.0/24", False, "cidr"))
        self.assertFalse(target_matches_asset("not-an-ip", "192.0.2.0/24", False, "cidr"))


if __name__ == "__main__":
    unittest.main()
