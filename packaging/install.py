#!/usr/bin/env python3
"""Stage a system package without touching the build host's installation."""
import argparse
import pathlib
import shutil

ROOT = pathlib.Path(__file__).resolve().parent.parent


def install(dest, prefix="/usr"):
    dest = pathlib.Path(dest)
    relative = pathlib.Path(prefix).relative_to('/')
    lib = dest / relative / 'lib/cut-proxies'
    lib.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / 'cut_proxies', lib / 'cut_proxies',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'),
                    dirs_exist_ok=True)
    launcher = lib / 'launcher.py'
    launcher.write_text('from cut_proxies.cli import main\nraise SystemExit(main())\n')
    binary = dest / relative / 'bin/silhouette-control-center'
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_text('#!/usr/bin/python3\nimport sys\n'
                      f'sys.path.insert(0, {str(pathlib.Path(prefix) / "lib/cut-proxies")!r})\n'
                      'from cut_proxies.cli import main\nraise SystemExit(main())\n')
    binary.chmod(0o755)
    (binary.parent / 'cut-proxies').symlink_to('silhouette-control-center')
    for source, target in (
        ('share/cut-proxies.desktop', 'usr/share/applications/org.printing.SilhouetteControlCenter.desktop'),
        ('share/icons/cut-proxies.svg', 'usr/share/icons/hicolor/scalable/apps/org.printing.SilhouetteControlCenter.svg'),
        ('README.md', 'usr/share/doc/silhouette-control-center/README.md'),
        ('docs/SETUP.md', 'usr/share/doc/silhouette-control-center/SETUP.md'),
        ('docs/FINDINGS.md', 'usr/share/doc/silhouette-control-center/FINDINGS.md'),
    ):
        target = dest / relative / target.removeprefix("usr/")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / source, target)
    if (ROOT / 'LICENSE').is_file():
        target = dest / relative / 'share/licenses/cut-proxies/LICENSE'
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / 'LICENSE', target)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destdir', required=True)
    parser.add_argument('--prefix', default='/usr')
    args = parser.parse_args()
    install(args.destdir, args.prefix)
