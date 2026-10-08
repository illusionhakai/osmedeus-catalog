"""Stable query objects shared by every presentation layer.

The CLI, web API, and future integrations should express reads with these
objects rather than constructing SQL or depending on SQLite row objects.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Generic, Mapping, TypeVar


T = TypeVar("T")
MAX_PAGE_SIZE = 200


def _page_size(value: object, default: int = 50) -> int:
    try:
        parsed = int(value) if value is not None else default
    except (TypeError, ValueError) as exc:
        raise ValueError("limit must be an integer") from exc
    if parsed < 1:
        raise ValueError("limit must be at least 1")
    return min(parsed, MAX_PAGE_SIZE)


def _offset(value: object) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("offset must be an integer") from exc
    if parsed < 0:
        raise ValueError("offset cannot be negative")
    return parsed


def _optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _optional_bool(value: object, default: bool | None = None) -> bool | None:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    lowered = str(value).strip().lower()
    if lowered in {"1", "true", "yes", "active"}:
        return True
    if lowered in {"0", "false", "no", "inactive"}:
        return False
    if lowered in {"all", "any"}:
        return None
    raise ValueError(f"invalid boolean value: {value}")


@dataclass(frozen=True)
class ProgramQuery:
    """Filters and pagination for browsing programs."""

    text: str | None = None
    source: str | None = None
    platform: str | None = None
    status: str | None = None
    active: bool | None = True
    limit: int = 50
    offset: int = 0
    scope: str | None = None
    scope_text: str | None = None
    kind: str | None = None
    scannable_only: bool = False

    def __post_init__(self) -> None:
        if self.scope not in {None, "in_scope", "out_of_scope", "candidate"}:
            raise ValueError("invalid asset scope")
        if not 1 <= self.limit <= MAX_PAGE_SIZE:
            raise ValueError(f"limit must be between 1 and {MAX_PAGE_SIZE}")
        if self.offset < 0:
            raise ValueError("offset cannot be negative")

    @classmethod
    def from_mapping(cls, values: Mapping[str, object]) -> "ProgramQuery":
        return cls(
            text=_optional_text(values.get("q") or values.get("text")),
            source=_optional_text(values.get("source")),
            platform=_optional_text(values.get("platform")),
            status=_optional_text(values.get("status")),
            active=_optional_bool(values.get("active"), True),
            limit=_page_size(values.get("limit")),
            offset=_offset(values.get("offset")),
            scope=_optional_text(values.get("scope")),
            scope_text=_optional_text(values.get("scope_text")),
            kind=_optional_text(values.get("kind")),
            scannable_only=bool(_optional_bool(values.get("scannable_only"), False)),
        )


@dataclass(frozen=True)
class AssetQuery:
    """Filters and pagination for browsing normalized scope assets."""

    text: str | None = None
    program_id: int | None = None
    source: str | None = None
    scope: str | None = None
    kind: str | None = None
    active: bool | None = True
    limit: int = 50
    offset: int = 0
    target_text: str | None = None

    def __post_init__(self) -> None:
        if self.program_id is not None and self.program_id < 1:
            raise ValueError("program_id must be positive")
        if self.scope not in {None, "in_scope", "out_of_scope", "candidate"}:
            raise ValueError("invalid asset scope")
        if not 1 <= self.limit <= MAX_PAGE_SIZE:
            raise ValueError(f"limit must be between 1 and {MAX_PAGE_SIZE}")
        if self.offset < 0:
            raise ValueError("offset cannot be negative")

    @classmethod
    def from_mapping(cls, values: Mapping[str, object]) -> "AssetQuery":
        program_id = values.get("program_id")
        try:
            parsed_program_id = int(program_id) if program_id not in {None, ""} else None
        except (TypeError, ValueError) as exc:
            raise ValueError("program_id must be an integer") from exc
        if parsed_program_id is not None and parsed_program_id < 1:
            raise ValueError("program_id must be positive")
        return cls(
            text=_optional_text(values.get("q") or values.get("text")),
            program_id=parsed_program_id,
            source=_optional_text(values.get("source")),
            scope=_optional_text(values.get("scope")),
            kind=_optional_text(values.get("kind")),
            active=_optional_bool(values.get("active"), True),
            limit=_page_size(values.get("limit")),
            offset=_offset(values.get("offset")),
            target_text=_optional_text(values.get("target_text")),
        )


@dataclass(frozen=True)
class Page(Generic[T]):
    """Storage-neutral paginated result."""

    items: list[T]
    total: int
    limit: int
    offset: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": self.items,
            "total": self.total,
            "limit": self.limit,
            "offset": self.offset,
            "has_more": self.offset + len(self.items) < self.total,
        }
