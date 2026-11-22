"""The settings-finding test sheet, and the standing calibration offset.

Both of these change what the blade does without changing the geometry on
the page, so the checks here are about the stream: that each test square
really carries its own settings, and that a calibration offset moves every
cut by exactly the amount asked for and no more.
"""

import dataclasses
import unittest
from unittest import mock

from cutline import (geometry, graphtec, job as job_mod, presets,
                         testcut, verify)
from cutline.graphtec import CutSettings


def cameo4():
    return graphtec.device_by_name("Silhouette Cameo 4")


class TestCombinations(unittest.TestCase):

    def setUp(self):
        self.base = CutSettings(force=30, speed=2, passes=2, depth=7)

    def test_manual_depth_and_retracted_autoblade_are_distinct(self):
        cells = testcut.combinations(self.base, depths="-1,0,7")
        self.assertEqual([c.depth for c in cells], [None, 0, 7])

    def test_an_unvaried_axis_keeps_the_base_value(self):
        out = testcut.combinations(self.base, forces="25,33")
        self.assertEqual([s.force for s in out], [25, 33])
        self.assertTrue(all(s.speed == 2 and s.passes == 2 for s in out))

    def test_axes_multiply(self):
        out = testcut.combinations(self.base, forces="25,33", passes="1,2,3")
        self.assertEqual(len(out), 6)

    def test_nothing_varied_gives_exactly_the_base(self):
        out = testcut.combinations(self.base)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].force, 30)

    def test_lists_and_strings_are_both_accepted(self):
        a = testcut.combinations(self.base, forces="25,33")
        b = testcut.combinations(self.base, forces=[25, 33])
        self.assertEqual([s.force for s in a], [s.force for s in b])

    def test_junk_in_a_list_is_dropped_not_crashed_on(self):
        out = testcut.combinations(self.base, forces="25, ,oops,33")
        self.assertEqual([s.force for s in out], [25, 33])


class TestLayout(unittest.TestCase):

    def test_squares_are_the_requested_size_and_do_not_overlap(self):
        ts = testcut.build(cameo4(), CutSettings(), forces="20,25,30",
                           passes="1,2", size_mm=15.0, gap_mm=5.0)
        self.assertEqual(len(ts.cells), 6)
        for c in ts.cells:
            self.assertAlmostEqual(c.rect.w, 15.0)
            self.assertAlmostEqual(c.rect.h, 15.0)
        rects = [c.rect for c in ts.cells]
        for i in range(len(rects)):
            for k in range(i + 1, len(rects)):
                self.assertFalse(rects[i].overlaps(rects[k]))

    def test_everything_stays_inside_the_sheet(self):
        ts = testcut.build(cameo4(), CutSettings(), forces="1,5,10,15,20,25",
                           passes="1,2,3", sheet_w_mm=215.9, sheet_h_mm=279.4)
        e = ts.extent()
        self.assertGreaterEqual(e.x, 0)
        self.assertGreaterEqual(e.y, 0)
        self.assertLessEqual(e.x2, 215.9)
        self.assertLessEqual(e.y2, 279.4)

    def test_too_many_combinations_is_refused(self):
        with self.assertRaises(ValueError):
            testcut.build(cameo4(), CutSettings(),
                          forces=",".join(str(i) for i in range(1, 34)),
                          passes="1,2,3")

    def test_squares_too_big_for_the_sheet_are_refused(self):
        with self.assertRaises(ValueError) as ctx:
            testcut.build(cameo4(), CutSettings(), forces="1,2,3,4,5,6",
                          passes="1,2,3,4,5", size_mm=90.0)
        self.assertIn("usable", str(ctx.exception))

    def test_the_map_has_one_row_per_square(self):
        ts = testcut.build(cameo4(), CutSettings(), forces="20,30")
        self.assertEqual(len(ts.map_rows()), len(ts.cells))
        self.assertIn("force 20", ts.map_rows()[0][1])


class TestStream(unittest.TestCase):

    def setUp(self):
        self.ts = testcut.build(
            cameo4(), CutSettings(force=30, speed=2, passes=1, depth=7),
            forces="25,29,33", passes="1,2")

    def test_each_force_reaches_the_stream(self):
        d = verify.decode(self.ts.stream)
        forces = {int(c[2:].split(",")[0])
                  for c in d.commands if c.startswith("FX")}
        self.assertEqual(forces, {25, 29, 33})

    def test_pass_counts_are_honoured_per_square(self):
        # Three squares at one pass and three at two: nine paths, not twelve.
        d = verify.decode(self.ts.stream)
        self.assertEqual(len(d.paths), 3 * 1 + 3 * 2)

    def test_sample_map_reports_effective_values_and_checks_the_wire(self):
        ts = testcut.build(cameo4(), CutSettings(), forces="34", speeds="31", passes="2", depths="11")
        self.assertIn("force 33, speed 30, 2 pass(es), depth 10", ts.map_rows()[0][1])
        commands = verify.decode(ts.stream).commands
        self.assertIn("FX33,1", commands)
        self.assertIn("!30,1", commands)
        self.assertIn("TF10,1", commands)
        self.assertEqual(len(verify.decode(ts.stream).paths), 2)

        original = graphtec.CutJob.plan_matrix

        def corrupt(plan, *args, **kwargs):
            steps = original(plan, *args, **kwargs)
            return [dataclasses.replace(step, data=step.data.replace(b"TF10,1\x03", b"TF8,1\x03"))
                    for step in steps]

        with mock.patch.object(graphtec.CutJob, "plan_matrix", corrupt):
            with self.assertRaisesRegex(ValueError, "settings for each sample"):
                testcut.build(cameo4(), CutSettings(), depths="10")

    def test_sample_refuses_zero_passes_and_mixed_manual_and_automatic_depth(self):
        with self.assertRaisesRegex(ValueError, "at least one cutting pass"):
            testcut.build(cameo4(), CutSettings(), passes="0")
        with self.assertRaisesRegex(ValueError, "separate sheets"):
            testcut.build(cameo4(), CutSettings(), depths="-1,7")

    def test_every_square_comes_out_the_right_size(self):
        d = verify.decode(self.ts.stream)
        for box in d.path_bboxes():
            self.assertAlmostEqual(box.w, 15.0, delta=0.06)
            self.assertAlmostEqual(box.h, 15.0, delta=0.06)

    def test_squares_are_closed_loops(self):
        d = verify.decode(self.ts.stream)
        self.assertEqual(len(d.closed_paths), len(d.paths))

    def test_registration_is_off_by_default(self):
        d = verify.decode(self.ts.stream)
        self.assertFalse(d.preamble_present)
        self.assertIsNone(d.scan_command)

    def test_registration_can_be_turned_on(self):
        ts = testcut.build(cameo4(), CutSettings(), forces="20",
                           registration=True)
        d = verify.decode(ts.stream)
        self.assertTrue(d.preamble_present)
        self.assertEqual(d.scan_command, "TB123")


class TestCalibrationOffset(unittest.TestCase):
    """A standing offset must move everything, by exactly its own size."""

    def _first_card(self, job):
        return verify.decode(job.stream).path_bboxes()[0]

    def test_an_offset_moves_every_cut(self):
        plain = job_mod.build(preset_name="Plain paper test",
                              offset_mm=(0.0, 0.0))
        moved = job_mod.build(preset_name="Plain paper test",
                              offset_mm=(0.5, -0.3))
        a, b = self._first_card(plain), self._first_card(moved)
        self.assertAlmostEqual(b.x - a.x, 0.5, delta=0.03)
        self.assertAlmostEqual(b.y - a.y, -0.3, delta=0.03)

    def test_it_moves_the_last_card_by_the_same_amount(self):
        # A translation, not a scale: the error must not grow across the
        # sheet, which is what would happen if it were applied per-axis in
        # the wrong place.
        plain = job_mod.build(preset_name="Plain paper test",
                              offset_mm=(0.0, 0.0))
        moved = job_mod.build(preset_name="Plain paper test",
                              offset_mm=(0.5, -0.3))
        a = verify.decode(plain.stream).path_bboxes()[-1]
        b = verify.decode(moved.stream).path_bboxes()[-1]
        self.assertAlmostEqual(b.x - a.x, 0.5, delta=0.03)
        self.assertAlmostEqual(b.y - a.y, -0.3, delta=0.03)

    def test_card_size_is_unchanged_by_an_offset(self):
        moved = job_mod.build(preset_name="Plain paper test",
                              offset_mm=(0.5, -0.3))
        for box in verify.decode(moved.stream).path_bboxes():
            self.assertAlmostEqual(box.w, 63.0, delta=0.1)
            self.assertAlmostEqual(box.h, 88.0, delta=0.1)

    def test_a_calibrated_job_still_verifies(self):
        # The check has to know about the offset, or a correctly calibrated
        # job reads as a position error exactly its own size and is refused.
        moved = job_mod.build(preset_name="Plain paper test",
                              offset_mm=(0.5, -0.3))
        self.assertEqual(moved.blocked, [])

    def test_zero_offset_is_reported_as_none(self):
        plain = job_mod.build(preset_name="Plain paper test",
                              offset_mm=(0.0, 0.0))
        self.assertEqual(dict(plain.summary())["Calibration"], "none")

    def test_a_nonzero_offset_is_stated_in_the_summary(self):
        moved = job_mod.build(preset_name="Plain paper test",
                              offset_mm=(0.5, -0.3))
        self.assertIn("+0.5", dict(moved.summary())["Calibration"])


class TestProofMap(unittest.TestCase):

    def test_the_map_is_labelled_and_says_it_is_not_a_cut_file(self):
        ts = testcut.build(cameo4(), CutSettings(), forces="20,30")
        svg = testcut.write_proof(None, ts, 215.9, 279.4)
        self.assertIn("MAP, NOT A CUT FILE", svg)
        self.assertIn("<text", svg)
        self.assertEqual(svg.count("<text"), 2 * len(ts.cells))

    def test_the_map_is_millimetre_locked_like_every_other_output(self):
        ts = testcut.build(cameo4(), CutSettings(), forces="20")
        svg = testcut.write_proof(None, ts, 215.9, 279.4)
        self.assertIn('width="215.9mm"', svg)
        self.assertIn('viewBox="0 0 215.9 279.4"', svg)



class TestStudioPattern(unittest.TestCase):
    def test_uses_captured_size_and_cuts_all_triangle_passes_first(self):
        settings = CutSettings(passes=2, path_order="nearest")
        sheet = testcut.build(cameo4(), settings, shape="studio", end_position="start")
        self.assertEqual(sheet.size_mm, 10)
        self.assertEqual([p.label for p in sheet.paths], ["test triangle", "test square"])
        first, second, third, fourth = verify.decode(sheet.stream).paths
        self.assertEqual(first, second)
        self.assertEqual(third, fourth)
        self.assertEqual(first[0], (19.5, 18.1))
        self.assertEqual(third[:3], [(16, 15), (15, 15), (15, 25)])
        self.assertTrue(sheet.stream.endswith(graphtec.frame("TB50,0")))
        self.assertEqual(testcut.write_proof(None, sheet, 215.9, 279.4).count("<polyline"), 2)

    def test_corrupt_motion_is_rejected(self):
        original = graphtec.CutJob._path_commands

        def wrong_position(*args):
            commands = original(*args)
            commands[1] = "D400,400"
            return commands

        with mock.patch.object(graphtec.CutJob, "_path_commands", wrong_position):
            with self.assertRaisesRegex(ValueError, "does not match the requested geometry"):
                testcut.build(cameo4(), CutSettings(), shape="studio")

    def test_off_sheet_calibration_and_too_wide_samples_are_refused(self):
        with self.assertRaisesRegex(ValueError, "outside the cut window"):
            testcut.build(cameo4(), CutSettings(), shape="studio",
                          margin_mm=1, offset_mm=(-3, 0))
        with self.assertRaisesRegex(ValueError, "exceed the sheet width"):
            testcut.build(cameo4(), CutSettings(), shape="studio", size_mm=200)

if __name__ == "__main__":
    unittest.main()
