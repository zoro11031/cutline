#!/usr/bin/env python3
"""Build an x64 Windows installer from official Python and MSYS2 packages.

The Windows build is Qt only: the official embedded Python with PySide6
wheels, plus the MSYS2 DLLs Cutline loads (Cairo, Pango, GEOS, Poppler,
libusb)."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory
from urllib.request import urlretrieve
from zipfile import ZipFile

from install import ROOT

PYTHON = "3.13.13"
MSYS2 = "https://repo.msys2.org/mingw/ucrt64/"
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


def wanted(path):
    needed_binary = path.parent == Path("bin") and (path.suffix == ".dll" or path.name in POPPLER_PROGRAMS)
    return needed_binary or path.is_relative_to("share/licenses") or path.is_relative_to("share/poppler")


def unpack_native(archive, destination):
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


ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)


def build_ico(svg_path, ico_path):
    """Render a multi-resolution Windows .ico from the app SVG, or copy the checked-in .ico."""
    try:
        from PIL import Image
    except ImportError:
        Image = None
    if Image is None or not shutil.which("rsvg-convert"):
        fallback = ROOT / "share/icons/cutline.ico"
        if not fallback.is_file():
            raise RuntimeError("Install rsvg-convert and Pillow (or provide share/icons/cutline.ico) to build the Windows icon.")
        shutil.copy2(fallback, ico_path)
        return ico_path

    entries = []
    for size in ICO_SIZES:
        png = subprocess.check_output(["rsvg-convert", "-w", str(size), "-h", str(size), str(svg_path)])
        if size == 256:
            entries.append((size, png))
            continue
        image = Image.open(io.BytesIO(png)).convert("RGBA")
        pixels = image.load()
        row_stride = ((size + 31) // 32) * 4
        xor_rows = []
        and_rows = []
        for y in range(size - 1, -1, -1):
            row = bytearray()
            mask = bytearray(row_stride)
            for x in range(size):
                r, g, b, a = pixels[x, y]
                row.extend((b, g, r, a))
                if a == 0:
                    mask[x // 8] |= 1 << (7 - (x % 8))
            xor_rows.append(bytes(row))
            and_rows.append(bytes(mask))
        xor_data = b"".join(xor_rows)
        and_data = b"".join(and_rows)
        header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0,
                             len(xor_data) + len(and_data), 0, 0, 0, 0)
        entries.append((size, header + xor_data + and_data))

    ico = bytearray(struct.pack("<HHH", 0, 1, len(entries)))
    offset = 6 + 16 * len(entries)
    for size, blob in entries:
        dim = 0 if size == 256 else size
        ico.extend(struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(blob), offset))
        offset += len(blob)
    for _, blob in entries:
        ico.extend(blob)
    ico_path.write_bytes(ico)
    return ico_path


def runtime_member(name):
    """Cutline uses Qt Widgets. Omit Qt development tools and QML sources."""
    path = Path(name)
    if not path.is_relative_to("PySide6") or "license" in name.lower():
        return True
    relative = path.relative_to("PySide6")
    if not relative.parts:
        return True
    if path.suffix == ".pyd" and path.stem.startswith("Qt"):
        return path.stem in {"QtCore", "QtGui", "QtWidgets"}
    return (relative.parts[0] not in {"include", "typesystems", "metatypes", "qml", "scripts"}
            and path.suffix.lower() not in {".exe", ".lib", ".pyi"})


def build(cache, output):
    wheel_dir = cache / "windows-wheels"
    lock = json.loads((ROOT / "packaging/windows-dependencies.json").read_text())
    with TemporaryDirectory(prefix="cutline-requirements-", dir=cache) as folder:
        requirements = Path(folder) / "requirements.txt"
        lines = []
        for filename, digest in lock["pypi_wheels"].items():
            package, version, _ = filename.split("-", 2)
            lines.append(f"{package}=={version} --hash=sha256:{digest}")
        requirements.write_text("\n".join(lines) + "\n")
        subprocess.run([*pip_command(), "download", "--dest", wheel_dir,
                        "--platform", "win_amd64", "--python-version", "3.13",
                        "--implementation", "cp", "--only-binary=:all:", "--no-deps",
                        "--require-hashes", "-r", requirements], check=True)
    python_name = f"python-{PYTHON}-embed-amd64.zip"
    python_zip = download(cache, f"https://www.python.org/ftp/python/{PYTHON}/{python_name}",
                          lock["python"][python_name])
    packages = lock["msys2_ucrt64_packages"]
    with ThreadPoolExecutor(max_workers=8) as pool:
        archives = list(pool.map(lambda item: download(cache, MSYS2 + item[0], item[1]), packages.items()))
    with TemporaryDirectory(prefix="cutline-windows-") as stage:
        stage_dir = Path(stage)
        app = stage_dir / "Cutline"
        app.mkdir()
        with ZipFile(python_zip) as embedded:
            embedded.extractall(app)
        (app / "python313._pth").write_text("python313.zip\n.\nLib/site-packages\n")
        wheels = [wheel_dir / name for name in lock["pypi_wheels"]]
        for source in wheels:
            if sha256(source) != lock["pypi_wheels"][source.name]:
                raise ValueError(f"Checksum mismatch for {source.name}")
            with ZipFile(source) as wheel:
                wheel.extractall(app / "Lib/site-packages", filter(runtime_member, wheel.namelist()))
        for archive in archives:
            unpack_native(archive, app / "native")
        shutil.copytree(ROOT / "cutline", app / "cutline", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy2(ROOT / "packaging/windows-launcher.py", app / "launcher.py")
        shutil.copy2(ROOT / "LICENSE", app / "LICENSE")
        shutil.copy2(ROOT / "packaging/windows-readme.txt", app / "Start Here.txt")
        icon_file = build_ico(ROOT / "share/icons/cutline.svg", stage_dir / "cutline.ico")
        shutil.copy2(icon_file, app / "cutline.ico")
        license_file = stage_dir / "LICENSE.txt"
        license_file.write_bytes((ROOT / "LICENSE").read_text().replace("\n", "\r\n").encode("utf-8"))
        (app / "build-manifest.json").write_text(json.dumps({
            "python": {python_zip.name: sha256(python_zip)},
            "pypi_wheels": {wheel.name: sha256(wheel) for wheel in wheels},
            "msys2_ucrt64_packages": packages,
        }, indent=2) + "\n")
        version = re.search(r'^__version__ = "(.*?)"', (ROOT / "cutline/__init__.py").read_text(),
                            re.M).group(1)
        filename = f"Cutline-{version}-Windows-x86_64-Setup.exe"
        # Publish the completed installer only after makensis succeeds.
        with TemporaryDirectory(prefix="windows-setup-", dir=output) as folder:
            installer = Path(folder) / filename
            subprocess.run(["makensis", "-V3", f"-DAPP_VERSION={version}",
                            f"-DPAYLOAD={app}", f"-DICON={icon_file}",
                            f"-DLICENSE_FILE={license_file}", f"-DINSTALLER={installer}",
                            ROOT / "packaging/windows-installer.nsi"], check=True)
            installer.replace(output / filename)
        print(f"Windows installer: {output / filename}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=ROOT / "out/windows-cache")
    parser.add_argument("--output", type=Path, default=ROOT / "out/packages")
    args = parser.parse_args()
    if not shutil.which("makensis"):
        parser.error("Install NSIS (makensis) to build the Windows installer.")
    for directory in (args.cache, args.output):
        directory.mkdir(parents=True, exist_ok=True)
    build(args.cache.resolve(), args.output.resolve())
