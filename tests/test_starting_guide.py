import unittest
from unittest import mock

from cut_proxies import graphtec, job, starting_guide, testcut, transport, verify


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

    def test_repeating_uses_exact_same_geometry_settings_and_return_to_origin(self):
        trial = self.prepare()
        self.assertEqual(len(verify.decode(trial.sheet.stream).paths), 1)
        self.assertEqual(trial.settings.passes, 1)
        self.assertFalse(trial.sheet.registration)
        self.assertNotIn(graphtec.frame("FY0"), trial.sheet.stream)
        self.assertEqual(trial.sheet.steps[-1].data, graphtec.frame(graphtec.return_to_start_commands(self.device)))
        self.guide.record(trial)
        self.assertIs(self.prepare(force=32, offset_mm=(2, -1)), trial)
        self.guide.record(trial)
        self.assertEqual(self.guide.tested_settings.passes, 2)
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

    def test_inspection_offers_repeat_only_for_a_clean_edge_that_is_still_attached(self):
        trial = self.prepare()
        self.assertTrue(self.guide.can_cut)
        self.guide.record(trial)
        self.assertTrue(self.guide.awaiting_result)
        self.assertFalse(self.guide.can_cut)
        for outcome, suggestion in (("rough", "Reduce force"), ("shallow", "blade is seated"),
                                    ("skipped", "power-cycle"), ("mat", "Reduce blade depth"),
                                    ("clean", "Use the completed pass count")):
            with self.subTest(outcome=outcome):
                self.guide.inspect(outcome)
                self.assertFalse(self.guide.can_cut)
                self.assertIn(suggestion, self.guide.advice())
        self.guide.inspect("holding")
        self.assertTrue(self.guide.can_cut)
        self.assertIn("same spot", self.guide.advice())
        self.guide.record(trial)
        self.assertTrue(self.guide.awaiting_result)
        self.assertFalse(self.guide.can_cut)

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

    def test_single_spot_must_fit_and_can_only_take_one_settings_combination(self):
        base = graphtec.CutSettings()
        with self.assertRaisesRegex(ValueError, "outside the test sheet"):
            testcut.build(self.device, base, spot=10000)
        with self.assertRaisesRegex(ValueError, "one settings combination"):
            testcut.build(self.device, base, forces=(20, 25), spot=2)
        self.assertEqual(testcut.build(self.device, base, spot=2).cells[0].label, "r1c2")
