"""Candidate compatibility gate. Never updates pins or deploys a release."""
import argparse
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from dashboard import ROOT, prepare
from build_dashboard import build
from container_smoke import main as container_smoke
from deployment import release_name, source_fingerprint


def run(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def smoke(image):
    result = run(["docker", "run", "--rm", "--network", "none", "--mount",
        f"type=bind,source={ROOT / 'tests'},target=/fixtures,readonly", image,
        "run", "-m", "/fixtures/smoke-input.yaml", "-T", "/fixtures/smoke-targets.txt",
        "--base-folder", "/tmp/catalog-smoke"], capture_output=True, text=True)
    combined = result.stdout + result.stderr
    for target in ("catalog-one.example", "catalog-two.example"):
        if f"CATALOG_SMOKE:{target}" not in combined:
            raise RuntimeError(f"Engine did not process fixture target {target}: {combined}")
    print("Offline target-file smoke passed (2 targets)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--revision")
    parser.add_argument("--source")
    parser.add_argument("--smoke-image", help="Run only the offline input smoke against this existing image")
    args = parser.parse_args()
    if args.smoke_image:
        smoke(args.smoke_image)
        sys.exit(0)
    (ROOT / "build").mkdir(exist_ok=True)
    previous = ROOT / "build/compatibility.json"
    if previous.exists():
        previous.rename(previous.with_name("compatibility-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f") + ".json"))
    test_dir = tempfile.mkdtemp(prefix="test-gate-", dir=ROOT / "build")
    run([sys.executable, "-m", "pytest", "-q", "--basetemp", test_dir], cwd=ROOT)
    artifact = prepare(args.source, args.revision)
    build(artifact)
    run(["npm", "ci", "--no-audit", "--no-fund"], cwd=ROOT)
    # A pinned Linux browser image also works on Windows/WSL host distributions
    # that Playwright does not support directly.
    run(["docker", "run", "--rm", "--ipc=host", "--mount",
         f"type=bind,source={ROOT},target=/work", "-w", "/work",
         "mcr.microsoft.com/playwright:v1.58.2-noble", "npm", "run", "test:browser"], cwd=ROOT)
    tag = "osmedeus-catalog-engine-check:" + release_name(artifact)
    run(["docker", "build", "-f", str(ROOT / "tests/Dockerfile.engine"),
         "--build-arg", "REVISION=" + artifact["osmedeus_revision"], "-t", tag, artifact["source"]])
    smoke(tag)
    release = release_name(artifact)
    catalog_tag, gateway_tag = "osmedeus-catalog:" + release, "osmedeus-catalog-ui:" + release
    run(["docker", "build", "-f", str(ROOT / "deploy/Dockerfile.catalog"),
         "--build-arg", "OSMEDEUS_REVISION=" + artifact["osmedeus_revision"],
         "--build-arg", "COMPANION_VERSION=" + artifact["companion_version"], "-t", catalog_tag, str(ROOT)])
    run(["docker", "build", "-f", str(ROOT / "deploy/Dockerfile.gateway-prebuilt"), "-t", gateway_tag, str(ROOT)])
    container_smoke(tag, catalog_tag, gateway_tag)
    if source_fingerprint(ROOT) != artifact["build_id"]:
        raise RuntimeError("Source changed during compatibility checks; run the gate again")
    def image_id(name):
        return subprocess.check_output(["docker", "image", "inspect", "--format", "{{.Id}}", name], text=True).strip()
    report = {"status": "passed", "revision": artifact["osmedeus_revision"],
              "companion_version": artifact["companion_version"], "build_id": artifact["build_id"],
              "release": release, "completed_at": datetime.now(timezone.utc).isoformat(),
              "images": {name: image_id(name) for name in (tag, catalog_tag, gateway_tag)},
              "checks": ["catalog", "types", "overlay-lint", "static-build", "browser", "offline-input-smoke", "real-engine-auth-gateway-outage", "delivered-input-smoke", "explicit-workflow-handoff", "scan-batch-submit-cancel"]}
    (ROOT / "build/compatibility.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
