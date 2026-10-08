"""Deliver immutable inputs through Osmedeus's public upload API, never submit runs."""
from __future__ import annotations

import hashlib
import json
import re
import shlex
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

import httpx

from bugbounty_catalog.models import utc_now_iso
from .state import State


class DeliveryBusy(Exception):
    pass


def bundle_files(path):
    """Validate the whole archive before any upstream write, then build a fixed upload list."""
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        if manifest.get("schema_version") != 1 or manifest.get("export_id") != path.stem:
            raise ValueError("Unsupported or mismatched export manifest")
        if len(set(archive.namelist())) != len(archive.namelist()):
            raise ValueError("Duplicate export members")
        payloads = {}
        for name, digest in manifest["checksums"].items():
            content = archive.read(name)
            if hashlib.sha256(content).hexdigest() != digest:
                raise ValueError("Export checksum mismatch")
            payloads[name] = content
        result = []
        for program in manifest["programs"]:
            prefix = f"program-{program['id']}/"
            for kind in ("domains", "ips", "cidrs", "discovery-roots", "scope", "exclusions"):
                name = prefix + kind + (".json" if kind == "scope" else ".txt")
                content = payloads[name]
                target = kind not in ("scope", "exclusions")
                if target and not content:
                    continue
                result.append(({"member": name, "program_id": program["id"],
                    "program_name": program["name"], "kind": kind, "is_target_file": target}, content))
        if not any(item[0]["is_target_file"] for item in result):
            raise ValueError("No usable targets: review omissions or explicitly enable wildcard discovery roots")
        result.extend([({"member": "README.txt", "kind": "instructions", "is_target_file": False}, payloads["README.txt"]),
                       ({"member": "manifest.json", "kind": "manifest", "is_target_file": False}, archive.read("manifest.json"))])
    return result


class DeliveryManager:
    def __init__(self, state: State, engine: str, transport=None):
        self.state, self.engine, self.transport = state, engine.rstrip("/"), transport
        url = urlsplit(self.engine)
        if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.path or url.query or url.fragment:
            raise ValueError("OSMEDEUS_URL must be an HTTP(S) origin without credentials")
        self.prefix = "delivery:" + hashlib.sha256(self.engine.encode()).hexdigest() + ":"
        self.lock = threading.Lock()
        self.active = None
        self.pool = ThreadPoolExecutor(max_workers=1)
        for receipt in self.list():
            if receipt["status"] == "uploading":
                receipt.update(status="interrupted", error="Service restarted. An unconfirmed remote file may exist; retry explicitly.", completed_at=utc_now_iso())
                self.save(receipt)

    def save(self, receipt):
        self.state.set(self.prefix + receipt["export_id"], receipt)

    def get(self, export_id):
        return self.state.get(self.prefix + export_id)

    def list(self):
        with self.state.connect() as db:
            return [json.loads(row[0]) for row in db.execute(
                "SELECT value FROM settings WHERE key LIKE ? ORDER BY rowid DESC", (self.prefix + "%",))]

    def submit(self, path: Path, authorization: str, retry=False):
        with self.lock:
            receipt = self.get(path.stem)
            if receipt and (receipt["status"] in ("ready", "uploading") or not retry):
                return receipt
            if self.active:
                raise DeliveryBusy("Another delivery is running; wait for it to finish")
            files = bundle_files(path)
            if receipt is None:
                receipt = {"export_id": path.stem, "engine": self.engine, "created_at": utc_now_iso(),
                           "snapshot_at": json.loads(files[-1][1])["created_at"],
                           "files": [], "status": "uploading", "total_files": len(files), "attempts": 0}
            receipt.update(status="uploading", completed_at=None, error=None, attempts=receipt["attempts"] + 1)
            self.save(receipt)
            self.active = path.stem
            # Tokens exist only in this in-memory task, never in the persisted receipt.
            self.pool.submit(self._run, receipt, files, authorization)
            return receipt

    def _run(self, receipt, files, authorization):
        try:
            with httpx.Client(timeout=30, follow_redirects=False, trust_env=False, transport=self.transport) as client:
                completed = {item["member"] for item in receipt["files"]}
                for info, content in files:
                    if info["member"] in completed:
                        continue
                    filename = "catalog-" + receipt["export_id"] + "-" + info["member"].replace("/", "-")
                    receipt["pending_member"] = info["member"]
                    self.save(receipt)  # Crash after upstream acceptance remains explicitly unconfirmed.
                    response = client.post(self.engine + "/osm/api/upload-file",
                        headers={"Authorization": authorization},
                        files={"file": (filename, content, "application/octet-stream")})
                    if response.status_code != 200:
                        raise ValueError(f"Osmedeus upload returned HTTP {response.status_code}")
                    body = response.json()
                    remote = body.get("path") if isinstance(body, dict) else None
                    lines = content.count(b"\n") + int(bool(content) and not content.endswith(b"\n"))
                    if (not isinstance(remote, str) or not remote.startswith("/") or
                        any(ord(c) < 32 for c in remote) or ".." in PurePosixPath(remote).parts or
                        not re.fullmatch(r"\d+_" + re.escape(filename), PurePosixPath(remote).name) or
                        body.get("filename") != PurePosixPath(remote).name or
                        type(body.get("size")) is not int or body["size"] != len(content) or
                        type(body.get("lines")) is not int or body["lines"] != lines):
                        raise ValueError("Unexpected Osmedeus upload response; delivery is not confirmed")
                    item = {**info, "path": remote, "size": len(content), "lines": lines,
                            "sha256": hashlib.sha256(content).hexdigest()}
                    if info["is_target_file"]:
                        item["cli_template"] = "osmedeus run -f YOUR_REVIEWED_WORKFLOW -T " + shlex.quote(remote)
                        item["run_request_template"] = {"flow": "YOUR_REVIEWED_WORKFLOW", "target_file": remote}
                    receipt["files"].append(item)
                    receipt["pending_member"] = None
                    self.save(receipt)
            receipt.update(status="ready", completed_at=utc_now_iso())
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            # Never persist upstream response bodies or HTTP exceptions containing credentials.
            error = str(exc) if isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError) else "Upload failed or response was invalid"
            receipt.update(status="failed", error=error, completed_at=utc_now_iso())
        except Exception:
            receipt.update(status="failed", error="Delivery interrupted by an internal error", completed_at=utc_now_iso())
        finally:
            try:
                self.save(receipt)
            finally:
                with self.lock:
                    self.active = None

    def close(self):
        self.pool.shutdown(wait=True)
