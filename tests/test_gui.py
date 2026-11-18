"""GUI handlers and a display-backed Design button smoke test."""
import os
import tempfile
from pathlib import Path
import unittest
import types
from unittest import mock
from cut_proxies import config, job, presets

try:
    from cut_proxies import gui
except ImportError:
    gui = None


@unittest.skipUnless(gui is not None, 'GTK and PyGObject are required')
class TestDesignWorkspace(unittest.TestCase):
    def make_window(self):
        from gi.repository import Gdk

        if not gui.Gtk.init_check() or Gdk.Display.get_default() is None:
            self.skipTest('a display is required to create GTK widgets')
        gui.Adw.init()
        app = gui.Adw.Application(application_id='org.printing.DesignSmokeTest',
                                  flags=gui.Gio.ApplicationFlags.NON_UNIQUE)
        self.assertTrue(app.register(None))
        win = gui.Window(app)
        self.addCleanup(win.destroy)
        return win

    def test_design_button_opens_reviews_and_reopens_the_workspace(self):
        win = self.make_window()
        win.design_button.emit('clicked')
        self.assertIsNotNone(win.design)
        design = win.design
        self.assertIs(win.workspaces.get_visible_child(), design.view)
        self.assertTrue(design.project.shapes)
        design.review.emit('clicked')
        self.assertEqual(win.stack.get_visible_child_name(), 'cut')
        self.assertIsNotNone(win.job)
        win.design_button.emit('clicked')
        self.assertIs(win.workspaces.get_visible_child(), design.view)
        win.close_design()
        self.assertEqual(win.workspaces.get_visible_child_name(), 'tools')
        self.assertIsNone(win.design)

    def test_save_preset_button_reloads_edited_blade_settings(self):
        with tempfile.TemporaryDirectory() as folder, \
             mock.patch.object(config, "CONFIG_DIR", Path(folder)):
            win = self.make_window()
            self.assertEqual(win.r_force.get_value(), 35)
            win.r_force.set_value(32)
            win.r_speed.set_value(21)
            win.r_depth.set_value(0)
            win.r_passes.set_value(5)
            win.save_preset_btn.emit("clicked")
            win.r_preset.set_selected(0)
            names = sorted(presets.all_presets())
            win.r_preset.set_selected(names.index(config.DEFAULT_PRESET))
            self.assertEqual((win.r_force.get_value(), win.r_speed.get_value(),
                              win.r_depth.get_value(), win.r_passes.get_value()),
                             (32, 21, 0, 5))
            self.assertIsNone(win.job.settings.depth)
            self.assertEqual(win.job.settings.passes, 5)

    def test_registration_failure_opens_named_mark_diagnostics(self):
        win = self.make_window()
        report = mock.Mock()
        report.problems.return_value = ["top-right L: no ink found in the search window"]
        matched = mock.Mock()
        matched.near_marks.return_value = ("bottom-left L",)
        matched.mat_placement.return_value = "Align with the top-left of the mat grid."
        from cut_proxies.transport import RegistrationFailed
        with mock.patch.object(gui.Adw, 'AlertDialog') as dialog:
            win.show_registration_failure(str(RegistrationFailed()), win.job, matched,
                                          {"sheet": 2, "frontPage": 3}, report, "auto", 14.51)
        self.assertTrue(win.scan_result.get_visible())
        self.assertEqual(win.scan_result.get_title(), "Sheet 2 registration failed")
        body = dialog.call_args.kwargs["body"]
        self.assertIn("did not report which mark", body)
        self.assertIn("PDF check: top-right L", body)
        self.assertIn("Check for nearby artwork at bottom-left L", body)
        self.assertIn("PDF front page 3", win.scan_result.get_subtitle())
        self.assertIn("14.5 s", win.scan_result.get_subtitle())
        self.assertIn("TB123,", win.scan_result.get_subtitle())
        callback = dialog.return_value.connect.call_args.args[1]
        callback()
        self.assertEqual(win.stack.get_visible_child_name(), "sheet")

    @unittest.skipUnless(os.environ.get('PROXYPRESS_BUNDLE_DIR'), 'generate a ProxyPress bundle first')
    def test_selecting_a_real_zip_opens_its_paths_and_its_paired_studio_file(self):
        win = self.make_window()
        source = Path(os.environ['PROXYPRESS_BUNDLE_DIR']) / 'letter-landscape-8.zip'
        launched = []

        def launch(path):
            launched.append((path.name, path.read_bytes()[:13]))

        with mock.patch.object(gui.Gtk, 'FileDialog') as chooser, \
             mock.patch.object(win, 'measure_sheet') as measure, \
             mock.patch.object(gui.winboat, 'open_in_studio', side_effect=launch):
            win.on_pick_pdf()
            dialog = chooser.return_value
            callback = dialog.open.call_args.args[2]
            dialog.open_finish.return_value = gui.Gio.File.new_for_path(str(source))
            callback(dialog, mock.Mock())
            measure.assert_called_once()
            self.assertEqual(win.pdf_path, source)
            self.assertEqual(len(win.imported.paths), 8)
            self.assertFalse(win.job.blocked)
            win.on_open_studio()
        self.assertEqual(launched, [('sheet-1.studio3', b'silhouette05;')])

    @unittest.skipUnless(os.environ.get('PROXYPRESS_LEGACY_BUNDLE'), 'supply a legacy print-and-cut ZIP first')
    def test_selecting_legacy_zip_keeps_direct_cut_disabled_and_opens_studio_file(self):
        win = self.make_window()
        source = Path(os.environ['PROXYPRESS_LEGACY_BUNDLE'])
        launched = []

        def launch(path):
            launched.append(path.read_bytes()[:13])

        with mock.patch.object(gui.Gtk, 'FileDialog') as chooser, \
             mock.patch.object(win, 'measure_sheet') as measure, \
             mock.patch.object(gui.winboat, 'open_in_studio', side_effect=launch):
            win.on_pick_pdf()
            dialog = chooser.return_value
            dialog.open_finish.return_value = gui.Gio.File.new_for_path(str(source))
            dialog.open.call_args.args[2](dialog, mock.Mock())
            measure.assert_called_once()
            self.assertEqual(win.pdf_path, source)
            self.assertIsNone(win.job)
            self.assertFalse(win.cut_button.get_sensitive())
            win.on_open_studio()
        self.assertEqual(launched, [b'silhouette05;'])


@unittest.skipUnless(gui is not None, 'PyGObject is not installed')
class TestOpenDialogs(unittest.TestCase):
    def test_svg_button_opens_a_filtered_file_dialog(self):
        window = mock.Mock()
        with mock.patch.object(gui.Gtk, 'FileDialog') as dialog:
            gui.Window.on_open_artwork(window)
        dialog.return_value.open.assert_called_once()
        filters = dialog.return_value.set_filters.call_args.args[0]
        self.assertEqual(filters.get_n_items(), 1)
        self.assertEqual(filters.get_item(0).get_name(), 'Cuttable vector (SVG, DXF)')

    def test_pdf_button_opens_a_filtered_file_dialog(self):
        window = mock.Mock()
        with mock.patch.object(gui.Gtk, 'FileDialog') as dialog:
            gui.Window.on_pick_pdf(window)
        dialog.return_value.open.assert_called_once()
        filters = dialog.return_value.set_filters.call_args.args[0]
        self.assertEqual(filters.get_n_items(), 2)
        filt = filters.get_item(0)
        self.assertEqual(filt.get_name(), 'PDF and ZIP files (*.pdf, *.zip)')
        self.assertEqual(filters.get_item(1).get_name(), 'All files')
        dialog.return_value.set_default_filter.assert_called_once_with(filt)
        for name, mime, accepted in (
                ('Job.ZiP','application/octet-stream',True),
                ('Job.pdf','application/octet-stream',True),
                ('archive.zip','application/zip',True),
                ('vector.svg','image/svg+xml',False)):
            with self.subTest(name=name, mime=mime):
                entry = gui.Gio.FileInfo()
                entry.set_display_name(name)
                entry.set_content_type(mime)
                self.assertEqual(filt.match(entry), accepted)

    def test_studio_file_picker_passes_the_native_file_to_the_launcher(self):
        window = mock.Mock()
        with mock.patch.object(gui.Gtk, 'FileDialog') as chooser:
            gui.Window.on_pick_studio_file(window)
        dialog = chooser.return_value
        filters = dialog.set_filters.call_args.args[0]
        self.assertEqual(filters.get_item(0).get_name(), 'Silhouette Studio files')
        callback = dialog.open.call_args.args[2]
        dialog.open_finish.return_value = gui.Gio.File.new_for_path('/tmp/sheet-2.studio3')
        callback(dialog, mock.Mock())
        window.launch_studio_file.assert_called_once_with(Path('/tmp/sheet-2.studio3'))

    def test_a_failed_import_keeps_the_previous_artwork_and_job(self):
        previous = object()
        window = types.SimpleNamespace(imported=previous, toast=mock.Mock(), clear_bundle=mock.Mock())
        with mock.patch.object(gui.Gtk, 'FileDialog') as dialog, \
             mock.patch('cut_proxies.importer.load', side_effect=ValueError('bad file')):
            gui.Window.on_open_artwork(window)
            callback = dialog.return_value.open.call_args.args[2]
            dialog.return_value.open_finish.return_value.get_path.return_value = '/tmp/invalid.svg'
            callback(dialog.return_value, mock.Mock())
        self.assertIs(window.imported, previous)
        window.toast.assert_called_once()

    def test_colorblind_preference_preserves_calibration(self):
        window = types.SimpleNamespace(preview=mock.Mock(), toast=mock.Mock())
        toggle = mock.Mock()
        toggle.get_active.return_value = True
        with mock.patch.object(gui.config, 'load_settings', return_value={'cut_offset_mm': [0.2, -0.1]}), \
             mock.patch.object(gui.config, 'save_settings') as save:
            gui.Window.on_colorblind_changed(window, toggle)
        window.preview.set_colorblind.assert_called_once_with(True)
        self.assertEqual(save.call_args.args[0],
                         {'cut_offset_mm': [0.2, -0.1], 'colorblind_mode': True})


@unittest.skipUnless(gui is not None, 'PyGObject is not installed')
class TestCutConfirmationGuard(unittest.TestCase):
    def test_a_checked_unchanged_idle_job_is_allowed(self):
        checked = job.build(offset_mm=(0, 0))
        self.assertFalse(checked.blocked)
        self.assertEqual(gui.cut_refusal(checked, checked, False), '')

    def test_a_changed_job_is_refused(self):
        first = job.build(offset_mm=(0, 0))
        changed = job.build(force=20, offset_mm=(0, 0))
        self.assertTrue(gui.cut_refusal(changed, first, False))

    def test_a_blocked_job_is_refused(self):
        checked = job.build(cutter='Silhouette Portrait', offset_mm=(0, 0))
        self.assertTrue(checked.blocked)
        self.assertTrue(gui.cut_refusal(checked, checked, False))

    def test_an_active_send_is_refused(self):
        checked = job.build(offset_mm=(0, 0))
        self.assertTrue(gui.cut_refusal(checked, checked, True))

    def test_no_job_is_refused(self):
        self.assertTrue(gui.cut_refusal(None, None, False))


@unittest.skipUnless(gui is not None, 'PyGObject is not installed')
class TestSheetRequestGuard(unittest.TestCase):
    def make_window(self, token):
        return types.SimpleNamespace(_sheet_token=token, pdf_bundle=None, preview=mock.Mock(),
            sheet_result=mock.Mock(), rebuild=mock.Mock())

    def test_current_result_updates_artwork_and_measurement(self):
        token = object()
        window = self.make_window(token)
        report = mock.Mock(marks=[], found=[], page_w_mm=279.4, page_h_mm=215.9)
        report.diagnosis.return_value = 'marks are where the template expects them'
        gui.Window._sheet_done(window, report, [], b'png', token)
        self.assertIs(window.sheet_report, report)
        window.preview.set_pdf.assert_called_once_with(b'png', 279.4, 215.9)

    def test_stale_result_cannot_replace_the_current_preview(self):
        window = self.make_window(object())
        gui.Window._sheet_done(window, mock.Mock(marks=[], found=[]), [], b'old', object())
        window.preview.set_pdf.assert_not_called()
        window.rebuild.assert_not_called()
        self.assertFalse(window.preview.set_pdf.called)

    def test_stale_failure_cannot_clear_the_current_preview(self):
        window = self.make_window(object())
        gui.Window._sheet_failed(window, 'old failure', object())
        window.preview.set_pdf.assert_not_called()
        self.assertFalse(window.preview.set_pdf.called)

    def test_current_failure_clears_the_previous_preview(self):
        token = object()
        window = self.make_window(token)
        gui.Window._sheet_failed(window, 'bad PDF', token)
        window.preview.set_pdf.assert_called_once_with()
        self.assertIsNone(window.sheet_report)
