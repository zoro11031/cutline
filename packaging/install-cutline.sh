#!/bin/sh
# Run next to the downloaded package, as the desktop user.
set -eu
here=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
id=io.github.zoro11031.Cutline
data=${XDG_DATA_HOME:-"$HOME/.local/share"}
bin="$HOME/.local/bin"
version=0.3.0

case ${1:-} in
    --flatpak)
        bundle=${2:-"$here/Cutline-$version-x86_64.flatpak"}
        [ -f "$bundle" ] || { printf 'Package missing: %s\n' "$bundle" >&2; exit 1; }
        flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
        flatpak install --user --bundle "$bundle"
        printf 'Launch from the application menu or run flatpak run %s\n' "$id"
        ;;
    --appimage)
        image=${2:-"$here/Cutline-$version-x86_64.AppImage"}
        [ -f "$image" ] || { printf 'Package missing: %s\n' "$image" >&2; exit 1; }
        folder="$data/cutline"
        installed="$folder/Cutline.AppImage"
        install -Dm755 "$image" "$installed"
        work=$(mktemp -d)
        trap 'rm -rf "$work"' EXIT
        (cd "$work" && "$installed" --appimage-extract "$id.svg" >/dev/null)
        install -Dm644 "$work/squashfs-root/$id.svg" "$data/icons/hicolor/scalable/apps/$id.svg"
        mkdir -p "$bin" "$data/applications"
        cat > "$work/cutline" <<'LAUNCH'
#!/bin/sh
export APPIMAGE_EXTRACT_AND_RUN=1
exec "${XDG_DATA_HOME:-$HOME/.local/share}/cutline/Cutline.AppImage" "$@"
LAUNCH
        install -m755 "$work/cutline" "$bin/cutline"
        cat > "$data/applications/$id.desktop" <<LAUNCHER
[Desktop Entry]
Type=Application
Name=Cutline
Exec="$bin/cutline" gui
Icon=$id
StartupWMClass=$id
Terminal=false
Categories=Graphics;
LAUNCHER
        printf 'Launch from the application menu or run %s gui\n' "$bin/cutline"
        ;;
    --udev)
        sudo install -Dm644 /dev/stdin /etc/udev/rules.d/70-cutline.rules <<'RULES'
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
