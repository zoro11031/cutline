import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from cutline import graphtec, job, sheet, starting_guide, testcut, transport, verify


class TestStartingGuide(unittest.TestCase):
    def setUp(self):
        self.guide = starting_guide.Guide()
        self.device = graphtec.device_by_name("Silhouette Cameo 4")
        self.connection = ("auto", None)
        self.options = {
            "cutter": self.device.name, "sheet": "Letter", "mat": "cameo-12x12",
            "offset_mm": (0, 0), "force": 25, "speed": 25, "depth": 7,
            "blade_options": {"blade_diameter_mm": 0.9, "track_enhancing": True},
        }

    def prepare(self, shape="square", connection=None, **changes):
        return self.guide.prepare(connection or self.connection, dict(self.options, **changes), None, shape)

    def test_starter_sets_documented_values_and_respects_machine_capabilities(self):
        self.assertEqual(starting_guide.starting_values(self.device, 1, 0.9),
                         {"force": 25, "speed": 25, "depth": 7, "passes": 1})
        older = graphtec.device_by_name("Silhouette Cameo")
        self.assertEqual(starting_guide.starting_values(older, 1, 0.9)["speed"], older.max_speed)
        self.assertIsNone(starting_guide.starting_values(older, 1, 0.9)["depth"])
        self.assertIsNone(starting_guide.starting_values(self.device, 2, 0.9)["depth"])

    def test_sending_requires_a_selected_cutter_endpoint(self):
        for mode, endpoint in (("auto", (1, 2)), ("ble", "AA"), ("bluetooth", "BB")):
            with self.subTest(mode=mode):
                self.assertFalse(starting_guide.has_cutter_endpoint((mode, None)))
                self.assertTrue(starting_guide.has_cutter_endpoint((mode, endpoint)))

    def test_repeating_uses_exact_same_geometry_settings_and_return_to_origin(self):
        trial = self.prepare()
        self.assertEqual(len(verify.decode(trial.sheet.stream).paths), 1)
        self.assertEqual(trial.settings.passes, 1)
        self.assertFalse(trial.sheet.registration)
        self.assertNotIn(graphtec.frame("FY0"), trial.sheet.stream)
        self.assertEqual(trial.sheet.steps[-1].data, graphtec.frame(graphtec.return_to_start_commands(self.device)))
        self.guide.record(trial)
        self.assertIs(self.prepare(offset_mm=(2, -1)), trial)
        modified = self.prepare(force=32)
        self.assertEqual(modified.settings.force, 32)
        self.assertEqual(modified.sheet.cells[0].rect, trial.sheet.cells[0].rect)
        self.assertEqual(modified.placement, trial.placement)
        with self.assertRaisesRegex(ValueError, "tool holder"):
            self.prepare(blade_options={"holder": 2})
        self.guide.record(modified)
        self.assertEqual(self.guide.tested_settings.passes, 2)
        self.assertEqual(self.guide.tested_settings.force, 32)
        self.assertEqual(trial.settings.passes, 1)

    def test_next_spot_uses_fresh_geometry_with_different_shape_and_settings(self):
        original = self.prepare()
        self.guide.record(original)
        self.guide.next_spot()
        plan = job.build(sheet="A4", portrait=True)
        new = self.guide.prepare(self.connection, dict(self.options, force=28, offset_mm=(4, 4)), plan, "studio")
        self.assertEqual(new.placement, original.placement)
        self.assertFalse(new.sheet.extent().overlaps(original.sheet.extent()))
        self.assertEqual(new.sheet.shape, "studio")
        self.assertEqual(new.settings.force, 28)
        self.assertEqual(self.guide.completed, 0)

    def test_all_sample_shapes_and_sizes_build_checked_blank_and_registered_streams(self):
        connection = ("auto", (1, 2))
        for registered in (False, True):
            self.guide.registered = registered
            for shape in testcut.TEST_SHAPES:
                for size in starting_guide.SAMPLE_SIZES:
                    with self.subTest(shape=shape, size=size, registered=registered):
                        self.guide.configure_sample(shape, size)
                        trial = self.guide.prepare(connection, self.options, None)
                        location = trial.sheet.cells[0].rect
                        self.assertEqual((location.w, location.h), (size, size))
                        self.assertEqual(trial.sheet.shape, shape)
                        self.assertEqual(len(trial.sheet.paths), 2 if shape in ("studio", "ring") else 1)
                        self.assertTrue(all(location.x - 1e-9 <= x <= location.x2 + 1e-9
                                            and location.y - 1e-9 <= y <= location.y2 + 1e-9
                                            for path in trial.sheet.paths for x, y in path.points))
                        scan = [step.expect for step in trial.sheet.steps if step.kind == "expect"]
                        self.assertEqual(scan, [graphtec.RESP_REGMARK_OK] if registered else [])

    def test_resizing_a_sample_preserves_spots_and_reopens_mixed_sizes(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "marked.json"
            self.guide = starting_guide.Guide(path)
            self.guide.registered = True
            connection = ("auto", (1, 2))
            first = self.prepare(connection=connection)
            self.guide.record(first)
            first_rect = self.guide.spot_rect(1)
            self.guide.configure_sample("card", 88.0)
            self.assertNotEqual(self.guide.spot, 1)
            self.assertEqual(self.guide.completed, 0)
            self.assertEqual(self.guide.spot_rect(1), first_rect)
            self.assertTrue(all(not area.overlaps(first_rect.grown(testcut.DEFAULT_GAP_MM))
                                for area in self.guide.fresh_spots().values()))
            card_spot = self.guide.spot
            card = self.guide.prepare(connection, self.options, None)
            self.assertEqual(card.sheet.cells[0].rect, self.guide.spot_rect(card_spot))
            x_values = [x for x, _ in card.sheet.paths[0].points]
            self.assertAlmostEqual(max(x_values) - min(x_values), 63.0)
            self.guide.record(card)
            self.guide.persist()

            reopened = starting_guide.Guide(path)
            self.assertEqual(reopened.trial, card)
            self.assertEqual(reopened.spot_for_point(first_rect.x + 2, first_rect.y + 2), 1)
            reopened.select_spot(1)
            self.assertEqual((reopened.shape, reopened.size_mm), ("square", 15.0))
            self.assertEqual(reopened.prepare(connection, self.options, None), first)
            reopened.select_spot(card_spot)
            self.assertEqual((reopened.shape, reopened.size_mm), ("card", 88.0))
            self.assertEqual(reopened.prepare(connection, self.options, None), card)

    def test_turning_to_a_larger_sample_when_no_space_fits_keeps_the_tested_spot(self):
        self.guide.placement = starting_guide.Placement(65, 65, (0, 0), "auto")
        trial = self.prepare()
        self.guide.record(trial)
        with self.assertRaisesRegex(ValueError, "No fresh spot fits"):
            self.guide.configure_sample("card", 88.0)
        self.assertEqual(self.guide.trial, trial)
        self.assertEqual(self.guide.size_mm, 15.0)
        self.guide.configure_sample("triangle", 15.0)
        self.assertEqual(self.guide.spot, 2)
        self.assertEqual(self.guide.prepare(self.connection, self.options, None).sheet.shape, "triangle")

    def test_repeats_and_tested_settings_do_not_require_an_outcome(self):
        trial = self.prepare()
        self.assertTrue(self.guide.can_cut)
        self.guide.record(trial)
        self.assertTrue(self.guide.can_cut)
        self.assertEqual(self.guide.tested_settings.passes, 1)
        self.assertEqual(self.guide.outcome, "")
        for outcome, suggestion in (("rough", "Reduce force"), ("shallow", "blade is seated"),
                                    ("skipped", "power-cycle"), ("mat", "Reduce blade depth"),
                                    ("chipped", "Cut Settings A"), ("fibers", "full pass"),
                                    ("clean", "Use the completed pass count")):
            with self.subTest(outcome=outcome):
                self.guide.inspect(outcome)
                self.assertTrue(self.guide.can_cut)
                self.assertIn(suggestion, self.guide.advice())
        for _ in range(1, starting_guide.MAX_JOB_PASSES):
            self.guide.record(trial)
        self.assertFalse(self.guide.can_cut)
        with self.assertRaisesRegex(ValueError, f"{starting_guide.MAX_JOB_PASSES} passes"):
            self.prepare()
        self.assertEqual(self.guide.tested_settings.passes, starting_guide.MAX_JOB_PASSES)

    def test_spots_start_at_bottom_left_then_move_right_and_up(self):
        trial = self.prepare(portrait=True)
        first = trial.sheet.cells[0].rect
        self.assertEqual(first.x, 15)
        self.assertAlmostEqual(first.y2, 279.4 - 15)
        self.guide.record(trial)
        self.guide.record(trial)
        self.guide.next_spot()
        second = self.prepare().sheet.cells[0].rect
        self.assertEqual((second.x, second.y), (35, first.y))
        self.assertEqual(self.guide.history[0].rect, first)
        self.assertEqual(self.guide.history_text(), "Spot 1 · 2 passes · Force 25 · Speed 25 · Depth 7")
        self.guide.spot = 10
        next_row = self.prepare().sheet.cells[0].rect
        self.assertEqual(next_row.x, first.x)
        self.assertLess(next_row.y, first.y)

    def test_marked_sheet_uses_scan_before_writing_paths_and_reopens_with_counts(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "marked.json"
            self.guide = starting_guide.Guide(path)
            self.assertFalse(self.guide.registered)
            self.guide.registered = True
            proof = Path(folder) / "marks.svg"
            self.guide.write_marked_sheet(proof, None, dict(self.options, portrait=True))
            trial = self.prepare(connection=("auto", (1, 2), None))
            self.assertTrue(trial.sheet.registration)
            scan = next(i for i, step in enumerate(trial.sheet.steps) if step.kind == "expect")
            self.assertEqual(trial.sheet.steps[scan].expect, graphtec.RESP_REGMARK_OK)
            self.assertFalse(verify.decode(b"".join(step.data for step in trial.sheet.steps[:scan])).paths)
            self.assertEqual(trial.sheet.cells[0].rect.x, 20)
            self.assertAlmostEqual(trial.sheet.cells[0].rect.y2, 279.4 - 20)
            self.assertIn('width="215.9mm" height="279.4mm"', proof.read_text())
            self.assertIn('registration-marks-DO-NOT-CUT', proof.read_text())
            self.assertNotIn('id="page"', proof.read_text())
            self.guide.record(trial)
            self.guide.moved()
            self.assertTrue(self.guide.can_cut)
            self.guide.next_spot()
            next_trial = self.prepare(connection=trial.connection, force=29)
            self.guide.record(next_trial)
            self.guide.persist()
            restored = starting_guide.Guide(path)
            self.assertEqual(restored.trial, next_trial)
            self.assertFalse(restored.refusal(("auto", (1, 2), None), next_trial.sheet.device.name))
            self.assertFalse(restored.refusal(("auto", (1, 2)), next_trial.sheet.device.name))
            self.assertEqual(restored.spots, self.guide.spots)
            self.assertIn("Spot 1 · 1 pass", restored.history_text())
            self.assertIn("Spot 2 · 1 pass", restored.history_text())
            self.assertTrue(restored.can_cut)

    def test_marked_sheet_interrupted_during_a_pass_needs_fresh_spot(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "marked.json"
            self.guide = starting_guide.Guide(path)
            self.guide.registered = True
            trial = self.prepare()
            self.guide.record(trial)
            self.guide.persist(pending=trial)
            restored = starting_guide.Guide(path)
            self.assertEqual(restored.completed, 1)
            self.assertFalse(restored.can_cut)
            self.assertIsNone(restored.tested_settings)
            restored.next_spot()
            self.assertTrue(restored.history[0].uncertain)
            self.assertTrue(restored.can_cut)

    @unittest.skipUnless(shutil.which("rsvg-convert") and shutil.which("pdftoppm") and shutil.which("pdfinfo"),
                         "requires SVG and PDF rendering tools")
    def test_marked_sheet_print_geometry_matches_the_scan_window(self):
        with tempfile.TemporaryDirectory() as folder:
            source, pdf = (Path(folder) / name for name in ("marks.svg", "marks.pdf"))
            self.guide.registered = True
            self.guide.write_marked_sheet(source, None, dict(self.options, portrait=True))
            subprocess.run(["rsvg-convert", "-f", "pdf", "-o", str(pdf), str(source)], check=True)
            report = sheet.measure(pdf, self.guide.placement.marks, dpi=300)
            self.assertEqual(len(report.found), 3)
            self.assertFalse(report.problems(215.9, 279.4))

    def test_changed_cutter_or_uncertain_pass_requires_a_fresh_spot(self):
        trial = self.prepare()
        self.guide.record(trial)
        with self.assertRaisesRegex(ValueError, "cutter changed"):
            self.prepare(connection=("ble", "AA"))
        self.guide.record(trial, starting_guide.PassUnconfirmed("completion unknown"))
        self.assertEqual(self.guide.completed, 1)
        self.assertIsNone(self.guide.tested_settings)
        with self.assertRaisesRegex(ValueError, "fresh spot"):
            self.prepare()
        self.guide.next_spot()
        self.assertNotEqual(self.prepare().sheet.stream, trial.sheet.stream)

    def test_wait_for_completion_and_preflight_error_classes(self):
        trial = self.prepare()
        cutter = mock.Mock(can_read=True)
        cutter.status.return_value = "ready"
        cutter.run.return_value = len(trial.sheet.stream)
        self.assertEqual(starting_guide.run_pass(cutter, trial), len(trial.sheet.stream))
        self.assertEqual(cutter.run.call_args, mock.call(trial.sheet.steps, wait_done=True))
        cutter.can_read = False
        with self.assertRaisesRegex(transport.TransportError, "reports completion"):
            starting_guide.run_pass(cutter, trial)
        self.assertEqual(cutter.run.call_count, 1)
        cutter.can_read = True
        cutter.run.side_effect = transport.TransportError("timed out")
        with self.assertRaises(starting_guide.PassUnconfirmed):
            starting_guide.run_pass(cutter, trial)

    def test_moving_a_blank_sheet_preserves_the_confirmed_count(self):
        self.guide.record(self.prepare())
        self.guide.moved()
        self.assertFalse(self.guide.can_cut)
        self.assertEqual(self.guide.current_spot.count, "1 pass")
        self.assertIn("The sheet moved", self.guide.status())

    def test_rejected_registration_does_not_increment_or_lock_the_test_spot(self):
        self.guide.registered = True
        trial = self.prepare()
        self.guide.record(trial)
        cutter = mock.Mock(can_read=True)
        cutter.status.return_value = "ready"
        cutter.run.side_effect = transport.RegistrationFailed()
        with self.assertRaises(transport.RegistrationFailed) as failed:
            starting_guide.run_pass(cutter, trial)
        self.guide.record(trial, failed.exception)
        self.assertEqual(self.guide.completed, 1)
        self.assertTrue(self.guide.can_cut)

    def test_single_spot_must_fit_and_can_only_take_one_settings_combination(self):
        base = graphtec.CutSettings()
        with self.assertRaisesRegex(ValueError, "outside the test sheet"):
            testcut.build(self.device, base, spot=10000)
        with self.assertRaisesRegex(ValueError, "one settings combination"):
            testcut.build(self.device, base, forces=(20, 25), spot=2)
        self.assertEqual(testcut.build(self.device, base, spot=2).cells[0].label, "r1c2")

    def test_a_revisited_spot_on_a_marked_sheet_still_guards_changed_settings(self):
        self.guide.registered = True
        first = self.prepare()
        self.guide.record(first)
        self.assertEqual(self.guide.spot, 1)
        self.guide.select_spot(2)
        self.guide.record(self.prepare(force=30))
        self.guide.select_spot(1)

        self.assertEqual(self.guide.trial, first)
        self.assertEqual(self.guide.completed, 1)
        self.assertFalse(self.guide.interrupted)
        self.assertTrue(self.guide.can_cut)
        self.assertFalse(self.guide.settings_differ(self.options))
        self.assertTrue(self.guide.settings_differ(dict(self.options, force=30)))
        self.assertTrue(self.guide.settings_differ(dict(self.options, speed=5)))
        self.assertTrue(self.guide.settings_differ(dict(
            self.options, blade_options=dict(self.options["blade_options"], blade_diameter_mm=0.45))))
        changed = self.prepare(blade_options=dict(self.options["blade_options"], acceleration=1))
        self.assertEqual(changed.settings.acceleration, 1)
        self.assertEqual(changed.sheet.cells[0].rect, first.sheet.cells[0].rect)
        self.assertEqual(self.prepare(), first)

        self.guide.select_spot(2)
        self.assertTrue(self.guide.settings_differ(self.options))
        self.assertFalse(self.guide.settings_differ(dict(self.options, force=30)))

    def test_a_revisited_marked_spot_survives_restart_and_repeats_its_original_path(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "marked.json"
            self.guide = starting_guide.Guide(path)
            self.guide.registered = True
            connection = ("auto", (1, 2))
            first = self.prepare(shape="studio", connection=connection, depth=None)
            self.guide.record(first)
            self.guide.select_spot(2)
            self.guide.record(self.prepare(connection=connection, force=30))
            self.guide.select_spot(1)
            self.assertCountEqual([spot.number for spot in self.guide.spots], [1, 2])
            self.guide.persist()

            self.guide = starting_guide.Guide(path)
            self.assertEqual(self.guide.completed, 1)
            self.assertEqual(self.guide.tested_settings.depth, None)
            self.assertTrue(self.guide.settings_differ(self.options))
            with self.assertRaisesRegex(ValueError, "cutter changed"):
                self.prepare(connection=("auto", (1, 3)), depth=None)
            repeated = self.prepare(connection=connection, depth=None)
            self.assertEqual(repeated, first)
            self.guide.record(repeated)
            self.assertEqual(self.guide.tested_settings.passes, 2)

    def test_a_legacy_archived_spot_keeps_its_count_and_requires_a_fresh_spot(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "marked.json"
            self.guide = starting_guide.Guide(path)
            self.guide.registered = True
            first = self.prepare()
            self.guide.record(first)
            self.guide.next_spot()
            second = self.prepare(force=30)
            self.guide.record(second)
            self.guide.persist()
            before = json.loads(path.read_text())
            before.pop("spot_trials")
            before["spot_settings"] = {"1": vars(first.settings)}
            path.write_text(json.dumps(before))

            self.guide = starting_guide.Guide(path)
            self.assertEqual(self.prepare(force=30), second)
            self.guide.select_spot(1)
            self.assertEqual(self.guide.completed, 1)
            self.assertFalse(self.guide.can_cut)
            with self.assertRaisesRegex(ValueError, "original cutter and settings are unavailable"):
                self.prepare()
            self.guide.persist()

            self.guide = starting_guide.Guide(path)
            self.assertTrue(self.guide.registered)
            self.assertEqual(self.guide.completed, 1)
            self.guide.next_spot()
            self.assertEqual(self.guide.spot, 3)
            self.assertTrue(self.guide.can_cut)
            self.assertEqual(self.prepare().settings.force, 25)

    def test_a_spot_beyond_the_sheet_is_refused_and_has_tests_sees_any_progress(self):
        fresh = starting_guide.Guide()
        self.assertFalse(fresh.has_tests)
        trial = self.prepare()
        self.guide.record(trial)
        self.assertTrue(self.guide.has_tests)

        limit = self.guide.max_spots
        self.assertGreater(limit, 1)
        self.guide.select_spot(limit)
        self.assertEqual(self.guide.spot, limit)
        with self.assertRaisesRegex(ValueError, "spots"):
            self.guide.select_spot(limit + 1)
        self.assertEqual(self.guide.spot, limit)
        with self.assertRaisesRegex(ValueError, "positive integer"):
            self.guide.select_spot(0)

        moved = starting_guide.Guide()
        moved.select_spot(3)
        self.assertTrue(moved.has_tests, "an untouched later spot is still a test in progress")

    def test_spot_selection_and_keyboard_navigation_with_guarded_history(self):
        # 1. Start on spot 1, record 2 passes
        trial = self.prepare()
        self.guide.record(trial)
        self.guide.record(trial)
        self.assertEqual(self.guide.spot, 1)
        self.assertEqual(self.guide.completed, 2)
        self.assertEqual(self.guide.spot_status, "2 completed passes")

        # 2. Select a later untouched spot 5
        self.guide.select_spot(5)
        self.assertEqual(self.guide.spot, 5)
        self.assertEqual(self.guide.completed, 0)
        self.assertTrue(self.guide.can_cut)
        self.assertFalse(self.guide.interrupted)
        self.assertEqual(self.guide.spot_status, "Untouched stock")

        # Record a pass on spot 5
        trial5 = self.prepare()
        self.guide.record(trial5)
        self.assertEqual(self.guide.completed, 1)

        # 3. Return to tested spot 1 on blank stock
        self.guide.select_spot(1)
        self.assertEqual(self.guide.spot, 1)
        self.assertEqual(self.guide.completed, 2)
        # On blank stock, returning to an archived spot is guarded (interrupted because registration is off)
        self.assertTrue(self.guide.interrupted)
        self.assertFalse(self.guide.can_cut)
        self.assertEqual(self.guide.spot_status, "2 passes · Interrupted")

        # 4. Next spot finds next untouched stock (skips 1 and 5)
        self.guide.next_spot()
        self.assertEqual(self.guide.spot, 2)
        self.assertEqual(self.guide.completed, 0)
        self.assertTrue(self.guide.can_cut)

        # 5. Point lookup on placement
        rect2 = self.guide.spot_rect(2)
        self.assertIsNotNone(rect2)
        self.assertEqual(self.guide.spot_for_point(rect2.x + 2, rect2.y + 2), 2)
        self.assertIsNone(self.guide.spot_for_point(0, 0))

        # 6. Settings differ detection
        self.assertFalse(self.guide.settings_differ(self.options))
        self.guide.record(self.prepare())
        self.assertTrue(self.guide.settings_differ(dict(self.options, force=10)))
        self.assertTrue(self.guide.settings_differ(dict(self.options, speed=5)))
        self.assertTrue(self.guide.settings_differ(dict(self.options, depth=2)))


class TestPassLog(unittest.TestCase):
    """Each confirmed pass keeps the settings it was cut with."""

    def setUp(self):
        self.guide = starting_guide.Guide()
        self.options = {
            "cutter": "Silhouette Cameo 4", "sheet": "Letter", "mat": "cameo-12x12",
            "offset_mm": (0, 0), "force": 10, "speed": 3, "depth": 7,
            "blade_options": {"blade_diameter_mm": 0.9},
        }

    def cut(self, times=1, **changes):
        self.options.update(changes)
        for _ in range(times):
            self.guide.record(self.guide.prepare(("auto", None), self.options, None))

    def test_one_setting_reuses_it_and_names_it_once(self):
        self.cut(2)
        self.assertEqual(self.guide.pass_log, (starting_guide.PassSettings(10, 3, 7),) * 2)
        self.assertEqual(self.guide.history_text(), "Spot 1 · 2 passes · Force 10 · Speed 3 · Depth 7")
        settings = self.guide.tested_settings
        self.assertEqual((settings.force, settings.passes, settings.light_passes), (10, 2, 0))

    def test_light_passes_then_full_come_back_as_staged_passes(self):
        self.cut(2)
        self.cut(1, force=25, speed=8)
        self.assertEqual(self.guide.history_text(),
                         "Spot 1 · 3 passes: 2 at Force 10 · Speed 3 · Depth 7, then 1 at Force 25 · Speed 8 · Depth 7")
        self.assertIn("2 at Force 10", self.guide.status())
        settings = self.guide.tested_settings
        self.assertEqual((settings.passes, settings.force, settings.speed), (3, 25, 8))
        self.assertEqual((settings.light_passes, settings.light_force, settings.light_speed), (2, 10, 3))
        self.assertEqual(self.guide.mixed_reason, "")

    def test_settings_that_staged_passes_cannot_repeat_give_none_and_a_reason(self):
        self.cut(1)
        self.cut(1, force=20)
        self.cut(1, force=30)
        self.assertIsNone(self.guide.tested_settings, "three settings")
        self.assertIn("more than two settings", self.guide.mixed_reason)
        self.guide.next_spot()
        self.cut(1, force=10, depth=7)
        self.cut(1, force=20, depth=8)
        self.assertIsNone(self.guide.tested_settings, "depth changed between the runs")

    def test_the_log_follows_its_spot_and_survives_reopening(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "marked.json"
            self.guide = starting_guide.Guide(path)
            self.guide.registered = True
            self.options["blade_options"] = {"blade_diameter_mm": 0.9}
            connection = ("auto", (1, 2))
            for force in (10, 25):
                self.options["force"] = force
                self.guide.record(self.guide.prepare(connection, self.options, None))
            first = self.guide.pass_log
            self.guide.next_spot()
            self.assertEqual(self.guide.pass_log, ())
            self.options["force"] = 15
            self.guide.record(self.guide.prepare(connection, self.options, None))
            self.guide.persist()

            reopened = starting_guide.Guide(path)
            self.assertEqual(reopened.pass_log, (starting_guide.PassSettings(15, 3, 7),))
            reopened.select_spot(1)
            self.assertEqual(reopened.pass_log, first)
            self.assertEqual(reopened.tested_settings.light_force, 10)

    def test_history_saved_before_the_log_still_opens_and_counts(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "marked.json"
            self.guide = starting_guide.Guide(path)
            self.guide.registered = True
            self.guide.record(self.guide.prepare(("auto", (1, 2)), self.options, None))
            self.guide.persist()
            data = json.loads(path.read_text())
            del data["pass_log"]
            path.write_text(json.dumps(data))
            reopened = starting_guide.Guide(path)
            self.assertEqual((reopened.completed, reopened.pass_log), (1, ()))
            self.assertEqual(reopened.tested_settings.passes, 1, "an older file falls back to the count")

    def test_setting_names_show_once_a_spot_uses_two(self):
        settings = self.guide.prepare(("auto", None), self.options, None)
        self.guide.record(settings, name="A")
        self.guide.record(settings, name="A")
        self.assertEqual(self.guide.history_text(), "Spot 1 · 2 passes · Force 10 · Speed 3 · Depth 7")
        self.options["force"] = 25
        self.guide.record(self.guide.prepare(("auto", None), self.options, None), name="B")
        self.assertEqual(self.guide.history_text(), "Spot 1 · 3 passes: 2 at A: Force 10 · Speed 3 · Depth 7, "
                                                    "then 1 at B: Force 25 · Speed 3 · Depth 7")
        self.assertEqual(self.guide.tested_settings.light_passes, 2)

    def test_only_an_identical_pass_skips_the_confirmation(self):
        first = self.guide.prepare(("auto", None), self.options, None)
        self.assertFalse(self.guide.is_repeat(first), "the first pass is always confirmed")
        self.guide.record(first)
        same = self.guide.prepare(("auto", None), self.options, None)
        self.assertTrue(self.guide.is_repeat(same))
        self.assertEqual(self.guide.pass_label(self.options), "Repeat One Pass")
        self.options["force"] = 20
        changed = self.guide.prepare(("auto", None), self.options, None)
        self.assertFalse(self.guide.is_repeat(changed))
        self.assertEqual(self.guide.pass_label(self.options), "Cut One Pass…")
        self.assertEqual(self.guide.confirm_title(changed), "Cut With Changed Settings?")
        self.assertIn("This spot was last cut at Force 10", self.guide.prompt(changed))

    def test_the_prompt_says_where_a_scrap_goes_not_to_load_a_whole_sheet(self):
        trial = self.guide.prepare(("auto", None), self.options, None)
        prompt = self.guide.prompt(trial)
        spot = trial.sheet.cells[0].rect
        self.assertIn("Put a scrap at least", prompt)
        self.assertIn(f"{spot.x:g}–{spot.x2:g} mm from the left", prompt)
        self.assertNotIn("279.4 × 215.9", prompt)

