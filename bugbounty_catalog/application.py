"""Application facade for catalog reads and operations.

Presentation code depends on :class:`CatalogService`, not on SQLite, source
adapters, or crawler implementation details. A future GUI, API, scheduler, or
alternative repository can therefore reuse the same use cases.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, ContextManager, Protocol
from urllib.parse import urlsplit
from uuid import uuid4

from .db import DEFAULT_DB_PATH, CatalogDB
from .normalize import assets_from_values, classify_asset, clean_target, sanitize_catalog_target
from .models import Program, SCOPE_IN, SCOPE_OUT
from .query import AssetQuery, Page, ProgramQuery
from .sources import available_sources, build_sources


EventSink = Callable[[str, str, dict[str, Any]], None]


class CatalogStore(Protocol):
    """The persistence operations required by the application layer."""

    def init_schema(self) -> None: ...
    def stats(self) -> dict[str, Any]: ...
    def facets(self) -> dict[str, list[dict[str, Any]]]: ...
    def diagnostics(self) -> dict[str, Any]: ...
    def search_target(
        self, target: str, *, include_inactive: bool = False
    ) -> dict[str, list[dict[str, Any]]]: ...
    def list_programs(self, query: ProgramQuery): ...
    def get_program(self, program_id: int) -> dict[str, Any] | None: ...
    def add_manual_program(self, program: Program) -> dict[str, Any]: ...
    def list_recon_programs(self, query: ProgramQuery) -> dict[str, Any]: ...
    def list_assets(self, query: AssetQuery): ...
    def recent_source_runs(self, *, limit: int = 20) -> list[dict[str, Any]]: ...
    def recent_events(self, *, limit: int = 30) -> list[dict[str, Any]]: ...
    def start_run(self, source: str) -> int: ...
    def finish_run(
        self, run_id: int, *, status: str, records_seen: int = 0, error: str | None = None
    ) -> None: ...
    def upsert_programs(self, programs: Any, *, source_run_id: int | None = None) -> int: ...
    def apply_source_snapshot(self, source: str, programs: Any, *, source_run_id: int) -> int: ...
    def finalize_source_snapshot(
        self, source: str, seen_source_ids: set[str], *, source_run_id: int
    ) -> None: ...
    def export_programs(self, *, include_inactive: bool = False) -> Any: ...
    def export_assets(
        self,
        *,
        kind: str | None = None,
        scope: str = "in_scope",
        include_inactive: bool = False,
    ) -> Any: ...


class StoreFactory(Protocol):
    def __call__(self) -> ContextManager[CatalogStore]: ...


class CatalogService:
    """Storage-neutral entry point used by the web application."""

    def __init__(
        self,
        db_path: str | Path = DEFAULT_DB_PATH,
        *,
        store_factory: StoreFactory | None = None,
        expose_recon_actions: bool = False,
        source_factory: Callable[..., list[Any]] = build_sources,
    ) -> None:
        self.db_path = Path(db_path)
        self._expose_recon_actions = expose_recon_actions
        self._store_factory: StoreFactory = store_factory or (lambda: CatalogDB(self.db_path))
        self._source_factory = source_factory

    def initialize(self) -> dict[str, Any]:
        with self._store_factory() as store:
            store.init_schema()
            return store.diagnostics()

    def overview(self) -> dict[str, Any]:
        with self._ready_store() as store:
            return {
                "stats": store.stats(),
                "facets": store.facets(),
                "runs": store.recent_source_runs(limit=10),
                "events": store.recent_events(limit=12),
                "diagnostics": store.diagnostics(),
                "capabilities": {
                    "sources": list(available_sources()),
                    "asset_scopes": ["in_scope", "out_of_scope", "candidate"],
                },
            }

    def stats(self) -> dict[str, Any]:
        with self._ready_store() as store:
            return store.stats()

    def diagnostics(self) -> dict[str, Any]:
        with self._ready_store() as store:
            return store.diagnostics()

    def search(
        self, target: str, *, include_inactive: bool = False, program_id: int | None = None
    ) -> dict[str, Any]:
        cleaned = target.strip()
        if not cleaned:
            raise ValueError("target is required")
        with self._ready_store() as store:
            if program_id is not None:
                if program_id < 1:
                    raise ValueError("program_id must be positive")
                program = store.get_program(program_id)
                if program is None:
                    raise ValueError("Program not found")
                program_id = program["id"]
            groups = store.search_target(cleaned, include_inactive=include_inactive)
        if program_id is not None:
            groups = {
                key: [row for row in rows if row["program_id"] == program_id]
                for key, rows in groups.items()
            }
        asset_type, normalized, is_wildcard = classify_asset(cleaned)
        conflict = bool(groups["allowed"] and groups["excluded"])
        warnings: list[str] = []
        if groups["excluded"]:
            warnings.append("One or more explicit exclusions match this target.")
        if conflict:
            warnings.append(
                "The catalog contains both allowed and excluded matches; verify the current program rules manually."
            )
        if groups["candidate"]:
            warnings.append(
                "Candidate matches are evidence only and do not establish authorization."
            )
        if not any(groups.values()):
            warnings.append(
                "No local match was found; absence from the catalog does not establish scope."
            )
        return {
            "target": cleaned,
            "normalized_target": normalized,
            "target_type": asset_type,
            "target_is_wildcard": is_wildcard,
            "allowed": groups["allowed"],
            "excluded": groups["excluded"],
            "candidate": groups["candidate"],
            "conflict": conflict,
            "scope_status": "out_of_scope"
            if groups["excluded"]
            else (
                "in_scope"
                if groups["allowed"]
                else "candidate"
                if groups["candidate"]
                else "unknown"
            ),
            "program_id": program_id,
            "warnings": warnings,
        }

    def programs(self, query: ProgramQuery | None = None) -> dict[str, Any]:
        selected = query or ProgramQuery()
        with self._ready_store() as store:
            return store.list_programs(selected).to_dict()

    def recon_programs(
        self, query: ProgramQuery, *, operation: str = "discovery"
    ) -> dict[str, Any]:
        with self._ready_store() as store:
            return store.list_recon_programs(query, operation=operation)

    def program(self, program_id: int) -> dict[str, Any] | None:
        if program_id < 1:
            raise ValueError("program_id must be positive")
        with self._ready_store() as store:
            program = store.get_program(program_id)
        if program is not None:
            program["policy_links"] = _program_policy_links(program)
            program["actions"] = (
                _program_recon_actions(program) if self._expose_recon_actions else []
            )
        return program

    def assets(self, query: AssetQuery | None = None) -> dict[str, Any]:
        selected = query or AssetQuery()
        with self._ready_store() as store:
            return store.list_assets(selected).to_dict()

    def add_program(
        self,
        *,
        name: str,
        in_scope: tuple[str, ...],
        out_of_scope: tuple[str, ...] = (),
        program_url: str = "",
    ) -> dict[str, Any]:
        name = name.strip()
        if not name or len(name) > 200:
            raise ValueError("program name must contain 1 to 200 characters")
        url = program_url.strip()
        if url:
            parsed = urlsplit(url)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
            ):
                raise ValueError("program_url must be an HTTP(S) policy URL without credentials")
            url = (
                parsed._replace(
                    scheme=parsed.scheme.lower(), netloc=parsed.netloc.lower(), fragment=""
                )
                .geturl()
                .rstrip("/")
            )
        if not in_scope or len(in_scope) + len(out_of_scope) > 500:
            raise ValueError("provide in-scope assets and at most 500 total scope entries")
        unsupported = [
            clean_target(value)
            for value in (*in_scope, *out_of_scope)
            if clean_target(value) and sanitize_catalog_target(value) is None
        ]
        if unsupported:
            raise ValueError(
                f"Unsupported catalog target: {unsupported[0]}; enter a hostname, wildcard, "
                "IP address or CIDR"
            )
        assets = assets_from_values(in_scope, SCOPE_IN) + assets_from_values(
            out_of_scope, SCOPE_OUT
        )
        if not any(asset.scope == SCOPE_IN for asset in assets):
            raise ValueError("at least one in-scope asset is required")
        program = Program(
            source="manual",
            source_id=uuid4().hex,
            name=name,
            program_url=url or None,
            assets=assets,
            raw={"origin": "dashboard", "scope_provided_by": "user"},
        )
        with self._ready_store() as store:
            result = store.add_manual_program(program)
        result["program"]["policy_links"] = _program_policy_links(result["program"])
        result["program"]["actions"] = (
            _program_recon_actions(result["program"]) if self._expose_recon_actions else []
        )
        return result

    def runs(self, *, limit: int = 20) -> list[dict[str, Any]]:
        with self._ready_store() as store:
            return store.recent_source_runs(limit=limit)

    def events(self, *, limit: int = 30) -> list[dict[str, Any]]:
        with self._ready_store() as store:
            return store.recent_events(limit=limit)

    def refresh(
        self,
        source_name: str = "all",
        *,
        include_vdp: bool = False,
        on_event: EventSink | None = None,
    ) -> dict[str, Any]:
        emit = on_event or _discard_event
        sources = self._source_factory(source_name, include_vdp=include_vdp)
        if not sources:
            raise ValueError(f"no sources selected for {source_name!r}")
        results: list[dict[str, Any]] = []
        for source in sources:
            emit("info", f"Fetching {source.name}", {"source": source.name})
            with self._ready_store() as store:
                run_id = store.start_run(source.name)
                try:
                    programs = source.collect_programs()
                    if not isinstance(programs, list):
                        raise ValueError(f"{source.name} returned an invalid payload")
                    if not programs:
                        raise ValueError(
                            f"{source.name} returned no programs; refusing to apply an empty snapshot"
                        )
                    _validate_source_programs(source.name, programs)
                    apply_snapshot = getattr(store, "apply_source_snapshot", None)
                    if apply_snapshot is not None:
                        count = apply_snapshot(
                            source.name,
                            programs,
                            source_run_id=run_id,
                        )
                    else:
                        # Compatibility for external stores implementing the
                        # pre-0.2 mutation contract.
                        count = store.upsert_programs(programs, source_run_id=run_id)
                        store.finalize_source_snapshot(
                            source.name,
                            {program.source_id for program in programs},
                            source_run_id=run_id,
                        )
                        store.finish_run(run_id, status="completed", records_seen=count)
                except Exception as exc:
                    error = _record_failed_run(store, run_id, exc)
                    results.append(
                        {"source": source.name, "status": "failed", "records": 0, "error": error}
                    )
                    emit("error", f"{source.name} failed: {error}", {"source": source.name})
                    continue
            results.append(
                {"source": source.name, "status": "completed", "records": count, "error": None}
            )
            emit(
                "success",
                f"Stored {count} programs from {source.name}",
                {"source": source.name, "records": count},
            )

        failures = sum(item["status"] == "failed" for item in results)
        status = (
            "completed" if failures == 0 else ("failed" if failures == len(results) else "partial")
        )
        return {
            "status": status,
            "requested_source": source_name,
            "include_vdp": include_vdp,
            "sources": results,
            "records": sum(item["records"] for item in results),
        }

    def export_rows(
        self,
        table: str,
        *,
        kind: str | None = None,
        scope: str = "in_scope",
        include_inactive: bool = False,
        query: AssetQuery | ProgramQuery | None = None,
    ) -> list[dict[str, Any]]:
        with self._ready_store() as store:
            if query is not None:
                if (
                    (table == "assets" and not isinstance(query, AssetQuery))
                    or (table == "programs" and not isinstance(query, ProgramQuery))
                    or table not in {"assets", "programs"}
                ):
                    raise ValueError("export query must match the selected table")
                result: list[dict[str, Any]] = []
                selected = replace(query, limit=200, offset=0)
                while True:
                    page = (
                        store.list_assets(selected)
                        if isinstance(selected, AssetQuery)
                        else store.list_programs(selected)
                    )
                    result.extend(dict(row) for row in page.items)
                    if not page.items or selected.offset + len(page.items) >= page.total:
                        if table == "programs":
                            identities = {(row["source"], row["source_id"]) for row in result}
                            return [
                                dict(row)
                                for row in store.export_programs(
                                    include_inactive=query.active is not True
                                )
                                if (row["source"], row["source_id"]) in identities
                            ]
                        return result
                    selected = replace(selected, offset=selected.offset + selected.limit)
            if table == "programs":
                rows = store.export_programs(include_inactive=include_inactive)
            elif table == "assets":
                rows = store.export_assets(
                    kind=kind, scope=scope, include_inactive=include_inactive
                )
            else:
                raise ValueError(f"unknown export table: {table}")
            return [dict(row) for row in rows]

    def _ready_store(self) -> ContextManager[CatalogStore]:
        return _InitializedStore(self._store_factory())


class _InitializedStore:
    """Small unit-of-work wrapper that guarantees a migrated store."""

    def __init__(self, context: ContextManager[CatalogStore]) -> None:
        self._context = context
        self._store: CatalogStore | None = None

    def __enter__(self) -> CatalogStore:
        self._store = self._context.__enter__()
        self._store.init_schema()
        return self._store

    def __exit__(self, *args: object) -> object:
        return self._context.__exit__(*args)


def _program_recon_actions(program: dict[str, Any]) -> list[str]:
    # Companion catalogs gather scope only; scanners are owned by Osmedeus.
    return []


def _program_policy_links(program: dict[str, Any]) -> list[dict[str, Any]]:
    links: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(value: object, source: str, active: bool) -> None:
        if not isinstance(value, str):
            return
        try:
            parsed = urlsplit(value.strip())
            parsed.port
        except ValueError:
            return
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            return
        url = parsed._replace(
            scheme=parsed.scheme.lower(), netloc=parsed.netloc.lower(), fragment=""
        ).geturl().rstrip("/")
        if url in seen:
            return
        seen.add(url)
        links.append({"url": url, "source": source, "is_active": active})

    add(program.get("program_url"), "current", bool(program.get("is_active", True)))
    for record in program.get("source_records") or []:
        add(
            record.get("program_url"),
            str(record.get("source") or "provider"),
            bool(record.get("is_active")),
        )
    return links


def _discard_event(_level: str, _message: str, _details: dict[str, Any]) -> None:
    return None


def _record_failed_run(store: CatalogStore, run_id: int, cause: Exception) -> str:
    """Best-effort failure recording without hiding the operation's root cause."""

    error = str(cause) or cause.__class__.__name__
    try:
        store.finish_run(run_id, status="failed", records_seen=0, error=error)
    except Exception as record_error:
        detail = str(record_error) or record_error.__class__.__name__
        return f"{error} (could not record failed run: {detail})"
    return error


def _validate_source_programs(source_name: str, programs: list[Any]) -> None:
    seen_ids: set[str] = set()
    for program in programs:
        if getattr(program, "source", None) != source_name:
            raise ValueError(f"{source_name} returned a program for a different source")
        source_id = getattr(program, "source_id", None)
        if not isinstance(source_id, str) or not source_id.strip():
            raise ValueError(f"{source_name} returned a program without a source id")
        if source_id in seen_ids:
            raise ValueError(f"{source_name} returned duplicate program id: {source_id}")
        seen_ids.add(source_id)
