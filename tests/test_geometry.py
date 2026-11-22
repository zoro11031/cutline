"""Layout bounds and the published-table check.

The first test in this file is the one that matters: it asserts that our
reimplementation of the Silhouette Card Maker layout reproduces every number
in SCM's own `layouts.json` for the sheet and card combinations this suite
uses.  If that test fails, the templates this project generates will not line
up with anything, and no other test here is worth reading.
"""

import math
import unittest

from cutline import geometry
from cutline.geometry import LayoutImpossible, CenteredGridSettings


class TestPublishedLayouts(unittest.TestCase):
    """Against SCM's assets/layouts.json, read 2026-11-07.

    Each case is (sheet, card, portrait, inset, expected cols, rows, max arm).
    The inset of 10 mm is SCM's `default` registration preset and 3.5 mm is
    its `borderless` one; the expected arm length is the `registration.length`
    the published table carries for that layout.
    """

    CASES = (
        ("Letter", "standard", False, 10.0, 4, 2, 8.04),
        ("Letter", "standard", True, 3.5, 3, 3, 7.45),
        ("A4", "standard", False, 10.0, 4, 2, 9.40),
        ("A4", "standard", True, 3.5, 3, 3, 10.50),
        ("Letter", "poker", False, 10.0, 4, 2, 7.20),
        ("A4", "poker", False, 10.0, 4, 2, 8.38),
        ("Letter", "japanese", False, 10.0, 4, 2, 10.08),
        ("A4", "japanese", True, 10.0, 3, 3, 6.94),
    )

    def test_matches_scm_published_layouts(self):
        for sheet, card, portrait, inset, cols, rows, arm in self.CASES:
            with self.subTest(sheet=sheet, card=card, portrait=portrait,
                              inset=inset):
                g = geometry.scm_layout(sheet, card, portrait, inset)
                self.assertEqual((g.cols, g.rows), (cols, rows))
                self.assertAlmostEqual(g.max_mark_length_mm, arm, places=2)
                self.assertEqual(g.count, cols * rows)


class TestCardBounds(unittest.TestCase):

    def setUp(self):
        self.grid = geometry.scm_layout("Letter", "standard", False, 10.0)

    def test_every_card_is_exactly_the_nominal_size(self):
        # 63 x 88 mm quantised to 300 ppi integer pixels, which is the
        # quantisation SCM's algorithm applies on purpose.
        for i, c in enumerate(self.grid.cards):
            with self.subTest(card=i):
                self.assertAlmostEqual(c.w, 63.0, delta=0.05)
                self.assertAlmostEqual(c.h, 88.0, delta=0.05)

    def test_every_card_is_on_the_sheet(self):
        for i, c in enumerate(self.grid.cards):
            with self.subTest(card=i):
                self.assertGreaterEqual(c.x, 0.0)
                self.assertGreaterEqual(c.y, 0.0)
                self.assertLessEqual(c.x2, self.grid.sheet_w_mm)
                self.assertLessEqual(c.y2, self.grid.sheet_h_mm)

    def test_cards_do_not_overlap_each_other(self):
        cards = self.grid.cards
        for i in range(len(cards)):
            for k in range(i + 1, len(cards)):
                with self.subTest(a=i, b=k):
                    self.assertFalse(cards[i].overlaps(cards[k]))

    def test_the_grid_is_centred_on_the_usable_area(self):
        # Left and right slack must match, and top and bottom likewise, or a
        # double-sided sheet will not line up when it is turned over.
        # The slack may differ by up to one 300 ppi pixel (0.085 mm), which
        # is the quantisation SCM's integer-pixel algorithm introduces.
        one_px = geometry.px_to_mm(1) * 1.01   # allow the rounding itself
        b = self.grid.bounds
        self.assertAlmostEqual(b.x, self.grid.sheet_w_mm - b.x2, delta=one_px)
        self.assertAlmostEqual(b.y, self.grid.sheet_h_mm - b.y2, delta=one_px)

    def test_card_spacing_is_the_card_distance(self):
        row0 = self.grid.cards[:self.grid.cols]
        for a, b in zip(row0, row0[1:]):
            self.assertAlmostEqual(b.x - a.x2, geometry.SCM_CARD_DISTANCE_MM,
                                   delta=0.05)

    def test_reading_order(self):
        # Left to right, then top to bottom: the order a print layout fills
        # slots, so card n in the template is card n on the sheet.
        for r in range(self.grid.rows):
            for c in range(self.grid.cols - 1):
                i = r * self.grid.cols + c
                self.assertLess(self.grid.cards[i].x,
                                self.grid.cards[i + 1].x)
        for r in range(self.grid.rows - 1):
            i = r * self.grid.cols
            self.assertLess(self.grid.cards[i].y,
                            self.grid.cards[i + self.grid.cols].y)


class TestOrientationChoice(unittest.TestCase):

    def test_best_fit_prefers_more_cards(self):
        g = geometry.scm_layout("Letter", "standard", None, 3.5)
        self.assertEqual(g.count, 9)
        self.assertTrue(g.portrait)

    def test_an_explicit_orientation_is_never_flipped(self):
        g = geometry.scm_layout("Letter", "standard", False, 10.0)
        self.assertFalse(g.portrait)

    def test_nine_cards_need_the_borderless_inset(self):
        # The headline constraint: at the standard 10 mm inset there is no
        # 3x3 standard-card layout on Letter, in either orientation. The
        # marks take the room.
        for portrait in (True, False):
            g = geometry.scm_layout("Letter", "standard", portrait, 10.0)
            self.assertNotEqual((g.cols, g.rows), (3, 3))
        g = geometry.scm_layout("Letter", "standard", True, 3.5)
        self.assertEqual((g.cols, g.rows), (3, 3))

    def test_impossible_layouts_raise_rather_than_degrade(self):
        with self.assertRaises(LayoutImpossible):
            # A 70 x 120 mm tarot card on Letter with a 75 mm inset leaves
            # 65.9 mm of usable width in portrait and 65.9 mm of height in
            # landscape, so neither orientation fits even one card.
            geometry.scm_layout("Letter", "tarot", None, 75.0)


class TestCardNames(unittest.TestCase):

    def test_mtg_is_an_alias_for_standard(self):
        self.assertEqual(geometry.card("mtg"), geometry.card("standard"))
        self.assertEqual(geometry.card("MTG").width_mm, 63.0)

    def test_unknown_names_say_what_is_known(self):
        with self.assertRaises(KeyError) as ctx:
            geometry.card("hearthstone")
        self.assertIn("standard", str(ctx.exception))


class TestCenteredLayout(unittest.TestCase):
    """The grid centred on the whole sheet, against hand-worked layouts."""

    def test_letter_landscape_nine_up_rotated(self):
        # Its Silhouette preset: Letter landscape, no bleed, no gap. With
        # cards turned on their side, 3x3 fits, which is what that preset is
        # for.
        s = CenteredGridSettings(bleed_mm=0.0, gap_mm=0.0, margin_mm=4.0,
                               rotation="auto")
        g = geometry.centered_layout("Letter", portrait=False, settings=s)
        self.assertEqual((g.cols, g.rows), (3, 3))
        self.assertEqual(g.count, 9)
        # Rotated: the face presented is 88 wide by 63 tall.
        self.assertAlmostEqual(g.card.width_mm, 88.0)
        self.assertAlmostEqual(g.card.height_mm, 63.0)

    def test_grid_is_centred_on_the_whole_sheet(self):
        s = CenteredGridSettings(margin_mm=4.0)
        g = geometry.centered_layout("Letter", False, s)
        b = g.bounds
        self.assertAlmostEqual(b.x, g.sheet_w_mm - b.x2, places=6)
        self.assertAlmostEqual(b.y, g.sheet_h_mm - b.y2, places=6)

    def test_bleed_insets_the_face_within_its_slot(self):
        s = CenteredGridSettings(bleed_mm=1.0, gap_mm=0.0, margin_mm=4.0,
                               rotation="upright")
        g = geometry.centered_layout("Letter", False, s)
        # Adjacent faces are 2 * bleed apart when the gap is zero.
        row0 = g.cards[:g.cols]
        for a, b in zip(row0, row0[1:]):
            self.assertAlmostEqual(b.x - a.x2, 2.0, places=6)

    def test_explicit_cols_and_rows_are_honoured(self):
        s = CenteredGridSettings(cols=2, rows=2, rotation="upright")
        g = geometry.centered_layout("Letter", False, s)
        self.assertEqual((g.cols, g.rows), (2, 2))

    def test_the_current_cameo_setup_is_eight_up(self):
        from cutline import config
        g = geometry.centered_layout(
            "Letter", False, config.CARD_SHEET_8UP)
        self.assertEqual((g.cols, g.rows), (4, 2))
        self.assertAlmostEqual(g.card.radius_mm, 3.2)
        scm = geometry.scm_layout("Letter", "standard", False, 10.0)
        self.assertEqual(scm.count, 8)
        self.assertEqual(g.count, scm.count)

    def test_the_two_engines_disagree_and_say_so(self):
        a = geometry.scm_layout("Letter", "standard", False, 10.0)
        b = geometry.centered_layout(
            "Letter", False, CenteredGridSettings(margin_mm=4.0))
        self.assertEqual(a.engine, "scm")
        self.assertEqual(b.engine, "centered")
        # Same sheet, different grids: this is why a template records which
        # engine produced it.
        self.assertNotEqual((a.cols, a.rows), (b.cols, b.rows))


class TestUnits(unittest.TestCase):

    def test_mm_px_round_trip_at_300ppi(self):
        self.assertEqual(geometry.mm_to_px(25.4), 300)
        self.assertAlmostEqual(geometry.px_to_mm(300), 25.4)

    def test_rect_growth_and_overlap(self):
        a = geometry.Rect(0, 0, 10, 10)
        self.assertFalse(a.overlaps(geometry.Rect(10, 0, 5, 5)))
        self.assertTrue(a.overlaps(geometry.Rect(9.9, 0, 5, 5)))
        self.assertTrue(a.grown(1).overlaps(geometry.Rect(10.5, 0, 5, 5)))


if __name__ == "__main__":
    unittest.main()
