#!/usr/bin/env python3
"""Exercise an extracted AppImage using only its bundled Python and libraries."""
import argparse
import os
import pathlib
import subprocess
import tempfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('appdir', type=pathlib.Path)
parser.add_argument('--gui', action='store_true', help='open a window briefly on the current display')
parser.add_argument('--skip-usb-init', action='store_true',
                    help='verify the bundled library without initializing USB in a restricted sandbox')
args = parser.parse_args()
base = args.appdir.resolve()
env = os.environ.copy()
env.update(PYTHONHOME=str(base / 'usr'), PYTHONNOUSERSITE='1',
           PYTHONDONTWRITEBYTECODE='1', GI_TYPELIB_PATH=str(base / 'usr/lib/girepository-1.0'),
           GSETTINGS_SCHEMA_DIR=str(base / 'usr/share/glib-2.0/schemas'),
           GIO_EXTRA_MODULES=str(base / 'usr/lib/gio/modules'),
           XDG_DATA_DIRS=str(base / 'usr/share') + ':/usr/local/share:/usr/share',
           PATH=str(base / 'usr/bin') + ':' + os.environ['PATH'], GSK_RENDERER='cairo')
env['PYTHONPATH'] = os.pathsep.join(str(path) for path in base.glob('usr/lib/python*/site-packages'))
env['CUTLINE_LIBUSB'] = str(base / 'usr/lib/libusb-1.0.so.0')
env['SCC_SMOKE_USB_INIT'] = '0' if args.skip_usb_init else '1'
env.pop('LD_LIBRARY_PATH', None)
command = [str(base / 'usr/lib/ld-linux-x86-64.so.2'), '--library-path',
           str(base / 'usr/lib'), str(base / 'usr/bin/python3')]
code = r'''
import ctypes, os, pathlib, sys, tempfile
base = pathlib.Path(sys.prefix).parent
sys.path.insert(0, str(base / 'usr/lib/cutline'))
import gi, cairo, PIL, usb.core, usb.backend.libusb1
from cutline import transport
assert ctypes.CDLL(os.environ['CUTLINE_LIBUSB']).libusb_get_version
if os.environ['SCC_SMOKE_USB_INIT'] == '1':
    assert transport._bundled_usb_backend() is not None, 'could not initialize bundled libusb'
for module in (gi, cairo, PIL, usb.core):
    assert pathlib.Path(module.__file__).is_relative_to(base), module.__file__
from cutline import job, sheet, template
j = job.build()
assert not j.blocked and not j.liveness_failures
with tempfile.TemporaryDirectory() as temp:
    import subprocess
    proof = pathlib.Path(temp) / 'proof.svg'
    pdf = pathlib.Path(temp) / 'proof.pdf'
    template.write_svg(proof, j.grid, j.paths, mk=j.marks, proof=True)
    subprocess.run(['rsvg-convert', '-f', 'pdf', '-o', str(pdf), str(proof)], check=True)
    width, height, pages = sheet.page_size_mm(pdf)
    assert abs(width - 279.4) < 0.02 and abs(height - 215.9) < 0.02 and pages == 1, (width, height, pages)
    image = sheet.render(pdf, dpi=75)
    assert abs(image.width - 825) <= 1 and abs(image.height - 638) <= 1, image.size
print('Bundled Python/GTK/Pillow/PyUSB, verified job, PDF dimensions and rasterization: PASS')
'''
subprocess.run(command + ['-c', code], env=env, check=True, timeout=30)
qt_code = r'''
import pathlib, sys
base = pathlib.Path(sys.prefix).parent
sys.path.insert(0, str(base / 'usr/lib/cutline'))
from PySide6 import QtCore, QtGui, QtWidgets
from cutline import cli, qt_gui

for module in (QtCore, QtGui, QtWidgets):
    assert pathlib.Path(module.__file__).is_relative_to(base), module.__file__

class SmokeApplication(QtWidgets.QApplication):
    def exec(self):
        self.processEvents()
        window = self.activeWindow()
        assert isinstance(window, qt_gui.Window) and window.job is not None and not window.job.blocked
        plugin = base / 'usr/lib/qt6/plugins/platforms/libqoffscreen.so'
        assert str(plugin) in pathlib.Path('/proc/self/maps').read_text(), 'Qt used a host plugin'
        window.close()
        return 0

qt_gui.QApplication = SmokeApplication
assert cli.main(['gui']) == 0
print('Bundled Qt window and checked preview: PASS')
'''
with tempfile.TemporaryDirectory() as temp:
    qt_env = dict(env, XDG_CURRENT_DESKTOP='KDE', QT_QPA_PLATFORM='offscreen',
                  CUTLINE_CONFIG=str(pathlib.Path(temp) / 'config'),
                  CUTLINE_TEMPLATES=str(pathlib.Path(temp) / 'templates'))
    for name in ('QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH', 'QT_QPA_PLATFORMTHEME'):
        qt_env.pop(name, None)
    subprocess.run(command + ['-c', qt_code], env=qt_env, check=True, timeout=20)
if args.gui:
    gui_code = r'''
import pathlib, sys
sys.path.insert(0, str(pathlib.Path(sys.prefix) / 'lib/cutline'))
from cutline import gui
from gi.repository import Gio, GLib
app = gui.Application()
app.set_flags(Gio.ApplicationFlags.NON_UNIQUE)
passed = []
def check():
    window = app.get_active_window()
    passed.append(window is not None and window.job is not None and not window.job.blocked)
    app.quit()
    return False
GLib.timeout_add_seconds(3, check)
app.run([])
assert passed == [True], 'GUI did not produce a verified job'
print('Bundled GUI window and checked preview: PASS')
'''
    subprocess.run(command + ['-c', gui_code], env=env, check=True, timeout=20)
