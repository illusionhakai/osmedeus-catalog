"""Isolated real-engine/gateway/API check. All created containers are removed."""
import argparse
import json
import subprocess
import tempfile
import uuid
from pathlib import Path


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True).strip()


def main(engine_image, catalog_image, gateway_image):
    prefix = "catalog-check-" + uuid.uuid4().hex[:8]
    network, engine, catalog, gateway = (prefix + s for s in ("-net", "-engine", "-catalog", "-gateway"))
    created = []
    password = uuid.uuid4().hex
    root = Path(__file__).resolve().parents[1]
    docker("network", "create", "--internal", network)
    try:
        docker("run", "-d", "--name", engine, "--network", network, "--network-alias", "engine",
               "-e", "OSM_SERVER_SIMPLE_USER_MAP_KEY_CATALOGCHECK=" + password,
               "-e", "OSM_SERVER_JWT_SECRET_SIGNING_KEY=" + uuid.uuid4().hex,
               engine_image, "--base-folder", "/tmp/catalog-engine", "serve", "--host", "0.0.0.0", "--port", "8002")
        created.append(engine)
        # Install only harmless log/sleep fixtures in this disposable engine.
        docker("exec", engine, "mkdir", "-p", "/tmp/catalog-engine/workflows/modules")
        docker("cp", str(root / "tests/smoke-input.yaml"), engine + ":/tmp/catalog-engine/workflows/modules/catalog-input-smoke.yaml")
        docker("cp", str(root / "tests/smoke-cancel.yaml"), engine + ":/tmp/catalog-engine/workflows/modules/catalog-cancel-smoke.yaml")
        docker("cp", str(root / "tests/smoke-enum-subdomain.yaml"), engine + ":/tmp/catalog-engine/workflows/modules/enum-subdomain.yaml")
        for name in ("cidr", "domain-standard"):
            docker("cp", str(root / f"tests/smoke-{name}.yaml"), engine + f":/tmp/catalog-engine/workflows/{name}.yaml")
        docker("run", "-d", "--name", catalog, "--network", network, "--network-alias", "catalog",
               "-e", "OSMEDEUS_URL=http://engine:8002", "-e", "CATALOG_BACKGROUND=false", catalog_image)
        created.append(catalog)
        # Seed only the disposable catalog, with reserved .example targets.
        subprocess.run(["docker", "exec", "-i", catalog, "python", "-"], input='''
from pathlib import Path
import time
from bugbounty_catalog.db import CatalogDB
from bugbounty_catalog.models import Program
from bugbounty_catalog.normalize import assets_from_values
for _ in range(100):
    if Path('/data/catalog.sqlite').exists():
        break
    time.sleep(.1)
with CatalogDB('/data/catalog.sqlite') as db:
    db.init_schema()
    db.upsert_programs([Program(source='fixture', source_id='delivery-test', name='Delivery fixture',
        assets=assets_from_values(['catalog-one.example', 'catalog-two.example', 'scope-denied.example'], 'in_scope')
        + assets_from_values(['scope-denied.example'], 'out_of_scope')),
        Program(source='routing-fixture', source_id='mixed', name='Routing fixture',
            assets=assets_from_values(['host.wild.example', '*.wild.example', '192.0.2.1', '2001:db8::/32'], 'in_scope'))])
''', text=True, check=True)
        docker("run", "-d", "--name", gateway, "--network", network, "--network-alias", "gateway",
               "-e", "OSMEDEUS_URL=http://engine:8002", "-e", "NGINX_ENVSUBST_FILTER=^OSMEDEUS_URL$", gateway_image)
        created.append(gateway)
        def probe(outage=False):
            result = subprocess.run(["docker", "run", "--rm", "-i", "--network", network,
                "--entrypoint", "python", "-e", "TEST_PASSWORD=" + password,
                "-e", "TEST_OUTAGE=" + str(outage).lower(), catalog_image, "-"],
                input=Path(__file__).with_name("container_probe.py").read_text(), text=True, check=True, capture_output=True)
            receipt = None
            for line in result.stdout.splitlines():
                if line.startswith("DELIVERY_RECEIPT="):
                    receipt = json.loads(line.split("=", 1)[1])
                else:
                    print(line)
            return receipt
        receipt = probe()
        uploaded = next(item["path"] for item in receipt["files"] if item["kind"] == "domains")
        (root / "build").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="delivery-fixture-", dir=root / "build") as folder:
            targets = Path(folder) / "targets.txt"
            docker("cp", engine + ":" + uploaded, str(targets))
            assert targets.read_text().splitlines() == ["catalog-one.example", "catalog-two.example"]
            result = subprocess.run(["docker", "run", "--rm", "--network", "none", "--mount",
                f"type=bind,source={root / 'tests'},target=/fixtures,readonly", "--mount",
                f"type=bind,source={folder},target=/delivered,readonly", engine_image,
                "run", "-m", "/fixtures/smoke-input.yaml", "-T", "/delivered/targets.txt",
                "--base-folder", "/tmp/delivery-smoke"], text=True, capture_output=True, check=True)
            for target in targets.read_text().splitlines():
                assert "CATALOG_SMOKE:" + target in result.stdout + result.stderr
        print("PASS: catalog -> export -> engine upload -> offline -T consumption; exclusions preserved")
        docker("stop", catalog)
        probe(outage=True)
    except Exception:
        if catalog in created:
            subprocess.run(["docker", "logs", catalog], check=False)
        if gateway in created:
            subprocess.run(["docker", "logs", gateway], check=False)
        raise
    finally:
        for name in reversed(created):
            subprocess.run(["docker", "rm", "-f", name], check=False, stdout=subprocess.DEVNULL)
        subprocess.run(["docker", "network", "rm", network], check=False, stdout=subprocess.DEVNULL)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine-image", default="osmedeus-catalog-engine-check:0.3.0-9a02ed0f9dbd")
    parser.add_argument("--catalog-image", default="osmedeus-catalog:0.3.0-osm-9a02ed0f9dbd")
    parser.add_argument("--gateway-image", default="osmedeus-catalog-ui:0.3.0-osm-9a02ed0f9dbd")
    args = parser.parse_args()
    main(args.engine_image, args.catalog_image, args.gateway_image)
