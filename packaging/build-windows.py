#!/usr/bin/env python3
"""Assemble a portable x64 Windows ZIP from official Python and MSYS2 packages."""
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


def native_packages(cache):
    index = descriptor_index(download(cache, MSYS2 + "ucrt64.db"))
    todo = [MSYS2_PREFIX + name for name in NATIVE]
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


def unpack_native(archive, destination):
    process = subprocess.Popen(["zstd", "-dc", archive], stdout=subprocess.PIPE)
    with process.stdout, tarfile.open(fileobj=process.stdout, mode="r|") as files:
        for member in files:
            path = Path(member.name)
            if not member.isfile() or not path.is_relative_to("ucrt64"):
                continue
            path = path.relative_to("ucrt64")
            needed_binary = path.parent == Path("bin") and (
                path.suffix == ".dll" or path.name in POPPLER_PROGRAMS)
            if not (needed_binary or path.is_relative_to("share/licenses")
                    or path.is_relative_to("share/poppler")):
                continue
            if ".." in path.parts:
                raise ValueError(f"Invalid archive member {path}")
            target = destination / path
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("wb") as output, files.extractfile(member) as source:
                shutil.copyfileobj(source, output)
    if process.wait():
        raise RuntimeError(f"Could not unpack {archive.name}")


def build(cache, output):
    wheel_dir = cache / "windows-wheels"
    subprocess.run([sys.executable, "-m", "pip", "download", "--dest", wheel_dir,
                    "--platform", "win_amd64", "--python-version", "3.13",
                    "--implementation", "cp", "--only-binary=:all:", "--no-deps",
                    *WHEELS], check=True)
    python_zip = download(cache, f"https://www.python.org/ftp/python/{PYTHON}/python-{PYTHON}-embed-amd64.zip")
    packages = native_packages(cache)
    with ThreadPoolExecutor(max_workers=8) as pool:
        archives = list(pool.map(lambda item: download(cache, MSYS2 + item[0], item[1]), packages.items()))
    with TemporaryDirectory(prefix="scc-windows-") as stage:
        app = Path(stage) / "Silhouette Control Center"
        app.mkdir()
        with ZipFile(python_zip) as embedded:
            embedded.extractall(app)
        (app / "python313._pth").write_text("python313.zip\n.\nLib/site-packages\n")
        wheels = sorted(wheel_dir.glob("*.whl"))
        for source in wheels:
            with ZipFile(source) as wheel:
                wheel.extractall(app / "Lib/site-packages")
        for archive in archives:
            unpack_native(archive, app / "native")
        shutil.copytree(ROOT / "cut_proxies", app / "cut_proxies", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy2(ROOT / "packaging/windows-launcher.py", app / "launcher.py")
        shutil.copy2(ROOT / "LICENSE", app / "LICENSE")
        shutil.copy2(ROOT / "packaging/windows-readme.txt", app / "Start Here.txt")
        (app / "Silhouette Control Center.cmd").write_bytes(
            b'@echo off\r\nstart "" "%~dp0pythonw.exe" "%~dp0launcher.py"\r\n')
        (app / "build-manifest.json").write_text(json.dumps({
            "python": {python_zip.name: sha256(python_zip)},
            "pypi_wheels": {wheel.name: sha256(wheel) for wheel in wheels},
            "msys2_ucrt64_packages": packages,
        }, indent=2) + "\n")
        version = re.search(r'^__version__ = "(.*?)"', (ROOT / "cut_proxies/__init__.py").read_text(),
                            re.M).group(1)
        target = output / f"SilhouetteControlCenter-{version}-Windows-x86_64"
        shutil.make_archive(str(target), "zip", base_dir=app.name, root_dir=app.parent)
        print(f"Windows portable ZIP: {target}.zip")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=ROOT / "out/windows-cache")
    parser.add_argument("--output", type=Path, default=ROOT / "out/packages")
    args = parser.parse_args()
    for directory in (args.cache, args.output):
        directory.mkdir(parents=True, exist_ok=True)
    build(args.cache.resolve(), args.output.resolve())
