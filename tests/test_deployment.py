import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
from deployment import install_refresh, release_name, source_fingerprint

spec = importlib.util.spec_from_file_location('deploy_command', SCRIPTS / 'deploy.py')
command = importlib.util.module_from_spec(spec)
spec.loader.exec_module(command)


def test_windows_gate_initializes_linux_environment_inside_wsl(tmp_path, monkeypatch):
    (tmp_path / 'pyproject.toml').write_text('[project.optional-dependencies]\ntest = ["pytest>=8,<10"]\n')
    calls = []
    monkeypatch.setattr(command, 'os', SimpleNamespace(name='nt'))
    def run(*args, **kwargs):
        calls.append(args)
        return '/mnt/catalog' if args[0] == 'wsl' and 'wslpath' in args else None
    monkeypatch.setattr(command, 'run', run)
    scripts = []
    monkeypatch.setattr(command, 'powershell', lambda *args, **kwargs: scripts.append(args))
    command.check_release(tmp_path, tmp_path / 'engine')
    assert ('wsl', '-d', 'Ubuntu', '--cd', '/mnt/catalog', '--exec',
            'python3', '-m', 'venv', '.venv-linux') in calls
    assert scripts == [('check-upgrade.ps1', '-Source', str(tmp_path / 'engine'))]
    assert any(call[-1] == 'pytest>=8,<10' for call in calls)


def test_engine_selection_excludes_unmanaged_containers_and_uses_named_server(tmp_path, monkeypatch):
    calls = []
    def run(*args, **kwargs):
        calls.append(args)
        if args[1] == 'inspect':
            return json.dumps({'com.docker.compose.project': 'production'})
        assert 'label=com.docker.compose.config-hash' in args
        assert 'label=com.docker.compose.oneoff=False' in args
        return 'db\tpostgres\ncache\tredis\nold\tserver\ncurrent\tserver\nworker\tworker'
    monkeypatch.setattr(command, 'run', run)
    services = command.engine_containers(tmp_path)
    assert services == {'postgres': ['db'], 'redis': ['cache'],
                        'server': ['osmedeus-server'], 'worker': ['worker']}


def test_engine_without_commit_metadata_can_deploy_matching_version(deployment, monkeypatch):
    root, events = deployment
    previous = command.run
    def run(*args, **kwargs):
        if args[:3] == ('docker', 'exec', 'engine'):
            return json.dumps({'version': 'v5.1.1', 'commit': ''})
        return previous(*args, **kwargs)
    monkeypatch.setattr(command, 'run', run)
    command.deploy(root, open_browser=False)
    assert (root / 'build/deployed.json').exists()


def test_fingerprint_detects_edits_removals_and_ignores_runtime(tmp_path):
    (tmp_path / 'catalog_companion').mkdir()
    code = tmp_path / 'catalog_companion/api.py'
    code.write_text('first')
    first = source_fingerprint(tmp_path)
    for directory, filename in (('build', 'report.json'), ('data', 'catalog.sqlite'),
                                ('docs', 'guide.md'), ('catalog_companion/__pycache__', 'api.pyc')):
        path = tmp_path / directory / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('runtime')
    assert source_fingerprint(tmp_path) == first
    code.write_text('second')
    assert source_fingerprint(tmp_path) != first
    code.unlink()
    assert source_fingerprint(tmp_path) != first


def test_release_identity_changes_without_overwriting_version_tags():
    artifact = {'companion_version': '0.4.0', 'osmedeus_revision': 'f' * 40, 'build_id': 'a' * 64}
    first = release_name(artifact)
    artifact['build_id'] = 'b' * 64
    assert release_name(artifact) != first
    assert first.startswith('0.4.0-osm-ffffffffffff-build-')


def test_static_refresh_stamps_each_entry_point_and_omits_checkout_paths(tmp_path):
    (tmp_path / 'deploy').mkdir()
    (tmp_path / 'deploy/live-reload.js').write_text('const build = "__CATALOG_BUILD_ID__";')
    output = tmp_path / 'out'
    output.mkdir()
    for name in ('index.html', 'bug-bounty.html'):
        (output / name).write_text('<html><head></head><body>page</body></html>')
    artifact = {'build_id': 'a' * 64, 'source': 'private-checkout', 'dashboard': 'private-path'}
    install_refresh(tmp_path, output, artifact)
    for name in ('index.html', 'bug-bounty.html'):
        assert '/catalog-live.js?v=' + artifact['build_id'] in (output / name).read_text()
    assert artifact['build_id'] in (output / 'catalog-live.js').read_text()
    assert json.loads((output / 'catalog-release.json').read_text()) == {'build_id': artifact['build_id']}


@pytest.fixture
def deployment(tmp_path, monkeypatch):
    (tmp_path / 'catalog_companion').mkdir()
    (tmp_path / 'catalog_companion/api.py').write_text('code')
    (tmp_path / 'upstream.json').write_text(json.dumps({
        'osmedeus_revision': 'f' * 40, 'osmedeus_version': 'v5.1.1', 'companion_version': '0.4.0'}))
    (tmp_path / '.env.example').write_text('CATALOG_PORT=8080\n')
    calls = []

    def run(*args, **kwargs):
        calls.append(args)
        if args[:2] == ('docker', 'exec'):
            return json.dumps({'version': 'v5.1.1', 'commit': 'f' * 40})
        if 'ps' in args:
            return 'existing-container'
        if 'config' in args:
            return json.dumps({'services': {'gateway': {'ports': [{'target': 80, 'published': '8080'}]}}})

    def gate(root, source):
        calls.append(('gate',))
        (root / 'build/compatibility.json').write_text(json.dumps({
            'status': 'passed', 'build_id': source_fingerprint(root), 'release': 'tested-release',
            'revision': 'f' * 40, 'companion_version': '0.4.0'}))

    monkeypatch.setattr(command, 'run', run)
    monkeypatch.setattr(command, 'engine_containers', lambda root: {'server': ['engine']})
    monkeypatch.setattr(command, 'start_engine', lambda *args: calls.append(('start-engine',)))
    monkeypatch.setattr(command, 'check_release', gate)
    monkeypatch.setattr(command, 'powershell', lambda script, *args, **kwargs: calls.append((script, *args)))
    monkeypatch.setattr(command, 'wait_gateway', lambda *args: calls.append(('healthy',)))
    monkeypatch.setattr(command.webbrowser, 'open', lambda url: calls.append(('browser', url)))
    return tmp_path, calls


def test_changed_code_backs_up_before_gate_and_records_only_healthy_release(deployment):
    root, calls = deployment
    command.deploy(root=root)
    assert calls.index(('backup-companion.ps1',)) < calls.index(('gate',))
    assert calls.index(('gate',)) < calls.index(('start-engine',))
    assert any('--force-recreate' in call for call in calls)
    assert any(call[0] == 'browser' for call in calls)
    state = json.loads((root / 'build/deployed.json').read_text())
    assert state['build_id'] == source_fingerprint(root)
    assert not (root / 'build/deploy.lock').exists()


def test_unchanged_code_restores_services_without_rebuilding(deployment):
    root, calls = deployment
    command.deploy(root=root, open_browser=False)
    calls.clear()
    command.deploy(root=root, open_browser=False)
    assert ('gate',) not in calls
    assert ('start-engine',) in calls
    assert any('up' in call for call in calls)
    assert not any('--force-recreate' in call for call in calls)
    assert not any(call[0] == 'browser' for call in calls)


def test_already_checked_release_still_backs_up_before_first_deployment(deployment):
    root, calls = deployment
    (root / 'build').mkdir()
    command.check_release(root, None)
    calls.clear()
    command.deploy(root=root, open_browser=False)
    assert ('gate',) not in calls
    assert ('backup-companion.ps1',) in calls
    assert calls.index(('backup-companion.ps1',)) < calls.index(('start-engine',))


def test_configuration_change_recreates_services_without_rebuilding(deployment):
    root, calls = deployment
    command.deploy(root=root, open_browser=False)
    (root / '.env').write_text('CATALOG_PORT=8081\n')
    calls.clear()
    command.deploy(root=root, open_browser=False)
    assert ('gate',) not in calls
    assert any('--force-recreate' in call for call in calls)


def test_failed_gate_never_starts_or_deploys_services(deployment, monkeypatch):
    root, calls = deployment
    def fail(*args):
        raise RuntimeError('gate failed')
    monkeypatch.setattr(command, 'check_release', fail)
    with pytest.raises(RuntimeError, match='gate failed'):
        command.deploy(root=root)
    assert not any('up' in call or call[0] == 'start-engine' for call in calls)
    assert not (root / 'build/deployed.json').exists()
    assert not (root / 'build/deploy.lock').exists()


def test_edit_during_gate_is_rejected_before_deployment(deployment, monkeypatch):
    root, calls = deployment
    original_gate = command.check_release
    def edit(*args):
        original_gate(*args)
        (root / 'catalog_companion/api.py').write_text('changed during gate')
    monkeypatch.setattr(command, 'check_release', edit)
    with pytest.raises(RuntimeError, match='Code changed during checks'):
        command.deploy(root=root)
    assert not any('up' in call or call[0] == 'start-engine' for call in calls)


def test_failed_health_does_not_mark_release_deployed(deployment, monkeypatch):
    root, calls = deployment
    def fail(*args):
        raise RuntimeError('unhealthy')
    monkeypatch.setattr(command, 'wait_gateway', fail)
    with pytest.raises(RuntimeError, match='unhealthy'):
        command.deploy(root=root)
    assert not (root / 'build/deployed.json').exists()
    assert not any(call[0] == 'browser' for call in calls)


def test_concurrent_deploy_is_rejected_without_removing_other_lock(deployment):
    root, calls = deployment
    (root / 'build').mkdir()
    lock = root / 'build/deploy.lock'
    lock.write_text('another process')
    with pytest.raises(RuntimeError, match='Another deploy is active'):
        command.deploy(root=root)
    assert lock.read_text() == 'another process'
    assert not calls


def test_shared_network_override_is_applied_to_compose(tmp_path):
    (tmp_path / 'build').mkdir()
    override = tmp_path / 'build/catalog-network.override.yaml'
    override.write_text('network configuration')
    assert command.compose_args(tmp_path)[-2:] == ['-f', str(override)]


def test_browser_refreshes_only_after_a_new_successful_build():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required for the browser refresh script check')
    template = (SCRIPTS.parent / 'deploy/live-reload.js').read_text()
    script = template.replace('__CATALOG_BUILD_ID__', 'a' * 64)
    harness = r'''
const vm = require('node:vm');
let reloads = 0, tick;
let build = 'a'.repeat(64), offline = false;
const context = {
  document: {hidden: false, addEventListener: () => {}},
  window: {setInterval: callback => {tick = callback;}, location: {reload: () => {reloads++;}}},
  fetch: async (_url, options) => {
    if (options.cache !== 'no-store') throw Error('stale cache');
    if (offline) throw Error('gateway restarting');
    return {ok: true, json: async () => ({build_id: build})};
  }
};
vm.runInNewContext(SCRIPT, context);
(async () => {
  await new Promise(resolve => setImmediate(resolve));
  await tick();
  if (reloads !== 0) throw Error('unchanged build reloaded');
  offline = true; await tick();
  if (reloads !== 0) throw Error('outage reloaded');
  offline = false; build = 'invalid'; await tick();
  if (reloads !== 0) throw Error('malformed metadata reloaded');
  build = 'b'.repeat(64); context.document.hidden = true; await tick();
  if (reloads !== 0) throw Error('hidden page reloaded');
  context.document.hidden = false; await tick(); await tick();
  if (reloads !== 1) throw Error('new build did not reload exactly once');
})().catch(error => {console.error(error); process.exitCode = 1;});
'''
    subprocess.run([node, '-e', harness.replace('SCRIPT', json.dumps(script))], check=True)
