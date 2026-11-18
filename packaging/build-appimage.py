#!/usr/bin/env python3
"""Bundle the installed x86_64 Linux Python/GTK runtime into an AppImage.

Requires PyGObject, pycairo, Pillow, GTK4, libadwaita, Poppler, librsvg,
mksquashfs and an official AppImage type2 runtime supplied with --runtime.
Does not download or install anything.
"""
import argparse
import hashlib
import importlib
import json
import pathlib
import platform
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import zipfile

from install import ROOT, install


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=pathlib.Path, required=True)
    parser.add_argument('--pyusb-wheel', type=pathlib.Path,
                        help='official PyUSB 1.3.1 wheel (SHA-256 is pinned below)')
    parser.add_argument('--output', type=pathlib.Path, default=ROOT / 'out/packages')
    args = parser.parse_args()
    if platform.machine() != 'x86_64':
        parser.error('this builder currently supports x86_64 only')
    runtime = args.runtime.resolve()
    header = runtime.read_bytes()[:20]
    if header[:4] != b'\x7fELF' or header[8:11] != b'AI\x02' or header[18:20] != b'\x3e\x00':
        parser.error('--runtime must be an x86_64 AppImage type2 runtime')
    import gi
    repo = gi.Repository.get_default()
    repo.require('Gtk', '4.0', 0)
    repo.require('Adw', '1', 0)
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='appimage-build-', dir=out) as work:
        work = pathlib.Path(work)
        app = work / 'SilhouetteControlCenter.AppDir'
        install(app)
        lib = app / 'usr/lib'
        sources = set()

        def copy(source, target):
            source = pathlib.Path(source)
            sources.add(source.resolve())
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

        def tree(source, target, ignore=None):
            source = pathlib.Path(source)
            shutil.copytree(source, target, dirs_exist_ok=True, ignore=ignore)
            sources.update((source / p.relative_to(target)).resolve()
                           for p in target.rglob('*') if p.is_file()
                           and (source / p.relative_to(target)).is_file())

        ignore = shutil.ignore_patterns('__pycache__', '*.pyc', 'site-packages',
                                       'test', 'tests', 'idlelib', 'tkinter', 'ensurepip',
                                       '_tkinter*', 'config-*')
        stdlib = pathlib.Path(sysconfig.get_path('stdlib'))
        tree(stdlib, lib / stdlib.name, ignore)
        site = lib / stdlib.name / 'site-packages'
        for name in ('gi', 'cairo', 'PIL'):
            module = importlib.import_module(name)
            tree(pathlib.Path(module.__file__).parent, site / name,
                 shutil.ignore_patterns('__pycache__', '*.pyc'))
        # No pip or host installation: a verified pure-Python wheel can be
        # unpacked directly into this isolated runtime.
        if args.pyusb_wheel:
            digest = hashlib.sha256(args.pyusb_wheel.read_bytes()).hexdigest()
            if digest != 'bf9b754557af4717fe80c2b07cc2b923a9151f5c08d17bdb5345dac09d6a0430':
                parser.error('PyUSB wheel does not match the official 1.3.1 SHA-256')
            with zipfile.ZipFile(args.pyusb_wheel) as wheel:
                wheel.extractall(site)
        else:
            try:
                module = importlib.import_module('usb')
            except ImportError:
                pass
            else:
                tree(pathlib.Path(module.__file__).parent, site / 'usb',
                     shutil.ignore_patterns('__pycache__', '*.pyc'))
        try:
            ble_modules = [importlib.import_module(name) for name in ('bleak', 'dbus_fast')]
        except ImportError:
            ble_modules = []
        for module in ble_modules:
            tree(pathlib.Path(module.__file__).parent, site / module.__name__,
                 shutil.ignore_patterns('__pycache__', '*.pyc'))
        copy(sys.executable, app / 'usr/bin/python3')
        for namespace in repo.get_loaded_namespaces():
            path = pathlib.Path(repo.get_typelib_path(namespace))
            copy(path, lib / 'girepository-1.0' / path.name)
        for folder in ('glib-2.0/schemas', 'icons/Adwaita'):
            tree('/usr/share/' + folder, app / 'usr/share' / folder)
        copy('/usr/share/icons/hicolor/index.theme', app / 'usr/share/icons/hicolor/index.theme')
        tree('/usr/lib/gio/modules', lib / 'gio/modules')
        # Explicit dlopen roots; ldd alone cannot discover GI namespaces.
        roots = [pathlib.Path('/usr/lib') / name for name in (
            'libgtk-4.so.1', 'libadwaita-1.so.0', 'libgirepository-1.0.so.1',
            'libgirepository-2.0.so.0', 'libcairo-gobject.so.2',
            'libpangocairo-1.0.so.0', 'libgeos_c.so.1', 'libnss_dns.so.2', 'libnss_files.so.2',
            'libresolv.so.2', 'libusb-1.0.so.0')]
        # Bundle the PDF helpers, with wrappers using the matching loader.
        for command in ('pdfinfo', 'pdftoppm', 'rsvg-convert'):
            binary = shutil.which(command)
            if binary is None:
                parser.error('missing build dependency: ' + command)
            real = app / 'usr/libexec' / command
            copy(binary, real)
            wrapper = app / 'usr/bin' / command
            wrapper.write_text('#!/bin/sh\nset -eu\n'
                               'base=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)\n'
                               'exec "$base/lib/ld-linux-x86-64.so.2" --library-path "$base/lib" '
                               '"$base/libexec/' + command + '" "$@"\n')
            wrapper.chmod(0o755)
        explicit_roots = tuple(roots)
        for path in app.rglob('*'):
            if path.is_file():
                with path.open('rb') as stream:
                    elf = stream.read(18)
                    if elf[:4] == b'\x7fELF' and elf[16:18] in (b'\x02\x00', b'\x03\x00'):
                        roots.append(path)
        for binary in roots:
            if not binary.exists():
                parser.error('missing shared library: ' + str(binary))
            dependencies = run('ldd', str(binary), capture_output=True, text=True).stdout
            if 'not found' in dependencies:
                raise RuntimeError(dependencies)
            for match in re.finditer(r'(?:=>\s+|^\s*)(/\S+)', dependencies, re.M):
                path = pathlib.Path(match[1])
                copy(path, lib / path.name)
            if binary in explicit_roots:
                copy(binary, lib / binary.name)
        # Include provenance and licenses for the host-built runtime.
        ownership = subprocess.run(
            ['pacman', '-Qoq', *sorted(str(p) for p in sources if str(p).startswith('/usr/'))],
            capture_output=True, text=True, check=False)
        packages = sorted(set(ownership.stdout.splitlines()))
        if not packages:
            raise RuntimeError('pacman could not identify any bundled dependencies')
        for package in packages:
            license_dir = pathlib.Path('/usr/share/licenses') / package
            if license_dir.is_dir():
                tree(license_dir, app / 'usr/share/licenses' / package)
        common = pathlib.Path('/usr/share/licenses/common')
        if common.is_dir():
            tree(common, app / 'usr/share/licenses/common')
        manifest = {
            'python': sys.version, 'platform': platform.platform(),
            'runtime_sha256': hashlib.sha256(runtime.read_bytes()).hexdigest(),
            'unowned_files': ownership.stderr.splitlines(),
            'host_packages': run('pacman', '-Q', *packages, capture_output=True, text=True).stdout.splitlines(),
            'bleak_bundled': bool(ble_modules),
            'pyusb_bundled': (site / 'usb').exists(),
            'pyusb_wheel_sha256': digest if args.pyusb_wheel else None,
        }
        (app / 'usr/share/doc/silhouette-control-center/bundle.json').write_text(json.dumps(manifest, indent=2) + '\n')
        copy(ROOT / 'packaging/AppRun', app / 'AppRun')
        (app / 'AppRun').chmod(0o755)
        desktop = app / 'org.printing.SilhouetteControlCenter.desktop'
        copy(ROOT / 'share/cut-proxies.desktop', desktop)
        copy(ROOT / 'share/icons/cut-proxies.svg', app / 'org.printing.SilhouetteControlCenter.svg')
        (app / '.DirIcon').symlink_to('org.printing.SilhouetteControlCenter.svg')
        run('desktop-file-validate', str(desktop))
        run(str(app / 'AppRun'), 'cut', '--dry-run', stdout=subprocess.DEVNULL)
        payload = work / 'payload.squashfs'
        run('mksquashfs', str(app), str(payload), '-noappend', '-comp', 'zstd',
            '-no-progress', '-all-root')
        version = {}
        exec((ROOT / 'cut_proxies/__init__.py').read_text(), version)
        image = out / ('SilhouetteControlCenter-' + version['__version__'] + '-x86_64.AppImage')
        with image.open('wb') as stream:
            stream.write(runtime.read_bytes())
            with payload.open('rb') as data:
                shutil.copyfileobj(data, stream)
        image.chmod(0o755)
        print('AppImage:', image)


if __name__ == '__main__':
    main()
