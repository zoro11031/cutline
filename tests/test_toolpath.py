import math
import tempfile
import unittest

from cutline import graphtec, importer, job, template, toolpath, verify

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


class TestStudioSorting(unittest.TestCase):
    def test_interior_sort_understands_open_lines_and_concave_boundaries(self):
        outer = template.CutPath(((10, 10), (70, 10), (70, 70), (50, 70),
                                  (50, 30), (30, 30), (30, 70), (10, 70)), True)
        crossing = template.CutPath(((20, 55), (60, 55)), False)
        inside = template.CutPath(((14, 21), (25, 21)), False)
        settings = graphtec.CutSettings(interior_first=True)
        self.assertEqual([i for i, _ in toolpath.schedule([outer, crossing, inside], settings)],
                         [2, 0, 1])
        self.assertEqual([i for i, _ in toolpath.schedule([outer, crossing, inside],
                                                        graphtec.CutSettings())], [0, 1, 2])

    def test_roller_sort_orders_entries_along_feed_axis_then_across(self):
        paths = [template.CutPath(((x, y), (x + 5, y)), False)
                 for x, y in ((10, 40), (20, 20), (10, 20), (10, 60))]
        settings = graphtec.CutSettings(path_order="rollers")
        self.assertEqual([i for i, _ in toolpath.schedule(paths, settings, start=(10, 40))],
                         [2, 1, 0, 3])

    def test_delicate_media_alternates_winding_and_keeps_contour_starts(self):
        paths = [template.CutPath(tuple((x + offset, y) for x, y in SQUARE.points), True)
                 for offset in (0, 20, 40)]
        scheduled = toolpath.schedule(paths, graphtec.CutSettings(delicate_media=True))
        self.assertEqual([points[0] for _, points in scheduled], [p.points[0] for p in paths])
        self.assertEqual([points[1] for _, points in scheduled],
                         [paths[0].points[1], paths[1].points[-1], paths[2].points[1]])


class TestWeedSettings(unittest.TestCase):
    def artwork(self):
        moved = template.CutPath(tuple((x + 40, y + 50) for x, y in SQUARE.points), True)
        return importer.Imported((SQUARE, moved), 279.4, 215.9, "test", "mm",
                                 page_size_mm=(279.4, 215.9))

    def test_border_and_dividers_go_after_the_art_without_entering_its_bounds(self):
        checked = job.build(imported=self.artwork(), registration=False, offset_mm=(0, 0),
                            weed_border_mm=3, weed_lines="grid",
                            blade_options={"path_order": "rollers"})
        self.assertEqual(checked.blocked, [])
        self.assertEqual([p.label for p in checked.paths[-3:]], ["Weed line", "Weed line", "Weed border"])
        actual = verify.decode(checked.stream).paths
        self.assertEqual(actual[2], [(17, 50), (73, 50)])
        self.assertEqual(actual[3], [(45, 17), (45, 83)])
        self.assertEqual(actual[4], [(17, 17), (73, 17), (73, 83), (17, 83), (17, 17)])

    def test_weed_geometry_survives_svg_and_dxf_exports(self):
        checked = job.build(imported=self.artwork(), registration=False, offset_mm=(0, 0),
                            weed_border_mm=3, weed_lines="grid")
        with tempfile.TemporaryDirectory() as folder:
            exports = job.write_templates(checked, "weed", folder, proof=False)
            for kind in ("svg", "dxf"):
                with self.subTest(kind=kind):
                    restored = importer.load(exports[kind])
                    self.assertEqual(len(restored.paths), len(checked.paths))
                    self.assertEqual(restored.bounds, (17, 17, 73, 83))

    def test_out_of_bounds_weed_border_is_refused(self):
        checked = job.build(imported=self.artwork(), registration=False, offset_mm=(0, 0),
                            weed_border_mm=25)
        self.assertTrue(any("cuts inside the sheet" in error for error in checked.blocked))

    def test_weed_lines_require_a_border(self):
        with self.assertRaisesRegex(ValueError, "positive Weed Border padding"):
            job.build(imported=self.artwork(), weed_lines="horizontal")
