"""Retain a rollback image, recovering a pruned image from its container."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile


def docker(*args, check=True):
    result = subprocess.run(['docker', *map(str, args)], text=True, capture_output=True)
    if check and result.returncode:
        # Import arguments contain container environment values; do not log them.
        raise RuntimeError('Docker ' + args[0] + ' failed while retaining the rollback image')
    return result


def quoted(value):
    if '\n' in value or '\r' in value:
        raise RuntimeError('Cannot recover multiline image configuration safely')
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('$', '\\$') + '"'


def configuration_changes(config):
    changes = []
    for key in ('Entrypoint', 'Cmd'):
        if config.get(key) is not None:
            changes.append(key.upper() + ' ' + json.dumps(config[key]))
    for entry in config.get('Env') or []:
        name, value = entry.split('=', 1)
        changes.append('ENV ' + name + '=' + quoted(value))
    for key, instruction in (('User', 'USER'), ('WorkingDir', 'WORKDIR'), ('StopSignal', 'STOPSIGNAL')):
        if config.get(key):
            changes.append(instruction + ' ' + quoted(config[key]))
    for name, value in (config.get('Labels') or {}).items():
        changes.append('LABEL ' + quoted(name) + '=' + quoted(value))
    for port in config.get('ExposedPorts') or {}:
        changes.append('EXPOSE ' + port)
    if config.get('Volumes'):
        changes.append('VOLUME ' + json.dumps(list(config['Volumes'])))
    health = config.get('Healthcheck') or {}
    test = health.get('Test') or []
    if test == ['NONE']:
        changes.append('HEALTHCHECK NONE')
    elif test:
        options = ''
        for key, flag in (('Interval', 'interval'), ('Timeout', 'timeout'),
                          ('StartPeriod', 'start-period'), ('StartInterval', 'start-interval')):
            if health.get(key):
                options += '--' + flag + '=' + str(health[key]) + 'ns '
        if health.get('Retries'):
            options += '--retries=' + str(health['Retries']) + ' '
        if test[0] == 'CMD':
            command = json.dumps(test[1:])
        elif test[0] == 'CMD-SHELL' and len(test) == 2:
            command = test[1]
        else:
            raise RuntimeError('Cannot recover unsupported healthcheck configuration')
        changes.append('HEALTHCHECK ' + options + 'CMD ' + command)
    changes.extend('ONBUILD ' + item for item in config.get('OnBuild') or [])
    return changes


def retain(container, tag):
    original = json.loads(docker('inspect', container).stdout)[0]
    image = original['Image']
    if docker('image', 'inspect', image, check=False).returncode == 0:
        docker('image', 'tag', image, tag)
        method = 'tag'
    else:
        # Export reads the surviving container snapshot even when image blobs are gone.
        # Mounted data is excluded and is backed up separately by backup-companion.ps1.
        changes = configuration_changes(original['Config'])
        with tempfile.TemporaryDirectory(prefix='catalog-rollback-') as temporary:
            archive = Path(temporary) / 'rootfs.tar'
            pause = original['State'].get('Running') and not original['State'].get('Paused')
            if pause:
                docker('pause', container)
            try:
                docker('export', '--output', archive, container)
            finally:
                if pause:
                    docker('unpause', container)
            arguments = ['import', '--platform', original['Platform']]
            for change in changes:
                arguments.extend(['--change', change])
            docker(*arguments, archive, tag)
        recovered = json.loads(docker('image', 'inspect', tag).stdout)[0]['Config']
        for key in ('Entrypoint', 'Cmd', 'Env', 'User', 'WorkingDir', 'StopSignal',
                    'Labels', 'ExposedPorts', 'Volumes', 'Healthcheck', 'OnBuild'):
            expected, actual = original['Config'].get(key), recovered.get(key)
            if expected != actual and (expected or actual):
                raise RuntimeError('Recovered rollback image configuration differs: ' + key)
        method = 'export-import'
    retained = docker('image', 'inspect', '--format', '{{.Id}}', tag).stdout.strip()
    return {'original': image, 'retained': retained, 'method': method}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('container')
    parser.add_argument('tag')
    args = parser.parse_args()
    try:
        print(json.dumps(retain(args.container, args.tag)))
    except (RuntimeError, OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
