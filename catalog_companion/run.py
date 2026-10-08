"""Prepare, or explicitly submit, one delivered program/file to an Osmedeus workflow."""
import argparse
import json
import re
import shlex
import sys
from pathlib import PurePosixPath

import httpx

from .cli import connection_arguments, entrypoint, gateway, get_json


def make_plan(receipt, export_id, program_id, target_kind, workflow, workflow_kind):
    if receipt.get("export_id") != export_id or receipt.get("status") != "ready":
        raise RuntimeError("Delivery must be ready before workflow handoff")
    files = receipt.get("files")
    if not isinstance(files, list):
        raise RuntimeError("Invalid delivery receipt")
    matches = [item for item in files if item.get("is_target_file") is True and
               item.get("kind") == target_kind and
               (program_id is None or item.get("program_id") == program_id)]
    if len(matches) != 1:
        raise RuntimeError("Select exactly one delivered program/file with --program-id and --target-kind")
    item = matches[0]
    path = item.get("path")
    if (not isinstance(path, str) or not path.startswith("/") or
            ".." in PurePosixPath(path).parts or any(ord(c) < 32 for c in path) or
            type(item.get("lines")) is not int or item["lines"] < 1):
        raise RuntimeError("Invalid delivered target file")
    request = {workflow_kind: workflow, "target_file": path}
    return {"export_id": export_id, "snapshot_at": receipt.get("snapshot_at"),
            "program_id": item.get("program_id"), "program_name": item.get("program_name"),
            "member": item.get("member"), "target_count": item["lines"],
            "sha256": item.get("sha256"), "request": request,
            "cli": "osmedeus run " + ("-f " if workflow_kind == "flow" else "-m ") +
                   shlex.quote(workflow) + " -T " + shlex.quote(path),
            "warning": "Review current policy and workflow scope enforcement. Snapshot age is not authorization."}


def submit(client, request):
    # There is no upstream idempotency contract. Never automatically retry POST.
    uncertain = "Submission outcome unknown; inspect Osmedeus Scans/jobs before trying again. No retry was made."
    try:
        response = client.post("/osm/api/runs", json=request)
    except httpx.HTTPError as exc:
        raise RuntimeError(uncertain) from exc
    if 400 <= response.status_code < 500:
        raise RuntimeError(f"Osmedeus rejected the run (HTTP {response.status_code}); no retry was made")
    if response.status_code != 202:
        raise RuntimeError(uncertain)
    try:
        body = response.json()
    except ValueError as exc:
        raise RuntimeError(uncertain) from exc
    if (not isinstance(body, dict) or not isinstance(body.get("job_id"), str) or
            not body["job_id"] or body.get("status") != "queued"):
        raise RuntimeError(uncertain)
    return body


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    connection_arguments(parser)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--export-id")
    choice.add_argument("--receipt-stdin", action="store_true", help="Read ship JSON, then re-fetch the trusted receipt by export ID")
    choice.add_argument("--list-workflows", action="store_true")
    workflow = parser.add_mutually_exclusive_group()
    workflow.add_argument("--flow")
    workflow.add_argument("--module")
    parser.add_argument("--program-id", type=int)
    parser.add_argument("--target-kind", choices=("domains", "ips", "cidrs", "discovery-roots"))
    parser.add_argument("--search", default="")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--request-only", action="store_true", help="Print only API request JSON for piping; does not submit")
    parser.add_argument("--submit", action="store_true", help="Start an actual run; otherwise only prepare a plan")
    parser.add_argument("--acknowledge-scope", action="store_true", help="Confirm current authorization and workflow scope review")
    parser.add_argument("--allow-discovery-roots", action="store_true", help="Explicitly use wildcard-derived discovery seeds, not apex authorization")
    args = parser.parse_args()
    name = args.flow or args.module
    if not 1 <= args.limit <= 200 or args.offset < 0:
        parser.error("--limit must be 1..200 and --offset must be nonnegative")
    if args.list_workflows:
        if name or args.program_id or args.target_kind or args.submit or args.request_only or args.acknowledge_scope or args.allow_discovery_roots:
            parser.error("Workflow listing cannot include handoff options")
    else:
        if not name or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name):
            parser.error("Choose a --flow or --module name (letters, numbers, dot, underscore, hyphen)")
        if not args.target_kind:
            parser.error("Choose --target-kind explicitly")
        if args.program_id is not None and args.program_id < 1:
            parser.error("--program-id must be positive")
        if args.submit and (not args.acknowledge_scope or args.request_only):
            parser.error("--submit requires --acknowledge-scope and cannot use --request-only")
        if args.target_kind == "discovery-roots" and not args.allow_discovery_roots:
            parser.error("Discovery seeds require --allow-discovery-roots; apex scope is not implied")
        if args.receipt_stdin:
            # Only the ID is trusted as a selector. Never use a supplied filesystem path.
            body = json.load(sys.stdin)
            args.export_id = body.get("export_id") if isinstance(body, dict) else None
        if not isinstance(args.export_id, str) or not re.fullmatch(r"[a-f0-9]{32}", args.export_id):
            parser.error("A valid 32-character export ID is required")
    with gateway(args) as client:
        if args.list_workflows:
            print(json.dumps(get_json(client, "GET", "/osm/api/workflows", params={
                "search": args.search, "limit": args.limit, "offset": args.offset}), indent=2))
            return 0
        receipt = get_json(client, "GET", f"/catalog-api/v1/exports/{args.export_id}/delivery")
        kind = "flow" if args.flow else "module"
        plan = make_plan(receipt, args.export_id, args.program_id, args.target_kind, name, kind)
        details = get_json(client, "GET", f"/osm/api/workflows/{name}", params={"json": "true"})
        if details.get("name") != name or details.get("kind") != kind:
            raise RuntimeError("Workflow name/kind does not match the engine's workflow")
        print(plan["warning"], file=sys.stderr)
        if args.submit:
            # Print identity BEFORE the irreversible operation, even if its response is lost.
            print(f"Submitting export {args.export_id}, {plan['member']}, {kind} {name}; no automatic retry", file=sys.stderr)
            plan["submission"] = submit(client, plan["request"])
            plan["status"] = "accepted"  # Acceptance is not scan completion.
        else:
            plan["status"] = "prepared"
        print(json.dumps(plan["request"] if args.request_only else plan, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(entrypoint(main))
