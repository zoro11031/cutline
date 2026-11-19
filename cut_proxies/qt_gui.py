"""Qt Widgets frontend for the verified Silhouette desktop workflow."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import io
from pathlib import Path
import sys
from time import monotonic
import zipfile

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QApplication, QComboBox, QFileDialog, QGroupBox, QHBoxLayout, QInputDialog, QMenu,
                               QMainWindow, QMessageBox, QPlainTextEdit,
                               QSplitter, QStackedWidget, QStyle, QTabWidget, QVBoxLayout, QWidget)

from . import (actions, bluetooth, bundle, config, corner_test, editor_model, geometry, graphtec, importer, job,
               layout_presets, materials, presets, regmarks, starting_guide, template, testcut, toolpath, transport)
from .desktop_help import BLADE_VALUE_TEXT, CONTROL_HELP, SETUP_STEPS
from .holder_settings import HolderSettings, for_holder
from .qt_canvas import PREVIEW_MODES, Preview, ZoomControls
from .qt_controls import (Form, Page, SettingsWheel, Tasks, bar, button, set_choices,
                          styled_label, workspace_header, workspace_layout, workspace_style, workspace_tools)

APP_ID = "org.printing.SilhouetteControlCenter"
ARTWORK_FILTER = "Cut artwork (*.svg *.SVG *.dxf *.DXF *.studio3 *.STUDIO3)"
from . import cutters

DEVICES = {device.name: device.name.removeprefix("Silhouette ") for device in graphtec.DEVICES}

# Qt widgets use the same field names and units as the job builder.
BLADE_NUMBERS = (
    ("force", "Force", 1, 40, 1, 0), ("speed", "Speed", 1, 30, 1, 0),
    ("passes", "Passes", 0, 10, 1, 0), ("depth", "Blade Depth", -1, 10, 1, 0),
    ("holder", "Tool Holder", 1, 2, 1, 0), ("acceleration", "Acceleration", -1, 3, 1, 0),
    ("blade_diameter_mm", "Tool Offset (mm)", 0, 2.3, 0.05, 2),
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
QUICK_NUMBERS = {"force", "speed", "passes", "acceleration", "depth"}

class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Silhouette Control Center")
        self.resize(1210, 800)
        self.settings_wheel = SettingsWheel(self)
        self.setWindowIcon(QIcon.fromTheme(APP_ID))
        self.tasks = Tasks(self)
        self.settings = config.load_settings()
        self.job = self.artwork = self.project_shapes = self.design = self.reviewed_design = None
        self.pdf = self.bundle = self.sheet_token = self.report = None
        self.bundle_row_error = None
        self.bundle_checked = self.busy = self.loading = False
        self.tuning = False
        self.holder_state = HolderSettings()
        self.guide = starting_guide.Guide(config.CONFIG_DIR / "material-test.json")
        self.group_rows = {}
        self.group_choices = {}
        self.pages = {}
        self.controls = {}
        self.blade = {}
        self._initial_workspace_layout = True

        self.workspaces = QStackedWidget()
        self.setCentralWidget(self.workspaces)
        self.workspaces.setStyleSheet(workspace_style(self.palette()))
        setup = QWidget()
        setup.setObjectName("workspace")
        self.workspaces.addWidget(setup)
        content = workspace_layout(setup)
        header, self.menu_bar, self.design_button, self.send_workspace = workspace_header(
            setup, self.open_design, self.open_send_workspace, active="Send")
        content.addWidget(header)
        self.workspace_split = split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(12)
        self.tabs = QTabWidget()
        self.tabs.setProperty("role", "settings")
        self.tabs.setMinimumWidth(345)
        self.tabs.setAccessibleName("Cut settings")
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().setDrawBase(False)
        self.tabs.setUsesScrollButtons(True)
        self.tabs.currentChanged.connect(self.change_page)

        preview_panel = QWidget()
        right = QVBoxLayout(preview_panel)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(7)
        self.preview_bar, preview_controls = workspace_tools(setup)
        self.preview_title = styled_label("Sheet preview")
        preview_controls.addWidget(self.preview_title)
        preview_controls.addStretch(1)
        self.preview = Preview()
        self.preview_mode = QComboBox()
        set_choices(self.preview_mode, PREVIEW_MODES, "Overlay")
        self.preview_mode.currentIndexChanged.connect(lambda: self.change_preview())
        self.preview_mode.setAccessibleName("Sheet preview mode")
        self.preview_mode.hide()
        preview_controls.addWidget(self.preview_mode)
        self.preview_zoom = ZoomControls(self.preview)
        preview_controls.addWidget(self.preview_zoom)
        content.addWidget(self.preview_bar)
        content.addWidget(split, 1)
        self.preview.colorblind = bool(self.settings.get("colorblind_mode"))
        right.addWidget(self.preview, 1)

        self.summary_bar, summary_row = bar(preview_panel, (12, 8, 12, 8))
        summary_text = QVBoxLayout()
        summary_text.setSpacing(2)
        self.summary_title = styled_label()
        summary_text.addWidget(self.summary_title)
        self.summary = styled_label(role="body")
        self.summary.setWordWrap(True)
        summary_text.addWidget(self.summary)
        summary_row.addLayout(summary_text, 1)
        preview_options = button("Preview Options", lambda: None)
        preview_menu = QMenu(preview_options)
        preview_options.setMenu(preview_menu)
        self.colorblind = preview_menu.addAction("Colorblind mode")
        self.colorblind.setCheckable(True)
        self.colorblind.setChecked(self.preview.colorblind)
        self.colorblind.toggled.connect(self.change_palette)
        self.details_toggle = preview_menu.addAction("Job details")
        self.details_toggle.setCheckable(True)
        summary_row.addWidget(preview_options)
        right.addWidget(self.summary_bar)
        self.details = QPlainTextEdit(readOnly=True)
        self.details.setProperty("role", "diagnostics")
        self.details.setMaximumHeight(150)
        self.details.hide()
        self.details_toggle.toggled.connect(self.details.setVisible)
        right.addWidget(self.details)
        split.addWidget(preview_panel)
        split.addWidget(self.tabs)
        split.setStretchFactor(0, 1)

        footer_bar, footer = bar(setup)
        self.footer = footer
        self.footer_status = styled_label(role="body")
        self.footer_status.setWordWrap(True)
        footer.addWidget(self.footer_status, 1)
        self.review_button = button("Review Cut Job", self.follow_review, primary=True)
        self.send_button = button("Send to Cutter", self.confirm_send, primary=True,
                                  icon=("media-playback-start", QStyle.StandardPixmap.SP_MediaPlay))
        self.send_button.setMinimumHeight(32)
        self.back_to_send = button("Back to Send", self.close_material_guide, primary=True)
        footer.addWidget(self.review_button)
        footer.addWidget(self.send_button)
        footer.addWidget(self.back_to_send)
        content.addWidget(footer_bar)

        with self.editing():
            self.layout_page()
            self.sheet_page()
            self.materials_page()
            self.send_page()
            self.load_preset()
            self.load_materials()
        self.build_workspace_menus()
        for field, description in CONTROL_HELP.items():
            widget = self.blade.get(field) or self.controls.get(field)
            widget.setToolTip(description)
            if hasattr(widget, "slider"):
                widget.slider.setToolTip(description)
        self.skip.setToolTip("Comma-separated slot numbers, starting at 0 in the top-left. Skipped slots are left uncut.")
        self.rebuild()

    def showEvent(self, event):
        super().showEvent(event)
        if self._initial_workspace_layout:
            self._initial_workspace_layout = False
            width = self.workspace_split.width()
            settings_width = min(480, max(360, round(width * 0.36)))
            self.workspace_split.setSizes([width - settings_width, settings_width])
            tabs = self.tabs.tabBar()
            height = max(tabs.sizeHint().height(), self.preview_bar.sizeHint().height())
            tabs.setMinimumHeight(height)
            self.preview_bar.setMinimumHeight(height)

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

    def install_page(self, name, page, *, title=None):
        self.pages[name] = self.tabs.addTab(page.finish(), title or name)

    def build_workspace_menus(self):
        def command(menu, title, callback):
            action = menu.addAction(title)
            action.triggered.connect(lambda: callback())
            return action

        files = self.menu_bar.addMenu("&File")
        command(files, "Open Cut Artwork…", self.open_artwork)
        command(files, "Open PDF or ZIP…", self.open_sheet)
        self.close_print_action = command(files, "Close Print File", self.close_printed_sheet)
        self.paired_cut_action = command(files, "Save Paired Cut File…", self.save_bundle_file)
        files.addSeparator()
        command(files, "Export Cut Templates…", self.export_job)

        edit = self.menu_bar.addMenu("&Edit")
        command(edit, "Edit in Design", self.open_design)
        self.reset_layout_action = command(edit, "Use Generated Layout", self.reset_artwork)

        view = self.menu_bar.addMenu("&View")
        for index in self.pages.values():
            command(view, self.tabs.tabText(index), lambda page=index: self.tabs.setCurrentIndex(page))
        view.addSeparator()
        command(view, "Fit Preview", self.preview.fit)

        settings = self.menu_bar.addMenu("&Settings")
        command(settings, "Cut Settings", self.open_cut_settings)
        command(settings, "Advanced Send Settings", self.open_send_advanced)
        command(self.menu_bar.addMenu("&Help"), "Setup Guide", self.show_setup_guide)

    def open_cut_settings(self):
        self.open_send_workspace()
        page = self.tabs.widget(self.tab("Send"))
        QTimer.singleShot(0, self, lambda: page.ensureWidgetVisible(self.controls["preset"]))

    def open_send_advanced(self):
        self.open_send_workspace()
        self.send_advanced.disclosure.setChecked(True)
        page = self.tabs.widget(self.tab("Send"))
        QTimer.singleShot(0, self, lambda: page.ensureWidgetVisible(self.send_advanced.disclosure))

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
        source.push("Open Cut Artwork…", self.open_artwork)
        self.use_generated = source.push("Use Generated Layout", self.reset_artwork)
        layout = page.group("Media and cards")
        self.choose(layout, "layout_preset", "Layout preset",
                    {"": "Custom", **{key: value.name for key, value in layout_presets.available().items()}},
                    changed=self.apply_layout_preset)
        self.choose(layout, "sheet", "Sheet", sorted(geometry.SHEETS), config.DEFAULT_SHEET)
        self.choose(layout, "portrait", "Orientation", {"best": "Best fit", True: "Portrait", False: "Landscape"})
        self.choose(layout, "card", "Card size", sorted(geometry.CARDS), config.DEFAULT_CARD)
        self.layout_advanced = advanced = page.advanced()
        format_options = advanced.group("Cut layout")
        self.choose(format_options, "engine", "Layout engine", {"scm": "Silhouette Card Maker", "proxypress": "Proxy Press"})
        self.choose(format_options, "mode", "Cut mode", dict(zip(template.MODES, ("Rounded", "Square", "Cross-cuts"))))
        self.number(format_options, "radius_mm", "Corner radius (mm)", 3.2, 0, 10, 0.1, 1)
        self.choose(format_options, "colour", "Line color", {"": "All colors"})
        marks = advanced.group("Registration Marks")
        self.choose(marks, "pattern", "Pattern", {regmarks.THREE: "Type 1 (3 marks)", regmarks.FOUR: "4 marks"})
        self.number(marks, "mark_inset_mm", "Inset (mm)", 10, 2, 40, 0.1, 2)
        self.number(marks, "mark_length_mm", "Arm length (mm)", 0, 0, regmarks.MAX_LENGTH_MM, 0.1, 4)
        self.controls["mark_length_mm"].setSpecialValueText("Auto")
        self.number(marks, "mark_thickness_mm", "Thickness (mm)", 1, regmarks.MIN_THICKNESS_MM, regmarks.MAX_THICKNESS_MM, 0.05, 2)
        self.number(marks, "bleed_mm", "Bleed (mm)", 0, 0, 5, 0.25, 3)
        self.skip = marks.text("Skip slots (0-based)", changed=self.rebuild)
        advanced.group("Templates").push("Export SVG, DXF and Proof…", self.export_job)
        self.install_page("Layout", page)

    def sheet_page(self):
        page = Page()
        form = page.group("Printed sheet")
        self.sheet_file = form.note("Open a print PDF or a matched Print & Cut ZIP.")
        self.close_print = button("Close File", self.close_printed_sheet)
        form.actions(button("Open PDF or ZIP…", self.open_sheet), self.close_print)
        self.bundle_export = form.push("Save Paired Cut File…", self.save_bundle_file)
        self.bundle_print = form.push("Save Print PDF…", self.save_bundle_print)
        self.bundle_export.hide()
        self.bundle_print.hide()
        self.sheet_page_number = form.number("Page", 1, 1, 999, changed=self.change_sheet_page)
        self.sheet_page_label = form.labelForField(self.sheet_page_number)
        self.measure_button = form.push("Measure Registration Marks", self.measure_sheet)
        self.update_sheet_controls()
        result = page.group("Registration results")
        self.sheet_result = result.note("Not measured")
        scan = page.group("Last cutter scan")
        self.scan_box = scan.parentWidget()
        self.scan_result = scan.note()
        self.scan_result.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.copy_scan = scan.push("Copy Scan Details", self.copy_scan_details)
        self.clear_scan_result()
        self.sheet_advanced = advanced = page.advanced()
        self.sheet_dpi = advanced.group("PDF check").choice("Measurement resolution", (150, 200, 300), 200, self.measure_sheet)
        self.install_page("Sheet", page)

    def materials_page(self):
        page = Page()
        profile = page.group("Material preset")
        self.provenance = profile.note()
        profile.note("Choose and edit your cut settings in Send, then save them here.")
        profile.actions(button("Update Preset", self.save_preset),
                        button("Save as New…", self.save_new_preset))
        table = page.group("Studio materials")
        self.divisions = table.choice("Show Cutter Divisions", {False: "Known Cutters", True: "All Cutters"},
                                     changed=self.load_materials)
        self.material = table.choice("Factory preset", ())
        table.actions(button("Import CSV…", self.choose_material_csv),
                      button("Import Folder…", lambda: self.choose_material_csv(folder=True)))
        self.apply_material_button = table.push("Apply to Cut Settings", self.apply_material)
        page.group("Defaults").push("Restore Default Materials…", self.restore_materials)
        self.install_page("Materials", page)

    def add_advanced_cut_settings(self, advanced):
        blade_details = advanced.group("Blade details")
        for key, label, lo, hi, step, decimals in BLADE_NUMBERS:
            if key not in self.blade:
                self.blade[key] = blade_details.number(label, lo, lo, hi, step, decimals, self.rebuild)
        self.blade["lift_head"] = blade_details.check("Lift head", changed=self.rebuild)
        path_settings = advanced.group("Cut paths")
        for key, label, choices in BLADE_CHOICES:
            self.blade[key] = path_settings.choice(label, choices, changed=self.rebuild)
        for key, label in BLADE_SWITCHES[3:]:
            self.blade[key] = path_settings.check(label, changed=self.rebuild)
        weed = advanced.group("Weed Settings")
        self.weed_box = weed.parentWidget()
        self.number(weed, "weed_border_mm", "Weed border padding (mm)", 0, 0, 1000, 0.5, 2)
        self.choose(weed, "weed_lines", "Weed Lines", toolpath.WEED_LINES)

    def send_page(self):
        page = Page()
        self.send_normal = normal = page.section()
        cutter = normal.group("Cutter")
        self.choose(cutter, "cutter", "Resolved cutter", DEVICES, "Silhouette Cameo 4", self.change_cutter)
        cutter.setRowVisible(self.controls["cutter"], False)
        self.cutter_model = cutter.choice("Cutter model", cutters.MODELS, cutters.AUTO, self.change_model)
        self.wireless_choices = {}
        self.wireless_scanning = False
        self.preferred_endpoint = None
        self.detected = cutter.choice("Connected cutter", {"": "Select a cutter"}, changed=self.select_detected)
        self.connection_status = cutter.note("Cutter status not checked.")
        self.discover = button("Refresh Cutters", self.find_cutters)
        self.refresh_cutter = button("Check Status", self.check_device)
        cutter.actions(self.discover, self.refresh_cutter)

        groups = normal.group("Cut groups")
        self.group_box = groups.parentWidget()
        self.group_details = QWidget()
        self.group_layout = QVBoxLayout(self.group_details)
        self.group_layout.setContentsMargins(0, 0, 0, 0)
        groups.addRow(self.group_details)

        settings = normal.group("Cut Settings")
        self.blade["holder"] = settings.holders(self.rebuild)
        self.choose(settings, "preset", "Material", sorted(presets.all_presets()), config.DEFAULT_PRESET,
                    self.load_preset)
        for key, label, low, high, step, decimals in BLADE_NUMBERS:
            if key in QUICK_NUMBERS:
                self.blade[key] = settings.number(label, low, low, high, step, decimals, self.rebuild,
                                                  slider=True, special_text=BLADE_VALUE_TEXT.get(key, ""))
        for key, label in BLADE_SWITCHES[1:3]:
            self.blade[key] = settings.check(label, changed=self.rebuild)
        self.material_guide = settings.push("Dial In a Material…", self.open_material_guide)

        registration = normal.group("Registration")
        self.check(registration, "registration", "Scan for printed marks", True)
        self.scan_button = button("Test Registration", lambda: self.confirm_send(scan_only=True))
        self.corner_button = button("Corner Test Cut…", self.confirm_corner_test)
        self.scan_button.setToolTip("Scan the printed marks without cutting.")
        self.corner_button.setToolTip("Cut a single 3 mm square outside the artwork in a clear corner.")
        self.footer.insertWidget(1, self.scan_button)
        self.footer.insertWidget(2, self.corner_button)

        self.send_advanced = advanced = normal.advanced()
        connection = advanced.group("Connection")
        self.advanced_connection_form = connection
        self.link = connection.choice("Connection method", {"auto": "Automatic", **bluetooth.BLUETOOTH_MODES},
                                      changed=self.change_connection)
        self.link.setToolTip("Automatic looks for USB and Bluetooth cutters. Choose a Bluetooth method to connect manually.")
        self.identifier = connection.text("Bluetooth identifier")
        self.identifier.textChanged.connect(lambda: self.change_target())
        self.channel = connection.number("Channel", 1, 1, 30, changed=self.change_target)
        self.channel.setToolTip("Bluetooth Classic usually uses channel 1.")
        mode = advanced.group("Send by")
        self.group_mode_box = mode.parentWidget()
        self.choose(mode, "send_mode", "Mode", {**actions.SEND_MODES, "simple": "Standard"},
                    changed=self.change_group_mode)
        self.controls["send_mode"].setToolTip(
            "Standard uses your current settings. Line, Fill and Layer assign settings to groups of paths.")

        media = advanced.group("Media handling")
        self.choose(media, "mat", "Cutting mat", graphtec.MAT_OPTIONS)
        self.choose(media, "end_position", "Feed Options", graphtec.END_POSITIONS)
        self.number(media, "end_feed_mm", "Feed Distance (mm)", 0, 0, 3000, 0.5, 2)
        self.weed_visibility = media.choice("Show Weed Settings", ("Auto", "Always", "Never"),
                                          self.settings.get("show_weed_settings"), self.update_weed_preference)
        self.check(media, "auto_search", "Search automatically", True)
        self.add_advanced_cut_settings(advanced)

        self.guide_page = page.section()
        guide = self.guide_page.group("Dial In a Material")
        self.guide_box = guide.parentWidget()
        guide.note(starting_guide.GUIDE_TEXT)
        self.guide_apply = button("Use Starting Settings", self.apply_guide_start)
        self.guide_edit = button("Adjust Cut Settings", self.adjust_guide_settings)
        self.guide_edit.setToolTip("Choose a material preset or adjust pressure, speed and depth for the next spot.")
        guide.actions(self.guide_apply, self.guide_edit)
        self.guide_status = guide.note()
        self.guide_send = button("Cut One Pass…", self.confirm_guide_pass, primary=True)
        self.guide_use = button("Use Tested Settings…", self.use_guide_settings)
        self.guide_use.setToolTip("Use the completed pass count and optionally save the tested settings for this material.")
        guide.actions(self.guide_send, self.guide_use)
        self.guide_next = button("Next Test Spot", self.next_guide_spot)
        self.guide_next.setToolTip("Move to untouched stock before testing different settings.")
        guide.addRow(self.guide_next)
        extras = self.guide_page.advanced("Test Options")
        extra = extras.group("Test sheet")
        self.guide_registration = extra.check("Use Registration Marks", self.guide.registered,
                                              self.change_guide_registration)
        self.guide_marked_sheet = extra.push("Save Marked Test Sheet…", self.save_guide_marked_sheet)
        self.guide_reset = extra.push("New Test Sheet", self.reset_guide)
        self.guide_history = extras.group("Pass counts by spot").note()
        help_form = extras.group("Troubleshooting")
        self.guide_outcome = help_form.choice("Optional symptom", starting_guide.OUTCOMES, changed=self.inspect_test_cut)
        self.guide_advice = help_form.note()
        help_form.note(starting_guide.TUNING_TIPS)
        calibration = advanced.group("Calibration")
        dx, dy = config.cut_offset()
        self.cal_x = calibration.number("Offset X (mm)", dx, -5, 5, 0.05, 2, self.rebuild)
        self.cal_y = calibration.number("Offset Y (mm)", dy, -5, 5, 0.05, 2, self.rebuild)
        self.cal_x.setToolTip("Move the cut horizontally after a pen alignment test. Positive is right.")
        self.cal_y.setToolTip("Move the cut vertically after a pen alignment test. Positive is down.")
        calibration.push("Save Calibration", self.save_calibration)
        tests = advanced.group("Test-Cut Matrix")
        self.test_shape = tests.choice("Shape", testcut.TEST_SHAPES)
        self.test_force = tests.text("Force values", "25,29,33")
        self.test_passes = tests.text("Pass counts", "1,2,3,4")
        self.test_button = tests.push("Test Cut…", self.confirm_test_cut)
        advanced.group("Diagnostics").push("Save Raw Stream…", self.dump_stream)
        self.install_page("Send", page, title="Cut Job")
        self.change_connection()
        self.usb_timer = QTimer(self)
        self.usb_timer.timeout.connect(self.refresh_usb)
        self.usb_timer.start(2000)
        QTimer.singleShot(0, self, self.refresh_usb)
        self.wireless_timer = QTimer(self)
        self.wireless_timer.timeout.connect(self.refresh_wireless)
        self.wireless_timer.start(30000)
        QTimer.singleShot(0, self, self.refresh_wireless)

    def show_setup_guide(self):
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Set Up Your First Cut")
        dialog.setText("Set up your first cut")
        dialog.setInformativeText(SETUP_STEPS)
        tune = dialog.addButton("Dial In a Material…", QMessageBox.ButtonRole.ActionRole)
        dialog.addButton(QMessageBox.StandardButton.Close)
        dialog.exec()
        if dialog.clickedButton() is tune:
            self.open_material_guide()

    def open_material_guide(self):
        self.material_guide.setText("Dial In a Material…")
        index = self.tab("Send")
        self.tabs.setCurrentIndex(index)
        self.tuning = True
        self.refresh_send()
        scroll = self.tabs.widget(index)
        QTimer.singleShot(0, self, lambda: scroll.verticalScrollBar().setValue(self.guide_box.y() - 12))

    def open_send_workspace(self):
        if self.tuning:
            self.close_material_guide()
        self.tabs.setCurrentIndex(self.tab("Send"))

    def close_material_guide(self):
        self.tuning = False
        self.tabs.widget(self.tab("Send")).verticalScrollBar().setValue(0)
        self.refresh_send()

    def change_page(self, index):
        if index != self.pages.get("Send"):
            self.tuning = False
        self.refresh_send()

    def adjust_guide_settings(self):
        if self.guide.trial:
            self.next_guide_spot()
        self.open_cut_settings()
        self.material_guide.setText("Resume Material Test")

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
        cutter = self.choice("cutter")
        name, slot = self.choice("preset"), self.holder_state.active
        selected = presets.get(name, cutter)
        try:
            selected = presets.for_context(selected, cutter, slot)
        except ValueError as error:
            self.message(str(error))
            selected = presets.get(presets.STARTING_NAME, cutter)
        if selected.name != name:
            with self.editing():
                self.select("preset", selected.name)
        self.provenance.setText(f"{selected.name}\n{selected.note}")
        self.set_blade_settings(for_holder(selected.cut_settings(), slot))

    def current_holder_profile(self):
        preset = presets.get(self.choice("preset"), self.choice("cutter"))
        return preset, replace(preset.cut_settings(), **self.blade_values())

    def restore_holder(self):
        device = graphtec.device_by_name(self.choice("cutter"))
        holder = self.blade["holder"]
        slot = holder.value() if device.two_tools else 1
        holder.holders.button(2).setEnabled(device.two_tools)
        if slot == self.holder_state.active:
            return
        fresh = slot not in self.holder_state.drafts
        preset, settings = self.holder_state.switch(slot, *self.current_holder_profile())
        if fresh and preset.factory_group:
            try:
                preset = presets.for_context(preset, device.name, slot)
                settings = for_holder(preset.cut_settings(), slot)
            except ValueError:
                preset = presets.get(presets.STARTING_NAME, device.name)
                settings = for_holder(preset.cut_settings(), slot)
        with self.editing():
            self.select("preset", preset.name if self.controls["preset"].findData(preset.name) >= 0 else config.DEFAULT_PRESET)
            self.provenance.setText(f"{preset.name}\n{preset.note}")
            self.set_blade_settings(settings)

    def set_blade_settings(self, settings):
        with self.editing():
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
                self.choice("preset"), new_name=name, cutter=self.choice("cutter"),
                **{key: values.pop(key) for key in ("force", "speed", "depth", "passes")},
                blade_options=values)
        except (OSError, ValueError) as error:
            self.message(str(error))
            return
        if saved is not None:
            self.refresh_preset_choices(name.strip() if name else None)
            self.load_preset()
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
        set_choices(self.material, sorted(self.materials) or {"": "No materials imported"})
        self.apply_material_button.setEnabled(bool(self.materials))

    def refresh_preset_choices(self, selected=None):
        catalog = presets.all_presets(self.choice("cutter"))
        names = (presets.STARTING_NAME, *sorted(set(catalog) - {presets.STARTING_NAME}))
        set_choices(self.controls["preset"], names, selected)
        if self.group_rows:
            self.change_group_mode()

    def choose_material_csv(self, *, folder=False):
        path = (QFileDialog.getExistingDirectory(self, "Choose Studio factory folder") if folder
                else self.select_open("Choose Silhouette condition.csv", "CSV files (*.csv *.CSV)"))
        if path:
            try:
                materials.select_condition_csv(path)
            except (OSError, ValueError) as error:
                self.message(str(error))
            else:
                self.load_materials()
                self.refresh_preset_choices()
                self.message(f"Loaded {len(presets.factory_presets(self.choice('cutter')))} Studio materials.")

    def change_cutter(self):
        self.load_materials()
        selected = self.choice("preset")
        self.refresh_preset_choices()
        if selected in presets.factory_presets(self.choice("cutter")) or self.choice("preset") != selected:
            self.load_preset()
        else:
            self.rebuild()

    def apply_material(self):
        selected = self.materials.get(self.material.currentData())
        if selected is None:
            self.message("Import Studio materials, then choose a factory preset.")
            return
        name = self.material.currentData()
        if not self.divisions.currentData() and self.controls["preset"].findData(name) >= 0:
            self.select("preset", name)
            self.load_preset()
        else:
            self.set_blade_settings(for_holder(selected.cut_settings(), self.holder_state.active))
        self.open_cut_settings()

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
        if self.loading:
            return
        profile = layout_presets.available().get(self.choice("layout_preset"))
        if profile is None:
            if layout_presets.is_published(self.artwork):
                self.reset_artwork()
            return
        settings = profile.options()
        self.clear_bundle()
        self.project_shapes = self.reviewed_design = None
        with self.editing():
            for key in ("sheet", "card", "engine", "portrait", "mode", "pattern"):
                self.select(key, settings[key])
            for key in ("radius_mm", "mark_inset_mm", "mark_length_mm", "mark_thickness_mm", "bleed_mm"):
                self.controls[key].setValue(settings[key])
            self.controls["registration"].setChecked(True)
            self.controls["auto_search"].setChecked(True)
            self.skip.clear()
        if isinstance(profile, layout_presets.PublishedTemplate):
            self.use_artwork(profile.artwork(), profile.name)
        else:
            self.use_artwork(None, "Generated card layout")

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
        with self.editing():
            self.select("layout_preset", "")
        self.use_artwork(drawing, path.name + (f" · Page {page}" if page else ""))

    def reset_artwork(self):
        self.clear_bundle()
        self.project_shapes = self.reviewed_design = None
        with self.editing():
            self.select("layout_preset", "")
        self.use_artwork(None, "Generated card layout")

    def use_artwork(self, drawing, label, send_mode="simple", group_defaults=None):
        self.artwork = drawing
        self.clear_scan_result()
        self.group_choices.clear()
        if group_defaults:
            self.group_choices["layer"] = group_defaults
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
                holder_profiles = actions.HOLDER_SETTINGS.values()
                self.group_rows[key] = (
                    form.check("Send this group", enabled, self.rebuild),
                    form.choice("Preset", (actions.CURRENT_SETTINGS, *holder_profiles,
                                           *sorted(presets.all_presets(self.choice("cutter")))),
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
        if mode != "simple":
            options["holder_profiles"] = self.holder_state.profiles(*self.current_holder_profile())
        if options["end_position"] != "below":
            options["end_feed_mm"] = 0
        if artwork:
            options["weed_border_mm"] = self.controls["weed_border_mm"].value()
            options["weed_lines"] = self.choice("weed_lines") if options["weed_border_mm"] else "none"
        if self.bundle:
            options.update(self.bundle.layout, engine="scm", mode=template.ROUNDED, bleed_mm=0, registration=True,
                           skip=(), imported=self.artwork)
        return options

    def update_enabled_fields(self):
        fixed_layout = self.bundle or layout_presets.is_published(self.artwork)
        for key in ("sheet", "portrait", "engine", "card", "mode", "radius_mm"):
            self.controls[key].setEnabled(not fixed_layout)
        device = graphtec.device_by_name(self.choice("cutter"))
        for name, upper in (("force", device.max_pressure), ("speed", device.max_speed)):
            self.blade[name].setToolTip(f"{CONTROL_HELP[name]} Range on {DEVICES[device.name]} is 1–{upper}.")
        self.blade["holder"].setEnabled(device.two_tools)
        self.blade["depth"].setEnabled(device.autoblade and self.blade["holder"].value() == 1)
        self.blade["acceleration"].setEnabled(device.detects_tools)
        for field in QUICK_NUMBERS:
            control = self.blade[field]
            control.slider.setEnabled(control.isEnabled())
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
        self.group_mode_box.setVisible(self.artwork is not None)
        self.group_box.setVisible(self.artwork is not None and self.choice("send_mode") != "simple")
        self.use_generated.setEnabled(self.artwork is not None or self.bundle is not None)

    def rebuild(self):
        if self.loading:
            return
        self.restore_holder()
        self.update_enabled_fields()
        previous = self.job
        self.job = None
        problems = []
        title = "Layout checked"
        if self.bundle and self.artwork is None:
            title = "Cut paths unavailable" if self.bundle_row_error else "Legacy cut file"
            summary = (self.bundle_row_error or
                       "Save the legacy cut file from Sheet. A cut SVG is required for direct cutting.")
        else:
            try:
                self.job = job.build(**self.job_options())
            except (ValueError, KeyError) as error:
                title = "Layout needs attention"
                summary = str(error)
            else:
                current = self.job
                problems = current.blocked
                passes = "pass" if current.settings.passes == 1 else "passes"
                caption = f"{len(current.action_groups)} Send groups" if current.action_groups else (
                    f"{current.preset.name} · {current.settings.passes} {passes}")
                orientation = "portrait" if current.grid.portrait else "landscape"
                summary = (f"{current.grid.sheet_name} {orientation} · "
                           f"{current.grid.sheet_w_mm:g} × {current.grid.sheet_h_mm:g} mm · "
                           f"{len(current.paths)} paths\n{caption}")
                if problems:
                    title = "Layout needs attention"
                    summary += "\n" + problems[0]
                elif not current.registration:
                    title = "Layout checked · Registration off"
                    summary += "\nThe cutter uses sheet coordinates. Use a blank sheet."
                elif current.mask_hints:
                    summary += "\nArtwork is close to a registration mark."
                elif current.warnings:
                    summary += "\n" + current.warnings[0]
        marks = self.job.marks if self.job else None
        if self.pdf and not self.bundle and previous and previous.marks != marks:
            self.invalidate_measurement()
            self.sheet_result.setText("Registration geometry changed. Measure the PDF again.")
        self.summary_title.setText(title)
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
            if self.artwork is None or not self.bundle_checked:
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
        if self.bundle and self.artwork is None:
            return ("Choose another sheet or reopen the ZIP in Sheet." if self.bundle_row_error
                    else "Save the legacy cut file from Sheet.")
        if self.bundle and not self.bundle_checked:
            return "Check the matching PDF in Sheet."
        if plan is None or self.job is not plan or plan.blocked:
            return "Review the current job and resolve its checks."
        if connection != self.connection():
            return "The cutter connection changed. Review the job again."
        refusal = cutters.usb_refusal(connection, plan.device, self.cutter_model.currentData())
        if refusal:
            return refusal
        if not connection[1]:
            return "Select a cutter."
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
        self.send_normal.setVisible(not self.tuning)
        self.guide_page.setVisible(self.tuning)
        self.review_button.setText(f"Review {'Cut Job' if target == 'Send' else target}")
        self.close_print_action.setEnabled(self.pdf is not None)
        self.paired_cut_action.setEnabled(self.bundle is not None)
        self.reset_layout_action.setEnabled(self.artwork is not None or self.bundle is not None)
        self.back_to_send.setVisible(self.tuning)
        self.review_button.setVisible(not self.tuning and (target == "Design" or index != self.tab(target)))
        at_send = not self.tuning and index == self.tab("Send")
        for control in (self.send_button, self.scan_button, self.corner_button):
            control.setVisible(at_send)
        reason = self.send_refusal(self.job, self.connection())
        self.send_button.setEnabled(not reason)
        self.corner_button.setEnabled(not reason)
        self.scan_button.setEnabled(not self.send_refusal(self.job, self.connection(), scan_only=True))
        self.test_button.setEnabled(not self.busy)
        can_check = not self.busy and bool(self.connection()[1])
        self.refresh_cutter.setEnabled(can_check)
        if self.bundle and self.report is not None and not self.bundle_checked:
            self.summary_title.setText("PDF registration needs attention")
        self.update_guide()
        self.update_tuning_preview()
        if not self.tuning:
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
        self.clear_scan_result()
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
            self.bundle = None
            self.bundle_export.hide()
            self.bundle_print.hide()
            self.lock_bundle_controls(False)
            self.clear_printed_sheet()

    def update_sheet_controls(self):
        loaded = self.pdf is not None
        self.close_print.setEnabled(loaded)
        self.measure_button.setEnabled(loaded and self.bundle_row_error is None)
        self.sheet_page_label.setText("Sheet in ZIP" if self.bundle else "PDF page")
        self.sheet_page_number.setEnabled(loaded and (not self.bundle or len(self.bundle.rows) > 1))

    def clear_printed_sheet(self):
        self.pdf = self.bundle_row_error = None
        self.invalidate_measurement(clear_image=True)
        self.sheet_file.setText("Open a print PDF or a matched Print & Cut ZIP.")
        self.sheet_result.setText("Not measured")
        with self.editing():
            self.sheet_page_number.setRange(1, 999)
            self.sheet_page_number.setValue(1)
        self.update_sheet_controls()

    def close_printed_sheet(self):
        if self.bundle:
            self.reset_artwork()
        else:
            self.clear_printed_sheet()
            self.refresh_send()

    def save_bundle_file(self):
        matched, row = self.bundle, self.current_bundle_row()
        if matched is None:
            return
        try:
            name = matched.cut_file_name(row)
            path = self.choose_output("Save Paired Cut File", name)
            if path:
                self.message(f"Saved {matched.save_cut_file(row, path).name}.")
        except bundle.BundleError as error:
            self.message(str(error))

    def save_bundle_print(self):
        matched = self.bundle
        if matched is None:
            return
        path = self.choose_output("Save Print PDF", Path(matched.pdf).name)
        if path:
            try:
                self.message(f"Saved {matched.save_print_pdf(path).name}. Print at actual size.")
            except bundle.BundleError as error:
                self.message(str(error))

    def open_sheet(self):
        path = self.select_open("Open Printed Sheet", "Print PDF or matched ZIP (*.pdf *.PDF *.zip *.ZIP)")
        if not path:
            return
        if path.suffix.casefold() == ".zip" or zipfile.is_zipfile(path):
            try:
                self.load_bundle(path)
            except (OSError, ValueError) as error:
                self.message(str(error))
            else:
                self.tabs.setCurrentIndex(self.tab("Sheet"))
            return
        if self.bundle:
            self.reset_artwork()
        self.pdf = path
        self.sheet_file.setText(path.name)
        with self.editing():
            self.sheet_page_number.setValue(1)
        self.update_sheet_controls()
        self.measure_sheet()
        self.tabs.setCurrentIndex(self.tab("Sheet"))

    def load_bundle(self, path):
        matched = bundle.load(path)
        row = matched.rows[0]
        drawing = matched.artwork(row) if "svg" in row else None
        self.clear_bundle()
        self.bundle, self.pdf = matched, path
        self.bundle_export.show()
        self.bundle_print.show()
        self.reviewed_design = self.project_shapes = None
        self.lock_bundle_controls(True)
        with self.editing():
            self.select("layout_preset", "")
            self.sheet_page_number.setRange(1, len(matched.rows))
            self.sheet_page_number.setValue(1)
        self.use_bundle_row(row, drawing)

    def use_bundle_row(self, row, drawing, error=None):
        self.invalidate_measurement(clear_image=True)
        self.bundle_row_error = error
        self.sheet_file.setText(
            f"{self.pdf.name} · Sheet {row['sheet']} · PDF front page {row['frontPage']}\n"
            + self.bundle.mat_placement())
        self.use_artwork(drawing, row.get("svg") or row["cutFile"])
        self.update_sheet_controls()
        if error:
            self.sheet_result.setText(error)
        else:
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
                self.use_bundle_row(row, None, str(error))
            else:
                self.use_bundle_row(row, drawing)
        else:
            self.measure_sheet()

    def measure_sheet(self):
        path, matched, current = self.pdf, self.bundle, self.job
        if self.loading or path is None or self.bundle_row_error:
            return
        if not matched and not current:
            self.message("Check the layout before measuring the sheet.")
            return
        from . import sheet

        self.invalidate_measurement(clear_image=True)
        token = self.sheet_token = object()
        self.sheet_result.setText("Measuring…")
        self.refresh_send()
        row = self.current_bundle_row()
        marks = matched.marks() if matched else current.marks
        dpi, page = self.sheet_dpi.currentData(), self.sheet_page_number.value()
        orient_scm = layout_presets.is_published(self.artwork)

        def work():
            if matched:
                report, image = matched.measure(row, marks, dpi)
            else:
                report, image = sheet.measure_preview(path, marks, page, dpi, orient_scm=orient_scm)
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
                details = [report.diagnosis()]
                if report.preview_rotation in (90, 270):
                    details.append("Load the printed sheet face-up in the orientation shown in the preview.")
                self.sheet_result.setText("\n".join([*details, *(mark.describe() for mark in report.marks), *issues]))
                self.preview.show_pdf(png, (report.page_w_mm, report.page_h_mm), report)
                self.preview_mode.show()
            self.refresh_send()

        self.tasks.run(work, done)

    def connection(self):
        mode = self.link.currentData()
        if mode == "auto":
            endpoint = self.detected.currentData() or None
            if endpoint and endpoint[0] in bluetooth.BLUETOOTH_MODES:
                return endpoint[0], endpoint[1], None
            return mode, endpoint, None
        return mode, self.identifier.text().strip() or None, self.channel.value() if mode == "bluetooth" else None

    open_link = staticmethod(cutters.open_link)

    def change_model(self):
        model = self.cutter_model.currentData()
        if model in DEVICES:
            self.select("cutter", model)
        elif model == cutters.AUTO:
            self.device_selected()
        self.change_target()

    def refresh_usb(self):
        if self.busy or self.link.currentData() != "auto":
            return
        choices = {**cutters.usb_choices(), **self.wireless_choices}
        items = {"": "Select a cutter", **choices}
        if [(self.detected.itemData(i), self.detected.itemText(i)) for i in range(self.detected.count())] != list(items.items()):
            selected = cutters.detected_choice(choices, self.preferred_endpoint) or ""
            same_cutter = selected and (selected, choices[selected]) == (
                self.detected.currentData(), self.detected.currentText())
            set_choices(self.detected, items, selected)
            if same_cutter:
                return
            self.device_selected()
            self.set_cutter_status(choices.get(selected, "Select a cutter." if choices else "No cutter found."))


    def refresh_wireless(self):
        if self.busy or self.wireless_scanning or self.link.currentData() != "auto":
            return
        self.wireless_scanning = True

        def done(value, error):
            self.wireless_scanning = False
            self.wireless_choices = value if not error else {}
            self.refresh_usb()

        self.tasks.run(cutters.wireless_choices, done)

    def change_connection(self):
        mode = self.link.currentData()
        self.preferred_endpoint = None
        self.advanced_connection_form.setRowVisible(self.identifier, mode != "auto")
        self.advanced_connection_form.setRowVisible(self.channel, mode == "bluetooth")
        self.identifier.clear()
        set_choices(self.detected, {"": "Select a cutter"}, "")
        self.change_target()
        if mode == "auto":
            self.refresh_usb()

    def change_target(self):
        self.set_cutter_status("Cutter status not checked.")
        self.clear_scan_result()
        self.refresh_send()

    def set_cutter_status(self, text):
        self.connection_status.setText(text)

    def select_detected(self):
        if self.link.currentData() == "auto":
            self.preferred_endpoint = self.detected.currentData() or None
        self.device_selected()

    def device_selected(self):
        identifier = self.detected.currentData()
        if self.link.currentData() == "auto":
            model = cutters.auto_model(identifier, self.detected.currentText()) if identifier else None
            if model and self.cutter_model.currentData() == cutters.AUTO:
                self.select("cutter", model.name)
            self.change_target()
            return
        self.identifier.setText(identifier or "")
        device = bluetooth.advertised_model(self.detected.currentText().split(" · ")[0])
        if device and self.cutter_model.currentData() == cutters.AUTO:
            self.select("cutter", device.name)

    def find_cutters(self):
        from . import ble, rfcomm

        mode = self.link.currentData()
        if mode == "auto":
            self.refresh_usb()
            self.refresh_wireless()
            return
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
        self.set_cutter_status("Reading cutter status…")
        self.refresh_send()

        def work():
            with self.open_link(connection) as cutter:
                version = f" · {cutter.firmware_version()}" if connection[0] != "auto" else ""
                return cutter.device, f"{cutter.device.name} · {cutter.status_with_tools()}{version}"

        def done(value, error):
            self.busy = False
            if connection == self.connection():
                self.set_cutter_status(f"Cutter detection unavailable. {error}" if error else value[1])
                if not error and self.cutter_model.currentData() == cutters.AUTO:
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
            prompt += f"\nConnection: {connection[1]}"
        title = "Test Registration?" if scan_only else "Send to Cutter?"
        if self.confirm(title, prompt):
            self.start_send(plan, connection, scan_only=scan_only)

    def confirm_corner_test(self):
        plan, connection = self.job, self.connection()
        reason = self.send_refusal(plan, connection)
        if reason:
            self.message(reason)
            return
        try:
            sample = corner_test.build(plan, self.artwork, self.project_shapes or (), self.controls["bleed_mm"].value())
        except ValueError as error:
            self.message(str(error))
            return
        if self.confirm("Corner Test Cut?", sample.describe()):
            self.start_send(plan, connection, corner=sample)

    def start_send(self, plan, connection, *, scan_only=False, corner=None):
        reason = self.send_refusal(plan, connection, scan_only=scan_only)
        if reason:
            self.message(reason)
            return
        checked = corner.job if corner else plan
        steps = checked.registration_steps() if scan_only else checked.steps
        report, row, matched = self.report, self.current_bundle_row(), self.bundle
        started = monotonic()

        def failed(error):
            notes = plan.registration_notes(report, matched.near_marks(row) if matched else None)
            if report is None:
                notes.insert(0, "Open the print PDF in Sheet to check each mark.")
            if matched:
                notes.append(matched.mat_placement())
            notes.append("Check the print scale, keep all marks visible, and look for glare.")
            route = bluetooth.BLUETOOTH_MODES.get(connection[0], "USB")
            detail = "\n".join((f"{plan.device.name} · {route}",
                                f"Scan failed after {monotonic() - started:.1f} s.",
                                f"PDF front page {row['frontPage']}" if row else "",
                                "Commands sent before the reply", *plan.registration_writes()))
            self.show_scan_result("\n\n".join((str(error), *notes, detail)), reveal=True)

        success = ("Corner test sent." if corner else "Registration scan passed." if scan_only else "Cut sent.")
        self.send_steps(checked.device, connection, steps, success,
                        on_registration_failure=failed, wait_done=corner is not None,
                        on_success=(lambda: self.show_scan_result("Registration passed. No cut paths were sent."))
                        if scan_only else None)

    def clear_scan_result(self):
        self.scan_result.clear()
        self.scan_box.hide()

    def show_scan_result(self, message, *, reveal=False):
        self.scan_result.setText(message)
        self.scan_box.show()
        if reveal:
            index = self.tab("Sheet")
            self.tabs.setCurrentIndex(index)
            self.tabs.widget(index).ensureWidgetVisible(self.scan_box, 0, 12)

    def copy_scan_details(self):
        QApplication.clipboard().setText(self.scan_result.text())
        self.message("Scan details copied.")

    def send_steps(self, device, connection, steps, success, *, on_registration_failure=None,
                   on_success=None, test=False, wait_done=False):
        refusal = cutters.usb_refusal(connection, device, self.cutter_model.currentData())
        if refusal:
            self.message(refusal)
            return
        self.guide.moved()
        self.busy = True
        self.clear_scan_result()
        self.refresh_send()

        def work():
            with self.open_link(connection, device) as cutter:
                if cutter.can_read:
                    state = cutter.status()
                    if state in ("moving", "unloaded") or (state != "ready" and (test or connection[0] != "auto")):
                        raise transport.TransportError(f"The cutter is not ready ({state}).")
                if wait_done and cutter.can_read:
                    return cutter.run(steps, wait_done=True)
                return cutter.run(steps)

        def done(sent, error):
            self.busy = False
            self.refresh_send()
            if isinstance(error, transport.RegistrationFailed) and on_registration_failure:
                on_registration_failure(error)
            elif error is None and on_success:
                on_success()
            self.message(str(error) if error else f"{success} {sent:,} bytes.")

        self.tasks.run(work, done)

    def update_guide(self):
        state = self.guide
        for field in (self.guide_apply, self.guide_edit, self.guide_next, self.guide_reset):
            field.setEnabled(not self.busy)
        connected = starting_guide.has_cutter_endpoint(self.connection())
        self.guide_send.setEnabled(not self.busy and connected and state.can_cut)
        self.guide_use.setEnabled(not self.busy and state.tested_settings is not None)
        self.guide_registration.setEnabled(not self.busy and state.placement is None)
        self.guide_marked_sheet.setEnabled(not self.busy and state.registered)
        self.guide_send.setText("Repeat One Pass" if state.completed else "Cut One Pass…")
        if self.tuning:
            self.footer_status.setText(state.sheet_status)
        status = state.status()
        if self.busy:
            status += "\nWaiting for the cutter to finish…"
        elif not connected:
            status += "\nGo back to Send and select a cutter to run a test pass."
        if state.resume_error:
            status += "\n" + state.resume_error
        self.guide_status.setText(status)
        self.guide_history.setText(state.history_text() or "Completed passes appear here.")
        blocked = self.guide_outcome.blockSignals(True)
        self.guide_outcome.setCurrentIndex(self.guide_outcome.findData(state.outcome))
        self.guide_outcome.blockSignals(blocked)
        self.guide_outcome.setEnabled(not self.busy)
        advice = state.advice()
        self.guide_advice.setText(advice)
        self.guide_advice.setVisible(bool(advice))

    def inspect_test_cut(self):
        self.guide.inspect(self.guide_outcome.currentData())
        self.update_guide()

    def update_tuning_preview(self):
        trial = None
        if self.tuning:
            trial = self.guide.trial
            if trial is None:
                try:
                    trial = self.guide.prepare(self.connection(), self.job_options(), self.job, "square")
                except (ValueError, KeyError):
                    pass
        placement = self.guide.placement if self.tuning else None
        self.preview.show_trial(trial, self.guide.spots if self.tuning else (), placement)
        self.preview_title.setText("Material test sheet" if self.preview.test_placement else "Sheet preview")
        self.preview_mode.setVisible(self.preview.test_placement is None and not self.preview.pdf.isNull())
        self.summary_bar.setVisible(not self.tuning)
        self.details.setVisible(not self.tuning and self.details_toggle.isChecked())

    def set_guide_blade_values(self, values):
        with self.editing():
            for key, value in values.items():
                self.blade[key].setValue(-1 if value is None else value)
        self.rebuild()

    def apply_guide_start(self):
        if self.guide.trial:
            self.next_guide_spot()
        values = self.blade_values()
        device = graphtec.device_by_name(self.choice("cutter"))
        self.set_guide_blade_values(starting_guide.starting_values(
            device, values["holder"] if device.two_tools else 1, values["blade_diameter_mm"]))
        suffix = "" if self.blade["depth"].value() >= 0 else " Set depth on the blade."
        self.message("Starting settings applied." + suffix)

    def use_guide_settings(self):
        settings = self.guide.tested_settings
        if self.busy or settings is None:
            return
        device = graphtec.device_by_name(self.choice("cutter"))
        holder = self.blade["holder"].value() if device.two_tools else 1
        if device != self.guide.trial.sheet.device or settings.holder != holder:
            self.message("Select the tested cutter and tool holder first.")
            return
        self.set_blade_settings(settings)
        self.message(f"Applied the tested settings with {settings.passes} {'pass' if settings.passes == 1 else 'passes'}.")
        self.offer_tested_preset(settings, device.name)

    def offer_tested_preset(self, settings, cutter):
        dialog = QInputDialog(self)
        dialog.setWindowTitle("Save Material Preset?")
        dialog.setLabelText("Enter the material name to save the tested settings for future cuts.")
        dialog.setOkButtonText("Save Preset")
        dialog.setCancelButtonText("Use Without Saving")
        while dialog.exec():
            name = dialog.textValue().strip()
            try:
                presets.save_tested_settings(name, settings, cutter)
            except (OSError, ValueError) as error:
                dialog.setLabelText(str(error))
            else:
                self.refresh_preset_choices(name)
                self.load_preset()
                self.message(f"Saved {name}.")
                break

    def next_guide_spot(self):
        if self.busy:
            return
        self.guide.next_spot()
        self.persist_guide()
        self.update_guide()
        self.update_tuning_preview()

    def reset_guide(self):
        if not self.busy and self.confirm("Start a New Test Sheet?", "Load a fresh test sheet to start at the first spot."):
            self.guide.reset()
            self.persist_guide()
            self.update_guide()
            self.update_tuning_preview()

    def persist_guide(self):
        try:
            self.guide.persist()
        except OSError as error:
            self.message(f"Could not save the marked test sheet history. {error}")
            return False
        return True

    def change_guide_registration(self):
        self.guide.registered = self.guide_registration.isChecked()
        self.update_guide()
        self.update_tuning_preview()

    def save_guide_marked_sheet(self):
        path = self.choose_output("Save Marked Test Sheet", "material-test.svg", "Printable SVG (*.svg)")
        if path:
            try:
                self.guide.write_marked_sheet(path, self.job, self.job_options())
            except (OSError, ValueError, KeyError) as error:
                self.message(str(error))
            else:
                self.message("Saved the marked test sheet. Print at actual size.")
            self.update_guide()
            self.update_tuning_preview()

    def confirm_guide_pass(self):
        if self.busy or not self.guide.can_cut:
            return
        try:
            trial = self.guide.prepare(self.connection(), self.job_options(), self.job, "square")
        except (ValueError, KeyError) as error:
            self.message(str(error))
            return
        if self.guide.completed or self.confirm("Send One Test Pass?", self.guide.prompt(trial)):
            self.start_guide_pass(trial)

    def start_guide_pass(self, trial):
        if self.busy or not self.guide.can_cut:
            return
        try:
            current = self.guide.prepare(self.connection(), self.job_options(), self.job, "square")
        except (ValueError, KeyError) as error:
            self.message(str(error))
            return
        if trial != current:
            self.message("Test settings or cutter connection changed. Review the pass again.")
            return
        refusal = cutters.usb_refusal(trial.connection, trial.sheet.device, self.cutter_model.currentData())
        if refusal:
            self.message(refusal)
            return
        if trial.placement.registered:
            try:
                self.guide.persist(pending=trial)
            except OSError as error:
                self.message(f"Could not save the marked test sheet history. {error}")
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
            if self.persist_guide():
                self.message(str(error) if error else "Pass complete.")

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
        self.open_send_workspace()
        self.refresh_send()

    def review_design(self, project, action):
        artwork = project.artwork(action)
        if not artwork.paths or any(path.length_mm() <= 0 for path in artwork.paths):
            self.design.message("Draw or resize the paths before reviewing.")
            return
        self.tuning = False
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
        groups = editor_model.holder_defaults(artwork)
        grouped = groups or action == editor_model.ALL_ACTIONS
        self.use_artwork(artwork, f"Design · {action}", "layer" if grouped else "simple", groups)
        self.leave_design()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange and hasattr(self, "preview"):
            self.workspaces.setStyleSheet("")
            self.workspaces.setPalette(self.palette())
            self.workspaces.setStyleSheet(workspace_style(self.palette()))
            self.preview.render_sheet()
            if self.design is not None:
                self.design.canvas.redraw()

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
