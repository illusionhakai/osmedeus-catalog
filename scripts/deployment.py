"""Content identities and dashboard refresh assets for checked deployments."""
import hashlib
import json
from pathlib import Path


SOURCE_DIRECTORIES = ('bugbounty_catalog', 'catalog_companion', 'overlay', 'deploy', 'scripts', 'tests')
SOURCE_FILES = ('upstream.json', 'compose.yaml', '.dockerignore', 'pyproject.toml',
                'requirements.lock', 'package.json', 'package-lock.json', 'playwright.config.ts')
IGNORED_PARTS = {'__pycache__', '.pytest_cache', 'node_modules', '.git'}


def source_fingerprint(root):
    root = Path(root)
    paths = {root / name for name in SOURCE_FILES if (root / name).is_file()}
    for directory in SOURCE_DIRECTORIES:
        paths.update(path for path in (root / directory).rglob('*')
                     if path.is_file() and not path.is_symlink()
                     and not IGNORED_PARTS.intersection(path.relative_to(root).parts)
                     and path.suffix not in ('.pyc', '.log'))
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: item.relative_to(root).as_posix()):
        digest.update(path.relative_to(root).as_posix().encode() + b'\0')
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def release_name(artifact):
    return (artifact['companion_version'] + '-osm-' + artifact['osmedeus_revision'][:12]
            + '-build-' + artifact['build_id'][:16])


def install_refresh(root, output, artifact):
    """Stamp the exact build into each HTML entry point and its polling script."""
    output = Path(output)
    script = (Path(root) / 'deploy/live-reload.js').read_text(encoding='utf-8')
    script = script.replace('__CATALOG_BUILD_ID__', artifact['build_id'])
    (output / 'catalog-live.js').write_text(script, encoding='utf-8')
    tag = '<script defer src="/catalog-live.js?v=' + artifact['build_id'] + '"></script>'
    for page in output.rglob('*.html'):
        html = page.read_text(encoding='utf-8')
        if '</head>' in html:
            page.write_text(html.replace('</head>', tag + '</head>', 1), encoding='utf-8')
    (output / 'catalog-release.json').write_text(json.dumps({
        key: value for key, value in artifact.items() if key not in ('source', 'dashboard')
    }), encoding='utf-8')
