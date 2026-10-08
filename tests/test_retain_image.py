import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location(
    'retain_image', Path(__file__).resolve().parents[1] / 'scripts/retain_image.py')
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


@pytest.fixture
def fixture(monkeypatch):
    config = {'Entrypoint': ['/entrypoint'], 'Cmd': ['serve', '--port', '8000'],
              'Env': ['TOKEN=secret $VALUE "quoted"'], 'User': '1000', 'WorkingDir': '/app',
              'StopSignal': 'SIGTERM', 'Labels': {'label': 'quoted "value"'},
              'Volumes': {'/data': {}}, 'ExposedPorts': {'8000/tcp': {}},
              'Healthcheck': {'Test': ['CMD', 'curl', '-f', 'http://localhost:8000/health'],
                              'Interval': 1000000000, 'Retries': 3}}
    state = {'Running': False, 'Paused': False}
    settings = {'available': False, 'fail_export': False, 'mismatch': False}
    calls = []

    def docker(*args, check=True):
        calls.append(args)
        if args == ('inspect', 'container'):
            value = [{'Image': 'sha256:old', 'Config': config, 'Platform': 'linux', 'State': state}]
        elif args == ('image', 'inspect', 'sha256:old'):
            return SimpleNamespace(returncode=0 if settings['available'] else 1, stdout='')
        elif args == ('image', 'inspect', 'rollback'):
            recovered = dict(config)
            if settings['mismatch']:
                recovered['Cmd'] = ['wrong']
            value = [{'Config': recovered}]
        elif args[0] == 'export' and settings['fail_export']:
            raise RuntimeError('export failed')
        else:
            return SimpleNamespace(returncode=0, stdout='sha256:retained\n')
        return SimpleNamespace(returncode=0, stdout=json.dumps(value))

    monkeypatch.setattr(recovery, 'docker', docker)
    return config, state, settings, calls


def test_existing_image_is_tagged_without_filesystem_export(fixture):
    _, _, settings, calls = fixture
    settings['available'] = True
    result = recovery.retain('container', 'rollback')
    assert result == {'original': 'sha256:old', 'retained': 'sha256:retained', 'method': 'tag'}
    assert ('image', 'tag', 'sha256:old', 'rollback') in calls
    assert not any(call[0] in ('export', 'import', 'pause') for call in calls)


def test_missing_image_recovers_filesystem_and_startup_configuration(fixture):
    config, _, _, calls = fixture
    result = recovery.retain('container', 'rollback')
    assert result['method'] == 'export-import'
    imported = next(call for call in calls if call[0] == 'import')
    changes = [imported[index + 1] for index, arg in enumerate(imported) if arg == '--change']
    assert 'ENTRYPOINT ' + json.dumps(config['Entrypoint']) in changes
    assert 'CMD ' + json.dumps(config['Cmd']) in changes
    assert 'ENV TOKEN="secret \\$VALUE \\"quoted\\""' in changes
    assert 'VOLUME ["/data"]' in changes
    assert 'HEALTHCHECK --interval=1000000000ns --retries=3 CMD ' + json.dumps(config['Healthcheck']['Test'][1:]) in changes
    archive = next(call[2] for call in calls if call[0] == 'export')
    assert not archive.parent.exists()


@pytest.mark.parametrize('fail_export', [False, True])
def test_running_container_is_unpaused_even_after_export_failure(fixture, fail_export):
    _, state, settings, calls = fixture
    state['Running'] = True
    settings['fail_export'] = fail_export
    if fail_export:
        with pytest.raises(RuntimeError, match='export failed'):
            recovery.retain('container', 'rollback')
    else:
        recovery.retain('container', 'rollback')
    assert ('pause', 'container') in calls
    assert ('unpause', 'container') in calls
    if fail_export:
        assert not any(call[0] == 'import' for call in calls)


def test_changed_recovered_configuration_blocks_promotion(fixture):
    _, _, settings, _ = fixture
    settings['mismatch'] = True
    with pytest.raises(RuntimeError, match='differs: Cmd'):
        recovery.retain('container', 'rollback')


def test_multiline_configuration_fails_before_export(fixture):
    config, _, _, calls = fixture
    config['Env'] = ['TOKEN=first\nsecond']
    with pytest.raises(RuntimeError, match='multiline'):
        recovery.retain('container', 'rollback')
    assert not any(call[0] == 'export' for call in calls)


def test_docker_errors_do_not_expose_environment_values(monkeypatch):
    monkeypatch.setattr(recovery.subprocess, 'run', lambda *args, **kwargs:
                        SimpleNamespace(returncode=1, stdout='', stderr='TOKEN=private'))
    with pytest.raises(RuntimeError) as error:
        recovery.docker('import', '--change', 'ENV TOKEN=private')
    assert 'private' not in str(error.value)
