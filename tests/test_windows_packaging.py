"""Verify Windows installer configuration, icon generation, and Qt icon fallback."""

import importlib.util
import os
from pathlib import Path
import re
import shutil
import struct
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent

try:
    from PIL import Image
except ImportError:
    Image = None

try:
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication
    from cutline import qt_gui
except ImportError:
    qt_gui = None


def _load_build_windows():
    packaging_dir = str(ROOT / "packaging")
    added = packaging_dir not in sys.path
    if added:
        sys.path.insert(0, packaging_dir)
    try:
        spec = importlib.util.spec_from_file_location("build_windows", ROOT / "packaging/build-windows.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if added and packaging_dir in sys.path:
            sys.path.remove(packaging_dir)


class TestWindowsPackaging(unittest.TestCase):
    def _assert_valid_ico(self, ico_path):
        data = ico_path.read_bytes()
        reserved, kind, count = struct.unpack_from("<HHH", data, 0)
        self.assertEqual((reserved, kind, count), (0, 1, 7))
        dimensions = []
        for index in range(count):
            width, height, _, _, planes, bpp, size, offset = struct.unpack_from("<BBBBHHII", data, 6 + index * 16)
            dimensions.append((width or 256, height or 256))
            self.assertEqual(planes, 1)
            self.assertEqual(bpp, 32)
            self.assertGreater(size, 0)
            self.assertLessEqual(offset + size, len(data))
        self.assertEqual(dimensions, [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
        if Image is not None:
            with Image.open(ico_path) as image:
                self.assertEqual(sorted(image.info["sizes"]), dimensions)

    def test_checked_in_ico_has_standard_multi_resolution_layers(self):
        ico_path = ROOT / "share/icons/cutline.ico"
        self.assertTrue(ico_path.is_file(), "share/icons/cutline.ico must exist")
        self._assert_valid_ico(ico_path)

    def test_build_ico_generates_valid_icon_and_supports_fallback(self):
        build_windows = _load_build_windows()
        checked_in = (ROOT / "share/icons/cutline.ico").read_bytes()
        with TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            with mock.patch.object(build_windows.shutil, "which", return_value=None):
                fallback_out = build_windows.build_ico(ROOT / "share/icons/cutline.svg", tmp_dir / "fallback.ico")
            self.assertEqual(fallback_out.read_bytes(), checked_in)

        if Image is not None and shutil.which("rsvg-convert"):
            with TemporaryDirectory() as tmp:
                generated = build_windows.build_ico(ROOT / "share/icons/cutline.svg", Path(tmp) / "fresh.ico")
                self._assert_valid_ico(generated)

    def test_nsis_script_includes_license_shortcuts_directory_verification_and_icons(self):
        nsi = (ROOT / "packaging/windows-installer.nsi").read_text()
        license_text = (ROOT / "LICENSE").read_text()
        self.assertIn("GNU GENERAL PUBLIC LICENSE", license_text)
        self.assertIn("Version 3", license_text)

        for expected in (
            "RequestExecutionLevel user",
            "SetShellVarContext current",
            "SetRegView 64",
            'InstallDir "$LOCALAPPDATA\\Programs\\${APP_NAME}"',
            'InstallDirRegKey HKCU "${UNINSTALL_KEY}" "InstallLocation"',
            '!define MUI_ICON "${ICON}"',
            '!define MUI_UNICON "${ICON}"',
            "!insertmacro MUI_PAGE_WELCOME",
            '!insertmacro MUI_PAGE_LICENSE "${LICENSE_FILE}"',
            "!insertmacro MUI_PAGE_COMPONENTS",
            "!define MUI_PAGE_CUSTOMFUNCTION_LEAVE VerifyInstallDir",
            "!insertmacro MUI_PAGE_DIRECTORY",
            "!insertmacro MUI_PAGE_INSTFILES",
            "!insertmacro MUI_PAGE_FINISH",
            "SectionIn RO",
            'Section "Start Menu shortcut" SecStartMenu',
            'Section /o "Desktop shortcut" SecDesktop',
            '"$SMPROGRAMS\\${APP_NAME}.lnk"',
            '"$DESKTOP\\${APP_NAME}.lnk"',
            '"$INSTDIR\\app\\cutline.ico"',
            'WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayIcon" "$INSTDIR\\app\\cutline.ico"',
            'WriteRegStr HKCU "${UNINSTALL_KEY}" "Publisher"',
            'WriteRegDWORD HKCU "${UNINSTALL_KEY}" "EstimatedSize"',
            '${GetSize} "$INSTDIR" "/S=0K"',
            'Delete "$SMPROGRAMS\\${APP_NAME}.lnk"',
            'Delete "$DESKTOP\\${APP_NAME}.lnk"',
            'RMDir "$INSTDIR"',
        ):
            self.assertIn(expected, nsi)

        # Every MessageBox must carry /SD so silent /S installs cannot hang.
        for line in nsi.splitlines():
            if "MessageBox " in line:
                self.assertIn("/SD ", line, f"MessageBox missing /SD silent default: {line}")

        # Relative +N jumps mixed with LogicLib macros break NSIS jump targets.
        self.assertIsNone(re.search(r"\b[+-]\d+\b", nsi), "Avoid relative jump offsets in NSIS script")

    def test_build_windows_stages_icon_and_crlf_license(self):
        source = (ROOT / "packaging/build-windows.py").read_text()
        self.assertIn("-DICON=", source)
        self.assertIn("-DLICENSE_FILE=", source)
        self.assertIn('app / "cutline.ico"', source)
        self.assertIn('replace("\\n", "\\r\\n")', source)


@unittest.skipIf(qt_gui is None, "Install PySide6 to run Qt icon tests")
class TestWindowsQtIcon(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication([])

    def test_app_icon_falls_back_to_bundled_ico_when_theme_is_unavailable(self):
        with mock.patch.object(qt_gui.QIcon, "fromTheme", return_value=QIcon()):
            icon = qt_gui._app_icon()
        self.assertFalse(icon.isNull())
        sizes = {(size.width(), size.height()) for size in icon.availableSizes()}
        self.assertTrue({(16, 16), (32, 32), (256, 256)}.issubset(sizes))
