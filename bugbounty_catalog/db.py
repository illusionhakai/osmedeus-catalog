from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterable

from .models import Program, SCOPE_IN, SCOPE_OUT, utc_now_iso
from .normalize import (
    assets_from_values,
    classify_asset,
    normalized_target_matches_asset,
    sanitize_catalog_assets,
    strip_scheme_and_path,
)
from .query import AssetQuery, Page, ProgramQuery
from .program_merge import merge_programs, policy_identity, program_from_payload, program_payload, yeswehack_policy_url


DEFAULT_DB_PATH = Path("data/catalog.sqlite")
SCHEMA_VERSION = 6


def _scope_blocked(target, target_wildcard, excluded, excluded_wildcard, excluded_type):
    """Use export-equivalent exclusion matching without materializing whole programs."""
    if not target or not excluded:
        return False
    if target_wildcard and excluded_wildcard and excluded_type == "domain":
        return target == excluded or target.endswith("." + excluded)
    return normalized_target_matches_asset(target, bool(target_wildcard), excluded,
        bool(excluded_wildcard), excluded_type, scope=SCOPE_OUT)


def _renormalized_program(program: Program) -> Program:
    return replace(program, assets=sanitize_catalog_assets(program.assets))


def _recover_provider_scope(program: Program) -> Program:
    """Rebuild scope from retained provider evidence before sanitizing it.

    Version 5 reduced URL-shaped scope entries to hosts. Re-reading the
    original provider row lets version 6 reject those entries instead of
    retaining an accidentally broadened permission. Manual records have no
    provider scope payload, so their existing canonical values remain the
    only recoverable evidence.
    """

    raw = program.raw
    if not isinstance(raw, dict):
        return program
    if program.source == "arkadiyt" and isinstance(raw.get("targets"), dict):
        from .sources.arkadiyt import _extract_targets

        in_scope, out_scope = _extract_targets(
            program.platform or "", raw, bounty_only=bool(program.offers_bounty)
        )
        assets = assets_from_values(
            in_scope,
            SCOPE_IN,
            eligible_for_bounty=True if program.offers_bounty else None,
        ) + assets_from_values(out_scope, SCOPE_OUT, eligible_for_bounty=False)
        return replace(program, assets=assets)
    if program.source == "rix-scope" and (
        "inscope_domains" in raw or "outofscope_domains" in raw
    ):
        assets = assets_from_values(raw.get("inscope_domains"), SCOPE_IN) + assets_from_values(
            raw.get("outofscope_domains"), SCOPE_OUT
        )
        return replace(program, assets=assets)
    if program.source == "chaos" and "domains" in raw:
        eligible = bool(raw.get("bounty")) if raw.get("bounty") is not None else None
        return replace(
            program,
            assets=assets_from_values(
                raw.get("domains"), SCOPE_IN, eligible_for_bounty=eligible
            ),
        )
    return program


class CatalogDB:
    def __init__(self, path: str | Path = DEFAULT_DB_PATH) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=30.0)
        self.conn.row_factory = sqlite3.Row
        self.conn.create_function("catalog_scope_blocked", 5, _scope_blocked, deterministic=True)
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "CatalogDB":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def init_schema(self) -> None:
        if self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'").fetchone():
            version = self.conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError("Catalog database is newer than this application")
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS source_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                status TEXT NOT NULL,
                records_seen INTEGER NOT NULL DEFAULT 0,
                error TEXT
            );

            CREATE TABLE IF NOT EXISTS programs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                source_id TEXT NOT NULL,
                name TEXT NOT NULL,
                organization TEXT,
                platform TEXT,
                program_url TEXT,
                program_type TEXT NOT NULL DEFAULT 'bug_bounty',
                status TEXT NOT NULL DEFAULT 'active',
                offers_bounty INTEGER,
                description TEXT,
                reward TEXT,
                min_bounty REAL,
                max_bounty REAL,
                safe_harbor TEXT,
                allows_disclosure INTEGER,
                last_updated TEXT,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                raw_json TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                UNIQUE(source, source_id)
            );

            CREATE TABLE IF NOT EXISTS assets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                program_id INTEGER NOT NULL REFERENCES programs(id) ON DELETE CASCADE,
                scope TEXT NOT NULL CHECK(scope IN ('in_scope', 'out_of_scope', 'candidate')),
                asset_type TEXT NOT NULL,
                raw_value TEXT NOT NULL,
                normalized_value TEXT,
                is_wildcard INTEGER NOT NULL DEFAULT 0,
                eligible_for_bounty INTEGER,
                confidence REAL NOT NULL DEFAULT 1.0,
                notes TEXT,
                raw_json TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                UNIQUE(program_id, scope, raw_value)
            );

            CREATE TABLE IF NOT EXISTS asset_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                program_id INTEGER NOT NULL REFERENCES programs(id) ON DELETE CASCADE,
                source_run_id INTEGER REFERENCES source_runs(id) ON DELETE SET NULL,
                event_type TEXT NOT NULL CHECK(event_type IN ('added', 'removed', 'scope_changed')),
                scope TEXT NOT NULL,
                asset_type TEXT NOT NULL,
                raw_value TEXT NOT NULL,
                normalized_value TEXT,
                observed_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS web_pages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                seed_url TEXT NOT NULL,
                page_url TEXT NOT NULL,
                title TEXT,
                matched_terms_json TEXT NOT NULL,
                extracted_urls_json TEXT NOT NULL,
                text_snippet TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                UNIQUE(seed_url, page_url)
            );

            CREATE TABLE IF NOT EXISTS program_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                program_id INTEGER NOT NULL REFERENCES programs(id) ON DELETE CASCADE,
                source_run_id INTEGER REFERENCES source_runs(id) ON DELETE SET NULL,
                event_type TEXT NOT NULL CHECK(event_type IN ('added', 'removed', 'restored')),
                observed_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS program_sources (
                source TEXT NOT NULL,
                source_id TEXT NOT NULL,
                program_id INTEGER NOT NULL REFERENCES programs(id) ON DELETE CASCADE,
                payload_json TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                PRIMARY KEY(source, source_id)
            );
            CREATE INDEX IF NOT EXISTS idx_program_sources_program ON program_sources(program_id);
            CREATE TABLE IF NOT EXISTS program_identities (
                identity TEXT PRIMARY KEY,
                program_id INTEGER NOT NULL REFERENCES programs(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS program_aliases (
                alias_id INTEGER PRIMARY KEY,
                program_id INTEGER NOT NULL REFERENCES programs(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_programs_name ON programs(name);
            CREATE INDEX IF NOT EXISTS idx_programs_platform ON programs(platform);
            CREATE INDEX IF NOT EXISTS idx_programs_source_active ON programs(source, is_active);
            CREATE INDEX IF NOT EXISTS idx_assets_scope_norm ON assets(scope, normalized_value);
            CREATE INDEX IF NOT EXISTS idx_assets_norm ON assets(normalized_value);
            CREATE INDEX IF NOT EXISTS idx_assets_type_wildcard ON assets(asset_type, is_wildcard);
            CREATE INDEX IF NOT EXISTS idx_assets_wildcard_norm ON assets(is_wildcard, normalized_value);
            CREATE INDEX IF NOT EXISTS idx_asset_events_run ON asset_events(source_run_id);
            CREATE INDEX IF NOT EXISTS idx_web_pages_url ON web_pages(page_url);
            """
        )
        self._add_missing_columns()
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            if not self.conn.execute("SELECT 1 FROM schema_migrations WHERE version = 2").fetchone():
                self._migrate_program_sources()
                self.conn.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (2, ?)", (utc_now_iso(),))
            if not self.conn.execute("SELECT 1 FROM schema_migrations WHERE version = 3").fetchone():
                self._repair_source_policy_urls()
                self.conn.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (3, ?)", (utc_now_iso(),))
            if not self.conn.execute("SELECT 1 FROM schema_migrations WHERE version = 4").fetchone():
                self._renormalize_catalog_targets()
                self.conn.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (4, ?)", (utc_now_iso(),))
            if not self.conn.execute("SELECT 1 FROM schema_migrations WHERE version = 5").fetchone():
                self._renormalize_catalog_targets()
                self.conn.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (5, ?)", (utc_now_iso(),))
            if not self.conn.execute("SELECT 1 FROM schema_migrations WHERE version = 6").fetchone():
                self._renormalize_catalog_targets(recover_provider_scope=True)
                self.conn.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (6, ?)", (utc_now_iso(),))

    def _renormalize_catalog_targets(self, *, recover_provider_scope: bool = False) -> None:
        """Rebuild target identities from retained evidence without fetching feeds."""
        rows = self.conn.execute("SELECT source,source_id,program_id,payload_json FROM program_sources").fetchall()
        affected = set()
        for row in rows:
            program = program_from_payload(json.loads(row["payload_json"]))
            if recover_provider_scope:
                program = _recover_provider_scope(program)
            updated = _renormalized_program(program)
            self.conn.execute("UPDATE program_sources SET payload_json=? WHERE source=? AND source_id=?",
                              (json.dumps(program_payload(updated)), row["source"], row["source_id"]))
            affected.add(row["program_id"])
        for program_id in sorted(affected):
            active = self.conn.execute(
                "SELECT source,source_id,is_active FROM programs WHERE id=?", (program_id,)
            ).fetchone()
            if active["is_active"]:
                self._rebuild_program(program_id)
            else:
                # Historical programs remain inactive while their retained targets
                # receive the same corrected normalization as current programs.
                observations = self.conn.execute(
                    "SELECT payload_json FROM program_sources WHERE program_id=?",
                    (program_id,),
                ).fetchall()
                programs = [
                    program_from_payload(json.loads(item["payload_json"]))
                    for item in observations
                ]
                if not programs:
                    programs = [_renormalized_program(program_from_payload(self.get_program(program_id)))]
                merged = merge_programs(
                    programs, source=active["source"], source_id=active["source_id"]
                )
                self._sync_assets(program_id, merged)
                self.conn.execute("UPDATE programs SET program_url=? WHERE id=?", (merged.program_url, program_id))

    def _repair_source_policy_urls(self) -> None:
        rows = self.conn.execute("SELECT source,source_id,payload_json FROM program_sources WHERE source='arkadiyt'").fetchall()
        affected = set()
        for row in rows:
            program = program_from_payload(json.loads(row["payload_json"]))
            if program.program_url or (program.platform or "").lower() != "yeswehack":
                continue
            url = yeswehack_policy_url(program.raw)
            if not url:
                continue
            program = replace(program, program_url=url)
            self.conn.execute("UPDATE program_sources SET payload_json=? WHERE source=? AND source_id=?",
                              (json.dumps(program_payload(program)), row["source"], row["source_id"]))
            current = self.conn.execute("SELECT program_id FROM program_sources WHERE source=? AND source_id=?",
                                        (row["source"], row["source_id"])).fetchone()
            affected.add(self._bind_identity(policy_identity(program), current["program_id"]))
        for program_id in affected:
            alias = self.conn.execute("SELECT program_id FROM program_aliases WHERE alias_id=?", (program_id,)).fetchone()
            self._rebuild_program(alias["program_id"] if alias else program_id)

    def _migrate_program_sources(self) -> None:
        rows = self.conn.execute("SELECT * FROM programs ORDER BY id").fetchall()
        for row in rows:
            payload = dict(row)
            payload["raw"] = json.loads(payload.pop("raw_json"))
            payload["assets"] = []
            for asset in self.conn.execute("SELECT * FROM assets WHERE program_id = ?", (row["id"],)):
                item = dict(asset)
                item["raw"] = json.loads(item.pop("raw_json"))
                payload["assets"].append(item)
            program = program_from_payload(payload)
            self.conn.execute(
                "INSERT INTO program_sources VALUES (?, ?, ?, ?, ?, ?, ?)",
                (row["source"], row["source_id"], row["id"], json.dumps(program_payload(program)),
                 row["is_active"], row["first_seen_at"], row["last_seen_at"]),
            )
        for row in rows:
            observed = self.conn.execute("SELECT program_id,payload_json FROM program_sources WHERE source=? AND source_id=?",
                                         (row["source"], row["source_id"])).fetchone()
            self._bind_identity(policy_identity(program_from_payload(json.loads(observed["payload_json"]))), observed["program_id"])
        for row in self.conn.execute("SELECT id FROM programs").fetchall():
            self._rebuild_program(row["id"])

    def _bind_identity(self, identity: str, program_id: int) -> int:
        existing = self.conn.execute("SELECT program_id FROM program_identities WHERE identity=?", (identity,)).fetchone()
        if existing and existing["program_id"] != program_id:
            keep, drop = sorted((program_id, existing["program_id"]))
            self._merge_program_ids(keep, drop)
            program_id = keep
        self.conn.execute("INSERT OR REPLACE INTO program_identities VALUES (?, ?)", (identity, program_id))
        return program_id

    def _merge_program_ids(self, keep: int, drop: int) -> None:
        for row in self.conn.execute("SELECT id,scope,raw_value,first_seen_at,last_seen_at FROM assets WHERE program_id=?", (drop,)).fetchall():
            existing = self.conn.execute("SELECT id FROM assets WHERE program_id=? AND scope=? AND raw_value=?",
                                         (keep, row["scope"], row["raw_value"])).fetchone()
            if existing:
                self.conn.execute("UPDATE assets SET first_seen_at=min(first_seen_at,?),last_seen_at=max(last_seen_at,?) WHERE id=?",
                                  (row["first_seen_at"], row["last_seen_at"], existing["id"]))
                self.conn.execute("DELETE FROM assets WHERE id=?", (row["id"],))
            else:
                self.conn.execute("UPDATE assets SET program_id=? WHERE id=?", (keep, row["id"]))
        for table in ("program_sources", "program_identities", "program_aliases", "program_events", "asset_events"):
            self.conn.execute(f"UPDATE {table} SET program_id=? WHERE program_id=?", (keep, drop))
        self.conn.execute("INSERT OR REPLACE INTO program_aliases VALUES (?, ?)", (drop, keep))
        self.conn.execute("UPDATE programs SET first_seen_at=min(first_seen_at,(SELECT first_seen_at FROM programs WHERE id=?)) WHERE id=?", (drop, keep))
        self.conn.execute("DELETE FROM programs WHERE id=?", (drop,))

    def _store_program_source(self, program: Program, *, source_run_id: int | None = None) -> int:
        program = _renormalized_program(program)
        row = self.conn.execute("SELECT program_id FROM program_sources WHERE source=? AND source_id=?",
                                (program.source, program.source_id)).fetchone()
        identity = policy_identity(program)
        match = self.conn.execute("SELECT program_id FROM program_identities WHERE identity=?", (identity,)).fetchone()
        program_id = row["program_id"] if row else (match["program_id"] if match else self._upsert_program(program, source_run_id=source_run_id))
        program_id = self._bind_identity(identity, program_id)
        now = utc_now_iso()
        self.conn.execute("""
            INSERT INTO program_sources VALUES (?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(source,source_id) DO UPDATE SET
                program_id=excluded.program_id,payload_json=excluded.payload_json,
                is_active=1,last_seen_at=excluded.last_seen_at
        """, (program.source, program.source_id, program_id, json.dumps(program_payload(program)), now, now))
        return program_id

    def _rebuild_program(self, program_id: int, *, source_run_id: int | None = None) -> None:
        row = self.conn.execute("SELECT source,source_id,is_active FROM programs WHERE id=?", (program_id,)).fetchone()
        if row is None:
            return
        observations = self.conn.execute("SELECT payload_json FROM program_sources WHERE program_id=? AND is_active=1", (program_id,)).fetchall()
        if not observations:
            if row["is_active"]:
                self.conn.execute("UPDATE programs SET is_active=0 WHERE id=?", (program_id,))
                self.conn.execute("INSERT INTO program_events(program_id,source_run_id,event_type,observed_at) VALUES (?,?,'removed',?)",
                                  (program_id, source_run_id, utc_now_iso()))
            return
        merged = merge_programs([program_from_payload(json.loads(item["payload_json"])) for item in observations],
                                source=row["source"], source_id=row["source_id"])
        self._upsert_program(merged, source_run_id=source_run_id)
        self._sync_assets(program_id, merged, source_run_id=source_run_id)

    def _add_missing_columns(self) -> None:
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(programs)")}
        additions = {
            "organization": "TEXT", "program_type": "TEXT NOT NULL DEFAULT 'bug_bounty'",
            "status": "TEXT NOT NULL DEFAULT 'active'", "offers_bounty": "INTEGER",
            "description": "TEXT", "is_active": "INTEGER NOT NULL DEFAULT 1",
        }
        for name, declaration in additions.items():
            if name not in columns:
                self.conn.execute(f"ALTER TABLE programs ADD COLUMN {name} {declaration}")

    def start_run(self, source: str) -> int:
        now = utc_now_iso()
        cursor = self.conn.execute(
            "INSERT INTO source_runs(source, started_at, status) VALUES (?, ?, ?)",
            (source, now, "running"),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def finish_run(
        self,
        run_id: int,
        *,
        status: str,
        records_seen: int = 0,
        error: str | None = None,
    ) -> None:
        with self.conn:
            self._finish_run(
                run_id,
                status=status,
                records_seen=records_seen,
                error=error,
            )

    def _finish_run(
        self,
        run_id: int,
        *,
        status: str,
        records_seen: int = 0,
        error: str | None = None,
    ) -> None:
        self.conn.execute(
            """
            UPDATE source_runs
               SET completed_at = ?, status = ?, records_seen = ?, error = ?
             WHERE id = ?
            """,
            (utc_now_iso(), status, records_seen, error, run_id),
        )

    def upsert_programs(self, programs: Iterable[Program], *, source_run_id: int | None = None) -> int:
        with self.conn:
            return self._upsert_programs(programs, source_run_id=source_run_id)

    def _upsert_programs(
        self, programs: Iterable[Program], *, source_run_id: int | None = None
    ) -> int:
        count = 0
        for program in programs:
            program_id = self._store_program_source(program, source_run_id=source_run_id)
            self._rebuild_program(program_id, source_run_id=source_run_id)
            count += 1
        return count

    def apply_source_snapshot(
        self,
        source: str,
        programs: Iterable[Program],
        *,
        source_run_id: int,
    ) -> int:
        """Atomically store a complete snapshot and finish its source run."""

        materialized = list(programs)
        seen_source_ids = {program.source_id for program in materialized}
        with self.conn:
            count = self._upsert_programs(materialized, source_run_id=source_run_id)
            self._finalize_source_snapshot(
                source,
                seen_source_ids,
                source_run_id=source_run_id,
            )
            self._finish_run(source_run_id, status="completed", records_seen=count)
        return count

    def finalize_source_snapshot(self, source: str, seen_source_ids: set[str], *, source_run_id: int) -> None:
        """Mark programs absent from a successful, complete provider snapshot as removed."""
        with self.conn:
            self._finalize_source_snapshot(
                source,
                seen_source_ids,
                source_run_id=source_run_id,
            )

    def _finalize_source_snapshot(
        self, source: str, seen_source_ids: set[str], *, source_run_id: int
    ) -> None:
        rows = self.conn.execute(
            "SELECT program_id AS id, source_id FROM program_sources WHERE source = ? AND is_active = 1", (source,)
        ).fetchall()
        now = utc_now_iso()
        for row in rows:
            if row["source_id"] not in seen_source_ids:
                self.conn.execute(
                    "UPDATE program_sources SET is_active = 0, last_seen_at = ? WHERE source = ? AND source_id = ?",
                    (now, source, row["source_id"]),
                )
                self._rebuild_program(row["id"], source_run_id=source_run_id)

    def search_target(
        self, target: str, *, include_inactive: bool = False
    ) -> dict[str, list[dict[str, Any]]]:
        active_filter = "" if include_inactive else "AND p.is_active = 1"
        _target_type, normalized_target, target_is_wildcard = classify_asset(target)
        if not normalized_target:
            normalized_target = strip_scheme_and_path(target)
        target_host = strip_scheme_and_path(target)
        rows = self.conn.execute(
            f"""
            SELECT
                p.id AS program_id,
                p.name,
                p.platform,
                p.program_url,
                p.reward,
                p.max_bounty,
                p.source,
                a.scope,
                a.asset_type,
                a.raw_value,
                a.normalized_value,
                a.is_wildcard,
                a.eligible_for_bounty,
                a.confidence
            FROM assets a
            JOIN programs p ON p.id = a.program_id
            WHERE a.normalized_value IS NOT NULL
              AND (
                    a.normalized_value = ? COLLATE NOCASE
                    OR (a.asset_type IN ('domain', 'website') AND (
                        a.normalized_value = ? COLLATE NOCASE
                        OR (? = 0 AND a.is_wildcard = 1
                            AND LOWER(?) LIKE '%.' || LOWER(a.normalized_value))
                    ))
                    OR (a.asset_type = 'ip' AND a.normalized_value = ? COLLATE NOCASE)
                    OR (? = 'cidr' AND a.asset_type = 'ip' AND a.scope = 'out_of_scope')
                    OR a.asset_type = 'cidr'
                    OR (a.asset_type = 'url' AND a.is_wildcard = 1)
                    OR (
                        ? = 0
                        AND a.is_wildcard = 1
                        AND LOWER(?) LIKE '%.' || LOWER(a.normalized_value)
                    )
              )
              {active_filter}
            ORDER BY p.name COLLATE NOCASE, a.scope
            """,
            (normalized_target, target_host, int(target_is_wildcard), target_host,
             target_host, _target_type, int(target_is_wildcard), normalized_target),
        ).fetchall()
        grouped = {"allowed": [], "excluded": [], "candidate": []}
        for row in rows:
            if not normalized_target_matches_asset(
                normalized_target,
                target_is_wildcard,
                row["normalized_value"],
                bool(row["is_wildcard"]),
                row["asset_type"],
                scope=row["scope"],
            ):
                continue
            item = dict(row)
            if row["scope"] == "in_scope":
                grouped["allowed"].append(item)
            elif row["scope"] == "out_of_scope":
                grouped["excluded"].append(item)
            else:
                grouped["candidate"].append(item)
        return grouped

    def list_programs(self, query: ProgramQuery, *, program_ids: Iterable[int] | None = None) -> Page[dict[str, Any]]:
        filters: list[str] = []
        params: list[Any] = []
        if program_ids is not None:
            ids = sorted(set(program_ids))
            filters.append("p.id IN (" + ",".join("?" for _ in ids) + ")" if ids else "0")
            params.extend(ids)
        if query.active is not None:
            filters.append("p.is_active = ?")
            params.append(int(query.active))
        if query.text:
            filters.append(
                "(p.name LIKE ? ESCAPE '\\' OR COALESCE(p.organization, '') LIKE ? ESCAPE '\\' "
                "OR COALESCE(p.program_url, '') LIKE ? ESCAPE '\\')"
            )
            pattern = _like_pattern(query.text)
            params.extend([pattern, pattern, pattern])
        if query.source:
            active_source = " AND ps.is_active = 1" if query.active is True else ""
            filters.append("EXISTS (SELECT 1 FROM program_sources ps WHERE ps.program_id = p.id AND ps.source = ?" + active_source + ")")
            params.append(query.source)
        if query.platform:
            filters.append("p.platform = ?")
            params.append(query.platform)
        if query.status:
            filters.append("p.status = ?")
            params.append(query.status)
        if query.scannable_only:
            filters.extend([
                "p.is_active=1 AND lower(p.status) IN ('active','open','public')",
                """EXISTS (SELECT 1 FROM assets usable
                    WHERE usable.program_id=p.id AND usable.scope='in_scope'
                    AND usable.asset_type IN ('domain','ip','cidr')
                    AND COALESCE(usable.normalized_value,'') <> ''
                    AND NOT EXISTS (SELECT 1 FROM assets denied
                        WHERE denied.program_id=p.id AND denied.scope='out_of_scope'
                        AND catalog_scope_blocked(usable.normalized_value, usable.is_wildcard,
                            denied.normalized_value, denied.is_wildcard, denied.asset_type)))""",
            ])
        asset_filters = ["s.program_id = p.id"]
        asset_params: list[Any] = []
        if query.scope:
            asset_filters.append("s.scope = ?")
            asset_params.append(query.scope)
        if query.scope_text:
            asset_filters.append("(s.raw_value LIKE ? ESCAPE '\\' OR s.normalized_value LIKE ? ESCAPE '\\')")
            asset_params.extend([_like_pattern(query.scope_text)] * 2)
        if query.kind == "wildcards":
            asset_filters.append("s.is_wildcard=1 AND s.asset_type='domain'")
        elif query.kind == "domains":
            asset_filters.append("s.is_wildcard=0 AND s.asset_type IN ('domain','website')")
        elif query.kind == "urls":
            asset_filters.append("s.asset_type IN ('url','website')")
        elif query.kind:
            asset_filters.append("s.asset_type=?")
            asset_params.append(query.kind)
        matched = " AND ".join(asset_filters)
        if query.scope or query.scope_text or query.kind:
            filters.append("EXISTS (SELECT 1 FROM assets s WHERE " + matched + ")")
            params.extend(asset_params)
        where = " WHERE " + " AND ".join(filters) if filters else ""
        total = int(
            self.conn.execute(f"SELECT COUNT(*) FROM programs p{where}", params).fetchone()[0]
        )
        rows = self.conn.execute(
            f"""
            SELECT
                p.id, p.source, p.source_id, p.name, p.organization, p.platform,
                p.program_url, p.program_type, p.status, p.offers_bounty, p.reward,
                p.min_bounty, p.max_bounty, p.last_updated, p.first_seen_at,
                p.last_seen_at, p.is_active,
                (SELECT COUNT(*) FROM assets s WHERE {matched}) AS matching_asset_count,
                (SELECT COUNT(*) FROM assets s WHERE {matched} AND s.is_wildcard=0 AND s.asset_type IN ('domain','website')) AS domain_count,
                (SELECT COUNT(*) FROM assets s WHERE {matched} AND s.is_wildcard=1 AND s.asset_type='domain') AS wildcard_count,
                (SELECT group_concat(DISTINCT ps.source) FROM program_sources ps WHERE ps.program_id=p.id) AS sources,
                COUNT(a.id) AS asset_count,
                COALESCE(SUM(CASE WHEN a.scope = 'in_scope' THEN 1 ELSE 0 END), 0) AS in_scope_count,
                COALESCE(SUM(CASE WHEN a.scope = 'out_of_scope' THEN 1 ELSE 0 END), 0) AS out_of_scope_count,
                COALESCE(SUM(CASE WHEN a.scope = 'candidate' THEN 1 ELSE 0 END), 0) AS candidate_count
            FROM programs p
            LEFT JOIN assets a ON a.program_id = p.id
            {where}
            GROUP BY p.id
            ORDER BY p.name COLLATE NOCASE, p.id
            LIMIT ? OFFSET ?
            """,
            [*asset_params, *asset_params, *asset_params, *params, query.limit, query.offset],
        ).fetchall()
        return Page([dict(row) for row in rows], total, query.limit, query.offset)

    def list_recon_programs(
        self, query: ProgramQuery, *, operation: str = "discovery"
    ) -> dict[str, Any]:
        """Return compact, server-filtered summaries for recon-capable programs."""

        if operation not in {"discovery", "host"}:
            raise ValueError("operation must be discovery or host")
        capability = (
            "w.scope='in_scope' AND w.is_wildcard=1 AND w.asset_type='domain'"
            if operation == "discovery"
            else "w.scope='in_scope' AND w.asset_type IN ('domain','website')"
        )

        filters = [
            "p.is_active = 1",
            "p.status = 'active'",
            "EXISTS (SELECT 1 FROM assets w WHERE w.program_id=p.id "
            f"AND {capability})",
        ]
        params: list[Any] = []
        if query.text:
            pattern = _like_pattern(query.text)
            filters.append(
                "(p.name LIKE ? ESCAPE '\\' OR COALESCE(p.organization,'') LIKE ? ESCAPE '\\' "
                "OR COALESCE(p.program_url,'') LIKE ? ESCAPE '\\')"
            )
            params.extend([pattern, pattern, pattern])
        if query.source:
            filters.append(
                "EXISTS (SELECT 1 FROM program_sources ps WHERE ps.program_id=p.id "
                "AND ps.source=? AND ps.is_active=1)"
            )
            params.append(query.source)
        if query.platform:
            filters.append("p.platform=?")
            params.append(query.platform)
        if query.status:
            filters.append("p.status=?")
            params.append(query.status)
        if query.scope_text:
            pattern = _like_pattern(query.scope_text)
            filters.append(
                "EXISTS (SELECT 1 FROM assets t WHERE t.program_id=p.id "
                "AND (t.raw_value LIKE ? ESCAPE '\\' OR "
                "COALESCE(t.normalized_value,'') LIKE ? ESCAPE '\\'))"
            )
            params.extend([pattern, pattern])

        facet_where = " WHERE " + " AND ".join(filters)
        item_filters = list(filters)
        item_params = list(params)
        if query.scope:
            if query.scope_text:
                pattern = _like_pattern(query.scope_text)
                item_filters.append(
                    "EXISTS (SELECT 1 FROM assets s WHERE s.program_id=p.id AND s.scope=? "
                    "AND (s.raw_value LIKE ? ESCAPE '\\' OR "
                    "COALESCE(s.normalized_value,'') LIKE ? ESCAPE '\\'))"
                )
                item_params.extend([query.scope, pattern, pattern])
            else:
                item_filters.append(
                    "EXISTS (SELECT 1 FROM assets s WHERE s.program_id=p.id AND s.scope=?)"
                )
                item_params.append(query.scope)
        item_where = " WHERE " + " AND ".join(item_filters)
        total = int(
            self.conn.execute(
                f"SELECT COUNT(*) FROM programs p{item_where}", item_params
            ).fetchone()[0]
        )
        rows = self.conn.execute(
            f"""
            SELECT p.id,p.name,p.organization,p.program_url,p.source,p.platform,
                   p.status,p.is_active,
                   (SELECT group_concat(DISTINCT ps.source) FROM program_sources ps
                    WHERE ps.program_id=p.id AND ps.is_active=1) AS sources,
                   COALESCE(SUM(CASE WHEN a.scope='in_scope' THEN 1 ELSE 0 END),0)
                       AS in_scope_count,
                   COALESCE(SUM(CASE WHEN a.scope='out_of_scope' THEN 1 ELSE 0 END),0)
                       AS out_of_scope_count,
                   COALESCE(SUM(CASE WHEN a.scope='candidate' THEN 1 ELSE 0 END),0)
                       AS candidate_count,
                   COALESCE(SUM(CASE WHEN a.scope='in_scope' AND a.is_wildcard=1
                                     AND a.asset_type='domain' THEN 1 ELSE 0 END),0)
                       AS wildcard_count
            FROM programs p LEFT JOIN assets a ON a.program_id=p.id
            {item_where}
            GROUP BY p.id
            ORDER BY p.name COLLATE NOCASE,p.id
            LIMIT ? OFFSET ?
            """,
            [*item_params, query.limit, query.offset],
        ).fetchall()
        scope_counts = {}
        for scope in ("in_scope", "out_of_scope", "candidate"):
            if query.scope_text:
                pattern = _like_pattern(query.scope_text)
                facet_filter = (
                    "EXISTS (SELECT 1 FROM assets f WHERE f.program_id=p.id AND f.scope=? "
                    "AND (f.raw_value LIKE ? ESCAPE '\\' OR "
                    "COALESCE(f.normalized_value,'') LIKE ? ESCAPE '\\'))"
                )
                facet_params = [*params, scope, pattern, pattern]
            else:
                facet_filter = (
                    "EXISTS (SELECT 1 FROM assets f WHERE f.program_id=p.id AND f.scope=?)"
                )
                facet_params = [*params, scope]
            scope_counts[scope] = int(
                self.conn.execute(
                    f"SELECT COUNT(*) FROM programs p{facet_where} AND {facet_filter}",
                    facet_params,
                ).fetchone()[0]
            )
        return {
            **Page([dict(row) for row in rows], total, query.limit, query.offset).to_dict(),
            "scope_counts": scope_counts,
        }

    def add_manual_program(self, program: Program) -> dict[str, Any]:
        """Reuse an unambiguous identity; never replace an imported program's scope."""
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            if program.program_url:
                rows = self.conn.execute(
                    "SELECT program_id AS id FROM program_identities WHERE identity = ?",
                    (policy_identity(program),),
                ).fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT id FROM programs WHERE name = ? COLLATE NOCASE", (program.name,)
                ).fetchall()
            if len(rows) > 1:
                raise ValueError("Multiple catalog programs match; select the intended program by ID")
            created = not rows
            program_id = self._store_program_source(program) if created else rows[0]["id"]
            if created:
                self._rebuild_program(program_id)
            return {"created": created, "program": self.get_program(program_id)}

    def get_program(self, program_id: int) -> dict[str, Any] | None:
        alias = self.conn.execute("SELECT program_id FROM program_aliases WHERE alias_id=?", (program_id,)).fetchone()
        if alias:
            program_id = alias["program_id"]
        row = self.conn.execute(
            """
            SELECT id, source, source_id, name, organization, platform, program_url,
                   program_type, status, offers_bounty, description, reward,
                   min_bounty, max_bounty, safe_harbor, allows_disclosure,
                   last_updated, first_seen_at, last_seen_at, raw_json, is_active
            FROM programs
            WHERE id = ?
            """,
            (program_id,),
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["raw"] = json.loads(result.pop("raw_json") or "{}")
        assets = self.conn.execute(
            """
            SELECT id, scope, asset_type, raw_value, normalized_value, is_wildcard,
                   eligible_for_bounty, confidence, notes, first_seen_at, last_seen_at
            FROM assets
            WHERE program_id = ?
            ORDER BY scope, asset_type, normalized_value COLLATE NOCASE, raw_value COLLATE NOCASE
            """,
            (program_id,),
        ).fetchall()
        result["assets"] = [dict(asset) for asset in assets]
        for scope in ("in_scope", "out_of_scope", "candidate"):
            result[scope + "_count"] = sum(asset["scope"] == scope for asset in assets)
        result["source_records"] = [dict(row) for row in self.conn.execute(
            "SELECT source,source_id,is_active,first_seen_at,last_seen_at,payload_json FROM program_sources WHERE program_id=? ORDER BY source,source_id",
            (program_id,),
        )]
        for record in result["source_records"]:
            record["program_url"] = json.loads(record.pop("payload_json")).get("program_url")
        result["sources"] = ",".join(sorted({row["source"] for row in result["source_records"]}))
        return result

    def _asset_filters(self, query: AssetQuery) -> tuple[str, list[Any]]:
        filters: list[str] = []
        params: list[Any] = []
        if query.active is not None:
            filters.append("p.is_active = ?")
            params.append(int(query.active))
        if query.program_id is not None:
            filters.append("a.program_id = ?")
            alias = self.conn.execute("SELECT program_id FROM program_aliases WHERE alias_id=?", (query.program_id,)).fetchone()
            params.append(alias["program_id"] if alias else query.program_id)
        if query.source:
            active_source = " AND ps.is_active = 1" if query.active is True else ""
            filters.append("EXISTS (SELECT 1 FROM program_sources ps WHERE ps.program_id = p.id AND ps.source = ?" + active_source + ")")
            params.append(query.source)
        if query.scope:
            filters.append("a.scope = ?")
            params.append(query.scope)
        if query.kind == "wildcards":
            filters.append("a.is_wildcard = 1")
        elif query.kind == "domains":
            filters.append("a.is_wildcard = 0")
            filters.append("a.asset_type IN ('domain', 'website')")
        elif query.kind == "mobile_app":
            filters.append("a.asset_type IN ('android_app', 'ios_app')")
        elif query.kind == "urls":
            filters.append("a.asset_type IN ('url', 'website')")
        elif query.kind:
            filters.append("a.asset_type = ?")
            params.append(query.kind)
        if query.text:
            filters.append(
                "(a.raw_value LIKE ? ESCAPE '\\' OR COALESCE(a.normalized_value, '') LIKE ? ESCAPE '\\' "
                "OR p.name LIKE ? ESCAPE '\\')"
            )
            pattern = _like_pattern(query.text)
            params.extend([pattern, pattern, pattern])
        if query.target_text:
            filters.append("(a.raw_value LIKE ? ESCAPE '\\' OR COALESCE(a.normalized_value, '') LIKE ? ESCAPE '\\')")
            params.extend([_like_pattern(query.target_text)] * 2)
        where = " WHERE " + " AND ".join(filters) if filters else ""
        return where, params

    def iter_export_assets(self, query: AssetQuery, *, program_ids: Iterable[int] | None = None,
                           asset_ids: Iterable[int] | None = None, platform: str | None = None):
        """Stream selected seeds without browser counts, sorting or repeated pagination."""
        where, params = self._asset_filters(query)
        for column, values in (("a.program_id", program_ids), ("a.id", asset_ids)):
            if values is not None:
                ids = sorted(set(values))
                clause = column + " IN (" + ",".join("?" for _ in ids) + ")" if ids else "0"
                where += (" AND " if where else " WHERE ") + clause
                params.extend(ids)
        if platform:
            where += (" AND " if where else " WHERE ") + "p.platform = ?"
            params.append(platform)
        cursor = self.conn.execute(
            "SELECT a.id,a.program_id,a.scope,a.asset_type,a.raw_value,a.normalized_value,a.is_wildcard "
            "FROM assets a JOIN programs p ON p.id=a.program_id" + where + " ORDER BY a.id", params)
        yield from (dict(row) for row in cursor)

    def list_assets(self, query: AssetQuery) -> Page[dict[str, Any]]:
        where, params = self._asset_filters(query)
        join = " FROM assets a JOIN programs p ON p.id = a.program_id"
        total = int(
            self.conn.execute(f"SELECT COUNT(*){join}{where}", params).fetchone()[0]
        )
        rows = self.conn.execute(
            f"""
            SELECT a.id, a.program_id, a.scope, a.asset_type, a.raw_value,
                   a.normalized_value, a.is_wildcard, a.eligible_for_bounty,
                   a.confidence, a.notes, a.first_seen_at, a.last_seen_at,
                   p.name AS program_name, p.platform, p.program_url, p.source,
                   p.is_active AS program_is_active,
                   (SELECT COUNT(DISTINCT a2.program_id) FROM assets a2
                    WHERE COALESCE(a2.normalized_value,a2.raw_value)=
                          COALESCE(a.normalized_value,a.raw_value)) AS security_programs
            {join}
            {where}
            ORDER BY COALESCE(a.normalized_value, a.raw_value) COLLATE NOCASE,
                     p.name COLLATE NOCASE, a.id
            LIMIT ? OFFSET ?
            """,
            [*params, query.limit, query.offset],
        ).fetchall()
        return Page([dict(row) for row in rows], total, query.limit, query.offset)

    def recent_source_runs(self, *, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT id, source, started_at, completed_at, status, records_seen, error
            FROM source_runs
            ORDER BY id DESC
            LIMIT ?
            """,
            (max(1, min(int(limit), 200)),),
        ).fetchall()
        return [dict(row) for row in rows]

    def recent_events(self, *, limit: int = 30) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT * FROM (
                SELECT 'asset' AS category, ae.id, ae.event_type, ae.observed_at,
                       ae.scope, ae.asset_type, ae.raw_value, ae.normalized_value,
                       p.id AS program_id, p.name AS program_name, p.source,
                       ae.source_run_id
                FROM asset_events ae
                JOIN programs p ON p.id = ae.program_id
                UNION ALL
                SELECT 'program' AS category, pe.id, pe.event_type, pe.observed_at,
                       NULL AS scope, NULL AS asset_type, NULL AS raw_value,
                       NULL AS normalized_value, p.id AS program_id,
                       p.name AS program_name, p.source, pe.source_run_id
                FROM program_events pe
                JOIN programs p ON p.id = pe.program_id
            )
            ORDER BY observed_at DESC, id DESC
            LIMIT ?
            """,
            (max(1, min(int(limit), 200)),),
        ).fetchall()
        return [dict(row) for row in rows]

    def diagnostics(self) -> dict[str, Any]:
        integrity = self.conn.execute("PRAGMA quick_check").fetchone()[0]
        journal_mode = self.conn.execute("PRAGMA journal_mode").fetchone()[0]
        version_row = self.conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
        failed_runs = self.conn.execute(
            """
            SELECT id, source, completed_at, error
            FROM source_runs
            WHERE status = 'failed'
            ORDER BY id DESC LIMIT 5
            """
        ).fetchall()
        return {
            "database_path": str(self.path.resolve()),
            "database_size_bytes": self.path.stat().st_size if self.path.exists() else 0,
            "schema_version": int(version_row[0] or 0),
            "expected_schema_version": SCHEMA_VERSION,
            "journal_mode": journal_mode,
            "integrity": integrity,
            "healthy": integrity == "ok" and int(version_row[0] or 0) == SCHEMA_VERSION,
            "recent_failures": [dict(row) for row in failed_runs],
        }

    def export_assets(
        self,
        *,
        kind: str | None = None,
        scope: str = "in_scope",
        include_inactive: bool = False,
    ) -> list[sqlite3.Row]:
        filters = ["a.scope = ?"]
        params: list[Any] = [scope]
        if kind == "wildcards":
            filters.append("a.is_wildcard = 1")
        elif kind == "domains":
            filters.append("a.is_wildcard = 0")
            filters.append("a.asset_type IN ('domain', 'website')")
        elif kind == "mobile_app":
            filters.append("a.asset_type IN ('android_app', 'ios_app')")
        elif kind == "urls":
            filters.append("a.asset_type IN ('url', 'website')")
        elif kind:
            filters.append("a.asset_type = ?")
            params.append(kind)
        if not include_inactive:
            filters.append("p.is_active = 1")
        where = " AND ".join(filters)
        return self.conn.execute(
            f"""
            SELECT DISTINCT
                a.scope,
                a.asset_type,
                a.raw_value,
                a.normalized_value,
                a.is_wildcard,
                p.name AS program_name,
                p.platform,
                p.program_url,
                p.source
            FROM assets a
            JOIN programs p ON p.id = a.program_id
            WHERE {where}
            ORDER BY a.normalized_value COLLATE NOCASE, p.name COLLATE NOCASE
            """,
            params,
        ).fetchall()

    def export_programs(self, *, include_inactive: bool = False) -> list[sqlite3.Row]:
        active_filter = "" if include_inactive else "WHERE is_active = 1"
        return self.conn.execute(
            f"""
            SELECT
                source, source_id, name, organization, platform, program_url, program_type,
                status, offers_bounty, description, reward,
                min_bounty, max_bounty, safe_harbor, allows_disclosure,
                last_updated, first_seen_at, last_seen_at, is_active,
                (SELECT group_concat(DISTINCT ps.source) FROM program_sources ps WHERE ps.program_id=programs.id) AS sources
            FROM programs
            {active_filter}
            ORDER BY name COLLATE NOCASE
            """
        ).fetchall()

    def stats(self) -> dict[str, Any]:
        row = self.conn.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM programs) AS programs,
                (SELECT COUNT(*) FROM programs WHERE is_active = 1) AS active_programs,
                (SELECT COUNT(*) FROM programs WHERE is_active = 0) AS inactive_programs,
                (SELECT COUNT(*) FROM assets) AS stored_assets,
                (SELECT COUNT(*) FROM assets a JOIN programs p ON p.id = a.program_id
                  WHERE p.is_active = 1) AS assets,
                (SELECT COUNT(*) FROM assets a JOIN programs p ON p.id = a.program_id
                  WHERE a.scope = 'in_scope' AND p.is_active = 1) AS in_scope_assets,
                (SELECT COUNT(*) FROM assets a JOIN programs p ON p.id = a.program_id
                  WHERE a.scope = 'out_of_scope' AND p.is_active = 1) AS out_of_scope_assets,
                (SELECT COUNT(*) FROM assets a JOIN programs p ON p.id = a.program_id
                  WHERE a.scope = 'candidate' AND p.is_active = 1) AS candidate_assets,
                (SELECT COUNT(*) FROM web_pages) AS web_pages,
                (SELECT COUNT(*) FROM source_runs) AS source_runs,
                (SELECT COUNT(*) FROM source_runs WHERE status = 'failed') AS failed_source_runs
            """
        ).fetchone()
        by_platform = self.conn.execute(
            """
            SELECT COALESCE(platform, source, 'unknown') AS platform, COUNT(*) AS count
            FROM programs
            WHERE is_active = 1
            GROUP BY COALESCE(platform, source, 'unknown')
            ORDER BY count DESC, platform
            """
        ).fetchall()
        result = dict(row)
        result["programs_by_platform"] = [dict(platform_row) for platform_row in by_platform]
        return result

    def facets(self) -> dict[str, list[dict[str, Any]]]:
        sources = self.conn.execute(
            """
            SELECT source AS value, COUNT(DISTINCT program_id) AS count
            FROM program_sources WHERE is_active = 1
            GROUP BY source ORDER BY source COLLATE NOCASE
            """
        ).fetchall()
        platforms = self.conn.execute(
            """
            SELECT platform AS value, COUNT(*) AS count
            FROM programs WHERE is_active = 1 AND platform IS NOT NULL AND platform <> ''
            GROUP BY platform ORDER BY platform COLLATE NOCASE
            """
        ).fetchall()
        asset_types = self.conn.execute(
            """
            SELECT a.asset_type AS value, COUNT(*) AS count
            FROM assets a JOIN programs p ON p.id = a.program_id
            WHERE p.is_active = 1
            GROUP BY a.asset_type ORDER BY a.asset_type COLLATE NOCASE
            """
        ).fetchall()
        return {
            "sources": [dict(row) for row in sources],
            "platforms": [dict(row) for row in platforms],
            "asset_types": [dict(row) for row in asset_types],
        }

    def _upsert_program(self, program: Program, *, source_run_id: int | None = None) -> int:
        now = utc_now_iso()
        raw_json = json.dumps(program.raw, sort_keys=True, ensure_ascii=True)
        existing = self.conn.execute(
            "SELECT id, is_active FROM programs WHERE source = ? AND source_id = ?",
            (program.source, program.source_id),
        ).fetchone()
        self.conn.execute(
            """
            INSERT INTO programs(
                source, source_id, name, organization, platform, program_url, program_type,
                status, offers_bounty, description, reward, min_bounty,
                max_bounty, safe_harbor, allows_disclosure, last_updated,
                first_seen_at, last_seen_at, raw_json, is_active
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(source, source_id) DO UPDATE SET
                name = excluded.name,
                organization = excluded.organization,
                platform = excluded.platform,
                program_url = excluded.program_url,
                program_type = excluded.program_type,
                status = excluded.status,
                offers_bounty = excluded.offers_bounty,
                description = excluded.description,
                reward = excluded.reward,
                min_bounty = excluded.min_bounty,
                max_bounty = excluded.max_bounty,
                safe_harbor = excluded.safe_harbor,
                allows_disclosure = excluded.allows_disclosure,
                last_updated = excluded.last_updated,
                last_seen_at = excluded.last_seen_at,
                raw_json = excluded.raw_json,
                is_active = 1
            """,
            (
                program.source,
                program.source_id,
                program.name,
                program.organization,
                program.platform,
                program.program_url,
                program.program_type,
                program.status,
                _bool_to_int(program.offers_bounty),
                program.description,
                program.reward,
                program.min_bounty,
                program.max_bounty,
                program.safe_harbor,
                _bool_to_int(program.allows_disclosure),
                program.last_updated,
                now,
                now,
                raw_json,
            ),
        )
        row = self.conn.execute(
            "SELECT id FROM programs WHERE source = ? AND source_id = ?",
            (program.source, program.source_id),
        ).fetchone()
        event_type = "added" if existing is None else ("restored" if not existing["is_active"] else None)
        if event_type:
            self.conn.execute(
                """
                INSERT INTO program_events(program_id, source_run_id, event_type, observed_at)
                VALUES (?, ?, ?, ?)
                """,
                (row["id"], source_run_id, event_type, now),
            )
        return int(row["id"])

    def _sync_assets(
        self,
        program_id: int,
        program: Program,
        *,
        source_run_id: int | None = None,
    ) -> None:
        now = utc_now_iso()
        old_rows = self.conn.execute(
            """
            SELECT id, scope, raw_value, normalized_value, asset_type, first_seen_at
            FROM assets
            WHERE program_id = ?
            """,
            (program_id,),
        ).fetchall()
        old_by_key = {(row["scope"], row["raw_value"]): row for row in old_rows}
        new_by_key = {(asset.scope, asset.raw_value): asset for asset in program.assets}
        old_scopes_by_raw: dict[str, set[str]] = {}
        for row in old_rows:
            old_scopes_by_raw.setdefault(row["raw_value"], set()).add(row["scope"])

        for removed_key in sorted(old_by_key.keys() - new_by_key.keys()):
            removed = old_by_key[removed_key]
            event_key = (
                removed["scope"],
                removed["raw_value"],
                removed["normalized_value"],
                removed["asset_type"],
            )
            self._record_asset_event(program_id, source_run_id, "removed", event_key, now)
            self.conn.execute("DELETE FROM assets WHERE id = ?", (removed["id"],))

        for asset in program.assets:
            identity = (asset.scope, asset.raw_value)
            event_key = (asset.scope, asset.raw_value, asset.normalized_value, asset.asset_type)
            raw_json = json.dumps(asset.raw, sort_keys=True, ensure_ascii=True)
            existing = old_by_key.get(identity)
            if existing is not None:
                self.conn.execute(
                    """
                    UPDATE assets
                       SET asset_type = ?, normalized_value = ?, is_wildcard = ?,
                           eligible_for_bounty = ?, confidence = ?, notes = ?,
                           raw_json = ?, last_seen_at = ?
                     WHERE id = ?
                    """,
                    (
                        asset.asset_type,
                        asset.normalized_value,
                        int(asset.is_wildcard),
                        _bool_to_int(asset.eligible_for_bounty),
                        asset.confidence,
                        asset.notes,
                        raw_json,
                        now,
                        existing["id"],
                    ),
                )
                continue

            event_type = "scope_changed" if asset.raw_value in old_scopes_by_raw else "added"
            self._record_asset_event(program_id, source_run_id, event_type, event_key, now)
            self.conn.execute(
                """
                INSERT INTO assets(
                    program_id, scope, asset_type, raw_value, normalized_value, is_wildcard,
                    eligible_for_bounty, confidence, notes, raw_json, first_seen_at, last_seen_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    program_id,
                    asset.scope,
                    asset.asset_type,
                    asset.raw_value,
                    asset.normalized_value,
                    int(asset.is_wildcard),
                    _bool_to_int(asset.eligible_for_bounty),
                    asset.confidence,
                    asset.notes,
                    raw_json,
                    now,
                    now,
                ),
            )

    def _record_asset_event(
        self,
        program_id: int,
        source_run_id: int | None,
        event_type: str,
        key: tuple[str, str, str | None, str],
        observed_at: str,
    ) -> None:
        scope, raw_value, normalized_value, asset_type = key
        self.conn.execute(
            """
            INSERT INTO asset_events(
                program_id, source_run_id, event_type, scope, asset_type,
                raw_value, normalized_value, observed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                program_id,
                source_run_id,
                event_type,
                scope,
                asset_type,
                raw_value,
                normalized_value,
                observed_at,
            ),
        )
def _bool_to_int(value: bool | None) -> int | None:
    if value is None:
        return None
    return 1 if value else 0


def _like_pattern(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"
