from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable

from bugbounty_catalog.sources.arkadiyt import ArkadiytSource
from bugbounty_catalog.sources.base import Source
from bugbounty_catalog.sources.chaos import ChaosSource
from bugbounty_catalog.sources.rix_scope import RixScopeSource


SourceBuilder = Callable[[bool], Source]


_SOURCE_BUILDERS: OrderedDict[str, SourceBuilder] = OrderedDict(
    (
        ("rix-scope", lambda _include_vdp: RixScopeSource()),
        ("chaos", lambda include_vdp: ChaosSource(bounty_only=not include_vdp)),
        ("arkadiyt", lambda include_vdp: ArkadiytSource(bounty_only=not include_vdp)),
    )
)


def register_source(name: str, builder: SourceBuilder, *, replace: bool = False) -> None:
    """Register a source adapter without changing the service or presentations."""

    normalized = name.strip().lower()
    if not normalized:
        raise ValueError("source name is required")
    if normalized == "all":
        raise ValueError("'all' is reserved for selecting every source")
    if normalized in _SOURCE_BUILDERS and not replace:
        raise ValueError(f"Source already registered: {normalized}")
    _SOURCE_BUILDERS[normalized] = builder


def available_sources() -> tuple[str, ...]:
    return tuple(_SOURCE_BUILDERS)


def build_sources(name: str, *, include_vdp: bool = False) -> list[Source]:
    if name == "all":
        return [builder(include_vdp) for builder in _SOURCE_BUILDERS.values()]
    try:
        builder = _SOURCE_BUILDERS[name]
    except KeyError as exc:
        raise ValueError(f"Unknown source: {name}") from exc
    return [builder(include_vdp)]


__all__ = [
    "ArkadiytSource",
    "ChaosSource",
    "RixScopeSource",
    "Source",
    "available_sources",
    "build_sources",
    "register_source",
]
