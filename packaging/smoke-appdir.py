#!/usr/bin/env python3
"""Exercise an extracted AppImage using only its bundled Python and libraries."""
import argparse
import os
import pathlib
import subprocess

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('appdir', type=pathlib.Path)
parser.add_argument('--gui', action='store_true', help='open a window briefly on the current display')
args = parser.parse_args()
base = args.appdir.resolve()
env = os.environ.copy()
env.update(PYTHONHOME=str(base / 'usr'), PYTHONNOUSERSITE='1',
           PYTHONDONTWRITEBYTECODE='1', GI_TYPELIB_PATH=str(base / 'usr/lib/girepository-1.0'),
           GSETTINGS_SCHEMA_DIR=str(base / 'usr/share/glib-2.0/schemas'),
           GIO_EXTRA_MODULES=str(base / 'usr/lib/gio/modules'),
           XDG_DATA_DIRS=str(base / 'usr/share') + ':/usr/local/share:/usr/share',
           PATH=str(base / 'usr/bin') + ':' + os.environ['PATH'], GSK_RENDERER='cairo')
env.pop('PYTHONPATH', None)
command = [str(base / 'usr/lib/ld-linux-x86-64.so.2'), '--library-path',
           str(base / 'usr/lib'), str(base / 'usr/bin/python3')]
code = r'''
import pathlib, sys, tempfile
base = pathlib.Path(sys.prefix).parent
sys.path.insert(0, str(base / 'usr/lib/cut-proxies'))
import gi, cairo, PIL, usb.core, usb.backend.libusb1
assert usb.backend.libusb1.get_backend() is not None, 'bundled libusb not found'
for module in (gi, cairo, PIL, usb.core):
    assert pathlib.Path(module.__file__).is_relative_to(base), module.__file__
from cut_proxies import job, sheet, template
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
if args.gui:
    gui_code = r'''
import pathlib, sys
sys.path.insert(0, str(pathlib.Path(sys.prefix) / 'lib/cut-proxies'))
from cut_proxies import gui
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
