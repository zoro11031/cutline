"""cut-proxies: a GTK4 / libadwaita front end.

Four pages - Layout, Blade, Cut and Studio - around one live preview.

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

import math
import pathlib
import threading
import traceback

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from . import (config, geometry, graphtec, job as job_mod, presets, regmarks,
               template, transport, winboat)  # noqa: E402

APP_ID = "org.printing.CutProxies"


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


# --------------------------------------------------------------------------
# the preview
# --------------------------------------------------------------------------

class Preview(Gtk.DrawingArea):
    """Draws the sheet, the marks, their exclusion zones and the cut paths."""

    def __init__(self):
        super().__init__()
        self.job = None
        self.sheet = None          # a sheet.SheetReport, when a PDF is loaded
        self.set_draw_func(self._draw)
        self.set_content_width(360)
        self.set_content_height(460)
        self.set_hexpand(True)
        self.set_vexpand(True)

    def set_job(self, job):
        self.job = job
        self.queue_draw()

    def set_sheet(self, report):
        self.sheet = report
        self.queue_draw()

    def _draw(self, _area, cr, width, height):
        dark = Adw.StyleManager.get_default().get_dark()
        bg = (0.13, 0.13, 0.14) if dark else (0.95, 0.95, 0.96)
        cr.set_source_rgb(*bg)
        cr.paint()

        j = self.job
        if j is None:
            cr.set_source_rgb(0.5, 0.5, 0.52)
            cr.select_font_face("Sans")
            cr.set_font_size(13)
            msg = "Choose a layout to see the template"
            extents = cr.text_extents(msg)
            cr.move_to((width - extents.width) / 2, height / 2)
            cr.show_text(msg)
            return

        W, H = j.grid.sheet_w_mm, j.grid.sheet_h_mm
        pad = 16
        scale = min((width - 2 * pad) / W, (height - 2 * pad) / H)
        cr.translate((width - W * scale) / 2, (height - H * scale) / 2)
        cr.scale(scale, scale)
        hair = 1.0 / scale

        # the sheet
        cr.set_source_rgb(1, 1, 1)
        cr.rectangle(0, 0, W, H)
        cr.fill()
        cr.set_source_rgb(0.6, 0.6, 0.62)
        cr.set_line_width(hair)
        cr.rectangle(0, 0, W, H)
        cr.stroke()

        mk = j.marks

        # exclusion zones, so a collision is visible rather than described
        cr.set_source_rgba(0.9, 0.3, 0.2, 0.13)
        for z in mk.corner_zones():
            cr.rectangle(z.x, z.y, z.w, z.h)
            cr.fill()

        # registration marks, at their printed size
        cr.set_source_rgb(0, 0, 0)
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

        # card faces; the ones that crowd a mark are called out in amber so
        # the operator can see which to cover, and skipped slots are hollow.
        crowded = {h["index"] for h in j.mask_hints}
        skipped = set(j.skip)
        for i, rect in enumerate(j.grid.cards):
            if i in skipped:
                continue
            if i in crowded:
                cr.set_source_rgba(0.90, 0.64, 0.23, 0.38)
            else:
                cr.set_source_rgba(0.25, 0.5, 0.85, 0.12)
            cr.rectangle(rect.x, rect.y, rect.w, rect.h)
            cr.fill()

        # the cut paths, as flattened: this is what gets streamed
        bad = bool(j.blocked)
        cr.set_source_rgb(0.85, 0.2, 0.15) if bad else \
            cr.set_source_rgb(0.1, 0.55, 0.25)
        cr.set_line_width(max(hair, 0.25))
        for p in j.paths:
            pts = p.points
            if not pts:
                continue
            cr.move_to(*pts[0])
            for x, y in pts[1:]:
                cr.line_to(x, y)
            if p.closed:
                cr.close_path()
            cr.stroke()

        # where the marks actually are on a loaded PDF, against where the
        # template wants them. This is the whole alignment question, visible.
        if self.sheet is not None:
            for fm in self.sheet.marks:
                if fm.ink_box is not None:
                    b = fm.ink_box
                    good = fm.is_credible and (fm.error_mm or 0) <= 0.5
                    cr.set_source_rgba(*((0.1, 0.7, 0.3, 0.9) if good
                                         else (0.95, 0.25, 0.15, 0.95)))
                    cr.set_line_width(max(hair, 0.4))
                    cr.rectangle(b.x, b.y, b.w, b.h)
                    cr.stroke()
                if fm.measured is not None:
                    mx, my = fm.measured
                    cr.move_to(mx - 2, my)
                    cr.line_to(mx + 2, my)
                    cr.move_to(mx, my - 2)
                    cr.line_to(mx, my + 2)
                    cr.stroke()

        # the registration origin: every cut coordinate is measured from here
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
        super().__init__(application=app, title="Cut Proxies",
                         default_width=1120, default_height=760)
        self.job = None
        self.sheet_report = None
        self.pdf_path = None
        self.imported = None
        self._building = False
        self._suppress = True        # don't rebuild while constructing rows

        self.toasts = Adw.ToastOverlay()
        self.set_content(self.toasts)

        outer = Adw.ToolbarView()
        self.toasts.set_child(outer)

        header = Adw.HeaderBar()
        self.stack = Adw.ViewStack()
        switcher = Adw.ViewSwitcher(stack=self.stack,
                                    policy=Adw.ViewSwitcherPolicy.WIDE)
        header.set_title_widget(switcher)
        outer.add_top_bar(header)

        self.build_button = Gtk.Button(label="Check")
        self.build_button.add_css_class("suggested-action")
        self.build_button.connect("clicked", lambda *_: self.rebuild())
        header.pack_start(self.build_button)

        self.banner = Adw.Banner(revealed=False)
        outer.add_top_bar(self.banner)

        # preview on the right, pages on the left
        split = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        split.append(self.stack)
        self.stack.set_hexpand(True)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        right.set_size_request(400, -1)
        right.set_margin_top(12)
        right.set_margin_bottom(12)
        right.set_margin_start(6)
        right.set_margin_end(12)
        self.preview = Preview()
        frame = Gtk.Frame()
        frame.set_child(self.preview)
        right.append(frame)
        self.checks = Gtk.Label(xalign=0, wrap=True, selectable=True)
        self.checks.add_css_class("monospace")
        self.checks.add_css_class("dim-label")
        scroller = Gtk.ScrolledWindow(vexpand=False)
        scroller.set_size_request(-1, 190)
        scroller.set_child(self.checks)
        right.append(scroller)
        split.append(right)

        outer.set_content(split)

        self._page_layout()
        self._page_sheet()
        self._page_blade()
        self._page_cut()
        self._page_studio()

        self._suppress = False
        self.rebuild()

    # ---- pages -------------------------------------------------------
    def _page_layout(self):
        page = Adw.PreferencesPage()

        gsrc = Adw.PreferencesGroup(
            title="What to cut",
            description="Either a layout generated here, or the paths in an "
                        "SVG or DXF you already have — a Card Maker "
                        "template, an Inkscape sheet, anything.")
        self.import_row = Adw.ActionRow(
            title="Generated layout",
            subtitle="Using the sheet and card settings below")
        self.import_row.set_subtitle_lines(0)
        open_btn = Gtk.Button(label="Open SVG/DXF...")
        open_btn.set_valign(Gtk.Align.CENTER)
        open_btn.connect("clicked", self.on_open_artwork)
        clear_btn = Gtk.Button(label="Clear")
        clear_btn.set_valign(Gtk.Align.CENTER)
        clear_btn.connect("clicked", self.on_clear_artwork)
        self.import_row.add_suffix(open_btn)
        self.import_row.add_suffix(clear_btn)
        gsrc.add(self.import_row)
        self.r_colour = _combo(
            "Cut only this line colour", ["all colours"],
            subtitle="Studio's cut-by-colour: a file can carry both cut "
                     "lines and printed guides")
        self.r_colour.connect("notify::selected", self.on_changed)
        gsrc.add(self.r_colour)
        page.add(gsrc)

        g = Adw.PreferencesGroup(
            title="Sheet and cards",
            description="The grid has to match the sheet that was printed. "
                        "Pick the engine that produced it.")
        self.r_engine = _combo(
            "Layout engine",
            ["Silhouette Card Maker", "Proxy Press"],
            subtitle="Card Maker centres cards in an inset usable area; "
                     "Proxy Press centres the grid on the whole sheet")
        self.r_sheet = _combo("Sheet", sorted(geometry.SHEETS),
                              sorted(geometry.SHEETS).index("Letter"))
        self.r_card = _combo("Card size", sorted(geometry.CARDS),
                             sorted(geometry.CARDS).index("standard"))
        self.r_orient = _combo("Orientation",
                               ["Best fit", "Portrait", "Landscape"])
        g.add(self.r_engine)
        g.add(self.r_sheet)
        g.add(self.r_card)
        g.add(self.r_orient)
        page.add(g)

        g2 = Adw.PreferencesGroup(title="Cut")
        self.r_mode = _combo(
            "Cut mode", ["rounded", "square", "cross"],
            subtitle="cross cuts straight across for a corner punch")
        self.r_radius = _spin("Corner radius", 0, 10, 3.2, 0.1, 1,
                              subtitle="mm; 3.2 mm MTG standard, 3 mm for Kadomaru punches")
        g2.add(self.r_mode)
        g2.add(self.r_radius)
        page.add(g2)

        g3 = Adw.PreferencesGroup(
            title="Registration marks",
            description="These are printed, not cut. They must match what is "
                        "on the sheet exactly, or the sensor will not find "
                        "them.")
        self.r_pattern = _combo(
            "Pattern", ["three", "four", "proxypress"],
            subtitle="three = every Cameo; four = Cameo 5 Alpha only")
        self.r_inset = _spin("Inset from the paper edge", 2, 40, 10.0, 0.5, 1,
                             subtitle="mm; 10 standard, 3.5 borderless "
                                      "(needed for a 3x3 nine-card sheet)")
        self.r_length = _spin("Arm length", 0, 20, 0.0, 0.5, 1,
                              subtitle="mm; 0 means the longest that clears "
                                       "the cards")
        self.r_thickness = _spin("Line thickness", 0.5, 1.0, 1.0, 0.1, 1,
                                 subtitle="mm; the machine accepts 0.5 to 1.0")
        self.r_bleed = _spin("Bleed around each card", 0, 5, 0.0, 0.25, 2,
                             subtitle="mm; used for the collision check")
        self.r_skip = Adw.EntryRow(title="Skip slots")
        self.r_skip.set_tooltip_text(
            "Comma-separated 0-based card numbers to leave uncut, for slots "
            "printed empty so a card stops crowding a mark.")
        for r in (self.r_pattern, self.r_inset, self.r_length,
                  self.r_thickness, self.r_bleed, self.r_skip):
            g3.add(r)
        page.add(g3)

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
        page.add(g4)

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
        page = Adw.PreferencesPage()
        g = Adw.PreferencesGroup(
            title="The printed sheet",
            description="Measure where the registration marks actually are, "
                        "instead of trusting that the print matched the "
                        "template. This is the question the cutter's sensor "
                        "asks.")
        self.sheet_row = Adw.ActionRow(title="No PDF loaded",
                                       subtitle="Pick the sheet you printed")
        self.sheet_row.set_subtitle_lines(0)
        pick = Gtk.Button(label="Open PDF...")
        pick.set_valign(Gtk.Align.CENTER)
        pick.connect("clicked", self.on_pick_pdf)
        self.sheet_row.add_suffix(pick)
        g.add(self.sheet_row)

        self.r_sheet_page = _spin("Page", 1, 99, 1, 1, 0,
                                  subtitle="which page of the PDF to measure")
        self.r_sheet_dpi = _combo("Render resolution", ["150", "200", "300"], 1,
                                  subtitle="higher is slower and barely more "
                                           "accurate")
        g.add(self.r_sheet_page)
        g.add(self.r_sheet_dpi)

        remeasure = Adw.ActionRow(
            title="Measure again",
            subtitle="After changing the layout or the mark settings")
        again = Gtk.Button(label="Measure")
        again.set_valign(Gtk.Align.CENTER)
        again.connect("clicked", lambda *_: self.measure_sheet())
        remeasure.add_suffix(again)
        g.add(remeasure)
        page.add(g)

        g2 = Adw.PreferencesGroup(
            title="Result",
            description="Green boxes in the preview are marks found where "
                        "the template expects them; red are not.")
        self.sheet_result = Adw.ActionRow(title="Not measured", subtitle="")
        self.sheet_result.set_subtitle_lines(0)
        g2.add(self.sheet_result)

        adopt = Adw.ActionRow(
            title="Use the measured offset as calibration",
            subtitle="Only for a sheet whose marks are displaced together")
        self.adopt_btn = Gtk.Button(label="Adopt")
        self.adopt_btn.set_valign(Gtk.Align.CENTER)
        self.adopt_btn.set_sensitive(False)
        self.adopt_btn.connect("clicked", self.on_adopt_offset)
        adopt.add_suffix(self.adopt_btn)
        g2.add(adopt)
        page.add(g2)

        self.stack.add_titled_with_icon(page, "sheet", "Sheet",
                                        "document-print-symbolic")

    def _page_blade(self):
        page = Adw.PreferencesPage()
        names = sorted(presets.all_presets())
        default = (names.index(config.DEFAULT_PRESET)
                   if config.DEFAULT_PRESET in names else 0)
        g = Adw.PreferencesGroup(
            title="Preset",
            description="Every number below is an index the machine uses, not "
                        "grams or mm per second. The mapping is not published.")
        self.r_preset = _combo("Preset", names, default)
        self.r_preset.connect("notify::selected", self.on_preset)
        g.add(self.r_preset)
        self.preset_note = Adw.ActionRow(title="Provenance", subtitle="")
        self.preset_note.set_subtitle_lines(0)
        g.add(self.preset_note)
        page.add(g)

        g2 = Adw.PreferencesGroup(
            title="Blade",
            description="Tuning order: passes, then force, then speed, then "
                        "depth. On heavy stock, multi-pass cuts cleaner. If "
                        "laminate edges ripple, reduce force. If machine "
                        "skips, reduce speed.")
        self.r_cutter = _combo(
            "Cutter", [d.name for d in graphtec.DEVICES],
            [d.name for d in graphtec.DEVICES].index("Silhouette Cameo 4"))
        self.r_force = _spin("Force", 1, 40, 30, 1, 0,
                             subtitle="1-33 on a Cameo 4; 40 on the Alpha")
        self.r_speed = _spin("Speed", 1, 30, 3, 1, 0,
                             subtitle="Silhouette's own tables never exceed "
                                      "10; the community guide uses 25")
        self.r_passes = _spin("Passes", 1, 10, 3, 1, 0,
                              subtitle="tune this first, before force")
        self.r_depth = _spin("AutoBlade depth", 0, 10, 7, 1, 0,
                             subtitle="0 means do not send a depth. A "
                                      "ceiling, not a cut amount.")
        for r in (self.r_cutter, self.r_force, self.r_speed, self.r_passes,
                  self.r_depth):
            g2.add(r)
        page.add(g2)

        g3 = Adw.PreferencesGroup(
            title="Silhouette's own material settings",
            description="Read from Studio's condition.csv. Coverstock is the "
                        "heaviest paper they publish a setting for, and this "
                        "suite prints on something heavier.")
        self.r_material = _combo("Material", ["(none loaded)"])
        apply_row = Adw.ActionRow(
            title="Apply the selected material",
            subtitle="Overwrites force, speed, passes and depth")
        apply_btn = Gtk.Button(label="Apply")
        apply_btn.set_valign(Gtk.Align.CENTER)
        apply_btn.connect("clicked", self.on_apply_material)
        apply_row.add_suffix(apply_btn)
        g3.add(self.r_material)
        g3.add(apply_row)
        page.add(g3)
        self._load_materials()

        self.r_cutter.connect("notify::selected", self.on_changed)
        for r in (self.r_force, self.r_speed, self.r_passes, self.r_depth):
            r.connect("notify::value", self.on_changed)

        self.stack.add_titled_with_icon(page, "blade", "Blade",
                                        "applications-engineering-symbolic")
        self.on_preset()

    def _page_cut(self):
        page = Adw.PreferencesPage()
        g = Adw.PreferencesGroup(
            title="Registration",
            description="The cutter finds the printed marks, then treats the "
                        "top-left mark as the origin of every coordinate it "
                        "is given.")
        self.r_register = _switch(
            "Scan for the marks", True,
            subtitle="Off cuts in sheet coordinates. Only for a blank sheet.")
        self.r_auto = _switch(
            "Search automatically", True,
            subtitle="Off expects you to park the head on the first mark "
                     "yourself")
        g.add(self.r_register)
        g.add(self.r_auto)
        page.add(g)
        self.r_register.connect("notify::active", self.on_changed)
        self.r_auto.connect("notify::active", self.on_changed)

        g2 = Adw.PreferencesGroup(title="Cutter")
        self.status_row = Adw.ActionRow(title="Status", subtitle="not checked")
        refresh = Gtk.Button(label="Refresh")
        refresh.set_valign(Gtk.Align.CENTER)
        refresh.connect("clicked", self.on_refresh_status)
        self.status_row.add_suffix(refresh)
        g2.add(self.status_row)
        page.add(g2)

        gcal = Adw.PreferencesGroup(
            title="Calibration",
            description="The standing error between where the sensor sees "
                        "the marks and where the tool lands. Measure it once "
                        "with the pen preset, or adopt it from a measured "
                        "sheet.")
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
        page.add(gcal)
        for r in (self.r_cal_x, self.r_cal_y):
            r.connect("notify::value", self.on_changed)

        gtest = Adw.PreferencesGroup(
            title="Find your settings",
            description="A grid of small squares, each cut with its own "
                        "force and pass count, on a blank offcut. Lift a "
                        "corner of each without ejecting the mat.")
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
        page.add(gtest)

        g3 = Adw.PreferencesGroup(
            title="Send",
            description="Cut stays locked until the job has been built and "
                        "every check has passed for exactly these settings.")
        dump = Adw.ActionRow(
            title="Save the raw stream",
            subtitle="Write the bytes to a file instead of cutting")
        dump_btn = Gtk.Button(label="Save as...")
        dump_btn.set_valign(Gtk.Align.CENTER)
        dump_btn.connect("clicked", self.on_dump)
        dump.add_suffix(dump_btn)
        g3.add(dump)

        cut = Adw.ActionRow(title="Register and cut",
                            subtitle="This moves the blade")
        self.cut_button = Gtk.Button(label="Cut")
        self.cut_button.add_css_class("destructive-action")
        self.cut_button.set_valign(Gtk.Align.CENTER)
        self.cut_button.set_sensitive(False)
        self.cut_button.connect("clicked", self.on_cut)
        cut.add_suffix(self.cut_button)
        g3.add(cut)
        page.add(g3)

        self.stack.add_titled_with_icon(page, "cut", "Cut",
                                        "media-playback-start-symbolic")

    def _page_studio(self):
        page = Adw.PreferencesPage()
        g = Adw.PreferencesGroup(
            title="Silhouette Studio in the WinBoat guest",
            description="The fallback when the direct USB path will not "
                        "register or will not cut. Also the reference to "
                        "capture traffic against.")
        self.guest_row = Adw.ActionRow(title="Guest", subtitle="not checked")
        self.guest_row.set_subtitle_lines(0)
        check = Gtk.Button(label="Check")
        check.set_valign(Gtk.Align.CENTER)
        check.connect("clicked", self.on_check_guest)
        self.guest_row.add_suffix(check)
        g.add(self.guest_row)

        prep = Adw.ActionRow(
            title="Stage the guest helper",
            subtitle="Writes a PowerShell script into the shared folder that "
                     "reports what the guest can see. Installs nothing.")
        prep_btn = Gtk.Button(label="Stage")
        prep_btn.set_valign(Gtk.Align.CENTER)
        prep_btn.connect("clicked", self.on_prepare_guest)
        prep.add_suffix(prep_btn)
        g.add(prep)

        launch_row = Adw.ActionRow(
            title="Launch Studio (blank)",
            subtitle="Opens Silhouette Studio without opening a template")
        launch_btn = Gtk.Button(label="Launch")
        launch_btn.set_valign(Gtk.Align.CENTER)
        launch_btn.connect("clicked", self.on_launch_blank_studio)
        launch_row.add_suffix(launch_btn)
        g.add(launch_row)

        open_row = Adw.ActionRow(
            title="Export and open in Studio",
            subtitle="Writes the template, stages it, and opens it as a "
                     "RemoteApp")
        open_btn = Gtk.Button(label="Open")
        open_btn.set_valign(Gtk.Align.CENTER)
        open_btn.connect("clicked", self.on_open_studio)
        open_row.add_suffix(open_btn)
        g.add(open_row)
        page.add(g)
        self.stack.add_titled_with_icon(page, "studio", "Studio",
                                        "computer-symbolic")

    # ---- reading the controls ----------------------------------------
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
        return dict(
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
                   if self.r_depth.get_value() > 0 else None),
            bleed_mm=self.r_bleed.get_value(),
            registration=self.r_register.get_active(),
            auto_search=self.r_auto.get_active(),
            skip=self._skip_indices(),
            offset_mm=(self.r_cal_x.get_value(), self.r_cal_y.get_value()),
            imported=self._selected_artwork(),
        )

    def _selected_artwork(self):
        """The imported drawing, narrowed to one line colour if chosen."""
        if self.imported is None:
            return None
        choice = self._text(self.r_colour)
        if choice == "all colours":
            return self.imported
        try:
            return self.imported.only(choice)
        except Exception:                         # noqa: BLE001
            return self.imported

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
        # Any change invalidates the checked job, so the Cut button has to go
        # back to insensitive before anything else happens.
        self.job = None
        self.cut_button.set_sensitive(False)
        self.rebuild()

    def on_preset(self, *_a):
        p = presets.get(self._text(self.r_preset))
        flag = {"measured": "measured on a real cut",
                "silhouette": "from Silhouette's published settings",
                "recovered": "from the recovered media table"}.get(
                    p.origin, "EXTRAPOLATED - never cut")
        self.preset_note.set_subtitle(f"{flag}\n\n{p.note}")
        was = self._suppress
        self._suppress = True
        self.r_force.set_value(p.settings.get("force", 20))
        self.r_speed.set_value(p.settings.get("speed", 5))
        self.r_passes.set_value(p.settings.get("passes", 1))
        self.r_depth.set_value(p.settings.get("depth") or 0)
        self._suppress = was
        self.on_changed()

    def rebuild(self):
        if self._suppress or self._building:
            return
        self._building = True
        opts = self.options()
        try:
            j = job_mod.build(**opts)
        except geometry.LayoutImpossible as exc:
            self.job = None
            self.preview.set_job(None)
            self.checks.set_text("")
            self.banner.set_title(str(exc))
            self.banner.set_revealed(True)
            self.cut_button.set_sensitive(False)
            self._building = False
            return
        except Exception as exc:              # noqa: BLE001
            self.job = None
            self.preview.set_job(None)
            self.checks.set_text(traceback.format_exc())
            self.banner.set_title(f"Could not build the job: {exc}")
            self.banner.set_revealed(True)
            self.cut_button.set_sensitive(False)
            self._building = False
            return

        self.job = j
        self.preview.set_job(j)

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
            lines.append("COVER FOR THE SCAN, UNCOVER BEFORE THE CUT:")
            for h in j.mask_hints:
                lines.append(f"  {h['text']}")
            lines.append("  A sticky note is enough. The sensor can read a "
                         "dark card edge")
            lines.append("  as part of the mark and land the whole cut wrong.")
        self.checks.set_text("\n".join(lines))

        blocked = j.blocked
        if blocked:
            self.banner.set_title(f"{len(blocked)} problem(s): {blocked[0]}")
            self.banner.set_revealed(True)
        elif not self.r_register.get_active():
            self.banner.set_title(
                "Registration is OFF: blade will cut in sheet coordinates (for blank sheets only)")
            self.banner.set_revealed(True)
        elif j.warnings:
            self.banner.set_title(j.warnings[0])
            self.banner.set_revealed(True)
        elif j.mask_hints:
            self.banner.set_title(
                f"{len(j.mask_hints)} card(s) crowd a registration mark — "
                f"cover them for the scan, uncover before the cut")
            self.banner.set_revealed(True)
        else:
            self.banner.set_revealed(False)

        self.cut_button.set_sensitive(not blocked and j.settings.passes > 0)
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
        filt.set_name("Cuttable vector (SVG, DXF)")
        filt.add_pattern("*.svg")
        filt.add_pattern("*.dxf")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(filt)
        dialog.set_filters(filters)

        def done(d, result):
            try:
                gfile = d.open_finish(result)
            except GLib.Error:
                return
            path = pathlib.Path(gfile.get_path())
            from . import importer
            try:
                self.imported = importer.load(path)
            except Exception as exc:              # noqa: BLE001
                self.imported = None
                self.toast(f"Could not read {path.name}: {exc}")
                return
            self.import_row.set_title(path.name)
            lines = [self.imported.describe()] + self.imported.warnings
            self.import_row.set_subtitle("\n".join(lines))
            cols = list(self.imported.colours())
            was, self._suppress = self._suppress, True
            self.r_colour.set_model(
                Gtk.StringList.new(["all colours"] + cols))
            self.r_colour.set_selected(0)
            self._suppress = was
            self.on_changed()

        dialog.open(self, None, done)

    def on_clear_artwork(self, *_a):
        if self.imported is None:
            return
        self.imported = None
        was, self._suppress = self._suppress, True
        self.r_colour.set_model(Gtk.StringList.new(["all colours"]))
        self.r_colour.set_selected(0)
        self._suppress = was
        self.import_row.set_title("Generated layout")
        self.import_row.set_subtitle(
            "Using the sheet and card settings below")
        self.on_changed()

    # ---- the printed sheet -------------------------------------------
    def on_pick_pdf(self, *_a):
        dialog = Gtk.FileDialog(title="Open the printed sheet")
        filt = Gtk.FileFilter()
        filt.set_name("PDF")
        filt.add_mime_type("application/pdf")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(filt)
        dialog.set_filters(filters)

        def done(d, result):
            try:
                gfile = d.open_finish(result)
            except GLib.Error:
                return
            self.pdf_path = pathlib.Path(gfile.get_path())
            self.sheet_row.set_title(self.pdf_path.name)
            self.sheet_row.set_subtitle(str(self.pdf_path.parent))
            self.measure_sheet()

        dialog.open(self, None, done)

    def measure_sheet(self):
        path = getattr(self, "pdf_path", None)
        if path is None or self.job is None:
            self.toast("Load a PDF and fix the layout first.")
            return
        self.sheet_result.set_title("Measuring...")
        self.sheet_result.set_subtitle("")
        marks = self.job.marks
        grid = self.job.grid
        page = int(self.r_sheet_page.get_value())
        dpi = int(self._text(self.r_sheet_dpi))

        def work():
            from . import sheet as sheet_mod
            try:
                report = sheet_mod.measure(path, marks, page=page, dpi=dpi)
            except Exception as exc:              # noqa: BLE001
                GLib.idle_add(self._sheet_failed, str(exc))
                return
            problems = report.problems(grid.sheet_w_mm, grid.sheet_h_mm)
            GLib.idle_add(self._sheet_done, report, problems)

        threading.Thread(target=work, daemon=True).start()

    def _sheet_failed(self, message):
        self.sheet_report = None
        self.preview.set_sheet(None)
        self.sheet_result.set_title("Could not measure")
        self.sheet_result.set_subtitle(message)
        self.adopt_btn.set_sensitive(False)
        self.rebuild()

    def _sheet_done(self, report, problems):
        self.sheet_report = report
        self.preview.set_sheet(report)
        lines = [m.describe() for m in report.marks]
        lines.append("")
        lines.append(report.diagnosis())
        for pr in problems:
            lines.append(f"PROBLEM: {pr}")
        self.sheet_result.set_title(
            "Sheet matches the template" if not problems
            else f"{len(problems)} problem(s)")
        self.sheet_result.set_subtitle("\n".join(lines))
        # Only a uniform displacement can be fixed with an offset.
        self.adopt_btn.set_sensitive(
            bool(report.found) and "correctable" in report.diagnosis())
        self.rebuild()

    def on_adopt_offset(self, *_a):
        r = getattr(self, "sheet_report", None)
        if r is None or not r.found:
            return
        dx = sum(m.delta_mm[0] for m in r.found) / len(r.found)
        dy = sum(m.delta_mm[1] for m in r.found) / len(r.found)
        # The cut must move the way the print moved.
        config.save_cut_offset(round(dx, 3), round(dy, 3))
        self.toast(f"Calibration set to {dx:+.2f}, {dy:+.2f} mm")
        self.rebuild()

    # ---- Silhouette's own materials ----------------------------------
    def _load_materials(self):
        self._materials = {}
        try:
            from . import materials
            self._materials = materials.by_name()
        except Exception:                         # noqa: BLE001
            pass
        names = sorted(self._materials) or ["(no Studio installation found)"]
        self.r_material.set_model(Gtk.StringList.new(names))
        if "Coverstock" in self._materials:
            self.r_material.set_selected(names.index("Coverstock"))

    def on_apply_material(self, *_a):
        name = self._text(self.r_material)
        m = self._materials.get(name)
        if m is None:
            self.toast("No Silhouette material database found. Run "
                       "'cut-proxies studio --run-helper' first.")
            return
        was, self._suppress = self._suppress, True
        self.r_force.set_value(m.force)
        self.r_speed.set_value(m.speed)
        self.r_passes.set_value(m.passes)
        self.r_depth.set_value(m.autoblade_depth or 0)
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

    def on_test_cut(self, *_a):
        from . import testcut
        device = graphtec.device_by_name(self._text(self.r_cutter))
        base = presets.get(self._text(self.r_preset)).cut_settings()
        try:
            ts = testcut.build(
                device, base,
                forces=self.r_test_force.get_text(),
                passes=self.r_test_passes.get_text(),
                sheet_w_mm=self.job.grid.sheet_w_mm if self.job else 215.9,
                sheet_h_mm=self.job.grid.sheet_h_mm if self.job else 279.4,
                offset_mm=config.cut_offset())
        except ValueError as exc:
            self.toast(str(exc))
            return
        rows = "\n".join(f"{label}  {desc}" for label, desc in ts.map_rows())
        dialog = Adw.AlertDialog(
            heading=f"Cut {len(ts.cells)} test squares?",
            body=("This moves the blade. Put a blank offcut of the real "
                  "stock on the mat; registration is off.\n\n" + rows))
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("cut", "Cut")
        dialog.set_response_appearance("cut",
                                       Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.connect("response", self._test_cut_response, ts)
        dialog.present(self)

    def _test_cut_response(self, _dialog, response, ts):
        if response != "cut":
            return

        def work():
            try:
                with transport.open_transport("auto") as tr:
                    sent = tr.run(ts.steps)
                GLib.idle_add(self.toast, f"Test cut sent: {sent:,} bytes")
            except Exception as exc:              # noqa: BLE001
                GLib.idle_add(self.toast, f"Not cut: {exc}")

        threading.Thread(target=work, daemon=True).start()

    def on_refresh_status(self, *_a):
        self.status_row.set_subtitle("checking...")

        def work():
            found = transport.find_devices()
            if not found:
                text = ("no Silhouette cutter on the USB bus; "
                        "--dry-run and Studio still work")
            else:
                dev, how = found[0]
                text = f"{dev.name} ({dev.usb_id}) - {how}"
                try:
                    with transport.open_transport("auto") as t:
                        text += f"; {t.name} says {t.status()}"
                except transport.TransportError as exc:
                    text += f"; cannot open it: {exc}"
            GLib.idle_add(self.status_row.set_subtitle, text)

        threading.Thread(target=work, daemon=True).start()

    def on_cut(self, *_a):
        j = self.job
        if j is None or j.blocked:
            self.toast("The job does not check out.")
            return
        body = ("This moves the blade.\n\n"
                f"{j.grid.describe()}\n"
                f"{j.preset.name}: force {j.settings.force}, speed "
                f"{j.settings.speed}, {j.settings.passes} pass(es)\n\n")
        if j.preset.origin != "measured":
            body += ("This preset has never been cut. Run the plain-paper "
                     "test preset first.\n\n")
        body += "The cutter will search for the printed marks first."
        dialog = Adw.AlertDialog(heading="Register and cut?", body=body)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("cut", "Cut")
        dialog.set_response_appearance("cut",
                                       Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.connect("response", self._cut_response)
        dialog.present(self)

    def _cut_response(self, _dialog, response):
        if response != "cut":
            return
        j = self.job
        self.cut_button.set_sensitive(False)

        def work():
            try:
                with transport.open_transport("auto") as t:
                    if t.can_read:
                        state = t.status()
                        if state == "unloaded":
                            raise transport.TransportError(
                                "the cutter says no media is loaded")
                        if state == "moving":
                            raise transport.TransportError(
                                "the cutter is busy")
                    window = regmarks.scan_window(j.marks)
                    cj = graphtec.CutJob(
                        j.device, j.settings, origin_mm=j.marks.origin_mm,
                        reg_width_mm=j.marks.reg_width_mm,
                        reg_length_mm=j.marks.reg_length_mm,
                        registration=True, auto_search=self.r_auto.get_active(),
                        quad=window["quad"])
                    sent = t.run(cj.plan(j.paths))
                GLib.idle_add(self.toast, f"Cut sent: {sent:,} bytes")
            except transport.TransportError as exc:
                GLib.idle_add(self.toast, f"Not cut: {exc}")
            except Exception as exc:              # noqa: BLE001
                GLib.idle_add(self.toast, f"Not cut: {exc}")
            GLib.idle_add(self.rebuild)

        threading.Thread(target=work, daemon=True).start()

    def on_check_guest(self, *_a):
        self.guest_row.set_subtitle("checking...")

        def work():
            g = winboat.status()
            bits = [
                f"container: {'running' if g.container_running else 'stopped'}",
                f"studio: {g.studio_state}",
                f"usb passthrough: {'yes' if g.usb_passthrough else 'no'}",
                f"rdp: port {g.rdp_port or '?'}, "
                + ("reachable" if g.rdp_reachable else "not listening")
                + f", session {g.rdp_in_use or 'free'}",
            ]
            if not g.ready_for_studio:
                bits.append(f"not ready: {g.why_not_ready()}")
            if g.share_host_path:
                bits.append(f"share: {g.share_host_path} -> "
                            f"{winboat.GUEST_SHARE}")
            bits += [f"- {d}" for d in g.detail]
            GLib.idle_add(self.guest_row.set_subtitle, "\n".join(bits))

        threading.Thread(target=work, daemon=True).start()

    def on_prepare_guest(self, *_a):
        try:
            info = winboat.prepare()
        except Exception as exc:              # noqa: BLE001
            self.toast(f"Could not stage the helper: {exc}")
            return
        self.guest_row.set_subtitle("\n".join(info["steps"]))
        self.toast(f"Staged {info['script'].name}")

    def on_launch_blank_studio(self, *_a):
        try:
            cmd = winboat.open_in_studio(None)
        except Exception as exc:              # noqa: BLE001
            self.toast(f"Could not launch Studio: {exc}")
            return
        self.toast("Opening Silhouette Studio in the guest")
        self.guest_row.set_subtitle(" ".join(cmd))

    def on_open_studio(self, *_a):
        if self.job is None:
            self.toast("Nothing to open: fix the layout first.")
            return
        j = self.job
        stem = (f"{j.grid.sheet_name.lower()}-{j.grid.card.name}-"
                f"{j.grid.cols}x{j.grid.rows}-{j.mode}")
        try:
            written = job_mod.write_templates(j, stem, folder=winboat.stage_dir(), proof=False)
            cmd = winboat.open_in_studio(written["dxf"])
        except Exception as exc:              # noqa: BLE001
            self.toast(f"Could not open Studio: {exc}")
            return
        b = j.grid.bounds
        self.toast(f"Staged {written['dxf'].name} & opening Studio")
        guide_text = (
            f"Command: {' '.join(cmd)}\n\n"
            f"In Silhouette Studio:\n"
            f"- Page Setup: {j.grid.sheet_name} {'Landscape' if not j.grid.portrait else 'Portrait'}\n"
            f"- Reg Marks: Type 1, Inset {j.marks.inset_mm:g} mm, Length {j.marks.reg_length_mm:g} mm\n"
            f"- Verify Size (Ctrl+A): {b.w:.2f} x {b.h:.2f} mm ({b.w / 25.4:.3f} x {b.h / 25.4:.3f} in)\n"
            f"- If scaled in Basic Edition: lock aspect ratio and set Width to {b.w:.2f} mm ({b.w / 25.4:.3f} in)\n"
            f"- Card size: {j.grid.card.width_mm:.1f} x {j.grid.card.height_mm:.1f} mm\n"
            f"- Settings: Force {j.settings.force}, Speed {j.settings.speed}, Passes {j.settings.passes}, Depth {j.settings.depth or 7}\n"
            f"- Sticky note hint: Cover bottom-left card during mark scan, remove before cutting"
        )
        self.guest_row.set_subtitle(guide_text)


class Application(Adw.Application):

    def __init__(self):
        super().__init__(application_id=APP_ID)

    def do_activate(self):
        win = self.props.active_window or Window(self)
        win.present()


def main(argv=None):
    app = Application()
    return app.run(argv or [])


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv))
