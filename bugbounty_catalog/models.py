from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


SCOPE_IN = "in_scope"
SCOPE_OUT = "out_of_scope"
SCOPE_CANDIDATE = "candidate"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass(frozen=True)
class Asset:
    scope: str
    raw_value: str
    asset_type: str = "other"
    normalized_value: str | None = None
    is_wildcard: bool = False
    eligible_for_bounty: bool | None = None
    confidence: float = 1.0
    notes: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Program:
    source: str
    source_id: str
    name: str
    organization: str | None = None
    platform: str | None = None
    program_url: str | None = None
    program_type: str = "bug_bounty"
    status: str = "active"
    offers_bounty: bool | None = None
    description: str | None = None
    reward: str | None = None
    min_bounty: float | None = None
    max_bounty: float | None = None
    safe_harbor: str | None = None
    allows_disclosure: bool | None = None
    last_updated: str | None = None
    assets: tuple[Asset, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict)
