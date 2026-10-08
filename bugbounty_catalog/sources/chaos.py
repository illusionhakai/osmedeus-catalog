from __future__ import annotations

from typing import Any

from bugbounty_catalog.http import fetch_json
from bugbounty_catalog.models import Program, SCOPE_IN
from bugbounty_catalog.normalize import assets_from_values, stable_id
from bugbounty_catalog.sources.base import Source


class ChaosSource(Source):
    name = "chaos"
    url = "https://raw.githubusercontent.com/projectdiscovery/public-bugbounty-programs/main/dist/data.json"

    def __init__(
        self,
        *,
        url: str | None = None,
        bounty_only: bool = True,
        timeout: float = 45.0,
    ) -> None:
        self.url = url or self.url
        self.bounty_only = bounty_only
        self.timeout = timeout

    def fetch(self) -> list[Program]:
        payload = fetch_json(self.url, timeout=self.timeout)
        if isinstance(payload, list):
            rows = payload
        elif isinstance(payload, dict):
            rows = payload.get("programs", [])
        else:
            rows = []
        if not isinstance(rows, list) or not rows or any(not isinstance(row, dict) for row in rows):
            raise ValueError("chaos returned an empty or malformed dataset")
        programs: list[Program] = []
        for row in rows:
            if "domains" in row and row["domains"] is not None and not isinstance(row["domains"], list):
                raise ValueError("chaos returned malformed domains")
            if self.bounty_only and not bool(row.get("bounty")):
                continue
            program_url = _string(row.get("url"))
            name = _string(row.get("name")) or program_url or "Unnamed Chaos program"
            assets = assets_from_values(
                row.get("domains"),
                SCOPE_IN,
                eligible_for_bounty=bool(row.get("bounty")) if row.get("bounty") is not None else None,
            )
            programs.append(
                Program(
                    source=self.name,
                    source_id=stable_id(self.name, program_url, name),
                    name=name,
                    organization=_string(row.get("organization") or row.get("company") or name),
                    platform=_platform_from_url(program_url),
                    program_url=program_url,
                    offers_bounty=_bool_or_none(row.get("bounty")),
                    description=_string(row.get("description")),
                    assets=assets,
                    raw=row,
                )
            )
        return programs


def _platform_from_url(url: str | None) -> str | None:
    if not url:
        return None
    lowered = url.lower()
    for platform in ("hackerone", "bugcrowd", "intigriti", "yeswehack", "hackenproof"):
        if platform in lowered:
            return platform
    return None


def _string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _bool_or_none(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None
