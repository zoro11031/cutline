#!/bin/sh
# Run next to the downloaded package, as the desktop user.
set -eu
here=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
id=org.printing.SilhouetteControlCenter
data=${XDG_DATA_HOME:-"$HOME/.local/share"}
bin="$HOME/.local/bin"
version=0.1.0

case ${1:-} in
    --flatpak)
        bundle=${2:-"$here/SilhouetteControlCenter-$version-x86_64.flatpak"}
        [ -f "$bundle" ] || { printf 'Package missing: %s\n' "$bundle" >&2; exit 1; }
        flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
        flatpak install --user --bundle "$bundle"
        printf 'Launch from the application menu or run flatpak run %s\n' "$id"
        ;;
    --appimage)
        image=${2:-"$here/SilhouetteControlCenter-$version-x86_64.AppImage"}
        [ -f "$image" ] || { printf 'Package missing: %s\n' "$image" >&2; exit 1; }
        folder="$data/silhouette-control-center"
        installed="$folder/SilhouetteControlCenter.AppImage"
        install -Dm755 "$image" "$installed"
        work=$(mktemp -d)
        trap 'rm -rf "$work"' EXIT
        (cd "$work" && "$installed" --appimage-extract "$id.svg" >/dev/null)
        install -Dm644 "$work/squashfs-root/$id.svg" "$data/icons/hicolor/scalable/apps/$id.svg"
        mkdir -p "$bin" "$data/applications"
        cat > "$work/silhouette-control-center" <<'LAUNCH'
#!/bin/sh
export APPIMAGE_EXTRACT_AND_RUN=1
exec "${XDG_DATA_HOME:-$HOME/.local/share}/silhouette-control-center/SilhouetteControlCenter.AppImage" "$@"
LAUNCH
        install -m755 "$work/silhouette-control-center" "$bin/silhouette-control-center"
        cat > "$data/applications/$id.desktop" <<LAUNCHER
[Desktop Entry]
Type=Application
Name=Silhouette Control Center
Exec="$bin/silhouette-control-center" gui
Icon=$id
StartupWMClass=$id
Terminal=false
Categories=Graphics;
LAUNCHER
        printf 'Launch from the application menu or run %s gui\n' "$bin/silhouette-control-center"
        ;;
    --udev)
        sudo install -Dm644 /dev/stdin /etc/udev/rules.d/70-silhouette-control-center.rules <<'RULES'
SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTR{idVendor}=="0b4d", ATTR{idProduct}=="110a|111a|111c|1121|1123|112b|112f|1132|1137|1138|1139|113a|113f|1140|1141|1146", TAG+="uaccess"
SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTR{idVendor}=="3844", ATTR{idProduct}=="0001|0002", TAG+="uaccess"
RULES
        sudo udevadm control --reload-rules
        printf '%s\n' 'Unplug and reconnect the cutter so the USB access rule takes effect.'
        ;;
    *)
        printf 'Usage: %s --flatpak [bundle] | --appimage [image] | --udev\n' "$0" >&2
        exit 2
        ;;
esac
