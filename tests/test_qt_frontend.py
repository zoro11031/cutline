"""Exercise the Qt frontend without a display or a hardware cutter."""

import io
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from cut_proxies import cli, editor_model, job, transport

try:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QFileDialog
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
        self.window = qt_gui.Window()
        self.addCleanup(self.window.deleteLater)

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
        window.leave_design()
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

    def test_starting_guide_repeats_one_confirmed_pass_and_applies_the_count(self):
        window = self.window
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
             mock.patch.object(transport, "open_transport", return_value=fake):
            window.start_guide_pass(trial)
            window.blade["force"].setValue(27)
            window.start_guide_pass(trial)
        self.assertEqual(fake.run.call_args_list, [mock.call(trial.sheet.steps, wait_done=True)] * 2)
        self.assertIn("2 completed passes", window.guide_status.text())
        window.guide_use.click()
        self.assertEqual((window.blade["force"].value(), window.blade["passes"].value()), (25, 2))
        window.guide_next.click()
        self.assertEqual(window.guide.spot, 2)
        self.assertFalse(window.guide_use.isEnabled())

    def test_qt_launcher_can_be_selected_explicitly_or_automatically(self):
        with mock.patch.object(qt_gui, "main", return_value=0) as launch:
            self.assertEqual(cli.main(["gui", "--frontend", "qt"]), 0)
            self.assertEqual(cli.main(["gui"]), 0)
        self.assertEqual(launch.call_args_list, [mock.call([]), mock.call([])])

    def test_qt_preview_produces_a_nonempty_render(self):
        window = self.window
        window.show()
        self.app.processEvents()
        image = window.grab().toImage().convertToFormat(QImage.Format.Format_RGB32)
        self.assertGreaterEqual(image.width(), 900)
        self.assertGreater(len(set(bytes(image.constBits()))), 15)
