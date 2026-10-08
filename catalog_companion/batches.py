"""Persistent, explicit scan batches. Engine side effects use public APIs only."""
import hashlib
import json
import threading
from datetime import datetime, timezone
from uuid import uuid4

from bugbounty_catalog.db import CatalogDB
from bugbounty_catalog.models import utc_now_iso
from .delivery import DeliveryBusy
from .engine import Engine, EngineError
from .exports import create_bundle, snapshot_token

SCAN_PROFILES = {
    "automatic": {"domains": ("enum-subdomain", "module"), "ips": ("cidr", "flow"),
                  "cidrs": ("cidr", "flow"), "discovery-roots": ("enum-subdomain", "module")},
    "full": {"domains": ("domain-standard", "flow"), "ips": ("cidr", "flow"),
             "cidrs": ("cidr", "flow"), "discovery-roots": ("enum-subdomain", "module")},
}


class BatchConflict(RuntimeError):
    pass


class BatchManager:
    def __init__(self, state, data, delivery, transport=None):
        self.state, self.data, self.delivery = state, data, delivery
        self.engine = Engine(delivery.engine, transport)
        self.prefix = "batch:" + hashlib.sha256(delivery.engine.encode()).hexdigest() + ":"
        self.lock = threading.RLock()
        self.tokens = {}  # Never persisted; restart requires authenticated resume.
        self.cancel_flags = set()
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        for batch in self.list():
            if batch["status"] in ("completed", "cancelled", "partial", "prepared"):
                continue
            for item in batch["items"]:
                if item["status"] == "submitting":
                    item.update(status="uncertain", error="Service restarted during submission; inspect engine jobs")
            batch.update(status="paused", error="Service restarted; authenticate and resume monitoring/dispatch")
            self.record(batch, "restart")

    def save(self, batch):
        batch["updated_at"] = utc_now_iso()
        self.state.set(self.prefix + batch["id"], batch)

    def record(self, batch, action):
        batch["history"].append({"at": utc_now_iso(), "action": action})
        self.save(batch)

    def get(self, batch_id):
        batch = self.state.get(self.prefix + batch_id)
        if batch is None:
            raise KeyError("Batch not found")
        return batch

    def list(self):
        with self.state.connect() as db:
            return [json.loads(row[0]) for row in db.execute(
                "SELECT value FROM settings WHERE key LIKE ? ORDER BY rowid DESC", (self.prefix + "%",))]

    def fingerprint(self):
        with CatalogDB(self.data / "catalog.sqlite") as store:
            store.conn.execute("BEGIN")
            return snapshot_token(store.conn)

    def view(self, batch):
        return {**batch, "catalog_changed": batch["manifest"]["snapshot_token"] != self.fingerprint(),
                "delivery": self.delivery.get(batch["export_id"]),
                "credentials_available": batch["id"] in self.tokens}

    def prepare(self, payload, authorization):
        request = payload.model_dump(mode="json")
        organization = self.engine.organization(authorization, request["org_uuid"])
        selection = request["selection"]
        filters = selection["filters"]
        manifest = create_bundle(self.data / "catalog.sqlite", self.data / "exports", {
            "program_selection": selection, "asset_ids": selection["asset_ids"],
            "filters": {"scope": filters.get("scope"), "source": filters.get("source"),
                "kind": filters.get("kind"), "target_text": filters.get("scope_text"), "active": filters.get("active", True)},
            "convert_wildcards": request["convert_wildcards"]})
        if request["scan_mode"]:
            present = {kind for program in manifest["programs"] for kind, count in program["counts"].items() if count}
            profile = SCAN_PROFILES[request["scan_mode"]]
            request["workflows"] = {kind: {"name": profile[kind][0], "kind": profile[kind][1], "params": {}}
                                    for kind in sorted(present)}
        for kind, mapping in request["workflows"].items():
            if mapping:
                try:
                    details = self.engine.validate_mapping(authorization, mapping)
                except ValueError as exc:
                    raise ValueError(f"{kind} workflow {mapping['name']}: {exc}") from exc
                except EngineError as exc:
                    raise EngineError(f"{kind} workflow {mapping['name']}: {exc}", auth=exc.auth) from exc
                defaults = {p["name"]: str(p["default"]) for p in details["params"] if p.get("default") not in (None, "")}
                mapping["params"] = {**defaults, **mapping["params"]}
        items = []
        for program in manifest["programs"]:
            for kind, count in program["counts"].items():
                if not count:
                    continue
                if kind not in request["workflows"]:
                    raise ValueError(f"Map or explicitly exclude {kind}")
                mapping = request["workflows"][kind]
                if mapping is None:
                    continue
                items.append({"id": uuid4().hex, "program_id": program["id"], "program_name": program["name"],
                    "kind": kind, "member": program["directory"] + kind + ".txt", "target_count": count,
                    "workflow": mapping, "status": "pending", "runs": [], "attempts": [], "error": None})
        if not items:
            raise ValueError("No scan-eligible inputs have a mapped workflow; inspect omissions or discovery-root options")
        batch = {"id": uuid4().hex, "engine": self.delivery.engine, "export_id": manifest["export_id"],
            "created_at": utc_now_iso(), "status": "prepared", "request": request,
            "organization": organization, "manifest": manifest, "items": items, "history": [],
            "error": None, "cancel_requested": False}
        with self.lock:
            self.record(batch, "prepared")
        return self.view(batch)

    def action(self, batch_id, action, payload, authorization):
        if not payload.confirmed:
            raise ValueError("Explicit scope/action confirmation is required")
        if action == "cancel":
            self.cancel_flags.add(batch_id)  # Stops dispatch even while an HTTP call is in flight.
        with self.lock:
            batch = self.get(batch_id)
            if action == "start":
                if batch["status"] != "prepared":
                    return self.view(batch)  # Repeated start can never duplicate a run.
                if self.fingerprint() != batch["manifest"]["snapshot_token"]:
                    raise BatchConflict("Catalog changed; generate and confirm a new preview")
                self.engine.organization(authorization, batch["organization"]["uuid"])
                for mapping in batch["request"]["workflows"].values():
                    if mapping:
                        self.engine.validate_mapping(authorization, mapping)
                batch.update(status="uploading", error=None)
            elif action == "resume":
                if batch["status"] not in ("paused", "cancel_requested"):
                    raise BatchConflict("Only paused or cancelling batches can resume")
                # Monitoring can resume with unresolved entries, dispatch cannot.
                unresolved = any(item["status"] in ("failed", "uncertain") for item in batch["items"])
                batch.update(status="cancel_requested" if batch["cancel_requested"] else "paused" if unresolved else "uploading", error=None)
                if payload.retry_delivery:
                    self.delivery.submit(self.data / "exports" / (batch["export_id"] + ".zip"), authorization, True)
            elif action == "cancel":
                if batch["status"] in ("completed", "cancelled", "partial"):
                    return self.view(batch)
                batch.update(cancel_requested=True, status="cancel_requested", error=None)
                for item in batch["items"]:
                    if item["status"] == "pending":
                        item["status"] = "skipped"
            else:
                raise ValueError("Unknown batch action")
            self.tokens[batch_id] = authorization
            self.record(batch, action)
            return self.view(batch)

    def item_action(self, batch_id, item_id, action, payload):
        if not payload.confirmed:
            raise ValueError("Explicit action confirmation required")
        with self.lock:
            batch = self.get(batch_id)
            if batch["status"] != "paused" or batch["cancel_requested"]:
                raise BatchConflict("Pause the batch before resolving failed entries")
            item = next((item for item in batch["items"] if item["id"] == item_id), None)
            if not item or item["status"] not in ("failed", "uncertain"):
                raise BatchConflict("Only failed or uncertain entries can be resolved")
            if item["status"] == "uncertain" and not payload.acknowledge_uncertain:
                raise BatchConflict("Inspect engine jobs and acknowledge that an untracked scan may exist")
            if action not in ("retry", "skip"):
                raise ValueError("Unknown item action")
            item["attempts"].append({key: item.get(key) for key in ("job_id", "status", "runs", "error", "submitted_at")})
            for key in ("job_id", "submitted_at", "accepted_count", "tracking_error", "cancellation"):
                item.pop(key, None)
            item.update(status="pending" if action == "retry" else "skipped", runs=[], error=None)
            self.record(batch, action + ":" + item_id)
            return self.view(batch)

    def observe(self, batch, item, authorization):
        try:
            runs = self.engine.job(authorization, item["job_id"])
        except EngineError as exc:
            # The engine creates run records lazily; initial 404 is not proof of rejection.
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(item["submitted_at"])).total_seconds()
            if "HTTP 404" in str(exc) and age < 30:
                return
            raise
        item["runs"] = runs
        item.pop("tracking_error", None)
        if len({run["run_uuid"] for run in runs}) != len(runs):
            raise EngineError("Duplicate run identifiers in job status")
        terminal = {"completed", "failed", "cancelled"}
        if batch["cancel_requested"] or batch["id"] in self.cancel_flags:
            cancellation = item.setdefault("cancellation", {})
            for run in runs:
                if run["status"] in ("pending", "running") and run["run_uuid"] not in cancellation:
                    result = self.engine.cancel(authorization, run["run_uuid"])
                    cancellation[run["run_uuid"]] = {key: result.get(key) for key in ("kill_method", "processes_terminated", "note")}
            item["status"] = "cancel_requested"
        if any(run["status"] == "failed" for run in runs) and not batch["cancel_requested"]:
            batch.update(status="paused", error="A scan failed; review before dispatching more work")
        # Never trust the upstream aggregate before all accepted targets materialize.
        if len(runs) >= item["accepted_count"] and all(run["status"] in terminal for run in runs):
            if batch["cancel_requested"]:
                item["status"] = "cancelled" if any(run["status"] == "cancelled" for run in runs) else "completed"
            else:
                item["status"] = "completed" if all(run["status"] == "completed" for run in runs) else "failed"
                if item["status"] == "failed":
                    batch.update(status="paused", error="A scan failed or was cancelled; review the batch")

    def tick(self):
        with self.lock:
            for saved in self.list():
                batch_id = saved["id"]
                authorization = self.tokens.get(batch_id)
                if not authorization or saved["status"] in ("prepared", "completed", "cancelled", "partial"):
                    continue
                batch = self.get(batch_id)
                try:
                    self._advance(batch, authorization)
                except EngineError as exc:
                    batch.update(status="paused", error=str(exc))
                    if exc.auth:
                        self.tokens.pop(batch_id, None)
                except (ValueError, DeliveryBusy):
                    batch.update(status="paused", error="Delivery or workflow validation failed; review and resume explicitly")
                except Exception:
                    # A dispatch intent without a confirmed response must never become retryable pending work.
                    for item in batch["items"]:
                        if item["status"] == "submitting":
                            item.update(status="uncertain", error="Submission interrupted; inspect engine jobs")
                    batch.update(status="paused", error="Control center interrupted; review before resuming")
                self.save(batch)
                if batch["status"] in ("completed", "cancelled", "partial"):
                    self.tokens.pop(batch_id, None)
                    self.cancel_flags.discard(batch_id)

    def _advance(self, batch, authorization):
        for item in batch["items"]:
            if item["status"] in ("running", "cancel_requested"):
                try:
                    self.observe(batch, item, authorization)
                except EngineError as exc:
                    item["tracking_error"] = str(exc)
                    batch.update(status="paused", error=str(exc))
                    if exc.auth:
                        raise
        if batch["cancel_requested"] or batch["id"] in self.cancel_flags:
            batch["cancel_requested"] = True
            for item in batch["items"]:
                if item["status"] == "pending":
                    item["status"] = "skipped"
            batch["status"] = "cancel_requested"
            if not any(item["status"] in ("running", "cancel_requested", "submitting", "uncertain") for item in batch["items"]):
                batch["status"] = "cancelled"
            return
        if batch["status"] == "paused":
            return
        receipt = self.delivery.get(batch["export_id"])
        if receipt is None:
            self.delivery.submit(self.data / "exports" / (batch["export_id"] + ".zip"), authorization)
            return
        if receipt["status"] == "uploading":
            return
        if receipt["status"] != "ready":
            batch.update(status="paused", error="Delivery failed/interrupted; explicitly retry delivery")
            return
        batch["status"] = "running"
        active = sum(item["status"] == "running" for item in batch["items"])
        for item in batch["items"]:
            if active >= batch["request"]["parallelism"] or batch["id"] in self.cancel_flags:
                break
            if item["status"] != "pending":
                continue
            file = next((file for file in receipt["files"] if file["member"] == item["member"] and file["is_target_file"]), None)
            if file is None or file["lines"] != item["target_count"]:
                raise ValueError("Delivery does not match reviewed input")
            mapping = item["workflow"]
            self.engine.validate_mapping(authorization, mapping)
            item.update(status="submitting", submitted_at=utc_now_iso())
            self.record(batch, "submit-intent:" + item["id"])
            try:
                result = self.engine.submit(authorization, {mapping["kind"]: mapping["name"], "target_file": file["path"],
                    "params": {**mapping["params"], "org_uuid": batch["organization"]["uuid"]},
                    "concurrency": batch["request"]["target_concurrency"]})
            except EngineError as exc:
                item.update(status="uncertain" if exc.uncertain else "failed", error=str(exc))
                raise
            item.update(status="running", job_id=result["job_id"], accepted_count=result["target_count"])
            self.record(batch, "accepted:" + item["id"])
            if result["target_count"] != item["target_count"]:
                # Acceptance cannot be undone or retried safely; keep monitoring it.
                batch.update(status="paused", error="Engine accepted a different target count; inspect the accepted job before resuming")
                return
            active += 1
        if all(item["status"] in ("completed", "skipped") for item in batch["items"]):
            batch["status"] = "completed" if all(item["status"] == "completed" for item in batch["items"]) else "partial"
            self.tokens.pop(batch["id"], None)

    def _loop(self):
        while not self.stop.wait(3):
            self.tick()

    def close(self):
        self.stop.set()
        if self.thread.is_alive():
            self.thread.join(timeout=60)
        self.tokens.clear()
