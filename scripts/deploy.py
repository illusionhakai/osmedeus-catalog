"""Check changed companion code, preserve rollback data, deploy, and refresh UI."""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import tomllib
import urllib.request
import webbrowser
from pathlib import Path

from deployment import source_fingerprint

ROOT = Path(__file__).resolve().parents[1]


def run(*args, root=ROOT, capture=False, env=None):
    result = subprocess.run([str(arg) for arg in args], cwd=root, env=env,
                            check=True, text=True, capture_output=capture)
    return result.stdout.strip() if capture else None


def powershell(script, *args, root=ROOT):
    executable = 'powershell' if os.name == 'nt' else 'pwsh'
    run(executable, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
        root / 'scripts' / script, *args, root=root)


def compose_args(root):
    args = ['docker', 'compose', '--project-directory', str(root), '-f', str(root / 'compose.yaml')]
    override = root / 'build/catalog-network.override.yaml'
    if override.exists():
        args.extend(['-f', str(override)])
    return args


def configuration_fingerprint(root):
    digest = hashlib.sha256()
    for name in ('.env', 'build/catalog-network.override.yaml'):
        path = root / name
        digest.update(name.encode() + b'\0')
        if path.exists():
            digest.update(path.read_bytes())
    return digest.hexdigest()


def engine_containers(root):
    labels = json.loads(run('docker', 'inspect', '--format', '{{json .Config.Labels}}',
                            'osmedeus-server', root=root, capture=True) or '{}')
    project = labels.get('com.docker.compose.project')
    if not project:
        raise RuntimeError('Deploy the Osmedeus production Compose stack from README first.')
    rows = run('docker', 'ps', '--all', '--filter', 'label=com.docker.compose.project=' + project,
               '--filter', 'label=com.docker.compose.config-hash',
               '--filter', 'label=com.docker.compose.oneoff=False',
               '--format', '{{.ID}}\t{{.Label "com.docker.compose.service"}}', root=root, capture=True)
    services = {}
    for row in rows.splitlines():
        identifier, service = row.split('\t', 1)
        services.setdefault(service, []).append(identifier)
    if any(not services.get(name) for name in ('postgres', 'redis', 'server', 'worker')):
        raise RuntimeError('Existing Osmedeus stack must contain postgres, redis, server and worker services.')
    # The named server was used to discover the project. Ignore obsolete replicas.
    services['server'] = ['osmedeus-server']
    return services


def wait_container(identifier, root, timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = json.loads(run('docker', 'inspect', '--format', '{{json .State}}',
                               identifier, root=root, capture=True))
        health = state.get('Health', {}).get('Status', 'healthy')
        if state.get('Running') and health == 'healthy':
            return
        if state.get('Status') in ('dead', 'exited') or health == 'unhealthy':
            raise RuntimeError('Container failed to start: ' + identifier)
        time.sleep(2)
    raise RuntimeError('Container health timed out: ' + identifier)


def start_engine(services, root):
    for name in ('postgres', 'redis', 'server', 'worker'):
        for identifier in services[name]:
            run('docker', 'start', identifier, root=root)
            wait_container(identifier, root)


def gateway_url(root, env):
    configuration = json.loads(run(*compose_args(root), 'config', '--format', 'json',
                                   root=root, capture=True, env=env))
    for port in configuration['services']['gateway']['ports']:
        if port.get('target') == 80:
            return 'http://localhost:' + str(port['published'])
    raise RuntimeError('Gateway port is not configured.')


def wait_gateway(url, build_id, timeout=180):
    # Local verification must not route through process proxy settings.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with opener.open(url + '/catalog-release.json', timeout=5) as response:
                release = json.load(response)
            with opener.open(url + '/health', timeout=5) as response:
                healthy = response.status == 200
            if release.get('build_id') == build_id and healthy:
                return
        except (OSError, ValueError):
            pass
        time.sleep(2)
    raise RuntimeError('Gateway did not serve the checked build with a healthy engine.')


def check_release(root, source):
    # The gate imports the working tree directly. Install dependencies only, avoiding
    # repeated editable-package discovery through generated builds on Windows mounts.
    project = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8'))
    test_dependencies = project['project']['optional-dependencies']['test']
    if os.name == 'nt':
        linux_root = run('wsl', '-d', 'Ubuntu', '--exec', 'wslpath', '-a',
                         str(root).replace('\\', '/'), root=root, capture=True)
        prefix = ['wsl', '-d', 'Ubuntu', '--cd', linux_root, '--exec']
        # Windows cannot reliably stat Linux interpreter symlinks. Ensure the
        # environment from WSL instead; venv safely reuses an existing directory.
        run(*prefix, 'python3', '-m', 'venv', '.venv-linux', root=root)
        run(*prefix, '.venv-linux/bin/python', '-m', 'pip', 'install', '-r',
            'requirements.lock', *test_dependencies, root=root)
        args = ['-Source', str(source)] if source else []
        powershell('check-upgrade.ps1', *args, root=root)
    else:
        python = root / '.venv-linux/bin/python'
        if not python.exists():
            run(sys.executable, '-m', 'venv', '.venv-linux', root=root)
        run(python, '-m', 'pip', 'install', '-r', 'requirements.lock', *test_dependencies, root=root)
        args = ['--source', str(source)] if source else []
        run('npx', '--yes', '--package=bun@1.3.11', '--', python,
            'scripts/check_upgrade.py', *args, root=root)


def deploy(root=ROOT, source=None, force=False, open_browser=True):
    root = Path(root).resolve()
    build = root / 'build'
    build.mkdir(exist_ok=True)
    lock_path = build / 'deploy.lock'
    try:
        lock = lock_path.open('x')
    except FileExistsError:
        raise RuntimeError('Another deploy is active. Remove build/deploy.lock only after verifying it stopped.')
    try:
        with lock:
            lock.write(str(os.getpid()))
        if not (root / '.env').exists():
            (root / '.env').write_bytes((root / '.env.example').read_bytes())
        build_id = source_fingerprint(root)
        configuration_id = configuration_fingerprint(root)
        pin = json.loads((root / 'upstream.json').read_text(encoding='utf-8'))
        state_path = build / 'deployed.json'
        state = json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {}
        services = engine_containers(root)
        report_path = build / 'compatibility.json'
        report = json.loads(report_path.read_text(encoding='utf-8')) if report_path.exists() else {}
        valid = (report.get('status') == 'passed' and report.get('build_id') == build_id
                 and report.get('revision') == pin['osmedeus_revision']
                 and report.get('companion_version') == pin['companion_version'])
        if not valid or state.get('build_id') != build_id:
            # Save the previous report/images/data before the gate replaces its report.
            existing = [run(*compose_args(root), 'ps', '--all', '--quiet', service,
                            root=root, capture=True) for service in ('catalog', 'gateway')]
            if any(existing) and not all(existing):
                raise RuntimeError('Incomplete companion deployment; restore both services before updating.')
            if all(existing):
                powershell('backup-companion.ps1', root=root)
        if not valid:
            print('Code changed: running the compatibility gate.', flush=True)
            check_release(root, source)
            report = json.loads(report_path.read_text(encoding='utf-8'))
        if source_fingerprint(root) != build_id or report.get('build_id') != build_id:
            raise RuntimeError('Code changed during checks. Run deploy again; no new deployment was applied.')
        powershell('deploy-checked.ps1', '-ValidateOnly', root=root)
        start_engine(services, root)
        version = json.loads(run('docker', 'exec', services['server'][0], 'osmedeus', 'version', '--json',
                                 root=root, capture=True))
        if version.get('version') != pin['osmedeus_version']:
            raise RuntimeError('Engine version differs from upstream.json; deploy the pinned engine first.')
        commit = version.get('commit') or 'unknown'
        if commit != 'unknown' and commit != pin['osmedeus_revision']:
            raise RuntimeError('Engine commit differs from upstream.json; deploy the pinned engine first.')
        env = {**os.environ, 'CATALOG_RELEASE': report['release']}
        changed = force or state.get('build_id') != build_id or state.get('configuration_id') != configuration_id
        args = compose_args(root) + ['up', '-d', '--no-build', '--wait', '--wait-timeout', '180']
        if changed:
            args.append('--force-recreate')
        run(*args, root=root, env=env)
        url = gateway_url(root, env)
        wait_gateway(url, build_id)
        # Do not certify a moving working tree or configuration as deployed.
        if source_fingerprint(root) != build_id or configuration_fingerprint(root) != configuration_id:
            raise RuntimeError('Files changed during deployment. Run deploy again to apply them.')
        temporary = state_path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'build_id': build_id, 'configuration_id': configuration_id,
                                         'release': report['release'], 'url': url}, indent=2), encoding='utf-8')
        temporary.replace(state_path)
        print(('Deployed: ' if changed else 'Already current: ') + url, flush=True)
        if open_browser:
            webbrowser.open(url + '/?build=' + build_id[:16])
        return build_id, configuration_id
    finally:
        lock_path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--watch', action='store_true')
    parser.add_argument('--force', action='store_true', help='Recreate services using checked images, even if unchanged')
    parser.add_argument('--no-open', action='store_true', help='Do not open the dashboard in the browser')
    parser.add_argument('--source', type=Path, help='Git object source for the pinned engine checks')
    args = parser.parse_args()
    last = deploy(source=args.source, force=args.force, open_browser=not args.no_open)
    if args.watch:
        print('Watching code changes. Press Ctrl+C to stop.', flush=True)
        while True:
            time.sleep(2)
            current = source_fingerprint(ROOT), configuration_fingerprint(ROOT)
            if current != last:
                time.sleep(2)  # Let a multi-file save settle before building.
                try:
                    last = deploy(source=args.source, open_browser=False)
                except (RuntimeError, subprocess.CalledProcessError, OSError, ValueError) as error:
                    print('Deployment failed: ' + str(error), file=sys.stderr, flush=True)
                    # Retry only after another edit, rather than loop over a failing gate.
                    last = current


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('Stopped.', flush=True)
    except (RuntimeError, subprocess.CalledProcessError, OSError, ValueError) as error:
        print('Deployment failed: ' + str(error), file=sys.stderr)
        sys.exit(1)
