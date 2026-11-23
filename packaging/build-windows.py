#!/usr/bin/env python3
"""Build an x64 Windows installer from official Python and MSYS2 packages.

The installer offers two windows. Qt runs on the official embedded Python
with PySide6 wheels and a few MSYS2 DLLs. GTK (experimental) needs PyGObject,
which exists for Windows only as MSYS2 builds, so it ships MSYS2's own Python
and GTK 4 runtime. The payload has three folders: core (Cutline itself), qt
and gtk; the installer copies core and the runtime chosen."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory
from urllib.request import urlretrieve
from zipfile import ZipFile

from install import ROOT

PYTHON = "3.13.13"
QT = "6.12.0"
MSYS2 = "https://repo.msys2.org/mingw/ucrt64/"
MSYS2_PREFIX = "mingw-w64-ucrt-x86_64-"
NATIVE = ("cairo", "pango", "geos", "poppler", "libusb")
GTK_NATIVE = ("python", "python-gobject", "python-cairo", "gtk4", "libadwaita", "librsvg",
              "adwaita-icon-theme", "hicolor-icon-theme", "python-pillow", "python-pyusb",
              "geos", "poppler", "libusb")
# Parts of the MSYS2 tree the GTK runtime does not need at run time.
GTK_SKIP = ("include", "share/doc", "share/man", "share/info", "share/gtk-doc", "share/aclocal",
            "share/gettext", "share/vala", "share/gir-1.0", "lib/pkgconfig", "lib/cmake")
WINRT = ("runtime", "windows-devices-bluetooth",
         "windows-devices-bluetooth-advertisement",
         "windows-devices-bluetooth-genericattributeprofile",
         "windows-devices-enumeration", "windows-devices-radios",
         "windows-foundation", "windows-foundation-collections",
         "windows-storage-streams")
WHEELS = (f"PySide6-Essentials=={QT}", f"shiboken6=={QT}",
          "Pillow==13.0.0", "pyusb==1.3.1", "bleak==3.1.0", "typing-extensions==4.16.0",
          *(f"winrt-{item}==3.2.1" for item in WINRT))
POPPLER_PROGRAMS = {"pdfinfo.exe", "pdftoppm.exe"}


def download(cache, url, expected=None):
    target = cache / url.rsplit("/", 1)[-1]
    if not target.is_file() or (expected and sha256(target) != expected):
        pending = target.with_suffix(target.suffix + ".part")
        urlretrieve(url, pending)
        if expected and sha256(pending) != expected:
            raise ValueError(f"Checksum mismatch for {url}")
        pending.replace(target)
    return target


def sha256(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def descriptor_index(db):
    data = subprocess.check_output(["zstd", "-dc", db])
    index = {}
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        for member in archive:
            if not member.name.endswith("/desc"):
                continue
            raw = archive.extractfile(member).read().decode().strip()
            fields = {header.strip("%"): values.splitlines()
                      for block in raw.split("\n\n")
                      for header, _, values in (block.partition("\n"),)}
            index[fields["NAME"][0]] = fields
    return index


def native_packages(cache, names=NATIVE):
    index = descriptor_index(download(cache, MSYS2 + "ucrt64.db"))
    todo = [MSYS2_PREFIX + name for name in names]
    found = {}
    while todo:
        name = todo.pop()
        if name in found:
            continue
        fields = index.get(name)
        if fields is None:
            fields = next((package for package in index.values()
                           if name in (re.split("[<>=]", item)[0]
                                       for item in package.get("PROVIDES", []))), None)
        if fields is None:
            raise ValueError(f"Unable to find the Windows dependency {name}")
        found[name] = fields
        todo.extend(re.split("[<>=]", item)[0] for item in fields.get("DEPENDS", []))
    return {fields["FILENAME"][0]: fields["SHA256SUM"][0] for fields in found.values()}


def wanted_for_qt(path):
    needed_binary = path.parent == Path("bin") and (path.suffix == ".dll" or path.name in POPPLER_PROGRAMS)
    return needed_binary or path.is_relative_to("share/licenses") or path.is_relative_to("share/poppler")


def wanted_for_gtk(path):
    if any(path.is_relative_to(skip) for skip in GTK_SKIP):
        return False
    if path.suffix == ".a" or "__pycache__" in path.parts:
        return False
    # The Python test suite: lib/python3.x/test and idlelib/idle_test.
    if len(path.parts) > 2 and path.parts[0] == "lib" and path.parts[2] in ("test", "idlelib"):
        return False
    return True


def unpack_native(archive, destination, wanted=wanted_for_qt):
    process = subprocess.Popen(["zstd", "-dc", archive], stdout=subprocess.PIPE)
    with process.stdout, tarfile.open(fileobj=process.stdout, mode="r|") as files:
        for member in files:
            path = Path(member.name)
            if not member.isfile() or not path.is_relative_to("ucrt64"):
                continue
            path = path.relative_to("ucrt64")
            if not wanted(path):
                continue
            if ".." in path.parts:
                raise ValueError(f"Invalid archive member {path}")
            target = destination / path
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("wb") as output, files.extractfile(member) as source:
                shutil.copyfileobj(source, output)
        # Read zstd's output to the end: the tar reader stops at the archive's
        # end marker, and closing the pipe early makes zstd fail on a write.
        while process.stdout.read(1 << 16):
            pass
    if process.wait():
        raise RuntimeError(f"Could not unpack {archive.name}")


def pip_command():
    """The host's pip, or the GNOME SDK's when the host has none."""
    try:
        import pip  # noqa: F401
        return [sys.executable, "-m", "pip"]
    except ImportError:
        if not shutil.which("flatpak"):
            raise SystemExit("Install pip (or the org.gnome.Sdk Flatpak) to download the Windows wheels.")
        return ["flatpak", "run", "--share=network", "--filesystem=host", "--command=python3",
                "org.gnome.Sdk//50", "-m", "pip"]


def compile_gtk_schemas(gtk):
    schemas = gtk / "share/glib-2.0/schemas"
    if schemas.is_dir() and shutil.which("glib-compile-schemas"):
        subprocess.run(["glib-compile-schemas", schemas], check=True)


def build(cache, output, gtk_runtime=True):
    wheel_dir = cache / "windows-wheels"
    subprocess.run([*pip_command(), "download", "--dest", wheel_dir,
                    "--platform", "win_amd64", "--python-version", "3.13",
                    "--implementation", "cp", "--only-binary=:all:", "--no-deps",
                    *WHEELS], check=True)
    python_zip = download(cache, f"https://www.python.org/ftp/python/{PYTHON}/python-{PYTHON}-embed-amd64.zip")
    packages = native_packages(cache)
    gtk_packages = native_packages(cache, GTK_NATIVE) if gtk_runtime else {}

    def fetch(items):
        with ThreadPoolExecutor(max_workers=8) as pool:
            return list(pool.map(lambda item: download(cache, MSYS2 + item[0], item[1]), items.items()))

    archives, gtk_archives = fetch(packages), fetch(gtk_packages)
    with TemporaryDirectory(prefix="cutline-windows-") as stage:
        payload = Path(stage)
        core, qt, gtk = payload / "core", payload / "qt", payload / "gtk"
        for folder in (core, qt, gtk):
            folder.mkdir()
        # Qt: the official embedded Python, PySide6 wheels and the DLLs Cutline loads.
        with ZipFile(python_zip) as embedded:
            embedded.extractall(qt)
        (qt / "python313._pth").write_text("python313.zip\n.\nLib/site-packages\n")
        wheels = sorted(wheel_dir.glob("*.whl"))
        for source in wheels:
            with ZipFile(source) as wheel:
                wheel.extractall(qt / "Lib/site-packages")
        for archive in archives:
            unpack_native(archive, qt / "native")
        # GTK: MSYS2's Python with PyGObject, GTK 4 and libadwaita.
        for archive in gtk_archives:
            unpack_native(archive, gtk, wanted_for_gtk)
        compile_gtk_schemas(gtk)
        # Core: Cutline and its launchers, shared by both.
        shutil.copytree(ROOT / "cutline", core / "cutline", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy2(ROOT / "packaging/windows-launcher.py", core / "launcher.py")
        shutil.copy2(ROOT / "LICENSE", core / "LICENSE")
        shutil.copy2(ROOT / "packaging/windows-readme.txt", core / "Start Here.txt")
        (core / "build-manifest.json").write_text(json.dumps({
            "python": {python_zip.name: sha256(python_zip)},
            "pypi_wheels": {wheel.name: sha256(wheel) for wheel in wheels},
            "msys2_ucrt64_packages": packages,
            "msys2_ucrt64_gtk_packages": gtk_packages,
        }, indent=2) + "\n")
        app = payload
        version = re.search(r'^__version__ = "(.*?)"', (ROOT / "cutline/__init__.py").read_text(),
                            re.M).group(1)
        filename = f"Cutline-{version}-Windows-x86_64-Setup.exe"
        # Publish the completed installer only after makensis succeeds.
        with TemporaryDirectory(prefix="windows-setup-", dir=output) as folder:
            installer = Path(folder) / filename
            flags = ["-DWITH_GTK"] if gtk_runtime else []
            subprocess.run(["makensis", "-V3", *flags, f"-DAPP_VERSION={version}",
                            f"-DPAYLOAD={app}", f"-DINSTALLER={installer}",
                            ROOT / "packaging/windows-installer.nsi"], check=True)
            installer.replace(output / filename)
        print(f"Windows installer: {output / filename}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=ROOT / "out/windows-cache")
    parser.add_argument("--output", type=Path, default=ROOT / "out/packages")
    parser.add_argument("--no-gtk", action="store_true", help="leave the experimental GTK runtime out")
    args = parser.parse_args()
    if not shutil.which("makensis"):
        parser.error("Install NSIS (makensis) to build the Windows installer.")
    for directory in (args.cache, args.output):
        directory.mkdir(parents=True, exist_ok=True)
    build(args.cache.resolve(), args.output.resolve(), gtk_runtime=not args.no_gtk)
