"""Registration mark geometry, the scan window, and clearance.

The scan window numbers are what the cutter is told; the mark positions are
what gets printed.  These tests pin the relationship between the two, because
an error there is silent: the sensor finds the marks, the cut happens, and
everything is in the wrong place by a constant offset.
"""

import unittest

from cut_proxies import geometry, regmarks


class TestMarkPlacement(unittest.TestCase):

    def setUp(self):
        self.mk = regmarks.marks(279.4, 215.9, inset_mm=10.0, length_mm=8.0,
                                 thickness_mm=1.0, pattern=regmarks.THREE)

    def test_three_pattern_has_a_square_and_two_ells(self):
        self.assertIsNotNone(self.mk.square)
        self.assertEqual(len(self.mk.ells), 2)

    def test_the_square_corner_sits_on_the_inset(self):
        # SCM's prose says the square is centred on the inset; its code puts
        # the square's corner there, and the code is what the published
        # templates were cut against.
        s = self.mk.square
        self.assertAlmostEqual(s.x, 10.0)
        self.assertAlmostEqual(s.y, 10.0)
        self.assertAlmostEqual(s.w, 5.0)
        self.assertAlmostEqual(s.h, 5.0)

    def test_there_is_no_bottom_right_mark_in_the_three_pattern(self):
        corners = {(e.corner_x, e.corner_y) for e in self.mk.ells}
        self.assertNotIn((279.4 - 10.0, 215.9 - 10.0), corners)
        self.assertIn((279.4 - 10.0, 10.0), corners)    # top-right
        self.assertIn((10.0, 215.9 - 10.0), corners)    # bottom-left

    def test_four_pattern_is_four_ells_and_no_square(self):
        mk = regmarks.marks(279.4, 215.9, pattern=regmarks.FOUR)
        self.assertIsNone(mk.square)
        self.assertEqual(len(mk.ells), 4)

    def test_proxypress_pattern_is_a_square_plus_three_ells(self):
        # What Proxy Press actually prints: the canonical square, but an extra
        # fourth mark at the bottom-right. See docs/FINDINGS.md section 3.
        mk = regmarks.marks(279.4, 215.9, pattern=regmarks.PROXYPRESS)
        self.assertIsNotNone(mk.square)
        self.assertEqual(len(mk.ells), 3)

    def test_arms_run_inward_and_are_shortened_by_half_the_line_width(self):
        tr = next(e for e in self.mk.ells if e.corner_x > 100)
        horizontal, vertical = tr.arms()
        # The horizontal arm runs left (inward) from the top-right corner.
        self.assertLess(horizontal[2], horizontal[0])
        # Reach is length minus half the thickness, so the two arms meet flush.
        self.assertAlmostEqual(abs(horizontal[2] - horizontal[0]),
                               8.0 - 0.5, places=6)
        self.assertGreater(vertical[3], vertical[1])   # and downward


class TestScanWindow(unittest.TestCase):

    def test_origin_is_the_top_left_mark(self):
        mk = regmarks.marks(279.4, 215.9, inset_mm=10.0)
        self.assertEqual(mk.origin_mm, (10.0, 10.0))

    def test_window_is_mark_to_mark_not_sheet_size(self):
        mk = regmarks.marks(279.4, 215.9, inset_mm=10.0)
        self.assertAlmostEqual(mk.reg_width_mm, 279.4 - 20.0)
        self.assertAlmostEqual(mk.reg_length_mm, 215.9 - 20.0)

    def test_borderless_inset_widens_the_window(self):
        a = regmarks.marks(215.9, 279.4, inset_mm=10.0)
        b = regmarks.marks(215.9, 279.4, inset_mm=3.5)
        self.assertGreater(b.reg_width_mm, a.reg_width_mm)
        self.assertGreater(b.reg_length_mm, a.reg_length_mm)

    def test_search_box_is_backed_off_and_clamped_at_zero(self):
        mk = regmarks.marks(279.4, 215.9, inset_mm=10.0)
        w = regmarks.scan_window(mk, search_mm=10.0)
        self.assertEqual(w["search_left_mm"], 0.0)
        self.assertEqual(w["search_top_mm"], 0.0)
        mk2 = regmarks.marks(279.4, 215.9, inset_mm=25.0)
        w2 = regmarks.scan_window(mk2, search_mm=10.0)
        self.assertEqual(w2["search_left_mm"], 15.0)

    def test_quad_is_only_set_for_the_four_pattern(self):
        self.assertFalse(regmarks.scan_window(
            regmarks.marks(279.4, 215.9, pattern=regmarks.THREE))["quad"])
        self.assertTrue(regmarks.scan_window(
            regmarks.marks(279.4, 215.9, pattern=regmarks.FOUR))["quad"])


class TestClamps(unittest.TestCase):

    def test_length_and_thickness_are_clamped_to_machine_limits(self):
        inset, length, thickness = regmarks.clamp(10.0, 100.0, 5.0)
        self.assertEqual(length, regmarks.MAX_LENGTH_MM)
        self.assertEqual(thickness, regmarks.MAX_THICKNESS_MM)
        _, length2, thickness2 = regmarks.clamp(10.0, 1.0, 0.1)
        self.assertEqual(length2, regmarks.MIN_LENGTH_MM)
        self.assertEqual(thickness2, regmarks.MIN_THICKNESS_MM)

    def test_a_small_inset_is_allowed_through_not_silently_raised(self):
        # Raising a 3.5 mm borderless inset to SCM's declared 10 mm minimum
        # would move every mark and invalidate the template.
        inset, _, _ = regmarks.clamp(3.5, 5.0, 1.0)
        self.assertEqual(inset, 3.5)


class TestClearance(unittest.TestCase):

    def test_a_clean_layout_has_no_collisions(self):
        grid = geometry.scm_layout("Letter", "standard", False, 10.0)
        mk = regmarks.for_grid(grid, inset_mm=10.0)
        self.assertEqual(regmarks.collisions(grid, mk), [])

    def test_the_default_arm_clears_with_a_safety_margin(self):
        """Found by searching, not estimated.

        `Grid.max_mark_length_mm` is a per-engine estimate and only the
        Card Maker engine computes it the way the corner rule works; the
        Proxy Press engine's version put 11.2 mm arms on top of four cards
        on an eight-up sheet. The arm is now the longest that actually
        clears with one card gap to spare.
        """
        grid = geometry.scm_layout("Letter", "standard", False, 10.0)
        mk = regmarks.for_grid(grid, inset_mm=10.0)
        self.assertEqual(regmarks.collisions(grid, mk), [])
        # It keeps real clearance, not a hair's breadth.
        hints = regmarks.mask_hints(grid, mk)
        self.assertGreater(hints[0]["gap_mm"], 1.0)

    def test_the_default_arm_is_as_long_as_it_can_safely_be(self):
        grid = geometry.scm_layout("Letter", "standard", False, 10.0)
        mk = regmarks.for_grid(grid, inset_mm=10.0)
        longer = regmarks.marks(grid.sheet_w_mm, grid.sheet_h_mm, 10.0,
                                mk.length_mm + 1.0, mk.thickness_mm,
                                mk.pattern)
        self.assertTrue(
            regmarks.collisions(grid, longer,
                                bleed_mm=grid.card_distance_mm),
            "a longer arm should have eaten the safety margin")

    def test_it_lands_near_the_value_card_maker_publishes(self):
        """Independent agreement, to within the search step.

        SCM's table gives 8.04 mm for Letter eight-up and 9.40 mm for A4.
        This searches rather than reproducing their formula, so landing
        inside a quarter-millimetre of both is a real cross-check of the
        rule rather than of the arithmetic.
        """
        for sheet, published in (("Letter", 8.04), ("A4", 9.40)):
            with self.subTest(sheet=sheet):
                grid = geometry.scm_layout(sheet, "standard", False, 10.0)
                mk = regmarks.for_grid(grid, inset_mm=10.0)
                self.assertAlmostEqual(mk.length_mm, published, delta=0.3)

    def test_both_engines_choose_the_same_arm_for_the_same_sheet(self):
        """The Proxy Press and Card Maker eight-up grids are the same
        grid to within rounding, so they must get the same marks."""
        from cut_proxies import config
        for sheet in ("Letter", "A4"):
            with self.subTest(sheet=sheet):
                a = regmarks.for_grid(
                    geometry.scm_layout(sheet, "standard", False, 10.0),
                    inset_mm=10.0)
                b = regmarks.for_grid(
                    geometry.proxypress_layout(sheet, False,
                                               config.PROXYPRESS_8UP),
                    inset_mm=10.0, pattern=regmarks.PROXYPRESS)
                self.assertAlmostEqual(a.length_mm, b.length_mm, places=2)

    def test_an_over_long_arm_is_reported_as_a_collision(self):
        grid = geometry.scm_layout("Letter", "standard", False, 10.0)
        mk = regmarks.for_grid(grid, inset_mm=10.0,
                               length_mm=regmarks.MAX_LENGTH_MM)
        found = regmarks.collisions(grid, mk)
        self.assertTrue(found)
        self.assertIn("exclusion zone", found[0])

    def test_bleed_is_counted_against_the_exclusion_zone(self):
        # Art printed over a mark is as bad as a card printed over it.
        grid = geometry.scm_layout("Letter", "standard", False, 10.0)
        mk = regmarks.for_grid(grid, inset_mm=10.0)
        self.assertEqual(regmarks.collisions(grid, mk, bleed_mm=0.0), [])
        self.assertTrue(regmarks.collisions(grid, mk, bleed_mm=6.0))

    def test_a_sheet_that_fits_reports_nothing_off_it(self):
        # The positive half: without this, a function returning [] always
        # would pass the off-sheet test below.
        grid = geometry.scm_layout("Letter", "standard", False, 10.0)
        mk = regmarks.for_grid(grid, inset_mm=10.0)
        self.assertEqual(regmarks.off_sheet(grid, mk), [])

    def test_off_sheet_marks_are_caught(self):
        grid = geometry.scm_layout("Letter", "standard", False, 10.0)
        mk = regmarks.marks(grid.sheet_w_mm, grid.sheet_h_mm, inset_mm=-5.0,
                            length_mm=5.0)
        self.assertTrue(regmarks.off_sheet(grid, mk))

    def test_nine_up_borderless_still_clears_its_marks(self):
        grid = geometry.scm_layout("Letter", "standard", True, 3.5)
        mk = regmarks.for_grid(grid, inset_mm=3.5)
        self.assertEqual((grid.cols, grid.rows), (3, 3))
        self.assertEqual(regmarks.collisions(grid, mk), [])


if __name__ == "__main__":
    unittest.main()
