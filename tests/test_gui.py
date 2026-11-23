"""GUI handlers and a display-backed Design button smoke test."""
import os
import tempfile
from pathlib import Path
import unittest
import types
from unittest import mock
from cutline import config, corner_test, cutters, editor_model, graphtec, importer, job, presets, starting_guide
from tests.test_vector_editing import compound_project
from tests.test_sticker_print import make_image

try:
    from cutline import gui
except ImportError:
    gui = None


_APP_SERIAL = 0


@unittest.skipUnless(gui is not None, 'GTK and PyGObject are required')
class TestDesignWorkspace(unittest.TestCase):
    def make_window(self):
        from gi.repository import Gdk

        if not gui.Gtk.init_check() or Gdk.Display.get_default() is None:
            self.skipTest('a display is required to create GTK widgets')
        gui.Adw.init()
        # A unique id per window: the discrimination suite runs a test class again
        # in-process, and a repeated application id fails to register.
        global _APP_SERIAL
        _APP_SERIAL += 1
        app = gui.Adw.Application(application_id=f'org.printing.DesignSmokeTest.{self._testMethodName}{_APP_SERIAL}',
                                  flags=gui.Gio.ApplicationFlags.NON_UNIQUE)
        self.assertTrue(app.register(None))
        win = gui.Window(app)
        self.addCleanup(win.destroy)
        device = graphtec.device_by_name("Silhouette Cameo 4")
        patcher = mock.patch.object(gui.transport, "usb_endpoints", return_value=[(device, "", (1, 2))])
        patcher.start()
        self.addCleanup(patcher.stop)
        # The tier stays offline even with a cutter on the bus: automatic tool
        # detection opens a link, so nothing may reach real hardware.
        offline = mock.patch.object(gui.transport, "open_transport",
                                    side_effect=gui.transport.NotFound("offline test"))
        offline.start()
        self.addCleanup(offline.stop)
        # Nor may a background refresh reach the radio (an LE scan, an inquiry).
        radio = mock.patch.object(cutters, "wireless_choices", return_value={})
        radio.start()
        self.addCleanup(radio.stop)
        with mock.patch.object(win, "on_refresh_status"):
            win.refresh_usb()
        return win

    def test_multiple_usb_cutters_require_an_endpoint_choice(self):
        win = self.make_window()
        device = graphtec.device_by_name("Silhouette Cameo 4")
        with mock.patch.object(win, "on_refresh_status"), mock.patch.object(gui.transport, "usb_endpoints",
                               return_value=[(device, "", (1, 2)), (device, "", (1, 3))]):
            win.refresh_usb()
            self.assertIsNone(win._connection()[1])
            self.assertFalse(win.cut_button.get_sensitive())
            win.r_bluetooth.set_selected(2)
            self.assertEqual(win._connection()[1], (1, 3))
            self.assertTrue(win.cut_button.get_sensitive())

    def test_scan_for_cutters_is_offered_while_no_cutter_is_found(self):
        # As in Silhouette Studio: with no cutter, the status row offers a
        # scan, shows it running, and says what to try when it finds nothing.
        win = self.make_window()
        device = graphtec.device_by_name("Silhouette Cameo 4")
        classic = ("bluetooth", "01:23:45:AB:CD:EF")
        with mock.patch.object(win, "on_refresh_status"), \
             mock.patch.object(gui.transport, "usb_endpoints", return_value=[]):
            win._wireless_choices = {}
            win.refresh_usb()
            self.assertTrue(win.scan_cutters_button.get_visible())
            self.assertTrue(win.scan_cutters_button.get_sensitive())
            self.assertFalse(win.refresh_cutters_button.get_visible())
            self.assertEqual(win.connection_status.get_subtitle(), cutters.NO_CUTTER_TEXT)
            with mock.patch.object(gui.threading, "Thread") as thread:
                win.scan_cutters_button.emit("clicked")
            self.assertEqual(thread.return_value.start.call_count, 1)
            self.assertFalse(win.scan_cutters_button.get_sensitive())
            self.assertEqual(win.connection_status.get_subtitle(), cutters.SCANNING_TEXT)
            win._wireless_done({})
            self.assertTrue(win.scan_cutters_button.get_visible())
            self.assertTrue(win.scan_cutters_button.get_sensitive())
            self.assertEqual(win.connection_status.get_subtitle(), cutters.NO_CUTTER_TEXT)
            win._wireless_done({classic: "CAMEO 4-1234 · Bluetooth Classic · 01:23:45:AB:CD:EF"})
            self.assertFalse(win.scan_cutters_button.get_visible())
            self.assertTrue(win.refresh_cutters_button.get_visible())
            self.assertEqual(win._connection(), classic)
        with mock.patch.object(win, "on_refresh_status"):
            win._wireless_done({})
            win.refresh_usb()
        self.assertFalse(win.scan_cutters_button.get_visible())  # the USB cutter is back
        self.assertEqual(win._connection()[1], (1, 2))
        self.assertIsNotNone(device)

    def test_only_scan_and_refresh_cutters_search_for_unpaired_cutters(self):
        win = self.make_window()
        with mock.patch.object(gui.threading, "Thread") as thread, \
             mock.patch.object(gui.GLib, "idle_add"), \
             mock.patch.object(cutters, "wireless_choices", return_value={}) as choices:
            win.refresh_wireless()                           # the 30 s timer
            thread.call_args.kwargs["target"]()
            self.assertEqual(choices.call_args, mock.call(search=False))
            win.refresh_wireless(search=True)                # asked for while the refresh runs
            self.assertEqual(thread.call_count, 1)
            win._wireless_done({})
            self.assertEqual(thread.call_count, 2)           # queued, not dropped
            thread.call_args.kwargs["target"]()
            self.assertEqual(choices.call_args, mock.call(search=True))
            win._wireless_done({})
            win.refresh_cutters_button.emit("clicked")       # Refresh Cutters
            thread.call_args.kwargs["target"]()
            self.assertEqual(choices.call_args, mock.call(search=True))

    def test_choosing_a_cutter_reads_its_status_and_pairs_an_unpaired_one(self):
        # As in Studio: selecting a cutter reads it, and pairs one the search found.
        win = self.make_window()
        classic = ("bluetooth", "AA:BB:CC:00:11:22")
        listing = {classic: "CAMEO 4-1234 · Bluetooth Classic · AA:BB:CC:00:11:22"}
        for unpaired in (True, False):
            with self.subTest(unpaired=unpaired), \
                 mock.patch.object(gui.transport, "usb_endpoints", return_value=[]), \
                 mock.patch.object(cutters, "unpaired", side_effect=lambda e, u=unpaired: u and e == classic), \
                 mock.patch.object(win, "detect_tools") as detect, \
                 mock.patch.object(gui.threading, "Thread") as thread:
                win._wireless_done({})
                detect.reset_mock()
                thread.reset_mock()
                win._wireless_done(listing)
                self.assertEqual(win._connection(), classic)
                self.assertEqual(win.connection_status.get_subtitle(),
                                 "Pairing with CAMEO 4-1234…" if unpaired else "Checking…")
                self.assertEqual(detect.call_count, 0)
                self.assertEqual(thread.call_count, 1)   # the status read, which pairs first
                win._checking_status = False

    def test_a_listed_cutter_that_does_not_answer_is_not_shown_as_available(self):
        # A paired cutter is listed even when it is switched off.
        win = self.make_window()
        classic = ("bluetooth", "AA:BB:CC:00:11:22")
        listing = {classic: "CAMEO 4-1234 · Bluetooth Classic · AA:BB:CC:00:11:22"}
        for answers in (True, False):
            cutter = mock.MagicMock()
            cutter.status_and_tools.return_value = ("unloaded", None)
            cutter.firmware_version.return_value = "1.20"
            cutter.device = graphtec.device_by_name("Silhouette Cameo 4")
            link = mock.MagicMock()
            link.__enter__.return_value = cutter
            if not answers:
                link.__enter__.side_effect = gui.transport.NotFound("No cutter detected at AA:BB:CC:00:11:22.")

            def run_now(target, daemon):
                return mock.Mock(start=target)

            with self.subTest(answers=answers), \
                 mock.patch.object(gui.transport, "usb_endpoints", return_value=[]), \
                 mock.patch.object(cutters, "open_link", return_value=link), \
                 mock.patch.object(gui.GLib, "idle_add", side_effect=lambda f, *a: f(*a)), \
                 mock.patch.object(gui.threading, "Thread", side_effect=run_now):
                win._wireless_done({})
                win._wireless_done(listing)
                text = win.connection_status.get_subtitle()
                self.assertNotEqual(text, listing[classic])
                if answers:
                    self.assertEqual(text, "Silhouette Cameo 4 · unloaded · 1.20")
                else:
                    self.assertEqual(text, cutters.NOT_DETECTED_TEXT)

    def test_refresh_keeps_an_explicit_cutter_and_does_not_switch_on_disconnect(self):
        win = self.make_window()
        device = graphtec.device_by_name("Silhouette Cameo 4")
        first, other = (1, 2), (1, 3)
        with mock.patch.object(gui.transport, "usb_endpoints") as detect:
            detect.return_value = [(device, "", first), (device, "", other)]
            win.refresh_usb()
            win.r_bluetooth.set_selected(1)
            win.connection_status.set_subtitle("Ready")
            detect.return_value = [(device, "", (1, 4)), (device, "", first), (device, "", other)]
            win.refresh_usb()
            self.assertEqual(win._connection()[1], first)
            self.assertEqual(win.connection_status.get_subtitle(), "Ready")
            detect.return_value = [(device, "", other)]
            win.refresh_usb()
            self.assertIsNone(win._connection()[1])
            self.assertFalse(win.cut_button.get_sensitive())

    def test_workspace_switch_leaves_an_empty_design_open_without_reviewing_it(self):
        win = self.make_window()
        original_job = win.job
        win.design_button.emit('clicked')
        self.assertIsNotNone(win.design)
        design = win.design
        self.assertIs(win.workspaces.get_visible_child(), design.view)
        design.commit((), ())
        self.assertFalse(design.review.get_visible())
        self.assertTrue(design.send_workspace.get_sensitive())
        design.send_workspace.emit('clicked')
        self.assertEqual(win.stack.get_visible_child_name(), 'cut')
        self.assertIsNone(win.reviewed_design)
        self.assertEqual(win.job.paths, original_job.paths)
        win.design_button.emit('clicked')
        self.assertIs(win.workspaces.get_visible_child(), design.view)
        self.assertFalse(design.project.shapes)
        with mock.patch.object(design, 'confirm_discard', side_effect=lambda discard: discard()):
            win.close_design()
        self.assertEqual(win.workspaces.get_visible_child_name(), 'tools')
        self.assertIsNone(win.design)

    def test_workspace_switch_exposes_the_current_workspace_as_pressed(self):
        win = self.make_window()
        self.assertIsInstance(win.send_workspace, gui.Gtk.ToggleButton)
        self.assertTrue(win.send_workspace.get_active())
        self.assertFalse(win.design_button.get_active())
        win.send_workspace.emit('clicked')
        self.assertTrue(win.send_workspace.get_active(), 'clicking the current workspace keeps it current')
        win.design_button.emit('clicked')
        design = win.design
        self.assertTrue(design.design_workspace.get_active())
        self.assertFalse(design.send_workspace.get_active())
        design.send_workspace.emit('clicked')
        self.assertFalse(win.design_button.get_active(), "Send's Design button must not stay pressed")
        self.assertTrue(win.send_workspace.get_active())
        self.assertFalse(design.send_workspace.get_active())

    @staticmethod
    def shortcuts(controller):
        return {controller.get_item(i).get_trigger().to_string(): controller.get_item(i).get_action().to_string()
                for i in range(controller.get_n_items())}

    def test_send_workspace_has_keyboard_shortcuts(self):
        win = self.make_window()
        keys = self.shortcuts(win.send_shortcuts)
        self.assertEqual(win.send_shortcuts.get_widget(), win.workspaces.get_child_by_name('tools'))
        for trigger, page in (('<Control>1', 'layout'), ('<Control>2', 'sheet'), ('<Control>3', 'cut')):
            action = keys[trigger].removeprefix('action(').removesuffix(')')
            win.workspaces.activate_action(action)
            self.assertEqual(win.stack.get_visible_child_name(), page, trigger)
        self.assertIn('open-artwork', keys['<Control>o'])
        self.assertIn('open-print', keys['<Shift><Control>o'])
        self.assertIn('design', keys['<Control>F1'])
        levels = []
        win.preview.connect('zoom-changed', lambda _preview, value: levels.append(value))
        for trigger in ('<Control>equal|<Control>plus', '<Control>minus', '<Control>0'):
            win.workspaces.activate_action(keys[trigger].removeprefix('action(').removesuffix(')'))
        self.assertEqual(len(levels), 3)
        # Nothing that moves the cutter is one keystroke away: these are all of them.
        self.assertEqual(sorted(a.removeprefix('action(workspace.').removesuffix(')') for a in keys.values()),
                         ['cut', 'design', 'fit-preview', 'layout', 'open-artwork', 'open-print',
                          'sheet', 'zoom-in', 'zoom-out'])

    def test_add_shape_works_with_the_default_select_tool(self):
        from cutline import editor_model

        win = self.make_window()
        win.on_edit_template()
        design = win.design
        design.history = editor_model.History(editor_model.Project(215.9, 279.4))
        design.refresh_objects()
        self.assertEqual(design.active_tool, 'Select')
        self.assertTrue(design.add_button.get_sensitive())
        design.add_button.emit('clicked')
        self.assertEqual([s.kind for s in design.project.shapes], ['Rectangle'])
        design.tool.set_selected(design.tools.index('Ellipse'))
        design.add_button.emit('clicked')
        self.assertEqual([s.kind for s in design.project.shapes], ['Rectangle', 'Ellipse'])

    def test_design_zoom_has_keyboard_shortcuts(self):
        win = self.make_window()
        win.on_edit_template()
        design = win.design
        keys = self.shortcuts(design.shortcuts)
        start = design.canvas.zoom
        design.view.activate_action(keys['<Control>equal|<Control>plus'].removeprefix('action(').removesuffix(')'))
        self.assertGreater(design.canvas.zoom, start)
        design.view.activate_action(keys['<Control>minus'].removeprefix('action(').removesuffix(')'))
        design.view.activate_action(keys['<Control>0'].removeprefix('action(').removesuffix(')'))
        self.assertEqual(design.canvas.zoom, 1.0)

    def test_file_menu_changes_pages_only_after_opening_the_print_file(self):
        win = self.make_window()
        win.send_workspace.emit('clicked')
        action = win.workspace_actions.lookup_action('open-print')
        with mock.patch.object(gui.Gtk, 'FileDialog') as chooser, \
             mock.patch.object(win, 'measure_sheet') as measure:
            action.activate(None)
            dialog = chooser.return_value
            dialog.open_finish.side_effect = gui.GLib.Error('Dismissed')
            dialog.open.call_args.args[2](dialog, mock.Mock())
            self.assertEqual(win.stack.get_visible_child_name(), 'cut')
            self.assertIsNone(win.pdf_path)
            action.activate(None)
            dialog.open_finish.side_effect = None
            dialog.open_finish.return_value = gui.Gio.File.new_for_path('/tmp/print.pdf')
            dialog.open.call_args.args[2](dialog, mock.Mock())
        self.assertEqual(win.stack.get_visible_child_name(), 'sheet')
        self.assertEqual(win.pdf_path, Path('/tmp/print.pdf'))
        measure.assert_called_once()

    def select_material(self, win, name):
        model = win.r_preset.get_model()
        win.r_preset.set_selected([model.get_string(i) for i in range(model.get_n_items())].index(name))

    def test_choosing_a_material_applies_it_and_saving_updates_that_material(self):
        with tempfile.TemporaryDirectory() as folder, \
             mock.patch.object(config, "CONFIG_DIR", Path(folder)), \
             mock.patch.object(presets, "factory_presets", return_value={}):
            win = self.make_window()
            pages = win.stack.get_pages()
            self.assertEqual([pages.get_item(i).get_name() for i in range(pages.get_n_items())],
                             ['layout', 'sheet', 'cut'])
            presets.save_blade_settings(presets.STARTING_NAME, new_name="Sample", force=16,
                                        speed=5, depth=None, passes=2)
            win.refresh_preset_choices()
            win._load_materials()
            self.assertEqual(win.job.settings.force, 25)
            self.assertIn("Card Maker guide", win.material_note.get_label())
            self.select_material(win, "Sample")
            self.assertEqual(win.job.settings.force, 16)
            self.assertTrue(win.save_preset_btn.get_sensitive())
            win.r_force.set_value(18)
            win.on_save_blade_preset()
            self.assertEqual(presets.get("Sample").cut_settings().force, 18)
            win.on_save_blade_preset(new_name="Copy of Sample")
            self.assertEqual(win._text(win.r_preset), "Copy of Sample")
            self.assertEqual(win.job.preset.name, "Copy of Sample")

    def test_delete_acts_on_the_chosen_material_and_only_custom_ones(self):
        win = self.make_window()
        win._load_materials()
        self.assertFalse(win.delete_preset_btn.get_sensitive(), "built-in presets cannot be deleted")

        with tempfile.TemporaryDirectory() as folder, \
             mock.patch.object(presets, "user_dir", return_value=Path(folder)):
            custom = presets.save_blade_settings(
                presets.STARTING_NAME, force=18, speed=10, depth=2, passes=1,
                new_name="Custom GTK Test")
            win.refresh_preset_choices()
            win._load_materials()
            self.select_material(win, "Custom GTK Test")
            self.assertEqual(win.job.settings.force, 18)
            self.assertTrue(win.delete_preset_btn.get_sensitive())
            self.assertIn("Custom GTK Test", win.delete_preset_btn.get_label())

            with mock.patch("gi.repository.Adw.AlertDialog.present",
                            lambda dialog, parent: dialog.emit("response", "delete")):
                win.on_delete_selected_preset()
            self.assertFalse(custom.exists())
            self.assertEqual(win._text(win.r_preset), presets.STARTING_NAME)
            self.assertFalse(win.delete_preset_btn.get_sensitive())

    def test_a_running_job_locks_the_material_and_its_library(self):
        win = self.make_window()
        with tempfile.TemporaryDirectory() as folder, \
             mock.patch.object(presets, "user_dir", return_value=Path(folder)):
            custom = presets.save_blade_settings(presets.STARTING_NAME, force=18, speed=10, depth=2,
                                                 passes=1, new_name="Locked GTK Test")
            win.refresh_preset_choices()
            win._load_materials()
            self.select_material(win, "Locked GTK Test")
            self.assertTrue(all(b.get_sensitive() for b in win.material_buttons()),
                            "idle: every material action is available")

            win._sending = True
            win.lock_job_controls(True)
            self.assertFalse(win.r_preset.get_sensitive())
            self.assertFalse(any(b.get_sensitive() for b in win.material_buttons()))
            with mock.patch("gi.repository.Adw.AlertDialog.present",
                            lambda dialog, parent: dialog.emit("response", "delete")), \
                 mock.patch.object(presets, "save_blade_settings") as save:
                win.on_delete_selected_preset()
                win.on_save_blade_preset()
                win.on_restore_default_materials()
            save.assert_not_called()
            self.assertTrue(custom.exists(), "a running job's material is not deleted")

            win._sending = False
            win.lock_job_controls(False)
            self.assertTrue(all(b.get_sensitive() for b in win.material_buttons()), "unlocked again")

    def test_send_editors_update_the_job_from_the_same_controls(self):
        win = self.make_window()
        win.send_workspace.emit('clicked')
        self.assertEqual(win.stack.get_visible_child_name(), 'cut')
        self.assertEqual(win._text(win.r_preset), presets.STARTING_NAME)
        win.r_passes.set_value(3)
        win.send_sliders['force'].set_value(22)
        self.assertEqual(win.r_passes.get_value(), 3)
        self.assertEqual(win.r_force.get_value(), 22)
        self.assertEqual(win.job.settings.passes, 3)
        self.assertEqual(win.job.settings.force, 22)
        win.r_track.set_active(False)
        self.assertFalse(win.job.settings.track_enhancing)
        cut_job = win.stack.get_child_by_name("cut")
        win.send_advanced.set_expanded(True)  # a collapsed expander detaches its child
        for widget in (win.r_preset, win.save_preset_btn, win.delete_preset_btn, win.r_force):
            self.assertTrue(widget.is_ancestor(cut_job), "material choice and saving live on Cut Job")

    def test_published_template_is_selected_in_layout_and_can_be_cleared(self):
        from cutline import layout_presets

        win = self.make_window()
        position = tuple(layout_presets.available()).index('scm-a4-standard-borderless') + 1
        win.r_layout_preset.set_selected(position)
        self.assertEqual(len(win.job.paths), 9)
        self.assertEqual(win.job.marks.inset_mm, 3.5)
        self.assertFalse(win.r_card.get_sensitive())
        self.assertIn('SCM A4', win.import_row.get_title())
        win.use_generated.emit('clicked')
        self.assertIsNone(win.imported)
        self.assertEqual(win.r_layout_preset.get_selected(), 0)
        self.assertTrue(win.r_card.get_sensitive())

    def test_sticker_workspace_traces_exports_and_loads_paired_geometry(self):
        from cutline import editor_model

        win = self.make_window()
        win.present()
        win.on_edit_template()
        design = win.design
        design.history = editor_model.History(editor_model.Project(215.9, 279.4))
        design.refresh_objects()
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / 'sticker.png'
            make_image(image)
            with mock.patch.object(design, 'file_dialog', side_effect=lambda _title, opened, **_: opened(image)):
                design.place_sticker()
            self.assertTrue(design.trace_button.get_sensitive())
            self.assertFalse(design.project.artwork().paths)
            design.trace_button.emit('clicked')
            dialog = win.get_visible_dialog()
            self.assertIsNotNone(dialog)

            def children(widget):
                yield widget
                current = widget.get_first_child()
                while current is not None:
                    yield from children(current)
                    current = current.get_next_sibling()

            trace = next(widget for widget in children(dialog) if isinstance(widget, gui.Gtk.Button) and
                         widget.get_label() == 'Trace')
            trace.emit('clicked')
            self.assertEqual(len(design.project.artwork().paths), 3)
            self.assertIn('Export Print', design.review.get_label())
            target = Path(folder) / 'stickers.zip'
            with mock.patch.object(design, 'file_dialog', side_effect=lambda _title, opened, **_: opened(target)), \
                 mock.patch.object(gui.threading, 'Thread', side_effect=lambda target, **_: mock.Mock(start=target)):
                design.review.emit('clicked')
                design.send_workspace.emit('clicked')
                with mock.patch.object(gui.Gtk, 'FileDialog') as chooser:
                    win.on_pick_pdf()
                    handle = chooser.return_value
                    handle.open_finish.return_value = gui.Gio.File.new_for_path(str(target))
                    handle.open.call_args.args[2](handle, mock.Mock())
            self.assertEqual(win.stack.get_visible_child_name(), 'sheet')
            self.assertEqual(len(win.job.paths), 3)
            self.assertFalse(win.job.blocked)

    def test_wheel_is_captured_on_settings_and_scrolls_the_pane(self):
        win = self.make_window()
        win.set_default_size(940, 620)
        win.present()
        win.send_workspace.emit('clicked')
        pane = win.stack.get_visible_child()
        context = gui.GLib.MainContext.default()
        for _ in range(100):
            if not context.pending():
                break
            context.iteration(False)
        scroll = pane.get_vadjustment()
        self.assertGreater(scroll.get_upper(), scroll.get_page_size())
        for widget, value in ((win.r_force, win.r_force.get_value), (win.send_sliders['force'], win.r_force.get_value),
                              (win.r_preset, win.r_preset.get_selected)):
            with self.subTest(widget=type(widget).__name__):
                controller = next(ctrl for ctrl in widget.observe_controllers()
                                  if isinstance(ctrl, gui.Gtk.EventControllerScroll) and
                                  ctrl.get_propagation_phase() == gui.Gtk.PropagationPhase.CAPTURE)
                scroll.set_value(0)
                before = value()
                self.assertTrue(controller.emit('scroll', 0.0, 40.0))
                self.assertEqual(value(), before)
                self.assertGreater(scroll.get_value(), 0)

    def test_compound_and_text_reopen_and_review_as_verified_contours(self):
        from cutline import editor_model
        from dataclasses import replace

        win = self.make_window()
        win.present()
        win.on_edit_template()
        design = win.design
        project = compound_project()
        design.commit(project.shapes, (0,))
        design.text_button.emit('clicked')
        dialog = win.get_visible_dialog()
        self.assertIsNotNone(dialog)
        text = dialog.get_child().get_content().get_first_child().get_child()
        text.get_buffer().set_text('O')

        def children(widget):
            yield widget
            child = widget.get_first_child()
            while child is not None:
                yield from children(child)
                child = child.get_next_sibling()

        add = next(widget for widget in children(dialog) if isinstance(widget, gui.Gtk.Button) and
                   widget.get_label() == 'Add')
        add.emit('clicked')
        self.assertEqual(design.text_button.get_tooltip_text(), 'Edit Text…')
        self.assertIsInstance(design.text_button.get_child(), gui.Gtk.DrawingArea)
        self.assertEqual(design.project.shapes[1].text.value, 'O')
        design.commit((design.project.shapes[0], replace(design.project.shapes[1], x=110, y=40)), (1,))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'compounds.scc-template'
            design.write_design(path, None)
            reopened = editor_model.load_project(path)
            with mock.patch.object(design, 'file_dialog', side_effect=lambda _title, opened, **_: opened(path)):
                design.open_project(None)
        self.assertEqual(design.project, reopened)
        design.send_workspace.emit('clicked')
        self.assertFalse(win.job.blocked)
        self.assertCountEqual(win.job.paths, reopened.artwork().paths)
        self.assertEqual(len(win.job.paths), 4)

    def test_manual_depth_is_readable(self):
        win = self.make_window()
        win.send_sliders["depth"].set_value(-1)
        self.assertEqual(win.r_depth.get_text(), "Manual")
        self.assertGreaterEqual(win.r_depth.get_width_chars(), len(win.r_depth.get_text()))
        self.assertIsNone(win.job.settings.depth)

    def test_workspace_menus_open_send_settings_and_run_design_commands(self):
        import cairo

        win = self.make_window()
        win.workspace_actions.lookup_action("advanced-settings").activate(None)
        self.assertEqual(win.stack.get_visible_child_name(), "cut")
        self.assertTrue(win.send_advanced.get_expanded())
        image = cairo.ImageSurface(cairo.FORMAT_RGB24, 480, 320)
        with mock.patch.object(gui, "draw_feed_arrow", wraps=gui.draw_feed_arrow) as draw:
            win.preview._draw(win.preview, cairo.Context(image), 480, 320)
        self.assertEqual(draw.call_args.args[1], win.job.grid.sheet_w_mm)
        win.on_edit_template()
        design = win.design
        self.assertIsNotNone(design.menu_actions.lookup_action("save"))
        design.menu_actions.lookup_action("select-all").activate(None)
        self.assertEqual(len(design.selected_indices()), len(design.project.shapes))

    def test_connection_method_defaults_to_automatic_inside_advanced_options(self):
        win = self.make_window()
        self.assertEqual(win._connection_mode(), "auto")
        self.assertFalse(win.send_advanced.get_expanded())
        self.assertTrue(win.r_connection.is_ancestor(win.send_advanced.get_child()))
        self.assertFalse(win.connection_status.is_ancestor(win.send_advanced.get_child()))
        win.send_advanced.set_expanded(True)
        self.assertFalse(win.r_bluetooth_id.get_visible())
        self.assertFalse(win.r_bluetooth_channel.get_visible())
        win.r_connection.set_selected(1)
        self.assertEqual(win._connection_mode(), "ble")
        self.assertTrue(win.r_bluetooth_id.get_visible())
        self.assertFalse(win.r_bluetooth_channel.get_visible())
        win.r_connection.set_selected(2)
        self.assertEqual(win._connection_mode(), "bluetooth")
        self.assertTrue(win.r_bluetooth_channel.get_visible())
        win.send_advanced.set_expanded(False)
        self.assertEqual(win._connection_mode(), "bluetooth")

    def test_invalid_bundle_row_can_be_left_without_switching_to_a_generated_cut(self):
        from cutline.editor_model import Project, Shape

        win = self.make_window()
        self.assertFalse(win.use_generated.get_sensitive())
        rows = [dict(sheet=n, frontPage=n, svg=f"sheet-{n}.svg") for n in (1, 2)]
        drawing = Project(279.4, 215.9, (Shape("Rectangle", 40, 40),)).artwork("Cut")
        matched = mock.Mock(rows=rows, layout={"sheet": "Letter", "portrait": False})
        matched.mat_placement.return_value = "Load the print face-up."
        matched.artwork.side_effect = [drawing, gui.bundle.BundleError("Unreadable SVG"), drawing]
        with mock.patch.object(gui.bundle, "load", return_value=matched), \
             mock.patch.object(gui.Gtk, "FileDialog") as chooser, \
             mock.patch.object(win, "measure_sheet", wraps=win.measure_sheet) as measure, \
             mock.patch.object(gui.threading, "Thread"):
            win.on_pick_pdf()
            dialog = chooser.return_value
            dialog.open_finish.return_value = gui.Gio.File.new_for_path("/tmp/project.zip")
            dialog.open.call_args.args[2](dialog, mock.Mock())
            self.assertEqual(win.stack.get_visible_child_name(), 'sheet')
            self.assertTrue(win.use_generated.get_sensitive())
            win.r_sheet_page.set_value(2)
            self.assertIs(win.pdf_bundle, matched)
            self.assertIsNone(win.imported)
            self.assertIsNone(win.job)
            self.assertEqual(win.sheet_result.get_subtitle(), "Unreadable SVG")
            win.send_workspace.emit("clicked")
            self.assertEqual(win.stack.get_visible_child_name(), "cut")
            self.assertTrue(win.cut_button.get_visible())
            self.assertFalse(win.cut_button.get_sensitive())
            win.review_send.emit("clicked")
            win.r_sheet_page.set_value(1)
            self.assertIs(win.imported, drawing)
            self.assertTrue(win._sheet_token)
            self.assertGreaterEqual(measure.call_count, 2)
            win.close_print.emit("clicked")
            self.assertIsNone(win.pdf_path)
            self.assertIsNone(win.pdf_bundle)
            self.assertTrue(win.r_sheet.get_sensitive())

    def test_material_selection_resolves_the_active_holder_variant(self):
        variants = {f"Sample · Holder {slot}": presets.Preset(
            f"Sample · Holder {slot}", "", "silhouette", {"force": force},
            factory_id=str(slot), factory_group="sample", factory_holder=slot)
            for slot, force in ((1, 12), (2, 21))}
        win = self.make_window()
        with mock.patch.object(presets, "factory_presets", return_value=variants):
            win.refresh_preset_choices()
            win.send_holder_buttons[2].set_active(True)
            names = win.r_preset.get_model()
            win.r_preset.set_selected(next(i for i in range(names.get_n_items())
                                               if names.get_string(i) == "Sample · Holder 1"))
            self.assertEqual(win._text(win.r_preset), "Sample · Holder 2")
            self.assertEqual(win.job.settings.force, 21)
            self.assertEqual(win.job.settings.holder, 2)

    def test_advanced_controls_and_tool_profiles_are_independent(self):
        from cutline.editor_model import Shape

        win = self.make_window()
        win.send_workspace.emit('clicked')
        self.assertFalse(win.send_advanced.get_expanded())
        self.assertTrue(win.corner_button.get_visible())
        win.send_sliders['force'].set_value(28)
        win.send_holder_buttons[2].set_active(True)
        self.assertFalse(win.r_depth.get_sensitive())
        win.send_sliders['force'].set_value(12)
        win.send_holder_buttons[1].set_active(True)
        self.assertEqual(win.send_sliders['force'].get_value(), 28)
        self.assertEqual(win.job.settings.holder, 1)
        win.send_advanced.set_expanded(True)
        win.r_end_position.set_selected(list(graphtec.END_POSITIONS).index('start'))
        win.send_advanced.set_expanded(False)
        self.assertEqual(win.job.end_position, 'start')

        win.on_edit_template()
        design = win.design
        design.commit((Shape('Rectangle', 30, 40), Shape('Line', 95, 40)), (0,))
        design.menu_actions.lookup_action('holder-1').activate(None)
        design.set_selection((1,))
        design.menu_actions.lookup_action('holder-2').activate(None)
        design.menu_actions.lookup_action('registration').activate(None)
        self.assertTrue(design.page_advanced.get_expanded())
        design.menu_actions.lookup_action('print-border').activate(None)
        self.assertTrue(design.print_border.get_active())
        design.send_workspace.emit('clicked')
        self.assertFalse(win.job.blocked)
        self.assertEqual([(group.settings.holder, group.settings.force) for group in win.job.action_groups],
                         [(1, 28), (2, 12)])

    def test_material_guide_requires_a_selected_cutter_to_send(self):
        win = self.make_window()
        win.open_material_guide()
        self.assertTrue(win.guide_send.get_sensitive())

        win.r_bluetooth.set_selected(0)
        self.assertFalse(win.guide_send.get_sensitive())
        self.assertIn("select a cutter", win.guide_status.get_subtitle())

        with mock.patch.object(win, "on_refresh_status"):
            win.r_bluetooth.set_selected(1)
        self.assertTrue(win.guide_send.get_sensitive())
        self.assertNotIn("select a cutter", win.guide_status.get_subtitle())

    def test_material_guide_shape_and_size_controls_move_to_fresh_stock(self):
        from cutline import testcut

        win = self.make_window()
        win.open_material_guide()
        self.assertTrue(win.guide_registration.get_sensitive())
        small = len(win.preview.test_grid)
        win.guide_shape.set_selected(tuple(testcut.TEST_SHAPES).index("rounded-square"))
        win.guide_size.set_selected(tuple(starting_guide.SAMPLE_SIZES).index(50.0))
        self.assertLess(len(win.preview.test_grid), small)
        self.assertEqual(win.preview.trial.sheet.shape, "rounded-square")
        self.assertEqual(win.preview.trial.sheet.size_mm, 50.0)
        chosen, box = max(win.preview.test_grid.items())
        self.assertTrue(win.on_preview_spot_clicked(box.x + 2, box.y + 2))
        self.assertEqual(win.guide.spot, chosen)
        self.assertEqual(win.preview.trial.sheet.cells[0].rect, box)
        win.guide.record(win.preview.trial)
        win.update_guide()
        self.assertFalse(win.guide_registration.get_sensitive())
        win.guide_shape.set_selected(tuple(testcut.TEST_SHAPES).index("triangle"))
        self.assertNotEqual(win.guide.spot, chosen)
        self.assertEqual(win.guide.spot_rect(chosen), box)
        self.assertEqual(win.preview.trial.sheet.shape, "triangle")

    def test_material_guide_shows_fresh_spots_and_restores_the_cut_preview(self):
        win = self.make_window()
        win.preview.zoom_by(1.25)
        self.assertEqual(win.preview_zoom.get_text(), '125%')
        win.r_speed.set_value(18)
        self.assertEqual(win.preview_zoom.get_text(), '125%')
        win.preview.fit()
        self.assertEqual(win.preview_zoom.get_text(), '100%')

        original = win.job
        win.open_material_guide()
        first = win.preview.trial.sheet.extent()
        self.assertEqual(win.preview_title.get_text(), 'Material test sheet')
        self.assertFalse(win.cut_button.get_visible())
        self.assertFalse(win.scan_button.get_visible())
        self.assertFalse(win.corner_button.get_visible())
        self.assertTrue(win.cut_settings.get_visible())
        self.assertFalse(win.send_cutter_group.get_visible())
        self.assertFalse(win.send_advanced.get_visible())

        win.guide_next.emit('clicked')
        self.assertFalse(win.preview.trial.sheet.extent().overlaps(first))
        self.assertEqual(int(win.guide_spot_selector.get_value()), 2)

        win.guide_spot_selector.set_value(1)
        self.assertEqual(win.guide.spot, 1)

        spot2_rect = win.guide.spot_rect(2)
        self.assertTrue(win.preview.on_spot_clicked(spot2_rect.x + 2, spot2_rect.y + 2))
        self.assertEqual(win.guide.spot, 2)
        self.assertEqual(int(win.guide_spot_selector.get_value()), 2)

        self.assertFalse(win.material_guide.get_visible(), 'no way back into a test already open')
        win.back_to_send.emit('clicked')
        self.assertTrue(win.material_guide.get_visible())
        self.assertEqual(win.material_guide.get_label(), 'Resume Material Test')
        win.material_guide.emit('clicked')
        self.assertTrue(win.tuning)

        # Changing cut settings while active does not silently advance spots
        win.r_force.set_value(28)
        self.assertEqual(win.guide.spot, 2)

        win.back_to_send.emit('clicked')
        self.assertIsNone(win.preview.trial)
        self.assertTrue(win.cut_button.get_visible())
        self.assertTrue(win.scan_button.get_visible())
        self.assertTrue(win.corner_button.get_visible())
        self.assertTrue(win.send_cutter_group.get_visible())
        self.assertEqual(win.job.settings.force, 28)

    def test_design_page_setup_preserves_custom_page_mat_and_marks_in_send(self):
        from cutline.editor_model import Shape

        win = self.make_window()
        win.design_button.emit('clicked')
        design = win.design
        design.commit((Shape('Rectangle', x=35, y=43, width=20, height=30),), (0,))
        design.page.set_selected(design.page_sizes.index('Custom'))
        design.page_width.set_value(150)
        design.page_height.set_value(180)
        design.mat.set_selected(list(graphtec.MAT_OPTIONS).index('cameo-12x12'))
        design.mark_inset.set_value(14)
        design.mark_length.set_value(9)
        self.assertEqual(design.mat_size, (304.8, 304.8))
        self.assertEqual(design.cut_border.x, 14)
        design.send_workspace.emit('clicked')
        self.assertEqual(win.stack.get_visible_child_name(), 'cut')
        self.assertEqual((win.job.grid.sheet_w_mm, win.job.grid.sheet_h_mm), (150, 180))
        self.assertEqual(win.job.mat, 'cameo-12x12')
        self.assertEqual((win.job.marks.inset_mm, win.job.marks.length_mm), (14, 9))
        self.assertEqual(win.job.blocked, [])

    def test_design_icon_palette_tracks_canvas_shortcuts_and_object_keys(self):
        from gi.repository import Gdk

        win = self.make_window()
        win.design_button.emit('clicked')
        design = win.design
        palette = design.tool
        for button, name in ((design.save_button, 'Save'), (design.undo_button, 'Undo'),
                             (design.redo_button, 'Redo'), (design.add_button, 'Add Shape'),
                             (design.text_button, 'Add Text…'), (design.trace_button, 'Trace and Bleed…')):
            self.assertTrue(button.get_tooltip_text().startswith(name))
            self.assertIsInstance(button.get_child(), gui.Gtk.DrawingArea)
        self.assertEqual(palette.get_orientation(), gui.Gtk.Orientation.VERTICAL)
        no_modifiers = Gdk.ModifierType(0)
        rounded = palette.button_for('Rounded rectangle')
        self.assertTrue(design.canvas_key(None, Gdk.KEY_u, 0, no_modifiers))
        self.assertIn('(U)', rounded.get_tooltip_text())
        self.assertTrue(rounded.get_active())
        self.assertEqual(palette.active_label.get_label(), 'Rounded rectangle')
        design.add_button.emit('clicked')
        self.assertEqual(design.project.shapes[-1].kind, 'Rounded rectangle')

        select = palette.slots['Select']
        select.emit('clicked')
        self.assertTrue(select.get_active())
        self.assertFalse(rounded.get_active())
        self.assertEqual(palette.active_label.get_label(), 'Select')
        start = design.project.shapes[-1].x
        self.assertTrue(design.canvas_key(None, Gdk.KEY_Right, 0, no_modifiers))
        self.assertEqual(design.project.shapes[-1].x, start + 1)
        count = len(design.project.shapes)
        self.assertTrue(design.canvas_key(None, Gdk.KEY_Delete, 0, no_modifiers))
        self.assertEqual(len(design.project.shapes), count - 1)

    def test_transform_flyout_scales_and_remembers_tool(self):
        from gi.repository import Gdk

        win = self.make_window()
        win.present()
        win.design_button.emit('clicked')
        design = win.design
        shape = editor_model.Shape('Rounded rectangle', 50, 60, 20, 30, radius=3)
        design.commit((shape,), (0,))
        palette = design.tool
        design.option_snap.set_active(True)
        self.assertTrue(design.snap.get_active())
        design.snap.set_active(False)
        self.assertFalse(design.option_snap.get_active())
        palette.menu_buttons['Transform tools'].popup()
        self.assertTrue(palette.menus['Transform tools'].get_visible())
        palette.choices['Scale'].emit('clicked')
        self.assertEqual(design.active_tool, 'Scale')
        self.assertTrue(design.quick_transforms.get_visible())
        self.assertFalse(design.option_snap.get_visible())
        self.assertEqual(design.panel_switcher.get_selected(), 1)
        palette.slots['Select'].emit('clicked')
        palette.slots['Transform tools'].emit('clicked')
        self.assertEqual(design.active_tool, 'Scale')
        self.assertFalse(palette.menus['Transform tools'].get_visible())

        gesture = mock.Mock()
        gesture.get_current_event_state.return_value = Gdk.ModifierType(0)
        depth = len(design.history.past)
        with mock.patch.object(design.canvas, 'viewport', return_value=(0, 0, 2)):
            design.drag_begin(gesture, 140, 180)
            design.drag_update(gesture, 40, 60)
            self.assertEqual(design.history.current.shapes, (shape,))
            self.assertEqual(design.project.shapes[0].width, 40)
            design.drag_end(gesture, 40, 60)
        scaled = design.project.shapes[0]
        self.assertEqual((scaled.x, scaled.y, scaled.width, scaled.height, scaled.radius), (50, 60, 40, 60, 6))
        self.assertEqual(len(design.history.past), depth + 1)
        design.undo_button.emit('clicked')
        self.assertEqual(design.project.shapes, (shape,))
        palette.choices['Rotate'].emit('clicked')
        gesture.get_current_event_state.return_value = Gdk.ModifierType.SHIFT_MASK
        with mock.patch.object(design.canvas, 'viewport', return_value=(0, 0, 2)):
            design.drag_begin(gesture, 132, 134)
            design.drag_end(gesture, 4, 28)
        self.assertEqual(design.project.shapes[0].rotation, 90)
        design.undo_button.emit('clicked')
        self.assertEqual(design.project.shapes, (shape,))
        design.menu_actions.lookup_action('panels-toggle').activate(None)
        self.assertFalse(design.dock.get_visible())
        self.assertFalse(palette.get_visible())
        design.menu_actions.lookup_action('panels-toggle').activate(None)
        self.assertTrue(design.dock.get_visible())
        self.assertEqual(design.active_tool, 'Rotate')

    def test_transform_tool_previews_and_commits_snapped_rotation(self):
        from gi.repository import Gdk
        from cutline.design_tools import rotation_handle

        win = self.make_window()
        win.design_button.emit('clicked')
        design = win.design
        shapes = (editor_model.Shape('Rectangle', 50, 60, 20, 20),
                  editor_model.Shape('Rectangle', 90, 80, 20, 20))
        design.commit(shapes, (0, 1))
        self.assertTrue(design.canvas_key(None, Gdk.KEY_t, 0, Gdk.ModifierType(0)))
        self.assertEqual(design.active_tool, 'Transform')
        self.assertEqual(design.panel_switcher.get_selected(), 1)
        self.assertTrue(design.alignment_buttons['Align Left'].get_sensitive())
        self.assertFalse(design.alignment_buttons['Space Horizontally'].get_sensitive())
        handle = rotation_handle(shapes, (0, 1), 2)
        x, y = handle.positions()[1]
        end = editor_model.rotate_point((x, y), handle.center, 42)
        delta = (2 * (end[0] - x), 2 * (end[1] - y))
        gesture = mock.Mock()
        gesture.get_current_event_state.return_value = Gdk.ModifierType.SHIFT_MASK
        depth = len(design.history.past)
        with mock.patch.object(design.canvas, 'viewport', return_value=(0, 0, 2)):
            design.drag_begin(gesture, 2*x, 2*y)
            design.drag_update(gesture, *delta)
            self.assertEqual([shape.rotation for shape in design.project.shapes], [45, 45])
            self.assertEqual(design.history.current.shapes, shapes)
            self.assertTrue(design.canvas_key(None, Gdk.KEY_Escape, 0, Gdk.ModifierType(0)))
            self.assertEqual(design.project.shapes, shapes)
            self.assertEqual(design.selected_indices(), (0, 1))
            design.drag_begin(gesture, 2*x, 2*y)
            design.drag_update(gesture, *delta)
            design.drag_end(gesture, 0, 0)
            self.assertEqual(design.project.shapes, shapes)
            self.assertEqual(len(design.history.past), depth)
            design.drag_begin(gesture, 2*x, 2*y)
            design.drag_end(gesture, *delta)
        self.assertEqual([shape.rotation for shape in design.project.shapes], [45, 45])
        self.assertEqual(len(design.history.past), depth + 1)
        design.undo_button.emit('clicked')
        self.assertEqual(design.project.shapes, shapes)
        design.transform_buttons['right'].emit('clicked')
        self.assertEqual([shape.rotation for shape in design.project.shapes], [90, 90])
        design.undo_button.emit('clicked')
        design.alignment_buttons['Align Left'].emit('clicked')
        self.assertEqual([shape.x for shape in design.project.shapes], [50, 50])

    def test_design_drawing_points_selection_and_replicate_enter_history(self):
        from cutline.editor_model import Shape
        from gi.repository import Gdk

        win = self.make_window()
        win.design_button.emit('clicked')
        design = win.design
        design.commit((), ())
        gesture = mock.Mock()
        gesture.get_current_event_state.return_value = Gdk.ModifierType(0)
        canvas = design.canvas
        with mock.patch.object(canvas, 'viewport', return_value=(0, 0, 2)):
            design.tool.set_selected(design.tools.index('Rectangle'))
            design.drag_begin(gesture, 120, 100)
            design.drag_end(gesture, 20, 30)
            self.assertEqual(design.project.shapes, (Shape('Rectangle', 60, 50, 10, 15),))
            design.tool.set_selected(design.tools.index('Polygon'))
            for x, y in ((180, 100), (220, 100), (220, 160)):
                design.pick_polygon_point(gesture, 1, x, y)
            design.finish_polygon()
            self.assertEqual(design.project.shapes[1].path().points, ((90, 50), (110, 50), (110, 80)))
            design.tool.set_selected(design.tools.index('Point Edit'))
            design.drag_begin(gesture, 180, 100)
            design.drag_end(gesture, 0, 20)
            self.assertEqual(design.project.shapes[1].path().points[0], (90, 60))
            design.tool.set_selected(0)
            design.drag_begin(gesture, 80, 60)
            design.drag_end(gesture, 160, 120)
        self.assertEqual(design.selected_indices(), (0, 1))
        design.group_button.emit('clicked')
        self.assertIsNotNone(design.project.shapes[0].group)
        self.assertEqual(design.project.shapes[0].group, design.project.shapes[1].group)
        design.rows.set_value(1)
        design.replicate_button.emit('clicked')
        self.assertEqual(len(design.project.shapes), 4)
        design.undo_button.emit('clicked')
        self.assertEqual(len(design.project.shapes), 2)

    def test_switch_preserves_design_and_send_requires_review_of_new_edits(self):
        from dataclasses import replace

        win = self.make_window()
        self.assertTrue(win.review_send.get_visible())
        win.review_send.emit('clicked')
        self.assertTrue(win.cut_button.get_visible())
        win.on_edit_template()
        design = win.design
        design.commit((replace(design.project.shapes[0], x=40),), (0,))
        design.send_workspace.emit('clicked')
        self.assertTrue(win.cut_button.get_sensitive())
        win.on_edit_template()
        design.commit((replace(design.project.shapes[0], x=45),), (0,))
        win.leave_design()
        self.assertEqual(win.stack.get_visible_child_name(), 'cut')
        self.assertEqual(win.review_send.get_label(), 'Review Design')
        self.assertFalse(win.cut_button.get_sensitive())
        self.assertFalse(win.scan_button.get_sensitive())
        win.review_send.emit('clicked')
        self.assertIs(win.design, design)
        self.assertEqual(design.project.shapes[0].x, 45)
        self.assertTrue(design.dirty)
        design.send_workspace.emit('clicked')
        self.assertTrue(win.cut_button.get_sensitive())

    def test_switching_to_send_sends_the_edited_design_like_studio(self):
        win = self.make_window()
        generated = len(win.job.paths)
        win.on_edit_template()
        design = win.design
        design.send_workspace.emit('clicked')
        self.assertEqual(len(win.job.paths), generated, "a design only looked at leaves Send alone")
        win.on_edit_template()
        cut = editor_model.Shape("Rectangle", 30, 40)
        score = editor_model.Shape("Line", 80, 40, action="Score")
        design.commit((cut, score), (0, 1))
        design.send_workspace.emit('clicked')
        self.assertEqual(win.workspaces.get_visible_child_name(), 'tools')
        self.assertEqual(win.stack.get_visible_child_name(), 'cut')
        self.assertEqual(len(win.job.paths), 2)
        groups = [key for key, *_ in win._send_rows]
        self.assertEqual(sorted(groups), ["Cut", "Score"], "mixed actions get their own groups")
        self.assertNotIn("design", win.send_status.get_label().lower())

    def test_newly_opened_send_artwork_is_not_replaced_by_an_older_design(self):
        win = self.make_window()
        win.on_edit_template()
        win.design.commit((editor_model.Shape("Rectangle", 30, 40),), (0,))
        win.design.send_workspace.emit('clicked')
        self.assertEqual(len(win.job.paths), 1)
        win.on_clear_artwork()
        generated = len(win.job.paths)
        self.assertGreater(generated, 1)
        win.on_edit_template()
        win.design.send_workspace.emit('clicked')
        self.assertEqual(len(win.job.paths), generated)

    def test_an_unusable_design_stays_in_design_with_the_reason(self):
        win = self.make_window()
        win.on_edit_template()
        design = win.design
        with mock.patch.object(editor_model, "send_handoff",
                               return_value=("invalid", "Draw or resize the paths before sending.")):
            design.send_workspace.emit('clicked')
        self.assertIs(win.workspaces.get_visible_child(), design.view)
        self.assertIn("resize the paths", design.status.get_text())

    def test_send_refuses_a_design_edit_it_has_not_used(self):
        win = self.make_window()
        win.on_edit_template()
        design = win.design
        design.commit((editor_model.Shape("Rectangle", 30, 40),), (0,))
        design.send_workspace.emit('clicked')
        self.assertNotIn("has not used", win.send_status.get_label())
        design.commit((editor_model.Shape("Ellipse", 30, 40),), (0,))  # edited without switching
        win.update_send_actions()
        self.assertIn("has not used", win.send_status.get_label())

    def test_save_updates_the_open_project_without_opening_the_picker(self):
        from cutline.editor_model import load_project

        win = self.make_window()
        win.on_edit_template()
        design = win.design
        with tempfile.TemporaryDirectory() as folder:
            dest = Path(folder) / 'cards.scc-template'
            design.write_design(dest, None)
            design.commit((), ())
            self.assertTrue(design.dirty)
            with mock.patch.object(design, 'file_dialog') as picker:
                design.save(None)
            self.assertEqual(picker.call_count, 0)
            self.assertFalse(design.dirty)
            self.assertEqual(load_project(dest), design.project)
            self.assertEqual(design.document_title.get_text(), dest.name)

    def test_zoom_keeps_the_point_under_the_pointer_and_fit_resets_the_view(self):
        win = self.make_window()
        win.on_edit_template()
        design = win.design
        canvas = design.canvas
        with mock.patch.object(canvas, 'get_width', return_value=700), \
             mock.patch.object(canvas, 'get_height', return_value=450):
            anchor = (204, 135)
            x, y, scale = canvas.viewport(700, 450)
            location = (anchor[0] - x) / scale, (anchor[1] - y) / scale
            canvas.zoom_by(1.25, anchor)
            x, y, scale = canvas.viewport(700, 450)
            self.assertAlmostEqual(x + location[0] * scale, anchor[0])
            self.assertAlmostEqual(y + location[1] * scale, anchor[1])
            self.assertEqual(design.zoom_readout.get_label(), '125%')
            design.fit(None)
        self.assertEqual((canvas.pan, canvas.zoom), ((0, 0), 1))
        self.assertEqual(design.zoom_readout.get_label(), '100%')

    def test_sheet_measurement_survives_blade_edits_but_not_registration_changes(self):
        win = self.make_window()
        win.pdf_path = Path('sample.pdf')
        report = win.sheet_report = object()
        token = win._sheet_token = object()
        win.checked_marks = win.job.marks
        win.r_force.set_value(win.r_force.get_value() - 1)
        self.assertIs(win.sheet_report, report)
        self.assertIs(win._sheet_token, token)
        win.r_inset.set_value(win.r_inset.get_value() + 1)
        self.assertIsNone(win.sheet_report)
        self.assertIsNone(win._sheet_token)

    def test_preview_separates_cut_paths_from_print_and_restores_default_without_a_pdf(self):
        import cairo
        import io

        win = self.make_window()
        preview = win.preview
        png = io.BytesIO()
        print_image = cairo.ImageSurface(cairo.FORMAT_RGB24, 50, 50)
        context = cairo.Context(print_image)
        context.set_source_rgb(0.02, 0.62, 0.91)
        context.paint()
        print_image.write_to_png(png)
        preview.set_pdf(png.getvalue(), 279.4, 215.9)
        self.assertTrue(win.preview_mode.get_visible())

        def render(mode):
            win.preview_mode.set_selected(gui.PREVIEW_MODES.index(mode))
            out = cairo.ImageSurface(cairo.FORMAT_RGB24, 320, 220)
            preview._draw(preview, cairo.Context(out), 320, 220)
            return bytes(out.get_data())

        cut = render('Cut paths')
        printed = render('Print PDF')
        overlay = render('Overlay')
        self.assertNotEqual(cut, printed)
        self.assertNotEqual(overlay, printed)
        preview.set_pdf()
        self.assertFalse(win.preview_mode.get_visible())
        self.assertEqual(render('Overlay'), cut)

    def test_send_confirmation_is_invalidated_if_the_cutter_changes(self):
        win = self.make_window()
        checked = win.job
        old_connection = win._connection()
        win.r_connection.set_selected(1)
        self.assertFalse(win.cut_button.get_sensitive())
        self.assertIn('Select a Bluetooth cutter', win.send_status.get_label())
        win.r_bluetooth_id.set_text('AB:00:00:00:00:01')
        self.assertTrue(win.cut_button.get_sensitive())
        with mock.patch.object(win, 'toast') as warning, \
             mock.patch.object(gui.transport, 'open_transport') as open_transport:
            win._cut_response(None, 'cut', checked, old_connection)
            self.assertEqual(open_transport.call_count, 0)
            self.assertIn('connection changed', warning.call_args.args[0])

    def test_staged_passes_open_from_a_switch_under_passes(self):
        win = self.make_window()
        self.assertFalse(win.r_stage.get_enable_expansion())
        self.assertEqual(win._blade_options()["light_passes"], 0)
        win.r_passes.set_value(1)
        win.r_stage.set_enable_expansion(True)
        self.assertTrue(win.r_stage.get_expanded())
        self.assertEqual(win.r_passes.get_value(), 2, "room for a full pass after the light one")
        for key, value in (("light_force", 10), ("light_speed", 3)):
            win.stage_fields[key].set_value(value)
        win.r_force.set_value(20)
        win.r_speed.set_value(5)
        options = win._blade_options()
        self.assertEqual((options["light_passes"], options["light_force"], options["light_speed"]), (1, 10, 3))
        self.assertEqual(win.stage_note.get_label(), "Pass 1 at force 10, speed 3; pass 2 at force 20, speed 5.")
        self.assertEqual([(g.settings.passes, g.settings.force) for g in win.job.action_groups], [(1, 10), (1, 20)])
        win.r_stage.set_enable_expansion(False)
        self.assertEqual(win._blade_options()["light_passes"], 0)
        self.assertEqual(win.job.action_groups, ())

    def test_the_material_test_compares_two_settings_and_hides_pass_counts(self):
        win = self.make_window()
        win.r_force.set_value(10)
        win.open_material_guide()
        for field in ("force", "speed", "passes", "depth"):
            self.assertFalse(win.send_rows[field].get_visible())
        self.assertFalse(win.r_stage.get_visible())
        a, b = win.guide_headers["A"], win.guide_headers["B"]
        self.assertTrue(a.get_expanded())
        self.assertEqual(win.guide_fields["B"]["force"].get_value(), 10, "both start from Cut Settings")
        b.set_expanded(True)
        self.assertTrue(a.get_expanded() and b.get_expanded(), "each opens on its own")
        win.guide_fields["B"]["force"].set_value(28)
        self.assertEqual(win.r_force.get_value(), 10, "editing B leaves the chosen A alone")
        self.assertEqual(win.guide_pass_name, "A")
        win.guide_slot_choice.set_selected(1)
        self.assertEqual(win.guide_pass_name, "B")
        self.assertEqual(win.r_force.get_value(), 28)
        win.guide_slot_choice.set_selected(0)
        self.assertEqual(win.r_force.get_value(), 10)
        win.back_to_send.emit('clicked')
        self.assertTrue(win.send_rows["passes"].get_visible())
        self.assertTrue(win.r_stage.get_visible())

    def test_the_app_reopens_on_the_material_used_last(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(config, "CONFIG_DIR", Path(folder)):
            win = self.make_window()
            model = win.r_preset.get_model()
            names = [model.get_string(i) for i in range(model.get_n_items())]
            other = next(name for name in names if name != config.DEFAULT_PRESET)
            win.r_preset.set_selected(names.index(other))
            self.assertEqual(config.load_settings()["last_preset"], other)
            self.assertEqual(win._text(self.make_window().r_preset), other)
            config.save_settings(dict(config.load_settings(), last_preset="A material since deleted"))
            self.assertEqual(win._text(self.make_window().r_preset), config.DEFAULT_PRESET)

    def test_starting_guide_applies_settings_and_keeps_its_own_pass_count(self):
        win = self.make_window()
        win.open_material_guide()
        win.guide_apply.emit('clicked')
        self.assertEqual((win.r_force.get_value(), win.r_speed.get_value(), win.r_depth.get_value(),
                          win.r_passes.get_value()), (25, 25, 7, 1))
        trial = win.guide_proposal()
        fake = mock.MagicMock()
        fake.__enter__.return_value = fake
        fake.can_read = True
        fake.status.return_value = 'ready'

        def synchronous_thread(*, target, daemon):
            return types.SimpleNamespace(start=target)

        with mock.patch.object(gui.threading, 'Thread', side_effect=synchronous_thread), \
             mock.patch.object(gui.GLib, 'idle_add', side_effect=lambda callback, *args: callback(*args)), \
             mock.patch.object(gui.transport, 'open_transport', return_value=fake), \
             mock.patch.object(gui.Adw, 'AlertDialog') as dialog:
            win.guide_send.emit('clicked')
            _, response, proposal = dialog.return_value.connect.call_args.args
            response(None, 'cut', proposal)
            self.assertTrue(win.guide_send.get_sensitive())
            win.guide_send.emit('clicked')
            self.assertEqual(fake.run.call_args_list, [mock.call(trial.sheet.steps, wait_done=True)] * 2)
            self.assertIn('2 completed passes', win.guide_status.get_title())
            self.assertTrue(win.guide_use.get_sensitive())
            with mock.patch.object(win, 'offer_tested_preset') as prompt:
                win.guide_use.emit('clicked')
            prompt.assert_called_once()
            self.assertEqual(win.r_passes.get_value(), 2)

            # Settings differ on an already-cut spot: updates status and repeats with new settings.
            win.guide_spot_selector.set_value(1)
            self.assertEqual(win.guide.spot, 1)
            win.r_force.set_value(win.r_force.get_value() + 5)
            self.assertIn('changed settings', win.guide_status.get_subtitle())
            self.assertEqual(win.guide_send.get_label(), 'Cut One Pass…')
            sent = len(fake.run.call_args_list)
            win.guide_send.emit('clicked')
            self.assertEqual(len(fake.run.call_args_list), sent, 'a changed setting asks before cutting')
            self.assertEqual(dialog.call_args.kwargs['heading'], 'Cut With Changed Settings?')
            _, response, proposal = dialog.return_value.connect.call_args.args
            response(None, 'cut', proposal)
            self.assertEqual(len(fake.run.call_args_list), sent + 1)
            self.assertEqual(win.guide.spot, 1)
            self.assertEqual(win.guide.completed, 3)
        self.assertEqual(dialog.call_count, 2)

        win.guide_next.emit('clicked')
        self.assertEqual(win.guide.spot, 2)
        self.assertIn('Spot 1 · 3 passes', win.guide_history.get_subtitle())
        self.assertEqual(win.preview.test_spots[0].passes, 3)

    def test_footer_actions_carry_their_roles_and_the_status_wraps_in_gtk(self):
        win = self.make_window()
        self.assertTrue(win.cut_button.has_css_class("suggested-action"))
        self.assertFalse(win.cut_button.has_css_class("destructive-action"))
        self.assertTrue(win.cancel_cut.has_css_class("destructive-action"))
        self.assertTrue(win.review_send.has_css_class("suggested-action"))
        self.assertFalse(win.scan_button.has_css_class("destructive-action"))
        self.assertFalse(win.corner_button.has_css_class("destructive-action"))

        # A refusal has to be readable at compact widths, so it wraps.
        self.assertTrue(win.send_status.get_wrap())
        self.assertFalse(win.send_status.get_ellipsize() == gui.Pango.EllipsizeMode.END)

    def test_the_material_test_survives_settings_edits_and_page_switches_in_gtk(self):
        win = self.make_window()
        win.open_material_guide()
        self.assertTrue(win.tuning)
        win.guide_next.emit("clicked")
        spot = win.guide.spot
        self.assertEqual(spot, 2)

        win.open_cut_settings()
        self.assertTrue(win.tuning, "editing cut settings closed the material test")
        self.assertEqual(win.stack.get_visible_child_name(), "cut")
        self.assertTrue(win.cut_settings.get_visible())
        self.assertFalse(win.cut_button.get_visible())

        win.r_force.set_value(win.r_force.get_value() + 3)
        self.assertEqual(win.guide.spot, spot)
        self.assertTrue(win.tuning)

        win.stack.set_visible_child_name("sheet")
        win.stack.set_visible_child_name("cut")
        self.assertTrue(win.tuning)
        self.assertEqual(win.guide.spot, spot)

        self.assertFalse(win.preset_actions.get_visible(), "the guide saves its own presets")
        win.open_cut_settings()
        self.assertTrue(win.tuning)
        win.open_send_workspace()
        self.assertTrue(win.tuning)

        win.back_to_send.emit("clicked")
        self.assertFalse(win.tuning)
        self.assertTrue(win.cut_button.get_visible())
        self.assertEqual(win.material_guide.get_label(), "Resume Material Test")

    def test_every_test_spot_can_be_reached_from_the_keyboard_in_gtk(self):
        win = self.make_window()
        win.open_material_guide()
        selector = win.guide_spot_selector
        self.assertTrue(selector.get_sensitive())

        selector.set_value(4)
        self.assertEqual(win.guide.spot, 4)
        self.assertEqual(win.guide_status.get_title(), "Spot 4 · Untouched stock")

        selector.set_value(1)
        self.assertEqual(win.guide.spot, 1)
        win.guide_next.emit("clicked")
        self.assertEqual(int(selector.get_value()), win.guide.spot)
        self.assertEqual(selector.get_adjustment().get_upper(),
                         max(win.guide.max_spots, win.guide.spot))

        # A spot the sheet does not have: the selector clamps, the model refuses.
        beyond = win.guide.max_spots + 1
        selector.set_value(beyond)
        self.assertEqual(int(selector.get_value()), win.guide.max_spots)
        self.assertEqual(win.guide.spot, win.guide.max_spots)
        with self.assertRaisesRegex(ValueError, "spots"):
            win.guide.select_spot(beyond)

        # The selector is not editable while the cutter is working.
        win._sending = True
        win.update_guide()
        self.assertFalse(selector.get_sensitive())
        win._sending = False

    def test_a_matched_zip_explains_itself_before_an_edit_is_attempted_in_gtk(self):
        win = self.make_window()
        self.assertFalse(win.bundle_banner.get_revealed())
        win.pdf_bundle = mock.MagicMock()
        win.update_send_actions()
        self.assertTrue(win.bundle_banner.get_revealed())
        self.assertIn("locked to the printed sheet", win.bundle_banner.get_title())
        self.assertEqual(win.bundle_banner.get_button_label(), "Open Sheet")
        win.pdf_bundle = None
        win.update_send_actions()
        self.assertFalse(win.bundle_banner.get_revealed())

    def test_workspace_toggle_and_matched_zip_restrictions(self):
        win = self.make_window()
        win.stack.set_visible_child_name("sheet")
        self.assertEqual(win.stack.get_visible_child_name(), "sheet")
        win.design_button.emit('clicked')
        self.assertIsNotNone(win.design)
        win.leave_design()
        self.assertEqual(win.stack.get_visible_child_name(), "sheet")

        # Matched ZIP locked dialog
        win.pdf_bundle = mock.MagicMock()
        with mock.patch.object(gui.Adw, "AlertDialog") as dialog_cls:
            win.on_edit_template()
            dialog = dialog_cls.return_value
            self.assertEqual(dialog_cls.call_args.kwargs["heading"], "Matched ZIP Paths Locked")
            _, response_handler = dialog.connect.call_args.args
            response_handler(None, "sheet")
            self.assertEqual(win.stack.get_visible_child_name(), "sheet")

    def test_guide_marked_sheet_export_uses_the_preview_placement(self):
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(config, 'CONFIG_DIR', Path(folder)):
            win = self.make_window()
            win.open_material_guide()
            self.assertIn("Registration off", win.send_status.get_label())
            win.guide_registration.set_active(True)
            self.assertIn("Registration scan before each pass", win.send_status.get_label())
            path = Path(folder) / 'marked-test.svg'
            with mock.patch.object(gui.Gtk, 'FileDialog') as chooser:
                win.guide_marked_sheet.emit('clicked')
                dialog = chooser.return_value
                dialog.save_finish.return_value = gui.Gio.File.new_for_path(str(path))
                dialog.save.call_args.args[2](dialog, mock.Mock())
            self.assertTrue(win.preview.test_placement.registered)
            self.assertFalse(win.guide_registration.get_sensitive())
            self.assertIn('registration-marks-DO-NOT-CUT', path.read_text())
            self.assertTrue(win.preview.trial.sheet.registration)

    def test_corner_cut_confirms_and_sends_only_the_sample(self):
        win = self.make_window()
        plan = win.job
        sample = corner_test.build(plan, bleed_mm=win.r_bleed.get_value())
        fake = mock.MagicMock()
        fake.__enter__.return_value = fake
        fake.can_read = True
        fake.status.return_value = 'ready'
        with mock.patch.object(gui.Adw, 'AlertDialog') as dialog:
            win.corner_button.emit('clicked')
        self.assertIn(sample.describe(), dialog.call_args.kwargs['body'])
        _, callback, *arguments = dialog.return_value.connect.call_args.args
        with mock.patch.object(gui.threading, 'Thread', side_effect=lambda *, target, daemon:
                               types.SimpleNamespace(start=target)), \
             mock.patch.object(gui.GLib, 'idle_add', side_effect=lambda callback, *args: callback(*args)), \
             mock.patch.object(gui.transport, 'open_transport', return_value=fake):
            callback(dialog.return_value, 'cut', *arguments)
        fake.run.assert_called_once_with(sample.job.steps, wait_done=True)
        self.assertEqual(win.job.stream, plan.stream)

    def test_save_preset_button_reloads_edited_blade_settings(self):
        with tempfile.TemporaryDirectory() as folder, \
             mock.patch.object(config, "CONFIG_DIR", Path(folder)):
            win = self.make_window()
            self.assertEqual(win.r_force.get_value(), 25)
            win.r_force.set_value(32)
            win.r_speed.set_value(21)
            win.r_depth.set_value(-1)
            win.r_passes.set_value(5)
            win.r_diameter.set_value(0.65)
            win.r_corners.set_active(True)
            win.r_corner_start.set_value(0.4)
            win.r_corner_end.set_value(0.7)
            win.r_lift.set_active(False)
            win.save_preset_btn.emit("clicked")
            win.r_preset.set_selected(0)
            names = sorted(presets.all_presets())
            win.r_preset.set_selected(names.index(config.DEFAULT_PRESET))
            self.assertEqual((win.r_force.get_value(), win.r_speed.get_value(),
                              win.r_depth.get_value(), win.r_passes.get_value()),
                             (32, 21, -1, 5))
            self.assertIsNone(win.job.settings.depth)
            self.assertEqual(win.job.settings.passes, 5)
            for cmd in (b"FC13,1,1", b"FE1,1", b"FF4,7,1"):
                self.assertIn(cmd, win.job.stream.split(b"\x03"))

    def test_save_as_new_selects_a_distinct_preset(self):
        with tempfile.TemporaryDirectory() as folder, \
             mock.patch.object(config, "CONFIG_DIR", Path(folder)):
            win = self.make_window()
            win.r_force.set_value(18)
            win.r_depth.set_value(-1)
            win.on_save_blade_preset(new_name=" Green holder ")
            self.assertEqual(win._text(win.r_preset), "Green holder")
            self.assertEqual(win.job.preset.name, "Green holder")
            self.assertEqual(presets.get("Green holder").cut_settings().force, 18)
            self.assertIsNone(presets.get("Green holder").cut_settings().depth)
            self.assertEqual(presets.get(config.DEFAULT_PRESET).cut_settings().force, 25)
            win.r_force.set_value(19)
            win.on_save_blade_preset(new_name="GREEN HOLDER")
            self.assertEqual(presets.get("Green holder").cut_settings().force, 18)

    def test_registration_failure_opens_named_mark_diagnostics(self):
        win = self.make_window()
        report = mock.Mock()
        report.problems.return_value = ["top-right L: no ink found in the search window"]
        matched = mock.Mock()
        matched.near_marks.return_value = ("bottom-left L",)
        matched.mat_placement.return_value = "Align with the top-left of the mat grid."
        from cutline.transport import RegistrationFailed
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

    def verify_bundle_export(self, source, direct_cut):
        win = self.make_window()
        with tempfile.TemporaryDirectory() as folder, \
             mock.patch.object(gui.Gtk, 'FileDialog') as chooser, \
             mock.patch.object(win, 'measure_sheet') as measure:
            win.on_pick_pdf()
            dialog = chooser.return_value
            dialog.open_finish.return_value = gui.Gio.File.new_for_path(str(source))
            dialog.open.call_args.args[2](dialog, mock.Mock())
            measure.assert_called_once()
            self.assertEqual(win.pdf_path, source)
            if direct_cut:
                self.assertEqual(len(win.imported.paths), 8)
                self.assertFalse(win.job.blocked)
            else:
                self.assertIsNone(win.job)
                self.assertFalse(win.cut_button.get_sensitive())
            win.on_save_bundle_file()
            output = Path(folder) / 'local.studio3'
            dialog.save_finish.return_value = gui.Gio.File.new_for_path(str(output))
            dialog.save.call_args.args[2](dialog, mock.Mock())
            self.assertEqual(output.read_bytes()[:13], b'silhouette05;')

    @unittest.skipUnless(os.environ.get('CUTLINE_CARD_BUNDLE_DIR'), 'generate a card print bundle first')
    def test_selecting_a_real_zip_opens_its_paths_and_exports_its_cut_file(self):
        self.verify_bundle_export(Path(os.environ['CUTLINE_CARD_BUNDLE_DIR']) / 'letter-landscape-8.zip', True)

    @unittest.skipUnless(os.environ.get('CUTLINE_LEGACY_BUNDLE'), 'supply a legacy print-and-cut ZIP first')
    def test_legacy_zip_exports_the_paired_cut_file(self):
        self.verify_bundle_export(Path(os.environ['CUTLINE_LEGACY_BUNDLE']), False)

    def test_standalone_pdf_registration_check_gates_hardware_actions_in_gtk(self):
        from cutline import sheet
        win = self.make_window()
        win.pdf_path = Path("test_sheet.pdf")
        win.r_register.set_active(True)
        win.rebuild()
        self.assertTrue(win.job.registration)

        # Pending check
        win.pdf_check_state = sheet.RegistrationDecision.CHECKING
        win.update_send_actions()
        self.assertEqual(win.send_review_destination(), ("sheet", "Review Sheet"))
        self.assertFalse(win.cut_button.get_sensitive())
        self.assertFalse(win.corner_button.get_sensitive())
        self.assertFalse(win.scan_button.get_sensitive())
        self.assertIn("pending", win.send_status.get_label())

        # Failed check
        win.pdf_check_state = sheet.RegistrationDecision.FAILED
        win.update_send_actions()
        self.assertEqual(win.send_review_destination(), ("sheet", "Review Sheet"))
        self.assertFalse(win.cut_button.get_sensitive())
        self.assertFalse(win.corner_button.get_sensitive())
        self.assertFalse(win.scan_button.get_sensitive())
        self.assertEqual(win.send_status.get_label(),
                         "⚠ Registration check failed. Open Sheet to see the missing marks.")
        self.assertTrue(win.send_status.has_css_class("warning"))

        toasts = []
        with mock.patch.object(win, "toast", side_effect=toasts.append):
            win.on_cut()
            win._cut_response(None, "cut", win.job)
        self.assertEqual(toasts, [
            "Registration check failed. Open Sheet to see the missing marks.",
            "Registration check failed. Open Sheet to see the missing marks.",
        ])

        # Error check
        win.pdf_check_state = sheet.RegistrationDecision.ERROR
        win.update_send_actions()
        self.assertEqual(win.send_review_destination(), ("sheet", "Review Sheet"))
        self.assertFalse(win.cut_button.get_sensitive())
        self.assertFalse(win.corner_button.get_sensitive())
        self.assertFalse(win.scan_button.get_sensitive())
        self.assertEqual(win.send_status.get_label(),
                         "⚠ Could not measure registration marks on the print file. Open Sheet to review.")

        # Passed check
        win.pdf_check_state = sheet.RegistrationDecision.PASSED
        win.update_send_actions()
        self.assertEqual(win.send_review_destination(), ("cut", "Review Cut Job"))
        self.assertTrue(win.cut_button.get_sensitive())
        self.assertTrue(win.corner_button.get_sensitive())
        self.assertTrue(win.scan_button.get_sensitive())

        # Registration off: blank stock cut is allowed even if PDF check was failed
        win.pdf_check_state = sheet.RegistrationDecision.FAILED
        win.r_register.set_active(False)
        win.rebuild()
        self.assertFalse(win.job.registration)
        win.update_send_actions()
        self.assertEqual(win.send_review_destination(), ("cut", "Review Cut Job"))
        self.assertTrue(win.cut_button.get_sensitive())
        self.assertTrue(win.corner_button.get_sensitive())
        self.assertFalse(win.scan_button.get_sensitive())

        # No PDF open: device scan allowed when registration is on
        win.r_register.set_active(True)
        win.clear_printed_sheet()
        win.rebuild()
        self.assertIsNone(win.pdf_path)
        self.assertTrue(win.job.registration)
        win.update_send_actions()
        self.assertEqual(win.send_review_destination(), ("cut", "Review Cut Job"))
        self.assertTrue(win.cut_button.get_sensitive())
        self.assertTrue(win.corner_button.get_sensitive())
        self.assertTrue(win.scan_button.get_sensitive())

    def test_a_send_locks_the_settings_and_every_ending_unlocks_them(self):
        from cutline import transport

        win = self.make_window()
        win.r_force.set_value(25)
        win.rebuild()
        checked = win.job
        self.assertTrue(win.r_force.get_sensitive())
        win.imported = mock.Mock()
        win.update_send_actions()
        self.assertTrue(win.use_generated.get_sensitive())
        self.assertFalse(win.r_sheet_page.get_sensitive())

        win.active_job = checked
        win.cut_control = transport.CutControl(lambda: None)
        win._sending = True
        win.lock_job_controls(True)
        win.update_send_actions()
        self.assertFalse(win.use_generated.get_sensitive())
        self.assertFalse(win.reset_layout_action.get_enabled())
        self.assertFalse(win.open_print_button.get_sensitive())
        self.assertFalse(win.send_holder_buttons[2].get_sensitive())
        win.on_clear_artwork()
        self.assertIsNotNone(win.imported)
        for widget in (win.r_force, win.r_speed, win.r_passes, win.r_cutter, win.r_preset, win.r_sheet):
            self.assertFalse(widget.get_sensitive())

        # An edit attempt cannot reach the submitted job while it is locked.
        win.r_force.set_value(10)
        win.on_changed()
        self.assertIs(win.active_job, checked)
        self.assertEqual(win.active_job.settings.force, 25)

        win.imported = None
        win.release_job()
        self.assertIsNone(win.active_job)
        self.assertFalse(win._sending)
        for widget in (win.r_force, win.r_speed, win.r_passes, win.r_cutter, win.r_preset, win.r_sheet):
            self.assertTrue(widget.get_sensitive())
        self.assertTrue(win.open_print_button.get_sensitive())
        self.assertTrue(win.send_holder_buttons[2].get_sensitive())
        self.assertFalse(win.r_sheet_page.get_sensitive())

    def test_a_matched_bundle_stays_locked_after_a_send_unlocks_the_rest(self):
        win = self.make_window()
        win.lock_bundle_layout(True)
        locked = win.bundle_locked_controls()
        win.lock_job_controls(True)
        win.lock_job_controls(False)
        for widget in locked:
            self.assertFalse(widget.get_sensitive())
        self.assertTrue(win.r_force.get_sensitive())
        win.lock_bundle_layout(False)
        self.assertTrue(win.r_pattern.get_sensitive())

    def test_a_job_without_steps_is_refused_without_claiming_the_window(self):
        win = self.make_window()
        win.r_register.set_active(False)
        win.rebuild()
        self.assertFalse(win.job.registration)
        with mock.patch.object(win, "toast") as toast:
            gui.Window._cut_response(win, None, "cut", win.job, win._connection(), True)
        self.assertIn("does not use registration", toast.call_args.args[0])
        self.assertIsNone(win.active_job)
        self.assertFalse(win._sending)
        self.assertTrue(win.r_force.get_sensitive())
        win.rebuild()
        self.assertIsNotNone(win.job)

    def test_a_passed_check_survives_blade_edits_and_a_change_during_one_invalidates_it(self):
        win = self.make_window()
        win.pdf_path = Path('sample.pdf')
        win.r_register.set_active(True)
        win.rebuild()
        win.checked_marks = win.job.marks
        win.sheet_report = object()
        win._sheet_token = object()
        win.pdf_check_state = gui.sheet_mod.RegistrationDecision.PASSED

        win.r_force.set_value(win.r_force.get_value() - 1)
        win.r_speed.set_value(win.r_speed.get_value() - 1)
        self.assertEqual(win.pdf_check_state, gui.sheet_mod.RegistrationDecision.PASSED)

        win.checked_marks = None
        with mock.patch.object(gui.threading, "Thread") as thread:
            win.measure_sheet()
        thread.return_value.start.assert_called_once()
        self.assertEqual(win.pdf_check_state, gui.sheet_mod.RegistrationDecision.CHECKING)
        self.assertEqual(win.checked_marks, win.job.marks)
        win.r_inset.set_value(win.r_inset.get_value() + 1)
        self.assertEqual(win.pdf_check_state, gui.sheet_mod.RegistrationDecision.NONE)
        self.assertIsNone(win.checked_marks)
        self.assertIsNone(win._sheet_token)

    def test_the_holder_tabs_name_the_detected_tool_and_depth_follows_it_in_gtk(self):
        win = self.make_window()
        buttons = win.send_holder_buttons
        win.r_depth.set_value(7)

        win.apply_tool_detection((2, 0))
        self.assertEqual(buttons[1].get_label(), "Tool Holder 1\nAutoBlade")
        self.assertEqual(buttons[2].get_label(), "Tool Holder 2\nEmpty")
        self.assertEqual(int(win.r_depth.get_value()), 7)

        with mock.patch.object(win, "toast") as toast:
            win.apply_tool_detection((4, 0))
        self.assertEqual(buttons[1].get_label(), "Tool Holder 1\nKraft blade")
        self.assertEqual(int(win.r_depth.get_value()), -1)
        self.assertIn("Kraft blade", toast.call_args.args[0])

        # The same reply again does not fight an operator who sets depth back.
        win.r_depth.set_value(5)
        with mock.patch.object(win, "toast") as toast:
            win.apply_tool_detection((4, 0))
        toast.assert_not_called()
        self.assertEqual(int(win.r_depth.get_value()), 5)

        win.forget_tool_detection()
        self.assertEqual(buttons[1].get_label(), "Tool Holder 1")
        self.assertEqual(buttons[2].get_label(), "Tool Holder 2")

    def test_a_pdf_check_stops_authorizing_cuts_when_orientation_mode_changes(self):
        win = self.make_window()
        win.pdf_path = Path("print.pdf")
        win.r_register.set_active(True)
        win.rebuild()
        win.checked_marks = win.job.marks
        win._sheet_token = object()
        win.pdf_check_state = gui.sheet_mod.RegistrationDecision.PASSED
        win.update_send_actions()
        self.assertTrue(win.cut_button.get_sensitive())
        with mock.patch.object(gui.layout_presets, "is_published", return_value=True):
            win.on_changed()
        self.assertIsNone(win._sheet_token)
        self.assertFalse(win.cut_button.get_sensitive())

    def test_manual_depth_is_applied_when_returning_to_holder_one(self):
        win = self.make_window()
        win.r_depth.set_value(7)
        win.r_holder.set_value(2)
        win.apply_tool_detection((1, 7))
        self.assertEqual(int(win.r_holder.get_value()), 2)
        self.assertIsNone(win.corrected_tools)
        win.r_holder.set_value(1)
        self.assertEqual(int(win.r_depth.get_value()), -1)

    def test_blade_picker_allows_rotary_in_holder_one_and_ignores_later_detection_errors(self):
        win = self.make_window()
        picker = win.r_tool_selection
        win.apply_tool_detection((2, 255))
        self.assertEqual(win._text(picker), "Detect automatically")
        picker.set_selected(win.tool_picker_choices.index("rotary"))
        win.apply_tool_detection((2, 255))
        self.assertEqual(win.r_holder.get_value(), 1)
        self.assertEqual(win.send_holder_buttons[1].get_label(), "Tool Holder 1\nRotary blade")
        self.assertFalse(win.r_depth.get_sensitive())
        self.assertEqual(win.job.blocked, [])
        self.assertEqual(win.job.settings.holder, 1)
        self.assertNotIn(b"TF", win.job.stream)

        win.r_holder.set_value(2)
        picker.set_selected(win.tool_picker_choices.index("rotary"))
        self.assertEqual(win.r_holder.get_value(), 2)
        self.assertEqual(win.send_holder_buttons[2].get_label(), "Tool Holder 2\nRotary blade")
        self.assertEqual(win.job.settings.holder, 2)
        self.assertIn(b"J2\x03", win.job.stream)
        self.assertNotIn(b"TF", win.job.stream)

        picker.set_selected(0)
        self.assertIn("No tool detected", win.send_holder_buttons[2].get_label())

        win.r_holder.set_value(1)
        self.assertEqual(win._text(picker), "Rotary blade")
        picker.set_selected(0)
        self.assertEqual(win._text(picker), "Detect automatically")
        self.assertTrue(win.r_depth.get_sensitive())

    def test_a_pending_tool_probe_blocks_other_commands_and_recovers_after_failure(self):
        win = self.make_window()
        with mock.patch.object(gui.threading, "Thread") as thread, \
             mock.patch.object(gui.GLib, "idle_add", side_effect=lambda fn, *a: fn(*a)), \
             mock.patch.object(gui.cutters, "open_link", side_effect=OSError("gone")):
            win.detect_tools()
            win.detect_tools()
            win.on_refresh_status()
            thread.assert_called_once()
            self.assertFalse(win.check_cutter_status.get_sensitive())
            self.assertFalse(win.cut_button.get_sensitive())
            thread.call_args.kwargs["target"]()
        self.assertTrue(win.check_cutter_status.get_sensitive())
        self.assertFalse(win._checking_status)

    def test_tools_are_read_once_per_connection_and_not_during_a_job_in_gtk(self):
        win = self.make_window()
        cutter = mock.MagicMock()
        cutter.__enter__.return_value = cutter
        cutter.tool_codes.return_value = (2, 0)

        class Immediate:
            def __init__(self, target=None, daemon=False, **_kwargs):
                self._target = target

            def start(self):
                self._target()

        with mock.patch.object(gui.threading, "Thread", Immediate), \
             mock.patch.object(gui.GLib, "idle_add", side_effect=lambda fn, *a: fn(*a)), \
             mock.patch.object(gui.cutters, "open_link", return_value=cutter):
            win.detect_tools()
            self.assertEqual(win.detected_tools, (2, 0))
            win.detect_tools()
            self.assertEqual(cutter.tool_codes.call_count, 1)

            win.tools_connection = None
            win._sending = True
            win.detect_tools()
            self.assertEqual(cutter.tool_codes.call_count, 1)
            win._sending = False

            win.active_job = win.job
            win.detect_tools()
            self.assertEqual(cutter.tool_codes.call_count, 1)
            win.active_job = None

    def test_active_and_paused_job_details_and_control_states_in_gtk(self):
        from cutline import transport
        win = self.make_window()
        win.r_force.set_value(25)
        win.rebuild()
        original_job = win.job
        self.assertEqual(original_job.settings.force, 25)

        control = transport.CutControl(lambda: None)
        win.cut_control = control
        win.active_job = original_job
        win._sending = True
        win.update_send_actions()

        # Running state
        self.assertEqual(control.state, "running")
        self.assertEqual(win.pause_cut.get_label(), "Pause Cut")
        self.assertTrue(win.pause_cut.get_sensitive())
        self.assertEqual(win.cancel_cut.get_label(), "Cancel Cut")
        self.assertTrue(win.cancel_cut.get_sensitive())
        self.assertIn("Sending to the cutter", win.send_status.get_label())

        # Pausing state
        control.toggle_pause()
        self.assertEqual(control.state, "pausing")
        win.update_send_actions()
        self.assertEqual(win.pause_cut.get_label(), "Pausing…")
        self.assertTrue(win.pause_cut.get_sensitive())
        self.assertEqual(win.cancel_cut.get_label(), "Cancel Cut")
        self.assertTrue(win.cancel_cut.get_sensitive())
        self.assertIn("Pausing", win.send_status.get_label())

        # Paused state
        with control._condition:
            control._set_state("paused")
        win.update_send_actions()
        self.assertEqual(control.state, "paused")
        self.assertEqual(win.pause_cut.get_label(), "Resume Cut")
        self.assertTrue(win.pause_cut.get_sensitive())
        self.assertEqual(win.cancel_cut.get_label(), "Cancel Cut")
        self.assertTrue(win.cancel_cut.get_sensitive())
        self.assertIn("Cut paused", win.send_status.get_label())

        # Calling rebuild or on_changed while active does NOT change the active job
        win.r_force.set_value(10)
        win.rebuild()
        win.on_changed()
        self.assertIs(win.active_job, original_job)
        self.assertEqual(win.active_job.settings.force, 25)

        # Cancelling state
        control.cancel()
        self.assertEqual(control.state, "cancelling")
        win.update_send_actions()
        self.assertEqual(win.cancel_cut.get_label(), "Stopping…")
        self.assertFalse(win.cancel_cut.get_sensitive())
        self.assertFalse(win.pause_cut.get_sensitive())
        self.assertIn("Stopping", win.send_status.get_label())

        # Finishing state
        control.finishing()
        self.assertEqual(control.state, "finishing")
        win.update_send_actions()
        self.assertFalse(win.cancel_cut.get_sensitive())
        self.assertFalse(win.pause_cut.get_sensitive())
        self.assertIn("Waiting for the cutter", win.send_status.get_label())

        # Terminal state reset restores editable controls
        win._send_done()
        self.assertIsNone(win.cut_control)
        self.assertIsNone(win.active_job)
        self.assertFalse(win._sending)
        self.assertFalse(win.pause_cut.get_visible())
        self.assertFalse(win.cancel_cut.get_visible())


@unittest.skipUnless(gui is not None, 'PyGObject is not installed')
class TestOpenDialogs(unittest.TestCase):
    def test_svg_button_opens_a_filtered_file_dialog(self):
        window = mock.Mock(_job_locked=False, _sending=False)
        with mock.patch.object(gui.Gtk, 'FileDialog') as dialog:
            gui.Window.on_open_artwork(window)
        dialog.return_value.open.assert_called_once()
        filters = dialog.return_value.set_filters.call_args.args[0]
        self.assertEqual(filters.get_n_items(), 1)
        self.assertEqual(filters.get_item(0).get_name(), 'Cut templates (SVG, DXF, Studio3)')

    def test_pdf_button_opens_a_filtered_file_dialog(self):
        window = mock.Mock(_job_locked=False, _sending=False)
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

    def test_a_failed_import_keeps_the_previous_artwork_and_job(self):
        previous = object()
        window = types.SimpleNamespace(imported=previous, toast=mock.Mock(), clear_bundle=mock.Mock(),
                                       _job_locked=False, _sending=False)
        window.load_artwork_file = types.MethodType(gui.Window.load_artwork_file, window)
        with mock.patch.object(gui.Gtk, 'FileDialog') as dialog, \
             mock.patch('cutline.importer.load', side_effect=ValueError('bad file')):
            gui.Window.on_open_artwork(window)
            callback = dialog.return_value.open.call_args.args[2]
            dialog.return_value.open_finish.return_value.get_path.return_value = '/tmp/invalid.svg'
            callback(dialog.return_value, mock.Mock())
        self.assertIs(window.imported, previous)
        window.toast.assert_called_once()

    def test_multipage_svg_prompts_then_loads_the_selected_page(self):
        path = Path("/tmp/multipage.svg")
        artwork = mock.Mock()
        window = types.SimpleNamespace(clear_bundle=mock.Mock(), set_artwork=mock.Mock(),
                                       _job_locked=False, _sending=False)
        window.load_artwork_file = types.MethodType(gui.Window.load_artwork_file, window)
        with mock.patch.object(gui.Adw, "AlertDialog") as dialog, \
             mock.patch.object(gui.Gtk, "SpinButton") as spin, \
             mock.patch.object(importer, "load", side_effect=[importer.InkscapePageRequired(3),
                                                            artwork]) as load:
            window.load_artwork_file(path)
            spin.new_with_range.assert_called_once_with(1, 3, 1)
            window.set_artwork.assert_not_called()
            spin.new_with_range.return_value.get_value_as_int.return_value = 2
            dialog.return_value.connect.call_args.args[1](dialog.return_value, "open")
        self.assertEqual(load.call_args_list, [mock.call(path, page=None), mock.call(path, page=2)])
        window.set_artwork.assert_called_once_with(artwork, "multipage.svg · Page 2")

    def test_group_assignments_keep_explicit_order_and_omit_disabled_rows(self):
        from cutline import actions

        def row(key, active, preset, holder, order):
            enabled = mock.Mock()
            enabled.get_active.return_value = active
            slot = mock.Mock()
            slot.get_selected.return_value = holder
            position = mock.Mock()
            position.get_value.return_value = order
            return key, None, enabled, preset, slot, position

        window = types.SimpleNamespace(_text=lambda value: value, _send_rows=[
            row("Cut", True, "Plain paper test", 1, 3),
            row("Guide", False, "Plain paper test", 0, 2),
            row("Score", True, "Pen, no blade", 2, 1),
        ])
        self.assertEqual(gui.Window._group_assignments(window), (
            actions.Assignment("Score", "Pen, no blade", 2),
            actions.Assignment("Cut", "Plain paper test", 1),
        ))

    def test_show_weed_settings_changes_visibility_only(self):
        window = types.SimpleNamespace(r_show_weed=mock.Mock(), _text=mock.Mock(),
            r_weed_border=mock.Mock(), r_weed_lines=mock.Mock(), weed_group=mock.Mock())
        window.r_weed_border.get_value.return_value = 3
        for mode, imported, visible in (("Auto", None, False), ("Auto", object(), True),
                                        ("Always", None, True), ("Never", object(), False)):
            with self.subTest(mode=mode, imported=bool(imported)):
                window._text.return_value = mode
                window.imported = imported
                gui.Window._update_weed_controls(window)
                window.weed_group.set_visible.assert_called_with(visible)
                window.r_weed_border.set_sensitive.assert_called_with(imported is not None)
        self.assertEqual(window.weed_group.set_visible.call_count, 4)
        self.assertEqual(window.r_weed_border.set_value.call_count, 0)

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
        return types.SimpleNamespace(_sheet_token=token, pdf_bundle=None, job=None, checked_marks=None,
            pdf_check_state=gui.sheet_mod.RegistrationDecision.NONE, preview=mock.Mock(),
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
