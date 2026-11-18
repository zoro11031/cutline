"""Qt Widgets frontend for the verified Silhouette desktop workflow."""

from __future__ import annotations

from contextlib import contextmanager
import io
from pathlib import Path
import sys
import tempfile
import zipfile

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog, QGroupBox, QHBoxLayout, QInputDialog,
                               QLabel, QMainWindow, QMessageBox, QPlainTextEdit,
                               QSplitter, QStackedWidget, QTabWidget, QVBoxLayout, QWidget)

from . import (actions, bluetooth, bundle, config, editor_model, geometry, graphtec, importer, job,
               layout_presets, materials, presets, regmarks, starting_guide, template, testcut, toolpath, transport, winboat)
from .qt_canvas import PREVIEW_MODES, Preview
from .qt_controls import Form, Page, Tasks, button, set_choices

APP_ID = "org.printing.SilhouetteControlCenter"
ARTWORK_FILTER = "Cut artwork (*.svg *.SVG *.dxf *.DXF *.studio3 *.STUDIO3)"
DEVICES = {device.name: device.name.removeprefix("Silhouette ") for device in graphtec.DEVICES}

# Qt widgets use the same field names and units as the job builder.
BLADE_NUMBERS = (
    ("force", "Force", 1, 40, 1, 0), ("speed", "Speed", 1, 30, 1, 0),
    ("passes", "Passes", 0, 10, 1, 0), ("depth", "AutoBlade depth (−1 to skip)", -1, 10, 1, 0),
    ("holder", "Tool holder", 1, 2, 1, 0), ("acceleration", "Acceleration (−1 for auto)", -1, 3, 1, 0),
    ("blade_diameter_mm", "Blade diameter (mm)", 0, 2.3, 0.05, 2),
    ("corner_start_mm", "Start Extension (mm)", 0, 0.9, 0.1, 1),
    ("corner_end_mm", "End Extension (mm)", 0, 0.9, 0.1, 1),
    ("overcut_mm", "Seam Overcut (mm)", 0, 1000, 0.1, 2),
)
BLADE_SWITCHES = (
    ("lift_head", "Lift head"), ("sharpen_corners", "Line Segment Overcut"), ("track_enhancing", "Track Enhancing"),
    ("interior_first", "Sort Interior Contours First"), ("delicate_media", "Delicate Media Path"),
    ("alternate_direction", "Alternate pass direction"),
)
BLADE_CHOICES = (
    ("path_order", "Cut Order Sorting", toolpath.PATH_ORDERS),
    ("pass_order", "Pass order", toolpath.PASS_ORDERS),
)


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Silhouette Control Center")
        self.resize(1160, 790)
        self.setWindowIcon(QIcon.fromTheme(APP_ID))
        self.tasks = Tasks(self)
        self.settings = config.load_settings()
        self.job = self.artwork = self.project_shapes = self.design = self.reviewed_design = None
        self.pdf = self.bundle = self.sheet_token = self.report = None
        self.bundle_checked = self.busy = self.loading = False
        self.guide = starting_guide.Guide()
        self.group_rows = {}
        self.group_choices = {}
        self.pages = {}
        self.controls = {}
        self.blade = {}

        self.workspaces = QStackedWidget()
        self.setCentralWidget(self.workspaces)
        setup = QWidget()
        self.workspaces.addWidget(setup)
        content = QVBoxLayout(setup)
        top = QHBoxLayout()
        self.design_button = button("Design", self.open_design)
        top.addWidget(self.design_button)
        top.addStretch(1)
        content.addLayout(top)
        split = QSplitter(Qt.Orientation.Horizontal)
        content.addWidget(split, 1)
        self.tabs = QTabWidget()
        self.tabs.currentChanged.connect(lambda: self.refresh_send())
        split.addWidget(self.tabs)

        preview_panel = QWidget()
        right = QVBoxLayout(preview_panel)
        right.setContentsMargins(4, 0, 0, 0)
        preview_controls = QHBoxLayout()
        self.preview_mode = QComboBox()
        set_choices(self.preview_mode, PREVIEW_MODES, "Overlay")
        self.preview_mode.currentIndexChanged.connect(lambda: self.change_preview())
        self.preview_mode.setAccessibleName("Sheet preview mode")
        self.preview_mode.hide()
        preview_controls.addWidget(self.preview_mode)
        fit = button("Fit", lambda: self.preview.fit())
        preview_controls.addWidget(fit)
        preview_controls.addStretch()
        self.colorblind = QCheckBox("Colorblind mode")
        self.colorblind.setChecked(bool(self.settings.get("colorblind_mode")))
        self.colorblind.toggled.connect(self.change_palette)
        preview_controls.addWidget(self.colorblind)
        right.addLayout(preview_controls)
        self.preview = Preview()
        self.preview.colorblind = self.colorblind.isChecked()
        right.addWidget(self.preview, 1)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        right.addWidget(self.summary)
        details_toggle = QCheckBox("Job details")
        right.addWidget(details_toggle)
        self.details = QPlainTextEdit(readOnly=True)
        self.details.setMaximumHeight(150)
        self.details.hide()
        details_toggle.toggled.connect(self.details.setVisible)
        right.addWidget(self.details)
        split.addWidget(preview_panel)
        split.setStretchFactor(1, 1)
        split.setSizes([420, 700])

        footer = QHBoxLayout()
        self.footer_status = QLabel()
        self.footer_status.setWordWrap(True)
        footer.addWidget(self.footer_status, 1)
        self.review_button = button("Review Send", self.follow_review)
        self.send_button = button("Send to Cutter", self.confirm_send)
        self.send_button.setMinimumHeight(32)
        primary = self.send_button.font()
        primary.setBold(True)
        self.send_button.setFont(primary)
        footer.addWidget(self.review_button)
        footer.addWidget(self.send_button)
        content.addLayout(footer)

        with self.editing():
            self.layout_page()
            self.sheet_page()
            self.blade_page()
            self.send_page()
            self.studio_page()
        self.rebuild()

    @contextmanager
    def editing(self):
        previous = self.loading
        self.loading = True
        try:
            yield
        finally:
            self.loading = previous

    def tab(self, name):
        return self.pages[name]

    def install_page(self, name, page):
        self.pages[name] = self.tabs.addTab(page.finish(), name)

    def choose(self, form, field, label, choices, default=None, changed=None):
        self.controls[field] = form.choice(label, choices, default, changed or self.rebuild)

    def number(self, form, field, label, default, low, high, step=1, decimals=0):
        self.controls[field] = form.number(label, default, low, high, step, decimals, self.rebuild)

    def check(self, form, field, label, default=False):
        self.controls[field] = form.check(label, default, self.rebuild)

    def choice(self, field):
        return self.controls[field].currentData()

    def select(self, field, value):
        widget = self.controls[field]
        widget.setCurrentIndex(max(0, widget.findData(value)))

    def layout_page(self):
        page = Page()
        source = page.group("Cut artwork")
        self.source_label = source.note("Generated card layout")
        source.push("Open SVG, DXF or Studio3…", self.open_artwork)
        source.push("Use Generated Layout", self.reset_artwork)
        self.choose(source, "colour", "Line color", {"": "All colors"})
        layout = page.group("Media and cards")
        self.choose(layout, "layout_preset", "Layout preset",
                    {"": "Custom", **{key: value.name for key, value in layout_presets.BUILTIN.items()}},
                    changed=self.apply_layout_preset)
        self.choose(layout, "sheet", "Sheet", sorted(geometry.SHEETS), config.DEFAULT_SHEET)
        self.choose(layout, "portrait", "Orientation", {"best": "Best fit", True: "Portrait", False: "Landscape"})
        self.choose(layout, "engine", "Layout engine", {"scm": "Silhouette Card Maker", "proxypress": "Proxy Press"})
        self.choose(layout, "card", "Card size", sorted(geometry.CARDS), config.DEFAULT_CARD)
        self.choose(layout, "mode", "Cut mode", dict(zip(template.MODES, ("Rounded", "Square", "Cross-cuts"))))
        self.number(layout, "radius_mm", "Corner radius (mm)", 3.2, 0, 10, 0.1, 1)
        marks = page.group("Registration Marks")
        self.choose(marks, "pattern", "Pattern", {regmarks.THREE: "Type 1 (3 marks)", regmarks.FOUR: "4 marks"})
        self.number(marks, "mark_inset_mm", "Inset (mm)", 10, 2, 40, 0.1, 2)
        self.number(marks, "mark_length_mm", "Length (mm, 0 for auto)", 0, 0, regmarks.MAX_LENGTH_MM, 0.1, 4)
        self.number(marks, "mark_thickness_mm", "Thickness (mm)", 1, regmarks.MIN_THICKNESS_MM, regmarks.MAX_THICKNESS_MM, 0.05, 2)
        self.number(marks, "bleed_mm", "Bleed (mm)", 0, 0, 5, 0.25, 3)
        self.skip = marks.text("Skip slots (0-based)", changed=self.rebuild)
        page.group("Templates").push("Export SVG, DXF and Proof…", self.export_job)
        self.install_page("Layout", page)

    def sheet_page(self):
        page = Page()
        form = page.group("Printed sheet")
        self.sheet_file = form.note("Open a print PDF or a matched ProxyPress ZIP.")
        form.push("Open PDF or ZIP…", self.open_sheet)
        self.sheet_page_number = form.number("Page", 1, 1, 999, changed=self.change_sheet_page)
        self.sheet_dpi = form.choice("Measurement resolution", (150, 200, 300), 200, self.measure_sheet)
        form.push("Measure Registration Marks", self.measure_sheet)
        result = page.group("Registration results")
        self.sheet_result = result.note("Not measured")
        self.scan_result = result.note("")
        self.install_page("Sheet", page)

    def blade_page(self):
        page = Page()
        profile = page.group("Material preset")
        self.choose(profile, "preset", "Preset", sorted(presets.all_presets()), config.DEFAULT_PRESET, self.load_preset)
        self.provenance = profile.note()
        profile.push("Save Preset", self.save_preset)
        profile.push("Save as New Preset…", self.save_new_preset)
        tool = page.group("Cutter and blade")
        self.choose(tool, "cutter", "Cutter", DEVICES, "Silhouette Cameo 4", self.change_cutter)
        for key, label, lo, hi, step, decimals in BLADE_NUMBERS:
            self.blade[key] = tool.number(label, lo, lo, hi, step, decimals, self.rebuild)
        for key, label in BLADE_SWITCHES[:3]:
            self.blade[key] = tool.check(label, changed=self.rebuild)
        path_settings = page.group("Cut paths")
        for key, label, choices in BLADE_CHOICES:
            self.blade[key] = path_settings.choice(label, choices, changed=self.rebuild)
        for key, label in BLADE_SWITCHES[3:]:
            self.blade[key] = path_settings.check(label, changed=self.rebuild)
        weed = page.group("Weed Settings")
        self.weed_box = weed.parentWidget()
        self.number(weed, "weed_border_mm", "Weed border padding (mm)", 0, 0, 1000, 0.5, 2)
        self.choose(weed, "weed_lines", "Weed Lines", toolpath.WEED_LINES)
        table = page.group("Silhouette material database")
        self.divisions = table.choice("Show Cutter Divisions", {False: "Known Cutters", True: "All Cutters"},
                                     changed=self.load_materials)
        self.material = table.choice("Material", ())
        table.push("Apply Material", self.apply_material)
        table.push("Restore Default Materials…", self.restore_materials)
        self.install_page("Blade", page)
        self.load_preset()
        self.load_materials()

    def send_page(self):
        page = Page()
        groups = page.group("Send by")
        self.group_box = groups.parentWidget()
        self.choose(groups, "send_mode", "Mode", actions.SEND_MODES, changed=self.change_group_mode)
        self.group_details = QWidget()
        self.group_layout = QVBoxLayout(self.group_details)
        self.group_layout.setContentsMargins(0, 0, 0, 0)
        groups.addRow(self.group_details)

        cutter = page.group("Connection")
        self.link = cutter.choice("Transport", {"auto": "USB", **bluetooth.BLUETOOTH_MODES},
                                  changed=self.change_connection)
        self.detected = cutter.choice("Bluetooth devices", {"": "Select a cutter"}, changed=self.device_selected)
        self.identifier = cutter.text("Bluetooth identifier")
        self.identifier.textChanged.connect(lambda: self.change_target())
        self.channel = cutter.number("RFCOMM channel", 1, 1, 30, changed=self.change_target)
        self.discover = cutter.push("Find Bluetooth Cutters", self.find_cutters)
        self.connection_status = cutter.note("Cutter status not checked.")
        cutter.push("Refresh Cutter Status", self.check_device)
        self.connection_form = cutter

        registration = page.group("Registration")
        self.check(registration, "registration", "Scan for printed marks", True)
        self.check(registration, "auto_search", "Search automatically", True)
        self.scan_button = registration.push("Test Registration", lambda: self.confirm_send(scan_only=True))

        media = page.group("Media handling")
        self.choose(media, "mat", "Cutting mat", graphtec.MAT_OPTIONS)
        self.choose(media, "end_position", "Feed Options", graphtec.END_POSITIONS)
        self.number(media, "end_feed_mm", "Feed Distance (mm)", 0, 0, 3000, 0.5, 2)
        self.weed_visibility = media.choice("Show Weed Settings", ("Auto", "Always", "Never"),
                                          self.settings.get("show_weed_settings"), self.update_weed_preference)
        guide = page.group("Starting Guide")
        guide.note(starting_guide.GUIDE_TEXT)
        self.guide_apply = guide.push("Apply Starting Settings", self.apply_guide_start)
        self.guide_status = guide.note()
        self.guide_send = guide.push("Cut One Pass…", self.confirm_guide_pass)
        self.guide_use = guide.push("Use Tested Settings", self.use_guide_settings)
        self.guide_next = guide.push("Next Test Spot", self.next_guide_spot)
        self.guide_reset = guide.push("New Test Sheet", self.reset_guide)
        calibration = page.group("Calibration")
        dx, dy = config.cut_offset()
        self.cal_x = calibration.number("Offset X (mm)", dx, -5, 5, 0.05, 2, self.rebuild)
        self.cal_y = calibration.number("Offset Y (mm)", dy, -5, 5, 0.05, 2, self.rebuild)
        calibration.push("Save Calibration", self.save_calibration)
        tests = page.group("Test-Cut Matrix")
        self.test_shape = tests.choice("Shape", testcut.TEST_SHAPES)
        self.test_force = tests.text("Force values", "25,29,33")
        self.test_passes = tests.text("Pass counts", "1,2,3,4")
        self.test_button = tests.push("Test Cut…", self.confirm_test_cut)
        page.group("Diagnostics").push("Save Raw Stream…", self.dump_stream)
        self.install_page("Send", page)
        self.change_connection()

    def studio_page(self):
        page = Page()
        form = page.group("Silhouette Studio via WinBoat")
        self.guest_status = form.note("Guest status not checked.")
        form.push("Check Guest", self.check_guest)
        form.push("Stage Guest Helper", self.prepare_guest)
        form.push("Launch Studio", lambda: self.open_in_studio(None))
        form.push("Open Current Sheet in Studio", self.open_current_in_studio)
        form.push("Open Studio File…", self.choose_studio_file)
        self.install_page("Studio", page)

    def change_preview(self):
        self.preview.mode = self.preview_mode.currentData()
        self.preview.render_sheet()

    def message(self, text):
        self.statusBar().showMessage(text, 9000)

    def save_settings(self, key, value):
        settings = config.load_settings()
        settings[key] = value
        try:
            config.save_settings(settings)
        except OSError as error:
            self.message(f"Could not save preference. {error}")

    def change_palette(self, enabled):
        self.preview.colorblind = enabled
        self.preview.render_sheet()
        self.save_settings("colorblind_mode", enabled)

    def update_weed_preference(self):
        mode = self.weed_visibility.currentData()
        self.update_enabled_fields()
        self.save_settings("show_weed_settings", mode)

    def load_preset(self):
        selected = presets.get(self.choice("preset"))
        with self.editing():
            self.provenance.setText(selected.note)
            settings = selected.cut_settings()
            for key, *_ in BLADE_NUMBERS:
                value = getattr(settings, key)
                self.blade[key].setValue(-1 if value is None else value)
            for key, _ in BLADE_SWITCHES:
                self.blade[key].setChecked(getattr(settings, key))
            for key, _, _ in BLADE_CHOICES:
                widget = self.blade[key]
                widget.setCurrentIndex(widget.findData(getattr(settings, key)))
        self.rebuild()

    def save_preset(self, name=None):
        values = self.blade_values()
        try:
            saved = presets.save_blade_settings(
                self.choice("preset"), new_name=name,
                **{key: values.pop(key) for key in ("force", "speed", "depth", "passes")},
                blade_options=values)
        except (OSError, ValueError) as error:
            self.message(str(error))
            return
        if saved is not None:
            set_choices(self.controls["preset"], sorted(presets.all_presets()), name.strip() if name else None)
            self.load_preset()
            if self.group_rows:
                self.change_group_mode()
        self.message("Preset saved." if saved else "Preset settings are already saved.")

    def save_new_preset(self):
        name, accepted = QInputDialog.getText(self, "Save as New Preset", "Preset name")
        if accepted:
            self.save_preset(name)

    def load_materials(self):
        try:
            self.materials = materials.cutter_divisions(
                materials.load(), self.choice("cutter"), self.divisions.currentData())
        except (OSError, ValueError):
            self.materials = {}
        set_choices(self.material, sorted(self.materials) or {"": "No Studio material database"})

    def change_cutter(self):
        self.load_materials()
        self.rebuild()

    def apply_material(self):
        selected = self.materials.get(self.material.currentData())
        if selected is None:
            self.message("Load Studio's material database through the WinBoat helper.")
            return
        with self.editing():
            self.blade["force"].setValue(selected.force)
            self.blade["speed"].setValue(selected.speed)
            self.blade["passes"].setValue(selected.passes)
            self.blade["depth"].setValue(-1 if selected.autoblade_depth is None else selected.autoblade_depth)
            self.blade["blade_diameter_mm"].setValue(selected.blade_diameter_mm)
        self.rebuild()

    def restore_materials(self):
        if not self.confirm("Restore Default Materials?",
                            "Edits to built-in presets will be backed up. Custom presets stay available."):
            return
        try:
            count, _ = presets.restore_default_materials()
        except OSError as error:
            self.message(str(error))
            return
        self.load_preset()
        self.message(f"Restored {count} built-in presets.")

    def save_calibration(self):
        try:
            config.save_cut_offset(self.cal_x.value(), self.cal_y.value())
        except OSError as error:
            self.message(str(error))
            return
        self.message("Calibration saved.")

    def apply_layout_preset(self):
        profile = layout_presets.BUILTIN.get(self.choice("layout_preset"))
        if profile is None:
            return
        settings = profile.options()
        self.reset_artwork()
        with self.editing():
            for key, value in (("sheet", settings["sheet"]), ("card", "standard"), ("engine", "proxypress"),
                               ("portrait", False), ("mode", template.ROUNDED), ("pattern", regmarks.THREE)):
                self.select(key, value)
            for key in ("radius_mm", "mark_inset_mm", "mark_length_mm", "mark_thickness_mm", "bleed_mm"):
                self.controls[key].setValue(settings[key])
            self.controls["registration"].setChecked(True)
            self.controls["auto_search"].setChecked(True)
            self.skip.clear()
        self.rebuild()

    def open_artwork(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open Cut Artwork", "", ARTWORK_FILTER)
        if path:
            self.load_artwork(Path(path))

    def load_artwork(self, path, page=None):
        try:
            drawing = importer.load(path, page=page)
        except importer.InkscapePageRequired as error:
            page, accepted = QInputDialog.getInt(self, "Choose an Inkscape Page", "Page", 1, 1, error.count)
            if accepted:
                self.load_artwork(path, page)
            return
        except (OSError, ValueError) as error:
            self.message(str(error))
            return
        self.clear_bundle()
        self.project_shapes = self.reviewed_design = None
        self.use_artwork(drawing, path.name + (f" · Page {page}" if page else ""))

    def reset_artwork(self):
        self.clear_bundle()
        self.project_shapes = self.reviewed_design = None
        self.use_artwork(None, "Generated card layout")

    def use_artwork(self, drawing, label, send_mode="simple"):
        self.artwork = drawing
        self.group_choices.clear()
        self.source_label.setText(label + ("\n" + drawing.describe() if drawing else ""))
        if drawing and drawing.warnings:
            self.source_label.setText(self.source_label.text() + "\n" + "\n".join(drawing.warnings))
        sizes = sorted(geometry.SHEETS)
        document_size = drawing is not None and drawing.use_document_page
        if document_size:
            sizes.append("Document Size")
        with self.editing():
            selected = self.choice("sheet")
            set_choices(self.controls["sheet"], sizes, "Document Size" if document_size else (
                selected if selected in sizes else config.DEFAULT_SHEET))
            set_choices(self.controls["colour"], {"": "All colors", **(
                {name: name for name in drawing.colours()} if drawing else {})}, "")
            self.select("send_mode", send_mode)
        self.change_group_mode(keep=False)

    def change_group_mode(self, keep=True):
        if keep and self.loading:
            return
        if keep and self.group_rows:
            self.group_choices[self.rendered_group_mode] = {
                key: (enable.isChecked(), profile.currentData(), holder.currentData(), order.value())
                for key, (enable, profile, holder, order) in self.group_rows.items()}
        while self.group_layout.count():
            self.group_layout.takeAt(0).widget().deleteLater()
        self.group_rows.clear()
        self.rendered_group_mode = mode = self.choice("send_mode") if self.artwork else "simple"
        if mode != "simple":
            groups = actions.group_paths(self.artwork.paths, mode)
            saved = self.group_choices.get(mode, {})
            for number, (key, paths) in enumerate(groups.items(), 1):
                box = QGroupBox(f"{key} · {len(paths)} paths")
                self.group_layout.addWidget(box)
                form = Form(box)
                enabled, profile, holder, order = saved.get(
                    key, (False, actions.CURRENT_SETTINGS, None, number))
                self.group_rows[key] = (
                    form.check("Send this group", enabled, self.rebuild),
                    form.choice("Preset", (actions.CURRENT_SETTINGS, *sorted(presets.all_presets())),
                                profile, self.rebuild),
                    form.choice("Tool holder", {None: "Use preset", 1: "1", 2: "2"}, holder, self.rebuild),
                    form.number("Send order", order, 1, len(groups), changed=self.rebuild))
        self.rebuild()

    def group_assignments(self):
        rows = sorted(self.group_rows.items(), key=lambda item: item[1][-1].value())
        return tuple(actions.Assignment(key, profile.currentData(), holder.currentData())
                     for key, (enabled, profile, holder, _) in rows if enabled.isChecked())

    def blade_values(self):
        values = {key: self.blade[key].value() for key, *_ in BLADE_NUMBERS}
        values.update({key: self.blade[key].isChecked() for key, _ in BLADE_SWITCHES})
        values.update({key: self.blade[key].currentData() for key, _, _ in BLADE_CHOICES})
        for name in ("depth", "acceleration"):
            if values[name] < 0:
                values[name] = None
        return values

    def job_options(self):
        mode = self.choice("send_mode") if self.artwork else "simple"
        artwork = self.artwork
        if artwork and mode == "simple" and self.choice("colour") and not self.bundle:
            artwork = artwork.only(self.choice("colour"))
        names = ("card", "engine", "cutter", "mode", "pattern", "mat", "end_position")
        options = {key: self.choice(key) for key in names}
        options["preset_name"] = self.choice("preset")
        options.update({key: self.controls[key].value() for key in (
            "radius_mm", "mark_inset_mm", "mark_length_mm", "mark_thickness_mm", "bleed_mm", "end_feed_mm")})
        options["mark_length_mm"] = options["mark_length_mm"] or None
        options["auto_search"] = self.controls["auto_search"].isChecked()
        options["registration"] = self.controls["registration"].isChecked()
        values = self.blade_values()
        for key in ("force", "speed", "passes", "depth"):
            options[key] = values.pop(key)
        device = graphtec.device_by_name(options["cutter"])
        if not device.two_tools:
            values["holder"] = 1
        if not device.autoblade or values["holder"] != 1:
            options["depth"] = None
        options["blade_options"] = values
        portrait = self.choice("portrait")
        source_sheet = self.choice("sheet")
        skip = tuple(int(s.strip()) for s in self.skip.text().replace(";", ",").split(",") if s.strip().isdigit())
        options.update(imported=artwork, sheet=config.DEFAULT_SHEET if source_sheet == "Document Size" else source_sheet,
                       portrait=portrait if type(portrait) is bool else None, skip=skip,
                       use_document_page=source_sheet == "Document Size", offset_mm=(self.cal_x.value(), self.cal_y.value()),
                       send_mode=mode, assignments=self.group_assignments() if mode != "simple" else ())
        if options["end_position"] != "below":
            options["end_feed_mm"] = 0
        if artwork:
            options["weed_border_mm"] = self.controls["weed_border_mm"].value()
            options["weed_lines"] = self.choice("weed_lines") if options["weed_border_mm"] else "none"
        if self.bundle:
            options.update(self.bundle.layout, engine="scm", mode=template.ROUNDED, bleed_mm=0, registration=True,
                           skip=(), use_document_page=False, imported=self.artwork)
        return options

    def update_enabled_fields(self):
        device = graphtec.device_by_name(self.choice("cutter"))
        self.blade["holder"].setEnabled(device.two_tools)
        self.blade["depth"].setEnabled(device.autoblade and self.blade["holder"].value() == 1)
        self.blade["acceleration"].setEnabled(device.detects_tools)
        self.blade["lift_head"].setEnabled(device.cameo3_on)
        corners = device.cameo3_on and self.blade["blade_diameter_mm"].value() > 0
        self.blade["sharpen_corners"].setEnabled(corners)
        for field in ("corner_start_mm", "corner_end_mm"):
            self.blade[field].setEnabled(corners and self.blade["sharpen_corners"].isChecked())
        weed = self.weed_visibility.currentData()
        self.weed_box.setVisible(weed == "Always" or (weed == "Auto" and self.artwork is not None))
        self.controls["weed_border_mm"].setEnabled(self.artwork is not None)
        self.controls["weed_lines"].setEnabled(
            self.artwork is not None and self.controls["weed_border_mm"].value() > 0)
        self.controls["colour"].setEnabled(
            self.artwork is not None and self.choice("send_mode") == "simple" and self.bundle is None)
        self.controls["end_feed_mm"].setEnabled(self.choice("end_position") == "below")
        self.controls["auto_search"].setEnabled(self.controls["registration"].isChecked())
        self.group_box.setVisible(self.artwork is not None)

    def rebuild(self):
        if self.loading:
            return
        self.update_enabled_fields()
        previous = self.job
        self.job = None
        problems = []
        if self.bundle and self.artwork is None:
            summary = "Open this ZIP's paired template in Studio."
        else:
            try:
                self.job = job.build(**self.job_options())
            except (ValueError, KeyError) as error:
                summary = str(error)
            else:
                current = self.job
                problems = current.blocked
                passes = "pass" if current.settings.passes == 1 else "passes"
                caption = f"{len(current.action_groups)} Send groups" if current.action_groups else (
                    f"{current.preset.name} · {current.settings.passes} {passes}")
                summary = (f"{current.grid.sheet_w_mm:g} × {current.grid.sheet_h_mm:g} mm · "
                           f"{len(current.paths)} paths\n{caption}")
                if problems or current.warnings:
                    summary += "\n" + (problems or current.warnings)[0]
        marks = self.job.marks if self.job else None
        if self.pdf and not self.bundle and previous and previous.marks != marks:
            self.invalidate_measurement()
            self.sheet_result.setText("Registration geometry changed. Measure the PDF again.")
        self.summary.setText(summary)
        diagnostics = ([f"{key}: {value}" for key, value in self.job.summary()] +
                       [f"{'OK' if row.ok else 'FAIL'} · {row.label}: {row.got}" for row in self.job.rows] +
                       list(dict.fromkeys((*problems, *self.job.warnings, *(h["text"] for h in self.job.mask_hints))))
                       if self.job else [summary])
        self.details.setPlainText("\n".join(diagnostics))
        self.preview.show_job(self.job)
        self.refresh_send()

    def pending_design(self):
        return self.reviewed_design is not None and self.design is not None and self.design.project != self.reviewed_design

    def review_tab(self):
        if self.pending_design():
            return "Design"
        if self.bundle:
            if self.artwork is None:
                return "Studio"
            if not self.bundle_checked:
                return "Sheet"
        return "Send"

    def follow_review(self):
        target = self.review_tab()
        if target == "Design":
            self.open_design()
        else:
            self.tabs.setCurrentIndex(self.tab(target))

    def send_refusal(self, plan, connection, *, scan_only=False):
        if self.busy:
            return "The cutter connection is busy."
        if self.pending_design():
            return "Review the latest edits in Design."
        if plan is None or self.job is not plan or plan.blocked:
            return "Review the current job and resolve its checks."
        if self.bundle and not self.bundle_checked:
            return "Check the matching PDF in Sheet."
        if connection != self.connection():
            return "The cutter connection changed. Review the job again."
        if connection[0] != "auto" and not connection[1]:
            return "Select a Bluetooth cutter."
        if scan_only and not plan.registration_steps():
            return "Enable registration to test the scan."
        if not scan_only and plan.settings.passes <= 0:
            return "Set at least one pass to send cut paths."
        return ""

    def refresh_send(self):
        if self.loading or "Send" not in self.pages:
            return
        target = self.review_tab()
        index = self.tabs.currentIndex()
        self.review_button.setText({"Studio": "Studio Options"}.get(target, f"Review {target}"))
        self.review_button.setVisible(target == "Design" or index != self.tab(target))
        self.send_button.setVisible(target == "Send" and index == self.tab("Send"))
        reason = self.send_refusal(self.job, self.connection())
        self.send_button.setEnabled(not reason)
        self.scan_button.setEnabled(not self.send_refusal(self.job, self.connection(), scan_only=True))
        self.test_button.setEnabled(not self.busy)
        self.update_guide()
        self.footer_status.setText(reason or (
            f"{self.job.device.name} · {len(self.job.paths)} paths · "
            + ("Registration on" if self.job.registration else "Registration off")))

    def confirm(self, title, prompt):
        return QMessageBox.question(self, title, prompt, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                    QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes

    def select_open(self, title, filters):
        value, _ = QFileDialog.getOpenFileName(self, title, "", filters)
        return Path(value) if value else None

    def choose_output(self, title, name, filters=""):
        value, _ = QFileDialog.getSaveFileName(self, title, name, filters)
        return Path(value) if value else None

    def export_job(self):
        if self.job is None:
            self.message("Build the layout before exporting.")
            return
        plan = self.job
        folder = QFileDialog.getExistingDirectory(self, "Export Templates", str(config.template_dir()))
        if folder:
            try:
                job.write_templates(plan, self.job_name(plan), Path(folder))
            except (OSError, ValueError) as error:
                self.message(str(error))
            else:
                self.message(f"Exported templates to {folder}.")

    @staticmethod
    def job_name(plan):
        return f"{plan.grid.sheet_name.lower()}-{plan.grid.card.name}-{plan.grid.cols}x{plan.grid.rows}-{plan.mode}"

    def dump_stream(self):
        if self.job is None:
            self.message("Build the layout before exporting.")
            return
        plan = self.job
        path = self.choose_output("Save Verified Stream", self.job_name(plan) + ".bin")
        if path:
            try:
                path.write_bytes(plan.stream)
            except OSError as error:
                self.message(str(error))
            else:
                self.message(f"Saved {path.name}.")

    def invalidate_measurement(self, *, clear_image=False):
        self.sheet_token = self.report = None
        self.bundle_checked = False
        self.preview.report = None
        if clear_image:
            self.preview.show_pdf()
            self.preview_mode.hide()

    def lock_bundle_controls(self, locked):
        for key in ("layout_preset", "sheet", "portrait", "engine", "card", "mode", "radius_mm",
                    "pattern", "mark_inset_mm", "mark_length_mm", "mark_thickness_mm", "bleed_mm", "registration"):
            self.controls[key].setEnabled(not locked)
        self.skip.setEnabled(not locked)

    def clear_bundle(self):
        if self.bundle:
            self.bundle = self.pdf = None
            self.lock_bundle_controls(False)
            self.invalidate_measurement(clear_image=True)
            self.sheet_file.setText("Open a print PDF or a matched ProxyPress ZIP.")
            self.sheet_result.setText("Not measured")
            with self.editing():
                self.sheet_page_number.setRange(1, 999)
                self.sheet_page_number.setValue(1)

    def open_sheet(self):
        path = self.select_open("Open Printed Sheet", "Print PDF or ProxyPress ZIP (*.pdf *.PDF *.zip *.ZIP)")
        if not path:
            return
        if path.suffix.casefold() == ".zip" or zipfile.is_zipfile(path):
            try:
                self.load_bundle(path)
            except (OSError, ValueError) as error:
                self.message(str(error))
            return
        if self.bundle:
            self.reset_artwork()
        self.pdf = path
        self.sheet_file.setText(path.name)
        with self.editing():
            self.sheet_page_number.setValue(1)
        self.measure_sheet()

    def load_bundle(self, path):
        matched = bundle.load(path)
        row = matched.rows[0]
        drawing = matched.artwork(row) if "svg" in row else None
        self.clear_bundle()
        self.bundle, self.pdf = matched, path
        self.reviewed_design = self.project_shapes = None
        self.lock_bundle_controls(True)
        with self.editing():
            self.sheet_page_number.setRange(1, len(matched.rows))
            self.sheet_page_number.setValue(1)
        self.use_bundle_row(row, drawing)

    def use_bundle_row(self, row, drawing):
        self.invalidate_measurement(clear_image=True)
        self.sheet_file.setText(
            f"{self.pdf.name} · Sheet {row['sheet']} · PDF front page {row['frontPage']}\n"
            + self.bundle.mat_placement())
        self.use_artwork(drawing, row.get("svg") or row["cutFile"])
        self.measure_sheet()

    def current_bundle_row(self):
        return self.bundle.rows[self.sheet_page_number.value() - 1] if self.bundle else None

    def change_sheet_page(self):
        if self.loading:
            return
        if self.bundle:
            row = self.current_bundle_row()
            try:
                drawing = self.bundle.artwork(row) if "svg" in row else None
            except (OSError, ValueError) as error:
                self.invalidate_measurement(clear_image=True)
                self.sheet_result.setText(str(error))
                self.refresh_send()
                return
            self.use_bundle_row(row, drawing)
        else:
            self.measure_sheet()

    def measure_sheet(self):
        path, matched, current = self.pdf, self.bundle, self.job
        if self.loading or path is None:
            return
        if not matched and not current:
            self.message("Check the layout before measuring the sheet.")
            return
        from . import sheet

        self.invalidate_measurement(clear_image=True)
        token = self.sheet_token = object()
        self.sheet_result.setText("Measuring…")
        self.scan_result.clear()
        self.refresh_send()
        row = self.current_bundle_row()
        marks = matched.marks() if matched else current.marks
        dpi, page = self.sheet_dpi.currentData(), self.sheet_page_number.value()

        def work():
            if matched:
                report, image = matched.measure(row, marks, dpi)
            else:
                image = sheet.render(path, page=page, dpi=dpi, colour=True)
                report = sheet.measure(path, marks, page=page, dpi=dpi, image=image)
            png = io.BytesIO()
            image.save(png, format="PNG")
            return report, report.problems(marks.sheet_w_mm, marks.sheet_h_mm), png.getvalue()

        def done(value, error):
            if self.sheet_token is not token:
                return
            if error:
                self.sheet_result.setText(f"Could not measure the sheet. {error}")
            else:
                report, issues, png = value
                self.report = report
                self.bundle_checked = bool(matched) and not issues
                self.sheet_result.setText("\n".join(
                    [report.diagnosis(), *(mark.describe() for mark in report.marks), *issues]))
                self.preview.show_pdf(png, (report.page_w_mm, report.page_h_mm), report)
                self.preview_mode.show()
            self.refresh_send()

        self.tasks.run(work, done)

    def connection(self):
        mode = self.link.currentData()
        if mode == "auto":
            return mode, None, None
        return mode, self.identifier.text().strip() or None, self.channel.value() if mode == "bluetooth" else None

    @staticmethod
    def open_link(connection, device=None):
        mode, identifier, channel = connection
        return transport.open_transport(mode, device=device, bluetooth_id=identifier, bluetooth_channel=channel)

    def change_connection(self):
        mode = self.link.currentData()
        for widget in (self.detected, self.identifier, self.discover):
            self.connection_form.setRowVisible(widget, mode != "auto")
        self.connection_form.setRowVisible(self.channel, mode == "bluetooth")
        self.identifier.clear()
        set_choices(self.detected, {"": "Select a cutter"}, "")
        self.change_target()

    def change_target(self):
        self.connection_status.setText("Cutter status not checked.")
        self.refresh_send()

    def device_selected(self):
        identifier = self.detected.currentData()
        if not identifier:
            return
        self.identifier.setText(identifier)
        device = bluetooth.advertised_model(self.detected.currentText().split(" · ")[0])
        if device:
            self.select("cutter", device.name)

    def find_cutters(self):
        from . import ble, rfcomm

        mode = self.link.currentData()
        scanner = ble if mode == "ble" else rfcomm
        self.discover.setEnabled(False)
        self.connection_status.setText("Searching for Bluetooth cutters…")

        def done(devices, error):
            self.discover.setEnabled(True)
            if mode != self.link.currentData():
                return
            if error:
                self.connection_status.setText(str(error))
                return
            set_choices(self.detected, {"": "Select a cutter", **{
                identifier: f"{name or 'Unnamed'} · {identifier}" for identifier, name in devices}}, "")
            self.connection_status.setText("Select a cutter from the list." if devices else "No cutters found.")

        self.tasks.run(scanner.discover, done)

    def check_device(self):
        connection = self.connection()
        if self.busy:
            return
        if connection[0] != "auto" and not connection[1]:
            self.message("Select a Bluetooth cutter.")
            return
        self.busy = True
        self.connection_status.setText("Reading cutter status…")
        self.refresh_send()

        def work():
            with self.open_link(connection) as cutter:
                version = f" · {cutter.firmware_version()}" if connection[0] != "auto" else ""
                return cutter.device, f"{cutter.device.name} · {cutter.status_with_tools()}{version}"

        def done(value, error):
            self.busy = False
            if connection == self.connection():
                self.connection_status.setText(str(error) if error else value[1])
                if not error and connection[0] != "auto":
                    self.select("cutter", value[0].name)
            self.refresh_send()

        self.tasks.run(work, done)

    def confirm_send(self, *, scan_only=False):
        plan, connection = self.job, self.connection()
        reason = self.send_refusal(plan, connection, scan_only=scan_only)
        if reason:
            self.message(reason)
            return
        prompt = "\n".join((plan.device.name, plan.grid.describe(), *(
            f"{group.key} · {group.describe()}" for group in plan.action_groups)))
        if not plan.action_groups:
            prompt += f"\nForce {plan.settings.force} · Speed {plan.settings.speed} · {plan.settings.passes} passes"
        if any(preset.origin != "measured" for preset in (
                [group.preset for group in plan.action_groups] or [plan.preset])) and not scan_only:
            prompt += "\nTest these settings on this material before cutting the full sheet."
        dx, dy = plan.offset_mm
        if dx or dy:
            prompt += f"\nCalibration: X {dx:+.2f} mm, Y {dy:+.2f} mm"
        if plan.registration and not plan.auto_search:
            prompt += "\nPosition the cutter head over the first registration mark before starting."
        if self.bundle:
            prompt += "\n" + self.bundle.mat_placement()
        if self.artwork and self.artwork.studio3_page:
            prompt += "\nMatch the registration settings to the printed Studio3 sheet."
        if scan_only:
            prompt += "\nThis scans the printed marks without sending cut paths."
        elif not plan.registration:
            prompt += "\nRegistration is off. The cutter uses sheet coordinates."
        if connection[1]:
            prompt += f"\nBluetooth: {connection[1]}"
        title = "Test Registration?" if scan_only else "Send to Cutter?"
        if self.confirm(title, prompt):
            self.start_send(plan, connection, scan_only=scan_only)

    def start_send(self, plan, connection, *, scan_only=False):
        reason = self.send_refusal(plan, connection, scan_only=scan_only)
        if reason:
            self.message(reason)
            return
        steps = plan.registration_steps() if scan_only else plan.steps
        report, row, matched = self.report, self.current_bundle_row(), self.bundle

        def failed(error):
            notes = plan.registration_notes(report, matched.near_marks(row) if matched else None)
            if matched:
                notes.append(matched.mat_placement())
            notes.extend(("Check the print scale and keep all marks visible.", *plan.registration_writes()))
            self.scan_result.setText("\n".join((str(error), *notes)))
            self.tabs.setCurrentIndex(self.tab("Sheet"))

        self.send_steps(plan.device, connection, steps, "Registration scan passed." if scan_only else "Cut sent.",
                        on_registration_failure=failed)

    def send_steps(self, device, connection, steps, success, *, on_registration_failure=None, test=False):
        self.guide.invalidate()
        self.busy = True
        self.scan_result.clear()
        self.refresh_send()

        def work():
            with self.open_link(connection, device) as cutter:
                if cutter.can_read:
                    state = cutter.status()
                    if state in ("moving", "unloaded") or (state != "ready" and (test or connection[0] != "auto")):
                        raise transport.TransportError(f"The cutter is not ready ({state}).")
                return cutter.run(steps)

        def done(sent, error):
            self.busy = False
            self.refresh_send()
            if isinstance(error, transport.RegistrationFailed) and on_registration_failure:
                on_registration_failure(error)
            self.message(str(error) if error else f"{success} {sent:,} bytes.")

        self.tasks.run(work, done)

    def update_guide(self):
        state = self.guide
        for field in (self.guide_apply, self.guide_next, self.guide_reset):
            field.setEnabled(not self.busy)
        self.guide_send.setEnabled(not self.busy and not state.interrupted)
        self.guide_use.setEnabled(not self.busy and state.tested_settings is not None)
        self.guide_send.setText("Repeat One Pass…" if state.completed else "Cut One Pass…")
        self.guide_status.setText("Waiting for the cutter to finish…" if self.busy else state.status())

    def set_guide_blade_values(self, values):
        with self.editing():
            for key, value in values.items():
                self.blade[key].setValue(-1 if value is None else value)
        self.rebuild()

    def apply_guide_start(self):
        values = self.blade_values()
        device = graphtec.device_by_name(self.choice("cutter"))
        self.set_guide_blade_values(starting_guide.starting_values(
            device, values["holder"] if device.two_tools else 1, values["blade_diameter_mm"]))
        suffix = "" if self.blade["depth"].value() >= 0 else " Set depth on the blade."
        self.message("Starting settings applied to Blade." + suffix)

    def use_guide_settings(self):
        settings = self.guide.tested_settings
        if settings is None:
            return
        device = graphtec.device_by_name(self.choice("cutter"))
        holder = self.blade["holder"].value() if device.two_tools else 1
        if device != self.guide.trial.sheet.device or settings.holder != holder:
            self.message("Select the tested cutter and tool holder first.")
            return
        self.set_guide_blade_values({name: getattr(settings, name) for name in ("force", "speed", "depth", "passes")})
        self.message(f"Applied the tested settings with {settings.passes} pass(es).")

    def next_guide_spot(self):
        self.guide.next_spot()
        self.update_guide()

    def reset_guide(self):
        if self.confirm("Start a New Test Sheet?", "Load a fresh blank sheet to start at the first spot."):
            self.guide.reset()
            self.update_guide()

    def confirm_guide_pass(self):
        if self.busy:
            return
        try:
            trial = self.guide.prepare(self.connection(), self.job_options(), self.job, self.test_shape.currentData())
        except (ValueError, KeyError) as error:
            self.message(str(error))
            return
        prompt = self.guide.prompt(trial)
        if self.confirm("Send One Test Pass?", prompt):
            self.start_guide_pass(trial)

    def start_guide_pass(self, trial):
        if self.busy:
            return
        try:
            current = self.guide.prepare(self.connection(), self.job_options(), self.job, self.test_shape.currentData())
        except (ValueError, KeyError) as error:
            self.message(str(error))
            return
        if trial != current:
            self.message("Test settings or cutter connection changed. Review the pass again.")
            return
        self.busy = True
        self.refresh_send()

        def work():
            with self.open_link(trial.connection, trial.sheet.device) as cutter:
                return starting_guide.run_pass(cutter, trial)

        def done(_sent, error):
            self.guide.record(trial, error)
            self.busy = False
            self.refresh_send()
            self.message(str(error) if error else "Pass complete. Keep the mat loaded and lift a corner.")

        self.tasks.run(work, done)

    def confirm_test_cut(self):
        connection, current = self.connection(), self.job
        if self.busy or (connection[0] != "auto" and not connection[1]):
            self.message("Connect an available cutter first.")
            return
        try:
            opts = self.job_options()
            device = graphtec.device_by_name(opts["cutter"])
            base = graphtec.CutSettings(force=opts["force"], speed=opts["speed"], passes=opts["passes"],
                                        depth=opts["depth"], **opts["blade_options"])
            samples = testcut.build(device, base, forces=self.test_force.text(), passes=self.test_passes.text(),
                sheet_w_mm=current.grid.sheet_w_mm if current else 215.9,
                sheet_h_mm=current.grid.sheet_h_mm if current else 279.4,
                offset_mm=opts["offset_mm"], shape=self.test_shape.currentData(),
                mat=opts["mat"], end_position=opts["end_position"], end_feed_mm=opts["end_feed_mm"])
        except (ValueError, KeyError) as error:
            self.message(str(error))
            return
        prompt = "Load a blank offcut. Registration is off.\n\n" + "\n".join(
            f"{label} · {description}" for label, description in samples.map_rows())
        if self.confirm("Cut Test Samples?", prompt):
            if not self.busy and self.connection() == connection and self.job is current:
                self.send_steps(device, connection, samples.steps, "Test cut sent.", test=True)
            else:
                self.message("The settings or cutter connection changed. Review the test cut again.")

    def check_guest(self):
        self.run_guest_task(winboat.status, lambda value:
            f"Studio: {value.studio_state}\n"
            + ("Ready to open the guest." if value.ready_for_studio else value.why_not_ready()))

    def prepare_guest(self):
        self.run_guest_task(winboat.prepare, lambda value: "\n".join(value["steps"]))

    def run_guest_task(self, work, format_result):
        def done(value, error):
            self.guest_status.setText(str(error) if error else format_result(value))
        self.guest_status.setText("Working…")
        self.tasks.run(work, done)

    def open_in_studio(self, path):
        self.run_guest_task(lambda: winboat.open_in_studio(path), lambda _: "Silhouette Studio is opening.")

    def choose_studio_file(self):
        path = self.select_open("Open Studio File", "Silhouette Studio (*.studio *.STUDIO *.studio3 *.STUDIO3)")
        if path:
            self.open_in_studio(path)

    def open_current_in_studio(self):
        plan, matched, row = self.job, self.bundle, self.current_bundle_row()

        def work():
            if matched:
                with tempfile.TemporaryDirectory() as folder:
                    return winboat.open_in_studio(matched.studio_file(row, folder))
            if not plan:
                raise ValueError("Build the layout before opening it in Studio.")
            files = job.write_templates(plan, self.job_name(plan), folder=winboat.stage_dir(), proof=False)
            return winboat.open_in_studio(files["dxf"])

        self.run_guest_task(work, lambda _: "Silhouette Studio is opening the current sheet.")

    def open_design(self):
        if self.bundle or self.busy:
            self.message("Finish the current job and clear the matched ZIP before opening Design.")
            return
        if self.design is None:
            if self.job is None:
                self.message("Check the layout before opening Design.")
                return
            from .qt_design import Design
            plan = self.job
            try:
                shapes = self.project_shapes or (editor_model.editable_import(self.artwork)[0] if self.artwork else
                                                  editor_model.shapes_from_paths(plan.paths))
            except ValueError as error:
                self.message(str(error))
                return
            registration = editor_model.Registration.from_marks(plan.marks) if plan.registration else None
            project = editor_model.Project(plan.grid.sheet_w_mm, plan.grid.sheet_h_mm, shapes, registration, plan.mat)
            self.design = Design(project, plan.device, self.leave_design, self.review_design)
            self.workspaces.addWidget(self.design)
        self.workspaces.setCurrentWidget(self.design)

    def leave_design(self):
        self.workspaces.setCurrentIndex(0)
        self.refresh_send()

    def review_design(self, project, action):
        artwork = project.artwork(action)
        if not artwork.paths or any(path.length_mm() <= 0 for path in artwork.paths):
            self.design.message("Draw or resize the paths before reviewing.")
            return
        self.project_shapes = project.shapes
        self.reviewed_design = project
        with self.editing():
            self.select("layout_preset", "")
            self.select("mode", template.ROUNDED)
            self.select("mat", project.mat)
            self.skip.clear()
            self.controls["registration"].setChecked(project.registration is not None)
            if project.registration:
                marks = project.registration
                self.select("pattern", marks.pattern)
                for field in ("inset_mm", "length_mm", "thickness_mm"):
                    self.controls[f"mark_{field}"].setValue(getattr(marks, field))
        grouped = action == editor_model.ALL_ACTIONS
        self.use_artwork(artwork, f"Design · {action}", "layer" if grouped else "simple")
        self.tabs.setCurrentIndex(self.tab("Send"))
        self.leave_design()

    def closeEvent(self, event):
        if self.busy:
            self.message("Wait for the cutter operation to finish before closing.")
            event.ignore()
        elif self.design and not self.design.may_replace():
            event.ignore()
        else:
            super().closeEvent(event)


def main(argv=None):
    app = QApplication([sys.argv[0], *(argv or [])])
    app.setApplicationName("Silhouette Control Center")
    app.setDesktopFileName(APP_ID)
    window = Window()
    window.show()
    return app.exec()
