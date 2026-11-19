"""Exercise the Qt frontend without a display or a hardware cutter."""

import io
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from cut_proxies import cli, corner_test, editor_model, job, materials, presets, transport
from tests.test_vector_editing import compound_project
from tests.test_sticker_print import make_image

try:
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QColor, QImage, QPalette, QWheelEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QFileDialog, QPlainTextEdit
    from cut_proxies import qt_gui
except ImportError:
    qt_gui = None


@unittest.skipIf(qt_gui is None, "Install PySide6 to run Qt frontend tests")
class TestQtFrontend(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.config_path = Path(folder.name)
        patcher = mock.patch.object(qt_gui.config, "CONFIG_DIR", self.config_path)
        patcher.start()
        self.addCleanup(patcher.stop)
        device = qt_gui.graphtec.device_by_name("Silhouette Cameo 4")
        endpoint = mock.patch.object(transport, "usb_endpoints", return_value=[(device, "", (1, 2))])
        endpoint.start()
        self.addCleanup(endpoint.stop)
        self.window = qt_gui.Window()
        self.window.refresh_usb()
        self.addCleanup(self.window.deleteLater)

    def test_multiple_usb_cutters_require_an_endpoint_choice(self):
        window = self.window
        device = qt_gui.graphtec.device_by_name("Silhouette Cameo 4")
        with mock.patch.object(transport, "usb_endpoints",
                               return_value=[(device, "", (1, 2)), (device, "", (1, 3))]):
            window.refresh_usb()
            self.assertIsNone(window.connection()[1])
            self.assertFalse(window.send_button.isEnabled())
            window.detected.setCurrentIndex(window.detected.findData((1, 3)))
            self.assertEqual(window.connection()[1], (1, 3))
            self.assertTrue(window.send_button.isEnabled())

    def test_refresh_keeps_an_explicit_cutter_and_does_not_switch_on_disconnect(self):
        window = self.window
        device = qt_gui.graphtec.device_by_name("Silhouette Cameo 4")
        first, other = (1, 2), (1, 3)
        with mock.patch.object(transport, "usb_endpoints") as detect:
            detect.return_value = [(device, "", first), (device, "", other)]
            window.refresh_usb()
            window.detected.setCurrentIndex(window.detected.findData(first))
            window.set_cutter_status("Ready")
            detect.return_value.reverse()
            window.refresh_usb()
            self.assertEqual(window.connection()[1], first)
            self.assertEqual(window.connection_status.text(), "Ready")
            detect.return_value = [(device, "", other)]
            window.refresh_usb()
            self.assertIsNone(window.connection()[1])
            self.assertFalse(window.send_button.isEnabled())

    def test_default_and_advanced_settings_use_the_shared_verified_plans(self):
        window = self.window
        expected = job.build(radius_mm=3.2)
        self.assertEqual(window.job.stream, expected.stream)
        self.assertEqual(window.job.steps, expected.steps)
        window.blade["force"].setValue(23)
        window.blade["speed"].setValue(17)
        window.blade["sharpen_corners"].setChecked(True)
        window.blade["corner_start_mm"].setValue(0.4)
        window.select("mat", "cameo-12x12")
        window.select("end_position", "start")
        expected = job.build(radius_mm=3.2, force=23, speed=17, mat="cameo-12x12", end_position="start",
                             blade_options={"sharpen_corners": True, "corner_start_mm": 0.4})
        self.assertEqual(window.job.stream, expected.stream)
        self.assertEqual(window.job.blocked, [])

    def test_send_editors_update_the_job_from_the_same_controls(self):
        window = self.window
        window.send_workspace.click()
        self.assertEqual(window.tabs.currentIndex(), window.tab("Send"))
        self.assertEqual(window.choice("preset"), presets.STARTING_NAME)
        window.blade["passes"].setValue(3)
        window.blade["force"].slider.setValue(22)
        self.assertEqual(window.blade["force"].value(), 22)
        self.assertEqual(window.job.settings.force, 22)
        self.assertEqual(window.job.settings.passes, 3)
        window.blade["track_enhancing"].setChecked(False)
        self.assertFalse(window.job.settings.track_enhancing)
        materials = window.tabs.widget(window.tab("Materials"))
        self.assertFalse(materials.isAncestorOf(window.controls["preset"]))
        self.assertFalse(materials.isAncestorOf(window.blade["force"]))

    def test_published_template_is_selected_in_layout_and_can_be_cleared(self):
        window = self.window
        window.select("layout_preset", "scm-a4-standard-borderless")
        self.assertEqual(len(window.job.paths), 9)
        self.assertEqual(window.job.marks.inset_mm, 3.5)
        self.assertFalse(window.controls["card"].isEnabled())
        self.assertIn("SCM A4", window.source_label.text())
        window.use_generated.click()
        self.assertIsNone(window.artwork)
        self.assertEqual(window.choice("layout_preset"), "")
        self.assertTrue(window.controls["card"].isEnabled())

    def test_sticker_workspace_traces_exports_and_loads_paired_geometry(self):
        window = self.window
        window.open_design()
        design = window.design
        design.replace_document(editor_model.Project(215.9, 279.4))
        image = self.config_path / "sticker.png"
        make_image(image)
        with mock.patch.object(QFileDialog, "getOpenFileName", return_value=(str(image), "")):
            design.place_sticker()
        self.assertTrue(design.trace_button.isEnabled())
        self.assertFalse(design.project.artwork().paths)

        def apply_trace(dialog):
            buttons = dialog.findChild(QDialogButtonBox)
            buttons.button(QDialogButtonBox.StandardButton.Ok).click()
            return dialog.result()

        with mock.patch.object(QDialog, "exec", apply_trace):
            design.trace_button.click()
        self.assertEqual(len(design.project.artwork().paths), 3)
        self.assertIn("Export Print", design.review_button.text())
        target = self.config_path / "stickers.zip"
        with mock.patch.object(QFileDialog, "getSaveFileName", return_value=(str(target), "")), \
             mock.patch.object(design.export_tasks, "run", side_effect=lambda work, done: done(work(), None)):
            design.review_button.click()
        design.send_workspace.click()
        with mock.patch.object(window, "select_open", return_value=target), \
             mock.patch.object(window.tasks, "run", side_effect=lambda work, done: done(work(), None)):
            window.open_sheet()
        self.assertEqual(window.tabs.currentIndex(), window.tab("Sheet"))
        self.assertTrue(window.bundle_checked)
        self.assertEqual(len(window.job.paths), 3)
        self.assertFalse(window.job.blocked)

    def test_wheel_moves_the_pane_without_changing_the_focused_setting(self):
        window = self.window
        window.resize(980, 620)
        window.show()
        window.send_workspace.click()
        self.app.processEvents()
        page = window.tabs.currentWidget()
        scroll = page.verticalScrollBar()
        self.assertGreater(scroll.maximum(), 0)
        force = window.blade["force"]
        for widget, value in ((force, force.value), (force.lineEdit(), force.value), (force.slider, force.value),
                              (window.controls["preset"], window.controls["preset"].currentIndex)):
            with self.subTest(widget=type(widget).__name__):
                widget.setFocus()
                scroll.setValue(0)
                before = value()
                center = widget.rect().center()
                wheel = QWheelEvent(QPointF(center), QPointF(widget.mapToGlobal(center)), QPoint(), QPoint(0, -120),
                                    Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                                    Qt.ScrollPhase.NoScrollPhase, False)
                QApplication.sendEvent(widget, wheel)
                self.assertEqual(value(), before)
                self.assertGreater(scroll.value(), 0)

    def test_workspace_uses_readable_text_on_disabled_and_accent_buttons(self):
        import re
        from cut_proxies.qt_controls import _contrast, workspace_style

        for background, text in (("#f5f5f5", "#202020"), ("#222629", "#eeeeee")):
            palette = QPalette()
            palette.setColor(QPalette.ColorRole.Button, QColor(background))
            palette.setColor(QPalette.ColorRole.ButtonText, QColor(text))
            palette.setColor(QPalette.ColorRole.Highlight, QColor("#38afe0"))
            palette.setColor(QPalette.ColorRole.HighlightedText, QColor("white"))
            style = workspace_style(palette)
            disabled = re.search(r'QPushButton:disabled \{ background: (#[0-9a-f]+); color: (#[0-9a-f]+)', style)
            accent = re.search(r'background: palette\(highlight\); color: (#[0-9a-f]+)', style)
            self.assertGreaterEqual(_contrast(*(QColor(color) for color in disabled.groups())), 4.5)
            self.assertGreaterEqual(_contrast(QColor(accent[1]), QColor("#38afe0")), 4.5)

    def test_compound_and_text_reopen_and_review_as_verified_contours(self):
        window = self.window
        window.open_design()
        design = window.design
        project = compound_project()
        design.replace_document(project)

        def apply_text(dialog):
            dialog.findChild(QPlainTextEdit).setPlainText("O")
            buttons = dialog.findChild(QDialogButtonBox)
            buttons.button(QDialogButtonBox.StandardButton.Ok).click()
            return dialog.result()

        with mock.patch.object(QDialog, "exec", apply_text):
            design.text_button.click()
        self.assertEqual(design.text_button.text(), "Edit Text…")
        self.assertEqual(design.project.shapes[1].text.value, "O")
        design.change_selected(x=110, y=40)
        path = self.config_path / "compounds.scc-template"
        with mock.patch.object(QFileDialog, "getSaveFileName", return_value=(str(path), "")):
            self.assertTrue(design.save())
        reopened = editor_model.load_project(path)
        with mock.patch.object(QFileDialog, "getOpenFileName", return_value=(str(path), "")):
            design.open_project()
        self.assertEqual(design.project, reopened)
        design.review_button.click()
        self.assertFalse(window.job.blocked)
        self.assertCountEqual(window.job.paths, reopened.artwork().paths)
        self.assertEqual(len(window.job.paths), 4)

    def test_workspace_switch_leaves_an_empty_design_open_without_reviewing_it(self):
        window = self.window
        original_job = window.job
        window.open_design()
        design = window.design
        design.commit_shapes((), ())
        self.assertFalse(design.review_button.isEnabled())
        self.assertTrue(design.send_workspace.isEnabled())
        self.assertIs(design.menu_bar.parentWidget(), design.send_workspace.parentWidget().parentWidget())
        design.send_workspace.click()
        self.assertEqual(window.tabs.currentIndex(), window.tab("Send"))
        self.assertIsNone(window.reviewed_design)
        self.assertEqual(window.job.paths, original_job.paths)
        window.design_button.click()
        self.assertIs(window.workspaces.currentWidget(), design)
        self.assertFalse(design.project.shapes)

    def test_file_menu_changes_pages_only_after_opening_the_print_file(self):
        window = self.window
        window.send_workspace.click()
        file_menu, *_ = window.menu_bar.actions()
        open_print = file_menu.menu().actions()[1]
        with mock.patch.object(window, "select_open", return_value=None):
            open_print.trigger()
        self.assertEqual(window.tabs.currentIndex(), window.tab("Send"))
        self.assertIsNone(window.pdf)
        path = Path("print.pdf")
        with mock.patch.object(window, "select_open", return_value=path), \
             mock.patch.object(window, "measure_sheet") as measure:
            open_print.trigger()
        self.assertEqual(window.tabs.currentIndex(), window.tab("Sheet"))
        self.assertEqual(window.pdf, path)
        measure.assert_called_once()

    def test_apply_material_opens_the_cut_settings_with_the_selected_values(self):
        window = self.window
        window.tabs.setCurrentIndex(window.tab("Materials"))
        with mock.patch.object(materials, "load", return_value={}):
            window.load_materials()
        self.assertFalse(window.apply_material_button.isEnabled())
        sample = mock.Mock()
        sample.cut_settings.return_value = qt_gui.graphtec.CutSettings(force=16)
        with mock.patch.object(materials, "load", return_value={}), \
             mock.patch.object(materials, "cutter_divisions", return_value={"Sample": sample}):
            window.load_materials()
        self.assertTrue(window.apply_material_button.isEnabled())
        window.apply_material_button.click()
        self.assertEqual(window.tabs.currentIndex(), window.tab("Send"))
        self.assertEqual(window.job.settings.force, 16)

    def test_manual_depth_fits_with_a_large_font(self):
        window = self.window
        font = self.app.font()
        font.setPointSize(18)
        window.resize(980, 760)
        window.show()
        window.send_workspace.click()
        window.blade["depth"].setValue(-1)
        field = window.blade["depth"]
        entry = field.lineEdit()
        field.setFont(font)
        entry.setFont(font)
        self.app.processEvents()
        self.assertEqual(entry.text(), "Manual")
        self.assertEqual(entry.font().pointSize(), 18)
        self.assertGreaterEqual(entry.contentsRect().width(),
                                entry.fontMetrics().horizontalAdvance(entry.text()) + 4)
        self.assertIsNone(window.job.settings.depth)

    def test_workspace_menus_open_send_settings_and_run_design_commands(self):
        window = self.window
        workspace_actions = window.menu_bar.actions()
        menus = {action.text().replace("&", ""): action.menu() for action in workspace_actions}
        self.assertEqual(tuple(menus), ("File", "Edit", "View", "Settings", "Help"))
        advanced = next(action for action in menus["Settings"].actions() if action.text() == "Advanced Send Settings")
        advanced.trigger()
        self.assertEqual(window.tabs.currentIndex(), window.tab("Send"))
        self.assertTrue(window.send_advanced.disclosure.isChecked())
        arrow = next(item for item in window.preview.scene().items() if item.toolTip() == "Feed this edge into the cutter")
        self.assertAlmostEqual(arrow.sceneBoundingRect().center().x(), window.job.grid.sheet_w_mm / 2)
        self.assertLess(arrow.sceneBoundingRect().bottom(), 0)
        window.open_design()
        design = window.design
        design_actions = design.menu_bar.actions()
        menus = {action.text().replace("&", ""): action.menu() for action in design_actions}
        self.assertEqual(tuple(menus), ("File", "Edit", "View", "Panels", "Object"))
        self.assertIn(design.save_action, menus["File"].actions())
        select_all = next(action for action in menus["Edit"].actions() if action.text() == "Select All")
        select_all.trigger()
        self.assertEqual(len(design.selection()), len(design.project.shapes))

    def test_wide_workspace_keeps_the_settings_pane_readable_and_resizable(self):
        window = self.window
        window.resize(1800, 900)
        window.show()
        self.app.processEvents()
        split = window.workspace_split
        self.assertEqual(split.sizes()[1], 480)
        self.assertIs(window.menu_bar.parentWidget(), window.design_button.parentWidget().parentWidget())
        self.assertEqual(window.preview_bar.height(), window.tabs.tabBar().height())

        total = sum(split.sizes())
        split.setSizes([total - 530, 530])
        window.hide()
        window.show()
        self.app.processEvents()
        self.assertEqual(split.sizes()[1], 530)

    def test_connection_method_defaults_to_automatic_inside_advanced_options(self):
        window = self.window
        window.show()
        window.send_workspace.click()
        self.assertEqual(window.link.currentData(), "auto")
        self.assertFalse(window.link.isVisible())
        self.assertTrue(window.detected.isVisible())
        toggle = window.send_advanced.disclosure
        toggle.click()
        self.assertTrue(window.link.isVisible())
        self.assertFalse(window.identifier.isVisible())
        self.assertFalse(window.channel.isVisible())
        window.link.setCurrentIndex(window.link.findData("ble"))
        self.assertTrue(window.identifier.isVisible())
        self.assertFalse(window.channel.isVisible())
        window.link.setCurrentIndex(window.link.findData("bluetooth"))
        self.assertTrue(window.channel.isVisible())
        toggle.click()
        self.assertFalse(window.link.isVisible())
        self.assertEqual(window.link.currentData(), "bluetooth")

    def test_sheet_failure_keeps_send_actions_visible_and_previous_paths_out_of_the_job(self):
        window = self.window
        window.show()
        rows = [dict(sheet=n, frontPage=n, svg=f"sheet-{n}.svg") for n in (1, 2)]
        project = editor_model.Project(279.4, 215.9, (editor_model.Shape("Rectangle", 40, 40),))
        matched = mock.Mock(rows=rows, layout={"sheet": "Letter", "portrait": False})
        matched.mat_placement.return_value = "Load the print face-up."
        matched.artwork.side_effect = [project.artwork("Cut"), qt_gui.bundle.BundleError("Unreadable SVG"),
                                       project.artwork("Cut")]
        with mock.patch.object(qt_gui.bundle, "load", return_value=matched), \
             mock.patch.object(window, "measure_sheet") as measure:
            window.load_bundle(Path("project.zip"))
            window.sheet_page_number.setValue(2)
            self.assertIs(window.bundle, matched)
            self.assertIsNone(window.artwork)
            self.assertIsNone(window.job)
            self.assertEqual(window.sheet_result.text(), "Unreadable SVG")
            window.send_workspace.click()
            self.assertEqual(window.tabs.currentIndex(), window.tab("Send"))
            self.assertTrue(window.send_button.isVisible())
            self.assertFalse(window.send_button.isEnabled())
            self.assertEqual(window.review_button.text(), "Review Sheet")
            window.review_button.click()
            window.sheet_page_number.setValue(1)
            self.assertIsNotNone(window.artwork)
            self.assertEqual(measure.call_count, 2)
            window.close_print.click()
            self.assertIsNone(window.pdf)
            self.assertIsNone(window.bundle)
            self.assertTrue(window.controls["sheet"].isEnabled())

    def test_material_selection_resolves_the_active_holder_variant(self):
        variants = {f"Sample · Holder {slot}": presets.Preset(
            f"Sample · Holder {slot}", "", "silhouette", {"force": force},
            factory_id=str(slot), factory_group="sample", factory_holder=slot)
            for slot, force in ((1, 12), (2, 21))}
        window = self.window
        with mock.patch.object(presets, "factory_presets", return_value=variants):
            window.refresh_preset_choices()
            window.blade["holder"].holders.button(2).click()
            window.select("preset", "Sample · Holder 1")
            self.assertEqual(window.choice("preset"), "Sample · Holder 2")
            self.assertEqual(window.job.settings.force, 21)
            self.assertEqual(window.job.settings.holder, 2)

    def test_advanced_controls_and_tool_profiles_are_independent(self):
        window = self.window
        window.show()
        window.send_workspace.click()
        self.app.processEvents()
        self.assertTrue(window.send_advanced.isHidden())
        self.assertTrue(window.blade["acceleration"].slider.isVisible())
        self.assertTrue(window.corner_button.isVisible())
        window.blade["force"].slider.setValue(28)
        window.blade["holder"].holders.button(2).click()
        window.blade["force"].setValue(12)
        self.assertFalse(window.blade["depth"].slider.isEnabled())
        window.blade["holder"].holders.button(1).click()
        self.assertEqual(window.blade["force"].slider.value(), 28)
        self.assertEqual(window.job.settings.holder, 1)
        window.send_advanced.disclosure.click()
        window.select("end_position", "start")
        window.send_advanced.disclosure.click()
        self.assertEqual(window.job.end_position, "start")

        window.open_design()
        design = window.design
        design.commit_shapes((editor_model.Shape("Rectangle", 30, 40, holder=1),
                              editor_model.Shape("Line", 95, 40, holder=2)), (0,))
        design.show_page_options()
        self.assertTrue(design.page_advanced.disclosure.isChecked())
        design.print_border.setChecked(True)
        self.assertTrue(design.print_border.isChecked())
        design.review_button.click()
        self.assertFalse(window.job.blocked)
        self.assertEqual([(group.settings.holder, group.settings.force) for group in window.job.action_groups],
                         [(1, 28), (2, 12)])

    def test_studio3_import_and_preview_keep_the_document_geometry(self):
        source = Path(__file__).parent / "fixtures" / "studio3" / "letter-catan-v2.studio3"
        window = self.window
        window.load_artwork(source)
        self.assertEqual(window.choice("sheet"), "Document Size")
        self.assertEqual(window.job.grid.sheet_w_mm, window.artwork.page_size_mm[0])
        self.assertEqual(window.job.paths, window.artwork.paths)
        window.open_design()
        self.assertEqual(len(window.design.project.shapes), 9)

    def test_design_draw_save_and_review_with_the_same_project_format(self):
        window = self.window
        window.show()
        window.open_design()
        self.app.processEvents()
        design = window.design
        design.commit_shapes((), ())
        design.tool.setCurrentIndex(design.tool.findData("Rectangle"))
        canvas = design.canvas
        start, end = (canvas.mapFromScene(x, y) for x, y in ((30, 35), (63, 70)))
        QTest.mousePress(canvas.viewport(), Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(canvas.viewport(), end)
        QTest.mouseRelease(canvas.viewport(), Qt.MouseButton.LeftButton, pos=end)
        shape, = design.project.shapes
        self.assertEqual(shape.kind, "Rectangle")
        self.assertAlmostEqual(shape.width, 33, delta=1.0)
        self.assertAlmostEqual(shape.height, 35, delta=1.0)
        target = self.config_path / "drawing.scc-template"
        with mock.patch.object(QFileDialog, "getSaveFileName", return_value=(str(target), "")):
            self.assertTrue(design.save())
        self.assertEqual(editor_model.load_project(target), design.project)
        design.review_button.click()
        self.assertEqual(window.tabs.currentIndex(), window.tab("Send"))
        self.assertEqual(len(window.job.paths), 1)
        self.assertFalse(window.job.blocked)

    def test_grouped_send_and_pending_design_are_preserved(self):
        window = self.window
        window.open_design()
        design = window.design
        cut = editor_model.Shape("Rectangle", 30, 40)
        score = editor_model.Shape("Line", 80, 40, action="Score")
        design.commit_shapes((cut, score), (0, 1))
        window.review_design(design.project, editor_model.ALL_ACTIONS)
        groups = window.group_rows
        groups["Cut"][0].setChecked(True)
        groups["Score"][0].setChecked(True)
        self.assertEqual([g.key for g in window.job.action_groups], ["Cut", "Score"])
        self.assertFalse(window.job.blocked)
        checked = window.job
        design.commit_shapes((cut,), (0,))
        design.send_workspace.click()
        self.assertEqual(window.tabs.currentIndex(), window.tab("Send"))
        self.assertEqual(window.review_tab(), "Design")
        self.assertIn("latest edits", window.send_refusal(checked, window.connection()))

    def test_send_rechecks_the_connection_and_only_passes_checked_steps(self):
        window = self.window
        expected = window.job
        connection = window.connection()
        cutter = mock.MagicMock()
        cutter.__enter__.return_value = cutter
        cutter.can_read = True
        cutter.status.return_value = "ready"
        cutter.run.return_value = 123

        def synchronous(work, callback):
            callback(work(), None)

        with mock.patch.object(window.tasks, "run", side_effect=synchronous), \
             mock.patch.object(transport, "open_transport", return_value=cutter) as connect:
            window.start_send(expected, connection, scan_only=True)
            self.assertEqual(cutter.run.call_args.args, (expected.registration_steps(),))
            self.assertFalse(window.busy)
            window.start_send(expected, connection)
            self.assertEqual(cutter.run.call_args.args, (expected.steps,))
            window.link.setCurrentIndex(window.link.findData("ble"))
            self.assertIn("connection changed", window.send_refusal(expected, connection))
            window.start_send(expected, connection)
            self.assertEqual(connect.call_count, 2)

    def test_pdf_measurement_retains_the_render_on_blade_changes(self):
        from PIL import Image

        window = self.window
        window.pdf = Path("print.pdf")
        bitmap = Image.new("RGB", (64, 48), (10, 150, 220))
        png = io.BytesIO()
        bitmap.save(png, format="PNG")
        report = mock.Mock(marks=())
        window.report = report
        window.preview.show_pdf(png.getvalue(), (279.4, 215.9), report)
        window.blade["force"].setValue(20)
        self.assertIs(window.report, report)
        self.assertFalse(window.preview.pdf.isNull())
        window.controls["mark_inset_mm"].setValue(11)
        self.assertIsNone(window.report)
        self.assertIsNone(window.preview.report)
        self.assertFalse(window.preview.pdf.isNull())

    def test_corner_test_sends_only_one_square_and_leaves_the_layout_in_place(self):
        window = self.window
        self.assertEqual(tuple(window.tabs.tabText(i) for i in range(window.tabs.count())),
                         ("Layout", "Sheet", "Materials", "Cut Job"))
        window.blade["passes"].setValue(4)
        plan = window.job
        sample = corner_test.build(plan, bleed_mm=window.controls["bleed_mm"].value())
        cutter = mock.MagicMock()
        cutter.__enter__.return_value = cutter
        cutter.can_read = True
        cutter.status.return_value = "ready"
        cutter.run.return_value = 123

        with mock.patch.object(window, "confirm", return_value=True) as confirm, \
             mock.patch.object(window.tasks, "run", side_effect=lambda work, done: done(work(), None)), \
             mock.patch.object(transport, "open_transport", return_value=cutter):
            window.corner_button.click()
        self.assertIn(sample.describe(), confirm.call_args.args[1])
        cutter.run.assert_called_once_with(sample.job.steps, wait_done=True)
        self.assertIs(window.job, plan)
        self.assertEqual(window.blade["passes"].value(), 4)

    def test_local_materials_file_is_used_and_invalid_replacement_is_refused(self):
        window = self.window
        source = self.config_path / "local.csv"
        source.write_text("category,uuid,parent_uuid,name_resolved,(force.pressure),(speed),(blade_depth)\n"
                          "Category,ID,Parent,Name,Force,Speed,Depth\n"
                          "group,g,,Sample vinyl,,,\n"
                          "leaf,l,g,,16,8,0.3\n")
        with mock.patch.object(window, "select_open", return_value=source):
            window.choose_material_csv()
        self.assertEqual(materials.find_condition_csv().read_bytes(), source.read_bytes())
        self.assertGreaterEqual(window.controls["preset"].findData("Sample vinyl"), 0)
        window.material.setCurrentIndex(window.material.findData("Sample vinyl"))
        window.apply_material()
        self.assertEqual([window.blade[field].value() for field in ("force", "speed", "depth")], [16, 8, 3])

        invalid = self.config_path / "unusable.csv"
        invalid.write_text("category,uuid,parent_uuid,name_resolved,(force.pressure),(speed)\n"
                           "Category,ID,Parent,Name,Force,Speed\n"
                           "group,g,,Sample vinyl,,\n")
        with mock.patch.object(window, "select_open", return_value=invalid):
            window.choose_material_csv()
        self.assertEqual(materials.find_condition_csv().read_bytes(), source.read_bytes())

    def test_starting_guide_repeats_one_confirmed_pass_and_applies_the_count(self):
        window = self.window
        window.open_material_guide()
        window.guide_apply.click()
        self.assertEqual([window.blade[field].value() for field in ("force", "speed", "depth", "passes")],
                         [25, 25, 7, 1])
        trial = window.guide.prepare(window.connection(), window.job_options(), window.job, "square")
        fake = mock.MagicMock()
        fake.__enter__.return_value = fake
        fake.can_read = True
        fake.status.return_value = "ready"
        fake.run.return_value = 200

        with mock.patch.object(window.tasks, "run", side_effect=lambda work, done: done(work(), None)), \
             mock.patch.object(transport, "open_transport", return_value=fake), \
             mock.patch.object(window, "confirm", return_value=True) as confirm:
            window.guide_send.click()
            self.assertTrue(window.guide_send.isEnabled())
            self.assertEqual(window.guide_outcome.currentData(), "")
            window.blade["force"].setValue(27)
            window.guide_send.click()
        confirm.assert_called_once()
        self.assertEqual(fake.run.call_args_list, [mock.call(trial.sheet.steps, wait_done=True)] * 2)
        self.assertIn("2 completed passes", window.guide_status.text())
        self.assertTrue(window.guide_use.isEnabled())
        with mock.patch.object(qt_gui, "QInputDialog") as dialog:
            dialog.return_value.exec.return_value = 1
            dialog.return_value.textValue.return_value = "310 gsm Matte"
            window.guide_use.click()
        dialog.return_value.setWindowTitle.assert_called_once_with("Save Material Preset?")
        saved = presets.get("310 gsm Matte")
        self.assertEqual(saved.origin, "measured")
        self.assertEqual(saved.cut_settings(), window.guide.tested_settings)
        self.assertTrue(saved.cut_settings().track_enhancing)
        self.assertEqual(window.choice("preset"), "310 gsm Matte")
        self.assertEqual((window.blade["force"].value(), window.blade["passes"].value()), (25, 2))
        window.guide_next.click()
        self.assertEqual(window.guide.spot, 2)
        self.assertFalse(window.guide_use.isEnabled())
        self.assertIn("Spot 1 · 2 passes", window.guide_history.text())
        self.assertEqual(window.preview.test_spots[0].passes, 2)

    def test_guide_marked_sheet_export_uses_the_preview_placement(self):
        window = self.window
        window.open_material_guide()
        self.assertIn("Registration off", window.footer_status.text())
        window.guide_registration.setChecked(True)
        self.assertIn("Registration scan before each pass", window.footer_status.text())
        path = self.config_path / "marked-test.svg"
        with mock.patch.object(window, "choose_output", return_value=path):
            window.guide_marked_sheet.click()
        self.assertTrue(window.preview.test_placement.registered)
        self.assertFalse(window.guide_registration.isEnabled())
        self.assertIn("registration-marks-DO-NOT-CUT", path.read_text())
        self.assertTrue(window.preview.trial.sheet.registration)

    def test_qt_launcher_can_be_selected_explicitly_or_automatically(self):
        with mock.patch.object(qt_gui, "main", return_value=0) as launch:
            self.assertEqual(cli.main(["gui", "--frontend", "qt"]), 0)
            self.assertEqual(cli.main(["gui"]), 0)
        self.assertEqual(launch.call_args_list, [mock.call([]), mock.call([])])

    def test_preview_zoom_survives_settings_edits_and_fit_restores_the_view(self):
        window = self.window
        window.show()
        self.app.processEvents()
        window.preview_zoom.in_button.click()
        scale = window.preview.transform().m11()
        self.assertEqual(window.preview_zoom.level.text(), "125%")
        window.blade["speed"].setValue(12)
        self.assertAlmostEqual(window.preview.transform().m11(), scale)
        window.preview_zoom.fit_button.click()
        self.assertEqual(window.preview_zoom.level.text(), "100%")

    def test_material_guide_requires_a_selected_cutter_to_send(self):
        window = self.window
        window.open_material_guide()
        self.assertTrue(window.guide_send.isEnabled())

        window.detected.setCurrentIndex(0)
        self.assertFalse(window.guide_send.isEnabled())
        self.assertIn("select a cutter", window.guide_status.text())

        window.detected.setCurrentIndex(window.detected.findData((1, 2)))
        self.assertTrue(window.guide_send.isEnabled())
        self.assertNotIn("select a cutter", window.guide_status.text())

    def test_material_guide_previews_fresh_spots_without_replacing_the_job(self):
        window = self.window
        window.show()
        self.app.processEvents()
        original = window.job
        window.open_material_guide()
        self.app.processEvents()
        first = window.preview.trial.sheet.extent()
        self.assertEqual(window.preview_title.text(), "Material test sheet")
        self.assertFalse(window.send_button.isVisible())
        window.guide_next.click()
        self.assertFalse(window.preview.trial.sheet.extent().overlaps(first))
        window.guide_edit.click()
        self.assertEqual(window.tabs.currentIndex(), window.tab("Send"))
        self.assertEqual(window.material_guide.text(), "Resume Material Test")
        window.material_guide.click()
        self.assertTrue(window.tuning)
        window.back_to_send.click()
        self.assertIsNone(window.preview.trial)
        self.assertTrue(window.send_button.isVisible())
        self.assertIs(window.job, original)

    def test_installed_tool_status_and_registration_failure_are_available_in_qt(self):
        window = self.window
        cutter = mock.MagicMock()
        cutter.__enter__.return_value = cutter
        cutter.can_read = True
        cutter.device = window.job.device
        cutter.status.return_value = "ready"
        cutter.status_with_tools.return_value = "ready · Holder 1 AutoBlade"
        cutter.run.side_effect = transport.RegistrationFailed()
        with mock.patch.object(window.tasks, "run", side_effect=lambda work, done: done(work(), None)), \
             mock.patch.object(transport, "open_transport", return_value=cutter):
            window.refresh_cutter.click()
        self.assertIn("Holder 1 AutoBlade", window.connection_status.text())

        def rejected(_work, done):
            done(None, cutter.run.side_effect)

        with mock.patch.object(window.tasks, "run", side_effect=rejected):
            window.start_send(window.job, window.connection(), scan_only=True)
        self.assertEqual(window.tabs.currentIndex(), window.tab("Sheet"))
        self.assertFalse(window.scan_box.isHidden())
        self.assertIn("Commands sent before the reply", window.scan_result.text())
        window.copy_scan.click()
        self.assertEqual(self.app.clipboard().text(), window.scan_result.text())

    def test_qt_preview_produces_a_nonempty_render(self):
        window = self.window
        window.show()
        self.app.processEvents()
        image = window.grab().toImage().convertToFormat(QImage.Format.Format_RGB32)
        self.assertGreaterEqual(image.width(), 900)
        self.assertGreater(len(set(bytes(image.constBits()))), 15)
