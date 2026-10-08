"""CLI pipeline: optional refresh -> immutable export -> upload -> delivery receipt."""
import argparse
import getpass
import json
import os
import re
import sys
import time

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--program-id", type=int)
    choice.add_argument("--export-id", help="Resume an existing immutable export")
    choice.add_argument("--all-programs", action="store_true", help="Explicitly deliver every program matching source/platform")
    parser.add_argument("--gateway", default="http://localhost:8080")
    parser.add_argument("--username", help="Prompt for an engine login password instead of using OSMEDEUS_TOKEN")
    parser.add_argument("--source")
    parser.add_argument("--platform")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--convert-wildcards", action="store_true")
    parser.add_argument("--retry", action="store_true", help="Explicitly retry unconfirmed files (duplicates may remain)")
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args()
    if args.program_id is not None and args.program_id < 1:
        parser.error("--program-id must be positive; use --all-programs explicitly for bulk delivery")
    if args.timeout < 1:
        parser.error("--timeout must be positive")
    if args.export_id and not re.fullmatch(r"[a-f0-9]{32}", args.export_id):
        parser.error("--export-id must be a 32-character export identifier")
    if args.export_id and (args.refresh or args.source or args.platform or args.convert_wildcards):
        parser.error("An existing export is immutable; refresh/filter options require a new export")
    if args.retry and not args.export_id:
        parser.error("--retry requires --export-id")
    token = os.getenv("OSMEDEUS_TOKEN", "")
    with httpx.Client(base_url=args.gateway.rstrip("/"), timeout=60, follow_redirects=False, trust_env=False) as client:
        if args.username:
            response = client.post("/osm/api/login", json={"username": args.username, "password": getpass.getpass("Osmedeus password: ")})
            response.raise_for_status()
            token = response.json()["token"]
        if not token:
            parser.error("Use --username or provide OSMEDEUS_TOKEN in the process environment")
        client.headers["Authorization"] = "Bearer " + token
        def call(method, path, **kwargs):
            response = client.request(method, "/catalog-api/v1" + path, **kwargs)
            response.raise_for_status()
            return response.json()
        deadline = time.monotonic() + args.timeout
        if args.refresh:
            schedule = call("GET", "/schedule")
            job = call("POST", "/refresh", json={"source": args.source or "all", "include_vdp": schedule["include_vdp"]})["job_id"]
            while True:
                record = next(item for item in call("GET", "/jobs")["items"] if item["id"] == job)
                if (record["parameters"]["source"] not in ("all", args.source or "all") or
                    record["parameters"]["include_vdp"] != schedule["include_vdp"]):
                    raise RuntimeError("A different refresh is running; wait for it and retry this pipeline")
                status = record["status"]
                if status != "running":
                    if status != "completed":
                        raise RuntimeError("Refresh was not fully successful; no new inputs were delivered")
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError("Timed out waiting for catalog refresh")
                time.sleep(2)
        export_id = args.export_id
        if not export_id:
            filters = {"scope": "in_scope", "active": True}
            if args.program_id:
                filters["program_id"] = args.program_id
            if args.source:
                filters["source"] = args.source
            export_id = call("POST", "/exports", json={"filters": filters, "platform": args.platform,
                "convert_wildcards": args.convert_wildcards})["export_id"]
        print("Delivery export: " + export_id, file=sys.stderr)
        call("POST", f"/exports/{export_id}/deliver", json={"retry": args.retry})
        while True:
            receipt = call("GET", f"/exports/{export_id}/delivery")
            if receipt["status"] != "uploading":
                print(json.dumps(receipt, indent=2))
                return 0 if receipt["status"] == "ready" else 1
            if time.monotonic() >= deadline:
                raise RuntimeError("Delivery continues on the server. Inspect/resume export " + export_id)
            time.sleep(2)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (httpx.HTTPError, RuntimeError, KeyError) as exc:
        print("Pipeline failed: " + (str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__), file=sys.stderr)
        sys.exit(1)
