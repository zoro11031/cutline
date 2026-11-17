#!/usr/bin/env python3
"""Capture up-to-date screenshots of the desktop workspace for the README."""

import io
import os
import pathlib
import subprocess
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication
from cut_proxies import qt_gui, template, sheet
SCREENSHOTS_DIR = ROOT / "screenshots"
SCREENSHOTS_DIR.mkdir(exist_ok=True)


def capture_all():
    app = QApplication.instance() or QApplication([])

    # 1. Main Send workspace
    win = qt_gui.Window()
    win.resize(1200, 800)
    win.tabs.setCurrentIndex(win.tab("Send"))
    win.show()
    app.processEvents()
    pix = win.grab()
    pix.save(str(SCREENSHOTS_DIR / "send-workspace.png"))
    print("Captured screenshots/send-workspace.png")

    # 2. Design workspace
    win_design = qt_gui.Window()
    win_design.resize(1200, 800)
    win_design.open_design()
    win_design.show()
    app.processEvents()
    pix = win_design.grab()
    pix.save(str(SCREENSHOTS_DIR / "design-workspace.png"))
    print("Captured screenshots/design-workspace.png")

    # 3. Sheet verification / Print alignment
    win_sheet = qt_gui.Window()
    win_sheet.resize(1200, 800)
    with tempfile.TemporaryDirectory() as temp:
        proof_svg = pathlib.Path(temp) / "proof.svg"
        pdf_path = pathlib.Path(temp) / "proof.pdf"
        j = win_sheet.job
        cards_svg = ""
        for c in j.grid.cards:
            x, y, w, h = c.x, c.y, c.w, c.h
            cards_svg += (
                f'<rect x="{x+1:.2f}" y="{y+1:.2f}" width="{w-2:.2f}" height="{h-2:.2f}" '
                f'rx="3" fill="#1c3144" stroke="#a0b2c6" stroke-width="0.5"/>'
                f'<rect x="{x+4:.2f}" y="{y+4:.2f}" width="{w-8:.2f}" height="{h*0.48:.2f}" '
                f'rx="2" fill="#2a5078"/>'
                f'<text x="{x+7:.2f}" y="{y+13:.2f}" font-size="4.5" fill="#ffffff" '
                f'font-family="sans-serif" font-weight="bold">Proxy Card</text>'
                f'<rect x="{x+4:.2f}" y="{y+4+h*0.52:.2f}" width="{w-8:.2f}" height="{h*0.38:.2f}" '
                f'rx="2" fill="#f4f6f8" stroke="#a0b2c6" stroke-width="0.3"/>'
            )
        svg = template.write_svg(None, j.grid, j.paths, mk=j.marks, proof=True)
        svg = svg.replace('<g id="cut"', f'<g id="artwork">{cards_svg}</g><g id="cut"')
        proof_svg.write_text(svg)
        subprocess.run(
            ["rsvg-convert", "-f", "pdf", "-o", str(pdf_path), str(proof_svg)],
            check=True,
        )
        img = sheet.render(pdf_path, dpi=150, colour=True)
        rep = sheet.measure(pdf_path, j.marks, dpi=150, image=img)
        buf = io.BytesIO()
        img.save(buf, format="PNG")

        win_sheet.tabs.setCurrentIndex(win_sheet.tab("Sheet"))
        win_sheet.preview.show_pdf(buf.getvalue(), (j.grid.sheet_w_mm, j.grid.sheet_h_mm), rep)
        win_sheet.show()
        app.processEvents()
        pix = win_sheet.grab()
        pix.save(str(SCREENSHOTS_DIR / "sheet-alignment.png"))
        print("Captured screenshots/sheet-alignment.png")

    # 4. Dial In a Material / Guided test cut
    win_guide = qt_gui.Window()
    win_guide.resize(1200, 800)
    win_guide.show()
    win_guide.open_material_guide()
    # Scroll to guide_box
    scroll = win_guide.tabs.widget(win_guide.tab("Send"))
    if hasattr(scroll, "verticalScrollBar"):
        scroll.verticalScrollBar().setValue(win_guide.guide_box.y() - 12)
    app.processEvents()
    pix = win_guide.grab()
    pix.save(str(SCREENSHOTS_DIR / "material-guide.png"))
    print("Captured screenshots/material-guide.png")


if __name__ == "__main__":
    capture_all()
