#!/usr/bin/env python3
"""Display-required GUI audit. No USB jobs or settings writes.

Run separately from the fast offline unit suite, with a 30-second bound.
"""
import argparse
import json
import pathlib
import sys
import io
import subprocess
import tempfile
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import traceback
from cutline import gui, layout_presets, sheet, template
from gi.repository import Gio, GLib, Gtk

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output', type=pathlib.Path, default=pathlib.Path('out/ui-audit'))
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=True)
results = {'checks': [], 'screenshots': [], 'capture_errors': []}
app = gui.Application()
app.set_application_id('io.github.zoro11031.Cutline.Audit')
app.set_flags(Gio.ApplicationFlags.NON_UNIQUE)
pages = iter(('layout', 'sheet', 'cut'))
failed = []
cameo4 = next(d for d in gui.graphtec.DEVICES if d.name == 'Silhouette Cameo 4')
fake_usb = [(cameo4, '/dev/usb/lp0', (1, 2))]


def capture(window, name):
    try:
        paintable = Gtk.WidgetPaintable.new(window)
        snapshot = Gtk.Snapshot.new()
        paintable.snapshot(snapshot, window.get_width(), window.get_height())
        node = snapshot.to_node()
        texture = window.get_renderer().render_texture(node, None)
        target = args.output / (name + '.png')
        if not texture.save_to_png(str(target)):
            raise RuntimeError('PNG save failed')
        results['screenshots'].append(str(target))
    except Exception as error:
        results['capture_errors'].append(str(error))


def next_page():
    try:
        window = app.get_active_window()
        name = next(pages, None)
        if name is None:
            results['window_size'] = [window.get_width(), window.get_height()]
            app.quit()
            return False
        window.stack.set_visible_child_name(name)
        GLib.timeout_add(250, snapshot_page, name)
    except Exception:
        failed.append(traceback.format_exc())
        app.quit()
    return False


def snapshot_page(name):
    capture(app.get_active_window(), name)
    results['checks'].append('Page opens: ' + name)
    GLib.idle_add(next_page)
    return False


def audit():
    try:
        window = app.get_active_window()
        window.refresh_usb()
        window._checking_status = False
        window.update_send_actions()
        assert window.job is not None and not window.job.blocked
        assert window.cut_button.get_sensitive()
        results['checks'].append('Initial preview builds and verifies')
        assert window.get_title() == 'Cutline'
        assert Gtk.Window.get_default_icon_name() == gui.APP_ID
        icon_theme = Gtk.IconTheme.get_for_display(window.get_display())
        assert icon_theme.has_icon(gui.APP_ID)
        results['checks'].append('Renamed window and taskbar icon resolve through the installed icon theme')
        assert not window.advanced_toggle.get_active()
        assert not window.advanced_panel.get_visible()
        window.advanced_toggle.set_active(True)
        assert window.advanced_panel.get_visible()
        window.advanced_toggle.set_active(False)
        assert not window.advanced_panel.get_visible()
        results['checks'].append('Diagnostics hidden by default and shown only through Advanced details')
        with mock.patch.object(gui.config, 'save_settings') as save:
            window.colorblind_toggle.set_active(True)
            assert window.preview.colorblind
            window.colorblind_toggle.set_active(False)
            assert not window.preview.colorblind
            save.assert_called()
        results['checks'].append('Colorblind palette switches on/off and saves its preference')
        assert window.r_pattern.get_model().get_n_items() == 2
        results['checks'].append('Only standard registration patterns are offered')
        for selected, profile in enumerate(layout_presets.BUILTIN.values(), 1):
            window.r_layout_preset.set_selected(selected)
            expected = profile.options()
            actual = window.options()
            for key, value in expected.items():
                assert actual[key] == value, (profile.name, key, actual[key], value)
            assert not window.job.blocked
            assert window.job.grid.card.radius_mm == 3.2
            results['checks'].append('Preset applies exact geometry: ' + profile.name)
        # Exercise the real handlers, intercepting only dialog presentation.
        for handler in (window.on_open_artwork, window.on_pick_pdf):
            with mock.patch.object(gui.Gtk, 'FileDialog') as dialog:
                handler()
                dialog.return_value.open.assert_called_once()
        results['checks'].append('SVG/DXF and PDF button handlers build filtered dialogs')
        window.r_register.set_active(False)
        assert not window.job.registration
        assert b'TB123' not in window.job.stream
        window.r_register.set_active(True)
        results['checks'].append('Registration switch changes verified stream')
        window.r_cutter.set_selected(0)
        assert window.job.blocked and not window.cut_button.get_sensitive()
        window.r_cutter.set_selected([d.name for d in gui.graphtec.DEVICES].index('Silhouette Cameo 4'))
        assert not window.job.blocked and window.cut_button.get_sensitive()
        results['checks'].append('Incompatible sheet/cutter disables Cut and recovers')
        window.r_layout_preset.set_selected(1)
        with tempfile.TemporaryDirectory() as temp:
            proof = pathlib.Path(temp) / 'artwork.svg'
            pdf = pathlib.Path(temp) / 'artwork.pdf'
            j = window.job
            svg = template.write_svg(None, j.grid, j.paths, mk=j.marks, proof=True)
            svg = svg.replace('<g id="cut"', '<g><rect x="40" y="40" width="30" height="60" fill="#2472bd"/></g><g id="cut"')
            proof.write_text(svg)
            subprocess.run(['rsvg-convert', '-f', 'pdf', '-o', str(pdf), str(proof)], check=True)
            image = sheet.render(pdf, dpi=100, colour=True)
            report = sheet.measure(pdf, j.marks, dpi=100, image=image)
            stream = io.BytesIO()
            image.save(stream, format='PNG')
            token = window._sheet_token = object()
            window._sheet_done(report, report.problems(j.grid.sheet_w_mm, j.grid.sheet_h_mm), stream.getvalue(), token)
            assert window.preview.pdf_surface is not None
            assert window.preview.pdf_size_mm == (report.page_w_mm, report.page_h_mm)
            assert image.getpixel((int(50 * 100 / 25.4), int(50 * 100 / 25.4)))[2] > 150
            results['checks'].append('Full-colour PDF artwork renders behind paths at its physical page dimensions')
        GLib.idle_add(next_page)
    except Exception:
        failed.append(traceback.format_exc())
        app.quit()
    return False


def start(*_):
    GLib.timeout_add(250, audit)


app.connect('activate', start)
GLib.timeout_add_seconds(25, lambda: (failed.append('audit timed out'), app.quit(), False)[-1])
with mock.patch.object(gui.transport, 'open_transport', side_effect=AssertionError('USB prohibited during UI audit')), \
     mock.patch.object(gui.transport, 'usb_endpoints', return_value=fake_usb):
    app.run([])
results['failures'] = failed
(args.output / 'report.json').write_text(json.dumps(results, indent=2) + '\n')
print(json.dumps(results, indent=2))
raise SystemExit(bool(failed))
