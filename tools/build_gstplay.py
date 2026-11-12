#!/usr/bin/env python3
"""Build only GstPlay, with upstream's dispose fix, in a private prefix.

No system library is replaced. BUILD.json and the exact source archive and
patch accompany the resulting library. Run inside MSYS2 UCRT64 or a Linux
build environment with Meson, Ninja, a C compiler and GStreamer base headers.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request

try:
    from gstplay_receipt import read_receipt
except ModuleNotFoundError:
    from tools.gstplay_receipt import read_receipt

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / 'build-aux/gstplay/gstplay-dispose.patch'
COMMIT = 'b8b17c948de515f72e91552e1f8607d370e8ba84'
SOURCES = {
    '1.28.2': '6467e3964828f4d7d08bfe1fbb4d76287a1c8fa76674e59e101a149c020fefd7',
    '1.28.8': '670d888bde8242579b0d8e91f60cf337aa7be20ca7c315ebfbdd6e5a9218d104',
    '1.28.9': '4a4e0b7bf84f868378c48f143a7ac3a98923b488a5b74f1a35ba46bc3c43f6b1',
    '1.26.11': '110fb82795f0e569b1e27b12ab9699d35c7762e1ff4db95335d6ac8d1442af3d',
}


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def source(version):
    if version not in SOURCES:
        raise ValueError(f'Unsupported GStreamer version: {version}')
    name = f'gst-plugins-bad-{version}.tar.xz'
    return {'file': name, 'url': f'https://gstreamer.freedesktop.org/src/gst-plugins-bad/{name}',
            'sha256': SOURCES[version]}


def relative_file(root, name):
    p = PurePosixPath(name)
    if not name or p.is_absolute() or '..' in p.parts or ':' in name or '\\' in name:
        raise ValueError(f'Unsafe relative build path: {name}')
    target = Path(root) / p
    if not target.resolve().is_relative_to(Path(root).resolve()):
        raise ValueError(f'Build path leaves its prefix: {name}')
    return target


def acquire(url, target, expected):
    target = Path(target)
    if not target.exists():
        partial = target.with_suffix(target.suffix + '.part')
        try:
            with urllib.request.urlopen(url, timeout=90) as remote, partial.open('wb') as out:
                shutil.copyfileobj(remote, out)
            if sha256(partial) != expected:
                raise ValueError(f'SHA-256 mismatch: {target.name}')
            partial.replace(target)
        finally:
            partial.unlink(missing_ok=True)
    if sha256(target) != expected:
        raise ValueError(f'SHA-256 mismatch: {target.name}')


def cached(output, key):
    try:
        receipt = json.loads((output / 'BUILD.json').read_text())
        return (receipt['cache_key'] == key and bool(receipt['outputs']) and
                all(sha256(relative_file(output, f['file'])) == f['sha256']
                    for f in receipt['outputs']))
    except (OSError, ValueError, KeyError, TypeError):
        return False


def capture(*command):
    return subprocess.check_output(command, text=True).strip()


def cache_key(version):
    # Include the installed dependency/toolchain versions, not just the source.
    packages = (capture('pacman', '-Q') if os.name == 'nt' else
                capture('dpkg-query', '-W') if shutil.which('dpkg-query') else
                capture('pkg-config', '--modversion', 'glib-2.0', 'gio-2.0',
                        'gstreamer-base-1.0', 'gstreamer-pbutils-1.0',
                        'gstreamer-audio-1.0', 'gstreamer-video-1.0'))
    inputs = [version, sha256(PATCH), sha256(Path(__file__)), platform.machine(),
              packages, capture('meson', '--version'), capture('ninja', '--version'),
              {name: os.environ.get(name, '') for name in
               ('CC', 'CFLAGS', 'CPPFLAGS', 'LDFLAGS')},
              capture(os.environ.get('CC', 'gcc'), '--version')]
    return hashlib.sha256(json.dumps(inputs).encode()).hexdigest()


def build(output, version, key, *, local_source=None, offline=False):
    output.mkdir(parents=True, exist_ok=True)
    src = source(version)
    if local_source is not None:
        if sha256(local_source) != src['sha256']:
            raise ValueError('SHA-256 mismatch: local source')
        if local_source.resolve() != (output / src['file']).resolve():
            shutil.copy2(local_source, output / src['file'])
    if offline and not (output / src['file']).is_file():
        raise ValueError('Offline build requires the pinned source archive')
    acquire(src['url'], output / src['file'], src['sha256'])
    patch = {'file': 'gstplay-dispose.patch',
             'url': f'https://github.com/GStreamer/gstreamer/commit/{COMMIT}.patch',
             'sha256': sha256(PATCH), 'upstream_commit': COMMIT,
             'derivation': 'Runtime-only diff; paths relative to gst-plugins-bad source root'}
    (output / patch['file']).write_bytes(PATCH.read_bytes())
    if cached(output, key):
        read_receipt(output / 'BUILD.json')
        print(f'Verified cached GstPlay {version}: {output}')
        return
    with tempfile.TemporaryDirectory(prefix='gstplay-', dir=output) as tmp:
        work = Path(tmp)
        with tarfile.open(output / src['file']) as archive:
            archive.extractall(work, filter='data')
        tree = work / f'gst-plugins-bad-{version}'
        git_env = dict(os.environ, GIT_CEILING_DIRECTORIES=str(tree.parent))
        subprocess.run(['git', 'apply', '--check', '-'], cwd=tree, env=git_env,
                       input=PATCH.read_bytes(), check=True)
        subprocess.run(['git', 'apply', '-'], cwd=tree, env=git_env,
                       input=PATCH.read_bytes(), check=True)
        if 'gboolean dispose_deferred;' not in (tree / 'gst-libs/gst/play/gstplay.c').read_text():
            raise ValueError('The GstPlay runtime patch was not applied')
        builddir = work / 'build'
        subprocess.run(['meson', 'setup', str(builddir), str(tree),
                        '--buildtype=release', '-Dauto_features=disabled', '-Dtests=disabled',
                        '-Dexamples=disabled', '-Dintrospection=disabled',
                        '-Dnls=disabled', '--wrap-mode=nodownload'], check=True)
        targets = json.loads(capture('meson', 'introspect', '--targets', str(builddir)))
        target = next(t for t in targets if t['name'] == 'gstplay-1.0')
        binary = next(Path(f) for f in target['filename']
                      if f.endswith('.dll') or '.so.' in f)
        subprocess.run(['ninja', '-C', str(builddir), str(binary.relative_to(builddir))], check=True)
        folder = 'bin' if os.name == 'nt' else 'lib'
        filename = binary.name if os.name == 'nt' else 'libgstplay-1.0.so.0'
        destination = output / folder / filename
        destination.parent.mkdir(exist_ok=True)
        shutil.copy2(binary, destination)
        shutil.copy2(tree / 'COPYING', output / 'COPYING')
    receipt = {'schema_version': 1, 'component': 'gstplay', 'version': version,
               'cache_key': key, 'source': src, 'patch': patch,
               'licence_files': [{'file': 'COPYING', 'sha256': sha256(output / 'COPYING')}],
               'recipe': {'project_file': 'tools/build_gstplay.py', 'sha256': sha256(Path(__file__))},
               'outputs': [{'file': f'{folder}/{filename}', 'sha256': sha256(destination)}]}
    temporary = output / 'BUILD.json.part'
    temporary.write_text(json.dumps(receipt, indent=2) + '\n')
    temporary.replace(output / 'BUILD.json')
    read_receipt(output / 'BUILD.json')
    print(f'Built GstPlay {version}: {destination}')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'build/gstplay')
    parser.add_argument('--version', help='Must equal the installed GStreamer version')
    parser.add_argument('--cache-key', action='store_true')
    parser.add_argument('--source', type=Path, help='An already downloaded, pinned source archive')
    parser.add_argument('--offline', action='store_true')
    args = parser.parse_args(argv)
    installed = capture('pkg-config', '--modversion', 'gstreamer-1.0')
    version = args.version or installed
    source(version)
    if installed != version:
        parser.error(f'Installed GStreamer is {installed}, requested {version}')
    key = cache_key(version)
    if args.cache_key:
        print(key)
    else:
        build(args.output.resolve(), version, key, local_source=args.source, offline=args.offline)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
