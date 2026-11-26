"""Exercise the Qt frontend without a display or a hardware cutter."""

import io
import math
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from cutline import cli, corner_test, cutters, editor_model, job, materials, path_edit, presets, sticker_trace, transport
from tests.test_vector_editing import compound_project
from tests.test_sticker_print import make_image

try:
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QColor, QImage, QPalette, QWheelEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import (QAbstractSpinBox, QApplication, QDialog, QDialogButtonBox, QFileDialog,
                                   QInputDialog, QMenu, QPlainTextEdit, QPushButton)
    from cutline import qt_gui
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
        # The tier stays offline even with a cutter on the bus: automatic tool
        # detection opens a link, so nothing may reach real hardware.
        offline = mock.patch.object(transport, "open_transport",
                                    side_effect=transport.NotFound("offline test"))
        offline.start()
        self.addCleanup(offline.stop)
        # Nor may a background refresh reach the radio (an LE scan, an inquiry).
        radio = mock.patch.object(cutters, "wireless_choices", return_value={})
        radio.start()
        self.addCleanup(radio.stop)
        self.window = qt_gui.Window()
        with mock.patch.object(self.window, "check_device"):
            self.window.refresh_usb()
        self.addCleanup(self.window.deleteLater)

    def test_multiple_usb_cutters_require_an_endpoint_choice(self):
        window = self.window
        device = qt_gui.graphtec.device_by_name("Silhouette Cameo 4")
        with mock.patch.object(window, "check_device"), mock.patch.object(transport, "usb_endpoints",
                               return_value=[(device, "", (1, 2)), (device, "", (1, 3))]):
            window.refresh_usb()
            self.assertIsNone(window.connection()[1])
            self.assertFalse(window.send_button.isEnabled())
            window.detected.setCurrentIndex(window.detected.findData((1, 3)))
            self.assertEqual(window.connection()[1], (1, 3))
            self.assertTrue(window.send_button.isEnabled())

    def test_scan_for_cutters_is_offered_while_no_cutter_is_found(self):
        window = self.window
        classic = ("bluetooth", "01:23:45:AB:CD:EF")
        with mock.patch.object(window, "check_device"), \
             mock.patch.object(transport, "usb_endpoints", return_value=[]):
            window.wireless_choices = {}
            window.refresh_usb()
            self.assertFalse(window.scan_cutters.isHidden())
            self.assertTrue(window.scan_cutters.isEnabled())
            self.assertTrue(window.discover.isHidden())
            self.assertEqual(window.connection_status.text(), cutters.NO_CUTTER_TEXT)
            with mock.patch.object(window.tasks, "run") as run:
                window.scan_cutters.click()
            self.assertEqual(run.call_count, 1)
            self.assertFalse(window.scan_cutters.isEnabled())
            self.assertEqual(window.connection_status.text(), cutters.SCANNING_TEXT)
            window._wireless_done({}, None)
            self.assertFalse(window.scan_cutters.isHidden())
            self.assertTrue(window.scan_cutters.isEnabled())
            self.assertEqual(window.connection_status.text(), cutters.NO_CUTTER_TEXT)
            window._wireless_done({classic: "CAMEO 4-1234 · Bluetooth Classic · 01:23:45:AB:CD:EF"}, None)
            self.assertTrue(window.scan_cutters.isHidden())
            self.assertFalse(window.discover.isHidden())
            self.assertIsNone(window.connection()[1])
        with mock.patch.object(window, "check_device"):
            window._wireless_done({}, None)
            window.refresh_usb()
        self.assertTrue(window.scan_cutters.isHidden())  # the USB cutter is back
        self.assertEqual(window.connection()[1], (1, 2))

    def test_only_scan_and_refresh_cutters_search_for_unpaired_cutters(self):
        window = self.window
        with mock.patch.object(window.tasks, "run") as run, \
             mock.patch.object(cutters, "wireless_choices", return_value={}) as choices:
            window.refresh_wireless()                        # the 30 s timer
            run.call_args.args[0]()
            self.assertEqual(choices.call_args, mock.call(search=False))
            window.refresh_wireless(search=True)             # asked for while the refresh runs
            self.assertEqual(run.call_count, 1)
            window._wireless_done({}, None)
            self.assertEqual(run.call_count, 2)              # queued, not dropped
            run.call_args.args[0]()
            self.assertEqual(choices.call_args, mock.call(search=True))
            window._wireless_done({}, None)
            window.find_cutters()                            # Refresh Cutters
            run.call_args.args[0]()
            self.assertEqual(choices.call_args, mock.call(search=True))

    def test_choosing_a_cutter_reads_its_status_and_pairs_an_unpaired_one(self):
        # As in Studio: selecting a cutter reads it, and pairs one the search found.
        window = self.window
        classic = ("bluetooth", "AA:BB:CC:00:11:22")
        listing = {classic: "CAMEO 4-1234 · Bluetooth Classic · AA:BB:CC:00:11:22"}
        for unpaired in (True, False):
            with self.subTest(unpaired=unpaired), \
                 mock.patch.object(transport, "usb_endpoints", return_value=[]), \
                 mock.patch.object(cutters, "unpaired", side_effect=lambda e, u=unpaired: u and e == classic), \
                 mock.patch.object(window, "detect_tools") as detect, \
                 mock.patch.object(window.tasks, "run") as run:
                window._wireless_done({}, None)
                detect.reset_mock()
                run.reset_mock()
                window._wireless_done(listing, None)
                self.assertIsNone(window.connection()[1])
                window.detected.setCurrentIndex(window.detected.findData(classic))
                self.assertIsNone(window.connection()[1])
                self.assertEqual(window.connection_status.text(),
                                 "Pairing with cutter…" if unpaired else "Connecting…")
                self.assertEqual(detect.call_count, 0)
                self.assertEqual(run.call_count, 1)          # the status read, which pairs first
                window.checking_status = False

    def test_template_import_works_while_startup_status_is_pending(self):
        window = self.window
        classic = ("bluetooth", "AA:BB:CC:00:11:22")
        source = Path(__file__).parent / "fixtures" / "studio3" / "letter-catan-v2.studio3"
        with mock.patch.object(transport, "usb_endpoints", return_value=[]), \
             mock.patch.object(window.tasks, "run") as run, \
             mock.patch.object(QFileDialog, "getOpenFileName", return_value=(str(source), "")) as picker:
            window._wireless_done({classic: "CAMEO 4-1234 · Bluetooth Classic"}, None)
            popup = window.controls["layout_preset"]
            window.show()
            popup.showPopup()
            window.detected.setCurrentIndex(window.detected.findData(classic))
            self.assertTrue(popup.isEnabled())
            self.assertTrue(popup.view().isVisible())
            window.select("layout_preset", "mtg-cameo-a4")
            self.assertEqual(window.job.grid.sheet_name, "A4")
            popup.hidePopup()
            run.assert_called_once()
            self.assertTrue(window.busy)
            self.assertFalse(window.send_button.isEnabled())
            self.assertTrue(window.open_artwork_button.isEnabled())
            self.assertTrue(window.open_print_button.isEnabled())
            self.assertTrue(window.guide_shape.isEnabled())
            window.open_design()
            self.assertIsNotNone(window.design)
            window.leave_design()
            window.open_artwork_button.click()
            picker.assert_called_once()
            self.assertEqual(window.job.paths, window.artwork.paths)
            self.send_shortcut("Ctrl+O").trigger()
            self.assertEqual(picker.call_count, 2)
            run.call_args.args[1](None, transport.NotFound("offline test"))
            self.assertFalse(window.busy)
            window.sending = True
            window.refresh_send()
            self.assertFalse(window.open_artwork_button.isEnabled())
            window.open_artwork()
            self.assertEqual(picker.call_count, 2)
            artwork = window.artwork
            window.load_artwork(source)
            self.assertIs(window.artwork, artwork)

    def test_a_listed_cutter_that_does_not_answer_is_not_called_connected(self):
        # A discovered cutter may stop responding before it is selected.
        window = self.window
        classic = ("bluetooth", "AA:BB:CC:00:11:22")
        listing = {classic: "CAMEO 4-1234 · Bluetooth Classic · AA:BB:CC:00:11:22"}
        for answers in (True, False):
            cutter = mock.MagicMock()
            cutter.status_and_tools.return_value = ("unloaded", None)
            cutter.firmware_version.return_value = "1.20"
            cutter.device = qt_gui.graphtec.device_by_name("Silhouette Cameo 4")
            link = mock.MagicMock()
            link.__enter__.return_value = cutter
            if not answers:
                link.__enter__.side_effect = transport.NotFound("No cutter detected at AA:BB:CC:00:11:22.")
            with self.subTest(answers=answers), \
                 mock.patch.object(transport, "usb_endpoints", return_value=[]), \
                 mock.patch.object(window, "open_link", return_value=link), \
                 mock.patch.object(window.tasks, "run", side_effect=self._run_now):
                window._wireless_done({}, None)
                window._wireless_done(listing, None)
                window.detected.setCurrentIndex(window.detected.findData(classic))
                self.assertEqual(window.connection()[:2], classic if answers else ("auto", None))
                self.assertEqual(window.detected.findData(classic) >= 0, answers)
                text = window.connection_status.text()
                self.assertNotIn("Connected", text)
                if answers:
                    self.assertEqual(text, "Silhouette Cameo 4 · unloaded · 1.20")
                    link.__enter__.side_effect = transport.NotFound("cutter powered off")
                    window._wireless_done({}, None)
                    self.assertIsNone(window.connection()[1])
                    self.assertFalse(window.send_button.isEnabled())
                    self.assertLess(window.detected.findData(classic), 0)
                else:
                    self.assertEqual(text, cutters.NOT_DETECTED_TEXT)

    @staticmethod
    def _run_now(work, done):
        try:
            value = work()
        except Exception as error:
            return done(None, error)
        return done(value, None)

    def test_check_status_button_reads_status(self):
        with mock.patch.object(self.window.tasks, "run"):
            self.window.refresh_cutter.click()
        self.assertEqual(self.window.connection_status.text(), "Reading cutter status…")

    def test_refresh_keeps_an_explicit_cutter_and_does_not_switch_on_disconnect(self):
        window = self.window
        device = qt_gui.graphtec.device_by_name("Silhouette Cameo 4")
        first, other = (1, 2), (1, 3)
        with mock.patch.object(window, "check_device"), mock.patch.object(transport, "usb_endpoints") as detect:
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
        cut_job = window.tabs.widget(window.tab("Send"))
        for widget in (window.controls["preset"], window.material_note, window.save_preset_btn,
                       window.delete_preset_btn, window.blade["force"]):
            self.assertTrue(cut_job.isAncestorOf(widget), "material choice and saving live on Cut Job")

    def test_layout_preset_tracks_and_restores_defaults(self):
        win = self.window
        self.assertEqual(win.controls["layout_preset"].currentText(), "Letter · Standard (8 cards)")
        original = win.job.paths
        win.select("sheet", "A4")
        self.assertEqual(win.controls["layout_preset"].currentText(), "Custom")
        win.select("layout_preset", "default")
        self.assertEqual(win.controls["layout_preset"].currentText(), "Letter · Standard (8 cards)")
        self.assertEqual(win.job.paths, original)

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

    def test_draw_in_design_replaces_only_after_confirmation(self):
        window = self.window
        original_job = window.job
        window.draw_in_design_btn.click()
        design = window.design
        self.assertEqual(design.project.shapes, ())
        self.assertEqual(design.dock.picker.currentText(), "Page Setup")
        self.assertEqual((design.project.width, design.project.height),
                         (original_job.grid.sheet_w_mm, original_job.grid.sheet_h_mm))
        self.assertEqual(design.project.mat, original_job.mat)
        self.assertEqual(design.project.registration, editor_model.Registration.from_marks(original_job.marks))
        self.assertIs(window.job, original_job)
        design.add_default_shape()
        design.send_workspace.click()
        previous = design.project
        with mock.patch.object(qt_gui.QMessageBox, "question", return_value=qt_gui.QMessageBox.StandardButton.Cancel):
            window.draw_in_design_btn.click()
        self.assertEqual(design.project, previous)
        self.assertIsNot(window.workspaces.currentWidget(), design)
        window.open_design()
        self.assertEqual(design.project, previous)
        with mock.patch.object(qt_gui.QMessageBox, "question", return_value=qt_gui.QMessageBox.StandardButton.Discard):
            window.draw_in_design_btn.click()
        self.assertEqual(design.project.shapes, ())
        design.add_default_shape()
        previous = design.project
        target = self.config_path / "saved.scc-template"
        with mock.patch.object(qt_gui.QMessageBox, "question", return_value=qt_gui.QMessageBox.StandardButton.Save), \
                mock.patch.object(QFileDialog, "getSaveFileName", return_value=(str(target), "")):
            window.draw_in_design_btn.click()
        self.assertEqual(editor_model.load_project(target), previous)
        self.assertEqual(design.project.shapes, ())
        window.open_design(replace_design=True)
        self.assertTrue(design.project.shapes)

    def test_add_shape_by_keyboard_focuses_transform_and_undoes(self):
        window = self.window
        window.show()
        window.draw_in_design_btn.click()
        design = window.design
        design.tool.setCurrentText("Ellipse")
        design.add_shape_button.setFocus()
        QTest.keyClick(design.add_shape_button, Qt.Key.Key_Space)
        self.app.processEvents()
        shape, = design.project.shapes
        self.assertEqual(shape.kind, "Ellipse")
        self.assertEqual(design.selection(), (0,))
        self.assertEqual(design.dock.picker.currentText(), "Transform")
        self.assertTrue(design.fields["x"].hasFocus())
        design.fields["x"].setValue(42)
        self.assertEqual(design.project.shapes[0].x, 42)
        design.undo()
        design.undo()
        self.assertEqual(design.project.shapes, ())

    def test_sticker_readiness_follows_multiple_images_and_undo(self):
        from dataclasses import replace
        self.window.draw_in_design_btn.click()
        design = self.window.design
        image = self.config_path / "sticker.png"
        make_image(image)
        sticker = sticker_trace.place(image, design.project.width, design.project.height)
        traced = sticker_trace.trace(sticker, source="Automatic", threshold=128, keep_holes=True, bleed_mm=0)
        design.commit_shapes((traced, sticker), (0,))
        self.assertFalse(design.export_action.isEnabled())
        with mock.patch.object(design, "trace_sticker") as trace:
            design.trace_next_button.click()
            trace.assert_called_once()
        self.assertEqual(design.selection(), (1,))
        design.commit_shapes((traced, replace(sticker, action="None")), ())
        self.assertTrue(design.export_action.isEnabled())
        self.assertTrue(design.trace_next_button.isHidden())
        design.undo()
        self.assertFalse(design.export_action.isEnabled())

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
        self.assertFalse(design.review_button.isEnabled())
        design.select(())
        self.assertIn("sticker.png", design.export_requirement.text())
        window.show()
        window.resize(720, 520)
        self.app.processEvents()
        self.assertEqual(window.size().toTuple(), (720, 520))
        objects = design.sidebar.widget(1)
        self.assertLess(design.dock.geometry().bottom(), objects.geometry().top())
        self.assertTrue(design.objects.isVisible())
        self.assertFalse(design.trace_next_button.isHidden())

        def apply_trace(dialog):
            buttons = dialog.findChild(QDialogButtonBox)
            buttons.button(QDialogButtonBox.StandardButton.Ok).click()
            return dialog.result()

        with mock.patch.object(QDialog, "exec", apply_trace):
            design.trace_next_button.click()
        self.assertTrue(design.review_button.isEnabled())
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
        from cutline.qt_controls import _contrast, workspace_style

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

    def test_checkbox_outlines_stand_out_in_light_and_dark_themes(self):
        import re
        from cutline.qt_controls import _contrast, workspace_style

        # The second pair is the dark theme where unchecked boxes disappeared:
        # the theme's mid colour sat almost on the panel.
        for base, text, mid in (("#ffffff", "#202020", "#b8b8b8"), ("#1e2124", "#eeeeee", "#2a2e32")):
            palette = QPalette()
            palette.setColor(QPalette.ColorRole.Base, QColor(base))
            palette.setColor(QPalette.ColorRole.Text, QColor(text))
            palette.setColor(QPalette.ColorRole.Mid, QColor(mid))
            style = workspace_style(palette)
            edge = re.search(r"QCheckBox::indicator:unchecked \{[^}]*border: 1px solid (#[0-9a-f]+)", style)
            self.assertIsNotNone(edge, "the outline is a computed colour, not palette(mid)")
            self.assertGreaterEqual(_contrast(QColor(edge[1]), QColor(base)), 3)

    def test_compound_and_text_reopen_and_review_as_verified_contours(self):
        window = self.window
        window.open_design()
        design = window.design
        project = compound_project()
        design.replace_document(project)

        self.assertFalse(design.text_button.isEnabled(), "Edit Text needs text selected")
        design.begin_text(at=(60, 40))
        design.text_box.setPlainText("O")
        design.finish_text()
        self.assertIsNone(design.text_box)
        self.assertTrue(design.text_button.isEnabled())
        self.assertEqual(design.project.shapes[1].text.value, "O")
        design.change_selected(x=110, y=40)
        path = self.config_path / "compounds.scc-template"
        with mock.patch.object(QFileDialog, "getSaveFileName", return_value=(str(path), "")):
            self.assertTrue(design.save())
        reopened = editor_model.load_project(path)
        with mock.patch.object(QFileDialog, "getOpenFileName", return_value=(str(path), "")):
            design.open_project()
        self.assertEqual(design.project, reopened)
        design.send_workspace.click()
        self.assertFalse(window.job.blocked)
        self.assertCountEqual(window.job.paths, reopened.artwork().paths)
        self.assertEqual(len(window.job.paths), 4)

    def test_send_does_not_reuse_deleted_design_paths(self):
        window = self.window
        window.show()
        window.draw_in_design_btn.click()
        design = window.design
        design.commit_shapes((editor_model.Shape("Rectangle", 30, 30),), (0,))
        design.send_workspace.click()
        self.assertEqual(len(window.job.paths), 1)
        previous = window.reviewed_design
        window.design_button.click()
        design.commit_shapes((), ())
        design.send_workspace.click()
        self.assertIs(window.workspaces.currentWidget(), design)
        self.assertIn("No cut paths in this design", design.status.text())
        self.assertTrue(design.notice_bar.isVisible())
        self.assertIs(window.reviewed_design, previous)
        design.undo()
        self.assertFalse(design.notice_bar.isVisible())
        design.send_workspace.click()
        self.assertEqual(len(window.job.paths), 1)
        self.assertIs(window.reviewed_design, previous)

    def test_workspace_switch_leaves_an_empty_design_open_without_reviewing_it(self):
        window = self.window
        original_job = window.job
        window.open_send_workspace()
        window.open_design()
        design = window.design
        design.commit_shapes((), ())
        self.assertTrue(design.review_button.isHidden())
        self.assertTrue(design.send_workspace.isEnabled())
        self.assertIs(design.menu_bar.parentWidget(), design.send_workspace.parentWidget().parentWidget())
        design.send_workspace.click()
        self.assertEqual(window.tabs.currentIndex(), window.tab("Send"))
        self.assertIsNone(window.reviewed_design)
        self.assertEqual(window.job.paths, original_job.paths)
        window.design_button.click()
        self.assertIs(window.workspaces.currentWidget(), design)
        self.assertFalse(design.project.shapes)

    def test_workspace_switch_exposes_the_current_workspace_as_checked(self):
        window = self.window
        self.assertTrue(window.send_workspace.isCheckable())
        self.assertTrue(window.send_workspace.isChecked())
        self.assertFalse(window.design_button.isChecked())
        window.send_workspace.click()
        self.assertTrue(window.send_workspace.isChecked(), "clicking the current workspace keeps it current")
        window.design_button.click()
        design = window.design
        self.assertTrue(design.design_workspace.isChecked())
        self.assertFalse(design.send_workspace.isChecked())
        design.send_workspace.click()
        self.assertFalse(window.design_button.isChecked(), "Send's Design button must not stay pressed")
        self.assertTrue(window.send_workspace.isChecked())
        self.assertFalse(design.send_workspace.isChecked())

    def test_cut_groups_appear_only_for_a_grouped_send(self):
        window = self.window
        window.tabs.setCurrentIndex(window.tab("Send"))
        window.refresh_send()
        self.assertTrue(window.group_box.isHidden(), "no artwork: the empty Cut groups box stays hidden")
        window.open_design()
        design = window.design
        design.commit_shapes((editor_model.Shape("Rectangle", 30, 40),
                              editor_model.Shape("Line", 80, 40, action="Score")), (0, 1))
        window.review_design(design.project, editor_model.ALL_ACTIONS)
        window.refresh_send()
        self.assertFalse(window.group_box.isHidden(), "grouped artwork shows its groups")

    def send_shortcut(self, keys):
        from PySide6.QtGui import QKeySequence
        wanted = QKeySequence(keys)
        found = [action for action in self.window.send_view.actions() if wanted in action.shortcuts()]
        self.assertEqual(len(found), 1, keys)
        self.assertEqual(found[0].shortcutContext(), qt_gui.Qt.ShortcutContext.WidgetWithChildrenShortcut)
        return found[0]

    def test_send_workspace_has_keyboard_shortcuts_scoped_to_itself(self):
        window = self.window
        for keys, page in (("Ctrl+1", "Layout"), ("Ctrl+2", "Sheet"), ("Ctrl+3", "Send")):
            self.send_shortcut(keys).trigger()
            self.assertEqual(window.tabs.currentIndex(), window.tab(page), keys)
        with mock.patch.object(qt_gui.QFileDialog, "getOpenFileName", return_value=("", "")) as artwork:
            self.send_shortcut("Ctrl+O").trigger()
        artwork.assert_called_once()
        with mock.patch.object(window, "select_open", return_value=None) as sheet:
            self.send_shortcut("Ctrl+Shift+O").trigger()
        sheet.assert_called_once()
        levels = []
        window.preview.zoom_changed.connect(levels.append)
        self.send_shortcut("Ctrl+=").trigger()
        self.send_shortcut("Ctrl+-").trigger()
        self.send_shortcut("Ctrl+0").trigger()
        self.assertEqual(len(levels), 3)
        self.send_shortcut("Ctrl+F1").trigger()
        self.assertIs(window.workspaces.currentWidget(), window.design)
        # Nothing that moves the cutter is one keystroke away: these are all of them.
        self.assertEqual(sorted(action.text() for action in window.send_view.actions()),
                         ["Cut Job", "Edit Current Cut Template", "Fit Preview", "Layout",
                          "Open Cut Template…", "Open Print File…", "Sheet", "Zoom In", "Zoom Out"])

    def test_troubleshooting_opens_the_controls_its_advice_names(self):
        window = self.window
        help_menu = next(action.menu() for action in window.menu_bar.actions() if action.text() == "&Help")
        self.assertIn("Troubleshoot a Cut…", [action.text() for action in help_menu.actions()])
        window.follow_troubleshooting(qt_gui.OPEN_SHEET)
        self.assertEqual(window.tabs.currentIndex(), window.tab("Sheet"))
        window.follow_troubleshooting(qt_gui.DIAL_IN)
        self.assertEqual(window.tabs.currentIndex(), window.tab("Send"))
        self.assertTrue(window.tuning)
        window.tabs.setCurrentIndex(window.tab("Sheet"))
        window.follow_troubleshooting(qt_gui.CUT_SETTINGS)
        self.assertEqual(window.tabs.currentIndex(), window.tab("Send"))
        with mock.patch.object(qt_gui.QInputDialog, "getItem", return_value=("", False)) as ask, \
                mock.patch.object(qt_gui.QMessageBox, "exec") as shown:
            window.troubleshoot_cut(False)
        ask.assert_called_once()
        shown.assert_not_called()
        with mock.patch.object(qt_gui.QMessageBox, "exec"), \
                mock.patch.object(qt_gui.QMessageBox, "clickedButton", return_value=None), \
                mock.patch.object(window, "follow_troubleshooting") as follow:
            window.troubleshoot_cut(qt_gui.REGISTRATION_PROBLEM)
        follow.assert_not_called()

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

    def test_choosing_a_material_applies_it_and_saving_updates_that_material(self):
        window = self.window
        with mock.patch.object(presets, "factory_presets", return_value={}):
            presets.save_blade_settings(presets.STARTING_NAME, new_name="Sample", force=16,
                                        speed=5, depth=None, passes=2)
            window.refresh_preset_choices()
            window.load_materials()
            self.assertEqual(window.job.settings.force, 25)
            self.assertIn("Card Maker guide", window.material_note.text())
            window.select("preset", "Sample")
            self.assertEqual(window.job.settings.force, 16)
            self.assertTrue(window.save_preset_btn.isEnabled())
            window.blade["force"].setValue(18)
            window.save_preset()
            self.assertEqual(presets.get("Sample").cut_settings().force, 18)
            window.save_preset("Copy of Sample")
            self.assertEqual(window.choice("preset"), "Copy of Sample")
            self.assertEqual(window.job.preset.name, "Copy of Sample")

    def test_material_actions_fit_the_compact_panel(self):
        window = self.window
        window.resize(850, 600)
        window.show()
        window.tabs.setCurrentIndex(window.tab("Send"))
        window.send_advanced.disclosure.setChecked(True)
        self.app.processEvents()
        page = window.tabs.currentWidget()
        self.assertEqual(page.horizontalScrollBar().maximum(), 0)

        font = window.font()
        font.setPointSize(15)
        window.setFont(font)
        window.resize(720, 520)
        QTest.qWait(30)
        self.assertEqual(page.horizontalScrollBar().maximum(), 0)
        for button in (window.save_preset_btn, window.save_new_preset_btn, window.delete_preset_btn,
                       window.import_csv_btn, window.import_folder_btn, window.restore_materials_btn):
            self.assertGreaterEqual(button.width(), button.minimumSizeHint().width(), button.text())

    def test_delete_acts_on_the_chosen_material_and_only_custom_ones(self):
        window = self.window
        window.load_materials()
        self.assertFalse(window.delete_preset_btn.isEnabled(), "built-in presets cannot be deleted")

        with TemporaryDirectory() as folder, \
             mock.patch.object(presets, "user_dir", return_value=Path(folder)):
            custom = presets.save_blade_settings(
                presets.STARTING_NAME, force=18, speed=10, depth=2, passes=1,
                new_name="Custom Qt Test")
            window.refresh_preset_choices()
            window.load_materials()
            window.select("preset", "Custom Qt Test")
            self.assertEqual(window.job.settings.force, 18)
            self.assertTrue(window.delete_preset_btn.isEnabled())
            self.assertIn("Custom Qt Test", window.delete_preset_btn.text())
            with mock.patch.object(qt_gui.QMessageBox, "question", return_value=qt_gui.QMessageBox.StandardButton.Yes):
                window.delete_selected_preset()
            self.assertFalse(custom.exists())
            self.assertEqual(window.choice("preset"), presets.STARTING_NAME)
            self.assertFalse(window.delete_preset_btn.isEnabled())

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
        self.assertEqual(tuple(menus), ("File", "Edit", "View", "Object"))
        self.assertIn(design.save_action, menus["File"].actions())
        select_all = next(action for action in menus["Edit"].actions() if action.text() == "Select All")
        select_all.trigger()
        self.assertEqual(len(design.selection()), len(design.project.shapes))

    def test_the_app_reopens_on_the_material_used_last(self):
        window = self.window
        names = [window.controls["preset"].itemData(i) for i in range(window.controls["preset"].count())]
        other = next(name for name in names if name != qt_gui.config.DEFAULT_PRESET)
        window.select("preset", other)
        self.assertEqual(qt_gui.config.load_settings()["last_preset"], other)
        with mock.patch.object(window, "check_device"):
            reopened = qt_gui.Window()
        self.assertEqual(reopened.choice("preset"), other)
        self.assertEqual(reopened.blade["force"].value(),
                         presets.get(other, reopened.choice("cutter")).cut_settings().force)
        reopened.close()

        qt_gui.config.save_settings(dict(qt_gui.config.load_settings(), last_preset="A material since deleted"))
        fresh = qt_gui.Window()
        self.assertEqual(fresh.choice("preset"), qt_gui.config.DEFAULT_PRESET)
        fresh.close()

    def test_the_material_test_compares_two_settings_and_hides_pass_counts(self):
        window = self.window
        blade = window.blade
        window.tabs.setCurrentIndex(window.tab("Send"))
        self.assertTrue(window.stage_toggle.isVisibleTo(window))
        blade["force"].setValue(10)
        window.open_material_guide()
        # One pass at a time, and force, speed and depth live in A and B.
        for hidden in (blade["passes"], window.stage_toggle, blade["force"], blade["speed"]):
            self.assertFalse(hidden.isVisibleTo(window))
        a, b = window.guide_headers["A"], window.guide_headers["B"]
        self.assertEqual((a.text(), b.text()), ("Cut Settings A", "Cut Settings B"))
        self.assertTrue(a.isChecked(), "A opens with the test")
        self.assertEqual(window.guide_fields["A"]["force"].value(), 10, "both start from Cut Settings")
        self.assertEqual(window.guide_fields["B"]["force"].value(), 10)

        # Each opens and closes on its own; both can be open.
        b.setChecked(True)
        self.assertTrue(a.isChecked() and b.isChecked())
        self.assertTrue(window.guide_bodies["B"].isVisibleTo(window))
        a.setChecked(False)
        self.assertFalse(window.guide_bodies["A"].isVisibleTo(window))
        self.assertTrue(window.guide_bodies["B"].isVisibleTo(window))

        # The dropdown by Cut One Pass picks which one cuts.
        window.guide_fields["B"]["force"].setValue(28)
        self.assertEqual(blade["force"].value(), 10, "editing B leaves the chosen A alone")
        self.assertEqual(window.guide_pass_name, "A")
        window.guide_slot_choice.setCurrentIndex(window.guide_slot_choice.findData("B"))
        self.assertEqual(window.guide_pass_name, "B")
        self.assertEqual(blade["force"].value(), 28)
        window.guide_fields["B"]["force"].setValue(30)
        self.assertEqual(blade["force"].value(), 30)
        window.guide_slot_choice.setCurrentIndex(window.guide_slot_choice.findData("A"))
        self.assertEqual(blade["force"].value(), 10)

        window.guide_apply.click()
        self.assertEqual(window.guide_fields["A"]["force"].value(), blade["force"].value(),
                         "starting settings land in the chosen setting")

        window.back_to_send.click()
        for shown in (blade["passes"], window.stage_toggle, blade["force"]):
            self.assertTrue(shown.isVisibleTo(window))

        # When light passes are enabled, A inherits main settings and B inherits light pass settings.
        blade["force"].setValue(25)
        blade["speed"].setValue(12)
        window.stage_toggle.setChecked(True)
        blade["light_force"].setValue(8)
        blade["light_speed"].setValue(-1)
        window.open_material_guide()
        self.assertEqual(window.guide_fields["A"]["force"].value(), 25)
        self.assertEqual(window.guide_fields["A"]["speed"].value(), 12)
        self.assertEqual(window.guide_fields["B"]["force"].value(), 8)
        self.assertEqual(window.guide_fields["B"]["speed"].value(), 12)
        window.back_to_send.click()

    def test_staged_passes_open_from_a_box_under_passes(self):
        window = self.window
        blade = window.blade
        window.tabs.setCurrentIndex(window.tab("Send"))
        self.assertFalse(window.send_advanced.disclosure.isChecked(), "no need to open Advanced Options")
        self.assertTrue(window.stage_toggle.isVisibleTo(window))
        self.assertFalse(window.stage_toggle.isChecked())
        self.assertFalse(blade["light_passes"].isVisibleTo(window))
        # Turning it on starts at one light pass and leaves room for a full one.
        window.stage_toggle.setChecked(True)
        self.assertEqual((blade["light_passes"].value(), blade["passes"].value()), (1, 2))
        self.assertTrue(blade["light_passes"].isVisibleTo(window))
        window.stage_toggle.setChecked(False)
        self.assertEqual(blade["light_passes"].value(), 0)
        self.assertFalse(blade["light_force"].isVisibleTo(window))
        for key, value in (("passes", 8), ("force", 20), ("speed", 5)):
            blade[key].setValue(value)
        self.assertEqual(window.job.action_groups, ())
        blade["light_passes"].setValue(2)
        self.assertTrue(window.stage_toggle.isChecked(), "the box follows the light pass count")
        self.assertTrue(blade["light_force"].isVisibleTo(window))
        self.assertTrue(blade["light_speed"].isVisibleTo(window))
        self.assertEqual(blade["light_force"].specialValueText(), "Same as Force")
        blade["light_force"].setValue(10)
        blade["light_speed"].setValue(3)
        self.assertEqual(window.stage_note.text(), "Passes 1–2 at force 10, speed 3; passes 3–8 at force 20, speed 5.")
        self.assertEqual(window.job.blocked, [])
        self.assertEqual([(g.settings.passes, g.settings.force, g.settings.speed) for g in window.job.action_groups],
                         [(2, 10, 3), (6, 20, 5)])
        blade["light_passes"].setValue(8)
        self.assertIn("at least one full pass", window.stage_note.text())
        self.assertIsNone(window.job, "an impossible staging builds no job")
        blade["light_passes"].setValue(0)
        self.assertEqual(window.job.action_groups, ())
        self.assertFalse(window.stage_toggle.isChecked())

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

    def test_sheet_failure_offers_review_and_keeps_previous_paths_out_of_the_job(self):
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
            self.assertFalse(window.send_button.isVisible())
            self.assertFalse(window.send_button.isEnabled())
            self.assertTrue(window.review_button.isVisible())
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
        design.show_document()
        self.assertIn("page", design.dock.shown())
        self.assertTrue(design.dock.panels["page"].body.isVisibleTo(design.dock))
        design.print_border.setChecked(True)
        self.assertTrue(design.print_border.isChecked())
        design.send_workspace.click()
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
        design.send_workspace.click()
        self.assertEqual(window.tabs.currentIndex(), window.tab("Send"))
        self.assertEqual(len(window.job.paths), 1)
        self.assertFalse(window.job.blocked)

    def test_switching_to_send_sends_the_edited_design_like_studio(self):
        window = self.window
        generated = len(window.job.paths)
        window.open_design()
        design = window.design
        design.send_workspace.click()
        self.assertEqual(len(window.job.paths), generated, "a design only looked at leaves Send alone")
        window.open_design()
        cut = editor_model.Shape("Rectangle", 30, 40)
        score = editor_model.Shape("Line", 80, 40, action="Score")
        design.commit_shapes((cut, score), (0, 1))
        design.send_workspace.click()
        self.assertEqual(window.workspaces.currentIndex(), 0)
        self.assertEqual(window.tabs.currentIndex(), window.tab("Send"))
        self.assertEqual(len(window.job.paths), 2)
        self.assertEqual(sorted(window.group_rows), ["Cut", "Score"], "mixed actions get their own groups")
        self.assertNotIn("design", window.send_refusal(window.job, window.connection()).lower())

    def test_newly_opened_send_artwork_is_not_replaced_by_an_older_design(self):
        window = self.window
        window.open_design()
        window.design.commit_shapes((editor_model.Shape("Rectangle", 30, 40),), (0,))
        window.design.send_workspace.click()
        self.assertEqual(len(window.job.paths), 1)
        window.reset_artwork()
        generated = len(window.job.paths)
        self.assertGreater(generated, 1)
        window.open_design()
        window.design.send_workspace.click()
        self.assertEqual(len(window.job.paths), generated)

    def test_an_unusable_design_stays_in_design_with_the_reason(self):
        window = self.window
        window.open_design()
        design = window.design
        with mock.patch.object(qt_gui.editor_model, "send_handoff",
                               return_value=("invalid", "Draw or resize the paths before sending.")):
            design.send_workspace.click()
        self.assertIs(window.workspaces.currentWidget(), design)
        self.assertIn("resize the paths", design.status.text())

    def test_send_refuses_a_design_edit_it_has_not_used(self):
        window = self.window
        window.open_design()
        design = window.design
        design.commit_shapes((editor_model.Shape("Rectangle", 30, 40),), (0,))
        design.send_workspace.click()
        self.assertEqual(window.send_refusal(window.job, window.connection()), "")
        design.commit_shapes((editor_model.Shape("Ellipse", 30, 40),), (0,))  # edited without switching
        self.assertIn("has not used", window.send_refusal(window.job, window.connection()))

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
            self.assertTrue(cutter.run.call_args.kwargs["wait_done"])
            self.assertIsInstance(cutter.run.call_args.kwargs["control"], transport.CutControl)
            self.assertIsNone(window.cut_control)
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
        window.checked_marks = window.job.marks
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
                         ("Layout", "Sheet", "Cut Job"))
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
        window.select("preset", "Sample vinyl")
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
            self.assertEqual(window.guide_send.text(), "Repeat One Pass")
            confirm.assert_called_once()

            # Changed settings are a new cut on the same spot: no "Repeat", and it asks first.
            window.blade["force"].setValue(27)
            self.assertEqual(window.guide_send.text(), "Cut One Pass…")
            confirm.return_value = False
            window.guide_send.click()
            self.assertEqual(len(fake.run.call_args_list), 1, "declining sends nothing")
            title, body = confirm.call_args.args
            self.assertEqual(title, "Cut With Changed Settings?")
            self.assertIn("last cut at Force 25", body)
            self.assertIn("uses Force 27", body)
            confirm.return_value = True
            window.guide_send.click()
            self.assertEqual(len(fake.run.call_args_list), 2)
            self.assertEqual(window.guide.spot, 1)
            self.assertEqual(window.guide.completed, 2)
            self.assertEqual(window.guide.tested_settings.force, 27)
        self.assertEqual(confirm.call_count, 3)
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
        self.assertEqual((window.blade["force"].value(), window.blade["passes"].value()), (27, 2))
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
            with mock.patch.dict(os.environ, XDG_CURRENT_DESKTOP="ubuntu:GNOME"):
                self.assertEqual(cli.main(["gui", "--frontend", "qt"]), 0)
            with mock.patch.dict(os.environ, XDG_CURRENT_DESKTOP="KDE"):
                self.assertEqual(cli.main(["gui"]), 0)
        self.assertEqual(launch.call_args_list, [mock.call([]), mock.call([])])

    def test_launcher_prefers_gtk_on_gnome_and_falls_back_to_qt(self):
        gtk = mock.Mock()
        gtk.main.return_value = 0
        with mock.patch.dict(os.environ, XDG_CURRENT_DESKTOP="ubuntu:GNOME"):
            with mock.patch.object(cli, "import_module", return_value=gtk) as load:
                self.assertEqual(cli.main(["gui"]), 0)
            load.assert_called_once_with(".gui", "cutline")
            gtk.main.assert_called_once_with([])
            with mock.patch.object(cli, "import_module", side_effect=[ImportError, qt_gui]) as load:
                with mock.patch.object(qt_gui, "main", return_value=0) as launch:
                    self.assertEqual(cli.main(["gui"]), 0)
                launch.assert_called_once_with([])
            self.assertEqual(load.call_args_list, [mock.call(".gui", "cutline"), mock.call(".qt_gui", "cutline")])

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

        with mock.patch.object(window, "check_device"):
            window.detected.setCurrentIndex(window.detected.findData((1, 2)))
        self.assertTrue(window.guide_send.isEnabled())
        self.assertNotIn("select a cutter", window.guide_status.text())

    def test_material_guide_shape_and_size_controls_update_the_clickable_grid(self):
        window = self.window
        window.open_material_guide()
        small = len(window.preview.test_grid)
        window.guide_shape.setCurrentIndex(window.guide_shape.findData("card"))
        window.guide_size.setCurrentIndex(window.guide_size.findData(88.0))
        self.assertLess(len(window.preview.test_grid), small)
        self.assertEqual(window.preview.trial.sheet.shape, "card")
        self.assertEqual(window.preview.trial.sheet.size_mm, 88.0)
        number, box = max(window.preview.test_grid.items())
        self.assertTrue(window.on_preview_spot_clicked(box.x + 2, box.y + 2))
        self.assertEqual(window.guide.spot, number)
        self.assertEqual(window.preview.trial.sheet.cells[0].rect, box)

        window.guide.record(window.preview.trial)
        window.update_guide()
        window.guide_shape.setCurrentIndex(window.guide_shape.findData("ring"))
        self.assertNotEqual(window.guide.spot, number)
        self.assertEqual(window.guide.spot_rect(number), box)
        self.assertEqual(len(window.preview.trial.sheet.paths), 2)

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
        self.assertTrue(window.guide_send.isVisible())
        self.assertIn("Spot 1 · Cut Settings A", window.footer_status.text())
        self.assertFalse(window.scan_button.isVisible())
        self.assertFalse(window.corner_button.isVisible())
        self.assertTrue(window.settings_box.isVisible())
        self.assertFalse(window.cutter_box.isVisible())

        window.guide_next.click()
        self.assertFalse(window.preview.trial.sheet.extent().overlaps(first))
        self.assertEqual(window.guide_spot_selector.value(), 2)

        window.guide_spot_selector.setValue(1)
        self.assertEqual(window.guide.spot, 1)

        spot2_rect = window.guide.spot_rect(2)
        self.assertTrue(window.preview.on_spot_clicked(spot2_rect.x + 2, spot2_rect.y + 2))
        self.assertEqual(window.guide.spot, 2)
        self.assertEqual(window.guide_spot_selector.value(), 2)

        self.assertFalse(window.material_guide.isVisibleTo(window), "no way back into a test already open")
        window.back_to_send.click()
        self.assertTrue(window.material_guide.isVisibleTo(window))
        self.assertEqual(window.material_guide.text(), "Resume Material Test")
        window.material_guide.click()
        self.assertTrue(window.tuning)

        # Changing cut settings while active does not silently advance spots
        window.blade["force"].setValue(28)
        self.assertEqual(window.guide.spot, 2)

        window.back_to_send.click()
        self.assertIsNone(window.preview.trial)
        self.assertTrue(window.send_button.isVisible())
        self.assertTrue(window.scan_button.isVisible())
        self.assertTrue(window.corner_button.isVisible())
        self.assertTrue(window.cutter_box.isVisible())
        self.assertEqual(window.job.settings.force, 28)

    def test_qt_workspace_toggle_and_matched_zip_restrictions(self):
        window = self.window
        window.tabs.setCurrentIndex(window.tab("Sheet"))
        self.assertEqual(window.tabs.currentIndex(), window.tab("Sheet"))
        window.open_design()
        self.assertIsNotNone(window.design)
        window.leave_design()
        self.assertEqual(window.tabs.currentIndex(), window.tab("Sheet"))

        # Matched ZIP locked dialog
        window.bundle = mock.MagicMock()
        with mock.patch("cutline.qt_gui.QMessageBox") as msg_cls:
            box = msg_cls.return_value
            action_btn = mock.MagicMock()
            box.addButton.side_effect = lambda text, role: action_btn if text == "Open Sheet" else mock.MagicMock()
            box.clickedButton.return_value = action_btn
            window.open_design()
            box.setWindowTitle.assert_called_with("Matched Cut Paths Locked")
            self.assertEqual(window.tabs.currentIndex(), window.tab("Sheet"))

    def test_footer_actions_carry_their_roles_and_warnings_stay_readable(self):
        from cutline import qt_controls

        window = self.window
        roles = {"send_button": "primary", "review_button": "primary", "back_to_send": "secondary",
                 "scan_button": "secondary", "corner_button": "secondary",
                 "pause_cut": "secondary", "cancel_cut": "destructive"}
        for name, role in roles.items():
            widget = getattr(window, name)
            self.assertEqual(widget.property("role"), role, name)
            self.assertGreaterEqual(widget.minimumHeight(), 32, name)

        # Every role resolves against the theme, in light and in dark.
        for name, window_color, text_color in (("light", "#f6f5f4", "#1b1b1b"), ("dark", "#1e1e1e", "#f2f2f2")):
            with self.subTest(theme=name):
                palette = QPalette()
                palette.setColor(QPalette.ColorRole.Window, QColor(window_color))
                palette.setColor(QPalette.ColorRole.WindowText, QColor(text_color))
                palette.setColor(QPalette.ColorRole.Button, QColor(window_color))
                palette.setColor(QPalette.ColorRole.ButtonText, QColor(text_color))
                css = qt_controls.workspace_style(palette)
                self.assertNotIn("@", css, "a theme token was left unresolved")
                self.assertIn('QLabel[role="warning"]', css)
                self.assertIn('QPushButton[role="destructive"]:pressed', css)
                self.assertIn('QPushButton[role="secondary"]:pressed', css)

        # A refusal is marked as a warning, not left to read as ordinary status.
        window.pdf = Path("print.pdf")
        window.controls["registration"].setChecked(True)
        window.rebuild()
        window.pdf_check_state = qt_gui.sheet.RegistrationDecision.FAILED
        window.refresh_send()
        self.assertEqual(window.footer_status.property("role"), "warning")
        self.assertTrue(window.footer_status.text().startswith("⚠"))
        self.assertIn("Registration check failed", window.footer_status.text())

        window.pdf_check_state = qt_gui.sheet.RegistrationDecision.PASSED
        window.refresh_send()
        self.assertEqual(window.footer_status.property("role"), "body")
        self.assertFalse(window.footer_status.text().startswith("⚠"))

    def test_the_material_test_survives_settings_edits_and_tab_switches(self):
        window = self.window
        window.open_material_guide()
        self.assertTrue(window.tuning)
        window.guide_next.click()
        spot = window.guide.spot
        self.assertEqual(spot, 2)

        window.open_cut_settings()
        self.assertTrue(window.tuning, "editing cut settings closed the material test")
        self.assertEqual(window.tabs.currentIndex(), window.tab("Send"))
        self.assertTrue(window.settings_box.isVisibleTo(window))
        self.assertFalse(window.send_button.isVisibleTo(window))

        window.blade["force"].setValue(window.blade["force"].value() + 3)
        self.assertEqual(window.guide.spot, spot)
        self.assertTrue(window.tuning)

        window.tabs.setCurrentIndex(window.tab("Sheet"))
        window.tabs.setCurrentIndex(window.tab("Send"))
        self.assertTrue(window.tuning)
        self.assertEqual(window.guide.spot, spot)

        self.assertFalse(window.preset_actions.isVisibleTo(window), "the guide saves its own presets")
        window.open_cut_settings()
        self.assertTrue(window.tuning)
        window.open_send_workspace()
        self.assertTrue(window.tuning)

        window.back_to_send.click()
        self.assertFalse(window.tuning)
        self.assertTrue(window.send_button.isVisibleTo(window))
        self.assertEqual(window.material_guide.text(), "Resume Material Test")

    def test_every_test_spot_can_be_reached_from_the_keyboard(self):
        window = self.window
        window.open_material_guide()
        selector = window.guide_spot_selector
        self.assertTrue(selector.isEnabled())

        selector.setValue(4)
        self.assertEqual(window.guide.spot, 4)
        self.assertIn("Spot 4 · Untouched stock", window.guide_status.text())

        selector.setValue(1)
        self.assertEqual(window.guide.spot, 1)
        window.guide_next.click()
        self.assertEqual(selector.value(), window.guide.spot)

        # The selector never offers a spot the sheet does not have.
        self.assertEqual(selector.maximum(), max(window.guide.max_spots, window.guide.spot))
        with self.assertRaisesRegex(ValueError, "spots"):
            window.guide.select_spot(window.guide.max_spots + 1)

    def test_a_matched_zip_explains_itself_before_an_edit_is_attempted(self):
        window = self.window
        self.assertFalse(window.bundle_notice_bar.isVisible())
        window.bundle = mock.MagicMock()
        window.refresh_send()
        self.assertTrue(window.bundle_notice_bar.isVisibleTo(window))
        self.assertIn("locked to the printed sheet", window.bundle_notice.text())
        window.bundle = None
        window.refresh_send()
        self.assertFalse(window.bundle_notice_bar.isVisibleTo(window))

    def test_installed_tool_status_and_registration_failure_are_available_in_qt(self):
        window = self.window
        cutter = mock.MagicMock()
        cutter.__enter__.return_value = cutter
        cutter.can_read = True
        cutter.device = window.job.device
        cutter.status.return_value = "ready"
        cutter.status_and_tools.return_value = ("ready · Holder 1 AutoBlade", (2, 0))
        cutter.run.side_effect = transport.RegistrationFailed()
        with mock.patch.object(window.tasks, "run", side_effect=lambda work, done: done(work(), None)), \
             mock.patch.object(transport, "open_transport", return_value=cutter):
            window.refresh_cutter.click()
        self.assertIn("Holder 1 AutoBlade", window.connection_status.text())
        cutter.status_and_tools.assert_called_once_with()
        cutter.tool_codes.assert_not_called()

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

    def test_standalone_pdf_registration_check_gates_hardware_actions_in_qt(self):
        from cutline import sheet
        window = self.window
        window.pdf = Path("test_sheet.pdf")
        window.controls["registration"].setChecked(True)
        window.rebuild()
        self.assertTrue(window.job.registration)

        # Pending check
        window.pdf_check_state = sheet.RegistrationDecision.CHECKING
        window.tabs.setCurrentIndex(window.tab("Send"))
        window.refresh_send()
        self.assertTrue(window.send_button.isHidden())
        self.assertFalse(window.review_button.isHidden())
        self.assertFalse(window.measure_button.isEnabled())
        self.assertEqual(window.measure_button.text(), "Checking PDF Marks…")
        self.assertEqual(window.review_tab(), "Sheet")
        self.assertFalse(window.send_button.isEnabled())
        self.assertFalse(window.corner_button.isEnabled())
        self.assertFalse(window.scan_button.isEnabled())
        self.assertIn("pending", window.send_refusal(window.job, window.connection()))

        # Failed check
        window.pdf_check_state = sheet.RegistrationDecision.FAILED
        window.refresh_send()
        self.assertEqual(window.review_tab(), "Sheet")
        self.assertFalse(window.send_button.isEnabled())
        self.assertFalse(window.corner_button.isEnabled())
        self.assertFalse(window.scan_button.isEnabled())
        self.assertEqual(window.send_refusal(window.job, window.connection()),
                         "Registration check failed. Open Sheet to see the missing marks.")

        # Error check
        window.pdf_check_state = sheet.RegistrationDecision.ERROR
        window.refresh_send()
        self.assertEqual(window.review_tab(), "Sheet")
        self.assertFalse(window.send_button.isEnabled())
        self.assertFalse(window.corner_button.isEnabled())
        self.assertFalse(window.scan_button.isEnabled())
        self.assertEqual(window.send_refusal(window.job, window.connection()),
                         "Could not measure registration marks on the print file. Open Sheet to review.")

        # Passed check
        window.pdf_check_state = sheet.RegistrationDecision.PASSED
        window.refresh_send()
        self.assertEqual(window.review_tab(), "Send")
        self.assertFalse(window.send_button.isHidden())
        self.assertTrue(window.review_button.isHidden())
        self.assertTrue(window.measure_button.isEnabled())
        self.assertEqual(window.measure_button.text(), "Check PDF Marks")
        self.assertTrue(window.send_button.isEnabled())
        self.assertTrue(window.corner_button.isEnabled())
        self.assertTrue(window.scan_button.isEnabled())
        self.assertEqual(window.send_refusal(window.job, window.connection()), "")

        # Registration off: blank stock cut is allowed even if PDF check was failed
        window.pdf_check_state = sheet.RegistrationDecision.FAILED
        window.controls["registration"].setChecked(False)
        window.rebuild()
        self.assertFalse(window.job.registration)
        window.refresh_send()
        self.assertEqual(window.review_tab(), "Send")
        self.assertTrue(window.send_button.isEnabled())
        self.assertTrue(window.corner_button.isEnabled())
        # Scan only is refused when registration is off
        self.assertIn("Enable registration", window.send_refusal(window.job, window.connection(), scan_only=True))

        # No PDF open: device scan allowed when registration is on
        window.controls["registration"].setChecked(True)
        window.clear_printed_sheet()
        window.rebuild()
        self.assertIsNone(window.pdf)
        self.assertTrue(window.job.registration)
        window.refresh_send()
        self.assertEqual(window.review_tab(), "Send")
        self.assertTrue(window.send_button.isEnabled())
        self.assertTrue(window.corner_button.isEnabled())
        self.assertTrue(window.scan_button.isEnabled())

    def test_a_blank_pdf_fails_the_registration_check_and_blocks_the_send(self):
        import shutil
        import subprocess

        if not shutil.which("rsvg-convert") or not shutil.which("pdftoppm"):
            self.skipTest("needs rsvg-convert and pdftoppm")
        window = self.window
        window.controls["registration"].setChecked(True)
        window.rebuild()
        marks = window.job.marks
        blank = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{marks.sheet_w_mm}mm" '
                 f'height="{marks.sheet_h_mm}mm" viewBox="0 0 {marks.sheet_w_mm} {marks.sheet_h_mm}">'
                 f'<rect width="100%" height="100%" fill="#ffffff"/></svg>')
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        svg = Path(folder.name) / "blank.svg"
        svg.write_text(blank)
        pdf = Path(folder.name) / "blank.pdf"
        rendered = subprocess.run(["rsvg-convert", "-f", "pdf", str(svg), "-o", str(pdf)],
                                  capture_output=True, check=False)
        self.assertEqual(rendered.returncode, 0, rendered.stderr)

        window.pdf = pdf
        with mock.patch.object(window.tasks, "run", side_effect=lambda work, done: done(work(), None)):
            window.measure_sheet()
        self.assertEqual(window.pdf_check_state, qt_gui.sheet.RegistrationDecision.FAILED)
        self.assertEqual(window.send_refusal(window.job, window.connection()),
                         "Registration check failed. Open Sheet to see the missing marks.")
        self.assertFalse(window.send_button.isEnabled())
        self.assertFalse(window.scan_button.isEnabled())
        self.assertFalse(window.corner_button.isEnabled())
        self.assertEqual(window.review_tab(), "Sheet")

    def test_a_passed_check_survives_blade_edits_and_a_late_result_cannot_authorize_it(self):
        from cutline import sheet

        window = self.window
        window.pdf = Path("print.pdf")
        window.controls["registration"].setChecked(True)
        window.rebuild()
        window.checked_marks = window.job.marks
        window.pdf_check_state = sheet.RegistrationDecision.PASSED
        window.report = mock.Mock(marks=())

        window.blade["force"].setValue(window.blade["force"].value() + 1)
        window.blade["speed"].setValue(window.blade["speed"].value() + 1)
        window.blade["passes"].setValue(2)
        self.assertEqual(window.pdf_check_state, sheet.RegistrationDecision.PASSED)
        self.assertEqual(window.send_refusal(window.job, window.connection()), "")

        # Changing the mark geometry mid-check invalidates it, and the late result is dropped.
        started = []
        with mock.patch.object(window.tasks, "run", side_effect=lambda work, done: started.append(done)):
            window.measure_sheet()
        self.assertEqual(window.pdf_check_state, sheet.RegistrationDecision.CHECKING)
        window.controls["mark_inset_mm"].setValue(window.controls["mark_inset_mm"].value() + 1)
        self.assertEqual(window.pdf_check_state, sheet.RegistrationDecision.NONE)
        self.assertIsNone(window.checked_marks)
        started[0]((mock.Mock(marks=(), preview_rotation=0), [], b""), None)
        self.assertEqual(window.pdf_check_state, sheet.RegistrationDecision.NONE)
        self.assertIn("Sheet", window.send_refusal(window.job, window.connection()))
        self.assertFalse(window.send_button.isEnabled())

    def test_changing_pdf_orientation_mode_invalidates_the_check_with_unchanged_marks(self):
        window = self.window
        window.pdf = Path("print.pdf")
        window.controls["registration"].setChecked(True)
        window.rebuild()
        with mock.patch.object(window.tasks, "run"):
            window.measure_sheet()
        marks = window.checked_marks
        with mock.patch.object(qt_gui.layout_presets, "is_published", return_value=True):
            window.rebuild()
        self.assertEqual(window.job.marks, marks)
        self.assertIsNone(window.sheet_token)
        self.assertIn("Sheet", window.send_refusal(window.job, window.connection()))
        self.assertFalse(window.send_button.isEnabled())

    def test_a_refused_connection_returns_the_window_to_an_editable_state(self):
        window = self.window
        plan, connection = window.job, window.connection()
        answers = ["", "the cutter moved"]

        # The cutter is fine when the job is checked and gone by the time the link opens.
        with mock.patch.object(qt_gui.cutters, "usb_refusal",
                               side_effect=lambda *_: answers.pop(0) if answers else ""), \
             mock.patch.object(window.tasks, "run") as run, \
             mock.patch.object(window, "message") as message:
            window.start_send(plan, connection)
        run.assert_not_called()
        message.assert_called_with("the cutter moved")
        self.assertIsNone(window.active_job)
        self.assertFalse(window.busy)
        self.assertTrue(window.blade["force"].isEnabled())
        self.assertTrue(window.controls["sheet"].isEnabled())
        self.assertTrue(window.cutter_model.isEnabled())
        self.assertTrue(window.send_button.isEnabled())

    def test_a_matched_bundle_keeps_its_layout_locked_across_a_rebuild(self):
        window = self.window
        window.lock_bundle_controls(True)
        for key in window.BUNDLE_LOCKED:
            self.assertFalse(window.controls[key].isEnabled(), key)
        window.rebuild()
        window.update_enabled_fields()
        for key in window.BUNDLE_LOCKED:
            self.assertFalse(window.controls[key].isEnabled(), key)
        self.assertFalse(window.skip.isEnabled())
        window.lock_bundle_controls(False)
        self.assertTrue(window.controls["pattern"].isEnabled())
        self.assertTrue(window.controls["mark_inset_mm"].isEnabled())
        self.assertTrue(window.skip.isEnabled())

    def test_a_running_job_locks_the_material_and_its_library(self):
        window = self.window
        with TemporaryDirectory() as folder, \
             mock.patch.object(presets, "user_dir", return_value=Path(folder)):
            custom = presets.save_blade_settings(presets.STARTING_NAME, force=18, speed=10, depth=2,
                                                 passes=1, new_name="Locked Qt Test")
            window.refresh_preset_choices()
            window.load_materials()
            window.select("preset", "Locked Qt Test")
            window.update_enabled_fields()
            idle = [b.isEnabled() for b in window.material_buttons()]
            self.assertTrue(all(idle), "idle: every material action is available")
            self.assertTrue(window.controls["preset"].isEnabled())

            window.active_job = window.job
            window.update_enabled_fields()
            self.assertFalse(window.controls["preset"].isEnabled())
            self.assertFalse(any(b.isEnabled() for b in window.material_buttons()))
            with mock.patch.object(window, "message") as message, \
                 mock.patch.object(window, "load_preset") as load, \
                 mock.patch.object(qt_gui.QMessageBox, "question",
                                   return_value=qt_gui.QMessageBox.StandardButton.Yes):
                window.delete_selected_preset()
                window.save_preset()
                window.restore_materials()
            load.assert_not_called()
            self.assertTrue(custom.exists(), "a running job's material is not deleted")
            self.assertEqual(message.call_count, 3)
            self.assertIn("running", message.call_args.args[0])

            window.active_job = None
            window.update_enabled_fields()
            self.assertTrue(all(b.isEnabled() for b in window.material_buttons()), "unlocked again")

    def test_the_holder_tabs_name_the_detected_tool_and_depth_follows_it(self):
        window = self.window
        picker = window.blade["holder"].holders
        window.blade["depth"].setValue(7)

        window.apply_tool_detection((2, 0))
        self.assertEqual(picker.button(1).text(), "Tool Holder 1\nAutoBlade")
        self.assertEqual(picker.button(2).text(), "Tool Holder 2\nEmpty")
        self.assertEqual(window.blade["depth"].value(), 7)

        with mock.patch.object(window, "message") as message:
            window.apply_tool_detection((1, 7))
        self.assertEqual(picker.button(1).text(), "Tool Holder 1\nRatchet blade")
        self.assertEqual(picker.button(2).text(), "Tool Holder 2\nPen")
        self.assertEqual(window.blade["depth"].value(), -1)
        self.assertIn("Ratchet blade", message.call_args.args[0])

        # The same reply again does not fight an operator who sets depth back.
        window.blade["depth"].setValue(5)
        with mock.patch.object(window, "message") as message:
            window.apply_tool_detection((1, 7))
        message.assert_not_called()
        self.assertEqual(window.blade["depth"].value(), 5)

    def test_an_unread_holder_is_not_reported_as_empty(self):
        window = self.window
        picker = window.blade["holder"].holders
        window.apply_tool_detection((2, 0))
        window.detected_tools = ()
        window.update_holder_labels()
        self.assertEqual(picker.button(1).text(), "Tool Holder 1")
        self.assertEqual(picker.button(2).text(), "Tool Holder 2")

    def test_manual_depth_is_applied_when_returning_to_holder_one(self):
        window = self.window
        window.blade["depth"].setValue(7)
        window.blade["holder"].setValue(2)
        window.apply_tool_detection((1, 7))
        self.assertEqual(window.blade["holder"].value(), 2)
        self.assertIsNone(window.corrected_tools)
        window.blade["holder"].setValue(1)
        self.assertEqual(window.blade["depth"].value(), -1)

    def test_blade_picker_can_override_failed_detection_and_allows_rotary_in_holder_one(self):
        window = self.window
        picker = window.tool_picker
        holders = window.blade["holder"]
        self.assertEqual(picker.currentText(), "Detect automatically")
        for name in ("AutoBlade", "Ratchet blade", "Manual blade (1 mm)", "Manual blade (2 mm)",
                     "Deep-cut blade", "Fabric blade", "Premium blade", "Kraft blade (2 mm)",
                     "Kraft blade (3 mm)", "Rotary blade", "Other manual blade"):
            self.assertGreaterEqual(picker.findText(name), 0)
        window.apply_tool_detection((2, 255))
        # Select rotary for holder 1
        picker.setCurrentIndex(picker.findData("rotary"))
        window.apply_tool_detection((2, 255))
        self.assertEqual(holders.value(), 1)
        self.assertEqual(holders.holders.button(1).text(), "Tool Holder 1\nRotary blade")
        self.assertFalse(window.blade["depth"].isEnabled())
        self.assertEqual(window.job.blocked, [])
        self.assertEqual(window.job.settings.holder, 1)
        self.assertNotIn(b"TF", window.job.stream)

        # Select rotary for holder 2
        holders.setValue(2)
        picker.setCurrentIndex(picker.findData("rotary"))
        self.assertEqual(holders.value(), 2)
        self.assertEqual(holders.holders.button(2).text(), "Tool Holder 2\nRotary blade")
        self.assertEqual(window.job.settings.holder, 2)
        self.assertIn(b"J2\x03", window.job.stream)
        self.assertNotIn(b"TF", window.job.stream)

        # Clear holder 2
        picker.setCurrentIndex(picker.findData(""))
        self.assertIn("No tool detected", holders.holders.button(2).text())

        # Switch back to holder 1: manual rotary is still selected
        holders.setValue(1)
        self.assertEqual(picker.currentData(), "rotary")
        picker.setCurrentIndex(picker.findData(""))
        self.assertEqual(picker.currentText(), "Detect automatically")
        self.assertTrue(window.blade["depth"].isEnabled())

    def test_tools_are_read_once_per_connection_and_not_during_a_job(self):
        window = self.window
        cutter = mock.MagicMock()
        cutter.__enter__.return_value = cutter
        cutter.tool_codes.return_value = (2, 0)
        with mock.patch.object(window.tasks, "run", side_effect=lambda work, done: done(work(), None)), \
             mock.patch.object(window, "open_link", return_value=cutter):
            window.detect_tools()
            self.assertEqual(window.detected_tools, (2, 0))
            window.detect_tools()
            self.assertEqual(cutter.tool_codes.call_count, 1)

            window.active_job = window.job
            window.tools_connection = None
            window.detect_tools()
            self.assertEqual(cutter.tool_codes.call_count, 1)

            window.active_job = None
            window.sending = True
            window.detect_tools()
            self.assertEqual(cutter.tool_codes.call_count, 1)
            window.sending = False

    def test_a_cutter_that_cannot_report_its_tools_is_never_queried(self):
        window = self.window
        window.select("cutter", "Silhouette Portrait 2")
        self.assertFalse(qt_gui.graphtec.device_by_name("Silhouette Portrait 2").detects_tools)
        with mock.patch.object(window, "open_link") as link:
            window.detect_tools()
        link.assert_not_called()
        self.assertEqual(window.detected_tools, ())

    def test_tool_probe_prevents_overlapping_commands_and_releases_after_failure(self):
        window = self.window
        with mock.patch.object(window.tasks, "run") as run:
            window.detect_tools()
            window.detect_tools()
            window.check_device()
            run.assert_called_once()
            self.assertFalse(window.refresh_cutter.isEnabled())
            self.assertFalse(window.send_button.isEnabled())
            run.call_args.args[1](None, OSError("gone"))
        self.assertTrue(window.refresh_cutter.isEnabled())
        self.assertTrue(window.blade["force"].isEnabled())
        self.assertFalse(window.busy)

    def test_active_and_paused_job_details_and_control_states_in_qt(self):
        window = self.window
        window.blade["force"].setValue(25)
        window.rebuild()
        original_job = window.job
        self.assertEqual(original_job.settings.force, 25)

        control = transport.CutControl(lambda: None)
        window.cut_control = control
        window.active_job = original_job
        window.sending = True
        window.update_enabled_fields()
        window.refresh_send()

        # Job controls are disabled while active
        self.assertFalse(window.open_artwork_action.isEnabled())
        self.assertFalse(window.open_print_button.isEnabled())
        self.assertFalse(window.blade["holder"].holders.button(2).isEnabled())
        self.assertFalse(window.blade["force"].isEnabled())
        self.assertFalse(window.blade["speed"].isEnabled())
        self.assertFalse(window.controls["sheet"].isEnabled())
        self.assertFalse(window.cutter_model.isEnabled())

        # Running state
        self.assertEqual(control.state, "running")
        self.assertEqual(window.pause_cut.text(), "Pause Cut")
        self.assertTrue(window.pause_cut.isEnabled())
        self.assertEqual(window.cancel_cut.text(), "Cancel Cut")
        self.assertTrue(window.cancel_cut.isEnabled())
        self.assertIn("Sending to the cutter", window.footer_status.text())

        # Pausing state
        control.toggle_pause()
        self.assertEqual(control.state, "pausing")
        window.refresh_send()
        self.assertEqual(window.pause_cut.text(), "Pausing…")
        self.assertTrue(window.pause_cut.isEnabled())
        self.assertEqual(window.cancel_cut.text(), "Cancel Cut")
        self.assertTrue(window.cancel_cut.isEnabled())
        self.assertIn("Pausing", window.footer_status.text())

        # Paused state
        with control._condition:
            control._set_state("paused")
        window.refresh_send()
        self.assertEqual(control.state, "paused")
        self.assertEqual(window.pause_cut.text(), "Resume Cut")
        self.assertTrue(window.pause_cut.isEnabled())
        self.assertEqual(window.cancel_cut.text(), "Cancel Cut")
        self.assertTrue(window.cancel_cut.isEnabled())
        self.assertIn("Cut paused", window.footer_status.text())

        # Calling rebuild while paused does NOT change the active job or rebuild preview
        window.blade["force"].setValue(10)
        window.rebuild()
        self.assertIs(window.active_job, original_job)
        self.assertEqual(window.active_job.settings.force, 25)

        # Cancelling state
        control.cancel()
        self.assertEqual(control.state, "cancelling")
        window.refresh_send()
        self.assertEqual(window.cancel_cut.text(), "Stopping…")
        self.assertFalse(window.cancel_cut.isEnabled())
        self.assertFalse(window.pause_cut.isEnabled())
        self.assertIn("Stopping", window.footer_status.text())

        # Finishing state
        control.finishing()
        self.assertEqual(control.state, "finishing")
        window.refresh_send()
        self.assertFalse(window.cancel_cut.isEnabled())
        self.assertFalse(window.pause_cut.isEnabled())
        self.assertIn("Waiting for the cutter", window.footer_status.text())

        # Terminal state reset restores editable controls
        window.cut_control = None
        window.active_job = None
        window.sending = False
        window.update_enabled_fields()
        window.rebuild()
        window.refresh_send()
        self.assertTrue(window.blade["force"].isEnabled())
        self.assertTrue(window.blade["speed"].isEnabled())
        self.assertTrue(window.controls["sheet"].isEnabled())
        self.assertTrue(window.cutter_model.isEnabled())
        self.assertFalse(window.pause_cut.isVisible())
        self.assertFalse(window.cancel_cut.isVisible())


@unittest.skipIf(qt_gui is None, "Install PySide6 to run Qt frontend tests")
class TestQtDesignEditor(unittest.TestCase):
    """Drive the Qt design editor itself, offscreen."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = QApplication.instance() or QApplication([])

    def editor(self, project=None):
        from cutline import qt_design

        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        patcher = mock.patch.object(qt_gui.config, "CONFIG_DIR", Path(folder.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        project = project or editor_model.Project(279.4, 215.9, ())
        device = qt_gui.graphtec.device_by_name("Silhouette Cameo 4")
        design = qt_design.Design(project, device, lambda *_: None, lambda *_: None)
        self.addCleanup(design.deleteLater)
        return design

    def drag(self, canvas, start, end, modifiers=None):
        if modifiers is None:
            modifiers = Qt.KeyboardModifier.NoModifier
        start, end = canvas.mapFromScene(QPointF(*start)), canvas.mapFromScene(QPointF(*end))
        QTest.mousePress(canvas.viewport(), Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(canvas.viewport(), end)
        QTest.mouseRelease(canvas.viewport(), Qt.MouseButton.LeftButton, modifiers, end)

    def click(self, canvas, point):
        QTest.mouseClick(canvas.viewport(), Qt.MouseButton.LeftButton, pos=canvas.mapFromScene(QPointF(*point)))

    def test_workspace_context_geometry_visibility_and_order_keep_project_history(self):
        shapes = (editor_model.Shape("Rectangle", 20, 20, 25, 15),
                  editor_model.Shape("Ellipse", 70, 35, 20, 10))
        design = self.editor(editor_model.Project(216, 279, shapes))
        design.resize(720, 520)
        design.show()
        self.app.processEvents()
        self.assertGreater(design.context_fields["x"].lineEdit().width(), 10)
        design.context_fields["x"].setValue(28)
        self.assertEqual(design.project.shapes[0].x, 28)
        self.assertEqual(design.fields["x"].value(), 28)
        design.objects.item(0).setCheckState(Qt.CheckState.Unchecked)
        self.assertFalse(design.project.shapes[0].visible)
        self.assertEqual(len(design.project.artwork().paths), 1)
        design.reorder_objects(2)
        self.assertEqual([shape.kind for shape in design.project.shapes], ["Ellipse", "Rectangle"])
        design.undo()
        self.assertEqual([shape.kind for shape in design.project.shapes], ["Rectangle", "Ellipse"])
        design.dock.disclosure.setChecked(False)
        self.assertFalse(design.dock.pages.isVisible())
        self.assertTrue(design.objects.isVisible())
        design.dock.open("transform")
        self.assertTrue(design.dock.pages.isVisible())

    def test_design_ribbon_fits_and_preserves_selection_during_resize(self):
        from PySide6.QtGui import QFont

        shapes = (editor_model.Shape("Rectangle", 20, 20, 20, 10, group=1),
                  editor_model.Shape("Rectangle", 50, 30, 20, 10, group=1),
                  editor_model.Shape("Ellipse", 80, 40, 20, 15))
        design = self.editor()
        design.setFont(QFont(design.font().family(), 14))
        title = "A long file name " * 20 + ".scc-template"
        design.replace_document(editor_model.Project(216, 279, shapes), source=title)
        design.resize(1280, 800)
        design.show()
        design.select((0, 1, 2))
        self.app.processEvents()
        self.assertTrue(design.group_ribbon_button.isVisible())
        design.group_ribbon_button.setFocus()
        design.resize(720, 520)
        self.app.processEvents()
        self.assertEqual(design.width(), 720)
        self.assertTrue(design.adaptive_selection.overflow.isVisible())
        self.assertTrue(design.adaptive_selection.overflow.hasFocus())
        self.assertEqual(design.document_title.accessibleName(), title)
        self.assertTrue(design.import_button.isVisible() and design.add_shape_button.isVisible())
        self.assertTrue(design.objects.isVisible())
        self.assertEqual(design.selection(), (0, 1, 2))
        design.tool.setCurrentText("Point Edit")
        self.app.processEvents()
        self.assertTrue(design.adaptive_points.overflow.isVisible())

    def test_design_inspector_switches_between_named_panels(self):
        shapes = (editor_model.Shape("Rectangle", 20, 20, 60, 40),
                  editor_model.Shape("Ellipse", 100, 30, 40, 40),
                  editor_model.Shape("Rounded rectangle", 40, 90, 30, 30, radius=4))
        design = self.editor(editor_model.Project(279.4, 215.9, shapes))
        design.resize(1100, 720)
        design.show()
        design.select(())
        self.app.processEvents()
        dock = design.dock
        self.assertEqual(list(dock.panels), ["page", "transform", "align", "replicate", "modify", "offset",
                                             "trace", "nesting", "text", "line", "grid"])
        self.assertEqual(dock.shown(), ["transform"])
        self.assertTrue(design.objects.isVisibleTo(design), "Objects stays visible alongside the inspector")
        dock.picker.setCurrentIndex(dock.picker.findData("replicate"))
        self.assertEqual(dock.shown(), ["replicate"])
        self.assertTrue(dock.panels["replicate"].isVisibleTo(design))
        self.assertTrue(design.objects.isVisibleTo(design))
        dock.open("transform")
        self.assertEqual(dock.picker.currentText(), "Transform")
        self.assertTrue(dock.panels["transform"].body.isVisibleTo(design))
        self.assertFalse(dock.panels["replicate"].isVisibleTo(design))

        dock.open("line")
        design.select((1,))
        self.assertEqual(dock.shown(), ["line"], "selecting a shape keeps the user's chosen inspector panel")
        dock.open("transform")

        # Controls follow the selection by enabling, not by moving.
        design.select(())
        self.assertFalse(design.action.isEnabled())
        self.assertFalse(design.fields["x"].isEnabled())
        design.select((2,))
        self.assertTrue(design.action.isEnabled())
        self.assertTrue(design.fields["radius"].isEnabled())
        self.assertFalse(design.alignment_buttons["Align Left"].isEnabled(), "align needs two objects")
        design.select((0,))
        self.assertFalse(design.fields["radius"].isEnabled(), "only rounded rectangles have a radius")

        # Several objects edit as one box instead of showing stale values.
        design.select((0, 1))
        self.assertTrue(design.alignment_buttons["Align Left"].isEnabled())
        self.assertEqual([design.fields[key].value() for key in ("x", "y", "width", "height")], [20, 20, 120, 50])
        self.assertFalse(design.fields["rotation"].isEnabled())
        design.fields["x"].setValue(30)
        self.assertEqual([shape.x for shape in design.project.shapes[:2]], [30, 110])
        design.fields["width"].setValue(240)
        scaled = design.project.shapes[:2]
        self.assertEqual((scaled[0].x, scaled[0].y), (30, 20))
        self.assertAlmostEqual(scaled[0].width, 120)
        self.assertAlmostEqual(scaled[1].height, 80)
        self.assertEqual(design.project.shapes[2], shapes[2])

        # The page's mark geometry shows only while registration is on.
        dock.open("page")
        design.register.setChecked(False)
        self.assertFalse(design.inset.isVisibleTo(design))
        design.register.setChecked(True)
        self.assertTrue(design.inset.isVisibleTo(design))

    def test_offset_and_modify_work_from_their_panels(self):
        shapes = (editor_model.Shape("Rectangle", 20, 20, 40, 40), editor_model.Shape("Rectangle", 40, 40, 40, 40))
        design = self.editor(editor_model.Project(279.4, 215.9, shapes))
        design.select(())
        self.assertFalse(design.offset_button.isEnabled())
        self.assertFalse(design.modify_buttons["Weld"].isEnabled())
        design.select((0,))
        design.offset_distance.setValue(2)
        design.offset_button.click()
        self.assertEqual(len(design.project.shapes), 3)
        self.assertAlmostEqual(design.project.shapes[2].width, 44, delta=0.01)
        design.undo()
        design.select((0, 1))
        design.modify_buttons["Weld"].click()
        self.assertEqual(len(design.project.shapes), 1)

    def test_view_options_live_in_the_view_menu_and_redraw(self):
        design = self.editor()
        menus = {action.text().replace("&", ""): action.menu() for action in design.menu_bar.actions()}
        show = next(action.menu() for action in menus["View"].actions() if action.text() == "Show")
        self.assertEqual([action.text() for action in show.actions()],
                         ["Paper Grid", "Mat Grid", "Rulers", "Crosshairs", "Cutting Mat", "Cut Border", "Print Border"])
        self.assertIn(design.snap, menus["View"].actions())
        with mock.patch.object(design.canvas, "redraw") as redraw:
            design.show_grid.setChecked(True)
        self.assertEqual(redraw.call_count, 1)
        self.assertTrue(design.grid_check.isChecked(), "the Grid panel follows View → Show → Paper Grid")
        grid = next(action for action in menus["View"].actions() if action.text() == "Grid…")
        grid.trigger()
        self.assertIn("grid", design.dock.shown())
        design.spacing.setValue(10)
        design.snap.setChecked(True)
        self.assertEqual(design.canvas.point(QPointF(13, 26)), (10, 30), "snapping uses the panel's spacing")
        self.assertTrue(design.snap_check.isChecked())
        design.snap.setChecked(False)
        design.option_snap.click()
        self.assertTrue(design.snap.isChecked())
        design.snap.setChecked(False)
        self.assertFalse(design.option_snap.isChecked())

    def test_photoshop_toolbox_groups_tools_behind_corner_marks(self):
        design = self.editor()
        design.resize(720, 520)
        design.show()
        self.app.processEvents()
        palette = design.tool
        self.assertEqual(list(palette.slots), ["Select", "Point Edit", "Shape tools", "Path tools", "Type",
                                               "Cut tools", "Sample tools", "View tools"])
        self.assertEqual([title for title, button in palette.slots.items() if button.flyout],
                         ["Shape tools", "Path tools", "Cut tools", "Sample tools", "View tools"])
        rounded = palette.button_for("Rounded rectangle")
        self.assertEqual(rounded.accessibleName(), "Rectangle")
        QTest.keyClick(design.canvas, Qt.Key.Key_U)
        self.assertEqual(rounded.accessibleName(), "Rounded rectangle")
        self.assertIn("(U)", rounded.toolTip())
        self.assertTrue(rounded.isChecked())
        self.assertEqual(palette.active_label.text(), "Rounded rectangle")
        self.assertTrue(design.radius_option.isVisibleTo(design))
        QTest.keyClick(design.canvas, Qt.Key.Key_V)
        self.assertEqual(palette.currentData(), "Select")
        self.assertFalse(design.draw_options.isVisibleTo(design))
        QTest.keyClick(design.canvas, Qt.Key.Key_T)
        self.assertEqual(palette.currentData(), "Type")

        palette.show_group["Path tools"]()
        menu = palette.menus["Path tools"]
        self.assertTrue(menu.isVisible())
        self.assertEqual([action.text().split("\t")[0] for action in menu.actions()],
                         ["Polygon", "Curve", "Arc", "Freehand", "Smooth Freehand"])
        menu.close()
        palette.choices["Curve"].trigger()
        self.assertTrue(palette.slots["Path tools"].isChecked())
        self.assertEqual(palette.slots["Path tools"].accessibleName(), "Curve")
        self.assertTrue(design.close_polygon.isVisibleTo(design))
        palette.setCurrentText("Select")
        palette.slots["Path tools"].click()
        self.assertEqual(palette.currentData(), "Curve", "a group remembers its last tool")
        self.assertTrue(design.canvas.hasFocus())

        canvas = design.canvas.mapTo(design, QPoint())
        dock = design.dock.mapTo(design, QPoint())
        self.assertLess(palette.mapTo(design, QPoint()).x(), canvas.x())
        self.assertLess(canvas.x(), dock.x())
        self.assertEqual(design.dock.shown(), ["page"])
        self.assertFalse(design.fields["x"].isVisibleTo(design))
        self.assertTrue(design.tools_bar.isVisibleTo(design))
        self.assertTrue(design.import_button.isVisibleTo(design))
        self.assertTrue(design.add_shape_button.isVisibleTo(design))
        self.assertTrue(design.undo_button.isVisibleTo(design))
        self.assertFalse(design.selection_tools.isVisibleTo(design))
        palette.setCurrentText("Select")
        self.assertIn("Blank page", design.tool_hint.text())

        design.commit_shapes((editor_model.Shape("Rectangle", 20, 20, 40, 30),
                              editor_model.Shape("Ellipse", 80, 20, 30, 30)), (0, 1))
        self.assertTrue(design.selection_tools.isVisibleTo(design))
        self.assertTrue(design.group_ribbon_button.isVisibleTo(design))
        self.assertTrue(design.align_button.isVisibleTo(design))
        self.assertEqual(design.save_state.text(), "Unsaved")
        self.assertEqual(design.dock.shown(), ["transform"])

    def test_new_shapes_take_the_options_bar_settings(self):
        design = self.editor()
        design.resize(900, 640)
        design.show()
        self.app.processEvents()
        design.tool.setCurrentText("Rounded rectangle")
        design.new_action.setCurrentIndex(design.new_action.findData("Score"))
        design.new_radius.setValue(6)
        self.drag(design.canvas, (30, 30), (80, 70))
        shape, = design.project.shapes
        self.assertEqual((shape.kind, shape.action, shape.radius), ("Rounded rectangle", "Score", 6))
        design.tool.setCurrentText("Ellipse")
        design.new_action.setCurrentIndex(design.new_action.findData("Cut"))
        self.drag(design.canvas, (100, 30), (130, 60))
        self.assertEqual(design.project.shapes[1].action, "Cut")

    def test_select_scales_from_a_corner_and_rotates_with_the_grip(self):
        shape = editor_model.Shape("Rounded rectangle", 50, 60, 20, 30, radius=3)
        design = self.editor(editor_model.Project(279.4, 215.9, (shape,)))
        design.resize(900, 640)
        design.show()
        self.app.processEvents()
        palette, canvas = design.tool, design.canvas
        self.assertEqual(palette.currentData(), "Select")
        design.select((0,))

        start = canvas.mapFromScene(QPointF(70, 90))
        end = canvas.mapFromScene(QPointF(90, 120))
        QTest.mousePress(canvas.viewport(), Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(canvas.viewport(), end)
        self.assertEqual(design.history.current.shapes, (shape,))
        self.assertGreater(design.project.shapes[0].width, shape.width)
        QTest.mouseRelease(canvas.viewport(), Qt.MouseButton.LeftButton, pos=end)
        scaled = design.project.shapes[0]
        self.assertEqual((scaled.x, scaled.y), (shape.x, shape.y))
        self.assertAlmostEqual(scaled.width, 40, delta=1)
        self.assertAlmostEqual(scaled.height, 60, delta=1)
        self.assertAlmostEqual(scaled.radius, 6, delta=0.2)
        self.assertEqual(len(design.history.past), 1)
        design.undo()
        self.assertEqual(design.project.shapes, (shape,))

        # Dragging inside still moves, as with Photoshop's Move tool.
        self.drag(canvas, (60, 75), (70, 85))
        moved = design.project.shapes[0]
        self.assertAlmostEqual(moved.x, 60, delta=0.5)
        self.assertAlmostEqual(moved.y, 70, delta=0.5)
        self.assertEqual((moved.width, moved.height), (shape.width, shape.height))
        design.undo()

        handle = canvas.rotation_handle()
        grip = handle.positions()[1]
        turned = editor_model.rotate_point(grip, handle.center, 88)
        self.drag(canvas, grip, turned, Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(design.project.shapes[0].rotation, 90)
        design.undo()
        self.assertEqual(design.project.shapes, (shape,))
        QTest.keyClick(canvas, Qt.Key.Key_F9)
        self.assertFalse(design.dock.isVisibleTo(design))
        self.assertTrue(palette.isHidden())
        QTest.keyClick(canvas, Qt.Key.Key_F9)
        self.assertTrue(design.dock.isVisibleTo(design))

    def test_select_rotates_several_objects_and_escape_cancels(self):
        shapes = (editor_model.Shape("Rectangle", 50, 60, 20, 20),
                  editor_model.Shape("Rectangle", 90, 80, 20, 20))
        design = self.editor(editor_model.Project(279.4, 215.9, shapes))
        design.resize(900, 640)
        design.show()
        self.app.processEvents()
        canvas = design.canvas
        design.select((0,))
        self.assertFalse(design.alignment_buttons["Align Left"].isEnabled())
        design.select((0, 1))
        self.assertTrue(design.alignment_buttons["Align Left"].isEnabled())
        self.assertFalse(design.alignment_buttons["Space Horizontally"].isEnabled())
        handle = canvas.rotation_handle()
        grip = handle.positions()[1]
        end = editor_model.rotate_point(grip, handle.center, 42)
        start_view = canvas.mapFromScene(QPointF(*grip))
        end_view = canvas.mapFromScene(QPointF(*end))

        QTest.mousePress(canvas.viewport(), Qt.MouseButton.LeftButton, pos=start_view)
        QTest.mouseMove(canvas.viewport(), end_view)
        self.assertNotEqual(design.project.shapes, shapes)
        self.assertEqual(design.history.current.shapes, shapes)
        QTest.keyClick(canvas, Qt.Key.Key_Escape)
        QTest.mouseRelease(canvas.viewport(), Qt.MouseButton.LeftButton, pos=end_view)
        self.assertEqual(design.selection(), (0, 1))
        self.assertEqual(design.project.shapes, shapes)

        QTest.mousePress(canvas.viewport(), Qt.MouseButton.LeftButton, pos=start_view)
        QTest.mouseMove(canvas.viewport(), end_view)
        QTest.mouseRelease(canvas.viewport(), Qt.MouseButton.LeftButton, pos=start_view)
        self.assertEqual(design.project.shapes, shapes)
        self.assertFalse(design.history.past)

        QTest.mousePress(canvas.viewport(), Qt.MouseButton.LeftButton, pos=start_view)
        QTest.mouseRelease(canvas.viewport(), Qt.MouseButton.LeftButton,
                           Qt.KeyboardModifier.ShiftModifier, end_view)
        self.assertEqual([shape.rotation for shape in design.project.shapes], [45, 45])
        self.assertEqual(len(design.history.past), 1)
        self.assertAlmostEqual(sum(shape.x for shape in design.project.shapes) / 2, 70)
        self.assertAlmostEqual(sum(shape.y for shape in design.project.shapes) / 2, 70)
        design.undo()
        self.assertEqual(design.project.shapes, shapes)
        design.transform_buttons["left"].click()
        self.assertEqual([shape.rotation for shape in design.project.shapes], [270, 270])
        design.undo()
        design.alignment_buttons["Align Left"].click()
        self.assertEqual([shape.x for shape in design.project.shapes], [50, 50])

    def test_curve_tool_traces_a_smooth_closed_outline_through_each_click(self):
        design = self.editor()
        design.resize(900, 640)
        design.show()
        self.app.processEvents()
        canvas = design.canvas
        QTest.keyClick(canvas, Qt.Key.Key_C)
        self.assertEqual(design.tool.currentData(), "Curve")
        clicks = ((40, 40), (90, 35), (100, 80), (45, 90))
        for point in clicks:
            self.click(canvas, point)
        self.assertFalse(design.project.shapes, "nothing is added until the outline is finished")
        self.click(canvas, clicks[0])
        outline, = design.project.shapes
        self.assertEqual(outline.kind, "Path")
        self.assertTrue(outline.closed)
        points = outline.paths()[0].points
        self.assertGreater(len(points), len(clicks), "a curve, not a polygon")
        self.assertEqual(len(outline.points), len(clicks), "the clicks stay editable points")
        for click in clicks:
            # Each click lands within a screen pixel of where it was aimed.
            self.assertLess(min(math.dist(click, point) for point in points), 0.5)

        # Studio: a double-click stops drawing and leaves the path open.
        for point in ((150, 40), (170, 60), (190, 40)):
            self.click(canvas, point)
        QTest.mouseDClick(canvas.viewport(), Qt.MouseButton.LeftButton, pos=canvas.mapFromScene(QPointF(190, 40)))
        opened = design.project.shapes[1]
        self.assertFalse(opened.closed)
        self.assertEqual(len(opened.points), 3)

        # Closing needs three points, and says so.
        self.click(canvas, (150, 140))
        self.click(canvas, (170, 140))
        design.close_polygon.click()
        self.assertEqual(len(design.project.shapes), 2)
        self.assertIn("three or more points", design.status.text())

    def test_keyboard_point_edit_nudges_one_node_and_supports_undo(self):
        from cutline import path_edit

        shape = editor_model.Shape("Ellipse", 20, 25, 30, 20)
        design = self.editor(editor_model.Project(216, 279, (shape,)))
        design.show()
        design.tool.setCurrentText("Point Edit")
        canvas = design.canvas
        before = path_edit.nodes(shape)
        QTest.keyClick(canvas, Qt.Key.Key_Right)
        self.assertEqual(design.project.shapes, (shape,))
        QTest.keyClick(canvas, Qt.Key.Key_BracketLeft)
        self.assertEqual(canvas.node_point, len(before.points) - 1)
        QTest.keyClick(canvas, Qt.Key.Key_BracketRight)
        QTest.keyClick(canvas, Qt.Key.Key_Right)
        after = path_edit.nodes(design.project.shapes[0])
        self.assertEqual(after.points[0], (before.points[0][0] + 1, before.points[0][1]))
        self.assertEqual(after.points[1:], before.points[1:])
        self.assertEqual(len(design.history.past), 1)
        design.undo()
        self.assertEqual(design.project.shapes, (shape,))

    def test_point_editing_works_as_in_studio(self):
        square = editor_model.Shape("Rectangle", 40, 40, 60, 60, action="Score")
        design = self.editor(editor_model.Project(279.4, 215.9, (square,)))
        design.resize(900, 640)
        design.show()
        self.app.processEvents()
        canvas = design.canvas
        # Double-clicking a shape enters Point Editing.
        QTest.mouseDClick(canvas.viewport(), Qt.MouseButton.LeftButton, pos=canvas.mapFromScene(QPointF(70, 40)))
        self.assertEqual(design.tool.currentData(), "Point Edit")
        self.assertTrue(design.point_options.isVisibleTo(design) or design.adaptive_points.overflow.isVisible())
        # Clicking the outline adds a point; clicking inside does not.
        self.click(canvas, (70, 70))
        self.assertEqual(design.project.shapes[0].kind, "Rectangle")
        design.select((0,))
        self.click(canvas, (70, 40.4))
        path = design.project.shapes[0]
        self.assertEqual((path.kind, len(path.points), path.action), ("Path", 5, "Score"))
        self.assertEqual(canvas.node_point, 1)
        added = path_edit.nodes(path).points[1]
        self.assertAlmostEqual(added[0], 70, delta=0.5)
        self.assertEqual(added[1], 40, "the new point lies on the edge, not where the pointer was")
        # Make Curve, then drag its handle out: the top edge bows.
        design.point_buttons["Make Curve"].click()
        nodes = path_edit.nodes(design.project.shapes[0])
        handle = nodes.handles[1][1]
        self.drag(canvas, handle, (handle[0], handle[1] - 15))
        top = [p for p in design.project.shapes[0].paths()[0].points if 72 < p[0] < 98]
        self.assertTrue(any(p[1] < 36 for p in top))
        # Delete removes the chosen point, not the object.
        canvas.node_point = 1
        QTest.keyClick(canvas, Qt.Key.Key_Delete)
        self.assertEqual(len(design.project.shapes), 1)
        self.assertEqual(len(design.project.shapes[0].points), 4)
        # Break Path opens it; dragging one end onto the other closes it again.
        canvas.node_point = 0
        design.point_buttons["Break Path"].click()
        opened = design.project.shapes[0]
        self.assertFalse(opened.closed)
        ends = path_edit.nodes(opened).points
        far = (ends[-1][0] + 12, ends[-1][1] + 12)
        self.drag(canvas, ends[-1], far)
        self.assertFalse(design.project.shapes[0].closed)
        self.drag(canvas, far, ends[0])
        self.assertTrue(design.project.shapes[0].closed)
        # Double-clicking empty canvas leaves Point Editing.
        QTest.mouseDClick(canvas.viewport(), Qt.MouseButton.LeftButton, pos=canvas.mapFromScene(QPointF(200, 180)))
        self.assertEqual(design.tool.currentData(), "Select")

    def test_drawing_tools_and_their_modifier_keys(self):
        design = self.editor()
        design.resize(900, 640)
        design.show()
        self.app.processEvents()
        canvas = design.canvas
        shift, alt = Qt.KeyboardModifier.ShiftModifier, Qt.KeyboardModifier.AltModifier
        design.tool.setCurrentText("Rectangle")
        self.drag(canvas, (20, 20), (60, 35), shift)
        square = design.project.shapes[-1]
        self.assertAlmostEqual(square.width, square.height, delta=0.5, msg="Shift draws a square")
        design.tool.setCurrentText("Ellipse")
        self.drag(canvas, (100, 100), (120, 110), alt)
        ellipse = design.project.shapes[-1]
        self.assertAlmostEqual(ellipse.x + ellipse.width / 2, 100, delta=0.5, msg="Alt draws from the center")
        design.tool.setCurrentText("Line")
        self.drag(canvas, (20, 150), (60, 162), shift)
        line = design.project.shapes[-1]
        self.assertAlmostEqual(line.height, 0, delta=0.5, msg="Shift keeps a line at 45° steps")
        design.tool.setCurrentText("Regular Polygon")
        self.assertTrue(design.sides_option.isVisibleTo(design))
        design.sides.setValue(6)
        self.drag(canvas, (150, 20), (190, 60))
        hexagon = design.project.shapes[-1]
        self.assertEqual((hexagon.kind, len(hexagon.points), hexagon.closed), ("Path", 6, True))
        design.tool.setCurrentText("Arc")
        self.drag(canvas, (200, 100), (240, 140))
        arc = design.project.shapes[-1]
        self.assertFalse(arc.closed)
        middle = arc.paths()[0].points[len(arc.paths()[0].points) // 2]
        self.assertGreater(middle[0] - 200, 20, "the arc bows away from the straight line")
        design.tool.setCurrentText("Smooth Freehand")
        start = canvas.mapFromScene(QPointF(30, 180))
        QTest.mousePress(canvas.viewport(), Qt.MouseButton.LeftButton, pos=start)
        for x, y in ((40, 170), (55, 172), (60, 185), (50, 195), (36, 192), (30.2, 180.4)):
            QTest.mouseMove(canvas.viewport(), canvas.mapFromScene(QPointF(x, y)))
        QTest.mouseRelease(canvas.viewport(), Qt.MouseButton.LeftButton, pos=canvas.mapFromScene(QPointF(30.2, 180.4)))
        drawn = design.project.shapes[-1]
        self.assertTrue(drawn.closed, "ending where it started closes the shape")
        self.assertTrue(drawn.handles, "smooth freehand draws curves, not a polyline")

    def test_knife_eraser_and_eyedropper(self):
        shapes = (editor_model.Shape("Rectangle", 20, 20, 60, 40),
                  editor_model.Shape("Ellipse", 120, 20, 40, 40, action="Score", stroke="#1f6f3a"))
        design = self.editor(editor_model.Project(279.4, 215.9, shapes))
        design.resize(900, 640)
        design.show()
        self.app.processEvents()
        canvas = design.canvas
        design.tool.setCurrentText("Knife")
        self.drag(canvas, (50, 5), (50, 75))
        self.assertEqual(len(design.project.shapes), 3, "the rectangle is split in two; the ellipse untouched")
        self.assertEqual(design.project.shapes[-1], shapes[1])
        design.undo()
        design.tool.setCurrentText("Eraser")
        self.assertTrue(design.eraser_options.isVisibleTo(design))
        design.eraser_size.setValue(4)
        self.drag(canvas, (30, 10), (30, 70))
        widths = sorted(round(shape.width) for shape in design.project.shapes[:-1])
        self.assertEqual(len(widths), 2)
        self.assertLess(widths[0], 10)
        design.undo()
        design.select((0,))
        design.tool.setCurrentText("Eyedropper")
        self.click(canvas, (140, 20))
        first = design.project.shapes[0]
        self.assertEqual((first.action, first.stroke), ("Score", "#1f6f3a"))

    def test_side_handles_stretch_and_the_red_point_sets_corner_radius(self):
        shapes = (editor_model.Shape("Rounded rectangle", 40, 40, 60, 40, radius=4),)
        design = self.editor(editor_model.Project(279.4, 215.9, shapes))
        design.resize(900, 640)
        design.show()
        self.app.processEvents()
        canvas = design.canvas
        design.select((0,))
        self.drag(canvas, (100, 60), (130, 60))
        stretched = design.project.shapes[0]
        self.assertAlmostEqual(stretched.width, 90, delta=0.6)
        self.assertEqual((stretched.x, stretched.height), (40, 40), "only one axis stretches")
        design.undo()
        self.drag(canvas, canvas.radius_handle(), (52, 40))
        self.assertAlmostEqual(design.project.shapes[0].radius, 12, delta=0.6)

    def test_color_swatches_offer_studio_colors_clear_and_canvas_sampling(self):
        shapes = (editor_model.Shape("Rectangle", 20, 20, 40, 40),
                  editor_model.Shape("Ellipse", 120, 20, 40, 40, stroke="#1f6f3a", fill="#f7c600"))
        design = self.editor(editor_model.Project(279.4, 215.9, shapes))
        design.resize(900, 640)
        design.show()
        self.app.processEvents()
        design.select((0,))
        swatch = design.line_color
        self.assertEqual(len(swatch.swatches), 16, "Clear plus fifteen basic colors")
        swatch.swatches["#d0202e"].click()
        self.assertEqual(design.project.shapes[0].stroke, "#d0202e")
        swatch.swatches[""].click()
        self.assertEqual(design.project.shapes[0].stroke, "")
        swatch.pick_from_canvas.emit()
        self.click(design.canvas, (140, 20))
        self.assertEqual(design.project.shapes[0].stroke, "#1f6f3a")
        self.assertEqual(design.selection(), (0,), "sampling does not change the selection")
        design.select((0, 1))
        self.assertFalse(design.line_color.mixed, "both now share the sampled line color")
        self.assertTrue(design.fill_color.mixed)

    def test_transform_panel_moves_rotates_scales_and_shears(self):
        design = self.editor(editor_model.Project(279.4, 215.9, (editor_model.Shape("Rectangle", 50, 50, 20, 10),)))
        design.select(())
        self.assertFalse(design.scale_button.isEnabled())
        design.select((0,))
        design.nudge.setValue(2)
        [b for b in design.dock.panels["transform"].findChildren(QPushButton) if b.text() == "→"][0].click()
        self.assertEqual(design.project.shapes[0].x, 52)
        design.move_by_x.setValue(-2)
        design.move_by_y.setValue(5)
        design.move_by_button.click()
        self.assertEqual((design.project.shapes[0].x, design.project.shapes[0].y), (50, 55))
        design.rotate_buttons["+90°"].click()
        self.assertEqual(design.project.shapes[0].rotation, 90)
        design.undo()
        design.scale_buttons["200%"].click()
        self.assertEqual((design.project.shapes[0].width, design.project.shapes[0].height), (40, 20))
        design.undo()
        design.shear_buttons["H +15°"].click()
        self.assertEqual(design.project.shapes[0].kind, "Path")
        design.undo()
        # Centre of rotation: O shows it, and Rotate By turns about it.
        QTest.keyClick(design.canvas, Qt.Key.Key_O)
        self.assertTrue(design.centre_check.isChecked())
        design.canvas.rotation_centre = (50, 55)
        design.rotate_buttons["+90°"].click()
        moved = editor_model.bounds(design.project.shapes)
        self.assertAlmostEqual(moved.x, 40, delta=0.01, msg="turned about the chosen centre, not its own")
        QTest.keyClick(design.canvas, Qt.Key.Key_O)
        self.assertIsNone(design.canvas.rotation_centre)

    def test_replicate_presets_mirror_and_fill_page(self):
        design = self.editor(editor_model.Project(279.4, 215.9, (editor_model.Shape("Rectangle", 10, 10, 20, 20),)))
        design.select((0,))
        design.replicate_gap.setValue(2)
        design.replicate_buttons["Row of 3"].click()
        self.assertEqual([s.x for s in design.project.shapes], [10, 32, 54])
        self.assertEqual(design.selection(), (1, 2))
        design.undo()
        design.select((0,))
        design.replicate_buttons["Mirror Right"].click()
        self.assertTrue(design.project.shapes[1].flip_x)
        design.undo()
        design.select((0,))
        design.replicate_buttons["Rotate 3"].click()
        self.assertEqual(len(design.project.shapes), 4)
        design.undo()
        design.select((0,))
        design.replicate_buttons["Fill Page"].click()
        x, y, w, h = design.cut_area()
        self.assertEqual(len(design.project.shapes), int((w + 2) // 22) * int((h + 2) // 22))

    def test_modify_offset_and_nesting_panels(self):
        shapes = (editor_model.Shape("Rectangle", 0, 0, 40, 40), editor_model.Shape("Rectangle", 20, 20, 40, 40))
        design = self.editor(editor_model.Project(279.4, 215.9, shapes))
        design.select((0, 1))
        for name in ("Weld", "Subtract", "Subtract All", "Intersect", "Divide", "Crop", "Make Compound Path"):
            self.assertTrue(design.modify_buttons[name].isEnabled(), name)
        design.modify_buttons["Divide"].click()
        self.assertEqual(len(design.project.shapes), 3)
        design.undo()
        design.select((0,))
        design.offset_join.setCurrentIndex(design.offset_join.findData("round"))
        design.offset_distance.setValue(3)
        design.internal_offset_button.click()
        self.assertAlmostEqual(design.project.shapes[-1].width, 34, delta=0.01, msg="Internal Offset insets")
        design.undo()
        design.select((0, 1))
        design.nest_rotations.setCurrentIndex(design.nest_rotations.findData(1))
        design.nest_button.click()
        boxes = [editor_model.bounds((shape,)) for shape in design.project.shapes]
        self.assertFalse(boxes[0].x < boxes[1].x + boxes[1].w and boxes[1].x < boxes[0].x + boxes[0].w
                         and boxes[0].y < boxes[1].y + boxes[1].h and boxes[1].y < boxes[0].y + boxes[0].h,
                         "nested shapes no longer overlap")
        self.assertIn("Nested 2", design.status.text())

    def test_trace_panel_traces_an_area_of_a_placed_image(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        picture = Path(folder.name) / "art.png"
        make_image(picture)
        placed = sticker_trace.place(picture, 279.4, 215.9)
        design = self.editor(editor_model.Project(279.4, 215.9, (placed,)))
        design.resize(1100, 720)
        design.show()
        design.dock.open("trace")
        self.app.processEvents()
        design.select(())
        self.assertFalse(design.trace_styles["Trace"].isEnabled())
        design.select((0,))
        self.assertTrue(design.trace_styles["Trace"].isEnabled())
        design.trace_area_button.click()
        self.assertTrue(design.canvas.tracing_area)
        frame = editor_model.bounds((placed,))
        self.drag(design.canvas, (frame.x - 2, frame.y - 2), (frame.x + frame.w + 2, frame.y + frame.h + 2))
        self.assertEqual(design.canvas.trace_area, (0, 0, 1, 1))
        design.trace_styles["Trace Outer Edge"].click()
        self.assertGreater(len(design.project.shapes), 1)
        self.assertTrue(all(s.kind in ("Path", "Compound") for s in design.project.shapes[1:]))
        self.assertIsNone(design.canvas.trace_area, "the area is used once, as in Studio")

    def test_grid_styles_rulers_and_crosshairs_draw(self):
        design = self.editor()
        design.resize(900, 640)
        design.show()
        self.app.processEvents()
        lines = lambda: sum(1 for item in design.canvas.scene().items() if item.type() == 6)  # noqa: E731
        design.show_grid.setChecked(True)
        square = lines()
        design.grid_divisions.setValue(2)
        self.assertGreater(lines(), square, "divisions add minor lines")
        design.grid_style.setCurrentIndex(design.grid_style.findData("isometric"))
        self.assertGreater(lines(), 0)
        design.rulers.setChecked(True)
        design.crosshairs.setChecked(True)
        self.assertTrue(design.ruler_check.isChecked() and design.crosshair_check.isChecked())
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QMouseEvent
        viewport = design.canvas.viewport()
        QApplication.sendEvent(viewport, QMouseEvent(QEvent.Type.MouseMove, QPointF(300, 300),
                                                     viewport.mapToGlobal(QPointF(300, 300)), Qt.MouseButton.NoButton,
                                                     Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))
        self.assertIsNotNone(design.canvas.pointer, "the pointer is tracked without a button held")
        self.assertFalse(design.canvas.viewport().grab().isNull())

    def test_right_click_menu_hand_and_arrange(self):
        shapes = (editor_model.Shape("Rectangle", 20, 20, 40, 40), editor_model.Shape("Ellipse", 30, 30, 40, 40))
        design = self.editor(editor_model.Project(279.4, 215.9, shapes))
        design.select((0,))
        menu = QMenu()
        design.fill_context_menu(menu)
        labels = [action.text() for action in menu.actions() if action.text()]
        for label in ("Cut", "Copy", "Paste", "Paste in Front", "Duplicate", "Delete", "Group", "Ungroup",
                      "Arrange", "Mirror", "Make Compound Path", "Release Compound Path", "Edit Points"):
            self.assertIn(label, labels)
        design.arrange("front")
        self.assertEqual(design.project.shapes, (shapes[1], shapes[0]))
        self.assertEqual(design.selection(), (1,))
        design.arrange("back")
        self.assertEqual(design.project.shapes, shapes)
        canvas = design.canvas
        design.resize(900, 640)
        design.show()
        self.app.processEvents()
        canvas.zoom_by(3)
        before = canvas.horizontalScrollBar().value()
        design.tool.setCurrentText("Hand")
        QTest.mousePress(canvas.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(300, 300))
        QTest.mouseMove(canvas.viewport(), QPoint(200, 300))
        QTest.mouseRelease(canvas.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(200, 300))
        self.assertGreater(canvas.horizontalScrollBar().value(), before)
        self.assertEqual(design.project.shapes, shapes, "the Hand tool never edits")

    def test_measure_tool_scales_a_photo_to_a_known_length(self):
        photo = editor_model.Shape("Rectangle", 20, 20, 100, 50)
        other = editor_model.Shape("Rectangle", 150, 20, 10, 10)
        design = self.editor(editor_model.Project(279.4, 215.9, (photo, other)))
        design.resize(1100, 720)
        design.show()
        self.app.processEvents()
        canvas = design.canvas
        QTest.keyClick(canvas, Qt.Key.Key_M)
        self.assertEqual(design.tool.currentData(), "Measure")
        self.assertTrue(design.measure_options.isVisibleTo(design))
        self.drag(canvas, (20, 30), (60, 30))
        measured = design.measurement()[0]
        self.assertAlmostEqual(measured, 40, delta=0.5)
        self.assertEqual(design.measure_label.text(), f"{measured:.2f} mm at 0.0°")
        design.select(())
        self.assertFalse(design.scale_to_button.isEnabled(), "nothing selected to scale")
        design.select((0,))
        self.assertTrue(design.scale_to_button.isEnabled())
        with mock.patch.object(QInputDialog, "getDouble", return_value=(30.0, True)):
            design.scale_to_button.click()
        factor = 30 / measured
        start = design.canvas.measure[0]
        scaled = design.project.shapes[0]
        self.assertAlmostEqual(scaled.width, 100 * factor)
        self.assertAlmostEqual(scaled.height, 50 * factor)
        # Scaled about where the measurement started, so that point stays put.
        self.assertAlmostEqual(scaled.x, start[0] + (20 - start[0]) * factor)
        self.assertAlmostEqual(scaled.y, start[1] + (20 - start[1]) * factor)
        self.assertEqual(design.project.shapes[1], other, "only the selection is scaled")
        self.assertEqual(design.measure_label.text(), "30.00 mm at 0.0°")
        QTest.keyClick(canvas, Qt.Key.Key_Escape)
        self.assertEqual(design.measure_label.text(), "Drag across a known distance")

    def test_copy_and_paste_carry_an_outline_into_a_new_design(self):
        from cutline import qt_design

        self.addCleanup(setattr, qt_design, "_clipboard", qt_design._clipboard)
        shapes = (editor_model.Shape("Rectangle", 20, 20, group=1), editor_model.Shape("Ellipse", 70, 20, group=1),
                  editor_model.Shape("Line", 10, 100))
        design = self.editor(editor_model.Project(279.4, 215.9, shapes))
        design.select((0, 1))
        design.copy_action.trigger()
        design.replace_document(editor_model.Project(279.4, 215.9, (editor_model.Shape("Rectangle", 5, 5, group=1),)))
        self.assertTrue(design.paste_action.isEnabled())
        design.paste_action.trigger()
        pasted = design.project.shapes[1:]
        width = editor_model.bounds(shapes[:2]).w
        self.assertEqual([(shape.kind, shape.x, shape.y) for shape in pasted],
                         [("Rectangle", 20 + width + 2, 20), ("Ellipse", 70 + width + 2, 20)],
                         "Studio pastes just to the right of the original")
        self.assertEqual({shape.group for shape in pasted}, {2}, "a pasted group gets a number of its own")
        self.assertEqual(design.selection(), (1, 2))
        design.paste_front_action.trigger()
        self.assertEqual([(shape.x, shape.y) for shape in design.project.shapes[3:]], [(20, 20), (70, 20)],
                         "Paste in Front keeps the copy exactly in place")
        design.cut_action.trigger()
        self.assertEqual(len(design.project.shapes), 3)

    def test_type_tool_places_text_where_clicked_and_edits_text_it_hits(self):
        design = self.editor()
        design.resize(900, 640)
        design.show()
        self.app.processEvents()
        canvas = design.canvas
        QTest.keyClick(canvas, Qt.Key.Key_T)
        self.click(canvas, (60, 50))
        self.assertIsNotNone(design.text_box, "the Text tool opens a box on the page")
        QTest.keyClicks(design.text_box, "A")
        self.assertEqual(design.history.current.shapes, (), "typing previews; nothing is committed yet")
        self.assertEqual(design.project.shapes[0].text.value, "A", "the outline follows the typing")
        QTest.keyClick(design.text_box, Qt.Key.Key_Escape)
        self.assertIsNone(design.text_box)
        text = design.project.shapes[0]
        self.assertAlmostEqual(text.x, 60, delta=0.5)
        self.assertAlmostEqual(text.y, 50, delta=0.5)
        self.assertEqual(text.text.value, "A")
        box = editor_model.bounds((text,))
        self.click(canvas, (box.x + box.w / 2, box.y + box.h / 2))
        self.assertIsNotNone(design.text_box, "clicking text with the Text tool edits it")
        self.assertEqual(design.text_box.toPlainText(), "A")
        QTest.keyClicks(design.text_box, "B")
        QTest.keyClick(design.text_box, Qt.Key.Key_Escape)
        self.assertEqual(len(design.project.shapes), 1)
        self.assertEqual(design.project.shapes[0].text.value, "AB")

    def test_text_style_panel_restyles_text_and_sets_it_on_a_path(self):
        from cutline import design_text

        if "DejaVu Sans" not in design_text.installed_families():
            self.skipTest("test font DejaVu Sans is not installed")
        lettering = design_text.shape(editor_model.TextSpec("Hi", "DejaVu Sans", 10), 30, 30)
        line = editor_model.Shape("Line", 20, 120, width=150, height=0)
        design = self.editor(editor_model.Project(279.4, 215.9, (lettering, line)))
        design.select((0,))
        self.assertTrue(design.text_button.isEnabled())
        self.assertFalse(design.release_path_button.isEnabled())
        self.assertFalse(design.text_to_path_button.isEnabled(), "Text to Path needs a guide selected too")
        design.text_style_buttons["bold"].click()
        self.assertTrue(design.project.shapes[0].text.bold)
        self.assertNotEqual(design.project.shapes[0].contours, lettering.contours)
        design.char_spacing.setValue(200)
        self.assertEqual(design.project.shapes[0].text.char_spacing, 200)
        self.assertGreater(design.project.shapes[0].width, lettering.width)

        design.select((0, 1))
        self.assertTrue(design.text_to_path_button.isEnabled())
        design.text_to_path_button.click()
        text, guide = design.project.shapes
        self.assertEqual(len(text.text.path), 2)
        self.assertEqual(guide.action, "None", "Studio stops cutting the guide once text sits on it")
        self.assertAlmostEqual(text.y + text.height, 120, delta=3)
        self.assertTrue(design.release_path_button.isEnabled())
        design.release_path_button.click()
        released = design.project.shapes[0]
        self.assertEqual(released.text.path, ())
        self.assertTrue(released.text.bold, "releasing keeps the style")
        design.undo_action.trigger()
        self.assertTrue(design.project.shapes[0].text.path, "each step is one undo")

    def test_a_pixscan_photo_is_placed_at_true_size_and_not_cut(self):
        from tests.test_pixscan import photograph
        from cutline import pixscan

        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        good = Path(folder.name) / "mat.png"
        photograph(pixscan.MATS[2]).save(good)
        blank = Path(folder.name) / "blank.png"
        QImage(400, 300, QImage.Format.Format_RGB32).save(str(blank))
        design = self.editor()
        with mock.patch.object(QFileDialog, "getOpenFileName", return_value=(str(blank), "")):
            design.import_pixscan()
        self.assertEqual(design.project.shapes, ())
        self.assertIn("No PixScan markers", design.status.text())
        with mock.patch.object(QFileDialog, "getOpenFileName", return_value=(str(good), "")):
            design.import_pixscan()
        photo, = design.project.shapes
        self.assertEqual((photo.kind, photo.width, photo.height, photo.action), ("Sticker", 275, 215, "None"))
        self.assertEqual(design.selection(), (0,))
        self.assertIn("PixScan mat 2", design.status.text())
        self.assertIn("not known yet", design.status.text())

    def test_replicate_works_from_its_panel(self):
        design = self.editor(editor_model.Project(279.4, 215.9, (editor_model.Shape("Rectangle", 10, 10, 20, 20),)))
        design.select(())
        self.assertFalse(design.replicate_button.isEnabled())
        design.select((0,))
        design.columns.setValue(3)
        design.rows.setValue(1)
        design.x_gap.setValue(5)
        design.replicate_button.click()
        self.assertEqual([shape.x for shape in design.project.shapes], [10, 35, 60])

    def test_design_zoom_has_keyboard_shortcuts_scoped_to_the_editor(self):
        from PySide6.QtGui import QKeySequence
        design = self.editor()
        levels = []
        design.canvas.zoom_changed.connect(levels.append)
        for keys in ("Ctrl+=", "Ctrl+-", "Ctrl+0"):
            found = [a for a in design.actions() if QKeySequence(keys) in a.shortcuts()]
            self.assertEqual(len(found), 1, keys)
            self.assertEqual(found[0].shortcutContext(), qt_gui.Qt.ShortcutContext.WidgetWithChildrenShortcut)
            found[0].trigger()
        self.assertEqual(len(levels), 3)

    def test_a_long_checkbox_label_wraps_instead_of_being_clipped(self):
        design = self.editor()
        design.show()
        self.app.processEvents()
        caption = getattr(design.register, "caption", None)
        self.assertIsNotNone(caption, "the registration checkbox should carry a wrapping label")
        self.assertEqual(caption.text(), "Enable registration")
        self.assertTrue(caption.wordWrap())
        self.assertEqual(design.register.accessibleName(), "Enable registration")
        initial = design.register.isChecked()
        QTest.mouseClick(caption, Qt.MouseButton.LeftButton)
        self.assertEqual(design.register.isChecked(), not initial)
        design.register.setDisabled(True)
        self.assertFalse(caption.isEnabled())
        QTest.mouseClick(caption, Qt.MouseButton.LeftButton)
        self.assertEqual(design.register.isChecked(), not initial)

    def test_simplify_reduces_a_selected_path_without_an_error_message(self):
        curve = tuple((index / 24, (index / 24) ** 2) for index in range(25))
        project = editor_model.Project(279.4, 215.9, (
            editor_model.Shape("Path", 30, 30, 60, 60, points=curve, closed=False),))
        design = self.editor(project)
        design.select((0,))
        with mock.patch.object(design, "message") as message:
            design.convert_selection(True)
        message.assert_not_called()
        shape, = design.project.shapes
        self.assertLess(len(shape.points), len(curve))
        self.assertEqual(shape.kind, "Path")
        design.undo()
        self.assertEqual(len(design.project.shapes[0].points), len(curve))

    def test_simplifying_an_untraced_sticker_reports_instead_of_raising(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        image = Path(folder.name) / "art.png"
        make_image(image)
        placed = sticker_trace.place(image, 279.4, 215.9)
        design = self.editor(editor_model.Project(279.4, 215.9, (placed,)))
        design.select((0,))
        with mock.patch.object(design, "message") as message:
            design.convert_selection(True)
        message.assert_called_once()
        self.assertIn("Trace", message.call_args.args[0])
