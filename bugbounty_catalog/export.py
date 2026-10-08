from __future__ import annotations

import csv
import io
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Iterable, TextIO

from .normalize import asset_target_value


def write_rows(
    rows: Iterable[sqlite3.Row | dict[str, Any]],
    *,
    output: str | None,
    fmt: str,
    value_field: str | None = None,
) -> None:
    handle: TextIO
    close = False
    if output:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("w", encoding="utf-8", newline="")
        close = True
    else:
        handle = sys.stdout
    try:
        handle.write(serialize_rows(rows, fmt=fmt, value_field=value_field))
    finally:
        if close:
            handle.close()


def serialize_rows(
    rows: Iterable[sqlite3.Row | dict[str, Any]],
    *,
    fmt: str,
    value_field: str | None = None,
) -> str:
    """Serialize export rows without tying callers to files or stdout."""

    materialized = [dict(row) for row in rows]
    handle = io.StringIO(newline="")
    if fmt == "json":
        json.dump(materialized, handle, indent=2, sort_keys=True)
        handle.write("\n")
    elif fmt == "csv":
        _write_csv(materialized, handle)
    elif fmt == "txt":
        field = value_field or "normalized_value"
        seen: set[str] = set()
        for row in materialized:
            value = row.get(field) or row.get("raw_value")
            text = str(value).strip() if value is not None else ""
            if field == "normalized_value" and text:
                text = asset_target_value(row)
            if text and text not in seen:
                seen.add(text)
                handle.write(text + "\n")
    else:
        raise ValueError(f"Unknown export format: {fmt}")
    return handle.getvalue()


def _write_csv(rows: list[dict[str, Any]], handle: TextIO) -> None:
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    writer = csv.DictWriter(handle, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
