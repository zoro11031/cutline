"""GUI handlers and a display-backed Design button smoke test."""
import os
import tempfile
from pathlib import Path
import unittest
import types
from unittest import mock
from cut_proxies import config, corner_test, graphtec, importer, job, presets, starting_guide
from tests.test_vector_editing import compound_project
from tests.test_sticker_print import make_image

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
        app = gui.Adw.Application(application_id=f'org.printing.DesignSmokeTest.{self._testMethodName}',
                                  flags=gui.Gio.ApplicationFlags.NON_UNIQUE)
        self.assertTrue(app.register(None))
        win = gui.Window(app)
        self.addCleanup(win.destroy)
        device = graphtec.device_by_name("Silhouette Cameo 4")
        patcher = mock.patch.object(gui.transport, "usb_endpoints", return_value=[(device, "", (1, 2))])
        patcher.start()
        self.addCleanup(patcher.stop)
        win.refresh_usb()
        return win

    def test_multiple_usb_cutters_require_an_endpoint_choice(self):
        win = self.make_window()
        device = graphtec.device_by_name("Silhouette Cameo 4")
        with mock.patch.object(gui.transport, "usb_endpoints",
                               return_value=[(device, "", (1, 2)), (device, "", (1, 3))]):
            win.refresh_usb()
            self.assertIsNone(win._connection()[1])
            self.assertFalse(win.cut_button.get_sensitive())
            win.r_bluetooth.set_selected(2)
            self.assertEqual(win._connection()[1], (1, 3))
            self.assertTrue(win.cut_button.get_sensitive())

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
        self.assertFalse(design.review.get_sensitive())
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

    def test_apply_material_opens_the_cut_settings_with_the_selected_values(self):
        from cut_proxies import materials

        win = self.make_window()
        win.stack.set_visible_child_name('materials')
        with mock.patch.object(materials, 'load', return_value={}):
            win._load_materials()
        self.assertFalse(win.apply_material_button.get_sensitive())
        sample = mock.Mock()
        sample.cut_settings.return_value = graphtec.CutSettings(force=16)
        sample.describe.return_value = 'Force 16'
        win.r_cutter_divisions.set_selected(1)
        with mock.patch.object(materials, 'load', return_value={}), \
             mock.patch.object(materials, 'cutter_divisions', return_value={'Sample': sample}):
            win._load_materials()
        self.assertTrue(win.apply_material_button.get_sensitive())
        win.apply_material_button.emit('clicked')
        self.assertEqual(win.stack.get_visible_child_name(), 'cut')
        self.assertEqual(win.job.settings.force, 16)

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
        materials = win.stack.get_child_by_name("materials")
        self.assertFalse(win.r_force.is_ancestor(materials))
        self.assertFalse(win.r_preset.is_ancestor(materials))

    def test_published_template_is_selected_in_layout_and_can_be_cleared(self):
        from cut_proxies import layout_presets

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
        from cut_proxies import editor_model

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
        from cut_proxies import editor_model
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
        self.assertEqual(design.text_button.get_label(), 'Edit Text…')
        self.assertEqual(design.project.shapes[1].text.value, 'O')
        design.commit((design.project.shapes[0], replace(design.project.shapes[1], x=110, y=40)), (1,))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'compounds.scc-template'
            design.write_design(path, None)
            reopened = editor_model.load_project(path)
            with mock.patch.object(design, 'file_dialog', side_effect=lambda _title, opened, **_: opened(path)):
                design.open_project(None)
        self.assertEqual(design.project, reopened)
        design.review.emit('clicked')
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
        from cut_proxies.editor_model import Project, Shape

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
        from cut_proxies.editor_model import Shape

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
        design.review.emit('clicked')
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

        win.r_bluetooth.set_selected(1)
        self.assertTrue(win.guide_send.get_sensitive())
        self.assertNotIn("select a cutter", win.guide_status.get_subtitle())

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

        win.guide_edit.emit('clicked')
        self.assertEqual(win.stack.get_visible_child_name(), 'cut')
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
        from cut_proxies.editor_model import Shape

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
        design.review.emit('clicked')
        self.assertEqual(win.stack.get_visible_child_name(), 'cut')
        self.assertEqual((win.job.grid.sheet_w_mm, win.job.grid.sheet_h_mm), (150, 180))
        self.assertEqual(win.job.mat, 'cameo-12x12')
        self.assertEqual((win.job.marks.inset_mm, win.job.marks.length_mm), (14, 9))
        self.assertEqual(win.job.blocked, [])

    def test_design_drawing_points_selection_and_replicate_enter_history(self):
        from cut_proxies.editor_model import Shape
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
        design.review.emit('clicked')
        self.assertTrue(win.cut_button.get_sensitive())
        win.on_edit_template()
        design.commit((replace(design.project.shapes[0], x=45),), (0,))
        design.send_workspace.emit('clicked')
        self.assertEqual(win.stack.get_visible_child_name(), 'cut')
        self.assertEqual(win.review_send.get_label(), 'Review Design')
        self.assertFalse(win.cut_button.get_sensitive())
        self.assertFalse(win.scan_button.get_sensitive())
        win.review_send.emit('clicked')
        self.assertIs(win.design, design)
        self.assertEqual(design.project.shapes[0].x, 45)
        self.assertTrue(design.dirty)
        design.review.emit('clicked')
        self.assertTrue(win.cut_button.get_sensitive())

    def test_save_updates_the_open_project_without_opening_the_picker(self):
        from cut_proxies.editor_model import load_project

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
        dialog.assert_called_once()
        self.assertEqual(fake.run.call_args_list, [mock.call(trial.sheet.steps, wait_done=True)] * 2)
        self.assertIn('2 completed passes', win.guide_status.get_title())
        self.assertTrue(win.guide_use.get_sensitive())
        with mock.patch.object(win, 'offer_tested_preset') as prompt:
            win.guide_use.emit('clicked')
        prompt.assert_called_once()
        self.assertEqual(win.r_passes.get_value(), 2)

        # Settings differ check on an already-cut spot
        win.guide_spot_selector.set_value(1)
        self.assertEqual(win.guide.spot, 1)
        win.r_force.set_value(win.r_force.get_value() + 5)
        with mock.patch.object(win, 'toast') as toast:
            win.guide_send.emit('clicked')
            toast.assert_called_with("Cut settings changed on this spot. Move to untouched stock before testing different settings.")

        win.guide_next.emit('clicked')
        self.assertEqual(win.guide.spot, 2)
        self.assertIn('Spot 1 · 2 passes', win.guide_history.get_subtitle())
        self.assertEqual(win.preview.test_spots[0].passes, 2)

    def test_workspace_toggle_and_matched_zip_restrictions(self):
        win = self.make_window()
        win.stack.set_visible_child_name("materials")
        self.assertEqual(win.stack.get_visible_child_name(), "materials")
        win.design_button.emit('clicked')
        self.assertIsNotNone(win.design)
        win.leave_design()
        self.assertEqual(win.stack.get_visible_child_name(), "materials")

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

    @unittest.skipUnless(os.environ.get('PROXYPRESS_BUNDLE_DIR'), 'generate a ProxyPress bundle first')
    def test_selecting_a_real_zip_opens_its_paths_and_exports_its_cut_file(self):
        self.verify_bundle_export(Path(os.environ['PROXYPRESS_BUNDLE_DIR']) / 'letter-landscape-8.zip', True)

    @unittest.skipUnless(os.environ.get('PROXYPRESS_LEGACY_BUNDLE'), 'supply a legacy print-and-cut ZIP first')
    def test_legacy_zip_exports_the_paired_cut_file(self):
        self.verify_bundle_export(Path(os.environ['PROXYPRESS_LEGACY_BUNDLE']), False)

    def test_standalone_pdf_registration_check_gates_hardware_actions_in_gtk(self):
        from cut_proxies import sheet
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
                         "Registration check failed. Open Sheet to see the missing marks.")

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
                         "Could not measure registration marks on the print file. Open Sheet to review.")

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

    def test_active_and_paused_job_details_and_control_states_in_gtk(self):
        from cut_proxies import transport
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
        window = mock.Mock()
        with mock.patch.object(gui.Gtk, 'FileDialog') as dialog:
            gui.Window.on_open_artwork(window)
        dialog.return_value.open.assert_called_once()
        filters = dialog.return_value.set_filters.call_args.args[0]
        self.assertEqual(filters.get_n_items(), 1)
        self.assertEqual(filters.get_item(0).get_name(), 'Cut templates (SVG, DXF, Studio3)')

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

    def test_a_failed_import_keeps_the_previous_artwork_and_job(self):
        previous = object()
        window = types.SimpleNamespace(imported=previous, toast=mock.Mock(), clear_bundle=mock.Mock())
        window.load_artwork_file = types.MethodType(gui.Window.load_artwork_file, window)
        with mock.patch.object(gui.Gtk, 'FileDialog') as dialog, \
             mock.patch('cut_proxies.importer.load', side_effect=ValueError('bad file')):
            gui.Window.on_open_artwork(window)
            callback = dialog.return_value.open.call_args.args[2]
            dialog.return_value.open_finish.return_value.get_path.return_value = '/tmp/invalid.svg'
            callback(dialog.return_value, mock.Mock())
        self.assertIs(window.imported, previous)
        window.toast.assert_called_once()

    def test_multipage_svg_prompts_then_loads_the_selected_page(self):
        path = Path("/tmp/multipage.svg")
        artwork = mock.Mock()
        window = types.SimpleNamespace(clear_bundle=mock.Mock(), set_artwork=mock.Mock())
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
        from cut_proxies import actions

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
