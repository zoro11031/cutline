#!/bin/sh
# Install cutline for the current user: the command, the launcher entry
# and the icons.
#
# Three names have to agree or the window gets a generic placeholder icon
# in the taskbar, which is the usual way this goes wrong:
#
#   * the GApplication id in cutline/gui.py  (APP_ID)
#   * the basename of the installed .desktop file
#   * the basename of the installed icon
#
# A compositor matches a window to its launcher by the Wayland toplevel app
# id, which GTK takes from the GApplication id. A .desktop file named
# anything else is never consulted, however good the artwork is.
#
#   ./install.sh            install
#   ./install.sh --uninstall   remove
set -e

APP_ID=io.github.zoro11031.Cutline
HERE=$(cd "$(dirname "$0")" && pwd)

BIN="${HOME}/.local/bin"
APPS="${HOME}/.local/share/applications"
ICONS="${HOME}/.local/share/icons/hicolor"

# Sizes a taskbar or dock may ask for. SVG alone is enough for GNOME and
# for KDE's launcher, but several panels and the window switcher look for a
# raster first and fall back to a generic icon rather than rasterising.
PNG_SIZES="16 22 24 32 48 64 128 256"

say() { printf '  %s\n' "$*"; }

# What earlier versions installed under their old name, removed on every run.
rm -f "${BIN}/silhouette-control-center" "${BIN}/cut-proxies" "${BIN}/kerf"
for OLD_ID in org.printing.SilhouetteControlCenter io.github.zoro11031.Kerf; do
    rm -f "${APPS}/${OLD_ID}.desktop" "${ICONS}/scalable/apps/${OLD_ID}.svg"
    for s in ${PNG_SIZES}; do
        rm -f "${ICONS}/${s}x${s}/apps/${OLD_ID}.png"
    done
done

if [ "$1" = "--uninstall" ]; then
    rm -f "${BIN}/cutline" "${APPS}/${APP_ID}.desktop" \
          "${ICONS}/scalable/apps/${APP_ID}.svg"
    for s in ${PNG_SIZES}; do
        rm -f "${ICONS}/${s}x${s}/apps/${APP_ID}.png"
    done
    update-desktop-database "${APPS}" 2>/dev/null || true
    gtk-update-icon-cache -qtf "${ICONS}" 2>/dev/null || true
    say "removed"
    exit 0
fi

# 1. the command
mkdir -p "${BIN}"
ln -sf "${HERE}/bin/cutline" "${BIN}/cutline"
say "command   ${BIN}/cutline"

case ":${PATH}:" in
    *":${BIN}:"*) ;;
    *) say "WARNING: ${BIN} is not on your PATH; add it to your shell rc" ;;
esac

# 2. the scalable icon
mkdir -p "${ICONS}/scalable/apps"
cp "${HERE}/share/icons/cutline.svg" \
   "${ICONS}/scalable/apps/${APP_ID}.svg"
say "icon      ${ICONS}/scalable/apps/${APP_ID}.svg"

# 3. raster icons, if anything here can make them
RASTER=""
if command -v rsvg-convert >/dev/null 2>&1; then
    RASTER="rsvg-convert"
elif command -v magick >/dev/null 2>&1; then
    RASTER="magick"
fi
if [ -n "${RASTER}" ]; then
    for s in ${PNG_SIZES}; do
        mkdir -p "${ICONS}/${s}x${s}/apps"
        if [ "${RASTER}" = "rsvg-convert" ]; then
            rsvg-convert -w "$s" -h "$s" \
                "${HERE}/share/icons/cutline.svg" \
                -o "${ICONS}/${s}x${s}/apps/${APP_ID}.png"
        else
            magick -background none \
                "${HERE}/share/icons/cutline.svg" \
                -resize "${s}x${s}" \
                "${ICONS}/${s}x${s}/apps/${APP_ID}.png"
        fi
    done
    say "icons     ${PNG_SIZES} px PNG renditions"
else
    say "NOTE: no rsvg-convert or magick, so only the SVG icon is installed."
    say "      Some panels want a raster and will show a placeholder."
fi

# 4. the launcher entry, with an absolute Exec so it works off PATH too
mkdir -p "${APPS}"
sed "s|^Exec=.*|Exec=${HERE}/bin/cutline gui|" \
    "${HERE}/share/cutline.desktop" > "${APPS}/${APP_ID}.desktop"
chmod 644 "${APPS}/${APP_ID}.desktop"
say "launcher  ${APPS}/${APP_ID}.desktop"

# Retire this application's previous launcher after the replacement exists.
# Keep a backup outside the .desktop suffix so the menu does not show both.
if [ -f "${APPS}/org.printing.CutProxies.desktop" ]; then
    mv "${APPS}/org.printing.CutProxies.desktop" \
       "${APPS}/org.printing.CutProxies.desktop.disabled"
fi

update-desktop-database "${APPS}" 2>/dev/null || true
gtk-update-icon-cache -qtf "${ICONS}" 2>/dev/null || true

# 5. say whether the three names actually agree, rather than assuming
GUI_ID=$(sed -n 's/^APP_ID *= *"\(.*\)"/\1/p' "${HERE}/cutline/gui.py")
WM=$(sed -n 's/^StartupWMClass=//p' "${APPS}/${APP_ID}.desktop")
echo
if [ "${GUI_ID}" = "${APP_ID}" ] && [ "${WM}" = "${APP_ID}" ]; then
    say "app id, desktop file and StartupWMClass all agree on ${APP_ID}"
    say "so the taskbar will use the installed icon rather than a placeholder"
else
    say "MISMATCH - the taskbar icon will not resolve:"
    say "  gui.py APP_ID      ${GUI_ID}"
    say "  desktop file       ${APP_ID}"
    say "  StartupWMClass     ${WM}"
    exit 1
fi
echo
say "Run it:  cutline gui (or find 'Cutline' in your launcher)"
