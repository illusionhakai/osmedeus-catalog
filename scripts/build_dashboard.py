"""Reproducible static dashboard build with the declared frozen Bun lockfile."""
import argparse
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from dashboard import ROOT, prepare
from deployment import install_refresh


def build(artifact, stage_only=False):
    if os.name != "nt":
        for key in ("dashboard", "source"):
            if ":\\" in artifact[key]:
                windows = PureWindowsPath(artifact[key])
                artifact[key] = str(Path("/mnt") / windows.drive[0].lower() / Path(*windows.parts[1:]))
    dashboard = Path(artifact["dashboard"])
    env = {**os.environ, "NEXT_EXPORT": "true", "NEXT_TELEMETRY_DISABLED": "1"}
    # Always include login and real API behavior in production artifacts.
    for name in ("NEXT_PUBLIC_USE_MOCK", "NEXT_PUBLIC_DISABLE_LOGIN", "BASE_API_URL", "NEXT_PUBLIC_API_URL"):
        env.pop(name, None)
    if not stage_only:
        subprocess.run(["bun", "install", "--frozen-lockfile"], cwd=dashboard, env=env, check=True)
        subprocess.run(["bun", "x", "next", "typegen"], cwd=dashboard, env=env, check=True)
        subprocess.run(["bun", "x", "tsc", "--noEmit"], cwd=dashboard, env=env, check=True)
        subprocess.run(["bun", "x", "eslint", "app/(dashboard)/bug-bounty"], cwd=dashboard, env=env, check=True)
        subprocess.run(["bun", "run", "build"], cwd=dashboard, env=env, check=True)
    source = dashboard / "out"
    if not source.exists():
        source = dashboard / "build"  # Next 16.3 exports to configured distDir.
    if not (source / "bug-bounty.html").exists():
        raise RuntimeError("Build did not produce the Bug Bounty page")
    output = ROOT / "build/dashboard-static"
    if output.exists():
        archives = ROOT / "build/artifacts"
        archives.mkdir(exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        output.rename(archives / ("dashboard-" + stamp))
    shutil.copytree(source, output)
    shutil.copy2(Path(artifact["source"]) / "LICENSE", output / "OSMEDEUS-LICENSE.txt")
    install_refresh(ROOT, output, artifact)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source")
    parser.add_argument("--revision")
    parser.add_argument("--prepared", action="store_true")
    parser.add_argument("--stage-only", action="store_true", help="Stage a build that already passed checks")
    args = parser.parse_args()
    artifact = json.loads((ROOT / "build/dashboard-build.json").read_text()) if args.prepared else prepare(args.source, args.revision)
    build(artifact, stage_only=args.stage_only)
