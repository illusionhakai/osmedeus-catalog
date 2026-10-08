"""Companion-owned control database and serialized background refreshes."""
from __future__ import annotations

import json
import sqlite3
import threading
import logging
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from bugbounty_catalog.application import CatalogService
from bugbounty_catalog.models import utc_now_iso


class State:
    def __init__(self, path: Path):
        self.path = path
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, status TEXT, created_at TEXT,
                    completed_at TEXT, result TEXT, parameters TEXT);
                CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
            """)
            db.execute("UPDATE jobs SET status='failed',completed_at=?,result=? WHERE status='running'",
                       (utc_now_iso(), json.dumps({"error": "Service interrupted during refresh"})))
            db.execute("INSERT OR IGNORE INTO settings VALUES ('schedule', ?)",
                       (json.dumps({"enabled": True, "time": "03:00", "timezone": "America/Sao_Paulo", "include_vdp": False}),))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, key, default=None):
        with self.connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def set(self, key, value):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, json.dumps(value)))

    def jobs(self):
        with self.connect() as db:
            return [dict(row) | {"result": json.loads(row["result"] or "null"),
                                "parameters": json.loads(row["parameters"])} for row in
                    db.execute("SELECT * FROM jobs ORDER BY created_at DESC, rowid DESC LIMIT 50")]


class RefreshManager:
    def __init__(self, service: CatalogService, state: State):
        self.service, self.state = service, state
        self.lock = threading.Lock()
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.active = None
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._scheduler, daemon=True)

    def submit(self, source="all", include_vdp=False):
        with self.lock:
            if self.active:
                return self.active
            job = uuid4().hex
            with self.state.connect() as db:
                db.execute("INSERT INTO jobs VALUES (?, 'running', ?, NULL, NULL, ?)",
                           (job, utc_now_iso(), json.dumps({"source": source, "include_vdp": include_vdp})))
            self.active = job
            self.pool.submit(self._run, job, source, include_vdp)
            return job

    def _run(self, job, source, include_vdp):
        try:
            result = self.service.refresh(source, include_vdp=include_vdp)
            status = result["status"]
        except Exception as exc:
            result, status = {"error": str(exc)}, "failed"
        finally:
            with self.state.connect() as db:
                db.execute("UPDATE jobs SET status=?,completed_at=?,result=? WHERE id=?",
                           (status, utc_now_iso(), json.dumps(result), job))
            with self.lock:
                self.active = None

    def tick(self, now=None):
        schedule = self.state.get("schedule")
        now = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(schedule["timezone"]))
        day = now.date().isoformat()
        if schedule["enabled"] and now.strftime("%H:%M") >= schedule["time"] and self.state.get("last_scheduled_day") != day:
            self.submit(include_vdp=schedule["include_vdp"])
            self.state.set("last_scheduled_day", day)

    def _scheduler(self):
        while not self.stop.is_set():
            try:
                self.tick()
            except Exception:
                logging.exception("Scheduled refresh could not be started; retrying on the next tick")
            self.stop.wait(30)

    def close(self):
        self.stop.set()
        if self.thread.is_alive():
            self.thread.join(timeout=5)
        self.pool.shutdown(wait=True)
