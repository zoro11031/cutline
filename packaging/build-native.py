#!/usr/bin/env python3
"""Build a source archive, RPM and local AUR recipe/Arch package."""
import argparse
import hashlib
import pathlib
import shutil
import subprocess
import tarfile

ROOT = pathlib.Path(__file__).resolve().parent.parent


def run(*args, **kwargs):
    subprocess.run(args, check=True, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=pathlib.Path, default=ROOT / 'out/packages')
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    version = {}
    exec((ROOT / 'cut_proxies/__init__.py').read_text(), version)
    name = 'silhouette-control-center-' + version['__version__']
    archive = out / (name + '.tar.gz')
    # Explicit allowlist: no guest files, credentials, build outputs, .git,
    # proprietary condition.csv or native Studio documents enter the archive.
    with tarfile.open(archive, 'w:gz') as tar:
        for entry in ('cut_proxies', 'bin', 'share', 'docs', 'packaging',
                      'tests', 'tools', 'run-tests.sh', 'README.md'):
            def clean(info):
                if '__pycache__' in pathlib.PurePosixPath(info.name).parts or info.name.endswith('.pyc'):
                    return None
                info.uid = info.gid = 0
                info.uname = info.gname = ''
                return info
            tar.add(ROOT / entry, arcname=name + '/' + entry, filter=clean)
        if (ROOT / 'LICENSE').is_file():
            tar.add(ROOT / 'LICENSE', arcname=name + '/LICENSE', filter=clean)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    aur = out / 'aur'
    aur.mkdir(exist_ok=True)
    shutil.copy2(archive, aur / archive.name)
    recipe = (ROOT / 'packaging/aur/PKGBUILD').read_text().replace('@SOURCE_SHA256@', digest)
    (aur / 'PKGBUILD').write_text(recipe)
    with (aur / '.SRCINFO').open('w') as stream:
        run('makepkg', '--printsrcinfo', cwd=aur, stdout=stream)
    run('makepkg', '--nodeps', '--force', '--cleanbuild', cwd=aur)
    rpm = out / 'rpm-build'
    for folder in ('SOURCES', 'SPECS', 'BUILD', 'BUILDROOT', 'RPMS', 'SRPMS', 'tmp'):
        (rpm / folder).mkdir(parents=True, exist_ok=True)
    shutil.copy2(archive, rpm / 'SOURCES' / archive.name)
    run('rpmbuild', '-ba', '--nodeps', '--define', '_topdir ' + str(rpm),
        '--define', '_tmppath ' + str(rpm / 'tmp'),
        str(ROOT / 'packaging/cut-proxies.spec'))
    for package in (rpm / 'RPMS').rglob('*.rpm'):
        shutil.copy2(package, out / package.name)
    for package in (rpm / 'SRPMS').glob('*.rpm'):
        shutil.copy2(package, out / package.name)
    print('Packages:', out)


if __name__ == '__main__':
    main()
