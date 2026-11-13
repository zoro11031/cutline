#!/bin/sh
# Build a local x86-64 bundle using the matching GNOME SDK.
set -eu
root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
output=${1:-"$root/out/packages"}
id=org.printing.SilhouetteControlCenter
branch=50
version=$(sed -n 's/^__version__ = "\([^"]*\)"/\1/p' "$root/cut_proxies/__init__.py")
mkdir -p "$output"
output=$(cd "$output" && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir "$work/src"
cd "$work/src"

curl -fsSLO https://poppler.freedesktop.org/poppler-26.11.0.tar.xz
curl -fsSLO https://poppler.freedesktop.org/poppler-data-0.4.12.tar.gz
sha256sum -c <<'HASHES'
41c32680160addf5a8dd7a830e4869c01e9f6bee1abb91e44fabd1ba9d7828fb  poppler-26.11.0.tar.xz
c835b640a40ce357e1b83666aabd95edffa24ddddd49b8daff63adb851cdab74  poppler-data-0.4.12.tar.gz
HASHES
tar -xf poppler-26.11.0.tar.xz
tar -xf poppler-data-0.4.12.tar.gz

flatpak build-init --arch=x86_64 "$work/build" "$id" org.gnome.Sdk org.gnome.Platform "$branch"
python3 "$root/packaging/install.py" --destdir "$work/stage" --prefix /app
cp -a "$work/stage/app/." "$work/build/files/"
flatpak build --share=network "$work/build" python3 -m pip install \
    --only-binary=:all: --no-deps --no-compile --target=/app/lib/python \
    Pillow==13.0.0 pyusb==1.3.1 bleak==3.1.0 dbus-fast==5.2.0
flatpak build --bind-mount=/run/build/scc="$work/src" "$work/build" sh -ec '
    cd /run/build/scc
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
export CUT_PROXIES_CONFIG="${XDG_CONFIG_HOME}/cut-proxies"
if [ "$#" -eq 0 ]; then set -- gui; fi
exec /app/bin/silhouette-control-center "$@"
LAUNCH
chmod +x "$work/build/files/bin/launch-silhouette"
sed -i 's/^Exec=.*/Exec=launch-silhouette gui/' "$work/build/files/share/applications/$id.desktop"
flatpak build-finish "$work/build" --command=launch-silhouette \
    --socket=wayland --socket=fallback-x11 --share=ipc \
    --device=usb --system-talk-name=org.bluez \
    --filesystem=xdg-documents --filesystem=xdg-download
flatpak build-export "$work/repo" "$work/build"
flatpak build-bundle --runtime-repo=https://dl.flathub.org/repo/flathub.flatpakrepo \
    "$work/repo" "$output/SilhouetteControlCenter-$version-x86_64.flatpak" "$id"
