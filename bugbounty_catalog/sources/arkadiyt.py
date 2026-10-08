from __future__ import annotations

from typing import Any, Iterable

from bugbounty_catalog.http import fetch_json
from bugbounty_catalog.models import Program, SCOPE_IN, SCOPE_OUT
from bugbounty_catalog.normalize import assets_from_values, stable_id
from bugbounty_catalog.sources.base import Source
from bugbounty_catalog.program_merge import yeswehack_policy_url


ARKADIYT_FILES = {
    "bugcrowd": "https://raw.githubusercontent.com/arkadiyt/bounty-targets-data/main/data/bugcrowd_data.json",
    "hackerone": "https://raw.githubusercontent.com/arkadiyt/bounty-targets-data/main/data/hackerone_data.json",
    "intigriti": "https://raw.githubusercontent.com/arkadiyt/bounty-targets-data/main/data/intigriti_data.json",
    "yeswehack": "https://raw.githubusercontent.com/arkadiyt/bounty-targets-data/main/data/yeswehack_data.json",
}


class ArkadiytSource(Source):
    name = "arkadiyt"

    def __init__(
        self,
        *,
        platforms: Iterable[str] | None = None,
        bounty_only: bool = True,
        timeout: float = 45.0,
    ) -> None:
        selected = tuple(platforms) if platforms else tuple(ARKADIYT_FILES)
        unknown = set(selected) - set(ARKADIYT_FILES)
        if unknown:
            raise ValueError(f"Unknown arkadiyt platform(s): {', '.join(sorted(unknown))}")
        self.platforms = selected
        self.bounty_only = bounty_only
        self.timeout = timeout

    def fetch(self) -> list[Program]:
        programs: list[Program] = []
        for platform in self.platforms:
            rows = fetch_json(ARKADIYT_FILES[platform], timeout=self.timeout)
            # All platform files form this provider's snapshot. Never retire an
            # entire platform because its file was empty or structurally invalid.
            if not isinstance(rows, list) or not rows or any(not isinstance(row, dict) for row in rows):
                raise ValueError(f"arkadiyt {platform} returned an empty or malformed dataset")
            for row in rows:
                if "targets" in row:
                    targets = row["targets"]
                    if not isinstance(targets, dict) or any(
                        key in targets and not isinstance(targets[key], list)
                        for key in ("in_scope", "out_of_scope")
                    ):
                        raise ValueError(f"arkadiyt {platform} returned malformed scope")
                parsed = self._parse_program(platform, row)
                if parsed is not None:
                    programs.append(parsed)
        return programs

    def _parse_program(self, platform: str, row: dict[str, Any]) -> Program | None:
        if self.bounty_only and not _has_bounty(platform, row):
            return None
        name = _string(row.get("name")) or _string(row.get("handle")) or "Unnamed program"
        program_url = _string(row.get("url") or row.get("program_url"))
        # Retain the historical source key while repairing the missing URL.
        source_id = stable_id(self.name, platform, program_url, name)
        if platform == "yeswehack" and not program_url:
            program_url = yeswehack_policy_url(row)
        in_scope, out_scope = _extract_targets(platform, row, bounty_only=self.bounty_only)
        assets = (
            assets_from_values(in_scope, SCOPE_IN, eligible_for_bounty=True if self.bounty_only else None)
            + assets_from_values(out_scope, SCOPE_OUT, eligible_for_bounty=False)
        )
        return Program(
            source=self.name,
            source_id=source_id,
            name=name,
            organization=_string(row.get("organization") or row.get("company") or name),
            platform=platform,
            program_url=program_url,
            min_bounty=_number(row.get("min_bounty")),
            max_bounty=_max_bounty(platform, row),
            safe_harbor=_string(row.get("safe_harbor")),
            allows_disclosure=_bool_or_none(row.get("allows_disclosure")),
            offers_bounty=_has_bounty(platform, row),
            status="archived" if row.get("archived") else "active",
            description=_string(row.get("description")),
            assets=assets,
            raw=row,
        )


def _extract_targets(
    platform: str,
    row: dict[str, Any],
    *,
    bounty_only: bool,
) -> tuple[list[Any], list[Any]]:
    targets = row.get("targets") if isinstance(row.get("targets"), dict) else {}
    in_rows = targets.get("in_scope", []) if isinstance(targets, dict) else []
    out_rows = targets.get("out_of_scope", []) if isinstance(targets, dict) else []
    return (
        _target_values(platform, in_rows, in_scope=True, bounty_only=bounty_only),
        _target_values(platform, out_rows, in_scope=False, bounty_only=False),
    )


def _target_values(platform: str, rows: Any, *, in_scope: bool, bounty_only: bool) -> list[Any]:
    values: list[Any] = []
    for target in rows if isinstance(rows, list) else []:
        if not isinstance(target, dict):
            values.append(target)
            continue
        if platform == "hackerone":
            value = target.get("asset_identifier") or target.get("target")
            if in_scope and bounty_only and value and target.get("eligible_for_bounty") is False:
                continue
        elif platform == "intigriti":
            value = target.get("endpoint") or target.get("target")
        else:
            value = target.get("target") or target.get("asset_identifier") or target.get("endpoint")
        if value:
            values.append(value)
    return values


def _has_bounty(platform: str, row: dict[str, Any]) -> bool:
    if platform == "intigriti":
        max_bounty = row.get("max_bounty")
        if isinstance(max_bounty, dict):
            return _amount_gt_zero(max_bounty.get("value"))
        return _amount_gt_zero(max_bounty)
    if platform in {"bugcrowd", "yeswehack"}:
        return _amount_gt_zero(row.get("max_payout") or row.get("max_bounty"))
    if platform == "hackerone":
        for target in _scope_rows(row):
            if isinstance(target, dict) and target.get("eligible_for_bounty"):
                return True
        return _amount_gt_zero(row.get("max_bounty") or row.get("max_payout"))
    return True


def _scope_rows(row: dict[str, Any]) -> list[Any]:
    targets = row.get("targets") if isinstance(row.get("targets"), dict) else {}
    in_scope = targets.get("in_scope", []) if isinstance(targets, dict) else []
    return in_scope if isinstance(in_scope, list) else []


def _max_bounty(platform: str, row: dict[str, Any]) -> float | None:
    if platform == "intigriti" and isinstance(row.get("max_bounty"), dict):
        return _number(row["max_bounty"].get("value"))
    return _number(row.get("max_bounty") or row.get("max_payout"))


def _amount_gt_zero(value: Any) -> bool:
    number = _number(value)
    return number is not None and number > 0


def _number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "").strip()
    digits = "".join(ch for ch in text if ch.isdigit() or ch == ".")
    if not digits:
        return None
    try:
        return float(digits)
    except ValueError:
        return None


def _bool_or_none(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    return None


def _string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
