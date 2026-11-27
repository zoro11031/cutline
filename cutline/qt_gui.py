"""Qt Widgets frontend for the verified Silhouette desktop workflow."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import io
from pathlib import Path
import sys
from time import monotonic
import zipfile

from PySide6.QtCore import QEvent, QPoint, Qt, QTimer
from PySide6.QtGui import QIcon, QKeySequence
from PySide6.QtWidgets import (QApplication, QComboBox, QFileDialog, QGroupBox, QHBoxLayout, QInputDialog, QMenu,
                               QMainWindow, QMessageBox, QPlainTextEdit, QPushButton,
                               QSizePolicy, QSplitter, QStackedWidget, QStyle, QTabWidget, QToolButton, QVBoxLayout,
                               QWidget)

from . import (actions, bluetooth, bundle, config, confirmation, corner_test, editor_model, geometry, graphtec, importer, job,
               layout_presets, materials, presets, regmarks, sheet, starting_guide, template, testcut, toolpath, transport)
from .desktop_help import (BLADE_VALUE_TEXT, CONTROL_HELP, CUT_PROBLEMS, CUT_SETTINGS, DIAL_IN, OPEN_SHEET,
                           REGISTRATION_PROBLEM, SETUP_STEPS)
from .holder_settings import HolderSettings, for_holder
from . import tooling
from .qt_canvas import PREVIEW_MODES, Preview, ZoomControls
from .qt_controls import (AdaptiveActions, Form, Page, SettingsWheel, Tasks, bar, button, footer_bar_widget,
                          set_choices, styled_label, workspace_header, workspace_layout, workspace_style,
                          workspace_tools)

APP_ID = "io.github.zoro11031.Cutline"
ARTWORK_FILTER = "Cut templates (*.svg *.SVG *.dxf *.DXF *.studio3 *.STUDIO3)"
from . import cutters

DEVICES = {device.name: device.name.removeprefix("Silhouette ") for device in graphtec.DEVICES}

# Qt widgets use the same field names and units as the job builder.
BLADE_NUMBERS = (
    ("force", "Force", 1, 40, 1, 0), ("speed", "Speed", 1, 30, 1, 0),
    ("passes", "Passes", 1, graphtec.MAX_PASSES, 1, 0), ("depth", "Blade Depth", -1, 10, 1, 0),
    ("holder", "Tool Holder", 1, 2, 1, 0), ("acceleration", "Acceleration", -1, 3, 1, 0),
    ("blade_diameter_mm", "Tool Offset (mm)", 0, 2.3, 0.05, 2),
    ("corner_start_mm", "Start Extension (mm)", 0, 0.9, 0.1, 1),
    ("corner_end_mm", "End Extension (mm)", 0, 0.9, 0.1, 1),
    ("overcut_mm", "Seam Overcut (mm)", 0, 1000, 0.1, 2),
    ("light_passes", "Light passes", 0, graphtec.MAX_LIGHT_PASSES, 1, 0),
    ("light_force", "Light force", -1, 40, 1, 0),
    ("light_speed", "Light speed", -1, 30, 1, 0),
)
# Staged passes: the first passes lighter, the rest at full settings.
STAGE_NUMBERS = ("light_passes", "light_force", "light_speed")
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


def _app_icon():
    icon = QIcon.fromTheme(APP_ID)
    if not icon.isNull():
        return icon
    root = Path(__file__).resolve().parent.parent
    for candidate in (root / "cutline.ico", root / "share/icons/cutline.ico", root / "share/icons/cutline.svg"):
        if candidate.is_file():
            icon = QIcon(str(candidate))
            if not icon.isNull():
                return icon
    return QIcon()


def _set_windows_app_id():
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except (AttributeError, OSError):
        pass


class Workspaces(QStackedWidget):
    """Keep a hidden workspace from setting the window's minimum size."""
    def __init__(self):
        super().__init__()
        self.currentChanged.connect(lambda _: self.updateGeometry())

    def minimumSizeHint(self):
        page = self.currentWidget()
        return page.minimumSizeHint() if page else super().minimumSizeHint()


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Cutline")
        self.resize(1210, 800)
        self.settings_wheel = SettingsWheel(self)
        self.setWindowIcon(_app_icon())
        self.tasks = Tasks(self)
        from . import windows_update
        self.can_update_windows = windows_update.installed()
        self.update_setup = self.update_dialog = None
        self.settings = config.load_settings()
        self.job = self.artwork = self.project_shapes = self.design = self.reviewed_design = self.design_origin = None
        self.pdf = self.bundle = self.sheet_token = self.report = None
        self.bundle_row_error = None
        self.bundle_checked = self.loading = False
        self.sending = self.checking_status = False
        self.pdf_check_state = sheet.RegistrationDecision.NONE
        self.checked_marks = None
        self.checked_scm_orientation = False
        self.detected_tools = ()
        self.tool_selection = tooling.ToolSelection()
        self.tools_connection = None
        self.corrected_tools = None
        self.bundle_locked = False
        self.active_job = None
        self.cut_control = None
        self.tuning = False
        self.last_send_tab = "Send"
        self.holder_state = HolderSettings()
        self.guide = starting_guide.Guide(config.CONFIG_DIR / "material-test.json")
        self.group_rows = {}
        self.group_choices = {}
        self.pages = {}
        self.controls = {}
        self.blade = {}
        self._initial_workspace_layout = True

        self.workspaces = Workspaces()
        self.setCentralWidget(self.workspaces)
        self.workspaces.setStyleSheet(workspace_style(self.palette()))
        self.send_view = setup = QWidget()
        setup.setObjectName("workspace")
        self.workspaces.addWidget(setup)
        content = workspace_layout(setup)
        header, self.menu_bar, self.design_button, self.send_workspace = workspace_header(
            setup, self.open_design, self.open_send_workspace, active="Send")
        content.addWidget(header)
        self.workspace_split = split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(4)
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
        self.preview.on_spot_clicked = self.on_preview_spot_clicked
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

        self.bundle_notice_bar, bundle_notice_row = bar(preview_panel, (12, 8, 12, 8))
        self.bundle_notice = styled_label(role="body")
        self.bundle_notice.setWordWrap(True)
        self.bundle_notice.setText("The cut paths are locked to the printed sheet. "
                                   "Open Sheet to review the match or clear it.")
        bundle_notice_row.addWidget(self.bundle_notice, 1)
        bundle_notice_row.addWidget(button("Open Sheet", lambda: self.tabs.setCurrentIndex(self.tab("Sheet")),
                                           role="secondary"))
        self.bundle_notice_bar.hide()
        right.addWidget(self.bundle_notice_bar)

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

        footer_bar, footer = footer_bar_widget(setup)
        self.footer_status = styled_label(role="body")
        self.footer_status.setWordWrap(True)
        # The status gives way first: at compact widths it wraps and shrinks so
        # the actions stay visible, in order, rather than being pushed off.
        self.footer_status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.footer_status.setMinimumWidth(0)
        footer.addWidget(self.footer_status, 1)
        self.review_button = button("Review Cut Job", self.follow_review, primary=True)
        self.send_button = button("Send to Cutter", self.confirm_send, primary=True,
                                  icon=("media-playback-start", QStyle.StandardPixmap.SP_MediaPlay))
        self.send_button.setMinimumHeight(32)
        self.back_to_send = button("Exit Material Test", self.close_material_guide, role="secondary")
        self.guide_send = button("Cut One Pass…", self.confirm_guide_pass, primary=True)
        self.pause_cut = button("Pause Cut", lambda: self.cut_control.toggle_pause(), role="secondary")
        self.cancel_cut = button("Cancel Cut", lambda: self.cut_control.cancel(), role="destructive")
        footer.addWidget(self.review_button)
        footer.addWidget(self.send_button)
        footer.addWidget(self.pause_cut)
        footer.addWidget(self.cancel_cut)
        footer.addWidget(self.back_to_send)
        footer.addWidget(self.guide_send)
        content.addWidget(footer_bar)

        with self.editing():
            self.layout_page()
            self.sheet_page()
            self.send_page()
            # Open on the material used last, when it still exists.
            last = self.settings.get("last_preset")
            if isinstance(last, str) and self.controls["preset"].findData(last) >= 0:
                self.select("preset", last)
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
        # Shortcuts are scoped to the Send page, so Design's own keys (Ctrl+O
        # opens a project there) never reach it. Nothing that moves the
        # cutter gets one: sending stays a deliberate click.
        def command(menu, title, callback, *keys):
            action = menu.addAction(title)
            action.triggered.connect(lambda: callback())
            if keys:
                action.setShortcuts([QKeySequence(key) for key in keys])
                action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
                self.send_view.addAction(action)
            return action

        files = self.menu_bar.addMenu("&File")
        self.open_artwork_action = command(files, "Open Cut Template…", self.open_artwork, "Ctrl+O")
        self.open_print_action = command(files, "Open Print File…", self.open_sheet, "Ctrl+Shift+O")
        self.close_print_action = command(files, "Close Print File", self.close_printed_sheet)
        self.paired_cut_action = command(files, "Save Paired Cut File…", self.save_bundle_file)
        files.addSeparator()
        command(files, "Export Cut Templates…", self.export_job)

        edit = self.menu_bar.addMenu("&Edit")
        command(edit, "Edit Current Cut Template", lambda: self.open_design(replace_design=True), "Ctrl+F1")
        self.reset_layout_action = command(edit, "Use Generated Layout", self.reset_artwork)

        view = self.menu_bar.addMenu("&View")
        for number, index in enumerate(self.pages.values(), 1):
            command(view, self.tabs.tabText(index), lambda page=index: self.tabs.setCurrentIndex(page),
                    f"Ctrl+{number}")
        view.addSeparator()
        command(view, "Zoom In", lambda: self.preview.zoom_by(1.25), "Ctrl+=", "Ctrl++")
        command(view, "Zoom Out", lambda: self.preview.zoom_by(0.8), "Ctrl+-")
        command(view, "Fit Preview", self.preview.fit, "Ctrl+0")

        settings = self.menu_bar.addMenu("&Settings")
        command(settings, "Cut Settings", self.open_cut_settings)
        command(settings, "Advanced Send Settings", self.open_send_advanced)
        help_menu = self.menu_bar.addMenu("&Help")
        command(help_menu, "Setup Guide", self.show_setup_guide)
        command(help_menu, "Troubleshoot a Cut…", self.troubleshoot_cut)
        if self.can_update_windows:
            command(help_menu, "Check for Updates…", self.check_for_updates)

    def check_for_updates(self):
        from .qt_update import UpdateDialog

        if self.update_dialog is None:
            self.update_dialog = UpdateDialog(self)
            self.update_dialog.check()
        dialog = self.update_dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def open_cut_settings(self):
        """Show the editable cut settings. An open material test stays open:
        only Exit Material Test closes one."""
        self.tabs.setCurrentIndex(self.tab("Send"))
        page = self.tabs.widget(self.tab("Send"))
        def reveal_settings():
            position = self.settings_box.mapTo(page.widget(), QPoint()).y()
            page.verticalScrollBar().setValue(max(0, position - 8))

        QTimer.singleShot(0, self, reveal_settings)

    def open_send_advanced(self):
        self.tabs.setCurrentIndex(self.tab("Send"))
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
        source = page.group("Cut template")
        self.source_label = source.note("Generated card layout")
        self.open_artwork_button = source.push("Open Cut Template…", self.open_artwork)
        self.draw_in_design_btn = source.push("Draw in Design", lambda: self.open_design(blank=True))
        self.use_generated = source.push("Use Generated Layout", self.reset_artwork)
        layout = page.group("Media and cards")
        self.choose(layout, "layout_preset", "Layout preset",
                    {"": "Custom", **{key: value.name for key, value in layout_presets.desktop_presets().items()}},
                    default=layout_presets.DEFAULT_KEY, changed=self.apply_layout_preset)
        self.choose(layout, "sheet", "Sheet", sorted(geometry.SHEETS), config.DEFAULT_SHEET)
        self.choose(layout, "portrait", "Orientation", {"best": "Best fit", True: "Portrait", False: "Landscape"})
        self.choose(layout, "card", "Card size", sorted(geometry.CARDS), config.DEFAULT_CARD)
        self.layout_advanced = advanced = page.advanced()
        format_options = advanced.group("Cut layout")
        self.choose(format_options, "engine", "Layout engine", {"scm": "Silhouette Card Maker", "centered": "Centered grid"})
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
        self.sheet_file = form.note("Open the PDF you printed.")
        self.close_print = button("Close File", self.close_printed_sheet)
        self.open_print_button = button("Open Print File…", self.open_sheet)
        form.actions(self.open_print_button, self.close_print)
        self.bundle_export = form.push("Save Paired Cut File…", self.save_bundle_file)
        self.bundle_print = form.push("Save Print PDF…", self.save_bundle_print)
        self.bundle_export.hide()
        self.bundle_print.hide()
        self.sheet_page_number = form.number("Page", 1, 1, 999, changed=self.change_sheet_page)
        self.sheet_page_label = form.labelForField(self.sheet_page_number)
        self.measure_button = form.push("Check PDF Marks", self.measure_sheet)
        result = page.group("PDF registration check")
        self.sheet_result = result.note("Not measured")
        scan = page.group("Last cutter scan")
        self.scan_box = scan.parentWidget()
        self.scan_result = scan.note()
        self.scan_result.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.copy_scan = scan.push("Copy Scan Details", self.copy_scan_details)
        scan.push("Troubleshoot Registration…", lambda: self.troubleshoot_cut(REGISTRATION_PROBLEM))
        self.clear_scan_result()
        self.sheet_advanced = advanced = page.advanced()
        self.sheet_dpi = advanced.group("PDF check").choice("Measurement resolution", (150, 200, 300), 200, self.measure_sheet)
        self.update_sheet_controls()
        self.install_page("Sheet", page)

    def add_advanced_cut_settings(self, advanced):
        blade_details = advanced.group("Blade details")
        for key, label, lo, hi, step, decimals in BLADE_NUMBERS:
            if key not in self.blade and key not in STAGE_NUMBERS:
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
        self.cutter_box = cutter.parentWidget()
        self.choose(cutter, "cutter", "Resolved cutter", DEVICES, "Silhouette Cameo 4", self.change_cutter)
        cutter.setRowVisible(self.controls["cutter"], False)
        self.cutter_model = cutter.choice("Cutter model", cutters.MODELS, cutters.AUTO, self.change_model)
        self.wireless_choices = {}
        self.wireless_scanning = False
        self.search_pending = False
        self.preferred_endpoint = None
        self.verified_wireless = None
        self.detected = cutter.choice("Selected cutter", {"": "Select a cutter"}, changed=self.select_detected)
        self.connection_status = cutter.note("Cutter status not checked.")
        # Offered only while no cutter is available, as in Silhouette Studio.
        self.cutter_form = cutter
        self.manual_scanning = False
        self.scan_cutters = button(cutters.SCAN_LABEL, self.on_scan_cutters, primary=True)
        cutter.addRow(self.scan_cutters)
        cutter.setRowVisible(self.scan_cutters, False)
        self.discover = button("Refresh Cutters", self.find_cutters)
        self.refresh_cutter = button("Check Status", lambda: self.check_device())
        cutter.actions(self.discover, self.refresh_cutter)

        groups = normal.group("Cut groups")
        self.group_box = groups.parentWidget()
        self.group_details = QWidget()
        self.group_layout = QVBoxLayout(self.group_details)
        self.group_layout.setContentsMargins(0, 0, 0, 0)
        groups.addRow(self.group_details)

        self.material_guide = button("Dial In a Material…", self.open_material_guide, role="secondary")
        settings = normal.group("Cut Settings", action=self.material_guide)
        self.cut_form = settings
        self.settings_box = settings.parentWidget()
        self.blade["holder"] = settings.holders(self.rebuild)
        self.tool_picker = settings.choice("Blade", (), changed=self.change_tool_selection)
        self.tool_picker.setToolTip("Choose the blade installed in this holder if detection is wrong.")
        self.choose(settings, "preset", "Material", sorted(presets.all_presets()), config.DEFAULT_PRESET,
                    self.load_preset)
        # One place to choose a material: where it comes from sits under the
        # choice, and saving the tuned values sits after the values.
        self.material_note = settings.note()
        self.material_note.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        for key, label, low, high, step, decimals in BLADE_NUMBERS:
            if key in QUICK_NUMBERS:
                self.blade[key] = settings.number(label, low, low, high, step, decimals, self.rebuild,
                                                  slider=True, special_text=BLADE_VALUE_TEXT.get(key, ""))
            if key == "passes":
                self.add_stage_controls(settings)
        # One width for every value box, so the sliders beside them line up
        # even when a box reads "Same as Speed".
        values = [self.blade[key] for key, *_ in BLADE_NUMBERS if key in QUICK_NUMBERS or key in STAGE_NUMBERS]
        widest = max(field.sizeHint().width() for field in values)
        for field in values:
            field.setFixedWidth(widest)
        self.update_stages(rebuild=False)
        self.automatic_depth_maximum = self.blade["depth"].maximum()
        for key, label in BLADE_SWITCHES[1:3]:
            self.blade[key] = settings.check(label, changed=self.rebuild)
        self.save_preset_btn = button("Update Preset", self.save_preset, role="secondary")
        self.save_new_preset_btn = button("Save as New…", self.save_new_preset, role="secondary")
        self.preset_actions = AdaptiveActions((self.save_preset_btn, self.save_new_preset_btn))
        settings.addRow(self.preset_actions)

        registration = normal.group("Registration")
        self.registration_box = registration.parentWidget()
        self.check(registration, "registration", "Scan for printed marks", True)
        self.scan_button = button("Test Registration", lambda: self.confirm_send(scan_only=True), role="secondary")
        self.corner_button = button("Corner Test Cut…", self.confirm_corner_test, role="secondary")
        self.scan_button.setToolTip("Scan the printed marks without cutting.")
        self.corner_button.setToolTip("Cut a single 3 mm square outside the artwork in a clear corner.")
        registration.actions(self.scan_button, self.corner_button)

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
        library = advanced.group("Material library")
        self.delete_preset_btn = button("Delete Preset", self.delete_selected_preset, role="secondary")
        self.import_csv_btn = button("Import CSV…", self.choose_material_csv, role="secondary")
        self.import_folder_btn = button("Import Folder…", lambda: self.choose_material_csv(folder=True),
                                        role="secondary")
        self.restore_materials_btn = button("Restore Defaults…", self.restore_materials, role="secondary")
        library.actions(self.delete_preset_btn)
        library.addRow(AdaptiveActions((self.import_csv_btn, self.import_folder_btn)))
        library.actions(self.restore_materials_btn)

        self.guide_page = page.section()
        guide = self.guide_page.group("Dial In a Material")
        self.guide_box = guide.parentWidget()
        guide.note(starting_guide.GUIDE_TEXT)
        self.guide_shape = guide.choice("Test shape", testcut.TEST_SHAPES, self.guide.shape,
                                       self.change_guide_sample)
        self.guide_size = guide.choice("Sample size", starting_guide.SAMPLE_SIZES, self.guide.size_mm,
                                      self.change_guide_sample)
        for field in (self.guide_shape, self.guide_size):
            field.setToolTip("Changing the sample after a cut moves to a fresh spot.")
        self.guide_spot_selector = guide.number("Test spot", 1, 1, 999, 1, 0,
                                                self.on_guide_spot_selected)
        self.guide_spot_selector.setToolTip("Or click a spot in the preview.")
        self.add_guide_slots(guide)
        self.guide_status = guide.note()
        self.guide_retry = button("Didn’t Cut — Retry", self.retry_guide_spot)
        self.guide_retry.setToolTip("The blade did not cut this spot. Unlock it and try again.")
        self.guide_confirm_pass = button("Pass Finished — Count It", self.accept_unconfirmed_guide_pass)
        self.guide_confirm_pass.setToolTip("The last pass finished cleanly. Add it to this spot’s pass count and continue.")
        self.guide_recovery = AdaptiveActions((self.guide_retry, self.guide_confirm_pass))
        guide.addRow(self.guide_recovery)
        self.guide_recovery.hide()
        self.guide_form = guide
        guide.addRow("Cut with", self.guide_slot_choice)
        self.guide_batch_passes = guide.number("Passes to cut", 1, 1, starting_guide.MAX_JOB_PASSES, 1, 0,
                                               self.on_guide_batch_changed)
        self.guide_batch_passes.setToolTip("How many passes to cut in one run on this spot.")
        self.guide_next = button("Next Spot", self.next_guide_spot)
        self.guide_next.setToolTip("Move to untouched stock.")
        self.guide_use = button("Use Tested Settings…", self.use_guide_settings)
        self.guide_use.setToolTip("Copy what this spot was cut with into Cut Settings, and optionally save it "
                                  "as a material.")
        self.guide_apply = button("Use Starting Settings", self.apply_guide_start)
        self.guide_apply.setToolTip("Reset Cut Settings to a known starting point for this blade.")
        self.guide_more = AdaptiveActions((self.guide_next, self.guide_use, self.guide_apply))
        guide.addRow(self.guide_more)
        extras = self.guide_page.advanced("Test Options")
        extra = extras.group("Test sheet")
        self.guide_registration = extra.check("Use Registration Marks", self.guide.registered,
                                              self.change_guide_registration)
        self.guide_marked_sheet = extra.push("Save Marked Test Sheet…", self.save_guide_marked_sheet)
        self.guide_reset = extra.push("New Test Sheet", self.reset_guide)
        self.guide_history = extras.group("Passes by spot").note()
        help_form = extras.group("Troubleshooting")
        self.guide_outcome = help_form.choice("Optional symptom", starting_guide.OUTCOMES, changed=self.inspect_test_cut)
        self.guide_advice = help_form.note()
        help_form.note(starting_guide.TUNING_TIPS)
        help_form.push("Troubleshoot a Cut…", self.troubleshoot_cut)
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
        tune = dialog.addButton("Resume Material Test" if self.guide.has_tests else "Dial In a Material…",
                                QMessageBox.ButtonRole.ActionRole)
        dialog.addButton(QMessageBox.StandardButton.Close)
        dialog.exec()
        if dialog.clickedButton() is tune:
            self.open_material_guide()

    def troubleshoot_cut(self, symptom=None):
        if not isinstance(symptom, str) or symptom not in CUT_PROBLEMS:
            symptom, accepted = QInputDialog.getItem(self, "Troubleshoot a Cut", "What happened?",
                                                      list(CUT_PROBLEMS), editable=False)
            if not accepted:
                return
        text, action = CUT_PROBLEMS[symptom]
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Troubleshoot a Cut")
        dialog.setText(symptom)
        dialog.setInformativeText(text)
        follow = dialog.addButton(action, QMessageBox.ButtonRole.ActionRole) if action else None
        dialog.addButton(QMessageBox.StandardButton.Close)
        dialog.exec()
        if follow is not None and dialog.clickedButton() is follow:
            self.follow_troubleshooting(action)

    def follow_troubleshooting(self, action):
        if action == OPEN_SHEET:
            self.tabs.setCurrentIndex(self.tab("Sheet"))
        elif action == DIAL_IN:
            self.open_material_guide()
        elif action == CUT_SETTINGS:
            self.open_cut_settings()

    def open_material_guide(self):
        index = self.tab("Send")
        self.tabs.setCurrentIndex(index)
        self.tuning = True
        self.start_guide_slots()
        if self.guide.placement is None:
            try:
                self.guide.placement = self.guide.make_placement(self.job, self.job_options())
            except Exception:
                pass
        self.refresh_send()
        scroll = self.tabs.widget(index)
        QTimer.singleShot(0, self, lambda: scroll.verticalScrollBar().setValue(self.guide_box.y() - 12))

    def open_send_workspace(self):
        """The Send workspace toggle. An open material test stays open:
        only Exit Material Test closes one."""
        self.tabs.setCurrentIndex(self.tab("Send"))

    def close_material_guide(self):
        self.tuning = False
        self.tabs.widget(self.tab("Send")).verticalScrollBar().setValue(0)
        self.refresh_send()

    def change_page(self, index):
        if self.workspaces.currentIndex() == 0:
            for name, idx in self.pages.items():
                if idx == index:
                    self.last_send_tab = name
                    break
        self.refresh_send()

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
        self.update_material_details()
        self.set_blade_settings(for_holder(selected.cut_settings(), slot))
        if self.settings.get("last_preset") != selected.name:
            self.settings["last_preset"] = selected.name
            self.save_settings("last_preset", selected.name)

    def current_holder_profile(self):
        preset = presets.get(self.choice("preset"), self.choice("cutter"))
        return preset, replace(preset.cut_settings(), **self.blade_values())

    def restore_holder(self):
        device = graphtec.device_by_name(self.choice("cutter"))
        holder = self.blade["holder"]
        slot = holder.value() if device.two_tools else 1
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
            self.set_blade_settings(settings)
        self.update_material_details()

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

    def update_material_details(self):
        cutter, name = self.choice("cutter"), self.choice("preset")
        preset = getattr(self, "materials", {}).get(name)
        self.material_note.setText((preset.note or preset.description) if preset else "")
        action = presets.removal_action(name, cutter) if preset else ""
        self.delete_preset_btn.setText(f"Revert ‘{name}’ to Default" if action == "restore"
                                       else f"Delete ‘{name}’")
        self.delete_preset_btn.setEnabled(bool(action) and not self.settings_locked())
        self.delete_preset_btn.setToolTip("" if action else "Built-in presets cannot be deleted.")
        self.save_preset_btn.setToolTip(f"Save the current values to ‘{name}’.")

    def material_buttons(self):
        return (self.save_preset_btn, self.save_new_preset_btn, self.delete_preset_btn,
                self.import_csv_btn, self.import_folder_btn, self.restore_materials_btn)

    @property
    def busy(self):
        return self.sending or self.checking_status

    def can_import_template(self):
        return self.active_job is None and not self.sending

    def settings_locked(self):
        """A sent job owns its settings until it ends."""
        return self.active_job is not None or self.sending

    def delete_selected_preset(self):
        if self.settings_locked():
            self.message("The cutter is running this job. Wait for it to finish before changing settings.")
            return
        name, cutter = self.choice("preset"), self.choice("cutter")
        action = presets.removal_action(name, cutter)
        if not action:
            return
        restore = action == "restore"
        title = "Revert to Default" if restore else "Delete Preset"
        prompt = (f"Remove your saved changes to ‘{name}’ and restore the original settings?"
                  if restore else f"Delete the saved preset ‘{name}’?")

        if not self.confirm(title, prompt):
            return
        if not presets.delete_preset(name, cutter):
            self.message(f"Could not remove ‘{name}’.")
            return
        self.holder_state.drafts = {slot: (preset, settings) for slot, (preset, settings)
                                    in self.holder_state.drafts.items() if preset.name != name}
        self.refresh_preset_choices()
        self.load_materials()
        self.load_preset()
        self.message(f"Restored ‘{name}’." if restore else f"Deleted ‘{name}’.")

    def save_preset(self, name=None):
        if self.settings_locked():
            self.message("The cutter is running this job. Wait for it to finish before changing settings.")
            return
        target_name = self.choice("preset")
        values = self.blade_values()
        try:
            saved = presets.save_blade_settings(
                target_name, new_name=name, cutter=self.choice("cutter"),
                **{key: values.pop(key) for key in ("force", "speed", "depth", "passes")},
                blade_options=values)
        except (OSError, ValueError) as error:
            self.message(str(error))
            return
        if saved is not None:
            active_name = name.strip() if name is not None else target_name
            self.refresh_preset_choices(active_name)
            self.load_materials()
            self.rebuild()
        self.message("Preset saved." if saved else "Preset settings are already saved.")

    def save_new_preset(self):
        name, accepted = QInputDialog.getText(self, "Save as New Preset", "Preset name")
        if accepted:
            self.save_preset(name)

    def load_materials(self):
        self.materials = presets.all_presets(self.choice("cutter"))
        self.update_material_details()

    def refresh_preset_choices(self, selected=None):
        catalog = presets.all_presets(self.choice("cutter"))
        names = (presets.STARTING_NAME, *sorted(set(catalog) - {presets.STARTING_NAME}))
        set_choices(self.controls["preset"], names, selected)
        if self.group_rows:
            self.change_group_mode()

    def choose_material_csv(self, *, folder=False):
        if self.settings_locked():
            self.message("The cutter is running this job. Wait for it to finish before changing settings.")
            return
        path = (QFileDialog.getExistingDirectory(self, "Choose Studio factory folder") if folder
                else self.select_open("Choose Silhouette condition.csv", "CSV files (*.csv *.CSV)"))
        if path:
            try:
                materials.select_condition_csv(path)
            except (OSError, ValueError) as error:
                self.message(str(error))
            else:
                active = self.choice("preset")
                self.refresh_preset_choices()
                self.load_materials()
                if self.choice("preset") != active:
                    self.load_preset()
                self.message(f"Loaded {len(presets.factory_presets(self.choice('cutter')))} material presets.")

    def change_cutter(self):
        selected = self.choice("preset")
        self.refresh_preset_choices()
        self.load_materials()
        active = self.materials[self.choice("preset")]
        if active.factory_id or active.name != selected:
            self.load_preset()
        else:
            self.rebuild()

    def restore_materials(self):
        if self.settings_locked():
            self.message("The cutter is running this job. Wait for it to finish before changing settings.")
            return
        if not self.confirm("Restore Default Materials?",
                            "Edits to built-in presets will be backed up. Custom presets stay available."):
            return
        active = self.choice("preset")
        is_override = presets.removal_action(active, self.choice("cutter")) == "restore"
        try:
            count, _ = presets.restore_default_materials()
        except OSError as error:
            self.message(str(error))
            return
        if count:
            self.refresh_preset_choices()
            self.load_materials()
            if self.choice("preset") != active or is_override:
                self.load_preset()
        self.message(f"Restored {count} built-in presets." if count else "Default materials are already restored.")

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
        profile = layout_presets.desktop_presets().get(self.choice("layout_preset"))
        if profile is None:
            if layout_presets.is_published(self.artwork):
                self.reset_artwork()
            return
        settings = profile.options()
        self.clear_bundle()
        self.project_shapes = None
        self.forget_sent_design()
        with self.editing():
            for key in ("sheet", "card", "engine", "portrait", "mode", "pattern"):
                self.select(key, "best" if key == "portrait" and settings[key] is None else settings[key])
            for key in ("radius_mm", "mark_inset_mm", "mark_length_mm", "mark_thickness_mm", "bleed_mm"):
                self.controls[key].setValue(settings[key] or 0)
            self.controls["registration"].setChecked(True)
            self.controls["auto_search"].setChecked(True)
            self.skip.clear()
        if isinstance(profile, layout_presets.PublishedTemplate):
            self.use_artwork(profile.artwork(), profile.name)
        else:
            self.use_artwork(None, "Generated card layout")

    def open_artwork(self):
        if not self.can_import_template():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Open Cut Template", "", ARTWORK_FILTER)
        if path:
            self.load_artwork(Path(path))

    def load_artwork(self, path, page=None):
        if not self.can_import_template():
            return
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
        self.project_shapes = None
        self.forget_sent_design()
        with self.editing():
            self.select("layout_preset", "")
        self.use_artwork(drawing, path.name + (f" · Page {page}" if page else ""))

    def reset_artwork(self):
        if self.settings_locked():
            return
        self.clear_bundle()
        self.project_shapes = None
        self.forget_sent_design()
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
        for name in ("depth", "acceleration", "light_force", "light_speed"):
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
        options["manual_depth"] = self.tool_selection.manual_depth
        if options["manual_depth"] and values["holder"] == 1:
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
        active = self.settings_locked()
        self.detected.setEnabled(not active and not self.checking_status)
        self.tool_picker.setEnabled(not active and not self.checking_status and bool(self.connection()[1]))
        device = graphtec.device_by_name(self.choice("cutter"))
        holders = self.blade["holder"].holders
        holders.button(1).setEnabled(not active and device.two_tools)
        holders.button(2).setEnabled(not active and device.two_tools)
        for group in self.group_rows.values():
            for field in group:
                field.setEnabled(not active)
        if active:
            for c in self.controls.values():
                c.setEnabled(False)
            for c in self.blade.values():
                c.setEnabled(False)
                if hasattr(c, "slider"):
                    c.slider.setEnabled(False)
            self.cutter_model.setEnabled(False)
            self.link.setEnabled(False)
            self.identifier.setEnabled(False)
            self.channel.setEnabled(False)
            self.skip.setEnabled(False)
            self.use_generated.setEnabled(False)
            for c in self.material_buttons():
                c.setEnabled(False)
            return
        for c in self.blade.values():
            c.setEnabled(True)
        for c in self.controls.values():
            c.setEnabled(True)
        self.cutter_model.setEnabled(not self.checking_status)
        self.link.setEnabled(not self.checking_status)
        self.identifier.setEnabled(not self.checking_status)
        self.channel.setEnabled(not self.checking_status)
        self.skip.setEnabled(True)
        for c in self.material_buttons():
            c.setEnabled(True)
        self.update_material_details()
        fixed_layout = self.bundle or layout_presets.is_published(self.artwork)
        for key in ("sheet", "portrait", "engine", "card", "mode", "radius_mm"):
            self.controls[key].setEnabled(not fixed_layout)
        if self.bundle_locked:
            for key in self.BUNDLE_LOCKED:
                self.controls[key].setEnabled(False)
            self.skip.setEnabled(False)
        for name, upper in (("force", device.max_pressure), ("speed", device.max_speed)):
            self.blade[name].setToolTip(f"{CONTROL_HELP[name]} Range on {DEVICES[device.name]} is 1–{upper}.")
        self.blade["holder"].setEnabled(device.two_tools)
        self.update_holder_labels()
        depth = self.blade["depth"]
        maximum = tooling.MANUAL_DEPTH if self.tool_selection.manual_depth else self.automatic_depth_maximum
        if depth.maximum() != maximum:
            with self.editing():
                depth.setMaximum(maximum)
                depth.slider.setMaximum(maximum)
        depth.setEnabled(device.autoblade and self.blade["holder"].value() == 1
                         and not self.tool_selection.manual_depth)
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
        self.group_box.setVisible(self.shows_cut_groups())
        can_reset = self.artwork is not None or self.bundle is not None
        self.use_generated.setEnabled(can_reset)
        self.use_generated.setToolTip(
            "Discard the custom cut template and restore the generated card layout."
            if can_reset
            else "Already using the generated layout."
        )

    def add_stage_controls(self, form):
        """Staged passes, under Passes: a box that opens the light pass settings."""
        self.stage_toggle = form.check("Lighter first passes", changed=self.toggle_stages)
        self.stage_toggle.setToolTip("Cut the first passes lighter, then finish at Force and Speed. "
                                     "Useful when a full first pass lifts a laminated edge.")
        self.staged_form = form
        for key, label, lo, hi, step, decimals in BLADE_NUMBERS:
            if key in STAGE_NUMBERS:
                self.blade[key] = form.number(label, lo, lo, hi, step, decimals, self.update_stages,
                                              slider=True, special_text=BLADE_VALUE_TEXT[key])
                self.blade[key].setToolTip(CONTROL_HELP[key])
        self.stage_note = form.note()

    GUIDE_SLOT_KEYS = ("force", "speed", "depth")

    def add_guide_slots(self, form):
        """Cut Settings A and B: two settings to compare, each collapsible on
        its own. The dropdown by Cut One Pass picks which one cuts, and each
        pass records it by name."""
        self.guide_headers, self.guide_fields, self.guide_bodies = {}, {}, {}
        for name in ("A", "B"):
            header = QToolButton(text=f"Cut Settings {name}", checkable=True, arrowType=Qt.ArrowType.RightArrow,
                                 toolButtonStyle=Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            header.setProperty("role", "disclosure")
            body = QWidget()
            fields = Form(body)
            fields.setContentsMargins(18, 0, 0, 4)
            self.guide_fields[name] = {}
            for key, label, low, high, step, decimals in BLADE_NUMBERS:
                if key in self.GUIDE_SLOT_KEYS:
                    field = fields.number(label, low, low, high, step, decimals,
                                          lambda name=name: self.change_guide_slot(name),
                                          slider=True, special_text=BLADE_VALUE_TEXT.get(key, ""))
                    field.setAccessibleName(f"Cut Settings {name} {label}")
                    self.guide_fields[name][key] = field
            form.addRow(header)
            form.addRow(body)
            form.setRowVisible(body, False)
            header.toggled.connect(lambda shown, name=name: self.show_guide_slot(name, shown))
            self.guide_headers[name], self.guide_bodies[name] = header, body
        self.guide_slot_form = form
        self.guide_slot_choice = QComboBox()
        set_choices(self.guide_slot_choice, {"A": "Cut Settings A", "B": "Cut Settings B"}, "A")
        self.guide_slot_choice.setAccessibleName("Settings for the next pass")
        self.guide_slot_choice.setToolTip("Which cut settings the next pass uses.")
        self.guide_slot_choice.currentIndexChanged.connect(lambda _i: self.use_guide_slot())

    @property
    def guide_slot(self):
        return self.guide_slot_choice.currentData()

    def guide_slot_values(self, name):
        return {key: (None if field.value() < 0 else field.value())
                for key, field in self.guide_fields[name].items()}

    def _show_guide_slot_values(self, name, values):
        for key, field in self.guide_fields[name].items():
            blocked = field.blockSignals(True)
            field.setValue(-1 if values[key] is None else values[key])
            if hasattr(field, "slider"):
                field.slider.blockSignals(True)
                field.slider.setValue(field.value())
                field.slider.blockSignals(False)
            field.blockSignals(blocked)

    def start_guide_slots(self):
        """A new test starts A from Cut Settings and B from light pass settings, with A open."""
        main = {key: (None if self.blade[key].value() < 0 else self.blade[key].value())
                for key in self.GUIDE_SLOT_KEYS}
        light = dict(main)
        if self.blade["light_passes"].value() > 0:
            for key, stage_key in (("force", "light_force"), ("speed", "light_speed")):
                value = self.blade[stage_key].value()
                if value >= 0:
                    light[key] = value
        self._show_guide_slot_values("A", main)
        self._show_guide_slot_values("B", light)
        self.guide_headers["A"].setChecked(True)
        self.guide_slot_choice.setCurrentIndex(self.guide_slot_choice.findData("A"))

    def show_guide_slot(self, name, shown):
        self.guide_headers[name].setArrowType(Qt.ArrowType.DownArrow if shown else Qt.ArrowType.RightArrow)
        self.guide_slot_form.setRowVisible(self.guide_bodies[name], shown)

    def use_guide_slot(self):
        """The chosen setting is what the next pass is planned and checked with."""
        self.set_guide_blade_values(self.guide_slot_values(self.guide_slot))

    def change_guide_slot(self, name):
        if name == self.guide_slot:
            self.use_guide_slot()

    def sync_guide_slot(self):
        """Settings changed from elsewhere (Use Starting Settings, a material)
        land in the chosen setting."""
        if not hasattr(self, "guide_fields"):
            return
        values = {key: (None if self.blade[key].value() < 0 else self.blade[key].value())
                  for key in self.GUIDE_SLOT_KEYS}
        if values != self.guide_slot_values(self.guide_slot):
            self._show_guide_slot_values(self.guide_slot, values)

    @property
    def guide_pass_name(self):
        """Every pass records its setting; the history shows the names only
        once a spot has passes from both."""
        return self.guide_slot

    def _row_of(self, widget):
        """The widget a form row was added with: sliders and wrapped
        checkboxes sit inside a row widget of their own."""
        container = self.staged_form.parentWidget()
        while widget.parentWidget() is not None and widget.parentWidget() is not container:
            widget = widget.parentWidget()
        return widget

    def toggle_stages(self):
        """Turning staging on starts at one light pass, and makes room for a
        full pass after it; turning it off clears the light passes."""
        if self.loading:
            return
        on = self.stage_toggle.isChecked()
        with self.editing():
            if on:
                self.blade["light_passes"].setValue(max(1, self.blade["light_passes"].value()))
                if self.blade["passes"].value() <= self.blade["light_passes"].value():
                    self.blade["passes"].setValue(self.blade["light_passes"].value() + 1)
            else:
                self.blade["light_passes"].setValue(0)
        self.update_stages()

    def update_stages(self, rebuild=True):
        """Show the light pass settings only while light passes are on, and
        spell out what the blade will do."""
        if not hasattr(self, "stage_note"):
            return
        count = self.blade["light_passes"].value()
        blocked = self.stage_toggle.blockSignals(True)
        self.stage_toggle.setChecked(count > 0)
        self.stage_toggle.blockSignals(blocked)
        # A material test cuts one pass at a time: pass counts and staging
        # belong to the cut job, not to the test.
        testing = getattr(self, "tuning", False)
        form = self.staged_form
        for widget in (self.blade["passes"], self.stage_toggle):
            form.setRowVisible(self._row_of(widget), not testing)
        # Cut Settings A and B hold force, speed and depth during the test.
        for key in self.GUIDE_SLOT_KEYS:
            form.setRowVisible(self._row_of(self.blade[key]), not testing)
        for key in STAGE_NUMBERS:
            form.setRowVisible(self._row_of(self.blade[key]), count > 0 and not testing)
        form.setRowVisible(self.stage_note, count > 0 and not testing)
        total, force, speed = (self.blade[key].value() for key in ("passes", "force", "speed"))
        light_force = self.blade["light_force"].value()
        light_speed = self.blade["light_speed"].value()
        self.stage_note.setText(actions.stage_note(count, total, force, speed,
                                                   None if light_force < 0 else light_force,
                                                   None if light_speed < 0 else light_speed))
        if rebuild:
            self.rebuild()

    def rebuild(self):
        if self.loading:
            return
        if self.active_job is not None:
            self.refresh_send()
            return
        self.restore_holder()
        self.update_enabled_fields()
        self.update_stages(rebuild=False)
        self.correct_detected_depth()
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
                options = self.job_options()
                if self.choice("layout_preset") in ("", layout_presets.DEFAULT_KEY):
                    with self.editing():
                        self.select("layout_preset", layout_presets.DEFAULT_KEY
                                    if self.bundle is None and layout_presets.uses_default(options) else "")
                self.job = job.build(**options)
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
                ver_text = confirmation.verification_statement(current)
                summary = (f"{ver_text}\n"
                           f"{current.grid.sheet_name} {orientation} · "
                           f"{current.grid.sheet_w_mm:g} × {current.grid.sheet_h_mm:g} mm · "
                           f"{caption}")
                if problems:
                    title = "Layout needs attention"
                    summary += "\n" + problems[0]
                elif not current.registration:
                    title = "Layout checked · Registration off"
                    summary += "\nThe cutter uses sheet coordinates. Use a blank sheet."
                elif current.mask_hints:
                    summary += "\n" + current.cover_note()
                elif current.warnings:
                    summary += "\n" + current.warnings[0]
        marks = self.job.marks if self.job else None
        orientation_changed = self.checked_scm_orientation != layout_presets.is_published(self.artwork)
        if (self.pdf and not self.bundle and self.checked_marks is not None
                and (self.checked_marks != marks or orientation_changed)):
            self.invalidate_measurement(clear_image=orientation_changed)
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
        elif self.pdf is not None and self.job and self.job.registration:
            if self.pdf_check_state != sheet.RegistrationDecision.PASSED:
                return "Sheet"
        return "Send"

    def follow_review(self):
        target = self.review_tab()
        if target == "Design":
            self.open_design()
        else:
            self.tabs.setCurrentIndex(self.tab(target))
            if target == "Sheet":
                action = self.measure_button
                if self.bundle_row_error:
                    action = self.sheet_page_number
                elif self.bundle and self.artwork is None:
                    action = self.bundle_export
                if action.isEnabled():
                    action.setFocus()
            elif self.send_button.isEnabled():
                self.send_button.setFocus()

    def send_refusal(self, plan, connection, *, scan_only=False):
        if self.busy:
            return "The cutter connection is busy."
        if self.pending_design():
            return "The design has edits Send has not used. Open Design, then switch to Send."
        if self.bundle and self.artwork is None:
            return ("Choose another sheet or reopen the Print & Cut file in Sheet." if self.bundle_row_error
                    else "Save the legacy cut file from Sheet.")
        if self.bundle and not self.bundle_checked:
            if self.pdf_check_state == sheet.RegistrationDecision.FAILED:
                return "Registration check failed. Open Sheet to see the missing marks."
            if self.pdf_check_state == sheet.RegistrationDecision.ERROR:
                return "Could not measure registration marks on the print file. Open Sheet to review."
            if self.pdf_check_state == sheet.RegistrationDecision.CHECKING:
                return "Registration check pending. Open Sheet to review."
            return "Check the matching PDF in Sheet."
        if plan and plan.registration_steps() and self.pdf is not None:
            allowed, refusal = sheet.RegistrationDecision.evaluate(
                self.pdf_check_state, has_pdf=True, registration_on=True)
            if not allowed:
                return refusal
        if plan is None or self.job is not plan or plan.blocked:
            return "Review the current job and resolve its checks."
        if connection != self.connection():
            return "The cutter connection changed. Review the job again."
        refusal = cutters.usb_refusal(connection, plan.device, self.cutter_model.currentData())
        if refusal:
            return refusal
        if not connection[1]:
            return "Select a cutter."
        if refusal := cutters.wireless_refusal(connection, self.verified_wireless):
            return refusal
        if scan_only and not plan.registration_steps():
            return "Enable registration to test the scan."
        if not scan_only and plan.settings.passes <= 0:
            return "Set at least one pass to send cut paths."
        return ""

    def shows_cut_groups(self):
        """Cut groups exist only for a grouped send; with nothing to list the
        box would be an empty frame."""
        return not self.tuning and self.artwork is not None and self.choice("send_mode") != "simple"

    def refresh_send(self):
        if self.loading or "Send" not in self.pages:
            return
        target = self.review_tab()
        index = self.tabs.currentIndex()
        self.cutter_box.setVisible(not self.tuning)
        self.group_box.setVisible(self.shows_cut_groups())
        self.registration_box.setVisible(not self.tuning)
        self.send_advanced.disclosure.setVisible(not self.tuning)
        self.send_advanced.setVisible(not self.tuning and self.send_advanced.disclosure.isChecked())
        self.settings_box.setVisible(True)
        self.preset_actions.setVisible(not self.tuning)
        self.guide_page.setVisible(self.tuning)
        self.update_stages(rebuild=False)
        self.review_button.setText(f"Review {'Cut Job' if target == 'Send' else target}")
        editable = not self.settings_locked()
        for control in (self.open_artwork_action, self.open_artwork_button):
            control.setEnabled(self.can_import_template())
        for control in (self.open_print_action, self.open_print_button):
            control.setEnabled(editable)
        self.close_print_action.setEnabled(editable and self.pdf is not None)
        self.update_sheet_controls()
        self.paired_cut_action.setEnabled(self.bundle is not None)
        can_reset = editable and (self.artwork is not None or self.bundle is not None)
        self.reset_layout_action.setEnabled(can_reset)
        self.use_generated.setEnabled(can_reset)
        self.back_to_send.setText("Exit Material Test")
        self.back_to_send.setVisible(self.tuning)
        self.guide_send.setVisible(self.tuning and index == self.tab("Send"))
        self.review_button.setVisible(not self.tuning and (target == "Design" or index != self.tab(target)))
        control = self.cut_control
        at_send = not self.tuning and target == "Send" and control is None
        self.send_button.setVisible(at_send and index == self.tab("Send"))
        self.scan_button.setVisible(not self.tuning and bool(self.job and self.job.registration_steps()))
        self.corner_button.setVisible(not self.tuning)
        for button in (self.pause_cut, self.cancel_cut):
            button.setVisible(control is not None)
        if control is not None:
            state = control.state
            if state == "pausing":
                pause_text = "Pausing…"
                pause_enabled = True
            elif state == "paused":
                pause_text = "Resume Cut"
                pause_enabled = True
            else:
                pause_text = "Pause Cut"
                pause_enabled = state == "running"

            cancel_text = "Stopping…" if state == "cancelling" else "Cancel Cut"
            cancel_enabled = state in ("running", "pausing", "paused")

            self.pause_cut.setText(pause_text)
            self.pause_cut.setEnabled(pause_enabled)
            self.cancel_cut.setText(cancel_text)
            self.cancel_cut.setEnabled(cancel_enabled)
        reason = self.send_refusal(self.job, self.connection())
        self.send_button.setEnabled(not reason)
        self.corner_button.setEnabled(not reason)
        self.scan_button.setEnabled(not self.send_refusal(self.job, self.connection(), scan_only=True))
        connection = self.connection()
        self.test_button.setEnabled(not self.busy and (
            not cutters.wireless_refusal(connection, self.verified_wireless)))
        can_check = not self.busy and bool(self.connection()[1])
        self.refresh_cutter.setEnabled(can_check)
        if self.bundle and self.report is not None and not self.bundle_checked:
            self.summary_title.setText("PDF registration needs attention")
        self.update_guide()
        self.update_tuning_preview()
        if control is not None:
            self.footer_status.setText(control.status_text)
        elif not self.tuning:
            display_job = self.active_job or self.job
            is_send_page = self.tabs.currentIndex() == self.tab("Send")
            if reason == "Connect a cutter." and not is_send_page:
                self.footer_status.setProperty("role", "body")
                self.footer_status.style().unpolish(self.footer_status)
                self.footer_status.style().polish(self.footer_status)
                self.footer_status.setText("No cutter connected.")
            else:
                self.footer_status.setProperty("role", "warning" if reason else "body")
                self.footer_status.style().unpolish(self.footer_status)
                self.footer_status.style().polish(self.footer_status)
                self.footer_status.setText((f"⚠ {reason}" if reason else "") or (
                    f"{display_job.device.name} · {len(display_job.paths)} paths · "
                    + ("Registration on" if display_job.registration else "Registration off")))

    def confirm(self, title, prompt, *, confirm_label=None, destructive=False, details=None):
        if confirm_label:
            box = QMessageBox(self)
            box.setWindowTitle(title)
            box.setText(prompt)
            if details:
                box.setDetailedText(details)
            btn = box.addButton(
                confirm_label,
                QMessageBox.ButtonRole.DestructiveRole if destructive else QMessageBox.ButtonRole.AcceptRole,
            )
            cancel = box.addButton(QMessageBox.StandardButton.Cancel)
            box.setDefaultButton(cancel)
            box.setIcon(QMessageBox.Icon.Warning if destructive else QMessageBox.Icon.Information)
            box.exec()
            return box.clickedButton() == btn
        return QMessageBox.question(self, title, prompt, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                    QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes

    def select_open(self, title, filters, *, options=QFileDialog.Option(0)):
        value, _ = QFileDialog.getOpenFileName(self, title, "", filters, options=options)
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
        self.pdf_check_state = sheet.RegistrationDecision.NONE
        self.checked_marks = None
        self.preview.report = None
        self.clear_scan_result()
        if clear_image:
            self.preview.show_pdf()
            self.preview_mode.hide()

    BUNDLE_LOCKED = ("layout_preset", "sheet", "portrait", "engine", "card", "mode", "radius_mm",
                     "pattern", "mark_inset_mm", "mark_length_mm", "mark_thickness_mm", "bleed_mm",
                     "registration")

    def lock_bundle_controls(self, locked):
        self.bundle_locked = bool(locked)
        self.update_enabled_fields()

    def clear_bundle(self):
        if self.bundle:
            self.bundle = None
            self.bundle_export.hide()
            self.bundle_print.hide()
            self.lock_bundle_controls(False)
            self.clear_printed_sheet()

    def update_sheet_controls(self):
        loaded = self.pdf is not None
        editable = not self.settings_locked()
        self.close_print.setEnabled(editable and loaded)
        self.close_print.setToolTip("Close the open PDF." if loaded else "No print file open.")
        checking = self.pdf_check_state == sheet.RegistrationDecision.CHECKING
        can_measure = editable and loaded and self.bundle_row_error is None
        self.measure_button.setEnabled(can_measure and not checking)
        self.measure_button.setText("Checking PDF Marks…" if checking else "Check PDF Marks")
        self.measure_button.setToolTip(
            "Measure printed registration marks on the open PDF."
            if can_measure
            else (self.bundle_row_error or "Open a PDF first.")
        )
        self.sheet_dpi.setEnabled(editable and loaded)
        self.sheet_page_label.setText("Matched sheet" if self.bundle else "PDF page")
        self.sheet_page_number.setEnabled(editable and loaded and (not self.bundle or len(self.bundle.rows) > 1))

    def clear_printed_sheet(self):
        self.pdf = self.bundle_row_error = None
        self.invalidate_measurement(clear_image=True)
        self.sheet_file.setText("Open the PDF you printed.")
        self.sheet_result.setText("Not measured")
        with self.editing():
            self.sheet_page_number.setRange(1, 999)
            self.sheet_page_number.setValue(1)
        self.update_sheet_controls()

    def close_printed_sheet(self):
        if self.settings_locked():
            return
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
        if self.settings_locked():
            return
        path = self.select_open("Open Print File", "Print files (*.pdf *.PDF *.zip *.ZIP)",
                                options=QFileDialog.Option.HideNameFilterDetails)
        if not path or self.settings_locked():
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
        self.project_shapes = None
        self.forget_sent_design()
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
        if self.loading or self.settings_locked():
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
        if self.loading or self.settings_locked() or path is None or self.bundle_row_error:
            return
        if not matched and not current:
            self.message("Check the layout before measuring the sheet.")
            return
        from . import sheet

        self.invalidate_measurement(clear_image=True)
        self.pdf_check_state = sheet.RegistrationDecision.CHECKING
        token = self.sheet_token = object()
        self.sheet_result.setText("Checking PDF marks…")
        self.refresh_send()
        row = self.current_bundle_row()
        marks = matched.marks() if matched else current.marks
        self.checked_marks = marks
        dpi, page = self.sheet_dpi.currentData(), self.sheet_page_number.value()
        orient_scm = layout_presets.is_published(self.artwork)
        self.checked_scm_orientation = orient_scm

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
                self.pdf_check_state = sheet.RegistrationDecision.ERROR
                self.sheet_result.setText(f"Could not check PDF marks. {error}")
            else:
                report, issues, png = value
                self.report = report
                self.bundle_checked = bool(matched) and not issues
                self.pdf_check_state = (sheet.RegistrationDecision.FAILED if issues
                                        else sheet.RegistrationDecision.PASSED)
                details = (["PDF marks need attention", report.diagnosis()] if issues
                           else ["PDF marks match the template"])
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
        self._refresh_cutter_list()
        self._update_scan_ui()
        if self.search_pending and not self.wireless_scanning and not self.busy:
            self.search_pending = False
            self.refresh_wireless(search=True)

    def _refresh_cutter_list(self):
        choices = {**cutters.usb_choices(), **self.wireless_choices}
        if cutters.wireless_endpoint(self.preferred_endpoint) and self.preferred_endpoint not in choices:
            self.preferred_endpoint = self.verified_wireless = None
        items = {"": "Select a cutter", **choices}
        if [(self.detected.itemData(i), self.detected.itemText(i)) for i in range(self.detected.count())] != list(items.items()):
            preferred = self.preferred_endpoint
            if cutters.wireless_endpoint(preferred) and self.verified_wireless != (*preferred, None):
                selected = ""
            else:
                selected = cutters.detected_choice(choices, preferred) or ""
            same_cutter = selected and (selected, choices[selected]) == (
                self.detected.currentData(), self.detected.currentText())
            set_choices(self.detected, items, selected)
            if same_cutter:
                return
            self.device_selected()
            if not selected:   # otherwise device_selected is reading its status
                self.set_cutter_status("Select a cutter." if choices else cutters.NO_CUTTER_TEXT)


    def refresh_wireless(self, search=False):
        """Refresh the wireless list; a search also listens for unpaired
        Classic cutters (13 s), and is only run when asked for."""
        if search and (self.wireless_scanning or self.busy):
            self.search_pending = True   # run it once the window is free
            self._update_scan_ui()
            return
        if self.busy or self.wireless_scanning:
            return
        if self.link.currentData() != "auto":
            if not search and self.connection() == self.verified_wireless:
                self.check_device()
            return
        self.wireless_scanning = True
        self._update_scan_ui()
        self.tasks.run(lambda: cutters.wireless_choices(search=search), self._wireless_done)

    def _wireless_done(self, value, error):
        self.wireless_scanning = False
        selected = self.detected.currentData()
        last_label = self.wireless_choices.get(selected)
        self.wireless_choices = dict(value) if not error else {}
        if last_label and cutters.wireless_endpoint(selected):
            self.wireless_choices.setdefault(selected, last_label)
        self.refresh_usb()
        self._update_scan_ui()
        if cutters.wireless_endpoint(self.detected.currentData()):
            self.check_device()

    def on_scan_cutters(self):
        if self.link.currentData() == "auto":
            self.refresh_usb()
            self.refresh_wireless(search=True)
        else:
            self.find_cutters()

    def _update_scan_ui(self):
        """With no cutter available, offer a scan under the status and say
        what is happening; once there is one, the actions below suffice."""
        none = self.detected.count() <= 1 and (self.link.currentData() == "auto"
                                               or not self.identifier.text().strip())
        scanning = (self.wireless_scanning or self.search_pending if self.link.currentData() == "auto"
                    else self.manual_scanning)
        self.cutter_form.setRowVisible(self.scan_cutters, none)
        self.scan_cutters.setEnabled(not scanning and not self.busy)
        self.discover.setVisible(not none)
        if none:
            self.connection_status.setText(cutters.SCANNING_TEXT if scanning else cutters.NO_CUTTER_TEXT)

    def change_connection(self):
        mode = self.link.currentData()
        self.preferred_endpoint = None
        self.verified_wireless = None
        self.advanced_connection_form.setRowVisible(self.identifier, mode != "auto")
        self.advanced_connection_form.setRowVisible(self.channel, mode == "bluetooth")
        self.identifier.clear()
        set_choices(self.detected, {"": "Select a cutter"}, "")
        self.change_target()
        if mode == "auto" and not self.loading:
            self.refresh_usb()
        self._update_scan_ui()

    def change_target(self):
        if self.tools_connection is not None and self.tools_connection != self.connection():
            self.detected_tools = ()
            self.tools_connection = self.corrected_tools = None
        changed = self.update_holder_labels()
        self.tool_picker.setEnabled(not self.busy and self.active_job is None and bool(self.connection()[1]))
        self.set_cutter_status("Cutter status not checked.")
        self.clear_scan_result()
        if changed:
            self.rebuild()
        else:
            self.refresh_send()

    def set_cutter_status(self, text):
        self.connection_status.setText(text)

    def update_holder_labels(self):
        device = graphtec.device_by_name(self.choice("cutter"))
        changed = self.tool_selection.bind(self.connection(), device)
        holder = self.blade["holder"].value() if device.two_tools else 1
        choices = tooling.tool_choices(device)
        selected = self.tool_selection.selected(holder)
        if list(choices) != [self.tool_picker.itemData(i) for i in range(self.tool_picker.count())]:
            set_choices(self.tool_picker, choices, selected)
        else:
            blocked = self.tool_picker.blockSignals(True)
            self.tool_picker.setCurrentIndex(self.tool_picker.findData(selected))
            self.tool_picker.blockSignals(blocked)
        labels = tooling.holder_labels(self.detected_tools, device, self.tool_selection)
        picker = self.blade["holder"].holders
        for slot in (1, 2):
            label = labels[slot - 1] if slot <= len(labels) else f"Tool Holder {slot}"
            tab = picker.button(slot)
            tab.setText(label.replace(" · ", "\n", 1))
            tab.setAccessibleName(label)
            tab.setToolTip(f"{label} (manually selected)" if self.tool_selection.selected(slot) else label)
        return changed

    def change_tool_selection(self):
        if self.loading or self.active_job is not None or self.busy or not self.connection()[1]:
            return
        device = graphtec.device_by_name(self.choice("cutter"))
        holder = self.blade["holder"].value() if device.two_tools else 1
        choice = self.tool_picker.currentData()
        self.tool_selection.select(holder, choice)
        self.rebuild()

    def apply_tool_detection(self, codes):
        """Take a holder reply, label the tabs, and send depth back to manual once."""
        self.detected_tools = tuple(codes)
        self.tools_connection = self.connection()
        self.update_holder_labels()
        self.correct_detected_depth()

    def correct_detected_depth(self):
        if self.active_job is not None or self.busy or self.tool_selection.selected(1):
            return
        device = graphtec.device_by_name(self.choice("cutter"))
        if device.two_tools and self.blade["holder"].value() != 1:
            return
        if not self.detected_tools or self.corrected_tools == self.detected_tools:
            return
        self.corrected_tools = self.detected_tools
        depth, reason = tooling.depth_correction(self.detected_tools, self.blade["depth"].value())
        if depth is None:
            return
        self.blade["depth"].setValue(depth)
        self.set_cutter_status(reason)
        self.message(reason)

    def detect_tools(self):
        """Read the installed tools once for this connection, in the background."""
        connection = self.connection()
        device = graphtec.device_by_name(self.choice("cutter"))
        if (self.busy or self.active_job is not None or not connection[1]
                or not device.detects_tools or self.tools_connection == connection):
            return

        self.checking_status = True
        self.update_enabled_fields()
        self.refresh_send()

        def work():
            with self.open_link(connection) as cutter:
                return cutter.tool_codes()

        def done(codes, error):
            self.checking_status = False
            self.update_enabled_fields()
            if connection == self.connection():
                if error:
                    self.detected_tools = ()
                    self.tools_connection = connection
                    self.update_holder_labels()
                else:
                    self.apply_tool_detection(codes)
            else:
                self.detect_tools()
            self.refresh_send()

        self.tasks.run(work, done)

    def select_detected(self):
        mode = self.link.currentData()
        item = self.detected.currentData() or None
        if mode == "auto":
            self.preferred_endpoint = item
            endpoint = item if cutters.wireless_endpoint(item) else None
        else:
            endpoint = (mode, item) if item else None
        if endpoint:
            blocked = self.detected.blockSignals(True)
            self.detected.setCurrentIndex(0)
            self.detected.blockSignals(blocked)
            if mode != "auto":
                self.identifier.clear()
            self.change_target()
            pending = "Pairing with cutter…" if cutters.unpaired(endpoint) else "Connecting…"
            self.check_device(pending=pending, endpoint=endpoint)
            return
        self.device_selected()

    def device_selected(self):
        identifier = self.detected.currentData()
        if self.link.currentData() == "auto":
            model = cutters.auto_model(identifier, self.detected.currentText()) if identifier else None
            if model and self.cutter_model.currentData() == cutters.AUTO:
                self.select("cutter", model.name)
            self.change_target()
            if identifier:
                # As in Studio, choosing a cutter reads its status, so the window
                # never calls a paired cutter that is switched off "connected";
                # one a search found is paired first.
                name = self.detected.currentText().split(" · ")[0]
                self.check_device(pending=f"Pairing with {name}…" if cutters.unpaired(identifier)
                                  else "Reading cutter status…")
            return
        self.identifier.setText(identifier or "")
        device = bluetooth.advertised_model(self.detected.currentText().split(" · ")[0])
        if device and self.cutter_model.currentData() == cutters.AUTO:
            self.select("cutter", device.name)
        self.detect_tools()

    def find_cutters(self):
        mode = self.link.currentData()
        if mode == "auto":
            self.refresh_usb()
            self.refresh_wireless(search=True)
            return
        self.discover.setEnabled(False)
        self.manual_scanning = True
        self._update_scan_ui()
        self.connection_status.setText("Searching for Bluetooth cutters…")

        def done(devices, error):
            self.discover.setEnabled(True)
            self.manual_scanning = False
            if mode != self.link.currentData():
                self._update_scan_ui()
                return
            if error:
                self.connection_status.setText(str(error))
                return
            set_choices(self.detected, {"": "Select a cutter", **{
                identifier: f"{name or 'Unnamed'} · {identifier}" for identifier, name in devices}}, "")
            self.connection_status.setText("Select a cutter from the list." if devices else "No cutters found.")
            self._update_scan_ui()

        self.tasks.run(lambda: cutters.discover_desktop(mode), done)

    @staticmethod
    def _status_failure(error):
        if isinstance(error, transport.NotFound):
            return cutters.NOT_DETECTED_TEXT
        return f"Could not read cutter status. {error}"

    def check_device(self, pending="Reading cutter status…", *, endpoint=None):
        original = self.connection()
        automatic = self.link.currentData() == "auto"
        connection = (*endpoint, None if automatic else original[2]) if endpoint else original
        if self.busy or self.active_job is not None:
            return
        if connection[0] != "auto" and not connection[1]:
            self.message("Select a Bluetooth cutter.")
            return
        self.checking_status = True
        self.update_enabled_fields()
        self.set_cutter_status(pending)
        self.refresh_send()

        def work():
            with self.open_link(connection) as cutter:
                version = f" · {cutter.firmware_version()}" if connection[0] != "auto" else ""
                status, codes = cutter.status_and_tools()
                return cutter.device, f"{cutter.device.name} · {status}{version}", codes

        def done(value, error):
            self.checking_status = False
            self.update_enabled_fields()
            if original == self.connection():
                if connection[0] in bluetooth.BLUETOOTH_MODES:
                    self.verified_wireless = None if error else connection
                    wireless = endpoint or self.detected.currentData()
                    if error and automatic and cutters.wireless_endpoint(wireless):
                        self.wireless_choices.pop(wireless, None)
                        self.preferred_endpoint = None
                        self._refresh_cutter_list()
                        self._update_scan_ui()
                    elif error and not automatic:
                        index = self.detected.findData(connection[1])
                        if index > 0:
                            blocked = self.detected.blockSignals(True)
                            self.detected.setCurrentIndex(0)
                            self.detected.removeItem(index)
                            self.detected.blockSignals(blocked)
                            if self.identifier.text().strip() == connection[1]:
                                self.identifier.clear()
                    elif endpoint:
                        blocked = self.detected.blockSignals(True)
                        self.detected.setCurrentIndex(self.detected.findData(endpoint if automatic else endpoint[1]))
                        self.detected.blockSignals(blocked)
                        if not automatic:
                            self.identifier.setText(endpoint[1])
                        self.change_target()
                self.set_cutter_status(self._status_failure(error) if error else value[1])
                if not error and self.cutter_model.currentData() == cutters.AUTO:
                    self.select("cutter", value[0].name)
                if not error and value[2] is not None:
                    self.apply_tool_detection(value[2])
            self.refresh_send()

        self.tasks.run(work, done)

    def confirm_send(self, *, scan_only=False):
        plan, connection = self.job, self.connection()
        reason = self.send_refusal(plan, connection, scan_only=scan_only)
        if reason:
            self.message(reason)
            return
        conf = confirmation.build_send_confirmation(
            plan, connection, scan_only=scan_only, bundle=self.bundle, artwork=self.artwork
        )
        if self.confirm(
            conf.title,
            conf.summary,
            confirm_label=conf.confirm_label,
            destructive=conf.destructive,
            details=conf.details,
        ):
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
        if self.confirm("Cut a corner test?", sample.describe(), confirm_label="Cut Corner Test", destructive=True):
            self.start_send(plan, connection, corner=sample)

    def start_send(self, plan, connection, *, scan_only=False, corner=None):
        reason = self.send_refusal(plan, connection, scan_only=scan_only)
        if reason:
            self.message(reason)
            return
        checked = corner.job if corner else plan
        self.active_job = checked
        self.update_enabled_fields()
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
                        controllable=not scan_only and corner is None,
                        on_success=(lambda: self.show_scan_result("Registration passed. No cut paths were sent."))
                        if scan_only else None)

    def release_job(self):
        """Return the window to an editable state after a send ends, however it ended."""
        self.cut_control = None
        self.active_job = None
        self.sending = False
        self.update_enabled_fields()
        self.refresh_send()

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
                   on_success=None, test=False, wait_done=False, controllable=False):
        refusal = cutters.usb_refusal(connection, device, self.cutter_model.currentData())
        if refusal:
            self.release_job()
            self.message(refusal)
            return
        self.guide.moved()
        control = self.cut_control = (transport.CutControl(
            lambda: self.tasks.result.emit(lambda *_: self.refresh_send(), None, None))
            if controllable else None)
        self.sending = True
        self.update_enabled_fields()
        self.clear_scan_result()
        self.refresh_send()

        def work():
            with self.open_link(connection, device) as cutter:
                if cutter.can_read:
                    state = cutter.status()
                    if state in ("moving", "unloaded") or (state != "ready" and (test or connection[0] != "auto")):
                        raise transport.TransportError(f"The cutter is not ready ({state}).")
                if control is not None:
                    return cutter.run(steps, wait_done=True, control=control)
                if wait_done and cutter.can_read:
                    return cutter.run(steps, wait_done=True)
                return cutter.run(steps)

        def done(sent, error):
            self.release_job()
            if isinstance(error, transport.RegistrationFailed) and on_registration_failure:
                on_registration_failure(error)
            elif error is None and on_success:
                on_success()
            self.message(str(error) if error else f"{success} {sent:,} bytes.")

        self.tasks.run(work, done)

    def update_guide(self):
        state = self.guide
        if self.tuning:
            self.sync_guide_slot()
        for field in (self.guide_apply, self.guide_next, self.guide_reset, self.guide_slot_choice,
                      self.guide_batch_passes):
            field.setEnabled(not self.settings_locked())
        max_batch = max(1, state.remaining_passes)
        if state.batch_passes > max_batch:
            state.batch_passes = max_batch
        blocked = self.guide_batch_passes.blockSignals(True)
        self.guide_batch_passes.setMaximum(max_batch)
        self.guide_batch_passes.setValue(state.batch_passes)
        self.guide_batch_passes.blockSignals(blocked)
        self.guide_next.setEnabled(not self.settings_locked() and (
            state.placement is None or any(number != state.spot for number in state.fresh_spots())))
        used = state.current_spot
        sample_shape = state.trial.sheet.shape if state.trial else state.shape
        sample_size = used.rect.w if used else state.size_mm
        for field, choice in ((self.guide_shape, sample_shape), (self.guide_size, sample_size)):
            blocked = field.blockSignals(True)
            field.setCurrentIndex(field.findData(choice))
            field.blockSignals(blocked)
            field.setEnabled(not self.settings_locked())
        connected = starting_guide.has_cutter_endpoint(self.connection())
        self.guide_send.setEnabled(not self.busy and connected and state.can_cut
                                   and not cutters.wireless_refusal(self.connection(), self.verified_wireless))
        self.guide_use.setEnabled(not self.settings_locked() and state.tested_settings is not None)
        self.guide_use.setToolTip(state.mixed_reason or "Copy what this spot was cut with into Cut Settings, "
                                                       "and optionally save it as a material.")
        show_recovery = state.can_retry_spot or state.can_confirm_pass
        self.guide_form.setRowVisible(self.guide_recovery, show_recovery)
        self.guide_retry.setVisible(state.can_retry_spot)
        self.guide_retry.setEnabled(not self.settings_locked() and state.can_retry_spot)
        self.guide_confirm_pass.setVisible(state.can_confirm_pass)
        self.guide_confirm_pass.setEnabled(not self.settings_locked() and state.can_confirm_pass)
        if state.trial and state.trial.passes > 1:
            self.guide_confirm_pass.setText(f"Batch Finished — Count {state.trial.passes}")
        else:
            self.guide_confirm_pass.setText("Pass Finished — Count It")
        self.guide_registration.setEnabled(not self.settings_locked() and not (state.spots or state.sheet_exported))
        self.guide_marked_sheet.setEnabled(not self.settings_locked() and state.registered)
        self.guide_send.setText(state.pass_label(self.job_options()))
        if hasattr(self, "guide_spot_selector"):
            blocked = self.guide_spot_selector.blockSignals(True)
            self.guide_spot_selector.setMaximum(max(state.max_spots, state.spot))
            self.guide_spot_selector.setValue(state.spot)
            self.guide_spot_selector.blockSignals(blocked)
        differ = state.completed > 0 and state.settings_differ(self.job_options())
        self.material_guide.setText("Resume Material Test" if state.has_tests else "Dial In a Material…")
        # Inside the test, Cut Settings needs no way back into it.
        self.material_guide.setVisible(not self.tuning)
        if self.tuning:
            status = f"Spot {state.spot} · Cut Settings {self.guide_slot} · {state.sheet_status}"
            if self.settings_locked():
                status = "Waiting for the cutter to finish…"
            elif not connected:
                status = "Exit Material Test and select a cutter."
            self.footer_status.setText(status)
        status = state.summary(differ)
        if self.settings_locked():
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
        self.guide_outcome.setEnabled(not self.settings_locked())
        advice = state.advice()
        self.guide_advice.setText(advice)
        self.guide_advice.setVisible(bool(advice))

    def on_guide_spot_selected(self):
        if self.settings_locked():
            return
        value = int(self.guide_spot_selector.value())
        if value != self.guide.spot:
            try:
                self.guide.select_spot(value)
            except ValueError as error:
                self.update_guide()
                self.message(str(error))
                return
            self.persist_guide()
            self.update_guide()
            self.update_tuning_preview()

    def on_preview_spot_clicked(self, x, y):
        if not self.tuning or self.settings_locked():
            return False
        spot = self.guide.spot_for_point(x, y)
        if spot is not None:
            self.guide.select_spot(spot)
            self.persist_guide()
            self.update_guide()
            self.update_tuning_preview()
            return True
        return False

    def inspect_test_cut(self):
        self.guide.inspect(self.guide_outcome.currentData())
        self.update_guide()

    def change_guide_sample(self):
        if self.settings_locked():
            return
        try:
            self.guide.configure_sample(self.guide_shape.currentData(), self.guide_size.currentData())
        except ValueError as error:
            self.message(str(error))
        else:
            self.persist_guide()
        self.update_guide()
        self.update_tuning_preview()

    def update_tuning_preview(self):
        trial = None
        if self.tuning:
            trial = self.guide.trial
            if trial is None:
                try:
                    trial = self.guide.prepare(self.connection(), self.job_options(), self.job)
                except (ValueError, KeyError):
                    pass
        placement = self.guide.placement if self.tuning else None
        active_rect = self.guide.spot_rect(self.guide.spot) if self.tuning else None
        self.preview.show_trial(trial, self.guide.spots if self.tuning else (), placement, active_rect,
                                grid=self.guide.fresh_spots() if self.tuning else {})
        self.preview_title.setText("Material test sheet" if self.preview.test_placement else "Sheet preview")
        self.preview_mode.setVisible(self.preview.test_placement is None and not self.preview.pdf.isNull())
        self.summary_bar.setVisible(not self.tuning)
        self.bundle_notice_bar.setVisible(self.bundle is not None and not self.tuning)
        self.details.setVisible(not self.tuning and self.details_toggle.isChecked())

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
        self.message("Starting settings applied." + suffix)

    def use_guide_settings(self):
        settings = self.guide.tested_settings
        if self.settings_locked() or settings is None:
            return
        device = graphtec.device_by_name(self.choice("cutter"))
        holder = self.blade["holder"].value() if device.two_tools else 1
        if device != self.guide.trial.sheet.device or settings.holder != holder:
            self.message("Select the tested cutter and tool holder first.")
            return
        self.set_blade_settings(settings)
        staged = f", the first {settings.light_passes} lighter" if settings.light_passes else ""
        self.message(f"Applied the tested settings with {settings.passes} "
                     f"{'pass' if settings.passes == 1 else 'passes'}{staged}.")
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
        if self.settings_locked():
            return
        try:
            self.guide.next_spot()
        except ValueError as error:
            self.message(str(error))
            return
        self.persist_guide()
        self.update_guide()
        self.update_tuning_preview()

    def retry_guide_spot(self):
        if self.settings_locked():
            return
        try:
            self.guide.retry_spot()
        except ValueError as error:
            self.message(str(error))
            return
        self.persist_guide()
        self.update_guide()
        self.update_tuning_preview()
        self.message(f"Spot {self.guide.spot} unlocked to retry.")

    def on_guide_batch_changed(self):
        if self.settings_locked():
            return
        try:
            self.guide.set_batch_passes(int(self.guide_batch_passes.value()))
        except ValueError as error:
            self.message(str(error))
        self.update_guide()

    def accept_unconfirmed_guide_pass(self):
        if self.settings_locked():
            return
        try:
            self.guide.confirm_unconfirmed_pass()
        except ValueError as error:
            self.message(str(error))
            return
        self.persist_guide()
        self.update_guide()
        self.update_tuning_preview()
        count = "pass" if self.guide.completed == 1 else "passes"
        self.message(f"Spot {self.guide.spot} confirmed at {self.guide.completed} {count}.")

    def reset_guide(self):
        if not self.settings_locked() and self.confirm("Start a New Test Sheet?", "Load a fresh test sheet to start at the first spot."):
            self.guide.reset()
            self.guide.placement = self.guide.make_placement(self.job, self.job_options())
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
        if self.guide.placement is not None and not self.guide.spots:
            from dataclasses import replace
            self.guide.placement = replace(self.guide.placement, registered=self.guide.registered)
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
        if self.busy or not self.guide.can_cut or cutters.wireless_refusal(
                self.connection(), self.verified_wireless):
            return
        try:
            trial = self.guide.prepare(self.connection(), self.job_options(), self.job)
        except (ValueError, KeyError) as error:
            self.message(str(error))
            return
        if self.guide.is_repeat(trial) or self.confirm(self.guide.confirm_title(trial), self.guide.prompt(trial)):
            self.start_guide_pass(trial)

    def start_guide_pass(self, trial):
        if self.busy or not self.guide.can_cut or cutters.wireless_refusal(
                self.connection(), self.verified_wireless):
            return
        try:
            current = self.guide.prepare(self.connection(), self.job_options(), self.job)
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
        self.sending = True
        self.update_enabled_fields()
        self.refresh_send()

        def work():
            with self.open_link(trial.connection, trial.sheet.device) as cutter:
                return starting_guide.run_pass(cutter, trial)

        name = self.guide_pass_name

        def done(_sent, error):
            self.guide.record(trial, error, name)
            self.sending = False
            self.update_enabled_fields()
            self.refresh_send()
            if self.persist_guide():
                done_msg = "Pass complete." if trial.passes == 1 else f"{trial.passes} passes complete."
                self.message(str(error) if error else done_msg)

        self.tasks.run(work, done)

    def confirm_test_cut(self):
        connection, current = self.connection(), self.job
        if self.busy or cutters.wireless_refusal(connection, self.verified_wireless):
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

    def open_design(self, *, blank=False, replace_design=False):
        if self.bundle:
            dialog = QMessageBox(self)
            dialog.setWindowTitle("Matched Cut Paths Locked")
            dialog.setText("Matched cut paths are locked to the printed sheet.")
            dialog.setInformativeText("To edit cut paths or clear the match, open Sheet.")
            open_sheet = dialog.addButton("Open Sheet", QMessageBox.ButtonRole.ActionRole)
            dialog.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
            dialog.exec()
            if dialog.clickedButton() == open_sheet:
                self.tabs.setCurrentIndex(self.tab("Sheet"))
            return
        if self.settings_locked():
            self.message("Wait for the cutter operation to finish before opening Design.")
            return
        if self.design is None or blank or replace_design:
            if self.job is None:
                self.message("Check the layout before opening Design.")
                return
            from .qt_design import Design
            plan = self.job
            try:
                shapes = () if blank else (self.project_shapes or (
                    editor_model.editable_import(self.artwork)[0] if self.artwork else
                    editor_model.shapes_from_paths(plan.paths)))
            except ValueError as error:
                self.message(str(error))
                return
            registration = editor_model.Registration.from_marks(plan.marks) if plan.registration else None
            project = editor_model.Project(plan.grid.sheet_w_mm, plan.grid.sheet_h_mm, shapes, registration, plan.mat)
            if self.design is None:
                self.design = Design(project, plan.device, self.send_from_design, self.review_design)
                if self.can_update_windows:
                    help_menu = self.design.menu_bar.addMenu("&Help")
                    help_menu.addAction("Check for Updates…", self.check_for_updates)
                self.workspaces.addWidget(self.design)
            elif self.design.may_replace():
                self.design.replace_document(project)
            else:
                return
            self.design.device = plan.device
            self.design.second_holder_action.setEnabled(plan.device.two_tools)
            self.forget_sent_design()
        self.workspaces.setCurrentWidget(self.design)

    def send_from_design(self):
        """Hand off the current design or explain why it cannot be sent."""
        self.design.finish_text()
        verdict, detail = editor_model.send_handoff(self.design.project, self.design_origin, self.reviewed_design)
        if verdict == "invalid":
            self.design.message(detail, level="warning")
            return
        if verdict == "apply":
            self.review_design(self.design.project, detail)
            return
        if verdict == "stickers":
            self.message("Sticker sheets are cut from their Print & Cut file. Choose Export Print & Cut, "
                         "then open the file in Sheet.")
        if verdict != "keep":
            self.forget_sent_design()
        self.leave_design()

    def forget_sent_design(self):
        """Send has artwork of its own now: the design as it stands counts as
        seen, so only later edits replace that artwork."""
        self.reviewed_design = None
        self.design_origin = self.design.project if self.design is not None else None

    def leave_design(self):
        self.workspaces.setCurrentIndex(0)
        target = getattr(self, "last_send_tab", "Send")
        idx = self.tab(target) if target in self.pages else self.tab("Send")
        self.tabs.setCurrentIndex(idx)
        self.refresh_send()

    def review_design(self, project, action):
        artwork = project.artwork(action)
        if not artwork.paths or any(path.length_mm() <= 0 for path in artwork.paths):
            self.design.message("Draw or resize the paths before reviewing.", level="warning")
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
        self.tabs.setCurrentIndex(self.tab("Send"))

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.Type.PaletteChange, QEvent.Type.ApplicationFontChange) and hasattr(self, "preview"):
            self.workspaces.setStyleSheet("")
            self.workspaces.setPalette(self.palette())
            self.workspaces.setStyleSheet(workspace_style(self.palette()))
            self.preview.render_sheet()
            if self.design is not None:
                self.design.canvas.redraw()

    def closeEvent(self, event):
        if self.busy:
            self.message("Finish or cancel the cutter operation before closing.")
            event.ignore()
        elif self.design and not self.design.may_replace():
            event.ignore()
        else:
            if self.update_setup is not None:
                from .qt_update import launch

                if self.busy or self.active_job is not None or (self.design and self.design.exporting_stickers):
                    self.message("Wait for the cutter operation or export to finish before updating.")
                    event.ignore()
                    return
                try:
                    launch(self.update_setup)
                except OSError as error:
                    self.message(f"Couldn’t open Setup. {error}")
                    event.ignore()
                    return
            super().closeEvent(event)


def main(argv=None):
    _set_windows_app_id()
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication([sys.argv[0], *(argv or [])])
    app.setApplicationName("Cutline")
    app.setDesktopFileName(APP_ID)
    icon = _app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)
    window = Window()
    window.show()
    return app.exec()
