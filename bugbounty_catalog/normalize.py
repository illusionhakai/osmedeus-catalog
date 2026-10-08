from __future__ import annotations

import hashlib
import fnmatch
import ipaddress
import re
from dataclasses import replace
from typing import Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .models import Asset, SCOPE_OUT


DOMAIN_RE = re.compile(
    r"(?i)^(?:\*\.)?(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}\.?$"
)
HOST_CANDIDATE_RE = re.compile(
    r"(?i)(?:\*\.)?(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}"
)
GITHUB_RE = re.compile(r"(?i)^https?://(?:www\.)?github\.com/[^/\s]+/[^/\s#?]+")
MOBILE_STORE_RE = re.compile(
    r"(?i)^https?://(?:apps\.apple\.com|play\.google\.com)/"
)
ANDROID_PACKAGE_RE = re.compile(r"^[a-zA-Z][\w]*(?:\.[a-zA-Z][\w]*){2,}$")
ANDROID_PACKAGE_PREFIXES = {"com", "org", "net", "io", "co", "app", "dev"}
CATALOG_ASSET_TYPES = frozenset({"domain", "ip", "cidr"})


def stable_id(*parts: str | None, length: int = 20) -> str:
    joined = "|".join(part or "" for part in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:length]


def clean_target(value: object) -> str:
    text = str(value or "").strip()
    text = text.strip("`'\" \t\r\n")
    text = re.sub(r"^\s*[-*]\s+", "", text)
    text = text.replace("\\/", "/")
    return text.strip()


def normalize_url(value: str) -> str:
    parts = urlsplit(value)
    scheme = parts.scheme.lower() or "https"
    netloc = parts.netloc.lower()
    if parts.hostname and not parts.username and not parts.password:
        host = parts.hostname.lower().rstrip(".")
        if ":" in host:
            host = f"[{host}]"
        port = parts.port
        netloc = host + (
            f":{port}" if port is not None and (scheme, port) not in {("http", 80), ("https", 443)}
            else ""
        )
    # Preserve endpoint boundaries; only a root slash is interchangeable with no path.
    path = "" if parts.path == "/" else parts.path
    return urlunsplit((scheme, netloc, path, "", ""))


def normalize_mobile_store_url(value: str) -> str:
    """Normalize an app URL while retaining its store identity."""

    normalized = normalize_url(value)
    parts = urlsplit(value)
    if parts.netloc.lower() == "play.google.com":
        app_ids = [(key, val) for key, val in parse_qsl(parts.query) if key == "id" and val]
        if app_ids:
            base = urlsplit(normalized)
            return urlunsplit((base.scheme, base.netloc, base.path, urlencode(app_ids[:1]), ""))
    return normalized


def strip_scheme_and_path(value: str) -> str:
    text = clean_target(value)
    if re.match(r"(?i)^https?://", text):
        try:
            parts = urlsplit(text)
        except ValueError:
            return ""
        text = parts.hostname or ""
    else:
        text = text.split("/")[0]
    tokens = text.split()
    if not tokens:
        return ""
    text = tokens[0].strip().strip(".").lower()
    if text.startswith("*."):
        text = text[2:]
    elif text.startswith("*"):
        text = text[1:]
    return text.strip(".")


def looks_like_domain(value: str) -> bool:
    return bool(DOMAIN_RE.match(value.strip().lower()))


def extract_domain_candidates(text: str) -> list[str]:
    seen: set[str] = set()
    domains: list[str] = []
    for match in HOST_CANDIDATE_RE.finditer(text or ""):
        domain = strip_scheme_and_path(match.group(0))
        if domain and domain not in seen and looks_like_domain(domain):
            seen.add(domain)
            domains.append(domain)
    return domains


def expand_simple_alternation(value: str, limit: int = 50) -> list[str]:
    text = clean_target(value)
    match = re.search(r"\(([^()]*\|[^()]*)\)", text)
    if not match:
        return [text]
    options = [option.strip() for option in match.group(1).split("|") if option.strip()]
    if not options or len(options) > limit:
        return [text]
    expanded = [text[: match.start()] + option + text[match.end() :] for option in options]
    return expanded[:limit]


def classify_asset(value: object) -> tuple[str, str | None, bool]:
    raw = clean_target(value)
    lowered = raw.lower()

    if not raw:
        return "other", None, False

    if re.match(r"(?i)^https?://", raw):
        try:
            parts = urlsplit(raw)
            parts.port
            if not parts.hostname or parts.username or parts.password:
                return "other", raw, "*" in raw
        except ValueError:
            # Scope feeds can contain URL templates rather than valid URLs.
            return "other", raw, "*" in raw

    try:
        network = ipaddress.ip_network(raw, strict=False)
        return ("cidr" if "/" in raw else "ip"), str(network if "/" in raw else network.network_address), False
    except ValueError:
        pass

    if GITHUB_RE.match(raw):
        return "github_repo", normalize_url(raw), False

    if MOBILE_STORE_RE.match(raw):
        if "play.google.com" in lowered:
            return "android_app", normalize_mobile_store_url(raw), False
        return "ios_app", normalize_mobile_store_url(raw), False

    first_segment = raw.split(".", 1)[0].lower()
    if ANDROID_PACKAGE_RE.match(raw) and (
        not looks_like_domain(raw) or first_segment in ANDROID_PACKAGE_PREFIXES
    ):
        return "android_app", raw, False

    if re.match(r"(?i)^https?://", raw):
        host = strip_scheme_and_path(raw)
        parts = urlsplit(raw)
        host_wildcard = host.startswith("*.") or "://*." in lowered
        has_path_scope = bool(parts.path and parts.path != "/")
        default_port = 80 if parts.scheme.lower() == "http" else 443
        if looks_like_domain(host) and not has_path_scope and parts.port in {None, default_port}:
            return "website", host, host_wildcard
        return "url", normalize_url(raw), host_wildcard or "*" in parts.path

    wildcard = raw.startswith("*.")
    domain = strip_scheme_and_path(raw)
    if (looks_like_domain(domain) and ("*" not in raw or wildcard)
            and "/" not in raw and not re.search(r"\s", raw)):
        return "domain", domain, wildcard

    return "other", raw, wildcard


def sanitize_catalog_target(value: object) -> tuple[str, str, str, bool] | None:
    """Return a canonical catalog target, or reject unsupported scope data.

    Catalog scope is deliberately limited to exact domains, leading-wildcard
    domains, IP addresses and CIDRs. In particular, URLs are not converted to
    host grants: doing so could widen a path- or port-specific authorization.
    """

    raw = clean_target(value)
    if not raw:
        return None
    asset_type, normalized, is_wildcard = classify_asset(raw)
    if asset_type not in CATALOG_ASSET_TYPES or not normalized:
        return None
    canonical = normalized
    if asset_type == "domain" and is_wildcard:
        canonical = "*." + normalized.removeprefix("*.")
    return canonical, asset_type, normalized, is_wildcard


def sanitize_catalog_assets(assets: Iterable[Asset]) -> tuple[Asset, ...]:
    """Canonicalize and deduplicate the actionable catalog scope."""

    sanitized: list[Asset] = []
    seen: set[tuple[str, str, str, bool]] = set()
    for asset in assets:
        target = sanitize_catalog_target(asset.raw_value)
        if target is None:
            continue
        raw, asset_type, normalized, is_wildcard = target
        cleaned = replace(
            asset,
            raw_value=raw,
            asset_type=asset_type,
            normalized_value=normalized,
            is_wildcard=is_wildcard,
        )
        key = asset_identity(cleaned)
        if key in seen:
            continue
        seen.add(key)
        sanitized.append(cleaned)
    return tuple(sanitized)


def assets_from_values(
    values: list[object] | tuple[object, ...] | None,
    scope: str,
    *,
    eligible_for_bounty: bool | None = None,
    confidence: float = 1.0,
) -> tuple[Asset, ...]:
    if not values:
        return ()

    assets: list[Asset] = []
    for value in values:
        for expanded in expand_simple_alternation(clean_target(value)):
            raw = clean_target(expanded)
            if not raw:
                continue
            assets.append(
                Asset(
                    scope=scope,
                    raw_value=raw,
                    eligible_for_bounty=eligible_for_bounty,
                    confidence=confidence,
                )
            )
    return sanitize_catalog_assets(assets)


def asset_identity(asset: Asset) -> tuple[str, str, str, bool]:
    """Identify a recon target without collapsing its scope or wildcard."""
    kind = "host" if asset.asset_type in {"domain", "website"} else asset.asset_type
    value = asset.normalized_value or asset.raw_value
    if kind == "host":
        value = value.lower().rstrip(".")
    return asset.scope, kind, value, asset.is_wildcard


def asset_target_value(asset: dict) -> str:
    """Format a normalized target while retaining a host wildcard."""
    value = str(asset.get("normalized_value") or asset.get("raw_value") or "").strip()
    if (value and asset.get("is_wildcard") and asset.get("asset_type") in {"domain", "website"}
            and not value.startswith("*.")):
        value = "*." + value
    return value


def target_matches_asset(
    target: str,
    normalized_value: str | None,
    is_wildcard: bool,
    asset_type: str | None = None,
    *,
    scope: str | None = None,
) -> bool:
    _target_type, target_norm, target_is_wildcard = classify_asset(target)
    if not target_norm:
        target_norm = strip_scheme_and_path(target)

    return normalized_target_matches_asset(
        target_norm,
        target_is_wildcard,
        normalized_value,
        is_wildcard,
        asset_type,
        scope=scope,
    )


def normalized_target_matches_asset(
    target_norm: str,
    target_is_wildcard: bool,
    normalized_value: str | None,
    is_wildcard: bool,
    asset_type: str | None = None,
    *,
    scope: str | None = None,
) -> bool:
    """Match an already classified target against one stored asset.

    Repository searches classify the submitted target once and reuse the result
    for every candidate row. ``target_matches_asset`` remains the convenient
    public entry point for callers matching a single asset.
    """

    if not normalized_value:
        return False

    if target_norm.startswith(("http://", "https://")):
        try:
            parsed_target = urlsplit(target_norm)
            parsed_target.port
            if not parsed_target.hostname or parsed_target.username or parsed_target.password:
                return False
        except ValueError:
            return False
        if asset_type in {None, "domain", "website", "ip", "cidr"}:
            target_norm = parsed_target.hostname

    candidate = normalized_value

    if asset_type == "cidr":
        try:
            network = ipaddress.ip_network(candidate, strict=False)
            if "/" in target_norm:
                submitted = ipaddress.ip_network(target_norm, strict=False)
                return submitted.overlaps(network) if scope == SCOPE_OUT else submitted.subnet_of(network)
            return ipaddress.ip_address(target_norm) in network
        except (ValueError, TypeError):
            return False

    if asset_type in {"url", "github_repo", "android_app", "ios_app", "ip"}:
        if asset_type == "ip" and scope == SCOPE_OUT and "/" in target_norm:
            try:
                return ipaddress.ip_address(candidate) in ipaddress.ip_network(target_norm, strict=False)
            except ValueError:
                return False
        if asset_type == "url" and is_wildcard:
            try:
                target_url, rule_url = urlsplit(target_norm), urlsplit(candidate)
                target_port, rule_port = target_url.port, rule_url.port
            except ValueError:
                return False
            if target_url.scheme != rule_url.scheme or target_port != rule_port:
                return False
            target_host, rule_host = target_url.hostname or "", rule_url.hostname or ""
            if rule_host.startswith("*."):
                if not target_host.endswith(rule_host[1:]) or target_host == rule_host[2:]:
                    return False
            elif target_host != rule_host:
                return False
            return fnmatch.fnmatchcase(target_url.path, rule_url.path)
        return target_norm == candidate

    target_norm = target_norm.lower().strip(".")
    candidate = candidate.lower().strip(".")

    if is_wildcard:
        if target_is_wildcard:
            return target_norm == candidate
        return target_norm.endswith("." + candidate)

    if target_norm == candidate:
        return True

    return False
