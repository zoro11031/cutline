"""Cutline: a GTK4 / libadwaita front end.

Layout, Sheet, Materials and Send share one live preview.

The preview is not decoration.  A cut template is a set of coordinates that
have to agree with ink already on a sheet, and the fastest way to catch a
template that is subtly wrong is to look at it next to the marks it has to
line up with.  So the preview draws what the cutter will actually do: the
sheet, the registration marks at their real size, the exclusion zones around
them, and the flattened cut paths - the same points that go down the wire,
not an idealised redraw.

The Cut button follows the rule the printer driver settled on: it stays
insensitive until the job has been built and every check has passed for the
exact settings currently on screen.  Changing any control tears the result
down again, so the button can never be describing a different job from the one
in front of you.

The same module id has to serve as the GApplication id, the installed
.desktop basename and the installed icon name, or the window gets a generic
placeholder icon from the compositor.
"""

from __future__ import annotations

from dataclasses import replace
from functools import lru_cache
import math
import io
import pathlib
import threading
import time
import traceback
import zipfile
import cairo

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango  # noqa: E402

from . import (actions, bluetooth, bundle, config, confirmation, corner_test, geometry, graphtec, job as job_mod, layout_presets, presets, regmarks,
               sheet as sheet_mod, starting_guide, template, testcut, toolpath, tooling, transport)  # noqa: E402
from .desktop_help import (BLADE_VALUE_TEXT, CONTROL_HELP, CUT_PROBLEMS, CUT_SETTINGS,  # noqa: E402
                           DIAL_IN, OPEN_SHEET, REGISTRATION_PROBLEM, SETUP_STEPS)
from .editor_canvas import DesignCanvas, draw_feed_arrow  # noqa: E402
from .gtk_controls import (choose_inkscape_page, disclosure, menu_item, protect_settings_wheel, workspace_header,  # noqa: E402
                           workspace_tools, zoom_controls)
from .holder_settings import HolderSettings, for_holder  # noqa: E402

APP_ID = "io.github.zoro11031.Cutline"
PREVIEW_MODES = ("Cut paths", "Print PDF", "Overlay")

_CUT_SETTINGS_CSS = """
@define-color cutline_accent #4c64ad;
@define-color cutline_accent_fg #ffffff;
.scc-tabs button { min-width: 0; padding: 6px 12px; }
.scc-cut-settings row > box.header { min-height: 36px; padding: 4px 12px; }
.scc-cut-settings spinbutton button { min-width: 18px; min-height: 28px; padding: 0 4px; }
.scc-cut-settings .holder-tabs { padding: 6px 8px; }
.scc-cut-settings .cut-toggles { padding: 6px 12px; }
.scc-workspace button:disabled {
    opacity: 1;
    filter: none;
    color: mix(@window_fg_color, @window_bg_color, 0.35);
    background: alpha(@window_fg_color, 0.08);
    box-shadow: none;
}
.scc-workspace headerbar button.toggle:checked,
.scc-design .scc-design-rail button.toggle:checked,
.scc-design .scc-design-rail button.flat:checked,
.scc-design .scc-panel-rail button.toggle:checked,
.scc-design .scc-panel-rail button.flat:checked,
.scc-design .scc-origin-cell.active {
    background-color: @cutline_accent;
    background-image: none;
    color: @cutline_accent_fg;
}
.scc-design-commands {
    border: 0;
    border-bottom: 1px solid alpha(@window_fg_color, 0.14);
    border-radius: 0;
    background: mix(@window_bg_color, @view_bg_color, 0.35);
}
.scc-design-sidebar {
    border: 0;
    border-radius: 0;
    background: @window_bg_color;
}
.scc-design-rail, .scc-panel-rail {
    background: mix(@window_bg_color, @view_bg_color, 0.35);
}
.scc-design-options { border: 0; }
.scc-design-commands entry, .scc-design-commands spinbutton {
    min-height: 30px;
    padding: 0 6px;
}
.scc-design .scc-objects-list {
    background: @window_bg_color;
    border: 0;
    box-shadow: none;
}
.scc-design .scc-objects-list row {
    min-height: 32px;
    padding: 0;
    border-radius: 4px;
}
.scc-design .scc-objects-list row:selected,
.scc-design .boxed-list row:selected {
    background: alpha(@cutline_accent, 0.28);
    color: @view_fg_color;
}
.scc-design-rail button:focus-visible, .scc-panel-rail button:focus-visible,
.scc-design-commands button:focus-visible,
.scc-design-commands menubutton > button:focus-visible, .scc-design-sidebar button:focus-visible {
    outline: 2px solid @cutline_accent;
    outline-offset: -2px;
}
.scc-design-rail .scc-tool-flyout > button { min-width: 14px; padding: 0; }
.scc-transform-grid spinbutton { min-height: 30px; min-width: 0; }
.scc-transform-grid spinbutton > text { min-width: 56px; padding: 0 4px; }
.scc-transform-grid spinbutton > button { min-width: 18px; min-height: 26px; padding: 0 2px; }
.scc-origin-cell {
    min-width: 9px;
    min-height: 9px;
    padding: 0;
    margin: 5px;
    border-radius: 1px;
    background: alpha(@window_fg_color, 0.45);
}
.scc-object-visibility:checked, .scc-object-lock:checked { background: transparent; }
.scc-object-visibility, .scc-object-lock { padding: 2px 4px; min-height: 26px; min-width: 26px; }
.scc-object-lock { opacity: 0.34; }
.scc-object-lock:hover { opacity: 0.85; }
.scc-object-lock.locked { opacity: 1.0; color: @cutline_accent; }
.scc-notice {
    border: 0;
    border-bottom: 1px solid @cutline_accent;
    border-radius: 0;
    background: alpha(@cutline_accent, 0.12);
}
.scc-notice.scc-notice-warning {
    border-color: @error_color;
    background: alpha(@error_color, 0.12);
}
"""


@lru_cache(maxsize=1)
def _install_cut_settings_css(display):
    provider = Gtk.CssProvider()
    provider.load_from_data(_CUT_SETTINGS_CSS.encode())
    Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


def cut_refusal(current, confirmed, sending):
    """Recheck the job at confirmation, including changes while a dialog is open."""
    if sending:
        return 'The cutter connection is busy.'
    if confirmed is None or current is not confirmed:
        return 'The job changed. Check and confirm the current job again.'
    if confirmed.blocked:
        return 'The job does not check out.'
    return ''


def sheet_request_current(current, returned):
    return current is not None and returned is current


def design_review_pending(window):
    reviewed = getattr(window, "reviewed_design", None)
    return reviewed is not None and window.design is not None and window.design.project != reviewed


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def _combo(title, items, selected=0, subtitle=None):
    row = Adw.ComboRow(title=title)
    if subtitle:
        row.set_subtitle(subtitle)
    row.set_model(Gtk.StringList.new([str(i) for i in items]))
    row.set_selected(selected)
    return row


def _spin(title, lower, upper, value, step=1.0, digits=0, subtitle=None):
    row = Adw.SpinRow(
        title=title,
        adjustment=Gtk.Adjustment(lower=lower, upper=upper, value=value,
                                  step_increment=step,
                                  page_increment=step * 10))
    row.set_digits(digits)
    if subtitle:
        row.set_subtitle(subtitle)
    return row


def _switch(title, active, subtitle=None):
    row = Adw.SwitchRow(title=title, active=active)
    if subtitle:
        row.set_subtitle(subtitle)
    return row


def _special_value(row, value, text):
    def display(spin):
        if spin.get_value() == value:
            spin.set_text(text)
            return True
        return False

    row.set_width_chars(max(row.get_width_chars(), len(text)))
    row.connect("output", display)
    display(row)
    return row


from . import cutters



class ControlPage(Gtk.ScrolledWindow):
    """Scrollable controls that fill their pane instead of a preferences clamp."""

    def __init__(self):
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER,
                         vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
                         hexpand=True, vexpand=True)
        self.controls = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        for side in ('top', 'bottom', 'start', 'end'):
            getattr(self.controls, 'set_margin_' + side)(16)
        self.set_child(self.controls)

    def add(self, group):
        self.controls.append(group)


# --------------------------------------------------------------------------
# the preview
# --------------------------------------------------------------------------

class Preview(Gtk.DrawingArea):
    """Draws the sheet, the marks, their exclusion zones and the cut paths."""

    __gsignals__ = {"pdf-changed": (GObject.SignalFlags.RUN_LAST, None, ()),
                    "zoom-changed": (GObject.SignalFlags.RUN_LAST, None, (float,))}

    def __init__(self):
        super().__init__()
        self.mode = "Overlay"
        self.job = None
        self.trial = None
        self.test_placement = None
        self.test_spots = ()
        self.test_grid = {}
        self.active_spot_rect = None
        self.on_spot_clicked = None
        self.zoom = 1.0
        self.pan = self.pan_start = (0, 0)
        self.sheet = None          # a sheet.SheetReport, when a PDF is loaded
        self.pdf_surface = None
        self.pdf_size_mm = None
        self.colorblind = False
        self.set_draw_func(self._draw)
        self.set_content_width(280)
        self.set_content_height(300)
        self.set_hexpand(True)
        self.set_vexpand(True)
        pan = Gtk.GestureDrag(button=1)
        pan.connect("drag-begin", lambda *_: setattr(self, "pan_start", self.pan))
        pan.connect("drag-update", self.pan_preview)
        self.add_controller(pan)
        click = Gtk.GestureClick(button=1)
        click.connect("pressed", self.on_preview_clicked)
        self.add_controller(click)
        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.connect("scroll", self.scroll_zoom)
        self.add_controller(scroll)

    def to_sheet_mm(self, px, py):
        w = self.get_width()
        h = self.get_height()
        sheet_w, sheet_h = self.page_size()
        _, _, scale = self.viewport(w, h, sheet_w, sheet_h)
        scale = max(0.01, scale * self.zoom)
        dx = (w - sheet_w * scale) / 2 + self.pan[0]
        dy = (h - sheet_h * scale) / 2 + self.pan[1]
        return (px - dx) / scale, (py - dy) / scale

    def on_preview_clicked(self, gesture, n_press, x, y):
        if self.test_placement and hasattr(self, "on_spot_clicked") and self.on_spot_clicked:
            sx, sy = self.to_sheet_mm(x, y)
            self.on_spot_clicked(sx, sy)

    def set_job(self, job):
        before = self.page_size()
        self.job = job
        if self.page_size() != before:
            self.fit()
        self.queue_draw()

    def set_trial(self, trial, spots=(), placement=None, active_rect=None, *, grid=None):
        placement = trial.placement if trial else placement
        grid = grid or {}
        if ((trial, spots, placement, active_rect, grid)
                != (self.trial, self.test_spots, self.test_placement, self.active_spot_rect, self.test_grid)):
            previous = self.page_size()
            self.trial, self.test_spots, self.test_placement, self.active_spot_rect = trial, spots, placement, active_rect
            self.test_grid = grid
            if self.page_size() != previous:
                self.fit()
            self.queue_draw()

    def page_size(self):
        if self.test_placement:
            return self.test_placement.width, self.test_placement.height
        if self.job:
            return self.job.grid.sheet_w_mm, self.job.grid.sheet_h_mm
        return self.pdf_size_mm

    def fit(self):
        self.zoom = 1.0
        self.pan = (0, 0)
        self.emit("zoom-changed", self.zoom)
        self.queue_draw()

    def zoom_by(self, factor):
        self.zoom = min(8.0, max(0.3, self.zoom * factor))
        self.emit("zoom-changed", self.zoom)
        self.queue_draw()

    def scroll_zoom(self, controller, _dx, dy):
        if not dy or not controller.get_current_event_state() & Gdk.ModifierType.CONTROL_MASK:
            return False
        self.zoom_by(1.25 if dy < 0 else 0.8)
        return True

    def pan_preview(self, _gesture, dx, dy):
        self.pan = self.pan_start[0] + dx, self.pan_start[1] + dy
        self.queue_draw()

    def set_sheet(self, report):
        self.sheet = report
        self.queue_draw()

    def set_pdf(self, png=None, width_mm=None, height_mm=None):
        before = self.page_size()
        had_pdf = self.pdf_surface is not None
        self.pdf_surface = (cairo.ImageSurface.create_from_png(io.BytesIO(png))
                            if png is not None else None)
        self.pdf_size_mm = (width_mm, height_mm) if png is not None else None
        if had_pdf != (self.pdf_surface is not None):
            self.emit("pdf-changed")
        if self.page_size() != before:
            self.fit()
        self.queue_draw()

    def set_mode(self, mode):
        self.mode = mode
        self.queue_draw()

    def set_colorblind(self, enabled):
        self.colorblind = bool(enabled)
        self.queue_draw()

    @staticmethod
    def viewport(width, height, sheet_width, sheet_height):
        scale = min((width - 32) / sheet_width, (height - 32) / sheet_height)
        return (width - sheet_width * scale) / 2, (height - sheet_height * scale) / 2, scale

    def apply_camera(self, cr, width, height, sheet_width, sheet_height):
        _, _, scale = self.viewport(width, height, sheet_width, sheet_height)
        scale = max(0.01, scale * self.zoom)
        cr.translate((width - sheet_width * scale) / 2 + self.pan[0],
                     (height - sheet_height * scale) / 2 + self.pan[1])
        cr.scale(scale, scale)
        return scale

    def draw_trial(self, cr, width, height):
        sheet_w, sheet_h = self.page_size()
        scale = self.apply_camera(cr, width, height, sheet_w, sheet_h)
        cr.set_source_rgb(1, 1, 1)
        cr.rectangle(0, 0, sheet_w, sheet_h)
        cr.fill_preserve()
        cr.set_line_width(1 / scale)
        cr.set_source_rgb(0.55, 0.62, 0.68)
        cr.stroke()
        self.draw_orientation(cr, sheet_w, scale)
        marks = self.test_placement.marks
        if marks:
            DesignCanvas.draw_marks(cr, marks, scale)
        active = self.trial.sheet.cells[0] if self.trial else None
        active_rect = active.rect if active else self.active_spot_rect
        cr.set_line_width(0.45)
        cr.select_font_face("Sans")
        cr.set_font_size(min(6, max(4, 11 / scale)))
        for number, box in self.test_grid.items():
            if box == active_rect:
                continue
            cr.rectangle(box.x, box.y, box.w, box.h)
            cr.set_source_rgb(0.71, 0.76, 0.8)
            cr.stroke()
            cr.move_to(box.x + 1, box.y + 6)
            cr.set_source_rgb(0.31, 0.38, 0.43)
            cr.show_text(str(number))
        if active_rect:
            sample = active_rect
            cr.rectangle(sample.x, sample.y, sample.w, sample.h)
            cr.set_source_rgba(0.18, 0.56, 0.69, 0.20)
            cr.fill_preserve()
            cr.set_source_rgb(0, 0, 0)
            cr.set_line_width(0.75)
            cr.stroke()
            cr.set_source_rgb(1, 1, 1)
            cr.set_line_width(0.45)
            cr.set_dash([1.0, 1.0])
            cr.rectangle(sample.x + 0.35, sample.y + 0.35, sample.w - 0.7, sample.h - 0.7)
            cr.stroke()
            cr.set_dash([])
            active_num = (active.index + 1) if active else None
            if active_num is None or all(spot.number != active_num for spot in self.test_spots):
                cr.move_to(sample.x + 1, sample.y + 6)
                cr.set_source_rgb(0.07, 0.22, 0.30)
                cr.show_text(str(active_num) if active_num else "")
        for spot in self.test_spots:
            box = spot.rect
            color = (0.60, 0.30, 0.07) if spot.uncertain else (0.09, 0.39, 0.34)
            cr.rectangle(box.x, box.y, box.w, box.h)
            cr.set_source_rgba(*color, 0.12)
            cr.fill_preserve()
            cr.set_source_rgb(*color)
            cr.stroke()
            cr.move_to(box.x + 1, box.y + 6)
            cr.show_text(f"{spot.number}: {spot.passes}{'?' if spot.uncertain else ''}")
        if self.trial:
            cr.set_source_rgb(0.05, 0.24, 0.39)
            cr.set_line_width(0.6)
            for path in self.trial.sheet.paths:
                cr.move_to(*path.points[0])
                for point in path.points[1:]:
                    cr.line_to(*point)
                if path.closed:
                    cr.close_path()
                cr.stroke()

    @staticmethod
    def draw_orientation(cr, width, scale):
        dark = Adw.StyleManager.get_default().get_dark()
        color = (0.83, 0.87, 0.91) if dark else (0.32, 0.37, 0.41)
        draw_feed_arrow(cr, width, color, min(1, 1.2 / scale))

    def _draw(self, _area, cr, width, height):
        dark = Adw.StyleManager.get_default().get_dark()
        bg = (0.14, 0.15, 0.17) if dark else (0.89, 0.90, 0.92)
        cr.set_source_rgb(*bg)
        cr.paint()
        if self.test_placement:
            self.draw_trial(cr, width, height)
            return

        pdf = self.pdf_surface if self.mode != "Cut paths" or self.job is None else None
        pdf_size = self.pdf_size_mm if pdf is not None else None
        j = self.job if self.mode != "Print PDF" or pdf is None else None
        if j is None and pdf_size is None:
            cr.set_source_rgb(0.5, 0.5, 0.52)
            cr.select_font_face("Sans")
            cr.set_font_size(13)
            msg = "Choose a layout to see the template"
            extents = cr.text_extents(msg)
            cr.move_to((width - extents.width) / 2, height / 2)
            cr.show_text(msg)
            return

        W, H = (j.grid.sheet_w_mm, j.grid.sheet_h_mm) if j else pdf_size
        canvas_w, canvas_h = (max(W, pdf_size[0]), max(H, pdf_size[1])) if pdf_size else (W, H)
        scale = self.apply_camera(cr, width, height, canvas_w, canvas_h)
        hair = 1.0 / scale

        # the sheet
        cr.set_source_rgb(1, 1, 1)
        cr.rectangle(0, 0, W, H)
        cr.fill()
        if pdf is not None:
            cr.save()
            cr.scale(pdf_size[0] / pdf.get_width(), pdf_size[1] / pdf.get_height())
            cr.set_source_surface(pdf)
            cr.paint()
            cr.restore()
        cr.set_source_rgb(0.6, 0.6, 0.62)
        cr.set_line_width(hair)
        cr.rectangle(0, 0, W, H)
        cr.stroke()
        self.draw_orientation(cr, W, scale)
        if j is None:
            return

        mk = j.marks
        good_color = (0.0, 0.35, 0.75) if self.colorblind else (0.1, 0.55, 0.25)
        bad_color = (0.85, 0.35, 0.0) if self.colorblind else (0.85, 0.2, 0.15)

        if j.registration:
            # exclusion zones, so a collision is visible rather than described
            cr.set_source_rgba(*bad_color, 0.13)
            for z in mk.corner_zones():
                cr.rectangle(z.x, z.y, z.w, z.h)
                cr.fill()

            # registration marks, at their printed size
            cr.set_source_rgba(0, 0, 0, 0.0 if pdf is not None else 1.0)
            if mk.square is not None:
                s = mk.square
                cr.rectangle(s.x, s.y, s.w, s.h)
                cr.fill()
            cr.set_line_width(mk.thickness_mm)
            cr.set_line_cap(1)      # butt
            for ell in mk.ells:
                for x1, y1, x2, y2 in ell.arms():
                    cr.move_to(x1, y1)
                    cr.line_to(x2, y2)
                    cr.stroke()

        # Amber marks the card by the bottom-left mark, to cover for the scan.
        crowded = {h["index"] for h in j.mask_hints}
        skipped = set(j.skip)
        for i, rect in enumerate(j.grid.cards):
            if i in skipped:
                continue
            if pdf is not None:
                continue
            if i in crowded:
                cr.set_source_rgba(0.90, 0.64, 0.23, 0.38)
                cr.rectangle(rect.x, rect.y, rect.w, rect.h)
                cr.fill()
                cr.save()
                cr.set_source_rgba(0.71, 0.34, 0.07, 0.9)
                cr.set_line_width(0.4)
                cr.set_dash([1.5, 1.5], 0)
                cr.rectangle(rect.x, rect.y, rect.w, rect.h)
                cr.stroke()
                cr.restore()
            else:
                cr.set_source_rgba(0.25, 0.5, 0.85, 0.12)
                cr.rectangle(rect.x, rect.y, rect.w, rect.h)
                cr.fill()

        # The physical placement includes the same correction as the stream.
        cr.save()
        cr.translate(*j.offset_mm)
        bad = bool(j.blocked)
        cr.set_line_width(max(hair, 0.25))
        for p in j.paths:
            weed = toolpath.is_weed_path(p)
            cr.set_source_rgb(*(bad_color if bad else (0.1, 0.45, 0.75) if weed else good_color))
            cr.set_dash([2.0, 1.0] if weed or (self.colorblind and bad) else [])
            pts = p.points
            if not pts:
                continue
            cr.move_to(*pts[0])
            for x, y in pts[1:]:
                cr.line_to(x, y)
            if p.closed:
                cr.close_path()
            cr.stroke()
        cr.restore()

        # where the marks actually are on a loaded PDF, against where the
        # template wants them. This is the whole alignment question, visible.
        if pdf is not None and self.sheet is not None and j.registration:
            for fm in self.sheet.marks:
                if fm.ink_box is not None:
                    b = fm.ink_box
                    good = fm.is_credible and (fm.error_mm or 0) <= 0.5
                    cr.set_source_rgba(*(good_color if good else bad_color), 0.95)
                    cr.set_dash([1.5, 0.8] if self.colorblind and not good else [])
                    cr.set_line_width(max(hair, 0.4))
                    cr.rectangle(b.x, b.y, b.w, b.h)
                    cr.stroke()
                    cr.set_dash([])
                    if self.colorblind:
                        # Shape and text duplicate color, including on a
                        # grayscale display or a monochrome screenshot.
                        label = 'OK' if good else '!'
                        size = max(2.5, 11 * hair)
                        cr.select_font_face('Sans')
                        cr.set_font_size(size)
                        extents = cr.text_extents(label)
                        cr.set_source_rgba(1, 1, 1, 0.95)
                        cr.rectangle(b.x, b.y - size - 1, extents.width + 1.5, size + 1)
                        cr.fill()
                        cr.set_source_rgb(*(good_color if good else bad_color))
                        cr.move_to(b.x + 0.5, b.y - 1)
                        cr.show_text(label)
                if fm.measured is not None:
                    mx, my = fm.measured
                    cr.move_to(mx - 2, my)
                    cr.line_to(mx + 2, my)
                    cr.move_to(mx, my - 2)
                    cr.line_to(mx, my + 2)
                    cr.stroke()

        # the registration origin: every cut coordinate is measured from here
        if not j.registration:
            return
        ox, oy = mk.origin_mm
        cr.set_source_rgb(0.9, 0.45, 0.1)
        cr.set_line_width(max(hair, 0.3))
        cr.move_to(ox - 3, oy)
        cr.line_to(ox + 3, oy)
        cr.move_to(ox, oy - 3)
        cr.line_to(ox, oy + 3)
        cr.stroke()


# --------------------------------------------------------------------------
# the window
# --------------------------------------------------------------------------

class Window(Adw.ApplicationWindow):

    def __init__(self, app):
        super().__init__(application=app, title="Cutline",
                         default_width=1120, default_height=760)
        self.job = None
        self.sheet_report = None
        self.pdf_path = None
        self.pdf_bundle = None
        self.bundle_row_error = None
        self.bundle_checked = False
        self.pdf_check_state = sheet_mod.RegistrationDecision.NONE
        self.checked_marks = None
        self.checked_scm_orientation = False
        self.detected_tools = ()
        self.tool_selection = tooling.ToolSelection()
        self.tools_connection = None
        self.corrected_tools = None
        self._bundle_locked = False
        self._job_locked = False
        self._job_control_states = {}
        self.active_job = None
        self._sheet_token = None
        self.imported = None
        self._send_source = None
        self._send_mode = "simple"
        self._send_profiles = ()
        self._send_choices = {}
        self._send_defaults = {}
        self._send_rows = []
        self.template_shapes = ()
        self.design = None
        self.reviewed_design = self.design_origin = None
        self._close_confirmed = False
        self._building = False
        self._sending = False
        self.cut_control = None
        self._checking_status = False
        self.tuning = False
        self.last_send_tab = "cut"
        self.holder_state = HolderSettings()
        self.guide = starting_guide.Guide(config.CONFIG_DIR / "material-test.json")
        self._suppress = True        # don't rebuild while constructing rows

        self.toasts = Adw.ToastOverlay()
        self.set_content(self.toasts)

        outer = Adw.ToolbarView()
        outer.add_css_class("scc-workspace")
        self.workspaces = Gtk.Stack(hhomogeneous=False, vhomogeneous=False)
        self.workspaces.add_named(outer, 'tools')
        self.toasts.set_child(self.workspaces)
        self.connect('close-request', self.confirm_window_close)

        header, self.design_button, self.send_workspace = workspace_header(
            self.on_edit_template, self.open_send_workspace, active="Send")
        self.stack = Gtk.Stack()
        switcher = Gtk.StackSwitcher(stack=self.stack, margin_start=12, margin_end=12,
                                     css_classes=["linked", "scc-tabs"])
        outer.add_top_bar(header)

        self.banner = Adw.Banner(revealed=False)
        outer.add_top_bar(self.banner)
        self.bundle_banner = Adw.Banner(
            title="The cut paths are locked to the printed sheet.",
            button_label="Open Sheet", revealed=False)
        self.bundle_banner.connect("button-clicked",
                                   lambda *_: self.stack.set_visible_child_name("sheet"))
        outer.add_top_bar(self.bundle_banner)

        split = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL,
                          wide_handle=True, position=675,
                          resize_start_child=True, resize_end_child=False,
                          shrink_start_child=False, shrink_end_child=False)
        self.split = split
        settings = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        settings.set_size_request(440, -1)
        settings.append(switcher)
        settings.append(self.stack)
        split.set_end_child(settings)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        right.set_size_request(320, -1)
        right.set_margin_bottom(12)
        right.set_margin_start(6)
        right.set_margin_end(12)
        self.preview = Preview()
        self.preview.on_spot_clicked = self.on_preview_spot_clicked
        preview_bar = workspace_tools(margin_start=12, margin_end=12, margin_bottom=8)
        self.preview_title = Gtk.Label(label="Sheet preview", xalign=0, hexpand=True, css_classes=["heading"])
        preview_bar.append(self.preview_title)
        self.preview_mode = Gtk.DropDown.new_from_strings(PREVIEW_MODES)
        self.preview_mode.set_selected(PREVIEW_MODES.index("Overlay"))
        self.preview_mode.set_visible(False)
        self.preview_mode.update_property([Gtk.AccessibleProperty.LABEL], ["Preview mode"])
        self.preview_mode.connect("notify::selected", lambda picker, *_: self.preview.set_mode(
            PREVIEW_MODES[picker.get_selected()]))
        self.preview.connect("pdf-changed", lambda preview: self.preview_mode.set_visible(
            not self.tuning and preview.pdf_surface is not None))
        preview_bar.append(self.preview_mode)
        zoom, self.preview_zoom = zoom_controls(self.preview.zoom_by, self.preview.fit)
        self.preview.connect("zoom-changed", lambda _preview, value: self.preview_zoom.set_label(f"{value:.0%}"))
        preview_bar.append(zoom)
        outer.add_top_bar(preview_bar)
        frame = Gtk.Frame()
        frame.set_child(self.preview)
        right.append(frame)

        self.colorblind_toggle = Gtk.CheckButton(label='Colorblind mode')
        self.colorblind_toggle.set_tooltip_text('Use blue and orange. Failed checks also get a label or dashed outline.')
        self.colorblind_toggle.connect('toggled', self.on_colorblind_changed)
        self.colorblind_toggle.set_active(bool(config.load_settings().get('colorblind_mode', False)))

        self.summary_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.summary_card.add_css_class('card')
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        for side in ('top', 'bottom', 'start', 'end'):
            getattr(inner, 'set_margin_' + side)(14)
        status = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.summary_icon = Gtk.Image(icon_name='emblem-default-symbolic')
        self.summary_title = Gtk.Label(label='Checking layout', xalign=0)
        self.summary_title.add_css_class('heading')
        status.append(self.summary_icon)
        status.append(self.summary_title)
        inner.append(status)
        self.summary_layout = Gtk.Label(xalign=0, wrap=True)
        self.summary_material = Gtk.Label(xalign=0, wrap=True)
        self.summary_material.add_css_class('dim-label')
        self.summary_note = Gtk.Label(xalign=0, wrap=True)
        self.summary_note.add_css_class('dim-label')
        inner.append(self.summary_layout)
        inner.append(self.summary_material)
        inner.append(self.summary_note)
        self.summary_card.append(inner)
        right.append(self.summary_card)

        self.preview_options = Gtk.MenuButton(label="Preview Options", halign=Gtk.Align.START)
        popup = Gtk.Popover()
        options = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                          margin_top=8, margin_bottom=8, margin_start=10, margin_end=10)
        self.advanced_toggle = Gtk.CheckButton(label="Job details")
        options.append(self.advanced_toggle)
        options.append(self.colorblind_toggle)
        popup.set_child(options)
        self.preview_options.set_popover(popup)
        right.append(self.preview_options)
        self.checks = Gtk.Label(xalign=0, wrap=True, selectable=True)
        self.checks.add_css_class("monospace")
        self.checks.add_css_class("dim-label")
        scroller = Gtk.ScrolledWindow(vexpand=False)
        scroller.set_size_request(-1, 190)
        scroller.set_child(self.checks)
        self.advanced_panel = scroller
        scroller.set_visible(False)
        self.advanced_toggle.connect('toggled',
            lambda toggle: self.advanced_panel.set_visible(toggle.get_active()))
        right.append(scroller)
        split.set_start_child(right)

        outer.set_content(split)

        self._page_layout()
        self._page_sheet()
        self._page_cut()
        self._load_materials()
        self.on_preset()
        self.add_control_help()
        self.menu_bar = self.workspace_menu_bar()
        header.pack_start(self.menu_bar)
        outer.add_bottom_bar(self._send_bar())
        protect_settings_wheel(outer)

        self._suppress = False
        self.on_changed()
        self.stack.connect("notify::visible-child-name", self.on_page_changed)
        GLib.idle_add(self.refresh_usb)
        GLib.idle_add(self.refresh_wireless)
        wireless_timer = GLib.timeout_add_seconds(30, lambda: (self.refresh_wireless(), True)[1])
        self.connect("destroy", lambda *_: GLib.source_remove(wireless_timer))
        timer = GLib.timeout_add_seconds(2, lambda: (self.refresh_usb(), True)[1])
        self.connect("destroy", lambda *_: GLib.source_remove(timer))

    def on_page_changed(self, stack, _param):
        page = stack.get_visible_child_name()
        if page:
            self.last_send_tab = page
        self.update_send_actions()

    def workspace_menu_bar(self):
        self.workspace_actions = Gio.SimpleActionGroup()
        self.workspaces.insert_action_group("workspace", self.workspace_actions)

        # Shortcuts belong to the Send page, so Design's own keys (Ctrl+O
        # opens a project there) never reach it. Nothing that moves the
        # cutter gets one: sending stays a deliberate click.
        self.send_shortcuts = Gtk.ShortcutController(scope=Gtk.ShortcutScope.MANAGED)
        self.workspaces.get_child_by_name('tools').add_controller(self.send_shortcuts)

        def command(menu, label, name, callback, shortcut=None):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda *_: callback())
            self.workspace_actions.add_action(action)
            menu.append_item(menu_item(label, f"workspace.{name}", shortcut))
            if shortcut:
                self.send_shortcuts.add_shortcut(Gtk.Shortcut.new(
                    Gtk.ShortcutTrigger.parse_string(shortcut), Gtk.NamedAction.new(f"workspace.{name}")))
            return action

        files = Gio.Menu()
        self.open_artwork_action = command(files, "Open Cut Template…", "open-artwork", self.on_open_artwork, "<Control>o")
        self.open_print_action = command(files, "Open Print File…", "open-print", self.on_pick_pdf, "<Control><Shift>o")
        self.close_print_action = command(files, "Close Print File", "close-print", self.close_printed_sheet)
        self.paired_cut_action = command(files, "Save Paired Cut File…", "paired-cut", self.on_save_bundle_file)
        command(files, "Export Cut Templates…", "export-templates", self.on_export)

        edit = Gio.Menu()
        command(edit, "Edit Current Cut Template", "design",
                lambda: self.on_edit_template(replace_design=True), "<Control>F1")
        self.reset_layout_action = command(edit, "Use Generated Layout", "reset-layout", self.on_clear_artwork)

        view = Gio.Menu()
        pages = (("Layout", "layout"), ("Sheet", "sheet"), ("Cut Job", "cut"))
        for number, (label, name) in enumerate(pages, 1):
            command(view, label, name, lambda page=name: self.stack.set_visible_child_name(page), f"<Control>{number}")
        command(view, "Zoom In", "zoom-in", lambda: self.preview.zoom_by(1.25), "<Control>equal|<Control>plus")
        command(view, "Zoom Out", "zoom-out", lambda: self.preview.zoom_by(0.8), "<Control>minus")
        command(view, "Fit Preview", "fit-preview", self.preview.fit, "<Control>0")

        settings = Gio.Menu()
        command(settings, "Cut Settings", "cut-settings", self.open_cut_settings)
        command(settings, "Advanced Send Settings", "advanced-settings", self.open_send_advanced)
        help_menu = Gio.Menu()
        command(help_menu, "Setup Guide", "setup-guide", self.show_setup_guide)
        command(help_menu, "Troubleshoot a Cut…", "troubleshoot-cut", self.troubleshoot_cut)
        menus = Gio.Menu()
        for label, menu in (("_File", files), ("_Edit", edit), ("_View", view),
                            ("_Settings", settings), ("_Help", help_menu)):
            menus.append_submenu(label, menu)
        return Gtk.PopoverMenuBar.new_from_model(menus)

    def open_cut_settings(self):
        """Show the editable cut settings. An open material test stays open:
        only Exit Material Test closes one."""
        self.stack.set_visible_child_name("cut")
        GLib.idle_add(self.scroll_send_to, self.cut_settings)

    def open_send_advanced(self):
        self.stack.set_visible_child_name("cut")
        self.send_advanced.set_expanded(True)
        GLib.idle_add(self.scroll_send_to, self.send_advanced)

    def _send_bar(self):
        bar = Gtk.ActionBar()
        # A refusal has to be readable: wrap it instead of cutting it off at 68
        # characters, and let it shrink so the actions keep their places.
        self.send_status = Gtk.Label(xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
                                     max_width_chars=48, hexpand=True)
        bar.pack_start(self.send_status)
        self.review_send = Gtk.Button(label="Review Cut Job")
        self.review_send.add_css_class("suggested-action")
        self.review_send.connect("clicked", self.on_review_send)
        bar.pack_end(self.review_send)
        self.cut_button = Gtk.Button(label="Send to Cutter")
        self.cut_button.add_css_class("suggested-action")
        self.cut_button.connect("clicked", self.on_cut)
        bar.pack_end(self.cut_button)
        self.cancel_cut = Gtk.Button(label="Cancel Cut")
        self.cancel_cut.add_css_class("destructive-action")
        self.cancel_cut.connect("clicked", lambda *_: self.cut_control.cancel())
        bar.pack_end(self.cancel_cut)
        self.pause_cut = Gtk.Button(label="Pause Cut")
        self.pause_cut.add_css_class("flat")
        self.pause_cut.connect("clicked", lambda *_: self.cut_control.toggle_pause())
        bar.pack_end(self.pause_cut)
        self.guide_send = Gtk.Button(label="Cut One Pass…", css_classes=["suggested-action"])
        self.guide_send.connect("clicked", self.on_guide_pass)
        bar.pack_end(self.guide_send)
        self.back_to_send = Gtk.Button(label="Exit Material Test")
        self.back_to_send.connect("clicked", self.close_material_guide)
        bar.pack_end(self.back_to_send)
        return bar

    def send_review_destination(self):
        if design_review_pending(self):
            return "design", "Review Design"
        if self.pdf_bundle:
            if self.imported is None:
                return "sheet", "Review Sheet"
            if not self.bundle_checked:
                return "sheet", "Review Sheet"
        elif self.pdf_path is not None and self.job and self.job.registration:
            if self.pdf_check_state != sheet_mod.RegistrationDecision.PASSED:
                return "sheet", "Review Sheet"
        return "cut", "Review Cut Job"

    def on_review_send(self, *_args):
        if self.tuning:
            self.close_material_guide()
        destination, _ = self.send_review_destination()
        if destination == "design":
            self.on_edit_template()
        else:
            self.stack.set_visible_child_name(destination)
            if destination == "sheet":
                action = self.measure_button
                if self.bundle_row_error:
                    action = self.r_sheet_page
                elif self.pdf_bundle and self.imported is None:
                    action = self.save_paired_cut
                if action.get_sensitive():
                    action.grab_focus()
            elif self.cut_button.get_sensitive():
                self.cut_button.grab_focus()

    def open_send_workspace(self, *_args):
        """The Send workspace toggle. An open material test stays open:
        only Exit Material Test closes one."""
        self.stack.set_visible_child_name("cut")

    def update_send_actions(self):
        if not hasattr(self, "send_status"):
            return
        if hasattr(self, "bundle_banner"):
            self.bundle_banner.set_revealed(self.pdf_bundle is not None and not self.tuning)
        page = self.stack.get_visible_child_name()
        destination, label = self.send_review_destination()
        self.review_send.set_label(label)
        editable = not (self._job_locked or self._sending)
        device = graphtec.device_by_name(self._text(self.r_cutter))
        for button in self.send_holder_buttons.values():
            button.set_sensitive(editable and device.two_tools)
        for control in (self.open_artwork_button, self.open_print_button):
            control.set_sensitive(editable)
        self.open_artwork_action.set_enabled(editable)
        self.open_print_action.set_enabled(editable)
        self.close_print_action.set_enabled(editable and self.pdf_path is not None)
        self.update_sheet_controls()
        self.paired_cut_action.set_enabled(self.pdf_bundle is not None)
        can_reset_layout = self.imported is not None or self.pdf_bundle is not None
        self.reset_layout_action.set_enabled(editable and can_reset_layout)
        self.use_generated.set_sensitive(editable and can_reset_layout)
        self.use_generated.set_tooltip_text(
            "Discard the custom cut template and restore the generated card layout."
            if can_reset_layout
            else "Already using the generated layout."
        )
        self.review_send.set_visible(not self.tuning and page != destination)
        at_send = not self.tuning and destination == page == "cut" and self.cut_control is None
        self.cut_button.set_visible(at_send)
        show_scan_test = not self.tuning and bool(self.job and self.job.registration_steps())
        self.registration_test.set_visible(show_scan_test)
        self.scan_button.set_visible(show_scan_test)
        self.corner_button.set_visible(not self.tuning)
        control = self.cut_control
        for button in (self.pause_cut, self.cancel_cut):
            button.set_visible(control is not None)
        if control is not None:
            state = control.state
            if state == "pausing":
                pause_label = "Pausing…"
                pause_sensitive = True
            elif state == "paused":
                pause_label = "Resume Cut"
                pause_sensitive = True
            else:
                pause_label = "Pause Cut"
                pause_sensitive = state == "running"

            cancel_label = "Stopping…" if state == "cancelling" else "Cancel Cut"
            cancel_sensitive = state in ("running", "pausing", "paused")

            self.pause_cut.set_label(pause_label)
            self.pause_cut.set_sensitive(pause_sensitive)
            self.cancel_cut.set_label(cancel_label)
            self.cancel_cut.set_sensitive(cancel_sensitive)
        # A material test cuts one pass at a time with Cut Settings A or B:
        # the job's pass count, staging and its own force, speed and depth step aside.
        for field in ("force", "speed", "passes", "depth"):
            self.send_rows[field].set_visible(not self.tuning)
        self.r_stage.set_visible(not self.tuning)
        if self.tuning:
            self.send_cutter_group.set_visible(False)
            self.send_groups.set_visible(False)
            self.registration_group.set_visible(False)
            self.cut_settings.set_visible(True)
            self.preset_actions.set_visible(False)
            self.send_advanced.set_visible(False)
            self.guide_view.set_visible(True)
        else:
            self.send_cutter_group.set_visible(True)
            self.send_groups.set_visible(self._active_send_mode() != "simple")
            self.registration_group.set_visible(True)
            self.cut_settings.set_visible(True)
            self.preset_actions.set_visible(True)
            self.send_advanced.set_visible(True)
            self.guide_view.set_visible(False)
        self.back_to_send.set_visible(self.tuning)
        self.guide_send.set_visible(self.tuning and page == "cut")
        self.material_guide.set_label("Resume Material Test" if self.guide.has_tests else "Dial In a Material…")
        self.material_guide.set_visible(not self.tuning)
        job = self.job
        mode, identifier, *_ = self._connection()
        can_check = bool(identifier) and not (self._sending or self._checking_status)
        self.check_cutter_status.set_sensitive(can_check)
        self.r_bluetooth.set_sensitive(editable and not self._checking_status)
        self.r_tool_selection.set_sensitive(editable and can_check)
        pdf_refusal = ""
        if job and job.registration_steps() and self.pdf_path is not None:
            allowed, pdf_reason = sheet_mod.RegistrationDecision.evaluate(
                self.pdf_check_state, has_pdf=True, registration_on=True)
            if not allowed:
                pdf_refusal = pdf_reason
        reason = (
            control.status_text if control is not None else
            "Sending to the cutter…" if self._sending else
            "Checking cutter status…" if self._checking_status else
            "The design has edits Send has not used. Open Design, then switch to Send." if destination == "design" else
            "Choose another sheet or reopen the Print & Cut file in Sheet." if self.bundle_row_error else
            "Save the legacy cut file from Sheet." if self.pdf_bundle and self.imported is None else
            pdf_refusal if pdf_refusal else
            "Check the matching PDF in Sheet." if destination == "sheet" else
            "Check this layout before sending." if job is None or job.blocked else
            "Select a Bluetooth cutter." if mode in bluetooth.BLUETOOTH_MODES and not identifier else
            "Check the cutter connection first." if not self.wireless_ready() else
            ""
        )
        if not reason:
            reason = cutters.usb_refusal(self._connection(), job.device, self.selected_cutter())
        self.scan_button.set_sensitive(not reason and bool(job.registration_steps()))
        if not reason and job.settings.passes <= 0:
            reason = "Set at least one pass to send cut paths."
        self.cut_button.set_sensitive(not reason)
        self.corner_button.set_sensitive(not reason)
        if not self.tuning:
            display_job = self.active_job or job
            is_send_page = self.stack.get_visible_child_name() == "cut"
            if reason == "Connect a cutter." and not is_send_page:
                status = "No cutter connected."
                self.send_status.set_label(status)
                self.send_status.set_tooltip_text(status)
                self.send_status.remove_css_class("warning")
            else:
                status = (f"⚠ {reason}" if reason else "") or (
                    f"{len(display_job.paths)} paths · {display_job.device.name} · {'Registration on' if display_job.registration else 'Registration off'}")
                self.send_status.set_label(status)
                self.send_status.set_tooltip_text(status)
                warned = bool(reason)
                if warned != self.send_status.has_css_class("warning"):
                    getattr(self.send_status, "add_css_class" if warned else "remove_css_class")("warning")
        self.update_guide()
        self.update_tuning_preview()

    def show_setup_guide(self, *_args):
        dialog = Adw.AlertDialog(heading="Set Up Your First Cut", body=SETUP_STEPS)
        dialog.add_response("close", "Close")
        dialog.add_response("tune", "Dial In a Material…")
        dialog.set_response_appearance("tune", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_close_response("close")
        dialog.connect("response::tune", self.open_material_guide)
        dialog.present(self)

    def troubleshoot_cut(self, symptom=None, *_args):
        problems = tuple(CUT_PROBLEMS)
        dialog = Adw.AlertDialog(heading="Troubleshoot a Cut")
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        choices = Gtk.DropDown.new_from_strings(problems)
        choices.update_property([Gtk.AccessibleProperty.LABEL], ["What happened?"])
        if isinstance(symptom, str) and symptom in CUT_PROBLEMS:
            choices.set_selected(problems.index(symptom))
        content.append(choices)
        body = Gtk.Label(wrap=True, xalign=0, max_width_chars=56)
        content.append(body)
        dialog.set_extra_child(content)
        dialog.add_response("close", "Close")
        dialog.add_response("follow", OPEN_SHEET)
        dialog.set_close_response("close")
        self.troubleshoot_dialog = dialog

        def selected():
            return CUT_PROBLEMS[problems[choices.get_selected()]]

        def update(*_):
            text, action = selected()
            body.set_label(text)
            dialog.set_response_label("follow", action or OPEN_SHEET)
            dialog.set_response_enabled("follow", action is not None)

        choices.connect("notify::selected", update)
        dialog.connect("response::follow", lambda *_: self.follow_troubleshooting(selected()[1]))
        update()
        dialog.present(self)
        return dialog

    def follow_troubleshooting(self, action):
        if action == OPEN_SHEET:
            self.close_material_guide()
            self.stack.set_visible_child_name("sheet")
        elif action == DIAL_IN:
            self.open_material_guide()
        elif action == CUT_SETTINGS:
            self.open_cut_settings()

    def open_material_guide(self, *_args):
        if self.guide.placement is None:
            try:
                self.guide.placement = self.guide.make_placement(self.job, self.options())
            except (ValueError, KeyError):
                pass
        self.material_guide.set_label("Resume Material Test")
        self.stack.set_visible_child_name("cut")
        self.tuning = True
        self.start_guide_slots()
        self.send_page.get_vadjustment().set_value(0)
        self.update_send_actions()

    def scroll_send_to(self, target):
        if target.get_mapped():
            placed, bounds = target.compute_bounds(self.send_page.controls)
            if placed:
                self.send_page.get_vadjustment().set_value(max(0, bounds.get_y() - 12))
        return GLib.SOURCE_REMOVE

    def close_material_guide(self, *_args):
        self.tuning = False
        self.send_page.get_vadjustment().set_value(0)
        self.update_send_actions()

    GUIDE_SLOT_KEYS = ("force", "speed", "depth")

    def add_guide_slots(self, group):
        """Cut Settings A and B: two settings to compare, each opening on its
        own. "Cut with" picks which one cuts, and each pass records it."""
        self.guide_headers, self.guide_fields = {}, {}
        for name in ("A", "B"):
            header = Adw.ExpanderRow(title=f"Cut Settings {name}")
            self.guide_fields[name] = {}
            for field, title, low, high in (("force", "Force", 1, 40), ("speed", "Speed", 1, 30),
                                            ("depth", "Blade Depth", -1, 10)):
                row, precision, _ = self._slider_row(field, title, low, high, low if low > 0 else -1,
                                                     lambda *_a, name=name: self.change_guide_slot(name))
                header.add_row(row)
                self.guide_fields[name][field] = precision
            group.add(header)
            self.guide_headers[name] = header

    @property
    def guide_slot(self):
        return "AB"[self.guide_slot_choice.get_selected()]

    @property
    def guide_pass_name(self):
        return self.guide_slot

    def guide_slot_values(self, name):
        return {key: (None if field.get_value() < 0 else int(field.get_value()))
                for key, field in self.guide_fields[name].items()}

    def _show_guide_slot_values(self, name, values):
        previous, self._guide_slot_loading = getattr(self, "_guide_slot_loading", False), True
        try:
            for key, field in self.guide_fields[name].items():
                field.set_value(-1 if values[key] is None else values[key])
        finally:
            self._guide_slot_loading = previous

    def _blade_slot_values(self):
        return {key: (None if field.get_value() < 0 else int(field.get_value()))
                for key, field in (("force", self.r_force), ("speed", self.r_speed), ("depth", self.r_depth))}

    def start_guide_slots(self):
        """A new test starts A from Cut Settings and B from light pass settings, with A open."""
        main = self._blade_slot_values()
        light = dict(main)
        if self._light_passes() > 0:
            for key, stage_key in (("force", "light_force"), ("speed", "light_speed")):
                value = int(self.stage_fields[stage_key].get_value())
                if value >= 0:
                    light[key] = value
        self._show_guide_slot_values("A", main)
        self._show_guide_slot_values("B", light)
        self.guide_headers["A"].set_expanded(True)
        self.guide_slot_choice.set_selected(0)

    def use_guide_slot(self):
        self.set_guide_blade_values(self.guide_slot_values(self.guide_slot))

    def change_guide_slot(self, name):
        if not getattr(self, "_guide_slot_loading", False) and name == self.guide_slot:
            self.use_guide_slot()

    def sync_guide_slot(self):
        """Settings changed from elsewhere land in the chosen setting."""
        values = self._blade_slot_values()
        if values != self.guide_slot_values(self.guide_slot):
            self._show_guide_slot_values(self.guide_slot, values)

    def update_tuning_preview(self):
        trial = None
        if self.tuning:
            trial = self.guide.trial
            if trial is None:
                try:
                    trial = self.guide_proposal()
                except (ValueError, KeyError):
                    pass
        placement = self.guide.placement if self.tuning else None
        active_rect = self.guide.spot_rect(self.guide.spot) if self.tuning else None
        self.preview.set_trial(trial, self.guide.spots if self.tuning else (), placement, active_rect,
                               grid=self.guide.fresh_spots() if self.tuning else {})
        self.preview_title.set_label("Material test sheet" if self.preview.test_placement else "Sheet preview")
        self.preview_mode.set_visible(not self.tuning and self.preview.pdf_surface is not None)
        self.summary_card.set_visible(not self.tuning)
        self.preview_options.set_visible(not self.tuning)
        self.advanced_panel.set_visible(not self.tuning and self.advanced_toggle.get_active())

    # ---- pages -------------------------------------------------------
    def _page_layout(self):
        page = ControlPage()

        group = Adw.PreferencesGroup(
            title='Printed-sheet preset',
            description='Choose the layout you printed.')
        self.r_layout_preset = _combo(
            'Layout preset', ['Custom'] + [p.name for p in layout_presets.desktop_presets().values()])
        self.default_layout_index = tuple(layout_presets.desktop_presets()).index(layout_presets.DEFAULT_KEY) + 1
        self.r_layout_preset.set_selected(self.default_layout_index)
        self.r_layout_preset.connect('notify::selected', self.on_layout_preset)
        group.add(self.r_layout_preset)
        page.add(group)

        gsrc = Adw.PreferencesGroup(
            title="Cut template",
            description="Use a card layout or open your cut file.")
        self.import_row = Adw.ActionRow(
            title="Generated layout",
            subtitle="Using the sheet and card settings below")
        self.import_row.set_subtitle_lines(0)
        self.open_artwork_button = open_btn = Gtk.Button(label="Open Cut Template…")
        open_btn.set_tooltip_text("Open an SVG, DXF, or Studio3 file.")
        open_btn.connect("clicked", self.on_open_artwork)
        self.draw_in_design_btn = draw_btn = Gtk.Button(label="Draw in Design")
        draw_btn.set_tooltip_text("Open the Design workspace to draw custom cut paths.")
        draw_btn.connect("clicked", lambda *_: self.on_edit_template(blank=True))
        self.use_generated = Gtk.Button(label="Use Generated Layout", sensitive=False)
        self.use_generated.connect("clicked", self.on_clear_artwork)
        actions = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                          margin_top=6, margin_bottom=6, margin_start=12, margin_end=12)
        actions.append(open_btn)
        actions.append(draw_btn)
        actions.append(self.use_generated)
        gsrc.add(self.import_row)
        gsrc.add(Adw.PreferencesRow(child=actions, activatable=False, selectable=False))
        self.r_colour = _combo("Line color", ["all colours"], subtitle="Cut only lines of this color.")
        self.r_colour.connect("notify::selected", self.on_changed)
        page.add(gsrc)

        g = Adw.PreferencesGroup(title="Sheet and cards")
        self.r_engine = _combo(
            "Layout engine",
            ["Silhouette Card Maker", "Centered grid"],
            subtitle="Match how your cards were laid out on the page.")
        self.r_sheet = _combo("Sheet", sorted(geometry.SHEETS),
                              sorted(geometry.SHEETS).index("Letter"))
        self.r_card = _combo("Card size", sorted(geometry.CARDS),
                             sorted(geometry.CARDS).index("standard"))
        self.r_orient = _combo("Orientation",
                               ["Best fit", "Portrait", "Landscape"])
        g.add(self.r_sheet)
        g.add(self.r_card)
        g.add(self.r_orient)
        page.add(g)

        advanced, self.layout_advanced = disclosure(page.controls)
        g2 = Adw.PreferencesGroup(title="Cut layout")
        g2.add(self.r_engine)
        g2.add(self.r_colour)
        self.r_mode = _combo(
            "Cut mode", ["rounded", "square", "cross"],
            subtitle="cross cuts straight across for a corner punch")
        self.r_radius = _spin("Corner radius (mm)", 0, 10, 3.2, 0.1, 1)
        g2.add(self.r_mode)
        g2.add(self.r_radius)
        advanced.append(g2)

        g3 = Adw.PreferencesGroup(
            title="Registration marks",
            description="Match the marks on your print.")
        self.r_pattern = _combo(
            "Pattern", list(regmarks.PATTERNS),
            subtitle="Four marks are for Cameo 5 Alpha.")
        self.r_inset = _spin("Inset (mm)", 2, 40, 10.0, 0.5, 1)
        self.r_length = _spin("Arm length (mm)", 0, 20, 0.0, 0.5, 1)
        _special_value(self.r_length, 0, "Auto")
        self.r_thickness = _spin("Line thickness (mm)", 0.5, 1.0, 1.0, 0.1, 1)
        self.r_bleed = _spin("Bleed (mm)", 0, 5, 0.0, 0.25, 2)
        self.r_skip = Adw.EntryRow(title="Skip slots")
        self.r_skip.set_tooltip_text("Enter card numbers to skip, such as 0, 3. The first card is 0.")
        for r in (self.r_pattern, self.r_inset, self.r_length,
                  self.r_thickness, self.r_bleed, self.r_skip):
            g3.add(r)
        advanced.append(g3)

        g4 = Adw.PreferencesGroup(title="Templates")
        row = Adw.ActionRow(
            title="Write SVG, DXF and a proof",
            subtitle=str(config.TEMPLATE_DIR))
        btn = Gtk.Button(label="Export")
        btn.set_valign(Gtk.Align.CENTER)
        btn.connect("clicked", self.on_export)
        row.add_suffix(btn)
        row.set_activatable_widget(btn)
        g4.add(row)
        advanced.append(g4)

        for r in (self.r_engine, self.r_sheet, self.r_card, self.r_orient,
                  self.r_mode, self.r_pattern):
            r.connect("notify::selected", self.on_changed)
        for r in (self.r_radius, self.r_inset, self.r_length,
                  self.r_thickness, self.r_bleed):
            r.connect("notify::value", self.on_changed)
        self.r_skip.connect("changed", self.on_changed)

        self.stack.add_titled(page, "layout", "Layout")

    def _page_sheet(self):
        page = ControlPage()
        g = Adw.PreferencesGroup(title="Printed sheet", description="Check the PDF’s registration marks against your layout.")
        self.sheet_row = Adw.ActionRow(title="No print file open",
                                       subtitle="Open the PDF you printed.")
        self.sheet_row.set_subtitle_lines(0)
        self.open_print_button = pick = Gtk.Button(label="Open Print File…")
        pick.set_valign(Gtk.Align.CENTER)
        pick.connect("clicked", self.on_pick_pdf)
        self.close_print = Gtk.Button(label="Close File", valign=Gtk.Align.CENTER)
        self.close_print.connect("clicked", self.close_printed_sheet)
        g.add(self.sheet_row)
        file_actions = Gtk.Box(spacing=6, margin_top=6, margin_bottom=6, margin_start=12, margin_end=12)
        file_actions.append(pick)
        file_actions.append(self.close_print)
        g.add(Adw.PreferencesRow(child=file_actions, activatable=False, selectable=False))
        save_actions = Gtk.Box(spacing=6, margin_top=6, margin_bottom=6, margin_start=12, margin_end=12)
        save_print = Gtk.Button(label="Save Print PDF…")
        save_print.connect("clicked", self.on_save_bundle_print)
        self.save_paired_cut = Gtk.Button(label="Save Paired Cut File…")
        self.save_paired_cut.connect("clicked", self.on_save_bundle_file)
        save_actions.append(save_print)
        save_actions.append(self.save_paired_cut)
        self.bundle_export = Adw.PreferencesRow(child=save_actions, activatable=False, selectable=False)
        self.bundle_export.set_visible(False)
        g.add(self.bundle_export)

        self.r_sheet_page = _spin("PDF page", 1, 99, 1)
        self.r_sheet_dpi = _combo("Preview resolution (dpi)", ["150", "200", "300"], 1)
        g.add(self.r_sheet_page)
        self.sheet_page_changed_id = self.r_sheet_page.connect('notify::value', self.on_sheet_number_changed)
        self.r_sheet_dpi.connect('notify::selected', self.on_pdf_page_changed)

        remeasure = Adw.ActionRow(
            title="Registration marks",
            subtitle="Check again after changing the layout or marks.")
        self.measure_button = Gtk.Button(label="Check PDF Marks", valign=Gtk.Align.CENTER)
        self.measure_button.connect("clicked", lambda *_: self.measure_sheet())
        remeasure.add_suffix(self.measure_button)
        g.add(remeasure)
        page.add(g)

        g2 = Adw.PreferencesGroup(title="PDF registration check")
        self.sheet_result = Adw.ActionRow(title="Not measured", subtitle="")
        self.sheet_result.set_subtitle_lines(0)
        g2.add(self.sheet_result)
        self.scan_result = Adw.ActionRow(title="Last cutter scan")
        self.scan_result.set_subtitle_lines(0)
        self.scan_result.set_visible(False)
        g2.add(self.scan_result)

        page.add(g2)
        advanced, self.sheet_advanced = disclosure(page.controls)
        pdf_options = Adw.PreferencesGroup(title="PDF check")
        pdf_options.add(self.r_sheet_dpi)
        advanced.append(pdf_options)

        self.update_sheet_controls()
        self.stack.add_titled(page, "sheet", "Sheet")

    def add_advanced_cut_settings(self, advanced):
        blade = Adw.PreferencesGroup(title="Blade details")
        self.r_diameter = _spin("Tool Offset (mm)", 0, 2.3, 0.9, 0.05, 2)
        self.r_lift = _switch("Lift head", True)
        self.r_corner_start = _spin("Start Extension (mm)", 0, 0.9, 0.1, 0.1, 1)
        self.r_corner_end = _spin("End Extension (mm)", 0, 0.9, 0.1, 0.1, 1)
        for row in (self.r_diameter, self.r_lift, self.r_corner_start, self.r_corner_end):
            blade.add(row)
        advanced.append(blade)

        paths = Adw.PreferencesGroup(title="Cut paths")
        self.r_path_order = _combo("Cut Order Sorting", list(toolpath.PATH_ORDERS.values()))
        self.r_interiors = _switch("Sort Interior Contours First", False)
        self.r_delicate = _switch("Delicate Media Path", False)
        self.r_pass_order = _combo("Pass order", list(toolpath.PASS_ORDERS.values()))
        self.r_alternate = _switch("Alternate pass direction", False)
        self.r_overcut = _spin("Seam Overcut (mm)", 0, 1000, 0, 0.1, 2,
                               subtitle="Overlap the seam of each closed contour")
        for row in (self.r_path_order, self.r_interiors, self.r_delicate,
                    self.r_pass_order, self.r_alternate, self.r_overcut):
            paths.add(row)
        advanced.append(paths)

        self.weed_group = Adw.PreferencesGroup(
            title="Weed Settings", description="Available for imported artwork and Design projects")
        self.r_weed_border = _spin("Weed Border Padding (mm)", 0, 1000, 0, 0.5, 2,
                                   subtitle="Positive padding adds a cut border")
        self.r_weed_lines = _combo("Weed Lines", list(toolpath.WEED_LINES.values()))
        self.weed_group.add(self.r_weed_border)
        self.weed_group.add(self.r_weed_lines)
        advanced.append(self.weed_group)

        for row in (self.r_path_order, self.r_pass_order, self.r_weed_lines):
            row.connect("notify::selected", self.on_changed)
        for r in (self.r_lift, self.r_alternate, self.r_interiors, self.r_delicate):
            r.connect("notify::active", self.on_changed)
        for r in (self.r_diameter, self.r_corner_start, self.r_corner_end, self.r_overcut, self.r_weed_border):
            r.connect("notify::value", self.on_changed)

    def _page_cut(self):
        self.send_page = page = ControlPage()
        self.send_normal = normal = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        page.add(normal)
        cutter_names = [d.name for d in graphtec.DEVICES]
        self.r_cutter = _combo("Resolved cutter", cutter_names, cutter_names.index("Silhouette Cameo 4"))
        self.r_cutter.connect("notify::selected", self.on_material_cutter_changed)
        self.r_cutter.connect("notify::selected", self.on_changed)
        self.send_modes = Adw.PreferencesGroup(title="Send by")
        self.r_send_mode = _combo("Mode", ["Standard", "Line", "Fill", "Layer"])
        self.r_send_mode.set_tooltip_text("Use one set of cut settings, or assign settings by color or layer.")
        self.r_send_mode.connect("notify::selected", self.on_changed)
        self.send_modes.add(self.r_send_mode)
        self.send_groups = Adw.PreferencesGroup(
            title="Cut groups", description="Choose which groups to cut and in what order.")

        self.registration_group = g = Adw.PreferencesGroup(title="Registration")
        self.r_register = _switch("Scan for printed marks", True)
        self.r_auto = _switch("Search automatically", True)
        g.add(self.r_register)
        self.scan_button = Gtk.Button(label="Test Registration")
        self.scan_button.set_valign(Gtk.Align.CENTER)
        self.scan_button.set_tooltip_text("Scan the marks without cutting.")
        self.scan_button.connect("clicked", lambda *_: self.on_cut(scan_only=True))
        self.corner_button = Gtk.Button(label="Corner Test Cut…", valign=Gtk.Align.CENTER)
        self.corner_button.set_tooltip_text("Cut one 3 mm square in a clear corner.")
        self.corner_button.connect("clicked", lambda *_: self.on_cut(corner=True))
        registration_test = Adw.ActionRow(title="Registration scan")
        registration_test.add_suffix(self.scan_button)
        g.add(registration_test)
        self.registration_test = registration_test
        corner_test_row = Adw.ActionRow(title="Small test cut")
        corner_test_row.add_suffix(self.corner_button)
        g.add(corner_test_row)
        self.r_register.connect("notify::active", self.on_changed)
        self.r_auto.connect("notify::active", self.on_changed)

        self.send_cutter_group = g2 = Adw.PreferencesGroup(title="Cutter")
        cutter_actions = Gtk.Box(spacing=6)
        self.refresh_cutters_button = scan = Gtk.Button(label="Refresh Cutters")
        scan.connect("clicked", self._scan_bluetooth)
        cutter_actions.append(scan)
        self.check_cutter_status = Gtk.Button(label="Check Status", valign=Gtk.Align.CENTER)
        self.check_cutter_status.connect("clicked", self.on_refresh_status)
        cutter_actions.append(self.check_cutter_status)
        g2.set_header_suffix(cutter_actions)
        self.r_send_cutter = _combo("Cutter model", list(cutters.MODELS.values()))
        self.r_send_cutter.connect("notify::selected", self.on_send_cutter)
        g2.add(self.r_send_cutter)
        normal.prepend(g2)
        self.r_connection = _combo("Connection method", ["Automatic", *bluetooth.BLUETOOTH_MODES.values()])
        self.r_connection.set_tooltip_text(
            "Automatic looks for USB and Bluetooth cutters. Choose a Bluetooth method to connect manually.")
        self.r_bluetooth = _combo("Selected cutter", ["Select a cutter"])
        self.r_bluetooth_id = Adw.EntryRow(title="Bluetooth identifier")
        self.r_bluetooth_channel = _spin("Channel", 1, 30, 1, 1, 0)
        self.r_bluetooth_channel.set_tooltip_text("Bluetooth Classic usually uses channel 1.")
        self._bluetooth_devices = []
        self._usb_devices = {}
        self._wireless_choices = {}
        self._wireless_scanning = False
        self._search_pending = False
        self.preferred_endpoint = None
        self.verified_wireless = None
        self.endpoint_changed_id = self.r_bluetooth.connect("notify::selected", self.select_detected)
        g2.add(self.r_bluetooth)
        self.connection_status = Adw.ActionRow(title="Cutter status")
        self.connection_status.set_subtitle_lines(0)
        # Offered only while no cutter is available, as in Silhouette Studio.
        self._manual_scanning = False
        self.scan_cutters_button = Gtk.Button(label=cutters.SCAN_LABEL, valign=Gtk.Align.CENTER,
                                              css_classes=["suggested-action"], visible=False)
        self.scan_cutters_button.connect("clicked", self.on_scan_cutters)
        self.connection_status.add_suffix(self.scan_cutters_button)
        g2.add(self.connection_status)
        self.r_connection.connect("notify::selected", self.on_connection_changed)
        self.on_connection_changed()
        self.r_bluetooth_id.connect("changed", self.on_connection_target_changed)
        self.r_bluetooth_channel.connect("notify::value", self.on_connection_target_changed)
        normal.append(self.send_groups)
        self.cut_settings = self.cut_settings_group()
        normal.append(self.cut_settings)
        normal.append(g)
        advanced, self.send_advanced = disclosure(normal)
        bluetooth_options = Adw.PreferencesGroup(title="Connection")
        bluetooth_options.add(self.r_connection)
        bluetooth_options.add(self.r_bluetooth_id)
        bluetooth_options.add(self.r_bluetooth_channel)
        advanced.append(bluetooth_options)
        advanced.append(self.send_modes)

        media = Adw.PreferencesGroup(title="Media handling")
        self.r_mat = _combo("Cutting mat", list(graphtec.MAT_OPTIONS.values()))
        self.r_end_position = _combo("Feed Options", list(graphtec.END_POSITIONS.values()))
        self.r_feed = _spin("Feed Distance (mm)", 0, max(d.length_mm for d in graphtec.DEVICES),
                            0, 0.5, 2, subtitle="Distance beyond the farthest cut")
        for row in (self.r_mat, self.r_end_position):
            media.add(row)
            row.connect("notify::selected", self.on_changed)
        media.add(self.r_feed)
        self.r_feed.connect("notify::value", self.on_changed)
        modes = ["Auto", "Always", "Never"]
        preferred = config.load_settings().get("show_weed_settings")
        self.r_show_weed = _combo("Show Weed Settings", modes,
                                  modes.index(preferred) if preferred in modes else 0)
        self.r_show_weed.connect("notify::selected", self.on_show_weed_settings)
        media.add(self.r_show_weed)
        media.add(self.r_auto)
        advanced.append(media)
        self.add_advanced_cut_settings(advanced)
        advanced.append(self.material_library_group())

        self.guide_view = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, visible=False)
        page.add(self.guide_view)
        self.guide_group = guide = Adw.PreferencesGroup(title="Dial In a Material", description=starting_guide.GUIDE_TEXT)
        self.guide_shape = _combo("Test shape", list(testcut.TEST_SHAPES.values()))
        self.guide_size = _combo("Sample size", list(starting_guide.SAMPLE_SIZES.values()))
        for field in (self.guide_shape, self.guide_size):
            field.set_tooltip_text("Changing the sample after a cut moves to a fresh spot.")
            guide.add(field)
        self.guide_shape_id = self.guide_shape.connect("notify::selected", self.on_guide_sample)
        self.guide_size_id = self.guide_size.connect("notify::selected", self.on_guide_sample)
        self.guide_spot_selector = _spin("Test spot", 1, 999, 1, 1, 0)
        self.guide_spot_selector.set_tooltip_text("Or click a spot in the preview.")
        self.guide_spot_selector.connect("notify::value", self.on_guide_spot_selected)
        guide.add(self.guide_spot_selector)
        self.add_guide_slots(guide)
        self.guide_status = Adw.ActionRow(title="Spot 1")
        self.guide_status.set_subtitle_lines(0)
        guide.add(self.guide_status)
        self.guide_slot_choice = _combo("Cut with", ["Cut Settings A", "Cut Settings B"])
        self.guide_slot_choice.set_tooltip_text("Which cut settings the next pass uses.")
        self.guide_slot_choice.connect("notify::selected", lambda *_: self.use_guide_slot())
        guide.add(self.guide_slot_choice)
        self.guide_next = self.guide_button(guide, "Next Spot", self.on_guide_next)
        self.guide_use, self.guide_apply = self.guide_buttons(
            guide, ("Use Tested Settings…", self.on_guide_use), ("Use Starting Settings", self.on_guide_start))
        self.guide_view.append(guide)
        extras, self.guide_advanced = disclosure(self.guide_view, "Test Options")
        tips = Adw.PreferencesGroup(title="Test sheet")
        self.guide_registration = Adw.SwitchRow(title="Use Registration Marks", active=self.guide.registered)
        self.guide_registration.connect("notify::active", self.change_guide_registration)
        tips.add(self.guide_registration)
        self.guide_marked_sheet = self.guide_button(tips, "Save Marked Test Sheet…", self.save_guide_marked_sheet)
        self.guide_reset = self.guide_button(tips, "New Test Sheet", self.on_guide_reset)
        self.guide_history = Adw.ActionRow(title="Pass counts by spot")
        self.guide_history.set_subtitle_lines(0)
        tips.add(self.guide_history)
        extras.append(tips)
        help_group = Adw.PreferencesGroup(title="Troubleshooting")
        self.guide_outcome = _combo("Optional symptom", list(starting_guide.OUTCOMES.values()))
        self.guide_outcome.connect("notify::selected", self.on_guide_outcome)
        help_group.add(self.guide_outcome)
        self.guide_advice = Adw.ActionRow(title="What to try next")
        self.guide_advice.set_subtitle_lines(0)
        help_group.add(self.guide_advice)
        detail = Adw.ActionRow(title="One change at a time", subtitle=starting_guide.TUNING_TIPS)
        detail.set_subtitle_lines(0)
        help_group.add(detail)
        self.guide_button(help_group, "Troubleshoot a Cut…", self.troubleshoot_cut)
        extras.append(help_group)

        gcal = Adw.PreferencesGroup(
            title="Calibration", description="Use a pen test to measure how far to shift the cut.")
        dx, dy = config.cut_offset()
        self.r_cal_x = _spin("Offset X", -5, 5, dx, 0.05, 2,
                             subtitle="mm, positive is right")
        self.r_cal_y = _spin("Offset Y", -5, 5, dy, 0.05, 2,
                             subtitle="mm, positive is down")
        gcal.add(self.r_cal_x)
        gcal.add(self.r_cal_y)
        save_cal = Adw.ActionRow(title="Save calibration",
                                 subtitle="Applied to every job afterwards")
        cal_btn = Gtk.Button(label="Save")
        cal_btn.set_valign(Gtk.Align.CENTER)
        cal_btn.connect("clicked", self.on_save_calibration)
        save_cal.add_suffix(cal_btn)
        gcal.add(save_cal)
        advanced.append(gcal)
        for r in (self.r_cal_x, self.r_cal_y):
            r.connect("notify::value", self.on_changed)

        gtest = Adw.PreferencesGroup(
            title="Test-Cut Matrix",
            description="Try several settings on one blank sheet.")
        self.r_test_shape = _combo("Test shape", list(testcut.TEST_SHAPES.values()))
        gtest.add(self.r_test_shape)
        self.r_test_force = Adw.EntryRow(title="Force values")
        self.r_test_force.set_text("25,29,33")
        self.r_test_passes = Adw.EntryRow(title="Pass counts")
        self.r_test_passes.set_text("1,2,3,4")
        gtest.add(self.r_test_force)
        gtest.add(self.r_test_passes)
        test_row = Adw.ActionRow(
            title="Cut a test sheet",
            subtitle="Registration off; put a blank offcut on the mat")
        test_btn = Gtk.Button(label="Test cut")
        test_btn.add_css_class("destructive-action")
        test_btn.set_valign(Gtk.Align.CENTER)
        test_btn.connect("clicked", self.on_test_cut)
        test_row.add_suffix(test_btn)
        gtest.add(test_row)
        advanced.append(gtest)

        g3 = Adw.PreferencesGroup(title="Export")
        dump = Adw.ActionRow(
            title="Save the raw stream",
            subtitle="Save the cutter commands for troubleshooting.")
        dump_btn = Gtk.Button(label="Save as...")
        dump_btn.set_valign(Gtk.Align.CENTER)
        dump_btn.connect("clicked", self.on_dump)
        dump.add_suffix(dump_btn)
        g3.add(dump)

        advanced.append(g3)
        self.stack.add_titled(page, "cut", "Cut Job")

    def cut_settings_group(self):
        group = Adw.PreferencesGroup(title="Cut Settings", css_classes=["scc-cut-settings"])
        _install_cut_settings_css(self.get_display())
        self.material_guide = Gtk.Button(label="Dial In a Material…", valign=Gtk.Align.CENTER)
        self.material_guide.connect("clicked", self.open_material_guide)
        group.set_header_suffix(self.material_guide)
        self.r_holder = _spin("Tool Holder", 1, 2, 1)
        self.r_holder.connect("notify::value", self.on_changed)
        self.send_holder_buttons = self.tool_holder_picker(group)
        self.r_tool_selection = _combo("Blade", ["Detect automatically"])
        self.r_tool_selection.set_tooltip_text(
            "Choose the blade installed in this holder if detection is wrong.")
        self.tool_picker_choices = ("",)
        self.tool_picker_id = self.r_tool_selection.connect("notify::selected", self.on_tool_selection)
        group.add(self.r_tool_selection)
        names = sorted(presets.all_presets())
        # Open on the material used last, when it still exists.
        last = config.load_settings().get("last_preset")
        chosen = last if isinstance(last, str) and last in names else config.DEFAULT_PRESET
        default = names.index(chosen) if chosen in names else 0
        self.r_preset = _combo("Material", names, default)
        self.preset_changed_id = self.r_preset.connect("notify::selected", self.on_preset)
        group.add(self.r_preset)
        # Its own row, so a long provenance never truncates the material's name.
        self.material_note = Gtk.Label(xalign=0, wrap=True, css_classes=["dim-label", "caption"],
                                       margin_top=6, margin_bottom=6, margin_start=12, margin_end=12)
        group.add(Adw.PreferencesRow(child=self.material_note, activatable=False, selectable=False))
        self.send_sliders, self.send_rows = {}, {}
        for field, title, low, high, value in (
                ("force", "Force", 1, 40, 30), ("speed", "Speed", 1, 30, 3),
                ("passes", "Passes", 1, 10, 3), ("depth", "Blade Depth", -1, 10, 7),
                ("acceleration", "Acceleration", -1, 3, -1)):
            row, precision, slider = self._slider_row(field, title, low, high, value, self.on_changed)
            group.add(row)
            setattr(self, f"r_{field}", precision)
            self.send_sliders[field] = slider
            self.send_rows[field] = row
            if field == "passes":
                group.add(self._stage_expander())
        # The staging note spells out the full passes too, so it follows them.
        for field in (self.r_force, self.r_speed, self.r_passes):
            field.connect("value-changed", self._refresh_stage_note)
        toggles = Gtk.Box(spacing=16, css_classes=["cut-toggles"])
        self.r_corners = Gtk.CheckButton(label="Line Segment Overcut")
        self.r_track = Gtk.CheckButton(label="Track Enhancing")
        for row in (self.r_corners, self.r_track):
            row.connect("notify::active", self.on_changed)
            toggles.append(row)
        group.add(Adw.PreferencesRow(child=toggles, activatable=False, selectable=False))
        # One place to choose a material: where it comes from is the Material
        # row's subtitle, and saving the tuned values sits after the values.
        self.save_preset_btn = Gtk.Button(label="Update Preset")
        self.save_preset_btn.connect("clicked", self.on_save_blade_preset)
        self.save_new_preset_btn = Gtk.Button(label="Save as New…")
        self.save_new_preset_btn.connect("clicked", self.on_save_new_preset)
        saving = Gtk.Box(spacing=8, homogeneous=True, margin_top=6, margin_bottom=6, margin_start=12, margin_end=12)
        saving.append(self.save_preset_btn)
        saving.append(self.save_new_preset_btn)
        self.preset_actions = Adw.PreferencesRow(child=saving, activatable=False, selectable=False)
        group.add(self.preset_actions)
        return group

    def material_library_group(self):
        library = Adw.PreferencesGroup(title="Material library")
        self.delete_preset_btn = Gtk.Button(label="Delete Preset")
        self.delete_preset_btn.connect("clicked", self.on_delete_selected_preset)
        self.import_csv_btn = Gtk.Button(label="Import CSV…")
        self.import_csv_btn.connect("clicked", self.on_choose_material_csv)
        self.import_folder_btn = Gtk.Button(label="Import Folder…")
        self.import_folder_btn.connect("clicked", lambda *_: self.on_choose_material_csv(folder=True))
        self.restore_materials_btn = Gtk.Button(label="Restore Defaults…")
        self.restore_materials_btn.connect("clicked", self.on_restore_default_materials)
        for buttons in ((self.delete_preset_btn,), (self.import_csv_btn, self.import_folder_btn),
                        (self.restore_materials_btn,)):
            row = Gtk.Box(spacing=8, homogeneous=True, margin_top=6, margin_bottom=6, margin_start=12, margin_end=12)
            for button in buttons:
                row.append(button)
            library.add(Adw.PreferencesRow(child=row, activatable=False, selectable=False))
        return library

    def tool_holder_picker(self, group):
        row = Gtk.Box(css_classes=["linked", "holder-tabs"], homogeneous=True)
        buttons = {}
        for slot in (1, 2):
            tab = Gtk.ToggleButton(label=f"Tool Holder {slot}", active=slot == int(self.r_holder.get_value()))
            if slot == 2:
                tab.set_group(buttons[1])
            tab.connect("toggled", lambda widget, number=slot: self.r_holder.set_value(number)
                        if widget.get_active() else None)
            row.append(tab)
            buttons[slot] = tab
        slot_row = Adw.PreferencesRow(child=row, activatable=False, selectable=False)
        group.add(slot_row)
        return buttons

    def add_control_help(self):
        for field, row in {
            "force": self.r_force, "speed": self.r_speed, "passes": self.r_passes, "depth": self.r_depth,
            "holder": self.r_holder, "acceleration": self.r_acceleration, "blade_diameter_mm": self.r_diameter,
            "sharpen_corners": self.r_corners, "corner_start_mm": self.r_corner_start, "corner_end_mm": self.r_corner_end,
            "track_enhancing": self.r_track, "path_order": self.r_path_order, "pass_order": self.r_pass_order,
            "interior_first": self.r_interiors, "delicate_media": self.r_delicate, "overcut_mm": self.r_overcut,
            "registration": self.r_register, "auto_search": self.r_auto, "mark_inset_mm": self.r_inset,
            "mark_length_mm": self.r_length, "mark_thickness_mm": self.r_thickness, "bleed_mm": self.r_bleed,
            "mat": self.r_mat, "end_position": self.r_end_position, "end_feed_mm": self.r_feed,
        }.items():
            row.set_tooltip_text(CONTROL_HELP[field])
        for field, slider in self.send_sliders.items():
            slider.set_tooltip_text(CONTROL_HELP[field])

    def on_colorblind_changed(self, toggle):
        self.preview.set_colorblind(toggle.get_active())
        settings = config.load_settings()
        settings['colorblind_mode'] = toggle.get_active()
        try:
            config.save_settings(settings)
        except OSError as exc:
            self.toast(f'Could not save colorblind preference: {exc}')

    def on_layout_preset(self, *_a):
        if self._suppress:
            return
        selected = self.r_layout_preset.get_selected()
        if selected == 0:
            if layout_presets.is_published(self.imported):
                self.on_clear_artwork()
            return
        profile = tuple(layout_presets.desktop_presets().values())[selected - 1]
        self.clear_bundle()
        options = profile.options()
        was, self._suppress = self._suppress, True
        self.imported = None
        self.forget_sent_design()
        self.template_shapes = ()
        self.import_row.set_title('Generated layout')
        self.import_row.set_subtitle('Using the sheet and card settings below')
        self.r_colour.set_model(Gtk.StringList.new(['all colours']))
        self.r_colour.set_selected(0)
        self.r_engine.set_selected(0 if options['engine'] == 'scm' else 1)
        self.r_sheet.set_selected(sorted(geometry.SHEETS).index(options['sheet']))
        self.r_card.set_selected(sorted(geometry.CARDS).index(options['card']))
        self.r_orient.set_selected({None: 0, True: 1, False: 2}[options['portrait']])
        self.r_mode.set_selected(0)
        self.r_radius.set_value(options['radius_mm'])
        self.r_pattern.set_selected(0)
        self.r_inset.set_value(options['mark_inset_mm'])
        self.r_length.set_value(options['mark_length_mm'] or 0)
        self.r_thickness.set_value(options['mark_thickness_mm'])
        self.r_bleed.set_value(options['bleed_mm'])
        self.r_skip.set_text('')
        self.r_register.set_active(True)
        self.r_auto.set_active(True)
        self._suppress = was
        if isinstance(profile, layout_presets.PublishedTemplate):
            self.set_artwork(profile.artwork(), profile.name)
        else:
            self._update_document_sheet(None)
            self.on_changed()

    def _text(self, row):
        model = row.get_model()
        return model.get_string(row.get_selected())

    def options(self) -> dict:
        orient = self._text(self.r_orient)
        portrait = {"Best fit": None, "Portrait": True,
                    "Landscape": False}[orient]
        engine = "scm" if self._text(self.r_engine) == "Silhouette Card Maker" \
            else "centered"
        length = self.r_length.get_value()
        send_mode = self._active_send_mode()
        options = dict(
            sheet=self._text(self.r_sheet),
            card=self._text(self.r_card),
            portrait=portrait,
            engine=engine,
            preset_name=self._text(self.r_preset),
            cutter=self._text(self.r_cutter),
            mode=self._text(self.r_mode),
            radius_mm=self.r_radius.get_value(),
            mark_inset_mm=self.r_inset.get_value(),
            mark_length_mm=(None if length <= 0 else length),
            mark_thickness_mm=self.r_thickness.get_value(),
            pattern=self._text(self.r_pattern),
            force=int(self.r_force.get_value()),
            speed=int(self.r_speed.get_value()),
            passes=int(self.r_passes.get_value()),
            depth=(int(self.r_depth.get_value())
                   if self.r_depth.get_sensitive() and self.r_depth.get_value() >= 0 else None),
            manual_depth=self.tool_selection.manual_depth,
            blade_options=self._blade_options(),
            bleed_mm=self.r_bleed.get_value(),
            registration=self.r_register.get_active(),
            auto_search=self.r_auto.get_active(),
            mat=list(graphtec.MAT_OPTIONS)[self.r_mat.get_selected()],
            end_position=list(graphtec.END_POSITIONS)[self.r_end_position.get_selected()],
            end_feed_mm=self.r_feed.get_value() if self.r_end_position.get_selected() == 0 else 0,
            skip=self._skip_indices(),
            offset_mm=(self.r_cal_x.get_value(), self.r_cal_y.get_value()),
            imported=self._selected_artwork() if send_mode == "simple" else self.imported,
            send_mode=send_mode,
            assignments=self._group_assignments() if send_mode != "simple" else (),
            weed_border_mm=self.r_weed_border.get_value() if self.imported else 0,
            weed_lines=(list(toolpath.WEED_LINES)[self.r_weed_lines.get_selected()]
                        if self.imported and self.r_weed_border.get_value() else "none"),
        )
        if send_mode != "simple":
            options["holder_profiles"] = self.holder_state.profiles(*self.current_holder_profile())
        if options["sheet"] == "Document Size":
            options.update(sheet=config.DEFAULT_SHEET, use_document_page=True)
        if self.pdf_bundle:
            options.update(self.pdf_bundle.layout, engine='scm', mode=template.ROUNDED,
                           bleed_mm=0, registration=True, skip=(), imported=self.imported)
        return options

    def _active_send_mode(self):
        return list(actions.SEND_MODES)[self.r_send_mode.get_selected()] if self.imported else "simple"

    def _group_assignments(self):
        ordered = sorted(self._send_rows, key=lambda entry: entry[-1].get_value())
        return tuple(actions.Assignment(key, self._text(profile), holder.get_selected() or None)
                     for key, _row, enabled, profile, holder, _order in ordered if enabled.get_active())

    def _refresh_send_groups(self):
        source = self.imported
        mode = self._active_send_mode()
        self.send_modes.set_visible(source is not None)
        self.r_colour.set_sensitive(source is not None and mode == "simple" and self.pdf_bundle is None)
        self.send_groups.set_visible(mode != "simple")
        cutter = self._text(self.r_cutter)
        holder_profiles = tuple(actions.HOLDER_SETTINGS.values())
        profiles = (*holder_profiles, *sorted(presets.all_presets(cutter))) if mode != "simple" else ()
        same_source = self._send_source is source
        if same_source and self._send_mode == mode and self._send_profiles == profiles:
            return
        if same_source:
            self._send_choices[self._send_mode] = {
                key: (enabled.get_active(), self._text(profile), holder.get_selected(), order.get_value())
                for key, _row, enabled, profile, holder, order in self._send_rows}
        else:
            self._send_choices.clear()
        for _, row, *_ in self._send_rows:
            self.send_groups.remove(row)
        self._send_rows = []
        self._send_source, self._send_mode, self._send_profiles = source, mode, profiles
        if mode == "simple":
            return

        found = actions.group_paths(source.paths, mode)
        choices = self._send_choices.get(mode, self._send_defaults if mode == "layer" else {})
        names = [actions.CURRENT_SETTINGS, *profiles]
        for index, (key, paths) in enumerate(found.items(), 1):
            active, preset_name, slot, position = choices.get(key, (False, names[0], 0, index))
            p_unit = "path" if len(paths) == 1 else "paths"
            row = Adw.ExpanderRow(title=key, subtitle=f"{len(paths)} {p_unit}")
            enabled = _switch("Send this group", active)
            preset = _combo("Preset", names, names.index(preset_name) if preset_name in names else 0)
            holder = _combo("Tool Holder", ["Use preset", "1", "2"], slot or 0)
            order = _spin("Send order", 1, len(found), position)
            for control, prop in ((enabled, "active"), (preset, "selected"),
                                  (holder, "selected"), (order, "value")):
                row.add_row(control)
                control.connect(f"notify::{prop}", self.on_changed)
                protect_settings_wheel(control)
            self.send_groups.add(row)
            self._send_rows.append((key, row, enabled, preset, holder, order))

    def _selected_artwork(self):
        """The imported drawing, narrowed to one line colour if chosen."""
        if self.imported is None:
            return None
        choice = self._text(self.r_colour)
        if choice == "all colours":
            return self.imported
        return self.imported.only(choice)

    def _skip_indices(self):
        """Parse the skip field, ignoring anything that is not a number."""
        out = []
        for part in self.r_skip.get_text().replace(";", ",").split(","):
            part = part.strip()
            if part.isdigit():
                out.append(int(part))
        return tuple(sorted(set(out)))

    # ---- rebuilding ---------------------------------------------------
    def on_changed(self, *_a):
        if self._suppress or self.active_job is not None:
            return
        fixed_layout = self.pdf_bundle or layout_presets.is_published(self.imported)
        for control in (self.r_sheet, self.r_orient, self.r_engine, self.r_card, self.r_mode, self.r_radius):
            control.set_sensitive(not fixed_layout)
        device = graphtec.device_by_name(self._text(self.r_cutter))
        self.restore_holder(device)
        self.update_holder_labels()
        self.correct_detected_depth()
        self.r_holder.set_sensitive(device.two_tools)
        self.r_depth.set_sensitive(device.autoblade and (not device.two_tools or self.r_holder.get_value() == 1)
                                   and not self.tool_selection.manual_depth)
        self.r_acceleration.set_sensitive(device.detects_tools)
        self.r_lift.set_sensitive(device.cameo3_on)
        corner_capable = device.cameo3_on and self.r_diameter.get_value() > 0
        self.r_corners.set_sensitive(corner_capable)
        for extension in (self.r_corner_start, self.r_corner_end):
            extension.set_sensitive(corner_capable and self.r_corners.get_active())
        self.r_feed.set_sensitive(self.r_end_position.get_selected() == 0)
        self.r_auto.set_sensitive(self.r_register.get_active())
        self._update_weed_controls()
        self._refresh_send_groups()
        previous = self.job
        self.job = None
        self.cut_button.set_sensitive(False)
        self.rebuild()
        orientation_changed = self.checked_scm_orientation != layout_presets.is_published(self.imported)
        if self.pdf_bundle and not self.bundle_row_error and self._sheet_token is None:
            self.measure_sheet()
        elif self.pdf_path and not self.pdf_bundle and self.checked_marks is not None and (
                self.checked_marks != (self.job.marks if self.job else None) or orientation_changed):
            self._sheet_token = None
            self.sheet_report = None
            self.checked_marks = None
            self.pdf_check_state = sheet_mod.RegistrationDecision.NONE
            self.preview.set_sheet(None)
            if orientation_changed:
                self.preview.set_pdf()
            self.scan_result.set_visible(False)
            self.sheet_result.set_title('Registration settings changed')
            self.sheet_result.set_subtitle('Measure the PDF again to compare the marks.')
            self.update_send_actions()

    def _update_weed_controls(self):
        mode = self._text(self.r_show_weed)
        can_weed = self.imported is not None
        self.weed_group.set_visible(mode == "Always" or (mode == "Auto" and can_weed))
        self.r_weed_border.set_sensitive(can_weed)
        self.r_weed_lines.set_sensitive(can_weed and self.r_weed_border.get_value() > 0)

    def on_show_weed_settings(self, *_args):
        self._update_weed_controls()
        settings = config.load_settings()
        settings["show_weed_settings"] = self._text(self.r_show_weed)
        try:
            config.save_settings(settings)
        except OSError as exc:
            self.toast(f"Could not save Weed Settings visibility. {exc}")

    @staticmethod
    def _slider_row(field, title, low, high, value, changed):
        """A cut setting as a slider with its exact value beside it."""
        row = Adw.ActionRow(title=title)
        pieces = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)
        adjustment = Gtk.Adjustment(lower=low, upper=high, value=value, step_increment=1, page_increment=10)
        # A fixed width, not hexpand: a stretching slider squeezes the title
        # until it wraps ("Accelerati-on") and starts each slider at a
        # different x. Fixed, every suffix is the same width and they align.
        # 96 px leaves "Acceleration" one line in the 1024 px window.
        slider = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=adjustment,
                           draw_value=False, width_request=96)
        slider.update_property([Gtk.AccessibleProperty.LABEL], [title])
        # One width for every value box, so the sliders line up even when a
        # box reads "Same as Speed".
        precision = Gtk.SpinButton(adjustment=adjustment, width_chars=len(BLADE_VALUE_TEXT["light_speed"]))
        precision.update_property([Gtk.AccessibleProperty.LABEL], [f"{title} value"])
        if field in BLADE_VALUE_TEXT:
            _special_value(precision, -1, BLADE_VALUE_TEXT[field])
        precision.bind_property("sensitive", slider, "sensitive", GObject.BindingFlags.SYNC_CREATE)
        precision.connect("value-changed", changed)
        pieces.append(slider)
        pieces.append(precision)
        row.add_suffix(pieces)
        return row, precision, slider

    def _stage_expander(self):
        """Staged passes, under Passes: a switch that opens the light pass settings."""
        self.r_stage = Adw.ExpanderRow(title="Lighter first passes", show_enable_switch=True,
                                       enable_expansion=False)
        self.r_stage.set_tooltip_text("Cut the first passes lighter, then finish at Force and Speed. "
                                      "Useful when a full first pass lifts a laminated edge.")
        self.stage_fields = {}
        for field, title, low, high in (("light_passes", "Light passes", 1, 9),
                                        ("light_force", "Light force", -1, 40),
                                        ("light_speed", "Light speed", -1, 30)):
            row, precision, _ = self._slider_row(field, title, low, high, 1 if low > 0 else -1,
                                                 self.on_stage_changed)
            precision.set_tooltip_text(CONTROL_HELP[field])
            self.r_stage.add_row(row)
            self.stage_fields[field] = precision
        self.stage_note = Gtk.Label(label="Every pass uses Force and Speed.", xalign=0, wrap=True,
                                    css_classes=["dim-label", "caption"],
                                    margin_top=6, margin_bottom=6, margin_start=12, margin_end=12)
        self.r_stage.add_row(Adw.PreferencesRow(child=self.stage_note, activatable=False, selectable=False))
        self.r_stage.connect("notify::enable-expansion", self.on_stage_toggled)
        return self.r_stage

    def on_stage_toggled(self, *_args):
        if self.r_stage.get_enable_expansion():
            self.r_stage.set_expanded(True)
            light = int(self.stage_fields["light_passes"].get_value())
            if self.r_passes.get_value() <= light:
                self.r_passes.set_value(light + 1)
        self.on_stage_changed()

    def on_stage_changed(self, *_args):
        self._refresh_stage_note()
        self.on_changed()

    def _refresh_stage_note(self, *_args):
        count = self._light_passes()
        total, force, speed = (int(field.get_value()) for field in (self.r_passes, self.r_force, self.r_speed))
        light_force, light_speed = (int(self.stage_fields[key].get_value()) for key in ("light_force", "light_speed"))
        self.stage_note.set_label(actions.stage_note(count, total, force, speed,
                                                     None if light_force < 0 else light_force,
                                                     None if light_speed < 0 else light_speed))

    def _light_passes(self):
        return int(self.stage_fields["light_passes"].get_value()) if self.r_stage.get_enable_expansion() else 0

    def _blade_options(self):
        acceleration = int(self.r_acceleration.get_value())
        return dict(holder=int(self.r_holder.get_value()) if self.r_holder.get_sensitive() else 1,
                    acceleration=acceleration if acceleration >= 0 else None,
                    blade_diameter_mm=round(self.r_diameter.get_value(), 2),
                    lift_head=self.r_lift.get_active(), sharpen_corners=self.r_corners.get_active(),
                    corner_start_mm=round(self.r_corner_start.get_value(), 1),
                    corner_end_mm=round(self.r_corner_end.get_value(), 1),
                    path_order=list(toolpath.PATH_ORDERS)[self.r_path_order.get_selected()],
                    pass_order=list(toolpath.PASS_ORDERS)[self.r_pass_order.get_selected()],
                    alternate_direction=self.r_alternate.get_active(),
                    overcut_mm=round(self.r_overcut.get_value(), 2),
                    interior_first=self.r_interiors.get_active(),
                    delicate_media=self.r_delicate.get_active(),
                    track_enhancing=self.r_track.get_active(),
                    **self._stage_options())

    def _stage_options(self):
        force, speed = (int(self.stage_fields[key].get_value()) for key in ("light_force", "light_speed"))
        return dict(light_passes=self._light_passes(), light_force=force if force >= 0 else None,
                    light_speed=speed if speed >= 0 else None)

    def current_holder_profile(self):
        preset = presets.get(self._text(self.r_preset), self._text(self.r_cutter))
        depth = int(self.r_depth.get_value())
        settings = replace(preset.cut_settings(), force=int(self.r_force.get_value()),
                           speed=int(self.r_speed.get_value()), passes=int(self.r_passes.get_value()),
                           depth=depth if depth >= 0 else None, **self._blade_options())
        return preset, settings

    def restore_holder(self, device):
        slot = int(self.r_holder.get_value()) if device.two_tools else 1
        fresh = slot not in self.holder_state.drafts
        saved = self.holder_state.switch(slot, *self.current_holder_profile()) if slot != self.holder_state.active else None
        self._suppress = True
        try:
            if saved:
                preset, settings = saved
                if fresh and preset.factory_group:
                    try:
                        preset = presets.for_context(preset, device.name, slot)
                    except ValueError:
                        preset = presets.get(presets.STARTING_NAME, device.name)
                    settings = for_holder(preset.cut_settings(), slot)
                self.refresh_preset_choices(preset.name)
                self._load_blade_settings(settings)
                self._on_select_material_preset()
            self.send_holder_buttons[2].set_sensitive(device.two_tools)
            self.send_holder_buttons[slot].set_active(True)
        finally:
            self._suppress = False

    def _load_blade_settings(self, settings):
        self.r_stage.set_enable_expansion(settings.light_passes > 0)
        if settings.light_passes:
            self.stage_fields["light_passes"].set_value(settings.light_passes)
        self.stage_fields["light_force"].set_value(-1 if settings.light_force is None else settings.light_force)
        self.stage_fields["light_speed"].set_value(-1 if settings.light_speed is None else settings.light_speed)
        self.r_force.set_value(settings.force)
        self.r_speed.set_value(settings.speed)
        self.r_passes.set_value(settings.passes)
        self.r_depth.set_value(-1 if settings.depth is None else settings.depth)
        self.r_holder.set_value(settings.holder)
        self.r_acceleration.set_value(-1 if settings.acceleration is None else settings.acceleration)
        self.r_diameter.set_value(settings.blade_diameter_mm)
        self.r_lift.set_active(settings.lift_head)
        self.r_corners.set_active(settings.sharpen_corners)
        self.r_corner_start.set_value(settings.corner_start_mm)
        self.r_corner_end.set_value(settings.corner_end_mm)
        self.r_path_order.set_selected(list(toolpath.PATH_ORDERS).index(settings.path_order))
        self.r_pass_order.set_selected(list(toolpath.PASS_ORDERS).index(settings.pass_order))
        self.r_alternate.set_active(settings.alternate_direction)
        self.r_overcut.set_value(settings.overcut_mm)
        self.r_interiors.set_active(settings.interior_first)
        self.r_delicate.set_active(settings.delicate_media)
        self.r_track.set_active(settings.track_enhancing)

    def on_save_new_preset(self, *_args):
        entry = Gtk.Entry(placeholder_text="Preset name", activates_default=True)
        dialog = Adw.AlertDialog(heading="Save as New Preset", extra_child=entry)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("save", "Save")
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("save")
        dialog.set_close_response("cancel")
        dialog.connect("response::save", lambda *_: self.on_save_blade_preset(new_name=entry.get_text()))
        dialog.present(self)
        entry.grab_focus()

    def on_save_blade_preset(self, *_a, new_name=None):
        if self._job_locked or self._sending:
            return
        name = self._text(self.r_preset)
        try:
            saved = presets.save_blade_settings(
                name,
                force=int(self.r_force.get_value()), speed=int(self.r_speed.get_value()),
                depth=int(self.r_depth.get_value()) if self.r_depth.get_value() >= 0 else None,
                passes=int(self.r_passes.get_value()), blade_options=self._blade_options(),
                new_name=new_name, cutter=self._text(self.r_cutter))
        except (OSError, ValueError) as exc:
            self.toast(f"Could not save preset: {exc}")
            return
        if saved is None:
            self.toast("Preset settings are already saved.")
            return
        active_name = new_name.strip() if new_name is not None else name
        self.refresh_preset_choices(active_name)
        self._load_materials()
        self.on_changed()
        self.toast(f"Saved {active_name}.")

    def on_preset(self, *_a):
        name, cutter = self._text(self.r_preset), self._text(self.r_cutter)
        slot = self.holder_state.active
        try:
            p = presets.for_context(presets.get(name, cutter), cutter, slot)
        except ValueError as error:
            self.toast(str(error))
            p = presets.get(presets.STARTING_NAME, cutter)
        if p.name != name:
            model = self.r_preset.get_model()
            index = next(i for i in range(model.get_n_items()) if model.get_string(i) == p.name)
            with self.r_preset.handler_block(self.preset_changed_id):
                self.r_preset.set_selected(index)
        self._on_select_material_preset()
        was = self._suppress
        self._suppress = True
        self._load_blade_settings(for_holder(p.cut_settings(), slot))
        self._suppress = was
        self._remember_preset(p.name)
        self.on_changed()

    def _remember_preset(self, name):
        settings = config.load_settings()
        if settings.get("last_preset") == name:
            return
        settings["last_preset"] = name
        try:
            config.save_settings(settings)
        except OSError as exc:
            self.toast(f"Could not save the material choice: {exc}")

    def refresh_preset_choices(self, name=None):
        name = name or self._text(self.r_preset)
        names = sorted(presets.all_presets(self._text(self.r_cutter)))
        if name not in names:
            name = config.DEFAULT_PRESET
        with self.r_preset.handler_block(self.preset_changed_id):
            self.r_preset.set_model(Gtk.StringList.new(names))
            self.r_preset.set_selected(names.index(name))

    def rebuild(self):
        if self._suppress or self._building or self.active_job is not None:
            return
        if self.pdf_bundle and self.imported is None:
            self.job = None
            self.preview.set_job(None)
            self.summary_icon.set_from_icon_name('document-open-symbolic')
            self.summary_title.set_label('Cut paths unavailable' if self.bundle_row_error else 'Legacy cut file')
            self.summary_layout.set_label(self.bundle_row_error or 'Use Sheet → Save Paired Cut File.')
            self.summary_material.set_label('')
            self.summary_note.set_label('' if self.bundle_row_error else
                                        'A newer Print & Cut file includes SVG paths for direct cutting.')
            self.checks.set_text('')
            self.update_send_actions()
            self.banner.set_revealed(False)
            return
        self._building = True
        try:
            opts = self.options()
            if self.r_layout_preset.get_selected() in (0, self.default_layout_index):
                self._suppress = True
                try:
                    self.r_layout_preset.set_selected(self.default_layout_index
                                                      if self.pdf_bundle is None and layout_presets.uses_default(opts)
                                                      else 0)
                finally:
                    self._suppress = False
            j = job_mod.build(**opts)
        except geometry.LayoutImpossible as exc:
            self.job = None
            self.preview.set_job(None)
            self.checks.set_text("")
            self.summary_icon.set_from_icon_name('dialog-error-symbolic')
            self.summary_title.set_label('Layout needs attention')
            self.summary_layout.set_label(str(exc))
            self.summary_material.set_label('')
            self.summary_note.set_label('Adjust the sheet or card layout to continue.')
            self.banner.set_title(str(exc))
            self.banner.set_revealed(True)
            self.update_send_actions()
            self._building = False
            return
        except Exception as exc:              # noqa: BLE001
            self.job = None
            self.preview.set_job(None)
            self.checks.set_text(traceback.format_exc())
            self.summary_icon.set_from_icon_name('dialog-error-symbolic')
            self.summary_title.set_label('Could not check the layout')
            self.summary_layout.set_label(str(exc))
            self.summary_material.set_label('')
            self.summary_note.set_label('Advanced details contains the diagnostic report.')
            self.banner.set_title(f"Could not build the job: {exc}")
            self.banner.set_revealed(True)
            self.update_send_actions()
            self._building = False
            return

        self.job = j
        self.preview.set_job(j)
        failed_pdf = self.pdf_bundle and self.sheet_report is not None and not self.bundle_checked
        self.summary_icon.set_from_icon_name(
            'dialog-error-symbolic' if j.blocked or failed_pdf else 'emblem-default-symbolic')
        self.summary_title.set_label(
            'Layout needs attention' if j.blocked else
            'PDF registration needs attention' if failed_pdf else 'Layout checked')
        orientation = 'portrait' if j.grid.portrait else 'landscape'
        dimensions = f'{j.grid.sheet_w_mm:g} × {j.grid.sheet_h_mm:g} mm'
        ver_text = confirmation.verification_statement(j)
        self.summary_layout.set_label(f'{ver_text}\n{j.grid.sheet_name} {orientation} · {dimensions}')
        if j.action_groups:
            holders = ", ".join(str(holder) for holder in sorted({group.settings.holder for group in j.action_groups}))
            caption = f"{len(j.action_groups)} send groups · Tool holders: {holders}"
        else:
            passes = "pass" if j.settings.passes == 1 else "passes"
            caption = f"{j.preset.name} · {j.settings.passes} {passes}"
        self.summary_material.set_label(caption)
        mask_hint_text = j.cover_note()
        self.summary_note.set_label(
            j.blocked[0] if j.blocked else
            'Open Sheet to see which marks failed the PDF check.' if failed_pdf else
            j.warnings[0] if j.warnings else
            mask_hint_text if mask_hint_text else
            'Test these material settings on an offcut.' if j.preset.origin == 'derived' else '')

        lines = [f"{label}: {value}" for label, value in j.summary()]
        lines.append("")
        for r in j.rows:
            lines.append(f"[{'ok  ' if r.ok else 'FAIL'}] {r.label}: {r.got}")
        for c in j.collisions:
            lines.append(f"[FAIL] collision: {c}")
        for o in j.off_sheet:
            lines.append(f"[FAIL] off sheet: {o}")
        for w in j.warnings:
            lines.append(f"[warn] {w}")
        if j.mask_hints:
            lines.append("")
            lines.append("COVER FOR THE SCAN:")
            for h in j.mask_hints:
                lines.append(f"  {h['text']}")
            lines.append("Uncover it before cutting. Use Test scan to check the printed marks first.")
        self.checks.set_text("\n".join(lines))

        blocked = j.blocked
        if blocked:
            p_unit = "problem" if len(blocked) == 1 else "problems"
            self.banner.set_title(f"{len(blocked)} {p_unit}: {blocked[0]}")
            self.banner.set_revealed(True)
        elif not j.registration:
            self.banner.set_title(
                "Registration is OFF: blade will cut in sheet coordinates (for blank sheets only)")
            self.banner.set_revealed(True)
        else:
            self.banner.set_revealed(False)

        self.update_send_actions()
        self._building = False

    # ---- actions ------------------------------------------------------
    def toast(self, text):
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=5))

    def on_export(self, *_a):
        if self.job is None:
            self.toast("Nothing to export: fix the layout first.")
            return
        j = self.job
        stem = (f"{j.grid.sheet_name.lower()}-{j.grid.card.name}-"
                f"{j.grid.cols}x{j.grid.rows}-"
                f"{'portrait' if j.grid.portrait else 'landscape'}-{j.mode}")
        try:
            written = job_mod.write_templates(j, stem)
        except OSError as exc:
            self.toast(f"Could not write: {exc}")
            return
        f_unit = "file" if len(written) == 1 else "files"
        self.toast(f"Wrote {len(written)} {f_unit} to {config.TEMPLATE_DIR}")

    def on_dump(self, *_a):
        if self.job is None:
            self.toast("Nothing to save yet.")
            return
        dialog = Gtk.FileDialog(initial_name="cut-stream.bin")

        def done(d, result):
            try:
                gfile = d.save_finish(result)
            except GLib.Error:
                return
            pathlib.Path(gfile.get_path()).write_bytes(self.job.stream)
            self.toast(f"Wrote {len(self.job.stream):,} bytes")

        dialog.save(self, None, done)

    # ---- imported artwork --------------------------------------------
    def on_open_artwork(self, *_a):
        if self._job_locked or self._sending:
            return
        dialog = Gtk.FileDialog(title="Open Cut Template")
        filt = Gtk.FileFilter()
        filt.set_name("Cut templates (SVG, DXF, Studio3)")
        filt.add_pattern("*.svg")
        filt.add_pattern("*.dxf")
        filt.add_pattern("*.studio3")
        filt.add_pattern("*.STUDIO3")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(filt)
        dialog.set_filters(filters)

        def done(d, result):
            try:
                gfile = d.open_finish(result)
            except GLib.Error:
                return
            filename = gfile.get_path()
            if filename is None:
                self.toast("Choose a local cut file.")
                return
            self.load_artwork_file(pathlib.Path(filename))

        dialog.open(self, None, done)

    def load_artwork_file(self, path, page=None):
        if self._job_locked or self._sending:
            return
        from . import importer
        try:
            artwork = importer.load(path, page=page)
        except importer.InkscapePageRequired as exc:
            choose_inkscape_page(self, path, exc.count, lambda page: self.load_artwork_file(path, page))
            return
        except Exception as exc:              # noqa: BLE001
            self.toast(f"Could not read {path.name}: {exc}")
            return
        self.clear_bundle()
        self.template_shapes = ()
        name = f"{path.name} · Page {page}" if page is not None else path.name
        self.set_artwork(artwork, name)

    def on_edit_template(self, *_args, blank=False, replace_design=False):
        if self.pdf_bundle:
            dialog = Adw.AlertDialog(
                heading="Matched Cut Paths Locked",
                body="Matched cut paths are locked to the printed sheet. To edit cut paths or clear the match, open Sheet.")
            dialog.add_response("cancel", "Cancel")
            dialog.add_response("sheet", "Open Sheet")
            dialog.set_response_appearance("sheet", Adw.ResponseAppearance.SUGGESTED)
            dialog.set_default_response("sheet")
            dialog.set_close_response("cancel")

            def response(_dialog, resp):
                if resp == "sheet":
                    self.stack.set_visible_child_name("sheet")

            dialog.connect("response", response)
            dialog.present(self)
            return
        if self._sending:
            self.toast('Wait for the current job to finish before opening Design.')
            return
        if self.design is not None and not (blank or replace_design):
            self.workspaces.set_visible_child(self.design.view)
            return
        if self.job is None:
            self.toast('Select a sheet before opening Design.')
            return
        from .editor import DesignWorkspace
        from .editor_model import Project, Registration, editable_import, shapes_from_paths

        try:
            shapes = () if blank else (self.template_shapes or (
                editable_import(self.imported)[0] if self.imported else shapes_from_paths(self.job.paths)))
        except ValueError as error:
            self.toast(str(error))
            return
        grid = self.job.grid
        registration = Registration.from_marks(self.job.marks) if self.job.registration else None
        project = Project(grid.sheet_w_mm, grid.sheet_h_mm, shapes, registration, self.job.mat)
        device = self.job.device

        def open_document():
            if self.design is not None:
                self.design.replace_document(project)
                self.design.cutter = device
                self.design.menu_actions.lookup_action('holder-2').set_enabled(device.two_tools)
            else:
                self.design = DesignWorkspace(self, project, self.send_from_design, self.review_design, cutter=device)
                self.workspaces.add_named(self.design.view, 'design')
            self.forget_sent_design()
            self.workspaces.set_visible_child(self.design.view)

        if self.design is None:
            open_document()
        else:
            self.design.confirm_discard(open_document)

    def send_from_design(self, *_args):
        """Hand off the current design or explain why it cannot be sent."""
        from .editor_model import send_handoff

        verdict, detail = send_handoff(self.design.project, self.design_origin, self.reviewed_design)
        if verdict == "invalid":
            self.design.show_message(detail, level='warning')
            return
        if verdict == "apply":
            self.review_design(self.design.project, detail)
            return
        if verdict == "stickers":
            self.toast("Sticker sheets are cut from their Print & Cut file. Choose Export Print & Cut, "
                       "then open the file in Sheet.")
        if verdict != "keep":
            self.forget_sent_design()
        self.leave_design()

    def forget_sent_design(self):
        """Send has artwork of its own now: the design as it stands counts as
        seen, so only later edits replace that artwork."""
        self.reviewed_design = None
        self.design_origin = self.design.project if getattr(self, "design", None) is not None else None

    def leave_design(self, *_args):
        self.workspaces.set_visible_child_name('tools')
        target = getattr(self, "last_send_tab", "cut")
        if target in ("layout", "sheet", "cut"):
            self.stack.set_visible_child_name(target)
        else:
            self.stack.set_visible_child_name("cut")
        self.update_send_actions()

    def close_design(self, *_args):
        def discard():
            self.workspaces.set_visible_child_name('tools')
            self.workspaces.remove(self.design.view)
            self.design = None
            self.update_send_actions()

        self.design.confirm_discard(discard)

    def confirm_window_close(self, *_args):
        if self._sending:
            self.toast("Finish or cancel the cutter operation before closing.")
            return True
        if self._close_confirmed or self.design is None or not self.design.dirty:
            return False

        def discard():
            self._close_confirmed = True
            self.close()

        self.design.confirm_discard(discard)
        return True

    def review_design(self, project, action):
        from .editor_model import ALL_ACTIONS, holder_defaults
        artwork = project.artwork(action)
        if not artwork.paths or any(path.length_mm() <= 0 for path in artwork.paths):
            self.design.show_message('Draw or resize the paths before reviewing.', level='warning')
            return
        self.tuning = False
        group_defaults = holder_defaults(artwork)
        self.template_shapes = project.shapes
        self._suppress = True
        try:
            self.r_layout_preset.set_selected(0)
            self.r_mode.set_selected(0)
            self.r_mat.set_selected(list(graphtec.MAT_OPTIONS).index(project.mat))
            send_mode = "layer" if group_defaults or action == ALL_ACTIONS else "simple"
            self.r_send_mode.set_selected(list(actions.SEND_MODES).index(send_mode))
            self.r_skip.set_text('')
            self.r_register.set_active(project.registration is not None)
            if (marks := project.registration) is not None:
                self.r_pattern.set_selected(regmarks.PATTERNS.index(marks.pattern))
                self.r_inset.set_value(marks.inset_mm)
                self.r_length.set_value(marks.length_mm)
                self.r_thickness.set_value(marks.thickness_mm)
        finally:
            self._suppress = False
        self.set_artwork(artwork, f'Design · {action}', group_defaults)
        self.reviewed_design = project
        self.leave_design()
        self.stack.set_visible_child_name("cut")

    def set_artwork(self, artwork, name, group_defaults=None):
        self.forget_sent_design()
        self.imported = artwork
        self._send_defaults = group_defaults or {}
        self.import_row.set_title(name)
        lines = [artwork.describe(), *artwork.warnings] if artwork else ['Cut template for Silhouette Studio']
        self.import_row.set_subtitle("\n".join(lines))
        was, self._suppress = self._suppress, True
        if not layout_presets.is_published(artwork):
            self.r_layout_preset.set_selected(0)
        self._update_document_sheet(artwork)
        self.r_colour.set_model(Gtk.StringList.new(["all colours", *(artwork.colours() if artwork else ())]))
        self.r_colour.set_selected(0)
        self._suppress = was
        self.on_changed()

    def _update_document_sheet(self, artwork):
        sheets = sorted(geometry.SHEETS)
        current = self._text(self.r_sheet)
        native = artwork is not None and artwork.use_document_page
        if native:
            self.r_sheet.set_model(Gtk.StringList.new([*sheets, "Document Size"]))
            self.r_sheet.set_selected(len(sheets))
            self.r_orient.set_selected(1 if artwork.width_mm <= artwork.height_mm else 2)
        elif current == "Document Size":
            self.r_sheet.set_model(Gtk.StringList.new(sheets))
            self.r_sheet.set_selected(sheets.index(config.DEFAULT_SHEET))
        elif self.r_sheet.get_model().get_n_items() > len(sheets):
            self.r_sheet.set_model(Gtk.StringList.new(sheets))
            self.r_sheet.set_selected(sheets.index(current))

    def on_clear_artwork(self, *_a):
        if self._job_locked or self._sending or (self.imported is None and self.pdf_bundle is None):
            return
        self.clear_bundle()
        self.imported = None
        self.template_shapes = ()
        was, self._suppress = self._suppress, True
        self.r_layout_preset.set_selected(0)
        self._update_document_sheet(None)
        self.r_colour.set_model(Gtk.StringList.new(["all colours"]))
        self.r_colour.set_selected(0)
        self._suppress = was
        self.import_row.set_title("Generated layout")
        self.import_row.set_subtitle(
            "Using the sheet and card settings below")
        self.on_changed()

    # ---- the printed sheet -------------------------------------------
    def bundle_locked_controls(self):
        return (self.r_layout_preset, self.r_sheet, self.r_orient, self.r_engine,
                self.r_card, self.r_pattern, self.r_inset, self.r_length,
                self.r_thickness, self.r_bleed, self.r_register, self.r_mode,
                self.r_radius, self.r_skip, self.r_colour)

    def lock_bundle_layout(self, locked):
        self._bundle_locked = bool(locked)
        for widget in self.bundle_locked_controls():
            widget.set_sensitive(not locked)

    def job_controls(self):
        """Every settings widget a running job owns, so none of them can be edited."""
        widgets = [value for name, value in vars(self).items()
                   if name.startswith("r_") and isinstance(value, Gtk.Widget)]
        widgets.extend(widget for widget in (getattr(self, "use_generated", None),
                                             getattr(self, "material_guide", None))
                       if widget is not None)
        widgets.extend(widget for _, _, *fields in self._send_rows for widget in fields)
        widgets.extend(self.material_buttons())
        return tuple(widgets)

    def lock_job_controls(self, locked):
        """A sent job owns its settings: show them, and refuse edits until it ends."""
        if locked and not self._job_locked:
            self._job_control_states = {widget: widget.get_sensitive() for widget in self.job_controls()}
            for widget in self._job_control_states:
                widget.set_sensitive(False)
        self._job_locked = locked
        if not locked:
            for widget, sensitive in self._job_control_states.items():
                widget.set_sensitive(sensitive)
            self._job_control_states.clear()
            self.on_changed()
            if self._bundle_locked:
                self.lock_bundle_layout(True)

    def clear_bundle(self):
        if not self.pdf_bundle:
            return
        self.pdf_bundle = None
        self.bundle_export.set_visible(False)
        self.lock_bundle_layout(False)
        self.clear_printed_sheet()

    def update_sheet_controls(self):
        loaded = self.pdf_path is not None
        editable = not (self._job_locked or self._sending)
        self.close_print.set_sensitive(editable and loaded)
        self.close_print.set_tooltip_text("Close the open PDF." if loaded else "No print file open.")
        checking = self.pdf_check_state == sheet_mod.RegistrationDecision.CHECKING
        can_measure = editable and loaded and self.bundle_row_error is None
        self.measure_button.set_sensitive(can_measure and not checking)
        self.measure_button.set_label("Checking PDF Marks…" if checking else "Check PDF Marks")
        self.measure_button.set_tooltip_text(
            "Measure printed registration marks on the open PDF."
            if can_measure
            else (self.bundle_row_error or "Open a PDF first.")
        )
        self.r_sheet_page.set_sensitive(loaded and (not self.pdf_bundle or len(self.pdf_bundle.rows) > 1))

    def clear_printed_sheet(self):
        self.bundle_checked = False
        self.pdf_check_state = sheet_mod.RegistrationDecision.NONE
        self.checked_marks = None
        self.bundle_row_error = None
        self.pdf_path = None
        self._sheet_token = None
        self.sheet_report = None
        self.preview.set_sheet(None)
        self.preview.set_pdf()
        self.sheet_row.set_title('No print file open')
        self.sheet_row.set_subtitle('Open the PDF you printed.')
        self.sheet_result.set_title('Not measured')
        self.sheet_result.set_subtitle('')
        self.scan_result.set_visible(False)
        self.r_sheet_page.set_title('PDF page')
        self.r_sheet_page.set_subtitle('')
        self.r_sheet_page.set_value(1)
        self.r_sheet_page.get_adjustment().set_upper(99)
        self.update_sheet_controls()

    def close_printed_sheet(self, *_args):
        if self._job_locked or self._sending:
            return
        if self.pdf_bundle:
            self.on_clear_artwork()
        else:
            self.clear_printed_sheet()
            self.rebuild()

    def select_bundle_sheet(self):
        row = self.pdf_bundle.rows[int(self.r_sheet_page.get_value())-1]
        self.bundle_row_error = None
        try:
            artwork = self.pdf_bundle.artwork(row) if 'svg' in row else None
        except bundle.BundleError as exc:
            self.bundle_row_error = str(exc)
            artwork = None
        self.describe_bundle_sheet(row)
        self.bundle_checked = False
        self.pdf_check_state = sheet_mod.RegistrationDecision.NONE
        self._sheet_token = self.sheet_report = None
        self.preview.set_sheet(None)
        self.preview.set_pdf()
        self.scan_result.set_visible(False)
        self.update_sheet_controls()
        self.set_artwork(artwork, row.get('svg') or row['cutFile'])
        if self.bundle_row_error:
            self.sheet_result.set_title("Could not open cut paths")
            self.sheet_result.set_subtitle(self.bundle_row_error)

    def describe_bundle_sheet(self, row):
        self.sheet_row.set_subtitle(
            f'Sheet {row["sheet"]} · PDF front page {row["frontPage"]}\n'
            + self.pdf_bundle.mat_placement())

    def on_save_bundle_file(self, *_args):
        matched = self.pdf_bundle
        if matched is None:
            return
        row = matched.rows[int(self.r_sheet_page.get_value()) - 1]
        try:
            name = matched.cut_file_name(row)
        except bundle.BundleError as error:
            self.toast(str(error))
            return
        self.save_bundle_member("Save Paired Cut File", name, lambda path: matched.save_cut_file(row, path))

    def on_save_bundle_print(self, *_args):
        matched = self.pdf_bundle
        if matched:
            self.save_bundle_member("Save Print PDF", pathlib.Path(matched.pdf).name, matched.save_print_pdf)

    def save_bundle_member(self, title, name, save):
        dialog = Gtk.FileDialog(title=title, initial_name=name)

        def done(chooser, result):
            try:
                path = chooser.save_finish(result).get_path()
                if path is not None:
                    self.toast(f"Saved {save(path).name}.")
            except GLib.Error:
                return
            except bundle.BundleError as error:
                self.toast(str(error))

        dialog.save(self, None, done)

    def on_pick_pdf(self, *_a):
        if self._job_locked or self._sending:
            return
        dialog = Gtk.FileDialog(title="Open Print File")
        filt = Gtk.FileFilter()
        filt.set_name('Print files')
        for suffix in ('pdf', 'zip'):
            filt.add_suffix(suffix)
        all_files = Gtk.FileFilter()
        all_files.set_name('All files')
        all_files.add_pattern('*')
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(filt)
        filters.append(all_files)
        dialog.set_filters(filters)
        dialog.set_default_filter(filt)

        def done(d, result):
            if self._job_locked or self._sending:
                return
            try:
                gfile = d.open_finish(result)
            except GLib.Error:
                return
            filename = gfile.get_path()
            if filename is None:
                self.toast('Choose a local print file.')
                return
            path = pathlib.Path(filename)
            if path.suffix.lower() == '.zip' or zipfile.is_zipfile(path):
                try:
                    matched = bundle.load(path)
                    row = matched.rows[0]
                    artwork = matched.artwork(row) if 'svg' in row else None
                except bundle.BundleError as exc:
                    self.toast(str(exc))
                    return
                self.clear_bundle()
                self.pdf_bundle = matched
                self.bundle_export.set_visible(True)
                self.lock_bundle_layout(True)
                self.pdf_path = path
                self._sheet_token = self.sheet_report = None
                self.preview.set_sheet(None)
                self.preview.set_pdf()
                self.sheet_row.set_title(path.name)
                self._suppress = True
                self.r_sheet_page.set_title('Matched sheet')
                self.r_sheet_page.set_subtitle('which matched sheet to check and cut')
                self.r_sheet_page.get_adjustment().set_upper(len(matched.rows))
                self.r_sheet_page.set_value(1)
                self._suppress = False
                self.describe_bundle_sheet(row)
                self.update_sheet_controls()
                self.set_artwork(artwork, row.get('svg') or row['cutFile'])
                self.stack.set_visible_child_name("sheet")
                return
            if self.pdf_bundle:
                self.on_clear_artwork()
            self.pdf_path = path
            self.preview.set_pdf()
            with self.r_sheet_page.handler_block(self.sheet_page_changed_id):
                self.r_sheet_page.set_value(1)
            self.sheet_row.set_title(self.pdf_path.name)
            self.sheet_row.set_subtitle(str(self.pdf_path.parent))
            self.update_sheet_controls()
            self.measure_sheet()
            self.stack.set_visible_child_name("sheet")

        dialog.open(self, None, done)

    def on_pdf_page_changed(self, *_a):
        if self._suppress or self.pdf_path is None:
            return
        self._sheet_token = None
        self.pdf_check_state = sheet_mod.RegistrationDecision.NONE
        self.checked_marks = None
        self.preview.set_pdf()
        self.measure_sheet()

    def on_sheet_number_changed(self, *_a):
        if self.pdf_bundle and not self._suppress:
            self.select_bundle_sheet()
        else:
            self.on_pdf_page_changed()

    def measure_sheet(self):
        if self._job_locked or self._sending or self.bundle_row_error:
            return
        path = getattr(self, "pdf_path", None)
        matched = self.pdf_bundle
        if path is None or (self.job is None and not matched):
            self.toast("Load a PDF and fix the layout first.")
            return
        self.scan_result.set_visible(False)
        self.sheet_result.set_title("Checking PDF marks…")
        self.sheet_result.set_subtitle("")
        marks = matched.marks() if matched else self.job.marks
        page = int(self.r_sheet_page.get_value())
        if matched:
            row = matched.rows[page-1]
            self.bundle_checked = False
        dpi = int(self._text(self.r_sheet_dpi))
        orient_scm = layout_presets.is_published(self.imported)
        token = self._sheet_token = object()
        self.pdf_check_state = sheet_mod.RegistrationDecision.CHECKING
        self.checked_marks = marks
        self.checked_scm_orientation = orient_scm
        self.sheet_report = None
        self.preview.set_sheet(None)
        self.update_send_actions()

        def work():
            from . import sheet as sheet_mod
            try:
                if matched:
                    report, image = matched.measure(row, marks, dpi)
                else:
                    report, image = sheet_mod.measure_preview(path, marks, page, dpi, orient_scm=orient_scm)
                problems = report.problems(marks.sheet_w_mm, marks.sheet_h_mm)
                png = io.BytesIO()
                image.save(png, format='PNG')
            except Exception as exc:              # noqa: BLE001
                GLib.idle_add(self._sheet_failed, str(exc), token)
                return
            GLib.idle_add(self._sheet_done, report, problems, png.getvalue(), token)

        threading.Thread(target=work, daemon=True).start()

    def _sheet_failed(self, message, token):
        if not sheet_request_current(self._sheet_token, token):
            return
        self.pdf_check_state = sheet_mod.RegistrationDecision.ERROR
        self.sheet_report = None
        self.checked_marks = None
        self.preview.set_sheet(None)
        self.preview.set_pdf()
        self.sheet_result.set_title("Could not check PDF marks")
        self.sheet_result.set_subtitle(message)
        self.rebuild()

    def _sheet_done(self, report, problems, png, token):
        if not sheet_request_current(self._sheet_token, token):
            return
        self.sheet_report = report
        self.bundle_checked = bool(self.pdf_bundle) and not problems
        self.pdf_check_state = (sheet_mod.RegistrationDecision.FAILED if problems
                                else sheet_mod.RegistrationDecision.PASSED)
        self.preview.set_sheet(report)
        self.preview.set_pdf(png, report.page_w_mm, report.page_h_mm)
        lines = [m.describe() for m in report.marks]
        if getattr(report, 'preview_rotation', 0) in (90, 270):
            lines.insert(0, 'Load the printed sheet face-up in the orientation shown in the preview.')
        if problems:
            lines.extend(("", report.diagnosis(), *problems))
        p_unit = "problem" if len(problems) == 1 else "problems"
        self.sheet_result.set_title(
            "PDF marks match the template" if not problems
            else f"PDF marks need attention · {len(problems)} {p_unit}")
        self.sheet_result.set_subtitle("\n".join(lines))
        self.rebuild()

    # ---- Silhouette's own materials ----------------------------------
    def on_choose_material_csv(self, *_args, folder=False):
        from . import materials

        if self._job_locked or self._sending:
            return
        dialog = Gtk.FileDialog(title="Choose Studio factory folder" if folder else "Choose Silhouette condition.csv")
        filt = Gtk.FileFilter()
        filt.set_name("CSV files")
        filt.add_suffix("csv")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(filt)
        dialog.set_filters(filters)

        def done(chooser, result):
            try:
                path = (chooser.select_folder_finish(result) if folder else chooser.open_finish(result)).get_path()
                if path is not None:
                    materials.select_condition_csv(path)
                    active = self._text(self.r_preset)
                    self.refresh_preset_choices()
                    self._load_materials()
                    if self._text(self.r_preset) != active:
                        self.on_preset()
                    else:
                        self.on_changed()
                    self.toast(f"Loaded {len(presets.factory_presets(self._text(self.r_cutter)))} material presets.")
            except GLib.Error:
                return
            except (OSError, ValueError) as error:
                self.toast(str(error))

        (dialog.select_folder if folder else dialog.open)(self, None, done)

    def on_material_cutter_changed(self, *_args):
        selected = self._text(self.r_preset)
        self.refresh_preset_choices()
        self._load_materials()
        active = self._materials[self._text(self.r_preset)]
        if active.factory_id or active.name != selected:
            self.on_preset()

    def _on_select_material_preset(self, *_args):
        name, cutter = self._text(self.r_preset), self._text(self.r_cutter)
        preset = getattr(self, "_materials", {}).get(name)
        self.material_note.set_label((preset.note or preset.description) if preset else "")
        action = presets.removal_action(name, cutter) if preset else ""
        self.delete_preset_btn.set_label(f"Revert ‘{name}’ to Default" if action == "restore" else f"Delete ‘{name}’")
        self.delete_preset_btn.set_sensitive(bool(action) and not (self._job_locked or self._sending))
        self.delete_preset_btn.set_tooltip_text(None if action else "Built-in presets cannot be deleted.")
        self.save_preset_btn.set_tooltip_text(f"Save the current values to ‘{name}’.")

    def material_buttons(self):
        return (self.save_preset_btn, self.save_new_preset_btn, self.delete_preset_btn,
                self.import_csv_btn, self.import_folder_btn, self.restore_materials_btn)

    def on_delete_selected_preset(self, *_args):
        if self._job_locked or self._sending:
            return
        name, cutter = self._text(self.r_preset), self._text(self.r_cutter)
        action = presets.removal_action(name, cutter)
        if not action:
            return
        restore = action == "restore"
        title = "Revert to Default" if restore else "Delete Preset"
        prompt = (f"Remove your saved changes to ‘{name}’ and restore the original settings?"
                  if restore else f"Delete the saved preset ‘{name}’?")
        dialog = Adw.AlertDialog(heading=title, body=prompt)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Revert" if restore else "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_close_response("cancel")
        dialog.set_default_response("cancel")

        def delete_confirmed(_dialog, response):
            if response != "delete":
                return
            if not presets.delete_preset(name, cutter):
                self.toast(f"Could not remove ‘{name}’.")
                return
            self.holder_state.drafts = {slot: (preset, settings) for slot, (preset, settings)
                                        in self.holder_state.drafts.items() if preset.name != name}
            self.refresh_preset_choices()
            self._load_materials()
            self.on_preset()
            self.toast(f"Restored ‘{name}’." if restore else f"Deleted ‘{name}’.")

        dialog.connect("response", delete_confirmed)
        dialog.present(self)

    def _load_materials(self):
        self._materials = presets.all_presets(self._text(self.r_cutter))
        self._on_select_material_preset()

    def on_restore_default_materials(self, *_args):
        if self._job_locked or self._sending:
            return
        dialog = Adw.AlertDialog(heading="Restore Default Materials?",
                                 body="Edits to built-in materials will be backed up. Custom materials stay available.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("restore", "Restore")
        dialog.set_close_response("cancel")
        dialog.set_default_response("cancel")

        def restore(_dialog, response):
            if response != "restore":
                return
            active = self._text(self.r_preset)
            is_override = presets.removal_action(active, self._text(self.r_cutter)) == "restore"
            try:
                count, directory = presets.restore_default_materials()
            except OSError as exc:
                self.toast(f"Could not restore defaults. {exc}")
                return
            if count:
                self.refresh_preset_choices()
                self._load_materials()
                if self._text(self.r_preset) != active or is_override:
                    self.on_preset()
                self.toast(f"Restored {count} material presets. Backup at {directory}.")
            else:
                self.toast("Default materials are already restored.")

        dialog.connect("response", restore)
        dialog.present(self)

    # ---- calibration and test cuts -----------------------------------
    def on_save_calibration(self, *_a):
        dx = round(self.r_cal_x.get_value(), 3)
        dy = round(self.r_cal_y.get_value(), 3)
        config.save_cut_offset(dx, dy)
        self.toast(f"Calibration saved: {dx:+g}, {dy:+g} mm")
        self.rebuild()

    @staticmethod
    def guide_button(group, label, handler):
        button = Gtk.Button(label=label, halign=Gtk.Align.START)
        button.connect("clicked", handler)
        group.add(button)
        return button

    @staticmethod
    def guide_buttons(group, *actions):
        row = Gtk.Box(spacing=6, homogeneous=True, margin_top=6)
        buttons = []
        for label, handler in actions:
            button = Gtk.Button(label=label)
            button.connect("clicked", handler)
            row.append(button)
            buttons.append(button)
        group.add(row)
        return buttons

    def on_guide_spot_selected(self, row, _param=None):
        spot = int(row.get_value())
        if self.guide.spot == spot or self._sending:
            return
        try:
            self.guide.select_spot(spot)
        except ValueError as error:
            self.toast(str(error))
            row.set_value(self.guide.spot)
            return
        self.persist_guide()
        self.update_guide()
        self.update_tuning_preview()

    def on_preview_spot_clicked(self, x, y):
        if not self.tuning or self._sending:
            return False
        spot = self.guide.spot_for_point(x, y)
        if spot is not None:
            try:
                self.guide.select_spot(spot)
            except ValueError as error:
                self.toast(str(error))
                return False
            self.persist_guide()
            self.update_guide()
            self.update_tuning_preview()
            return True
        return False

    def update_guide(self):
        busy = self._sending
        for field in (self.guide_apply, self.guide_next, self.guide_reset, self.guide_slot_choice):
            field.set_sensitive(not busy)
        state = self.guide
        if self.tuning:
            self.sync_guide_slot()
        self.guide_next.set_sensitive(not busy and (
            state.placement is None or any(number != state.spot for number in state.fresh_spots())))
        used = state.current_spot
        shape = state.trial.sheet.shape if state.trial else state.shape
        size = used.rect.w if used and used.rect.w in starting_guide.SAMPLE_SIZES else state.size_mm
        for field, handler, selection in (
                (self.guide_shape, self.guide_shape_id, tuple(testcut.TEST_SHAPES).index(shape)),
                (self.guide_size, self.guide_size_id, tuple(starting_guide.SAMPLE_SIZES).index(size))):
            with field.handler_block(handler):
                field.set_selected(selection)
            field.set_sensitive(not busy)
        connected = starting_guide.has_cutter_endpoint(self._connection())
        self.guide_send.set_sensitive(not busy and not self._checking_status and self.wireless_ready() and connected and state.can_cut)
        self.guide_use.set_sensitive(not busy and state.tested_settings is not None)
        self.guide_registration.set_sensitive(not busy and not (state.spots or state.sheet_exported))
        self.guide_marked_sheet.set_sensitive(not busy and state.registered)
        if self.tuning:
            status = f"Spot {state.spot} · Cut Settings {self.guide_slot} · {state.sheet_status}"
            if busy:
                status = "Waiting for the cutter to finish…"
            elif not connected:
                status = "Exit Material Test and select a cutter."
            self.send_status.set_label(status)
            self.send_status.set_tooltip_text(status)
            self.send_status.remove_css_class("warning")
        label = state.pass_label(self.options())
        self.guide_send.set_label(label)
        differ = state.completed > 0 and state.settings_differ(self.options())
        title, _, body = state.summary(differ).partition("\n")
        self.guide_status.set_title(title)
        if not connected:
            body = "\n".join(filter(None, (body, "Go back to Send and select a cutter to run a test pass.")))
        if state.resume_error:
            body = "\n".join(filter(None, (body, state.resume_error)))
        self.guide_status.set_subtitle("Waiting for the cutter to finish…" if self._sending else body)
        if hasattr(self, "guide_spot_selector"):
            self.guide_spot_selector.set_sensitive(not busy)
            self.guide_spot_selector.get_adjustment().set_upper(max(self.guide.max_spots, self.guide.spot))
            if int(self.guide_spot_selector.get_value()) != self.guide.spot:
                self.guide_spot_selector.set_value(self.guide.spot)
        self.guide_history.set_subtitle(state.history_text() or "Completed passes appear here.")
        self.guide_outcome.set_sensitive(not busy)
        self.guide_outcome.set_selected(tuple(starting_guide.OUTCOMES).index(state.outcome))
        advice = state.advice()
        self.guide_advice.set_subtitle(advice)
        self.guide_advice.set_visible(bool(advice))

    def on_guide_outcome(self, row, *_args):
        self.guide.inspect(tuple(starting_guide.OUTCOMES)[row.get_selected()])
        self.update_guide()

    def on_guide_sample(self, *_args):
        if self._sending:
            return
        shape = tuple(testcut.TEST_SHAPES)[self.guide_shape.get_selected()]
        size = tuple(starting_guide.SAMPLE_SIZES)[self.guide_size.get_selected()]
        try:
            self.guide.configure_sample(shape, size)
        except ValueError as error:
            self.toast(str(error))
        else:
            self.persist_guide()
        self.update_guide()
        self.update_tuning_preview()

    def set_guide_blade_values(self, values):
        fields = {"force": self.r_force, "speed": self.r_speed, "depth": self.r_depth, "passes": self.r_passes}
        previous, self._suppress = self._suppress, True
        try:
            for name, value in values.items():
                fields[name].set_value(-1 if value is None else value)
        finally:
            self._suppress = previous
        self.on_changed()

    def on_guide_start(self, *_args):
        device = graphtec.device_by_name(self._text(self.r_cutter))
        opts = self._blade_options()
        self.set_guide_blade_values(starting_guide.starting_values(device, opts["holder"], opts["blade_diameter_mm"]))
        suffix = "" if self.r_depth.get_value() >= 0 else " Set depth on the blade."
        self.toast("Starting settings applied." + suffix)

    def on_guide_use(self, *_args):
        settings = self.guide.tested_settings
        if self._sending or settings is None:
            return
        device = graphtec.device_by_name(self._text(self.r_cutter))
        if device != self.guide.trial.sheet.device or settings.holder != self._blade_options()["holder"]:
            self.toast("Select the tested cutter and tool holder first.")
            return
        previous, self._suppress = self._suppress, True
        try:
            self._load_blade_settings(settings)
        finally:
            self._suppress = previous
        self.on_changed()
        self.offer_tested_preset(settings, device.name)

    def offer_tested_preset(self, settings, cutter, name="", error=None):
        dialog = Adw.AlertDialog(
            heading="Save Material Preset?",
            body=error or "Enter the material name to save the tested settings for future cuts.")
        entry = Gtk.Entry(text=name, placeholder_text="Material name", activates_default=True)
        dialog.set_extra_child(entry)
        dialog.add_response("skip", "Use Without Saving")
        dialog.add_response("save", "Save Preset")
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("save")
        dialog.set_close_response("skip")

        def save(_dialog, response):
            if response != "save":
                return
            name = entry.get_text().strip()
            try:
                presets.save_tested_settings(name, settings, cutter)
            except (OSError, ValueError) as problem:
                self.offer_tested_preset(settings, cutter, name, str(problem))
            else:
                self.refresh_preset_choices(name)
                self.on_preset()
                self.toast(f"Saved {name}.")

        dialog.connect("response", save)
        dialog.present(self)

    def on_guide_next(self, *_args):
        if self._sending:
            return
        try:
            self.guide.next_spot()
        except ValueError as error:
            self.toast(str(error))
            return
        self.persist_guide()
        self.update_guide()
        self.update_tuning_preview()

    def on_guide_reset(self, *_args):
        dialog = Adw.AlertDialog(heading="Start a New Test Sheet?",
                                 body="Load a fresh test sheet to start at the first spot.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("new", "New Sheet")
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def reset(_dialog, response):
            if response == "new" and not (self._sending):
                self.guide.reset()
                self.guide.placement = self.guide.make_placement(self.job, self.options())
                self.persist_guide()
                self.update_guide()
                self.update_tuning_preview()

        dialog.connect("response", reset)
        dialog.present(self)

    def persist_guide(self):
        try:
            self.guide.persist()
        except OSError as error:
            self.toast(f"Could not save the marked test sheet history. {error}")
            return False
        return True

    def change_guide_registration(self, row, *_args):
        self.guide.registered = row.get_active()
        if self.guide.placement is not None and not self.guide.spots:
            from dataclasses import replace
            self.guide.placement = replace(self.guide.placement, registered=self.guide.registered)
        self.update_guide()
        self.update_tuning_preview()

    def save_guide_marked_sheet(self, *_args):
        dialog = Gtk.FileDialog(title="Save Marked Test Sheet", initial_name="material-test.svg")

        def chosen(chooser, result):
            try:
                path = chooser.save_finish(result).get_path()
                if path is None:
                    return
                self.guide.write_marked_sheet(path, self.job, self.options())
            except GLib.Error:
                return
            except (OSError, ValueError, KeyError) as error:
                self.toast(str(error))
            else:
                self.toast("Saved the marked test sheet. Print at actual size.")
            self.update_guide()
            self.update_tuning_preview()

        dialog.save(self, None, chosen)

    def guide_proposal(self):
        return self.guide.prepare(self._connection(), self.options(), self.job)

    def on_guide_pass(self, *_args):
        if self._sending or self._checking_status or not self.guide.can_cut or not self.wireless_ready():
            return
        try:
            trial = self.guide_proposal()
        except (ValueError, KeyError) as error:
            self.toast(str(error))
            return
        if self.guide.is_repeat(trial):
            self._guide_pass_response(None, "cut", trial)
            return
        dialog = Adw.AlertDialog(heading=self.guide.confirm_title(trial), body=self.guide.prompt(trial))
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("cut", "Cut")
        dialog.set_response_appearance("cut", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._guide_pass_response, trial)
        dialog.present(self)

    def _guide_pass_response(self, _dialog, response, trial):
        if response != "cut" or self._sending or self._checking_status or not self.guide.can_cut or not self.wireless_ready():
            return
        try:
            current = self.guide_proposal()
        except (ValueError, KeyError) as error:
            self.toast(str(error))
            return
        if trial != current:
            self.toast("Test settings or cutter connection changed. Review the pass again.")
            return
        refusal = cutters.usb_refusal(trial.connection, trial.sheet.device, self.selected_cutter())
        if refusal:
            self.toast(refusal)
            return
        if trial.placement.registered:
            try:
                self.guide.persist(pending=trial)
            except OSError as error:
                self.toast(f"Could not save the marked test sheet history. {error}")
                return
        self._sending = True
        self.update_send_actions()

        name = self.guide_pass_name

        def done(error=None):
            self.guide.record(trial, error, name)
            self._send_done()
            if self.persist_guide():
                self.toast(str(error) if error else "Pass complete.")

        def work():
            try:
                with cutters.open_link(trial.connection, trial.sheet.device) as cutter:
                    starting_guide.run_pass(cutter, trial)
            except Exception as error:
                GLib.idle_add(done, error)
            else:
                GLib.idle_add(done)

        threading.Thread(target=work, daemon=True).start()

    def on_test_cut(self, *_a):
        connection = self._connection()
        if connection[0] in bluetooth.BLUETOOTH_MODES and not connection[1]:
            self.toast("Select a Bluetooth cutter first.")
            return
        device = graphtec.device_by_name(self._text(self.r_cutter))
        opts = self.options()
        base = graphtec.CutSettings(force=opts["force"], speed=opts["speed"],
                                    passes=opts["passes"], depth=opts["depth"],
                                    **opts["blade_options"])
        try:
            ts = testcut.build(
                device, base,
                forces=self.r_test_force.get_text(),
                passes=self.r_test_passes.get_text(),
                sheet_w_mm=self.job.grid.sheet_w_mm if self.job else 215.9,
                sheet_h_mm=self.job.grid.sheet_h_mm if self.job else 279.4,
                offset_mm=opts["offset_mm"],
                shape=list(testcut.TEST_SHAPES)[self.r_test_shape.get_selected()],
                mat=opts["mat"], end_position=opts["end_position"],
                end_feed_mm=opts["end_feed_mm"])
        except ValueError as exc:
            self.toast(str(exc))
            return
        rows = "\n".join(f"{label}  {desc}" for label, desc in ts.map_rows())
        dialog = Adw.AlertDialog(
            heading=f"Cut {len(ts.cells)} test samples?",
            body=f"{testcut.TEST_SHAPES[ts.shape]} · Load a blank offcut. Registration is off.\n\n" + rows)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("cut", "Cut")
        dialog.set_response_appearance("cut",
                                       Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.connect("response", self._test_cut_response, ts, connection)
        dialog.present(self)

    def _test_cut_response(self, _dialog, response, ts, connection):
        if response != "cut":
            return
        if self._sending or self._checking_status or not self.wireless_ready():
            self.toast("The cutter connection is busy.")
            return
        self.guide.moved()
        refusal = cutters.usb_refusal(connection, ts.device, self.selected_cutter())
        if refusal:
            self.toast(refusal)
            return
        self._sending = True
        self.update_send_actions()

        def work():
            try:
                with cutters.open_link(connection, ts.device) as tr:
                    if tr.can_read:
                        state = tr.status()
                        if state != "ready":
                            raise transport.TransportError(f"The cutter is not ready ({state}).")
                    sent = tr.run(ts.steps)
                GLib.idle_add(self.toast, f"Test cut sent: {sent:,} bytes")
            except Exception as exc:              # noqa: BLE001
                GLib.idle_add(self.toast, f"Send failed: {exc}")
            finally:
                GLib.idle_add(self._send_done)

        threading.Thread(target=work, daemon=True).start()

    def selected_cutter(self):
        return list(cutters.MODELS)[self.r_send_cutter.get_selected()]

    def on_send_cutter(self, *_args):
        model = self.selected_cutter()
        if model == cutters.AUTO:
            self.on_bluetooth_selected()
        else:
            for index, device in enumerate(graphtec.DEVICES):
                if device.name == model:
                    self.r_cutter.set_selected(index)
                    break
        self.update_send_actions()

    def refresh_usb(self):
        if self._sending or self._connection_mode() != "auto":
            return False
        self._refresh_cutter_list()
        self._update_scan_ui()
        if self._search_pending and not self._wireless_scanning and not self._checking_status:
            self._search_pending = False
            self.refresh_wireless(search=True)
        return False

    def _refresh_cutter_list(self):
        devices = {**cutters.usb_choices(), **self._wireless_choices}
        if cutters.wireless_endpoint(self.preferred_endpoint) and self.preferred_endpoint not in devices:
            self.preferred_endpoint = self.verified_wireless = None
        if devices != self._usb_devices:
            preferred = self.preferred_endpoint
            if cutters.wireless_endpoint(preferred) and self.verified_wireless != preferred:
                selected = None
            else:
                selected = cutters.detected_choice(devices, preferred)
            same_cutter = (selected is not None and selected == self._automatic_endpoint() and
                           devices[selected] == self._usb_devices.get(selected))
            self._usb_devices = devices
            with self.r_bluetooth.handler_block(self.endpoint_changed_id):
                self.r_bluetooth.set_model(Gtk.StringList.new(["Select a cutter", *devices.values()]))
                self.r_bluetooth.set_selected(list(devices).index(selected) + 1 if selected else 0)
            if same_cutter:
                return
            self.on_bluetooth_selected()
            if selected is None:   # otherwise on_bluetooth_selected is reading its status
                self.connection_status.set_subtitle("Select a cutter." if devices else cutters.NO_CUTTER_TEXT)

    def refresh_wireless(self, search=False):
        """Refresh the wireless list; a search also listens for unpaired
        Classic cutters (13 s), and is only run when asked for."""
        if search and (self._wireless_scanning or self._sending or self._checking_status):
            self._search_pending = True   # run it once the window is free
            self._update_scan_ui()
            return False
        if self._sending or self._wireless_scanning:
            return False
        if self._connection_mode() != "auto":
            if not search and self._connection() == self.verified_wireless:
                self.on_refresh_status()
            return False
        self._wireless_scanning = True
        self._update_scan_ui()

        def work():
            try:
                choices = cutters.wireless_choices(search=search)
            except transport.TransportError:
                choices = {}
            GLib.idle_add(self._wireless_done, choices)

        threading.Thread(target=work, daemon=True).start()
        return False

    def _wireless_done(self, choices):
        self._wireless_scanning = False
        selected = self._automatic_endpoint()
        last_label = self._wireless_choices.get(selected)
        self._wireless_choices = dict(choices)
        if last_label and cutters.wireless_endpoint(selected):
            self._wireless_choices.setdefault(selected, last_label)
        self.refresh_usb()
        self._update_scan_ui()
        if cutters.wireless_endpoint(self._automatic_endpoint()):
            self.on_refresh_status()
        return False

    def on_scan_cutters(self, *_args):
        if self._connection_mode() == "auto":
            self.refresh_usb()
            self.refresh_wireless(search=True)
        else:
            self._scan_bluetooth(self.scan_cutters_button)

    def _update_scan_ui(self):
        """With no cutter available, offer a scan in the status row and say
        what is happening; once there is one, the header actions suffice."""
        if self._connection_mode() == "auto":
            none, scanning = not self._usb_devices, self._wireless_scanning or self._search_pending
        else:
            none = not self._bluetooth_devices and not self.r_bluetooth_id.get_text().strip()
            scanning = self._manual_scanning
        self.scan_cutters_button.set_visible(none)
        self.scan_cutters_button.set_sensitive(not scanning and not self._sending)
        self.refresh_cutters_button.set_visible(not none)
        if none:
            self.connection_status.set_subtitle(cutters.SCANNING_TEXT if scanning else cutters.NO_CUTTER_TEXT)

    def _automatic_endpoint(self):
        index = self.r_bluetooth.get_selected() - 1
        return list(self._usb_devices)[index] if 0 <= index < len(self._usb_devices) else None

    def _connection_mode(self):
        return ("auto", *bluetooth.BLUETOOTH_MODES)[self.r_connection.get_selected()]

    def on_connection_changed(self, *_args):
        mode = self._connection_mode()
        self.preferred_endpoint = None
        self.verified_wireless = None
        self._bluetooth_devices = []
        with self.r_bluetooth.handler_block(self.endpoint_changed_id):
            self.r_bluetooth.set_model(Gtk.StringList.new(["Select a cutter"]))
            self.r_bluetooth.set_selected(0)
        self.r_bluetooth_id.set_text("")
        self._usb_devices = {}
        self.r_bluetooth.set_visible(True)
        if mode == "auto":
            GLib.idle_add(self.refresh_usb)
        self.r_bluetooth_id.set_visible(mode != "auto")
        self.r_bluetooth_channel.set_visible(mode == "bluetooth")
        self.on_connection_target_changed()

    def on_connection_target_changed(self, *_args):
        if self.tools_connection is not None and self.tools_connection != self._connection():
            self.detected_tools = ()
            self.tools_connection = self.corrected_tools = None
        changed = self.update_holder_labels()
        self.connection_status.set_subtitle("Not checked")
        self.scan_result.set_visible(False)
        self._update_scan_ui()
        if changed:
            self.on_changed()
        else:
            self.update_send_actions()

    def _scan_bluetooth(self, button):
        mode = self._connection_mode()
        if mode == "auto":
            self.refresh_usb()
            self.refresh_wireless(search=True)
            return

        def update(devices, error):
            button.set_sensitive(True)
            self._manual_scanning = False
            if mode != self._connection_mode():
                self._update_scan_ui()
                return
            if error:
                self.toast(error)
                return
            self._bluetooth_devices = devices
            self.r_bluetooth.set_model(Gtk.StringList.new(
                ["Select a cutter"] + [f"{name or 'Unnamed'} ({address})"
                                       for address, name in devices]))
            self.r_bluetooth.set_selected(0)
            if not devices:
                self.toast(f"No Silhouette cutter found over {bluetooth.BLUETOOTH_MODES[mode]}.")
            self._update_scan_ui()

        def work():
            try:
                GLib.idle_add(update, cutters.discover_desktop(mode), None)
            except transport.TransportError as exc:
                GLib.idle_add(update, [], str(exc))

        button.set_sensitive(False)
        self._manual_scanning = True
        self._update_scan_ui()
        threading.Thread(target=work, daemon=True).start()

    def select_detected(self, *_args):
        if self._checking_status:
            return
        if self._connection_mode() == "auto":
            endpoint = self._automatic_endpoint()
            if endpoint == self.preferred_endpoint:
                self.update_send_actions()
                return
            self.preferred_endpoint = endpoint
            if cutters.wireless_endpoint(endpoint):
                with self.r_bluetooth.handler_block(self.endpoint_changed_id):
                    self.r_bluetooth.set_selected(0)
                self.on_connection_target_changed()
                pending = "Pairing with cutter…" if cutters.unpaired(endpoint) else "Connecting…"
                self.on_refresh_status(pending=pending, endpoint=endpoint)
                return
        else:
            index = self.r_bluetooth.get_selected() - 1
            if 0 <= index < len(self._bluetooth_devices):
                mode = self._connection_mode()
                identifier = self._bluetooth_devices[index][0]
                old = self._connection()
                if old == self.verified_wireless and old[1] == identifier:
                    return
                with self.r_bluetooth.handler_block(self.endpoint_changed_id):
                    self.r_bluetooth.set_selected(0)
                self.r_bluetooth_id.set_text("")
                self.on_refresh_status(pending="Connecting…", endpoint=(mode, identifier),
                                       target=(mode, identifier, *old[2:]))
                return
        self.on_bluetooth_selected()

    def on_bluetooth_selected(self, *_args):
        index = self.r_bluetooth.get_selected() - 1
        if self._connection_mode() == "auto":
            endpoint = self._automatic_endpoint()
            model = cutters.auto_model(endpoint, self._usb_devices.get(endpoint, ""))
            if model and self.selected_cutter() == cutters.AUTO:
                self.r_cutter.set_selected(graphtec.DEVICES.index(model))
            self.on_connection_target_changed()
            if endpoint:
                # As in Studio, choosing a cutter reads its status, so a paired
                # cutter that is switched off is never shown as available; one a
                # search found is paired first.
                name = self._usb_devices.get(endpoint, "").split(" · ")[0]
                self.on_refresh_status(pending=f"Pairing with {name}…" if cutters.unpaired(endpoint)
                                       else "Checking…")
            return
        if 0 <= index < len(self._bluetooth_devices):
            identifier, name = self._bluetooth_devices[index]
            self.r_bluetooth_id.set_text(identifier)
            model = bluetooth.advertised_model(name)
            if model and self.selected_cutter() == cutters.AUTO:
                self.r_cutter.set_selected(graphtec.DEVICES.index(model))
            self.detect_tools()

    def _connection(self):
        mode = self._connection_mode()
        if mode == "auto":
            endpoint = self._automatic_endpoint()
            if endpoint and endpoint[0] in bluetooth.BLUETOOTH_MODES:
                return endpoint[0], endpoint[1]
            return mode, endpoint
        identifier = self.r_bluetooth_id.get_text().strip() or None
        if mode == "bluetooth":
            return mode, identifier, int(self.r_bluetooth_channel.get_value())
        return mode, identifier

    def update_holder_labels(self):
        buttons = getattr(self, "send_holder_buttons", None)
        if not buttons:
            return False
        device = graphtec.device_by_name(self._text(self.r_cutter))
        changed = self.tool_selection.bind(self._connection(), device)
        holder = int(self.r_holder.get_value()) if device.two_tools else 1
        choices = tooling.tool_choices(device)
        with self.r_tool_selection.handler_block(self.tool_picker_id):
            if tuple(choices) != self.tool_picker_choices:
                self.tool_picker_choices = tuple(choices)
                self.r_tool_selection.set_model(Gtk.StringList.new(list(choices.values())))
            self.r_tool_selection.set_selected(self.tool_picker_choices.index(self.tool_selection.selected(holder)))
        labels = tooling.holder_labels(self.detected_tools, device, self.tool_selection)
        for slot, button in buttons.items():
            label = labels[slot - 1] if slot <= len(labels) else f"Tool Holder {slot}"
            button.set_label(label.replace(" · ", "\n", 1))
            button.get_child().set_justify(Gtk.Justification.CENTER)
            button.get_child().set_wrap(True)
            button.update_property([Gtk.AccessibleProperty.LABEL], [label])
            button.set_tooltip_text(f"{label} (manually selected)" if self.tool_selection.selected(slot) else label)
        return changed

    def on_tool_selection(self, *_args):
        if self._suppress or self._sending or self._checking_status or self.active_job is not None:
            return
        if not self._connection()[1]:
            return
        device = graphtec.device_by_name(self._text(self.r_cutter))
        holder = int(self.r_holder.get_value()) if device.two_tools else 1
        choice = self.tool_picker_choices[self.r_tool_selection.get_selected()]
        self.tool_selection.select(holder, choice)
        self.on_changed()

    def apply_tool_detection(self, codes):
        """Take a holder reply, label the tabs, and send depth back to manual once."""
        self.detected_tools = tuple(codes)
        self.tools_connection = self._connection()
        self.update_holder_labels()
        self.correct_detected_depth()

    def correct_detected_depth(self):
        if self.active_job is not None or self._sending:
            return
        device = graphtec.device_by_name(self._text(self.r_cutter))
        if device.two_tools and self.r_holder.get_value() != 1:
            return
        if self.tool_selection.selected(1):
            if self.tool_selection.manual_depth:
                was, self._suppress = self._suppress, True
                try:
                    self.r_depth.set_value(tooling.MANUAL_DEPTH)
                finally:
                    self._suppress = was
            return
        if not self.detected_tools or self.corrected_tools == self.detected_tools:
            return
        self.corrected_tools = self.detected_tools
        depth, reason = tooling.depth_correction(self.detected_tools, int(self.r_depth.get_value()))
        if depth is None:
            return
        self.r_depth.set_value(depth)
        self.toast(reason)

    def detect_tools(self):
        """Read the installed tools once for this connection, in the background."""
        connection = self._connection()
        device = graphtec.device_by_name(self._text(self.r_cutter))
        if (self._sending or self._checking_status or self.active_job is not None or not connection[1]
                or not device.detects_tools or self.tools_connection == connection):
            return

        self._checking_status = True
        self.update_send_actions()

        def done(codes):
            self._checking_status = False
            if connection == self._connection():
                if codes is None:
                    self.detected_tools = ()
                    self.tools_connection = connection
                    self.update_holder_labels()
                else:
                    self.apply_tool_detection(codes)
            else:
                self.detect_tools()
            self.update_send_actions()

        def work():
            try:
                with cutters.open_link(connection) as cutter:
                    codes = cutter.tool_codes()
            except Exception:                     # noqa: BLE001
                codes = None
            GLib.idle_add(done, codes)

        threading.Thread(target=work, daemon=True).start()

    def forget_tool_detection(self):
        self.detected_tools = ()
        self.tools_connection = None
        self.corrected_tools = None
        self.update_holder_labels()

    def on_refresh_status(self, *_a, pending="Checking…", endpoint=None, target=None):
        if self._sending or self._checking_status or self.active_job is not None:
            return
        original = self._connection()
        connection = target or endpoint or original
        automatic = self._connection_mode() == "auto"
        mode = connection[0]
        if not connection[1]:
            self.connection_status.set_subtitle("Select a cutter.")
            return
        self._checking_status = True
        self.update_send_actions()
        self.connection_status.set_subtitle(pending)

        def done(text, device, codes):
            self._checking_status = False
            self.update_send_actions()
            if original != self._connection():
                return
            if mode in bluetooth.BLUETOOTH_MODES:
                self.verified_wireless = connection if device is not None else None
                wireless = endpoint or self._automatic_endpoint()
                if device is None and automatic and cutters.wireless_endpoint(wireless):
                    self._wireless_choices.pop(wireless, None)
                    self.preferred_endpoint = None
                    self._refresh_cutter_list()
                    self._update_scan_ui()
                elif not automatic:
                    if device is None:
                        filtered = [(address, name) for address, name in self._bluetooth_devices
                                    if address != connection[1]]
                        if filtered != self._bluetooth_devices:
                            self._bluetooth_devices = filtered
                            with self.r_bluetooth.handler_block(self.endpoint_changed_id):
                                self.r_bluetooth.set_model(Gtk.StringList.new(["Select a cutter"] + [
                                    f"{name or 'Unnamed'} ({address})" for address, name in filtered]))
                            if self.r_bluetooth_id.get_text().strip() == connection[1]:
                                self.r_bluetooth_id.set_text("")
                    elif endpoint:
                        self.r_bluetooth_id.set_text(endpoint[1])
                        position = next((i for i, (address, _) in enumerate(self._bluetooth_devices, 1)
                                         if address == endpoint[1]), 0)
                        with self.r_bluetooth.handler_block(self.endpoint_changed_id):
                            self.r_bluetooth.set_selected(position)
                elif endpoint and endpoint in self._usb_devices:
                    with self.r_bluetooth.handler_block(self.endpoint_changed_id):
                        self.r_bluetooth.set_selected(list(self._usb_devices).index(endpoint) + 1)
                    self.on_connection_target_changed()
            self.connection_status.set_subtitle(text)
            if device is not None and self.selected_cutter() == cutters.AUTO:
                self.r_cutter.set_selected(graphtec.DEVICES.index(device))
            if codes is not None:
                self.apply_tool_detection(codes)
            self.update_send_actions()

        def work():
            device = None
            codes = None
            try:
                with cutters.open_link(connection) as cutter:
                    device = cutter.device
                    status, codes = cutter.status_and_tools()
                    text = f"{device.name} · {status}"
                    if mode in bluetooth.BLUETOOTH_MODES:
                        text += f" · {cutter.firmware_version()}"
            except transport.NotFound:
                device = None
                text = cutters.NOT_DETECTED_TEXT
            except Exception as exc:
                device = None
                text = f"Could not read cutter status. {exc}"
            GLib.idle_add(done, text, device, codes)

        threading.Thread(target=work, daemon=True).start()

    def wireless_ready(self):
        connection = self._connection()
        return not cutters.wireless_refusal(connection, self.verified_wireless)

    def on_cut(self, *_a, scan_only=False, corner=False):
        if design_review_pending(self):
            self.toast('The design has edits Send has not used. Open Design, then switch to Send.')
            return
        pdf_path = getattr(self, "pdf_path", None)
        pdf_bundle = getattr(self, "pdf_bundle", None)
        pdf_state = getattr(self, "pdf_check_state", sheet_mod.RegistrationDecision.NONE)
        if pdf_bundle and not getattr(self, "bundle_checked", False):
            if pdf_state == sheet_mod.RegistrationDecision.FAILED:
                self.toast('Registration check failed. Open Sheet to see the missing marks.')
            else:
                self.toast('Check the matched PDF before cutting this sheet.')
            return
        if self.job and self.job.registration_steps() and pdf_path is not None:
            allowed, pdf_reason = sheet_mod.RegistrationDecision.evaluate(
                pdf_state, has_pdf=True, registration_on=True)
            if not allowed:
                self.toast(pdf_reason)
                return
        j = self.job
        refusal = cut_refusal(j, j, self._sending or self._checking_status) or (
            "" if self.wireless_ready() else "Check the cutter connection first.")
        if refusal:
            self.toast(refusal)
            return
        if not scan_only and j.settings.passes <= 0:
            self.toast("Set at least one pass to send cut paths.")
            return
        connection = self._connection()
        if connection[0] in bluetooth.BLUETOOTH_MODES and not connection[1]:
            self.toast("Select a Bluetooth cutter first.")
            return
        refusal = cutters.usb_refusal(connection, j.device, self.selected_cutter())
        if refusal:
            self.toast(refusal)
            return
        if corner:
            try:
                sample = corner_test.build(j, self.imported, self.template_shapes, self.r_bleed.get_value())
            except ValueError as error:
                self.toast(str(error))
                return
            dialog = Adw.AlertDialog(heading="Cut a corner test?", body=sample.describe())
            dialog.add_response("cancel", "Cancel")
            dialog.add_response("cut", "Cut Corner Test")
            dialog.set_response_appearance("cut", Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.set_default_response("cancel")
            dialog.set_close_response("cancel")
            dialog.connect("response", self._cut_response, j, connection, False, sample)
            dialog.present(self)
            return
        conf = confirmation.build_send_confirmation(
            j, connection, scan_only=scan_only, bundle=self.pdf_bundle, artwork=self.imported
        )
        dialog = Adw.AlertDialog(heading=conf.title, body=conf.summary)
        exp = Gtk.Expander(label="Details")
        label = Gtk.Label(label=conf.details, wrap=True, xalign=0)
        exp.set_child(label)
        dialog.set_extra_child(exp)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("cut", conf.confirm_label)
        dialog.set_response_appearance(
            "cut",
            Adw.ResponseAppearance.DESTRUCTIVE if conf.destructive else Adw.ResponseAppearance.SUGGESTED,
        )
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._cut_response, j, connection, scan_only)
        dialog.present(self)

    def _cut_response(self, _dialog, response, j, connection=("auto", None), scan_only=False, corner=None):
        if response != "cut":
            return
        if design_review_pending(self):
            self.toast('The design has edits Send has not used. Open Design, then switch to Send.')
            return
        pdf_path = getattr(self, "pdf_path", None)
        pdf_bundle = getattr(self, "pdf_bundle", None)
        pdf_state = getattr(self, "pdf_check_state", sheet_mod.RegistrationDecision.NONE)
        if pdf_bundle and not getattr(self, "bundle_checked", False):
            if pdf_state == sheet_mod.RegistrationDecision.FAILED:
                self.toast('Registration check failed. Open Sheet to see the missing marks.')
            else:
                self.toast('Check the matched PDF before cutting this sheet.')
            return
        if self.job and self.job.registration_steps() and pdf_path is not None:
            allowed, pdf_reason = sheet_mod.RegistrationDecision.evaluate(
                pdf_state, has_pdf=True, registration_on=True)
            if not allowed:
                self.toast(pdf_reason)
                return
        refusal = cut_refusal(self.job, j, self._sending or self._checking_status) or (
            "" if self.wireless_ready() else "Check the cutter connection first.")
        if refusal:
            self.toast(refusal)
            return
        if not scan_only and j.settings.passes <= 0:
            self.toast("Set at least one pass to send cut paths.")
            return
        if connection != self._connection():
            self.toast("The cutter connection changed. Review and confirm the job again.")
            return
        refusal = cutters.usb_refusal(connection, j.device, self.selected_cutter())
        if refusal:
            self.toast(refusal)
            return
        checked = corner.job if corner else j
        steps = checked.registration_steps() if scan_only else checked.steps
        if not steps:
            self.toast("This job does not use registration.")
            return
        self.active_job = checked
        self.lock_job_controls(True)
        self.guide.moved()
        control = self.cut_control = (transport.CutControl(lambda: GLib.idle_add(self.update_send_actions))
                                      if not scan_only and corner is None else None)
        self._sending = True
        self.cut_button.set_sensitive(False)
        self.scan_button.set_sensitive(False)
        self.update_send_actions()
        self.scan_result.set_visible(False)
        matched = self.pdf_bundle
        row = matched.rows[int(self.r_sheet_page.get_value()) - 1] if matched else None
        report = self.sheet_report if matched else None

        def work():
            mode = connection[0]
            try:
                with cutters.open_link(connection, j.device) as t:
                    if t.can_read:
                        state = t.status()
                        if state == "unloaded":
                            raise transport.TransportError(
                                "the cutter says no media is loaded")
                        if mode in bluetooth.BLUETOOTH_MODES and state != "ready":
                            raise transport.TransportError(f"Bluetooth cutter is not ready ({state})")
                        if state == "moving":
                            raise transport.TransportError(
                                "the cutter is busy")
                    started = time.monotonic()
                    if control is not None:
                        sent = t.run(steps, wait_done=True, control=control)
                    else:
                        sent = t.run(steps, wait_done=True) if corner and t.can_read else t.run(steps)
                message = ("Corner test sent." if corner else "Registration scan passed. No cut paths were sent."
                           if scan_only else f"Cut sent: {sent:,} bytes")
                GLib.idle_add(self.toast, message)
            except transport.RegistrationFailed as exc:
                GLib.idle_add(self.show_registration_failure, str(exc), j, matched, row, report,
                              mode, time.monotonic() - started)
            except transport.CutCancelled as exc:
                GLib.idle_add(self.toast, str(exc))
            except Exception as exc:              # noqa: BLE001
                GLib.idle_add(self.toast, f"Send failed: {exc}")
            GLib.idle_add(self._send_done)

        threading.Thread(target=work, daemon=True).start()

    def show_registration_failure(self, message, j, matched, row, report, mode="auto", elapsed=None):
        heading = f'Sheet {row["sheet"]} registration failed' if row else 'Registration failed'
        notes = j.registration_notes(report, matched.near_marks(row) if matched else None)
        if report is None:
            notes.insert(0, "In Sheet, open the print PDF to check each mark by name.")
        if matched:
            notes.append(matched.mat_placement())
        notes.append("Check the printed front at 100% scale. Keep all marks uncovered "
                     "and check for glare at the corners.")
        connection = bluetooth.BLUETOOTH_MODES.get(mode, "USB")
        scan_details = [f"{j.device.name} · {connection}"]
        if row:
            scan_details.append(f"PDF front page {row['frontPage']}")
        if elapsed is not None:
            scan_details.append(f"Scan rejected after {elapsed:.1f} s.")
        scan_details.append("Commands sent before the reply (one write per line)")
        scan_details.extend(j.registration_writes())
        detail = "\n\n".join([message, *notes])
        diagnostics = "\n\n".join([detail, "\n".join(scan_details)])
        self.scan_result.set_title(heading)
        self.scan_result.set_subtitle(diagnostics)
        self.scan_result.set_visible(True)
        dialog = Adw.AlertDialog(heading=heading, body=detail)
        dialog.add_response("close", "Close")
        dialog.add_response("copy", "Copy scan details")
        dialog.add_response("sheet", "Review marks")
        dialog.add_response("troubleshoot", "Troubleshoot…")
        dialog.set_default_response("sheet")
        dialog.set_close_response("close")
        dialog.connect("response::copy", lambda *_: self.get_clipboard().set(diagnostics))
        dialog.connect("response::troubleshoot", lambda *_: self.troubleshoot_cut(REGISTRATION_PROBLEM))
        dialog.connect("response::sheet", lambda *_: self.stack.set_visible_child_name("sheet"))
        dialog.present(self)

    def _send_done(self):
        self.release_job()

    def release_job(self):
        """Return the window to an editable state after a send ends, however it ended."""
        self.cut_control = None
        self.active_job = None
        self._sending = False
        self.lock_job_controls(False)
        self.rebuild()
        self.update_send_actions()


class Application(Adw.Application):

    def __init__(self):
        super().__init__(application_id=APP_ID)
        Gtk.Window.set_default_icon_name(APP_ID)

    def do_activate(self):
        win = self.props.active_window or Window(self)
        win.present()


def main(argv=None):
    app = Application()
    return app.run(argv or [])


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv))
