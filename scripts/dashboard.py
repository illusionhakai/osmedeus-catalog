"""Build overlay in a disposable checkout, never in the source repository."""
import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from deployment import source_fingerprint

ROOT = Path(__file__).resolve().parents[1]


def run(*args, cwd=None):
    subprocess.run(args, cwd=cwd, check=True)


def apply_overlay(checkout, dashboard):
    if not checkout.resolve().is_relative_to((ROOT / "build").resolve()):
        raise RuntimeError("Refusing to modify a checkout outside build/")
    target = dashboard / "app/(dashboard)/bug-bounty"
    if target.exists():
        raise RuntimeError("Upstream owns /bug-bounty; resolve the route collision")
    patch = ROOT / "overlay/sidebar.patch"
    run("git", "apply", "--check", str(patch), cwd=checkout)
    run("git", "apply", str(patch), cwd=checkout)
    shutil.copytree(ROOT / "overlay/bug-bounty", target)


def prepare(source=None, revision=None, overlay=True):
    pin = json.loads((ROOT / "upstream.json").read_text())
    revision = revision or pin["osmedeus_revision"]
    if len(revision) != 40 or any(c not in "0123456789abcdef" for c in revision):
        raise ValueError("Use a full commit SHA, not a floating branch")
    build = ROOT / "build"
    build.mkdir(exist_ok=True)
    checkout = Path(tempfile.mkdtemp(prefix="upstream-", dir=build)) / "source"
    run("git", "clone", "--no-hardlinks", "--no-checkout", source or pin["osmedeus_repository"], str(checkout))
    run("git", "checkout", "--detach", revision, cwd=checkout)
    dashboard = checkout / pin["dashboard_directory"]
    if overlay:
        apply_overlay(checkout, dashboard)
    artifact = {**pin, "build_id": source_fingerprint(ROOT), "osmedeus_revision": revision,
                "osmedeus_version": pin["osmedeus_version"] if revision == pin["osmedeus_revision"] else "candidate",
                "dashboard": str(dashboard), "source": str(checkout)}
    (build / "dashboard-build.json").write_text(json.dumps(artifact, indent=2) + "\n")
    return artifact


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source")
    parser.add_argument("--revision")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.apply:
        artifact = json.loads((ROOT / "build/dashboard-build.json").read_text())
        apply_overlay(Path(artifact["source"]), Path(artifact["dashboard"]))
    else:
        artifact = prepare(args.source, args.revision, not args.prepare_only)
    print(json.dumps(artifact, indent=2))
