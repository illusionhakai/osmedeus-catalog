"""Immutable, program-scoped scan input bundles; no scan submission."""
from __future__ import annotations

import hashlib
import io
import json
import zipfile
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from bugbounty_catalog.db import CatalogDB
from bugbounty_catalog.models import utc_now_iso
from bugbounty_catalog.normalize import asset_target_value, target_matches_asset
from bugbounty_catalog.query import AssetQuery, ProgramQuery


def snapshot_token(connection):
    """Provider commits plus catalog mutations identify the reviewed snapshot."""
    rows = list(connection.execute("SELECT source,MAX(id) FROM source_runs WHERE status='completed' GROUP BY source ORDER BY source"))
    # The aggregate also makes manual/test catalog updates invalidate previews.
    for table in ("programs", "assets", "asset_events", "program_events"):
        rows.append(tuple(connection.execute(f"SELECT COUNT(*),MAX(id) FROM {table}").fetchone()))
    return hashlib.sha256(_json([list(row) for row in rows])).hexdigest()

USAGE = """These are scan seeds, not workflow-enforced scope restrictions.
Run a reviewed workflow using: osmedeus run -f YOUR_WORKFLOW -T domains.txt
Use ips.txt and cidrs.txt only with workflows accepting those target types.
discovery-roots.txt contains explicitly requested wildcard conversions. An apex
in that file is NOT independently authorized by a wildcard declaration.
Review scope.json and exclusions.txt before scanning. Workflow enumeration,
redirects, DNS resolution and follow-up steps can leave the declared scope.
Osmedeus does not automatically consume this manifest or enforce exclusions.
No workflow is launched by creating or downloading this archive.
"""


def _json(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def excluded(value: str, asset: dict, rule: dict) -> bool:
    if asset["is_wildcard"] and rule["is_wildcard"] and rule["asset_type"] == "domain":
        root, denied = asset["normalized_value"], rule["normalized_value"]
        return root == denied or root.endswith("." + denied)
    return target_matches_asset(value, rule["normalized_value"], bool(rule["is_wildcard"]),
                                rule["asset_type"], scope="out_of_scope")


def create_bundle(db_path: Path, output: Path, request: dict) -> dict:
    query = AssetQuery.from_mapping({**request.get("filters", {}), "limit": 200})
    selected = set(request.get("asset_ids") or [])
    programs = {}
    program_rows = {}
    rows = []
    # All pages, metadata, and exclusions are read under a single SQLite snapshot.
    with CatalogDB(db_path) as store:
        store.conn.execute("BEGIN")
        fingerprint = snapshot_token(store.conn)
        allowed_programs = None
        selection = request.get("program_selection")
        if selection:
            program_query = ProgramQuery.from_mapping({**selection.get("filters", {}), "limit": 200})
            requested = set(selection["program_ids"]) if selection["mode"] == "ids" else None
            allowed_programs = set()
            while True:
                page = store.list_programs(program_query, program_ids=requested)
                allowed_programs.update(row["id"] for row in page.items)
                if not page.to_dict()["has_more"]:
                    break
                program_query = replace(program_query, offset=program_query.offset + len(page.items))
            if selection["mode"] == "ids":
                if requested - allowed_programs:
                    raise ValueError("Selected programs no longer match the filters")
                allowed_programs = requested
            if not allowed_programs:
                raise ValueError("No programs match the selection")
        rows = list(store.iter_export_assets(query, program_ids=allowed_programs,
                    asset_ids=selected or None, platform=request.get("platform")))
        for row in rows:
            pid = row["program_id"]
            program_rows.setdefault(pid, []).append(row)
            if pid not in programs:
                programs[pid] = store.get_program(pid)
        snapshots = [dict(row) for row in store.conn.execute(
            "SELECT * FROM source_runs WHERE status='completed' ORDER BY id DESC"
        )]
    if selected - {row["id"] for row in rows}:
        raise ValueError("Selected assets no longer match the filters; refresh the selection")
    if not rows:
        raise ValueError("No assets match this export")
    latest = {}
    for snapshot in snapshots:
        latest.setdefault(snapshot["source"], snapshot)
    files: dict[str, bytes] = {"README.txt": USAGE.encode()}
    manifest = {"schema_version": 1, "export_id": uuid4().hex,
                "created_at": utc_now_iso(), "request": request, "snapshot_token": fingerprint,
                "source_snapshots": latest, "programs": [], "checksums": {}}
    for pid, program in sorted(programs.items()):
        assert program is not None
        chosen = program_rows[pid]
        exclusions = [a for a in program["assets"] if a["scope"] == "out_of_scope"]
        buckets = {name: set() for name in ("domains", "ips", "cidrs", "discovery-roots")}
        omitted, conversions = [], []
        for asset in chosen:
            value = asset_target_value(asset)
            reason = None
            bucket = {"domain": "domains", "ip": "ips", "cidr": "cidrs"}.get(asset["asset_type"])
            if not program["is_active"] or program["status"].lower() not in {"active", "open", "public"}:
                reason = "inactive_program"
            elif asset["scope"] != "in_scope":
                reason = asset["scope"]
            elif any(excluded(value, asset, rule) for rule in exclusions):
                reason = "explicit_exclusion_or_network_overlap"
            elif bucket is None:
                reason = "unsupported_type"
            elif asset["is_wildcard"]:
                if request.get("convert_wildcards", False):
                    root = asset["normalized_value"]
                    # Do not export a discovery root that is itself explicitly excluded.
                    if any(target_matches_asset(root, rule["normalized_value"], bool(rule["is_wildcard"]),
                                                rule["asset_type"], scope="out_of_scope") for rule in exclusions):
                        reason = "discovery_root_excluded"
                    else:
                        buckets["discovery-roots"].add(root)
                        conversions.append({"asset_id": asset["id"], "from": value, "to": root,
                                            "purpose": "discovery_only", "apex_authorized": False})
                else:
                    reason = "wildcard_conversion_disabled"
            else:
                buckets[bucket].add(value)
            if reason:
                omitted.append({"asset_id": asset["id"], "value": value, "reason": reason})
        prefix = f"program-{pid}/"
        for name, values in buckets.items():
            files[prefix + name + ".txt"] = ("".join(v + "\n" for v in sorted(values))).encode()
        files[prefix + "exclusions.txt"] = ("".join(sorted({asset_target_value(a) + "\n" for a in exclusions}))).encode()
        files[prefix + "scope.json"] = _json(program["assets"])
        manifest["programs"].append({"id": pid, "name": program["name"],
            "program_url": program["program_url"], "source_records": program["source_records"],
            "policy_links": program.get("policy_links", []),
            "exclusions": [{"value": asset_target_value(a), "asset_type": a["asset_type"], "is_wildcard": bool(a["is_wildcard"])} for a in exclusions],
            "directory": prefix, "counts": {k: len(v) for k, v in buckets.items()},
            "omitted": omitted, "conversions": conversions})
    manifest["checksums"] = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    files["manifest.json"] = _json(manifest)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    output.mkdir(parents=True, exist_ok=True)
    export_id = manifest["export_id"]
    temp = output / f"{export_id}.tmp"
    temp.write_bytes(buffer.getvalue())
    temp.replace(output / f"{export_id}.zip")
    return manifest


def preview_bundle(path: Path) -> dict:
    with zipfile.ZipFile(path) as archive:
        return json.loads(archive.read("manifest.json"))
