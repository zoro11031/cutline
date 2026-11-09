"""Coordinate transforms: units, axis order, and the registration origin.

This is the chain where a mistake is invisible until card is already cut:

    sheet mm  ->  minus the mark origin  ->  Silhouette units  ->  (y, x)

Each hop is tested on its own, and then the whole chain is tested by decoding
a real built stream and measuring the cards back out of it.
"""

import unittest

from cut_proxies import geometry, graphtec, job as job_mod, regmarks, template
from cut_proxies.graphtec import CutSettings


class TestUnits(unittest.TestCase):

    def test_one_silhouette_unit_is_a_twentieth_of_a_millimetre(self):
        self.assertEqual(graphtec.su(1.0), 20)
        self.assertEqual(graphtec.su(0.05), 1)
        self.assertEqual(graphtec.su(63.0), 1260)
        self.assertEqual(graphtec.su(88.0), 1760)

    def test_the_reference_values_hold(self):
        # Graphtec.py's own worked examples: 8.5 in = 4318 SU, 11 in = 5588 SU.
        self.assertEqual(graphtec.su(8.5 * 25.4), 4318)
        self.assertEqual(graphtec.su(11.0 * 25.4), 5588)

    def test_round_trip(self):
        for mm in (0.0, 0.05, 1.0, 63.0, 215.9, 279.4):
            self.assertAlmostEqual(graphtec.su_to_mm(graphtec.su(mm)), mm,
                                   places=6)


class TestAxisOrder(unittest.TestCase):

    def test_move_and_draw_send_y_first(self):
        # The single most common way to get a cut that is transposed.
        self.assertEqual(graphtec.move_mm(10.0, 20.0), "M400,200")
        self.assertEqual(graphtec.draw_mm(10.0, 20.0), "D400,200")

    def test_clip_commands_also_send_y_first(self):
        self.assertEqual(graphtec.upper_left_mm(1.0, 2.0), "\\40,20")
        self.assertEqual(graphtec.lower_right_mm(1.0, 2.0), "Z40,20")


class TestFraming(unittest.TestCase):

    def test_every_command_is_etx_terminated(self):
        self.assertEqual(graphtec.frame("TB99"), b"TB99\x03")
        self.assertEqual(graphtec.frame(["A", "B"]), b"A\x03B\x03")

    def test_the_regmark_preamble_is_the_recovered_sequence(self):
        self.assertEqual(
            graphtec.REGMARK_PREAMBLE,
            ("TB50,0", "TB99", "TB52,2", "TB51,400", "TB53,10", "TB55,1"))

    def test_scan_commands_carry_millimetres_as_units(self):
        cmd = graphtec.auto_regmark_search_mm(195.9, 259.4, 0.0, 0.0)
        self.assertEqual(cmd, "TB123,3918,5188,0,0")
        quad = graphtec.auto_quad_regmark_search_mm(195.9, 259.4, 0.0, 0.0)
        self.assertTrue(quad.startswith("TB124,"))


class TestOriginShift(unittest.TestCase):
    """After a scan, (0, 0) is the top-left mark, not the paper corner."""

    def _job(self, registration):
        dev = graphtec.device_by_name("Silhouette Cameo 4")
        return graphtec.CutJob(
            dev, CutSettings(force=20, speed=5, passes=1),
            origin_mm=(10.0, 10.0), reg_width_mm=259.4, reg_length_mm=195.9,
            registration=registration)

    def test_paths_are_shifted_by_the_mark_origin(self):
        path = template.CutPath(((10.0, 10.0), (20.0, 10.0)), closed=False)
        cmds = self._job(True)._path_commands([path])
        # (10, 10) in sheet mm is (0, 0) relative to the mark.
        self.assertEqual(cmds[0], "M0,0")
        self.assertEqual(cmds[1], "D0,200")

    def test_without_registration_the_sheet_origin_is_used(self):
        path = template.CutPath(((10.0, 10.0), (20.0, 10.0)), closed=False)
        cmds = self._job(False)._path_commands([path])
        self.assertEqual(cmds[0], "M200,200")

    def test_a_closed_path_returns_to_its_first_point(self):
        pts = ((10.0, 10.0), (20.0, 10.0), (20.0, 20.0), (10.0, 20.0))
        cmds = self._job(True)._path_commands(
            [template.CutPath(pts, closed=True)])
        self.assertEqual(cmds[0], "M0,0")
        self.assertEqual(cmds[-1], "D0,0")

    def test_passes_repeat_the_whole_path_list(self):
        dev = graphtec.device_by_name("Silhouette Cameo 4")
        job = graphtec.CutJob(dev, CutSettings(passes=3),
                              origin_mm=(10.0, 10.0),
                              reg_width_mm=259.4, reg_length_mm=195.9)
        pts = ((10.0, 10.0), (20.0, 10.0))
        cmds = job._path_commands([template.CutPath(pts, closed=False)])
        self.assertEqual(cmds.count("M0,0"), 3)


class TestArcFlattening(unittest.TestCase):

    def test_chords_stay_inside_the_tolerance(self):
        import math
        r = 3.0
        rect = geometry.Rect(0, 0, 63, 88)
        pts = template.rounded_rect_points(rect, r)
        # Every point on a corner arc must sit within tolerance of radius r
        # from that corner's centre.
        centre = (rect.x2 - r, rect.y + r)
        on_arc = [p for p in pts
                  if p[0] > centre[0] - 1e-9 and p[1] < centre[1] + 1e-9]
        for p in on_arc:
            d = math.dist(p, centre)
            self.assertLessEqual(abs(d - r), 1e-9,
                                 f"{p} is not on the corner arc")
        # And consecutive chords deviate by less than the tolerance.
        for a, b in zip(on_arc, on_arc[1:]):
            mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
            sag = r - math.dist(mid, centre)
            self.assertLessEqual(sag, template.CHORD_TOLERANCE_MM + 1e-9)

    def test_flattening_is_finer_than_the_machine_can_step(self):
        # One Silhouette unit is 0.05 mm; flattening must not be the limiting
        # error, so the tolerance is held below half a step.
        self.assertLessEqual(template.CHORD_TOLERANCE_MM,
                             graphtec.su_to_mm(1) / 2)

    def test_a_zero_radius_is_four_corners(self):
        pts = template.rounded_rect_points(geometry.Rect(0, 0, 10, 10), 0.0)
        self.assertEqual(len(pts), 4)

    def test_the_radius_is_capped_at_half_the_short_side(self):
        pts = template.rounded_rect_points(geometry.Rect(0, 0, 10, 10), 99.0)
        xs = [p[0] for p in pts]
        self.assertAlmostEqual(min(xs), 0.0, places=6)
        self.assertAlmostEqual(max(xs), 10.0, places=6)

    def test_the_path_closes_without_a_duplicate_point(self):
        pts = template.rounded_rect_points(geometry.Rect(0, 0, 63, 88), 3.0)
        self.assertNotEqual(pts[0], pts[-1])


class TestWholeChain(unittest.TestCase):
    """Build a stream, decode it, and measure the cards back out."""

    def setUp(self):
        self.job = job_mod.build(
            sheet="Letter", card="standard", portrait=False, engine="scm",
            preset_name="Plain paper test", cutter="Silhouette Cameo 4",
            mode="rounded", mark_inset_mm=10.0)

    def test_the_job_checks_out(self):
        self.assertEqual(self.job.blocked, [],
                         f"blocked: {self.job.blocked}")

    def test_every_decoded_card_measures_its_nominal_size(self):
        from cut_proxies import verify
        d = verify.decode(self.job.stream)
        self.assertEqual(len(d.paths), self.job.grid.count)
        for i, box in enumerate(d.path_bboxes()):
            with self.subTest(card=i):
                self.assertAlmostEqual(box.w, 63.0, delta=0.1)
                self.assertAlmostEqual(box.h, 88.0, delta=0.1)

    def test_decoded_positions_match_the_grid_once_the_origin_is_added_back(self):
        from cut_proxies import verify
        d = verify.decode(self.job.stream)
        ox, oy = self.job.marks.origin_mm
        for i, (rect, box) in enumerate(zip(self.job.grid.cards,
                                            d.path_bboxes())):
            with self.subTest(card=i):
                self.assertAlmostEqual(box.x + ox, rect.x, delta=0.1)
                self.assertAlmostEqual(box.y + oy, rect.y, delta=0.1)

    def test_nothing_is_cut_outside_the_registered_area(self):
        from cut_proxies import verify
        d = verify.decode(self.job.stream)
        mk = self.job.marks
        for p in d.paths:
            for x, y in p:
                self.assertGreaterEqual(x, -0.05)
                self.assertGreaterEqual(y, -0.05)
                self.assertLessEqual(x, mk.reg_width_mm + 0.05)
                self.assertLessEqual(y, mk.reg_length_mm + 0.05)

    def test_all_paths_are_closed_loops(self):
        from cut_proxies import verify
        d = verify.decode(self.job.stream)
        self.assertEqual(len(d.closed_paths), self.job.grid.count)


class TestCrossCuts(unittest.TestCase):

    def test_cross_mode_emits_one_line_per_card_edge(self):
        grid = geometry.scm_layout("Letter", "standard", False, 10.0)
        paths = template.cut_paths(grid, template.CROSS)
        # Cards are spaced, so no edges collapse: two x and two y per card
        # row/column.
        self.assertEqual(len(paths), 2 * grid.cols + 2 * grid.rows)
        for p in paths:
            self.assertFalse(p.closed)
            self.assertEqual(len(p.points), 2)

    def test_cross_lines_span_the_whole_grid(self):
        grid = geometry.scm_layout("Letter", "standard", False, 10.0)
        paths = template.cut_paths(grid, template.CROSS)
        b = grid.bounds
        verticals = [p for p in paths if p.points[0][0] == p.points[1][0]]
        for p in verticals:
            self.assertAlmostEqual(p.points[0][1], b.y, places=6)
            self.assertAlmostEqual(p.points[1][1], b.y2, places=6)


class TestSettingsClamping(unittest.TestCase):

    def test_force_is_clamped_to_the_machine_maximum(self):
        dev = graphtec.device_by_name("Silhouette Cameo 4")
        s = CutSettings(force=99, speed=99).clamped(dev)
        self.assertEqual(s.force, dev.max_pressure)
        self.assertEqual(s.speed, dev.max_speed)

    def test_the_alpha_allows_more_force(self):
        alpha = graphtec.device_by_name("Silhouette Cameo 5 Alpha")
        self.assertEqual(alpha.max_pressure, 40)
        self.assertTrue(alpha.quad_regmarks)

    def test_clamping_is_warned_about_not_silent(self):
        dev = graphtec.device_by_name("Silhouette Cameo 4")
        warnings = CutSettings(force=99).warnings(dev)
        self.assertTrue(any("clamped" in w for w in warnings))

    def test_an_older_cameo_tops_out_at_speed_ten(self):
        dev = graphtec.device_by_name("Silhouette Cameo 2")
        self.assertEqual(CutSettings(speed=30).clamped(dev).speed, 10)


class TestDeviceTable(unittest.TestCase):

    def test_lookup_by_usb_id(self):
        dev = graphtec.device_for(0x0B4D, 0x1137)
        self.assertEqual(dev.name, "Silhouette Cameo 4")

    def test_the_alpha_is_a_different_vendor(self):
        dev = graphtec.device_for(0x3844, 0x0001)
        self.assertEqual(dev.name, "Silhouette Cameo 5 Alpha")

    def test_a_letter_sheet_fits_every_cameo(self):
        for name in ("Silhouette Cameo 3", "Silhouette Cameo 4",
                     "Silhouette Cameo 5"):
            dev = graphtec.device_by_name(name)
            self.assertGreaterEqual(dev.width_mm, 279.4)

    def test_a_letter_sheet_does_not_fit_a_portrait(self):
        dev = graphtec.device_by_name("Silhouette Portrait 2")
        self.assertLess(dev.width_mm, 279.4)


if __name__ == "__main__":
    unittest.main()
