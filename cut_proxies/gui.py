"""Silhouette Control Center: a GTK4 / libadwaita front end.

Layout, Sheet, Blade and Send share one live preview.

The preview is not decoration.  A cut template is a set of coordinates that
have to agree with ink already on a sheet, and the fastest way to catch a
template that is subtly wrong is to look at it next to the marks it has to
line up with.  So the preview draws what the cutter will actually do: the
sheet, the registration marks at their real size, the exclusion zones around
them, and the flattened cut paths - the same points that go down the wire,
not an idealised redraw.

The Cut button follows the rule the Canon Control Center settled on: it stays
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

from . import (actions, bluetooth, bundle, config, corner_test, geometry, graphtec, job as job_mod, layout_presets, presets, regmarks,
               starting_guide, template, testcut, toolpath, transport)  # noqa: E402
from .desktop_help import CONTROL_HELP, SETUP_STEPS  # noqa: E402
from .gtk_controls import disclosure  # noqa: E402
from .holder_settings import HolderSettings, for_holder  # noqa: E402

APP_ID = "org.printing.SilhouetteControlCenter"
PREVIEW_MODES = ("Cut paths", "Print PDF", "Overlay")

_CUT_SETTINGS_CSS = """
.scc-cut-settings row > box.header { min-height: 34px; padding: 2px 12px; }
.scc-cut-settings spinbutton button { min-width: 16px; min-height: 25px; padding: 0 3px; }
.scc-cut-settings .holder-tabs { padding: 5px 8px; }
.scc-cut-settings .cut-toggles { padding: 4px 12px; }
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

    row.connect("output", display)
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
        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.connect("scroll", self.scroll_zoom)
        self.add_controller(scroll)

    def set_job(self, job):
        before = self.page_size()
        self.job = job
        if self.page_size() != before:
            self.fit()
        self.queue_draw()

    def set_trial(self, trial):
        if trial != self.trial:
            previous = self.page_size()
            self.trial = trial
            if self.page_size() != previous:
                self.fit()
            self.queue_draw()

    def page_size(self):
        if self.trial:
            return self.trial.placement.width, self.trial.placement.height
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
        sample = self.trial.sheet.cells[0].rect
        cr.rectangle(sample.x, sample.y, sample.w, sample.h)
        cr.set_source_rgba(0.18, 0.56, 0.69, 0.17)
        cr.fill_preserve()
        cr.set_source_rgb(0.10, 0.43, 0.53)
        cr.stroke()
        cr.select_font_face("Sans")
        cr.set_font_size(max(3, 11 / scale))
        cr.move_to(sample.x2 + 2, sample.y + 5)
        cr.show_text("Test cut")

    def _draw(self, _area, cr, width, height):
        dark = Adw.StyleManager.get_default().get_dark()
        bg = (0.14, 0.15, 0.17) if dark else (0.89, 0.90, 0.92)
        cr.set_source_rgb(*bg)
        cr.paint()
        if self.trial:
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

        # Amber marks proximity, not a requirement to mask every such card.
        crowded = {h["index"] for h in j.mask_hints}
        skipped = set(j.skip)
        for i, rect in enumerate(j.grid.cards):
            if i in skipped:
                continue
            if pdf is not None:
                continue
            if i in crowded:
                cr.set_source_rgba(0.90, 0.64, 0.23, 0.38)
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
        super().__init__(application=app, title="Silhouette Control Center",
                         default_width=1120, default_height=760)
        self.job = None
        self.sheet_report = None
        self.pdf_path = None
        self.pdf_bundle = None
        self.bundle_checked = False
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
        self.reviewed_design = None
        self._close_confirmed = False
        self._building = False
        self._sending = False
        self._checking_status = False
        self.tuning = False
        self.holder_state = HolderSettings()
        self.guide = starting_guide.Guide()
        self._suppress = True        # don't rebuild while constructing rows

        self.toasts = Adw.ToastOverlay()
        self.set_content(self.toasts)

        outer = Adw.ToolbarView()
        self.workspaces = Gtk.Stack(hhomogeneous=False, vhomogeneous=False)
        self.workspaces.add_named(outer, 'tools')
        self.toasts.set_child(self.workspaces)
        self.connect('close-request', self.confirm_window_close)

        header = Adw.HeaderBar()
        self.stack = Adw.ViewStack()
        switcher = Adw.ViewSwitcher(stack=self.stack,
                                    policy=Adw.ViewSwitcherPolicy.WIDE)
        header.set_title_widget(Adw.WindowTitle(title="Silhouette Control Center"))
        outer.add_top_bar(header)

        setup = Gtk.Button(label="Setup Guide")
        setup.connect("clicked", self.show_setup_guide)
        header.pack_start(setup)

        workspace = Gtk.Box(spacing=4)
        self.design_button = Gtk.Button(label='Design')
        self.design_button.connect('clicked', self.on_edit_template)
        self.design_button.set_tooltip_text("Draw or edit cut paths.")
        workspace.append(self.design_button)
        self.send_workspace = Gtk.Button(label="Send", css_classes=["suggested-action"])
        self.send_workspace.connect("clicked", self.on_review_send)
        self.send_workspace.set_tooltip_text("Review the job and cutter settings.")
        workspace.append(self.send_workspace)
        header.pack_end(workspace)

        self.banner = Adw.Banner(revealed=False)
        outer.add_top_bar(self.banner)

        split = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL,
                          wide_handle=True, position=700,
                          resize_start_child=True, resize_end_child=False,
                          shrink_start_child=False, shrink_end_child=False)
        self.split = split
        settings = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        settings.set_size_request(414, -1)
        settings.append(switcher)
        settings.append(self.stack)
        split.set_end_child(settings)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        right.set_size_request(320, -1)
        right.set_margin_top(12)
        right.set_margin_bottom(12)
        right.set_margin_start(6)
        right.set_margin_end(12)
        self.preview = Preview()
        preview_bar = Gtk.Box(spacing=8)
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
        zoom = Gtk.Box(spacing=4)
        for label, tip, action in (("−", "Zoom out", lambda: self.preview.zoom_by(0.8)),
                                   ("+", "Zoom in", lambda: self.preview.zoom_by(1.25)),
                                   ("Fit", "Show the whole sheet", self.preview.fit)):
            widget = Gtk.Button(label=label, tooltip_text=tip)
            widget.connect("clicked", lambda _button, call=action: call())
            zoom.append(widget)
        self.preview_zoom = Gtk.Label(label="100%", width_chars=5)
        zoom.insert_child_after(self.preview_zoom, zoom.get_first_child())
        self.preview.connect("zoom-changed", lambda _preview, value: self.preview_zoom.set_label(f"{value:.0%}"))
        preview_bar.append(zoom)
        right.append(preview_bar)
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
        self._page_blade()
        self._page_cut()
        self.add_control_help()
        outer.add_bottom_bar(self._send_bar())

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
        if page != "cut":
            self.tuning = False
        self.update_send_actions()
        if page == "blade":
            self.on_refresh_status()

    def _send_bar(self):
        bar = Gtk.ActionBar()
        self.send_status = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=68)
        bar.pack_start(self.send_status)
        self.review_send = Gtk.Button(label="Review Send")
        self.review_send.add_css_class("suggested-action")
        self.review_send.connect("clicked", self.on_review_send)
        bar.pack_end(self.review_send)
        self.cut_button = Gtk.Button(label="Send to Cutter")
        self.cut_button.add_css_class("destructive-action")
        self.cut_button.connect("clicked", self.on_cut)
        bar.pack_end(self.cut_button)
        bar.pack_end(self.corner_button)
        bar.pack_end(self.scan_button)
        self.back_to_send = Gtk.Button(label="Back to Send", css_classes=["suggested-action"])
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
        return "cut", "Review Send"

    def on_review_send(self, *_args):
        if self.tuning:
            self.close_material_guide()
        destination, _ = self.send_review_destination()
        if destination == "design":
            self.on_edit_template()
        else:
            self.stack.set_visible_child_name(destination)

    def update_send_actions(self):
        if not hasattr(self, "send_status"):
            return
        page = self.stack.get_visible_child_name()
        destination, label = self.send_review_destination()
        self.review_send.set_label(label)
        self.review_send.set_visible(not self.tuning and page != destination)
        at_send = not self.tuning and page == "cut" and destination == "cut"
        for control in (self.cut_button, self.scan_button, self.corner_button):
            control.set_visible(at_send)
        self.send_normal.set_visible(not self.tuning)
        self.guide_view.set_visible(self.tuning)
        self.back_to_send.set_visible(self.tuning)
        job = self.job
        mode, identifier, *_ = self._connection()
        reason = (
            "Sending to the cutter…" if self._sending else
            "Checking cutter status…" if self._checking_status else
            "Review the latest edits in Design." if destination == "design" else
            "This ZIP has a legacy cut file. Save it from Sheet." if self.pdf_bundle and self.imported is None else
            "Check the matching PDF in Sheet." if destination == "sheet" else
            "Check this layout before sending." if job is None or job.blocked else
            "Select a Bluetooth cutter." if mode in bluetooth.BLUETOOTH_MODES and not identifier else
            ""
        )
        if not reason:
            reason = cutters.usb_refusal(self._connection(), job.device, self.selected_cutter())
        self.scan_button.set_sensitive(not reason and bool(job.registration_steps()))
        if not reason and job.settings.passes <= 0:
            reason = "Set at least one pass to send cut paths."
        self.cut_button.set_sensitive(not reason)
        self.corner_button.set_sensitive(not reason)
        status = ("Blank test stock · Registration off for samples" if self.tuning else reason or
                  f"{len(job.paths)} paths · {job.device.name} · {'Registration on' if job.registration else 'Registration off'}")
        self.send_status.set_label(status)
        self.send_status.set_tooltip_text(status)
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

    def open_material_guide(self, *_args):
        self.stack.set_visible_child_name("cut")
        self.tuning = True
        self.update_send_actions()
        GLib.idle_add(self.scroll_to_guide)

    def scroll_to_guide(self):
        if self.tuning and self.send_page.get_mapped():
            placed, bounds = self.guide_group.compute_bounds(self.send_page.controls)
            if placed:
                self.send_page.get_vadjustment().set_value(max(0, bounds.get_y() - 12))
        return GLib.SOURCE_REMOVE

    def open_advanced_blade(self, *_args):
        self.stack.set_visible_child_name("blade")
        self.blade_advanced.set_expanded(True)

    def close_material_guide(self, *_args):
        self.tuning = False
        self.send_page.get_vadjustment().set_value(0)
        self.update_send_actions()

    def adjust_guide_settings(self, *_args):
        if self.guide.trial:
            self.on_guide_next()
        self.stack.set_visible_child_name("blade")

    def update_tuning_preview(self):
        trial = None
        if self.tuning:
            trial = self.guide.trial
            if trial is None:
                try:
                    trial = self.guide_proposal()
                except (ValueError, KeyError):
                    pass
        self.preview.set_trial(trial)
        self.preview_title.set_label("Material test sheet" if trial else "Sheet preview")
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
            'Layout preset', ['Custom'] + [p.name for p in layout_presets.BUILTIN.values()])
        self.r_layout_preset.connect('notify::selected', self.on_layout_preset)
        group.add(self.r_layout_preset)
        page.add(group)

        gsrc = Adw.PreferencesGroup(
            title="What to cut",
            description="Use a card layout or open your cut file.")
        self.import_row = Adw.ActionRow(
            title="Generated layout",
            subtitle="Using the sheet and card settings below")
        self.import_row.set_subtitle_lines(0)
        open_btn = Gtk.Button(label="Open Cut Artwork…")
        open_btn.set_tooltip_text("Open an SVG, DXF, or Studio3 file.")
        open_btn.set_valign(Gtk.Align.CENTER)
        open_btn.connect("clicked", self.on_open_artwork)
        clear_btn = Gtk.Button(label="Clear")
        clear_btn.set_valign(Gtk.Align.CENTER)
        clear_btn.connect("clicked", self.on_clear_artwork)
        self.import_row.add_suffix(open_btn)
        self.import_row.add_suffix(clear_btn)
        gsrc.add(self.import_row)
        self.r_colour = _combo("Line color", ["all colours"], subtitle="Cut only lines of this color.")
        self.r_colour.connect("notify::selected", self.on_changed)
        page.add(gsrc)

        g = Adw.PreferencesGroup(title="Sheet and cards")
        self.r_engine = _combo(
            "Layout engine",
            ["Silhouette Card Maker", "Proxy Press"],
            subtitle="Choose the app that laid out your cards.")
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

        self.stack.add_titled_with_icon(page, "layout", "Layout",
                                        "view-grid-symbolic")

    def _page_sheet(self):
        page = ControlPage()
        g = Adw.PreferencesGroup(title="Printed sheet", description="Open your print file and check its marks.")
        self.sheet_row = Adw.ActionRow(title="No PDF loaded",
                                       subtitle="Pick the sheet you printed")
        self.sheet_row.set_subtitle_lines(0)
        pick = Gtk.Button(label="Open PDF or ZIP...")
        pick.set_valign(Gtk.Align.CENTER)
        pick.connect("clicked", self.on_pick_pdf)
        self.sheet_row.add_suffix(pick)
        g.add(self.sheet_row)
        self.bundle_export = Adw.ActionRow(title="Paired cut file")
        save = Gtk.Button(label="Save Paired Cut File…", valign=Gtk.Align.CENTER)
        save.connect("clicked", self.on_save_bundle_file)
        self.bundle_export.add_suffix(save)
        self.bundle_export.set_visible(False)
        g.add(self.bundle_export)

        self.r_sheet_page = _spin("Page", 1, 99, 1)
        self.r_sheet_dpi = _combo("Preview resolution (dpi)", ["150", "200", "300"], 1)
        g.add(self.r_sheet_page)
        self.r_sheet_page.connect('notify::value', self.on_sheet_number_changed)
        self.r_sheet_dpi.connect('notify::selected', self.on_pdf_page_changed)

        remeasure = Adw.ActionRow(
            title="Measure again",
            subtitle="After changing the layout or the mark settings")
        again = Gtk.Button(label="Measure")
        again.set_valign(Gtk.Align.CENTER)
        again.connect("clicked", lambda *_: self.measure_sheet())
        remeasure.add_suffix(again)
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

        self.stack.add_titled_with_icon(page, "sheet", "Sheet",
                                        "document-print-symbolic")

    def _page_blade(self):
        page = ControlPage()
        names = sorted(presets.all_presets())
        default = (names.index(config.DEFAULT_PRESET)
                   if config.DEFAULT_PRESET in names else 0)
        g = Adw.PreferencesGroup(title="Material preset")
        self.r_preset = _combo("Preset", names, default)
        self.preset_changed_id = self.r_preset.connect("notify::selected", self.on_preset)
        g.add(self.r_preset)
        self.preset_note = Adw.ActionRow(title="About this preset", subtitle="")
        self.preset_note.set_subtitle_lines(0)
        g.add(self.preset_note)
        self.guide_button(g, "Dial In a Material…", self.open_material_guide)
        page.add(g)

        g2 = Adw.PreferencesGroup(title="Cutter and blade")
        self.r_cutter = _combo(
            "Cutter", [d.name for d in graphtec.DEVICES],
            [d.name for d in graphtec.DEVICES].index("Silhouette Cameo 4"))
        self.r_force = _spin("Force", 1, 40, 30)
        self.r_speed = _spin("Speed", 1, 30, 3)
        self.r_passes = _spin("Passes", 1, 10, 3)
        self.r_depth = _special_value(_spin("Blade Depth", -1, 10, 7), -1, "Manual / off")
        self.r_holder = _spin("Tool Holder", 1, 2, 1)
        self.r_acceleration = _special_value(_spin("Acceleration", -1, 3, -1), -1, "Auto")
        self.r_diameter = _spin("Tool Offset (mm)", 0, 2.3, 0.9, 0.05, 2)
        self.r_lift = _switch("Lift head", True)
        self.r_corners = _switch("Line Segment Overcut", False)
        self.r_corner_start = _spin("Start Extension (mm)", 0, 0.9, 0.1, 0.1, 1)
        self.r_corner_end = _spin("End Extension (mm)", 0, 0.9, 0.1, 0.1, 1)
        self.r_track = _switch("Track Enhancing", False)
        self.blade_holder_buttons = self.tool_holder_picker(g2)
        for r in (self.r_cutter, self.r_force, self.r_speed, self.r_passes,
                  self.r_depth, self.r_acceleration, self.r_corners, self.r_track):
            g2.add(r)
        self.tool_row = Adw.ActionRow(title="Installed tools", subtitle="See what is in each holder.")
        detect = Gtk.Button(label="Detect Installed Tools", valign=Gtk.Align.CENTER)
        detect.connect("clicked", self.on_refresh_status)
        self.tool_row.add_suffix(detect)
        g2.add(self.tool_row)
        save_preset = Adw.ActionRow(
            title="Keep these settings",
            subtitle="Save the blade and tool settings to the selected preset.")
        self.save_preset_btn = Gtk.Button(label="Save preset")
        self.save_preset_btn.set_valign(Gtk.Align.CENTER)
        self.save_preset_btn.connect("clicked", self.on_save_blade_preset)
        save_preset.add_suffix(self.save_preset_btn)
        self.new_preset_name = Adw.EntryRow(title="New preset name")
        create = Gtk.Button(label="Save as New", valign=Gtk.Align.CENTER)
        create.connect("clicked", lambda *_: self.on_save_blade_preset(
            new_name=self.new_preset_name.get_text()))
        self.new_preset_name.add_suffix(create)
        g2.add(self.new_preset_name)
        page.add(g2)
        advanced, self.blade_advanced = disclosure(page.controls)
        blade_details = Adw.PreferencesGroup(title="Blade details")
        for row in (self.r_diameter, self.r_lift, self.r_corner_start, self.r_corner_end, save_preset):
            blade_details.add(row)
        advanced.append(blade_details)

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

        g3 = Adw.PreferencesGroup(
            title="Studio materials",
            description="Import Studio’s condition.csv to add its factory presets.")
        self.r_cutter_divisions = _combo("Show Cutter Divisions", ["Known Cutters", "All Cutters"])
        self.r_cutter_divisions.connect("notify::selected", self._load_materials)
        g3.add(self.r_cutter_divisions)
        self.r_material = _combo("Material", ["(none loaded)"])
        apply_row = Adw.ActionRow(
            title="Apply the selected material",
            subtitle="Load blade settings from the selected Studio material")
        apply_btn = Gtk.Button(label="Apply")
        apply_btn.set_valign(Gtk.Align.CENTER)
        apply_btn.connect("clicked", self.on_apply_material)
        apply_row.add_suffix(apply_btn)
        g3.add(self.r_material)
        csv = Adw.ActionRow(title="Local materials file")
        choose = Gtk.Button(label="Import Studio Materials…", valign=Gtk.Align.CENTER)
        choose.connect("clicked", self.on_choose_material_csv)
        csv.add_suffix(choose)
        choose_folder = Gtk.Button(label="Import Factory Folder…", valign=Gtk.Align.CENTER)
        choose_folder.connect("clicked", lambda *_: self.on_choose_material_csv(folder=True))
        csv.add_suffix(choose_folder)
        g3.add(csv)
        g3.add(apply_row)
        restore = Adw.ActionRow(title="Restore Default Materials",
                                subtitle="Back up edits to built-in materials and restore their original settings")
        restore_button = Gtk.Button(label="Restore", valign=Gtk.Align.CENTER)
        restore_button.connect("clicked", self.on_restore_default_materials)
        restore.add_suffix(restore_button)
        g3.add(restore)
        advanced.append(g3)
        self._load_materials()
        self.r_cutter.connect("notify::selected", self.on_material_cutter_changed)
        self.r_cutter.set_visible(False)

        for row in (self.r_cutter, self.r_path_order, self.r_pass_order, self.r_weed_lines):
            row.connect("notify::selected", self.on_changed)
        for r in (self.r_lift, self.r_corners, self.r_alternate, self.r_track,
                  self.r_interiors, self.r_delicate):
            r.connect("notify::active", self.on_changed)
        for r in (self.r_force, self.r_speed, self.r_passes, self.r_depth, self.r_holder,
                  self.r_acceleration, self.r_diameter, self.r_corner_start, self.r_corner_end,
                  self.r_overcut, self.r_weed_border):
            r.connect("notify::value", self.on_changed)

        self.stack.add_titled_with_icon(page, "blade", "Blade",
                                        "applications-engineering-symbolic")
        self.on_preset()

    def _page_cut(self):
        self.send_page = page = ControlPage()
        self.send_normal = normal = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        page.add(normal)
        self.send_modes = Adw.PreferencesGroup(title="Send by")
        self.r_send_mode = _combo("Mode", ["Standard", "Line", "Fill", "Layer"])
        self.r_send_mode.set_tooltip_text("Use one set of cut settings, or assign settings by color or layer.")
        self.r_send_mode.connect("notify::selected", self.on_changed)
        self.send_modes.add(self.r_send_mode)
        self.send_groups = Adw.PreferencesGroup(
            title="Cut groups", description="Choose which groups to cut and in what order.")

        g = Adw.PreferencesGroup(title="Registration")
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
        self.r_register.connect("notify::active", self.on_changed)
        self.r_auto.connect("notify::active", self.on_changed)

        g2 = Adw.PreferencesGroup(title="Cutter")
        refresh = Gtk.Button(label="Check Status")
        refresh.set_valign(Gtk.Align.CENTER)
        refresh.connect("clicked", self.on_refresh_status)
        g2.set_header_suffix(refresh)
        self.r_send_cutter = _combo("Cutter", list(cutters.MODELS.values()))
        self.r_send_cutter.connect("notify::selected", self.on_send_cutter)
        g2.add(self.r_send_cutter)
        normal.prepend(g2)
        self.r_connection = _combo("Connection", ["Automatic", *bluetooth.BLUETOOTH_MODES.values()],
                                   subtitle="Status not checked")
        self.r_connection.set_subtitle_lines(0)
        self.r_bluetooth = _combo("Connected cutter", ["Select a cutter"])
        self.r_bluetooth_id = Adw.EntryRow(title="Bluetooth identifier")
        self.r_bluetooth_channel = _spin("RFCOMM channel", 1, 30, 1, 1, 0)
        self._bluetooth_devices = []
        self._usb_devices = {}
        self._wireless_choices = {}
        self._wireless_scanning = False
        self.r_bluetooth.connect("notify::selected", self.on_bluetooth_selected)
        scan = Gtk.Button(label="Refresh Cutters")
        scan.connect("clicked", self._scan_bluetooth)
        self.r_bluetooth.add_suffix(scan)
        g2.add(self.r_connection)
        g2.add(self.r_bluetooth)
        self.r_connection.connect("notify::selected", self.on_connection_changed)
        self.on_connection_changed()
        self.r_bluetooth_id.connect("changed", self.on_connection_target_changed)
        self.r_bluetooth_channel.connect("notify::value", self.on_connection_target_changed)
        advanced, self.send_advanced = disclosure(normal)
        bluetooth_options = Adw.PreferencesGroup(title="Bluetooth")
        bluetooth_options.add(self.r_bluetooth_id)
        bluetooth_options.add(self.r_bluetooth_channel)
        self.advanced_bluetooth = bluetooth_options
        bluetooth_options.set_visible(False)
        advanced.append(bluetooth_options)
        advanced.append(self.send_modes)
        normal.append(self.send_groups)
        normal.append(self.cut_settings_group())
        normal.append(g)

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
        paths = Adw.PreferencesGroup(title="Cut paths")
        order = _combo("Cut Order Sorting", list(toolpath.PATH_ORDERS.values()))
        self.r_path_order.bind_property("selected", order, "selected",
                                        GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE)
        interiors = _switch("Sort Interior Contours First", self.r_interiors.get_active())
        self.r_interiors.bind_property("active", interiors, "active",
                                       GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE)
        paths.add(order)
        paths.add(interiors)
        self.guide_button(paths, "More Blade Options…", self.open_advanced_blade)
        advanced.append(paths)

        self.guide_view = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, visible=False)
        page.add(self.guide_view)
        self.guide_group = guide = Adw.PreferencesGroup(title="Dial In a Material", description=starting_guide.GUIDE_TEXT)
        self.guide_apply, self.guide_edit = self.guide_buttons(
            guide, ("Use Starting Settings", self.on_guide_start), ("Adjust in Blade", self.adjust_guide_settings))
        self.guide_status = Adw.ActionRow(title="Test spot 1")
        self.guide_status.set_subtitle_lines(0)
        guide.add(self.guide_status)
        self.guide_outcome = _combo("How did the cut look?", list(starting_guide.OUTCOMES.values()))
        self.guide_outcome.connect("notify::selected", self.on_guide_outcome)
        guide.add(self.guide_outcome)
        self.guide_advice = Adw.ActionRow(title="What to try next")
        self.guide_advice.set_subtitle_lines(0)
        guide.add(self.guide_advice)
        self.guide_send, self.guide_use = self.guide_buttons(
            guide, ("Cut One Pass…", self.on_guide_pass), ("Use Tested Settings…", self.on_guide_use))
        self.guide_next = self.guide_button(guide, "Next Test Spot", self.on_guide_next)
        self.guide_view.append(guide)
        extras, self.guide_advanced = disclosure(self.guide_view, "Test Options")
        tips = Adw.PreferencesGroup(title="Test sheet")
        self.guide_reset = self.guide_button(tips, "New Test Sheet", self.on_guide_reset)
        detail = Adw.ActionRow(title="One change at a time", subtitle=starting_guide.TUNING_TIPS)
        detail.set_subtitle_lines(0)
        tips.add(detail)
        extras.append(tips)

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
        defaults = Adw.PreferencesGroup(title="Defaults")
        self.guide_button(defaults, "Restore Default Materials…", self.on_restore_default_materials)
        advanced.append(defaults)

        self.stack.add_titled_with_icon(page, "cut", "Send", "media-playback-start-symbolic")

    def cut_settings_group(self):
        group = Adw.PreferencesGroup(title="Cut Settings", css_classes=["scc-cut-settings"])
        _install_cut_settings_css(self.get_display())
        dial_in = Gtk.Button(label="Dial In a Material…", valign=Gtk.Align.CENTER)
        dial_in.connect("clicked", self.open_material_guide)
        group.set_header_suffix(dial_in)
        self.send_holder_buttons = self.tool_holder_picker(group)
        self.r_send_preset = _combo("Material", [config.DEFAULT_PRESET])
        self.send_preset_changed_id = self.r_send_preset.connect(
            "notify::selected", lambda row, *_: self.r_preset.set_selected(row.get_selected()))
        group.add(self.r_send_preset)
        self.send_controls = {}
        self.send_sliders = {}
        for field, source in (("force", self.r_force), ("speed", self.r_speed), ("passes", self.r_passes),
                              ("acceleration", self.r_acceleration), ("depth", self.r_depth)):
            row = Adw.ActionRow(title=source.get_title())
            pieces = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)
            adjustment = source.get_adjustment()
            slider = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=adjustment,
                               draw_value=False, hexpand=True, width_request=110)
            slider.update_property([Gtk.AccessibleProperty.LABEL], [source.get_title()])
            precision = Gtk.SpinButton(adjustment=adjustment, digits=source.get_digits(), width_chars=4)
            precision.update_property([Gtk.AccessibleProperty.LABEL], [f"{source.get_title()} value"])
            if field in ("acceleration", "depth"):
                _special_value(precision, -1, "Auto" if field == "acceleration" else "Manual")
            source.bind_property("sensitive", row, "sensitive", GObject.BindingFlags.SYNC_CREATE)
            pieces.append(slider)
            pieces.append(precision)
            row.add_suffix(pieces)
            group.add(row)
            self.send_controls[field] = precision
            self.send_sliders[field] = slider
        toggles = Gtk.Box(spacing=16, css_classes=["cut-toggles"])
        for field, source in (("sharpen_corners", self.r_corners), ("track_enhancing", self.r_track)):
            control = Gtk.CheckButton(label=source.get_title())
            source.bind_property("active", control, "active",
                                 GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE)
            source.bind_property("sensitive", control, "sensitive", GObject.BindingFlags.SYNC_CREATE)
            self.send_controls[field] = control
            toggles.append(control)
        group.add(Adw.PreferencesRow(child=toggles, activatable=False, selectable=False))
        return group

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

    def sync_send_material(self):
        with self.r_send_preset.handler_block(self.send_preset_changed_id):
            model = self.r_preset.get_model()
            if self.r_send_preset.get_model() is not model:
                self.r_send_preset.set_model(model)
            self.r_send_preset.set_selected(self.r_preset.get_selected())

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
        for field, row in self.send_controls.items():
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
        selected = self.r_layout_preset.get_selected()
        if selected == 0:
            return
        profile = list(layout_presets.BUILTIN.values())[selected - 1]
        self.clear_bundle()
        options = profile.options()
        was, self._suppress = self._suppress, True
        self.imported = None
        self.reviewed_design = None
        self.template_shapes = ()
        self.import_row.set_title('Generated layout')
        self.import_row.set_subtitle('Using the sheet and card settings below')
        self.r_colour.set_model(Gtk.StringList.new(['all colours']))
        self.r_colour.set_selected(0)
        self.r_engine.set_selected(1)
        self.r_sheet.set_selected(sorted(geometry.SHEETS).index(options['sheet']))
        self.r_card.set_selected(sorted(geometry.CARDS).index('standard'))
        self.r_orient.set_selected(2)
        self.r_mode.set_selected(0)
        self.r_radius.set_value(options['radius_mm'])
        self.r_pattern.set_selected(0)
        self.r_inset.set_value(options['mark_inset_mm'])
        self.r_length.set_value(options['mark_length_mm'])
        self.r_thickness.set_value(options['mark_thickness_mm'])
        self.r_bleed.set_value(options['bleed_mm'])
        self.r_skip.set_text('')
        self.r_register.set_active(True)
        self.r_auto.set_active(True)
        self._suppress = was
        self.on_changed()

    def _text(self, row):
        model = row.get_model()
        return model.get_string(row.get_selected())

    def options(self) -> dict:
        orient = self._text(self.r_orient)
        portrait = {"Best fit": None, "Portrait": True,
                    "Landscape": False}[orient]
        engine = "scm" if self._text(self.r_engine) == "Silhouette Card Maker" \
            else "proxypress"
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
            row = Adw.ExpanderRow(title=key, subtitle=f"{len(paths)} path(s)")
            enabled = _switch("Send this group", active)
            preset = _combo("Preset", names, names.index(preset_name) if preset_name in names else 0)
            holder = _combo("Tool Holder", ["Use preset", "1", "2"], slot or 0)
            order = _spin("Send order", 1, len(found), position)
            for control, prop in ((enabled, "active"), (preset, "selected"),
                                  (holder, "selected"), (order, "value")):
                row.add_row(control)
                control.connect(f"notify::{prop}", self.on_changed)
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
        if self._suppress:
            return
        device = graphtec.device_by_name(self._text(self.r_cutter))
        self.restore_holder(device)
        self.sync_send_material()
        self.r_holder.set_sensitive(device.two_tools)
        self.r_depth.set_sensitive(device.autoblade and (not device.two_tools or self.r_holder.get_value() == 1))
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
        if self.pdf_bundle and self._sheet_token is None:
            self.measure_sheet()
        elif self.pdf_path and not self.pdf_bundle and (
                (previous.marks if previous else None) != (self.job.marks if self.job else None)):
            previously_measured = self.sheet_report is not None or self._sheet_token is not None
            self._sheet_token = None
            self.sheet_report = None
            self.preview.set_sheet(None)
            if previously_measured:
                self.sheet_result.set_title('Registration settings changed')
                self.sheet_result.set_subtitle('Measure the PDF again to compare the marks.')

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
                    track_enhancing=self.r_track.get_active())

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
                self.preset_note.set_subtitle(preset.note or preset.description)
                self._load_blade_settings(settings)
            for buttons in (self.blade_holder_buttons, self.send_holder_buttons):
                buttons[2].set_sensitive(device.two_tools)
                buttons[slot].set_active(True)
        finally:
            self._suppress = False

    def _load_blade_settings(self, settings):
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

    def on_save_blade_preset(self, *_a, new_name=None):
        name = self._text(self.r_preset)
        try:
            saved = presets.save_blade_settings(
                name, force=int(self.r_force.get_value()), speed=int(self.r_speed.get_value()),
                depth=int(self.r_depth.get_value()) if self.r_depth.get_value() >= 0 else None,
                passes=int(self.r_passes.get_value()), blade_options=self._blade_options(),
                new_name=new_name, cutter=self._text(self.r_cutter))
        except (OSError, ValueError) as exc:
            self.toast(f"Could not save preset: {exc}")
            return
        if saved is None:
            self.toast("Preset settings are already saved.")
            return
        if new_name is not None:
            name = new_name.strip()
            self.refresh_preset_choices(name)
            self.new_preset_name.set_text("")
        self.on_preset()
        self.toast(f"Saved {name}.")

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
        self.preset_note.set_subtitle(p.note or p.description)
        was = self._suppress
        self._suppress = True
        self._load_blade_settings(for_holder(p.cut_settings(), slot))
        self._suppress = was
        self.on_changed()

    def refresh_preset_choices(self, name=None):
        name = name or self._text(self.r_preset)
        names = sorted(presets.all_presets(self._text(self.r_cutter)))
        if name not in names:
            name = config.DEFAULT_PRESET
        with self.r_preset.handler_block(self.preset_changed_id):
            self.r_preset.set_model(Gtk.StringList.new(names))
            self.r_preset.set_selected(names.index(name))

    def rebuild(self):
        if self._suppress or self._building:
            return
        if self.pdf_bundle and self.imported is None:
            self.job = None
            self.preview.set_job(None)
            self.summary_icon.set_from_icon_name('document-open-symbolic')
            self.summary_title.set_label('Legacy cut file')
            self.summary_layout.set_label('Use Sheet → Save Paired Cut File.')
            self.summary_material.set_label('')
            self.summary_note.set_label('A newer ProxyPress ZIP includes SVG paths for direct cutting.')
            self.checks.set_text('')
            self.update_send_actions()
            self.banner.set_revealed(False)
            return
        self._building = True
        try:
            opts = self.options()
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
        self.summary_layout.set_label(f'{j.grid.sheet_name} {orientation} · {dimensions}')
        if j.action_groups:
            holders = ", ".join(str(holder) for holder in sorted({group.settings.holder for group in j.action_groups}))
            caption = f"{len(j.action_groups)} send groups · Tool holders: {holders}"
        else:
            passes = "pass" if j.settings.passes == 1 else "passes"
            caption = f"{j.preset.name} · {j.settings.passes} {passes}"
        self.summary_material.set_label(caption)
        self.summary_note.set_label(
            j.blocked[0] if j.blocked else
            'Open Sheet to see which marks failed the PDF check.' if failed_pdf else
            j.warnings[0] if j.warnings else
            'Artwork is close to a registration mark.' if j.mask_hints else
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
            lines.append("NEAR REGISTRATION MARKS (conservative proximity check):")
            for h in j.mask_hints:
                lines.append(f"  {h['text']}")
            lines.append("Use Test scan to see whether the cutter can read the printed marks.")
        self.checks.set_text("\n".join(lines))

        blocked = j.blocked
        if blocked:
            self.banner.set_title(f"{len(blocked)} problem(s): {blocked[0]}")
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
        self.toasts.add_toast(Adw.Toast(title=text, timeout=5))

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
        self.toast(f"Wrote {len(written)} file(s) to {config.TEMPLATE_DIR}")

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
        dialog = Gtk.FileDialog(title="Open artwork to cut")
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
            path = pathlib.Path(gfile.get_path())
            self.load_artwork_file(path)

        dialog.open(self, None, done)

    def load_artwork_file(self, path, page=None):
        from . import importer
        try:
            artwork = importer.load(path, page=page)
        except importer.InkscapePageRequired as exc:
            dialog = Adw.AlertDialog(heading="Choose an Inkscape page",
                                     body=f"{path.name} contains {exc.count} pages.")
            selector = Gtk.SpinButton.new_with_range(1, exc.count, 1)
            selector.set_tooltip_text("Page number")
            selector.set_halign(Gtk.Align.CENTER)
            dialog.set_extra_child(selector)
            dialog.add_response("cancel", "Cancel")
            dialog.add_response("open", "Open page")
            dialog.set_default_response("open")
            dialog.set_close_response("cancel")

            def selected(_dialog, response):
                if response == "open":
                    self.load_artwork_file(path, selector.get_value_as_int())

            dialog.connect("response", selected)
            dialog.present(self)
            return
        except Exception as exc:              # noqa: BLE001
            self.toast(f"Could not read {path.name}: {exc}")
            return
        self.clear_bundle()
        self.template_shapes = ()
        name = f"{path.name} · Page {page}" if page is not None else path.name
        self.set_artwork(artwork, name)

    def on_edit_template(self, *_args):
        if self.pdf_bundle:
            self.toast('Clear the matched job before editing its paths.')
            return
        if self._sending:
            self.toast('Wait for the current job to finish before opening Design.')
            return
        if self.design is not None:
            self.workspaces.set_visible_child(self.design.view)
            return
        if self.job is None:
            self.toast('Select a sheet before opening Design.')
            return
        from .editor import DesignWorkspace
        from .editor_model import Project, Registration, editable_import, shapes_from_paths

        try:
            shapes = self.template_shapes or (editable_import(self.imported)[0]
                                             if self.imported else shapes_from_paths(self.job.paths))
        except ValueError as error:
            self.toast(str(error))
            return
        grid = self.job.grid
        registration = Registration.from_marks(self.job.marks) if self.job.registration else None
        project = Project(grid.sheet_w_mm, grid.sheet_h_mm, shapes, registration, self.job.mat)
        design = DesignWorkspace(self, project, self.leave_design, self.review_design, cutter=self.job.device)
        self.workspaces.add_named(design.view, 'design')
        self.design = design
        self.workspaces.set_visible_child(design.view)

    def leave_design(self, *_args):
        self.workspaces.set_visible_child_name('tools')
        self.update_send_actions()

    def close_design(self, *_args):
        def discard():
            self.workspaces.set_visible_child_name('tools')
            self.workspaces.remove(self.design.view)
            self.design = None
            self.update_send_actions()

        self.design.confirm_discard(discard)

    def confirm_window_close(self, *_args):
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
        if any(path.length_mm() <= 0 for path in artwork.paths):
            self.design.status.set_text('Resize any paths with no length before reviewing.')
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
        self.stack.set_visible_child_name('cut')
        self.leave_design()

    def set_artwork(self, artwork, name, group_defaults=None):
        self.reviewed_design = None
        self.imported = artwork
        self._send_defaults = group_defaults or {}
        self.import_row.set_title(name)
        lines = [artwork.describe(), *artwork.warnings] if artwork else ['Cut template for Silhouette Studio']
        self.import_row.set_subtitle("\n".join(lines))
        was, self._suppress = self._suppress, True
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
        if self.imported is None and self.pdf_bundle is None:
            return
        self.clear_bundle()
        self.imported = None
        self.template_shapes = ()
        was, self._suppress = self._suppress, True
        self._update_document_sheet(None)
        self.r_colour.set_model(Gtk.StringList.new(["all colours"]))
        self.r_colour.set_selected(0)
        self._suppress = was
        self.import_row.set_title("Generated layout")
        self.import_row.set_subtitle(
            "Using the sheet and card settings below")
        self.on_changed()

    # ---- the printed sheet -------------------------------------------
    def lock_bundle_layout(self, locked):
        for widget in (self.r_layout_preset, self.r_sheet, self.r_orient, self.r_engine,
                       self.r_card, self.r_pattern, self.r_inset, self.r_length,
                       self.r_thickness, self.r_bleed, self.r_register, self.r_mode,
                       self.r_radius, self.r_skip, self.r_colour):
            widget.set_sensitive(not locked)

    def clear_bundle(self):
        if not self.pdf_bundle:
            return
        self.pdf_bundle = None
        self.bundle_export.set_visible(False)
        self.lock_bundle_layout(False)
        self.bundle_checked = False
        self.pdf_path = None
        self._sheet_token = None
        self.sheet_report = None
        self.preview.set_sheet(None)
        self.preview.set_pdf()
        self.sheet_row.set_title('No PDF loaded')
        self.sheet_row.set_subtitle('Pick the sheet you printed')
        self.sheet_result.set_title('Not measured')
        self.sheet_result.set_subtitle('')
        self.scan_result.set_visible(False)
        self.r_sheet_page.set_title('Page')
        self.r_sheet_page.set_subtitle('which page of the PDF to measure')
        self.r_sheet_page.get_adjustment().set_upper(99)

    def select_bundle_sheet(self):
        row = self.pdf_bundle.rows[int(self.r_sheet_page.get_value())-1]
        try:
            artwork = self.pdf_bundle.artwork(row) if 'svg' in row else None
        except bundle.BundleError as exc:
            self.on_clear_artwork()
            self.toast(str(exc))
            return
        self.describe_bundle_sheet(row)
        self.bundle_checked = False
        self._sheet_token = self.sheet_report = None
        self.preview.set_sheet(None)
        self.preview.set_pdf()
        self.set_artwork(artwork, row.get('svg') or row['cutFile'])

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
        dialog = Gtk.FileDialog(title="Save Paired Cut File", initial_name=name)

        def done(chooser, result):
            try:
                path = chooser.save_finish(result).get_path()
                if path is not None:
                    self.toast(f"Saved {matched.save_cut_file(row, path).name}.")
            except GLib.Error:
                return
            except bundle.BundleError as error:
                self.toast(str(error))

        dialog.save(self, None, done)

    def on_pick_pdf(self, *_a):
        dialog = Gtk.FileDialog(title="Open the printed sheet")
        filt = Gtk.FileFilter()
        filt.set_name('PDF and ZIP files (*.pdf, *.zip)')
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
            try:
                gfile = d.open_finish(result)
            except GLib.Error:
                return
            filename = gfile.get_path()
            if filename is None:
                self.toast('Choose a local PDF or ProxyPress ZIP.')
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
                self.r_sheet_page.set_title('Sheet in ZIP')
                self.r_sheet_page.set_subtitle('which matched sheet to check and cut')
                self.r_sheet_page.get_adjustment().set_upper(len(matched.rows))
                self.r_sheet_page.set_value(1)
                self._suppress = False
                self.describe_bundle_sheet(row)
                self.set_artwork(artwork, row.get('svg') or row['cutFile'])
                return
            if self.pdf_bundle:
                self.on_clear_artwork()
            self.pdf_path = path
            self.preview.set_pdf()
            self.sheet_row.set_title(self.pdf_path.name)
            self.sheet_row.set_subtitle(str(self.pdf_path.parent))
            self.measure_sheet()

        dialog.open(self, None, done)

    def on_pdf_page_changed(self, *_a):
        if self._suppress or self.pdf_path is None:
            return
        self._sheet_token = None
        self.preview.set_pdf()
        self.measure_sheet()

    def on_sheet_number_changed(self, *_a):
        if self.pdf_bundle and not self._suppress:
            self.select_bundle_sheet()
        else:
            self.on_pdf_page_changed()

    def measure_sheet(self):
        path = getattr(self, "pdf_path", None)
        matched = self.pdf_bundle
        if path is None or (self.job is None and not matched):
            self.toast("Load a PDF and fix the layout first.")
            return
        self.scan_result.set_visible(False)
        self.sheet_result.set_title("Measuring...")
        self.sheet_result.set_subtitle("")
        marks = matched.marks() if matched else self.job.marks
        page = int(self.r_sheet_page.get_value())
        if matched:
            row = matched.rows[page-1]
            self.bundle_checked = False
            self.update_send_actions()
        dpi = int(self._text(self.r_sheet_dpi))
        token = self._sheet_token = object()
        self.sheet_report = None
        self.preview.set_sheet(None)

        def work():
            from . import sheet as sheet_mod
            try:
                if matched:
                    report, image = matched.measure(row, marks, dpi)
                else:
                    image = sheet_mod.render(path, page=page, dpi=dpi, colour=True)
                    report = sheet_mod.measure(path, marks, page=page, dpi=dpi, image=image)
                png = io.BytesIO()
                image.save(png, format='PNG')
            except Exception as exc:              # noqa: BLE001
                GLib.idle_add(self._sheet_failed, str(exc), token)
                return
            problems = report.problems(marks.sheet_w_mm, marks.sheet_h_mm)
            GLib.idle_add(self._sheet_done, report, problems, png.getvalue(), token)

        threading.Thread(target=work, daemon=True).start()

    def _sheet_failed(self, message, token):
        if not sheet_request_current(self._sheet_token, token):
            return
        self.sheet_report = None
        self.preview.set_sheet(None)
        self.preview.set_pdf()
        self.sheet_result.set_title("Could not measure")
        self.sheet_result.set_subtitle(message)
        self.rebuild()

    def _sheet_done(self, report, problems, png, token):
        if not sheet_request_current(self._sheet_token, token):
            return
        self.sheet_report = report
        self.bundle_checked = bool(self.pdf_bundle) and not problems
        self.preview.set_sheet(report)
        self.preview.set_pdf(png, report.page_w_mm, report.page_h_mm)
        lines = [m.describe() for m in report.marks]
        lines.append("")
        lines.append(report.diagnosis())
        for pr in problems:
            lines.append(f"PROBLEM: {pr}")
        self.sheet_result.set_title(
            "Sheet matches the template" if not problems
            else f"{len(problems)} problem(s)")
        self.sheet_result.set_subtitle("\n".join(lines))
        self.rebuild()

    # ---- Silhouette's own materials ----------------------------------
    def on_choose_material_csv(self, *_args, folder=False):
        from . import materials

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
                    self._load_materials()
                    self.refresh_preset_choices()
                    self.on_changed()
            except GLib.Error:
                return
            except (OSError, ValueError) as error:
                self.toast(str(error))

        (dialog.select_folder if folder else dialog.open)(self, None, done)

    def on_material_cutter_changed(self, *_args):
        self._load_materials()
        selected = self._text(self.r_preset)
        self.refresh_preset_choices()
        if (selected in presets.factory_presets(self._text(self.r_cutter))
                or self._text(self.r_preset) != selected):
            self.on_preset()

    def _load_materials(self, *_args):
        selected = self._text(self.r_material)
        previous = getattr(self, "_materials", {}).get(selected)
        self._materials = {}
        try:
            from . import materials
            self._materials = materials.cutter_divisions(
                materials.load(), self._text(self.r_cutter),
                self.r_cutter_divisions.get_selected() == 1)
        except (OSError, ValueError):
            pass
        names = sorted(self._materials) or ["(no local CSV selected)"]
        self.r_material.set_model(Gtk.StringList.new(names))
        if selected in self._materials:
            self.r_material.set_selected(names.index(selected))
        elif previous is not None:
            match = next((i for i, name in enumerate(names)
                          if self._materials.get(name) == previous), 0)
            self.r_material.set_selected(match)
        elif "Coverstock" in self._materials:
            self.r_material.set_selected(names.index("Coverstock"))

    def on_restore_default_materials(self, *_args):
        dialog = Adw.AlertDialog(heading="Restore Default Materials?",
                                 body="Edits to built-in materials will be backed up. Custom materials stay available.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("restore", "Restore")
        dialog.set_close_response("cancel")
        dialog.set_default_response("cancel")

        def restore(_dialog, response):
            if response != "restore":
                return
            try:
                count, directory = presets.restore_default_materials()
            except OSError as exc:
                self.toast(f"Could not restore defaults. {exc}")
                return
            if count:
                self.on_preset()
                self.toast(f"Restored {count} material presets. Backup at {directory}.")
            else:
                self.toast("The default materials already have their original settings.")

        dialog.connect("response", restore)
        dialog.present(self)

    def on_apply_material(self, *_a):
        name = self._text(self.r_material)
        m = self._materials.get(name)
        if m is None:
            self.toast("Import Studio Materials and choose a material first.")
            return
        if self.r_cutter_divisions.get_selected() == 0:
            self.refresh_preset_choices(name)
            self.on_preset()
            return
        was, self._suppress = self._suppress, True
        self._load_blade_settings(for_holder(m.cut_settings(), self.holder_state.active))
        self._suppress = was
        self.toast(f"{name}: {m.describe()}")
        self.on_changed()

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

    def update_guide(self):
        busy = self._sending or self._checking_status
        for field in (self.guide_apply, self.guide_edit, self.guide_next, self.guide_reset):
            field.set_sensitive(not busy)
        state = self.guide
        self.guide_send.set_sensitive(not busy and state.can_cut)
        self.guide_use.set_sensitive(not busy and state.outcome == "clean" and state.tested_settings is not None)
        for button in (self.guide_send, self.guide_use):
            if button.get_sensitive():
                button.add_css_class("suggested-action")
            else:
                button.remove_css_class("suggested-action")
        label = "Repeat One Pass…" if self.guide.completed else "Cut One Pass…"
        self.guide_send.set_label(label)
        title, _, body = self.guide.status().partition("\n")
        self.guide_status.set_title(title)
        if state.awaiting_result and not busy:
            body += "\nLeave the mat loaded and lift a corner to check the sample."
        self.guide_status.set_subtitle("Waiting for the cutter to finish…" if self._sending else body)
        inspecting = state.completed > 0 and not state.interrupted
        self.guide_outcome.set_visible(inspecting)
        self.guide_outcome.set_sensitive(not busy)
        self.guide_outcome.set_selected(tuple(starting_guide.OUTCOMES).index(state.outcome))
        advice = state.advice() if inspecting else ""
        self.guide_advice.set_subtitle(advice)
        self.guide_advice.set_visible(bool(advice))

    def on_guide_outcome(self, row, *_args):
        self.guide.inspect(tuple(starting_guide.OUTCOMES)[row.get_selected()])
        self.update_guide()

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
        if self.guide.trial:
            self.guide.next_spot()
        device = graphtec.device_by_name(self._text(self.r_cutter))
        opts = self._blade_options()
        self.set_guide_blade_values(starting_guide.starting_values(device, opts["holder"], opts["blade_diameter_mm"]))
        suffix = "" if self.r_depth.get_value() >= 0 else " Set depth on the blade."
        self.toast("Starting settings applied to Blade." + suffix)

    def on_guide_use(self, *_args):
        settings = self.guide.tested_settings
        if settings is None or self.guide.outcome != "clean":
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
        self.guide.next_spot()
        self.update_guide()
        self.update_tuning_preview()

    def on_guide_reset(self, *_args):
        dialog = Adw.AlertDialog(heading="Start a New Test Sheet?",
                                 body="Load a fresh blank sheet to start at the first spot.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("new", "New Sheet")
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def reset(_dialog, response):
            if response == "new" and not (self._sending or self._checking_status):
                self.guide.reset()
                self.update_guide()
                self.update_tuning_preview()

        dialog.connect("response", reset)
        dialog.present(self)

    def guide_proposal(self):
        return self.guide.prepare(self._connection(), self.options(), self.job, "square")

    def on_guide_pass(self, *_args):
        if self._sending or self._checking_status or not self.guide.can_cut:
            return
        try:
            trial = self.guide_proposal()
        except (ValueError, KeyError) as error:
            self.toast(str(error))
            return
        prompt = self.guide.prompt(trial)
        dialog = Adw.AlertDialog(heading="Send One Test Pass?", body=prompt)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("cut", "Cut")
        dialog.set_response_appearance("cut", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._guide_pass_response, trial)
        dialog.present(self)

    def _guide_pass_response(self, _dialog, response, trial):
        if response != "cut" or self._sending or self._checking_status or not self.guide.can_cut:
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
        self._sending = True
        self.update_send_actions()

        def done(error=None):
            self.guide.record(trial, error)
            self._send_done()
            self.toast(str(error) if error else "Pass complete. Keep the mat loaded and lift a corner.")

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
        label = "square-and-triangle" if ts.shape == "studio" else "square"
        dialog = Adw.AlertDialog(
            heading=f"Cut {len(ts.cells)} {label} test samples?",
            body="Load a blank offcut of the material. Registration is off.\n\n" + rows)
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
        if self._sending or self._checking_status:
            self.toast("The cutter connection is busy.")
            return
        self.guide.invalidate()
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
        devices = {**cutters.usb_choices(), **self._wireless_choices}
        if devices != self._usb_devices:
            self._usb_devices = devices
            self.r_bluetooth.set_model(Gtk.StringList.new(["Select a cutter", *devices.values()]))
            selected = next(iter(devices)) if len(devices) == 1 and cutters.auto_model(next(iter(devices)), next(iter(devices.values()))) else None
            self.r_bluetooth.set_selected(list(devices).index(selected) + 1 if selected else 0)
            self.on_bluetooth_selected()
            self.r_connection.set_subtitle(devices.get(selected, "Select a cutter." if devices else "No cutter found."))
        return False

    def refresh_wireless(self):
        if self._sending or self._wireless_scanning or self._connection_mode() != "auto":
            return False
        self._wireless_scanning = True

        def done(choices):
            self._wireless_scanning = False
            self._wireless_choices = choices
            self.refresh_usb()
            return False

        def work():
            try:
                choices = cutters.wireless_choices()
            except transport.TransportError:
                choices = {}
            GLib.idle_add(done, choices)

        threading.Thread(target=work, daemon=True).start()
        return False

    def _automatic_endpoint(self):
        index = self.r_bluetooth.get_selected() - 1
        return list(self._usb_devices)[index] if 0 <= index < len(self._usb_devices) else None

    def _connection_mode(self):
        return ("auto", *bluetooth.BLUETOOTH_MODES)[self.r_connection.get_selected()]

    def on_connection_changed(self, *_args):
        mode = self._connection_mode()
        self._bluetooth_devices = []
        self.r_bluetooth.set_model(Gtk.StringList.new(["Select a cutter"]))
        self.r_bluetooth.set_selected(0)
        self.r_bluetooth_id.set_text("")
        self._usb_devices = {}
        self.r_bluetooth.set_visible(True)
        if mode == "auto":
            GLib.idle_add(self.refresh_usb)
        if hasattr(self, "advanced_bluetooth"):
            self.advanced_bluetooth.set_visible(mode != "auto")
        self.r_bluetooth_id.set_visible(mode != "auto")
        self.r_bluetooth_channel.set_visible(mode == "bluetooth")
        self.on_connection_target_changed()

    def on_connection_target_changed(self, *_args):
        self.r_connection.set_subtitle("Status not checked")
        self.tool_row.set_subtitle("Not checked")
        self.update_send_actions()

    def _scan_bluetooth(self, button):
        from . import ble, rfcomm
        mode = self._connection_mode()
        if mode == "auto":
            self.refresh_usb()
            self.refresh_wireless()
            return
        scanner = ble if mode == "ble" else rfcomm

        def update(devices, error):
            button.set_sensitive(True)
            if mode != self._connection_mode():
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

        def work():
            try:
                GLib.idle_add(update, scanner.discover(), None)
            except transport.TransportError as exc:
                GLib.idle_add(update, [], str(exc))

        button.set_sensitive(False)
        threading.Thread(target=work, daemon=True).start()

    def on_bluetooth_selected(self, *_args):
        index = self.r_bluetooth.get_selected() - 1
        if self._connection_mode() == "auto":
            endpoint = self._automatic_endpoint()
            model = cutters.auto_model(endpoint, self._usb_devices.get(endpoint, ""))
            if model and self.selected_cutter() == cutters.AUTO:
                self.r_cutter.set_selected(graphtec.DEVICES.index(model))
            self.update_send_actions()
            return
        if 0 <= index < len(self._bluetooth_devices):
            identifier, name = self._bluetooth_devices[index]
            self.r_bluetooth_id.set_text(identifier)
            model = bluetooth.advertised_model(name)
            if model and self.selected_cutter() == cutters.AUTO:
                self.r_cutter.set_selected(graphtec.DEVICES.index(model))

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

    def on_refresh_status(self, *_a):
        if self._sending or self._checking_status:
            return
        connection = self._connection()
        mode = connection[0]
        if mode in bluetooth.BLUETOOTH_MODES and not connection[1]:
            self.r_connection.set_subtitle("Select a Bluetooth cutter.")
            self.tool_row.set_subtitle("Select a Bluetooth cutter.")
            return
        self._checking_status = True
        self.update_send_actions()
        self.r_connection.set_subtitle("Checking…")
        self.tool_row.set_subtitle("Checking…")

        def done(text, device):
            self._checking_status = False
            self.update_send_actions()
            if connection != self._connection():
                return
            self.r_connection.set_subtitle(text)
            self.tool_row.set_subtitle(text)
            if device is not None and self.selected_cutter() == cutters.AUTO:
                self.r_cutter.set_selected(graphtec.DEVICES.index(device))

        def work():
            device = None
            try:
                with cutters.open_link(connection) as cutter:
                    device = cutter.device
                    text = f"{device.name} · {cutter.status_with_tools()}"
                    if mode in bluetooth.BLUETOOTH_MODES:
                        text += f" · {cutter.firmware_version()}"
            except Exception as exc:
                text = f"Tool detection unavailable · {exc}"
            GLib.idle_add(done, text, device)

        threading.Thread(target=work, daemon=True).start()

    def on_cut(self, *_a, scan_only=False, corner=False):
        if design_review_pending(self):
            self.toast('Review the latest edits in Design.')
            return
        if self.pdf_bundle and not self.bundle_checked:
            self.toast('Check the matched PDF before cutting this sheet.')
            return
        j = self.job
        refusal = cut_refusal(j, j, self._sending or self._checking_status)
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
            dialog = Adw.AlertDialog(heading="Corner Test Cut?", body=sample.describe())
            dialog.add_response("cancel", "Cancel")
            dialog.add_response("cut", "Cut")
            dialog.set_response_appearance("cut", Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.set_default_response("cancel")
            dialog.set_close_response("cancel")
            dialog.connect("response", self._cut_response, j, connection, False, sample)
            dialog.present(self)
            return
        profiles = ("\n".join(f"{i + 1}. {group.key} · {group.describe()}"
                              for i, group in enumerate(j.action_groups))
                    if j.action_groups else
                    f"{j.preset.name}: force {j.settings.force}, speed "
                    f"{j.settings.speed}, {j.settings.passes} pass(es)")
        dx, dy = j.offset_mm
        body = (f"This moves the blade.\n\n{j.grid.describe()}\n{profiles}\n\n"
                f"Cutter offset: X {dx:+.2f} mm, Y {dy:+.2f} mm.\n\n")
        used = [group.preset for group in j.action_groups] or [j.preset]
        if any(preset.origin != "measured" for preset in used):
            body += "The selected settings need a hardware test on this stock.\n\n"
        body += ("The cutter will search for the printed marks first."
                 if j.registration else "Registration is off; this uses sheet coordinates.")
        if scan_only:
            body = "This test ends after the cutter reports the scan result."
        if j.registration and not j.auto_search:
            body += "\n\nPosition the cutter head over the first mark before starting the scan."
        if self.pdf_bundle:
            body += "\n\n" + self.pdf_bundle.mat_placement()
        if self.imported is not None and self.imported.studio3_page:
            body += "\n\nCheck the imported outlines and match the registration settings to the printed Studio3 sheet."
        dialog = Adw.AlertDialog(heading="Test registration?" if scan_only else "Send this cut?", body=body)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("cut", "Scan" if scan_only else "Cut")
        dialog.set_response_appearance("cut", Adw.ResponseAppearance.SUGGESTED
                                       if scan_only else Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        if connection[1]:
            dialog.set_body(f"{body}\n\nConnection: {j.device.name} ({connection[1]})")
        dialog.connect("response", self._cut_response, j, connection, scan_only)
        dialog.present(self)

    def _cut_response(self, _dialog, response, j, connection=("auto", None), scan_only=False, corner=None):
        if response != "cut":
            return
        if design_review_pending(self):
            self.toast('Review the latest edits in Design.')
            return
        if self.pdf_bundle and not self.bundle_checked:
            self.toast('Check the matched PDF before cutting this sheet.')
            return
        refusal = cut_refusal(self.job, j, self._sending or self._checking_status)
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
        self.guide.invalidate()
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
                    sent = t.run(steps, wait_done=True) if corner and t.can_read else t.run(steps)
                message = ("Corner test sent." if corner else "Registration scan passed. No cut paths were sent."
                           if scan_only else f"Cut sent: {sent:,} bytes")
                GLib.idle_add(self.toast, message)
            except transport.RegistrationFailed as exc:
                GLib.idle_add(self.show_registration_failure, str(exc), j, matched, row, report,
                              mode, time.monotonic() - started)
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
        dialog.set_default_response("sheet")
        dialog.set_close_response("close")
        dialog.connect("response::copy", lambda *_: self.get_clipboard().set(diagnostics))
        dialog.connect("response::sheet", lambda *_: self.stack.set_visible_child_name("sheet"))
        dialog.present(self)

    def _send_done(self):
        self._sending = False
        self.rebuild()


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
