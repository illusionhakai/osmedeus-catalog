"""Deterministic policy identities and lossless source snapshot aggregation."""

from dataclasses import asdict, fields, replace
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .models import Asset, Program
from .normalize import asset_identity


def yeswehack_policy_url(raw: dict[str, Any]) -> str | None:
    slug = raw.get("id")
    if isinstance(slug, str) and re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        return "https://yeswehack.com/programs/" + slug
    return None


def policy_identity(program: Program) -> str:
    """Match policies, not organizations or shared asset domains."""
    policy_url = program.program_url
    if not policy_url and program.source == "arkadiyt" and (program.platform or "").lower() == "yeswehack":
        policy_url = yeswehack_policy_url(program.raw)
    try:
        parsed = urlsplit(policy_url or "")
        parsed.port
    except ValueError:
        return f"source:{program.source}:{program.source_id}"
    if parsed.username is not None or parsed.password is not None:
        return f"source:{program.source}:{program.source_id}"
    if parsed.scheme.lower() in {"http", "https"} and parsed.hostname:
        host = parsed.hostname.lower().removeprefix("www.")
        path = parsed.path.rstrip("/")
        # These are alternate routes to the same platform engagement.
        if host in {"bugcrowd.com", "hackerone.com"}:
            parts = path.strip("/").split("/")
            if host == "bugcrowd.com" and parts[0] == "engagements":
                parts = parts[1:]
            if parts and parts[0] and (len(parts) == 1 or parts[1:] in (["policy"], ["scope"], ["updates"])):
                path = "/" + parts[0].lower()
                query = ""
            else:
                query = parsed.query
        else:
            query = parsed.query
        port = f":{parsed.port}" if parsed.port and parsed.port not in {80, 443} else ""
        return "url:" + urlunsplit(("https", host + port, path, query, ""))
    # An absent policy URL cannot establish that two similarly named programs
    # share permissions. Preserve that source's stable identity instead.
    return f"source:{program.source}:{program.source_id}"


def select_policy_url(programs: list[Program]) -> str | None:
    """Choose a usable current policy link in the existing metadata order."""
    for program in programs:
        identity = policy_identity(program)
        if not identity.startswith("url:"):
            continue
        canonical = identity.removeprefix("url:")
        host = urlsplit(canonical).hostname
        if host in {"hackerone.com", "bugcrowd.com", "yeswehack.com"}:
            return canonical
        # Custom policies can distinguish HTTP from HTTPS. Retain their URL.
        return program.program_url
    return None


def program_payload(program: Program) -> dict[str, Any]:
    return asdict(program)


def program_from_payload(payload: dict[str, Any]) -> Program:
    data = {field.name: payload[field.name] for field in fields(Program) if field.name in payload}
    data["assets"] = tuple(Asset(**{field.name: asset[field.name] for field in fields(Asset) if field.name in asset})
                           for asset in payload.get("assets", []))
    return Program(**data)


def merge_programs(programs: list[Program], *, source: str, source_id: str) -> Program:
    """Union current evidence, preserving explicit exclusions and raw snapshots elsewhere."""
    priority = {"manual": 0, "arkadiyt": 1, "rix-scope": 2, "chaos": 3}
    ordered = sorted(programs, key=lambda p: (p.status != "active", priority.get(p.source, 4), p.source, p.source_id))
    metadata = program_payload(ordered[0])
    for field in fields(Program):
        if field.name in {"assets", "raw", "source", "source_id"}:
            continue
        metadata[field.name] = next((getattr(p, field.name) for p in ordered
                                     if getattr(p, field.name) not in (None, "")), metadata[field.name])
    if metadata.get("platform"):
        metadata["platform"] = metadata["platform"].strip().lower()
    metadata["program_url"] = select_policy_url(ordered)
    assets: dict[tuple, Asset] = {}
    has_active_policy = any(p.status == "active" for p in ordered)
    for program in ordered:
        for asset in program.assets:
            # An archived/candidate observation must not contribute new grants
            # to an otherwise active policy. Its explicit exclusions still apply.
            if has_active_policy and program.status != "active" and asset.scope == "in_scope":
                continue
            key = asset_identity(asset)
            previous = assets.get(key)
            if previous is None:
                assets[key] = asset
            else:
                # Bounty eligibility does not override a negative observation.
                eligibility = False if False in (previous.eligible_for_bounty, asset.eligible_for_bounty) else (
                    previous.eligible_for_bounty if previous.eligible_for_bounty is not None else asset.eligible_for_bounty)
                assets[key] = replace(previous, eligible_for_bounty=eligibility,
                                      notes=previous.notes or asset.notes)
    metadata.update(source=source, source_id=source_id, assets=[asdict(a) for a in assets.values()])
    return program_from_payload(metadata)
