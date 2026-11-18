import math
import unittest

from cut_proxies import graphtec, job, template, toolpath, verify

SQUARE = template.CutPath(((20, 20), (30, 20), (30, 30), (20, 30)), closed=True)
LINE = template.CutPath(((50, 25), (65, 26), (55, 31)), closed=False)


class TestToolpathSchedule(unittest.TestCase):
    def test_seam_overlap_follows_both_sides_of_the_original_contour(self):
        settings = graphtec.CutSettings(overcut_mm=2)
        (source, points), = toolpath.schedule([SQUARE], settings)
        self.assertEqual(source, 0)
        self.assertEqual(points, ((20, 22), *SQUARE.points, (20, 20), (22, 20)))
        self.assertAlmostEqual(sum(math.dist(a, b) for a, b in zip(points, points[1:])), 44)

    def test_large_overcut_stops_after_one_lap_and_ignores_open_paths(self):
        settings = graphtec.CutSettings(overcut_mm=1000)
        (_, points), (_, line) = toolpath.schedule([SQUARE, LINE], settings)
        self.assertEqual(len(points), 13)
        self.assertEqual(points[::4], ((20, 20),) * 4)
        self.assertEqual(line, LINE.points)

    def test_path_order_groups_closed_and_open_passes_in_place(self):
        settings = graphtec.CutSettings(passes=2, pass_order="path")
        paths = toolpath.schedule([SQUARE, LINE], settings)
        self.assertEqual([index for index, _ in paths], [0, 1, 1])
        self.assertEqual(paths[0][1], SQUARE.points + SQUARE.points + (SQUARE.points[0],))
        self.assertEqual(paths[1][1], LINE.points)
        self.assertEqual(paths[2][1], LINE.points)

    def test_reverse_pass_can_continue_open_path_without_a_lift(self):
        settings = graphtec.CutSettings(passes=2, pass_order="path", alternate_direction=True)
        (_, points), = toolpath.schedule([LINE], settings)
        self.assertEqual(points, LINE.points + tuple(reversed(LINE.points[:-1])))

    def test_sheet_order_reverses_every_other_pass_at_the_authored_entry(self):
        settings = graphtec.CutSettings(passes=2, alternate_direction=True)
        paths = toolpath.schedule([SQUARE, LINE], settings)
        self.assertEqual([index for index, _ in paths], [0, 1, 0, 1])
        self.assertEqual(paths[2][1], ((20, 20), (20, 30), (30, 30), (30, 20), (20, 20)))
        self.assertEqual(paths[3][1], tuple(reversed(LINE.points)))

    def test_nearest_uses_current_endpoint_without_reversing_the_path(self):
        near = template.CutPath(((21, 20), (49, 25)), closed=False)
        settings = graphtec.CutSettings(path_order="nearest")
        paths = toolpath.schedule([SQUARE, LINE, near], settings, start=(21, 21))
        self.assertEqual([index for index, _ in paths], [2, 1, 0])
        self.assertEqual(paths[0][1], near.points)
        self.assertEqual(paths[1][1], LINE.points)

    def test_schedule_is_validated_and_geometric_changes_are_detected(self):
        checked = job.build(offset_mm=(0.15, -0.2), passes=2,
                            blade_options=dict(overcut_mm=0.5, alternate_direction=True,
                                               pass_order="path", path_order="nearest"))
        self.assertFalse(checked.blocked)
        self.assertEqual(len(verify.decode(checked.stream).paths), len(checked.paths))
        self.assertTrue(any(row.label == "scheduled path geometry" for row in checked.rows))
        corrupted = verify._corrupt_imported_vertex(checked.stream)
        ok, rows = verify.expectations_met(
            corrupted, checked.grid, checked.marks, checked.settings, checked.device,
            mode=checked.mode, offset_mm=checked.offset_mm, source_paths=checked.paths)
        self.assertFalse(ok)
        self.assertIn("scheduled path geometry", {row.label for row in rows if not row.ok})

    def test_invalid_overcut_is_refused(self):
        for invalid in (-1, float("nan"), float("inf")):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "Overcut"):
                job.build(blade_options={"overcut_mm": invalid})
