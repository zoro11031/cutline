# Packages

Builds go into `out/packages/`. Your presets and settings stay in
`~/.config/cut-proxies/`. See [Setup](../docs/SETUP.md) for USB access and
[LICENSE](../LICENSE) for the project’s license.

## RPM and Arch/AUR

Run this from the repository folder:

```sh
python3 packaging/build-native.py
```

You’ll need Python 3, `makepkg` with fakeroot and zstd, and `rpmbuild`.
The build creates the RPM and Arch packages, a source tarball, and an
AUR recipe in `out/packages/aur/`. It runs a dry cut using no hardware.

Install the output with your usual package manager:

```sh
sudo dnf install ./out/packages/silhouette-control-center-0.1.0-1.noarch.rpm
sudo pacman -U ./out/packages/silhouette-control-center-0.1.0-1-any.pkg.tar.zst
```

For AUR publishing, use the generated `PKGBUILD` and `.SRCINFO`. Check
that the source URL, checksum, and license in the recipe match the release.
The builder leaves publishing to you.

## Desktop frontend selection

RPM and Arch packages install PySide6 and open Qt by default. With GTK 4,
libadwaita, PyGObject, and Python Cairo installed, run
`silhouette-control-center gui --frontend gtk`. The AppImage and
Flatpak recipes bundle GTK.

## AppImage (x86_64)

This builder packages the build host's Python, GTK4, libadwaita, GI typelibs,
Pillow, libraries (including a matching libc and loader), icons, schemas,
PDF helpers and license files. Build on x86-64 Debian or Arch with
`mksquashfs`, `ldd`, `glib-compile-schemas`, and `desktop-file-validate`.
Install Python GI/cairo/Pillow, GTK4, libadwaita, GEOS, libusb, Poppler
and `rsvg-convert` on the build host.

Download the official x86_64 [AppImage type2 runtime](https://github.com/AppImage/type2-runtime/releases)
and optionally the official PyUSB 1.3.1 wheel from PyPI. No pip installation
is needed. The PyUSB wheel's SHA-256 is pinned in the builder.

```sh
python3 packaging/build-appimage.py --runtime /path/to/runtime-x86_64 \
  --pyusb-wheel /path/to/pyusb-1.3.1-py3-none-any.whl
./out/packages/SilhouetteControlCenter-0.1.0-x86_64.AppImage
./out/packages/SilhouetteControlCenter-0.1.0-x86_64.AppImage cut --dry-run
```

For a machine without usable FUSE, add `--appimage-extract-and-run` before
the application arguments. The bundle uses the matching loader explicitly
without exporting its library path to host tools. The
default renderer is Cairo to avoid bundling GPU drivers. Bundled runtime
versions, runtime hash and package provenance are in
`usr/share/doc/silhouette-control-center/bundle.json` inside the image. This is a local
host-built bundle; compatibility with other distributions remains untested.

To inspect and smoke-test the actual extracted payload:

```sh
mkdir -p out/appimage-smoke
cd out/appimage-smoke
../packages/SilhouetteControlCenter-0.1.0-x86_64.AppImage --appimage-extract
cd ../..
python3 packaging/smoke-appdir.py out/appimage-smoke/squashfs-root --gui
```

The smoke test checks bundled module origins, the libusb backend, a verified
job and its liveness probes, PDF physical dimensions, and rasterization.
Use `--skip-usb-init` in a sandbox that cannot access USB discovery. With
`--gui`, it briefly opens a separate window, checks its verified preview,
and closes it. It never sends a cut. Host USB permissions still apply; see
`docs/SETUP.md`.

For Cameo 4 Bluetooth LE, build the AppImage with Bleak and its Linux
backend (dbus-fast) installed on the build host. The AppImage records
whether they were bundled in its manifest and uses the target host's BlueZ
service at runtime.

## Fedora Silverblue (x86-64)

Copy `install-silverblue.sh` and the matching package to the laptop. Install
either package as your desktop user:

```sh
sh install-silverblue.sh --flatpak
# Or use the AppImage, including on systems without FUSE 2:
sh install-silverblue.sh --appimage
```

Both install a desktop launcher. The Flatpak installs per user and downloads
the GNOME 50 runtime from Flathub when needed. It can access USB devices,
BlueZ, Documents, Downloads, and files chosen through the desktop file
picker. For direct USB access, apply the model-specific seat permission
once with `sh install-silverblue.sh --udev`, then reconnect the cutter.


Build the Flatpak with `./packaging/build-flatpak.sh` after installing
`org.gnome.Sdk//50` and `org.gnome.Platform//50` from Flathub. Install
Python with pip on the build host to download the wheels for the SDK
interpreter. This builder uses the GNOME SDK's Python and compiles the PDF
utilities from the pinned
[Poppler source](https://poppler.freedesktop.org/). AppImage build steps
are above.


## Windows portable ZIP (x86-64)

Build on Linux with Python 3.12 or newer, pip, and `zstd`.

```sh
python3 packaging/build-windows.py
```

Extract the resulting ZIP and open `Silhouette Control Center.cmd`. The
package includes Python, Qt, Poppler, Cairo, Pango, GEOS, and the PyUSB and
Bluetooth dependencies. It keeps presets and settings under
`%LOCALAPPDATA%\Silhouette Control Center`.

The ZIP contains `Start Here.txt` and a manifest with dependency checksums.
USB on Windows requires a libusb-compatible cutter driver. The package
does not install drivers. Direct USB and Bluetooth need a separate
physical-cutter test in the Windows environment where you plan to use them.
