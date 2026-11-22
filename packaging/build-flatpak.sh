#!/bin/sh
# Build a local x86-64 bundle using the matching GNOME SDK.
set -eu
root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
output=${1:-"$root/out/packages"}
id=io.github.zoro11031.Cutline
branch=50
version=$(sed -n 's/^__version__ = "\([^"]*\)"/\1/p' "$root/cutline/__init__.py")
mkdir -p "$output"
output=$(cd "$output" && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir "$work/src"
cd "$work/src"

sources=${SCC_FLATPAK_SOURCES:-}
for tarball in poppler-26.11.0.tar.xz poppler-data-0.4.12.tar.gz geos-3.13.1.tar.bz2; do
    if [ -n "$sources" ] && [ -f "$sources/$tarball" ]; then
        cp "$sources/$tarball" .
    fi
done
[ -f poppler-26.11.0.tar.xz ] || curl --http1.1 -fsSLO https://poppler.freedesktop.org/poppler-26.11.0.tar.xz
[ -f poppler-data-0.4.12.tar.gz ] || curl --http1.1 -fsSLO https://poppler.freedesktop.org/poppler-data-0.4.12.tar.gz
[ -f geos-3.13.1.tar.bz2 ] || curl --http1.1 -fsSLO https://download.osgeo.org/geos/geos-3.13.1.tar.bz2
sha256sum -c <<'HASHES'
41c32680160addf5a8dd7a830e4869c01e9f6bee1abb91e44fabd1ba9d7828fb  poppler-26.11.0.tar.xz
c835b640a40ce357e1b83666aabd95edffa24ddddd49b8daff63adb851cdab74  poppler-data-0.4.12.tar.gz
df2c50503295f325e7c8d7b783aca8ba4773919cde984193850cf9e361dfd28c  geos-3.13.1.tar.bz2
HASHES
tar -xf poppler-26.11.0.tar.xz
tar -xf poppler-data-0.4.12.tar.gz
tar -xf geos-3.13.1.tar.bz2

flatpak build-init --arch=x86_64 "$work/build" "$id" org.gnome.Sdk org.gnome.Platform "$branch"
python3 "$root/packaging/install.py" --destdir "$work/stage" --prefix /app
cp -a "$work/stage/app/." "$work/build/files/"
# Download on the host using the SDK's interpreter version; install offline.
# CI hosts that cannot create a network namespace may share the build host network.
build_network=
if [ "${SCC_FLATPAK_BUILD_SHARE_NETWORK:-0}" = 1 ]; then
    build_network=--share=network
fi
sdk_python=$(flatpak build $build_network "$work/build" python3 -c \
    'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
python3 -m pip download --dest "$work/src/wheels" --python-version "$sdk_python" \
    --implementation cp --platform manylinux_2_28_x86_64 --platform manylinux2014_x86_64 \
    --only-binary=:all: --no-deps \
    Pillow==13.0.0 pyusb==1.3.1 bleak==3.1.0 dbus-fast==5.2.0
flatpak build $build_network --bind-mount=/run/build/scc="$work/src" "$work/build" \
    sh -ec 'python3 -m pip install --no-index --no-deps --no-compile --target=/app/lib/python \
        /run/build/scc/wheels/*.whl'
flatpak build $build_network --bind-mount=/run/build/scc="$work/src" "$work/build" sh -ec '
    cd /run/build/scc
    cmake -S geos-3.13.1 -B geos-build -G Ninja \
        -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/app -DCMAKE_INSTALL_LIBDIR=lib \
        -DBUILD_TESTING=OFF -DBUILD_GEOSOP=OFF -DBUILD_DOCUMENTATION=OFF
    cmake --build geos-build --parallel 8
    cmake --install geos-build
    install -Dm644 geos-3.13.1/COPYING /app/share/licenses/geos/COPYING
    cmake -S poppler-26.11.0 -B poppler-build -G Ninja \
        -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/app \
        -DBUILD_SHARED_LIBS=OFF -DBUILD_MANUAL_TESTS=OFF \
        -DENABLE_BOOST=OFF -DENABLE_CPP=OFF -DENABLE_GLIB=OFF \
        -DENABLE_QT5=OFF -DENABLE_QT6=OFF -DENABLE_NSS3=OFF -DENABLE_GPGME=OFF
    cmake --build poppler-build --parallel 8 --target pdfinfo pdftoppm
    install -m755 poppler-build/utils/pdfinfo poppler-build/utils/pdftoppm /app/bin/
    strip /app/bin/pdfinfo /app/bin/pdftoppm
    make -C poppler-data-0.4.12 prefix=/app install
    install -Dm644 poppler-26.11.0/COPYING /app/share/licenses/poppler/COPYING
'
cat > "$work/build/files/bin/launch-silhouette" <<'LAUNCH'
#!/bin/sh
export PYTHONPATH=/app/lib/python
export CUTLINE_CONFIG="${XDG_CONFIG_HOME}/cutline"
if [ "$#" -eq 0 ]; then set -- gui; fi
exec /app/bin/cutline "$@"
LAUNCH
chmod +x "$work/build/files/bin/launch-silhouette"
sed -i 's/^Exec=.*/Exec=launch-silhouette gui/' "$work/build/files/share/applications/$id.desktop"
flatpak build $build_network "$work/build" /app/bin/launch-silhouette cut --dry-run
flatpak build-finish "$work/build" --command=launch-silhouette \
    --socket=wayland --socket=fallback-x11 --share=ipc \
    --device=usb --system-talk-name=org.bluez \
    --filesystem=xdg-documents --filesystem=xdg-download
flatpak build-export "$work/repo" "$work/build"
flatpak build-bundle --runtime-repo=https://dl.flathub.org/repo/flathub.flatpakrepo \
    "$work/repo" "$output/Cutline-$version-x86_64.flatpak" "$id"
