#!/usr/bin/env python3
"""Stage a system package without touching the build host's installation."""
import argparse
import pathlib
import shutil

ROOT = pathlib.Path(__file__).resolve().parent.parent


def install(dest, prefix="/usr"):
    dest = pathlib.Path(dest)
    relative = pathlib.Path(prefix).relative_to('/')
    lib = dest / relative / 'lib/cutline'
    lib.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / 'cutline', lib / 'cutline',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'),
                    dirs_exist_ok=True)
    launcher = lib / 'launcher.py'
    launcher.write_text('from cutline.cli import main\nraise SystemExit(main())\n')
    binary = dest / relative / 'bin/cutline'
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_text('#!/usr/bin/python3\nimport sys\n'
                      f'sys.path.insert(0, {str(pathlib.Path(prefix) / "lib/cutline")!r})\n'
                      'from cutline.cli import main\nraise SystemExit(main())\n')
    binary.chmod(0o755)
    for source, target in (
        ('share/cutline.desktop', 'usr/share/applications/io.github.zoro11031.Cutline.desktop'),
        ('share/icons/cutline.svg', 'usr/share/icons/hicolor/scalable/apps/io.github.zoro11031.Cutline.svg'),
        ('README.md', 'usr/share/doc/cutline/README.md'),
    ):
        target = dest / relative / target.removeprefix("usr/")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / source, target)
    if (ROOT / 'LICENSE').is_file():
        target = dest / relative / 'share/licenses/cutline/LICENSE'
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / 'LICENSE', target)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destdir', required=True)
    parser.add_argument('--prefix', default='/usr')
    args = parser.parse_args()
    install(args.destdir, args.prefix)
