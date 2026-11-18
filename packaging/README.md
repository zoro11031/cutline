# Distribution packages

Build output goes into ignored `out/packages/`. Neither native packages nor
the AppImage include the guest staging area, Studio, vendor material files,
Windows credentials, or user settings. Application settings still live under
the user's existing `~/.config/cut-proxies` directory.

## RPM and Arch/AUR

From the repository root:

```sh
python3 packaging/build-native.py
```

Build tools: Python 3, `makepkg` with fakeroot/zstd, and `rpmbuild`.
This produces a source tarball, a noarch binary RPM and source RPM, plus
`out/packages/aur/PKGBUILD`, `.SRCINFO`, the source tarball and an Arch
`pkg.tar.zst`. Both builds execute a verified job without touching hardware.
Build-time dependency resolution is disabled because the host is Arch;
dependencies are still declared in the resulting RPM and Arch metadata.
Install with the destination system's package manager so it resolves them:

```sh
sudo dnf install ./silhouette-control-center-0.1.0-1.noarch.rpm
sudo pacman -U ./silhouette-control-center-0.1.0-1-any.pkg.tar.zst
```

The RPM uses Fedora dependency names. It is built on Arch and has not been
installed or exercised on Fedora. Both packages install private, version
independent Python sources into `/usr/lib/cut-proxies`, a launcher into
`/usr/bin`, and matching desktop/icon entries. GTK, Python, Pillow and
Poppler are native dependencies; PyUSB and the WinBoat tools are optional.

The AUR recipe uses the user-provided upstream repository:
`https://github.com/zoro11031/silhouette-linux`. The source tarball must be
uploaded to that repository's `v0.1.0` release at the URL in the generated
PKGBUILD before another machine can download it. The builder uses the local
copy and pins its actual SHA-256; it does not publish anything. Commit the
generated `PKGBUILD` and `.SRCINFO` to the AUR only after the source is
available and upstream licensing is declared. `packaging/aur/PKGBUILD` is a
build template; use the rendered recipe in `out/packages/aur`.

The checkout currently has no LICENSE file. Metadata uses
`LicenseRef-Unknown` instead of assigning a license on the author's behalf.
That metadata must be resolved before public release.

## Desktop frontend selection

The RPM and Arch package install the distro's PySide6 binding and launch the
Qt frontend. Native installations with the GTK4, libadwaita, PyGObject, and
Cairo Python bindings can also use `gui --frontend gtk`. The AppImage and
Flatpak recipes bundle the GTK runtime.


## AppImage (x86_64)

This builder packages the build host's Python, GTK4, libadwaita, GI typelibs,
Pillow, libraries (including a matching libc and loader), icons, schemas,
PDF helpers and license files. It currently requires an Arch-based build
host with `pacman` for dependency provenance, `mksquashfs`, `ldd`, and
`desktop-file-validate`, plus installed Python GI/cairo/Pillow, GTK4,
libadwaita, libusb, Poppler and `rsvg-convert`.

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
without exporting its library path to host Docker/FreeRDP processes. The
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
job and its liveness probes, PDF physical dimensions, and rasterization. With
`--gui`, it briefly opens a separate window, checks its verified preview,
and closes it. It never sends a cut. Host USB permissions still apply; see
`docs/SETUP.md`. Windows, Studio, Docker and FreeRDP are not bundled.

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
The AppImage runs on the host and can use host WinBoat, Docker and
FreeRDP when they are installed. Use it for the Windows bridge.

Build the Flatpak with `./packaging/build-flatpak.sh` after installing
`org.gnome.Sdk//50` and `org.gnome.Platform//50` from Flathub. It uses the
GNOME SDK's Python and compiles the PDF utilities from the pinned
[Poppler source](https://poppler.freedesktop.org/). AppImage build steps
are above.
