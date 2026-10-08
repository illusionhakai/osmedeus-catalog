from __future__ import annotations

from datetime import date
from typing import Any

from bugbounty_catalog.http import fetch_json
from bugbounty_catalog.models import Program, SCOPE_IN, SCOPE_OUT
from bugbounty_catalog.normalize import assets_from_values, stable_id
from bugbounty_catalog.sources.base import Source


class RixScopeSource(Source):
    name = "rix-scope"
    url = "https://raw.githubusercontent.com/rix4uni/scope/refs/heads/main/programs.json"

    def __init__(self, *, url: str | None = None, timeout: float = 45.0) -> None:
        self.url = url or self.url
        self.timeout = timeout

    def fetch(self) -> list[Program]:
        rows = fetch_json(self.url, timeout=self.timeout)
        if not isinstance(rows, list) or not rows or any(not isinstance(row, dict) for row in rows):
            raise ValueError("rix-scope returned an empty or malformed dataset")
        programs: list[Program] = []
        for row in rows:
            for key in ("inscope_domains", "outofscope_domains"):
                if key in row and row[key] is not None and not isinstance(row[key], list):
                    raise ValueError(f"rix-scope returned malformed {key}")
            program_url = _string(row.get("program_url") or row.get("url"))
            name = _string(row.get("name")) or program_url or "Unnamed program"
            assets = (
                assets_from_values(row.get("inscope_domains"), SCOPE_IN)
                + assets_from_values(row.get("outofscope_domains"), SCOPE_OUT)
            )
            if not _string(row.get("name")) and not assets:
                # Anonymous submission links are not detailed program records.
                continue
            reward = _string(row.get("reward"))
            programs.append(
                Program(
                    source=self.name,
                    source_id=stable_id(self.name, program_url, name),
                    name=name,
                    organization=_string(row.get("organization") or row.get("company") or name),
                    platform=_string(row.get("platform")),
                    program_url=program_url,
                    reward=reward,
                    offers_bounty=True if reward else None,
                    status=_string(row.get("status")) or "active",
                    description=_string(row.get("description")),
                    last_updated=_string(row.get("last_updated")),
                    assets=assets,
                    raw=row,
                )
            )
        # The feed can publish multiple snapshots of the same program.
        # Keep the newest whole snapshot so stale scope is not reintroduced.
        unique: dict[str, Program] = {}
        for program in programs:
            previous = unique.get(program.source_id)
            if previous is None or _updated_date(program) > _updated_date(previous):
                unique[program.source_id] = program
        return list(unique.values())


def _updated_date(program: Program) -> date:
    try:
        return date.fromisoformat(program.last_updated or "")
    except ValueError:
        return date.min


def _string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return None if text.lower() in {"", "-", "--", "n/a", "none", "null", "unknown"} else text
