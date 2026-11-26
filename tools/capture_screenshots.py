#!/usr/bin/env python3
"""Capture the README screenshots from the Qt app, offscreen.

Runs against a scratch settings folder and a simulated Cameo 4, so the
pictures do not depend on this machine's presets and no cutter is opened.
"""

import dataclasses
import os
import pathlib
import subprocess
import sys
import tempfile
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_scratch = tempfile.TemporaryDirectory()
os.environ["CUTLINE_CONFIG"] = str(pathlib.Path(_scratch.name) / "config")
os.environ["CUTLINE_TEMPLATES"] = str(pathlib.Path(_scratch.name) / "templates")

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from cutline import cutters, editor_model, graphtec, qt_gui, template, transport  # noqa: E402

SCREENSHOTS_DIR = ROOT / "screenshots"
SIZE = (1280, 800)


def _window():
    window = qt_gui.Window()
    window.resize(*SIZE)
    window.refresh_usb()
    window.show()
    QApplication.processEvents()
    if window.job is None:
        window.rebuild()
    return window


def _save(app, widget, name):
    app.processEvents()
    widget.grab().save(str(SCREENSHOTS_DIR / name))
    print(f"Captured screenshots/{name}")


def capture_all():
    SCREENSHOTS_DIR.mkdir(exist_ok=True)
    app = QApplication.instance() or QApplication([])
    device = graphtec.device_by_name("Silhouette Cameo 4")
    def simulated_status(window, *_args, **_kwargs):
        window.set_cutter_status("Cutter status not checked.")

    with mock.patch.object(transport, "usb_endpoints", return_value=[(device, "", (1, 2))]), \
            mock.patch.object(cutters, "wireless_choices", return_value={}), \
            mock.patch.object(qt_gui.Window, "check_device", simulated_status), \
            mock.patch.object(transport, "open_transport", side_effect=transport.NotFound("screenshot")):
        # 1. Design, with the eight-up sheet and its first card selected.
        win = _window()
        with mock.patch.object(win, "message", side_effect=RuntimeError):
            win.open_design()
        design = win.design
        cards = tuple(editor_model.Shape("Rounded rectangle", c.x, c.y, c.w, c.h, radius=3)
                      for c in win.job.grid.cards)
        design.replace_document(dataclasses.replace(design.project, shapes=cards))
        design.select((0,))
        design.dock.open("transform")
        app.processEvents()
        design.canvas.fit()
        _save(app, win, "design-workspace.png")

        # 2. Send, with the default card sheet ready to check and cut.
        win = _window()
        win.tabs.setCurrentIndex(win.tab("Send"))
        _save(app, win, "send-workspace.png")

        # 3. Sheet: a printed sheet's marks measured against the template.
        win = _window()
        with tempfile.TemporaryDirectory() as temp:
            proof_svg = pathlib.Path(temp) / "proof.svg"
            pdf_path = pathlib.Path(temp) / "proof.pdf"
            job = win.job
            art = ""
            for c in job.grid.cards:
                art += (f'<rect x="{c.x + 1:.2f}" y="{c.y + 1:.2f}" width="{c.w - 2:.2f}" '
                        f'height="{c.h - 2:.2f}" rx="3" fill="#264653"/>'
                        f'<rect x="{c.x + 5:.2f}" y="{c.y + 5:.2f}" width="{c.w - 10:.2f}" '
                        f'height="{c.h * 0.5:.2f}" rx="2" fill="#e9c46a"/>'
                        f'<rect x="{c.x + 5:.2f}" y="{c.y + 8 + c.h * 0.5:.2f}" width="{c.w - 10:.2f}" '
                        f'height="{c.h * 0.32:.2f}" rx="2" fill="#f4f1de"/>')
            svg = template.write_svg(None, job.grid, job.paths, mk=job.marks, proof=True)
            proof_svg.write_text(svg.replace('<g id="cut"', f'<g id="artwork">{art}</g><g id="cut"'))
            subprocess.run(["rsvg-convert", "-f", "pdf", "-o", str(pdf_path), str(proof_svg)], check=True)
            win.pdf = pdf_path
            win.sheet_file.setText(pdf_path.name)
            with mock.patch.object(win.tasks, "run", side_effect=lambda work, done: done(work(), None)):
                win.measure_sheet()
            win.tabs.setCurrentIndex(win.tab("Sheet"))
            _save(app, win, "sheet-alignment.png")

        # 4. Dial In a Material.
        win = _window()
        win.open_material_guide()
        scroll = win.tabs.widget(win.tab("Send"))
        if hasattr(scroll, "verticalScrollBar"):
            scroll.verticalScrollBar().setValue(win.guide_box.y() - 12)
        _save(app, win, "material-guide.png")

        # 5. Lighter first passes, shown in the Cut Settings panel.
        win = _window()
        win.tabs.setCurrentIndex(win.tab("Send"))
        win.stage_toggle.setChecked(True)
        win.blade["light_force"].setValue(15)
        _save(app, win.staged_form.parentWidget(), "lighter-first-passes.png")


if __name__ == "__main__":
    capture_all()
