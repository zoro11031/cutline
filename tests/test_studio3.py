"""Reading and rewriting Silhouette Studio `.studio3` cutting templates.

What these tests can and cannot establish: they prove the geometry read out of
Card Maker's own templates is the geometry those templates were built with,
and that a rewrite moves exactly the cards and nothing else. They prove
nothing about what Silhouette Studio does with the result, because there is no
Studio on this machine. Section 29 of docs/FINDINGS.md keeps the two apart.
"""

import pathlib
import struct
import unittest

from cut_proxies import studio3

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "studio3"

#: What each vendored base is, independently of anything the parser says.
#: Card sizes are Card Maker's published ones for those card types.
KNOWN = {
    "letter-standard-v6": dict(cards=8, w=63.0, h=88.0, page=(279.4, 215.9)),
    "letter-catan-v2": dict(cards=9, w=54.0, h=80.0, page=(215.9, 279.4)),
    "a4-standard-v5": dict(cards=8, w=63.0, h=88.0, page=(296.9, 210.1)),
    "a4-bridge-v4": dict(cards=9, w=57.15, h=88.9, page=(210.1, 296.9)),
}


def load(name):
    return (FIXTURES / f"{name}.studio3").read_bytes()


class TestReading(unittest.TestCase):
    def test_every_base_parses_to_its_known_geometry(self):
        for name, want in KNOWN.items():
            with self.subTest(name):
                data = load(name)
                cards = studio3.read_cards(data)
                self.assertEqual(len(cards), want["cards"])
                for c in cards:
                    self.assertAlmostEqual(c.w, want["w"], places=2)
                    self.assertAlmostEqual(c.h, want["h"], places=2)
                pw, ph = studio3.page_size(data)
                self.assertAlmostEqual(pw, want["page"][0], places=1)
                self.assertAlmostEqual(ph, want["page"][1], places=1)

    def test_cards_sit_on_a_grid_inside_the_page(self):
        for name, want in KNOWN.items():
            with self.subTest(name):
                data = load(name)
                pw, ph = studio3.page_size(data)
                for c in studio3.read_cards(data):
                    self.assertGreaterEqual(c.x, 0)
                    self.assertGreaterEqual(c.y, 0)
                    self.assertLessEqual(c.x + c.w, pw + 0.01)
                    self.assertLessEqual(c.y + c.h, ph + 0.01)

    def test_corners_are_rounded(self):
        for name in KNOWN:
            with self.subTest(name):
                for c in studio3.read_cards(load(name)):
                    self.assertGreater(c.radius, 0)
                    self.assertLessEqual(c.radius, min(c.w, c.h) / 2)

    def test_refuses_something_that_is_not_a_studio3(self):
        with self.assertRaises(studio3.Studio3Error):
            studio3.read_cards(b"not a studio file, not even close" * 40)

    def test_refuses_a_truncated_file(self):
        data = load("letter-standard-v6")[:2000]
        with self.assertRaises(studio3.Studio3Error):
            studio3.read_cards(data)


class TestRewriting(unittest.TestCase):
    def test_rewriting_unchanged_cards_keeps_the_geometry(self):
        for name in KNOWN:
            with self.subTest(name):
                data = load(name)
                cards = studio3.read_cards(data)
                out = studio3.write_cards(data, cards)
                self.assertEqual(len(out), len(data))
                for before, after in zip(cards, studio3.read_cards(out)):
                    self.assertAlmostEqual(before.x, after.x, places=2)
                    self.assertAlmostEqual(before.y, after.y, places=2)
                    self.assertAlmostEqual(before.w, after.w, places=2)
                    self.assertAlmostEqual(before.h, after.h, places=2)

    def test_moving_a_card_moves_it_and_leaves_the_others(self):
        # Shifted along x only: read_cards returns cards in grid order, so
        # moving one across rows would reorder the list and the comparison
        # below would be against a different card.
        data = load("letter-standard-v6")
        cards = studio3.read_cards(data)
        moved = list(cards)
        moved[0] = studio3.Card(cards[0].x + 5.0, cards[0].y,
                                cards[0].w, cards[0].h, cards[0].radius)
        back = studio3.read_cards(studio3.write_cards(data, moved))
        self.assertAlmostEqual(back[0].x, cards[0].x + 5.0, places=2)
        self.assertAlmostEqual(back[0].y, cards[0].y, places=2)
        for i in range(1, len(cards)):
            self.assertAlmostEqual(back[i].x, cards[i].x, places=2)
            self.assertAlmostEqual(back[i].y, cards[i].y, places=2)

    def test_resizing_cards_is_read_back(self):
        data = load("letter-catan-v2")
        cards = studio3.read_cards(data)
        new = [studio3.Card(c.x, c.y, 63.0, 88.0, 3.2) for c in cards]
        back = studio3.read_cards(studio3.write_cards(data, new))
        for c in back:
            self.assertAlmostEqual(c.w, 63.0, places=2)
            self.assertAlmostEqual(c.h, 88.0, places=2)
            self.assertAlmostEqual(c.radius, 3.2, places=2)

    def test_page_size_is_written(self):
        data = load("a4-standard-v5")
        out = studio3.write_cards(data, studio3.read_cards(data), page=(297.0, 210.0))
        pw, ph = studio3.page_size(out)
        self.assertAlmostEqual(pw, 297.0, places=2)
        self.assertAlmostEqual(ph, 210.0, places=2)

    def test_only_coordinate_bytes_change(self):
        """A rewrite must not disturb a single byte outside the outlines.

        This is what makes the in-place approach safe: the container's
        undecoded counters and offsets stay correct because nothing they
        describe has moved.
        """
        data = load("letter-standard-v6")
        cards = studio3.read_cards(data)
        moved = [studio3.Card(c.x + 1.0, c.y, c.w, c.h, c.radius) for c in cards]
        out = studio3.write_cards(data, moved)
        allowed = set()
        for _, outs in studio3.find_cards(data):
            for o in outs:
                for off in o.offsets:
                    allowed.update(range(off, off + 8))
        changed = {i for i in range(len(data)) if data[i] != out[i]}
        self.assertTrue(changed, "the rewrite changed nothing at all")
        self.assertEqual(changed - allowed, set())

    def test_refuses_a_different_number_of_cards(self):
        data = load("letter-standard-v6")
        cards = studio3.read_cards(data)
        with self.assertRaises(studio3.Studio3Error) as caught:
            studio3.write_cards(data, cards[:-1])
        self.assertIn("card count cannot be changed", str(caught.exception))
        with self.assertRaises(studio3.Studio3Error):
            studio3.write_cards(data, cards + cards[:1])


class TestCopies(unittest.TestCase):
    def test_every_copy_of_a_card_is_rewritten(self):
        """Templates that store each outline twice must have both moved.

        Leaving the second copy behind would be invisible here and wrong in
        Studio, so it is checked by counting the outlines the scan finds at
        the new position rather than by trusting the writer.
        """
        for name in KNOWN:
            with self.subTest(name):
                data = load(name)
                copies = studio3.copies_per_card(data)
                self.assertIn(copies, (1, 2))
                cards = studio3.read_cards(data)
                moved = [studio3.Card(c.x, c.y + 3.0, c.w, c.h, c.radius) for c in cards]
                out = studio3.write_cards(data, moved)
                groups = studio3.find_cards(out)
                self.assertEqual(len(groups), len(cards))
                for g in groups:
                    self.assertEqual(len(g[1]), copies)
                for before, (after, _) in zip(cards, groups):
                    self.assertAlmostEqual(after.y, before.y + 3.0, places=2)


class TestScanIsNotFooled(unittest.TestCase):
    def test_a_run_of_floats_that_is_not_a_card_is_rejected(self):
        pts = [(10.0, 10.0)] * 12
        self.assertIsNone(studio3._fit_card(pts))

    def test_a_plain_rectangle_without_rounded_corners_is_rejected(self):
        # Four corners repeated three times each: right count, wrong shape.
        pts = []
        for x, y in ((10, 10), (70, 10), (70, 98), (10, 98)):
            pts += [(float(x), float(y))] * 3
        self.assertIsNone(studio3._fit_card(pts))

    def test_a_real_outline_is_accepted_both_ways_round(self):
        card = studio3.Card(10.0, 20.0, 63.0, 88.0, 3.0)
        straight = studio3._fit_card(card.points)
        self.assertIsNotNone(straight)
        self.assertFalse(straight[1])
        flipped = studio3._fit_card(studio3._reflect(card.points, card))
        self.assertIsNotNone(flipped)
        self.assertTrue(flipped[1])


if __name__ == "__main__":
    unittest.main()


class TestArmLengthMatchesWhatProxyPressPrints(unittest.TestCase):
    def test_stock_landscape_jobs_use_active_arm_from_native_template(self):
        from cut_proxies import job
        for paper, base in (("Letter", "letter-standard-v6"), ("A4", "a4-standard-v5")):
            with self.subTest(paper=paper):
                arm = struct.unpack_from("<f", load(base), 1137)[0]
                built = job.build(sheet=paper, engine="proxypress")
                self.assertAlmostEqual(built.marks.length_mm, arm, places=3)
                self.assertFalse(built.blocked)

    def test_card_maker_templates_place_a_card_inside_the_corner_zone(self):
        """A manually selected 20 mm arm crowds the stock Letter layout."""
        cards = studio3.read_cards(load("letter-standard-v6"))
        pw, ph = studio3.page_size(load("letter-standard-v6"))
        arm = 20.0
        inset = 10.0
        # Bottom-left L: its corner zone reaches up from the bottom inset.
        zone_x0, zone_x1 = inset, inset + arm
        zone_y0, zone_y1 = ph - inset - arm, ph - inset
        intruding = [c for c in cards
                     if c.x < zone_x1 and c.x + c.w > zone_x0
                     and c.y < zone_y1 and c.y + c.h > zone_y0]
        self.assertTrue(
            intruding,
            "expected Card Maker's own layout to enter the 20 mm corner zone")

    def test_the_arm_only_reaches_cards_near_its_own_edge(self):
        """Measure physical arm strokes independently of the exclusion square."""
        data = load("letter-standard-v6")
        cards = studio3.read_cards(data)
        pw, ph = studio3.page_size(data)
        inset, arm, half = 10.0, 20.0, 0.5
        arms = [
            # (x0, y0, x1, y1) centre-lines of the four L arms
            (pw - inset - arm, inset, pw - inset, inset),
            (pw - inset, inset, pw - inset, inset + arm),
            (inset, ph - inset, inset + arm, ph - inset),
            (inset, ph - inset - arm, inset, ph - inset),
        ]
        for i, (x0, y0, x1, y1) in enumerate(arms):
            rx0, rx1 = min(x0, x1) - half, max(x0, x1) + half
            ry0, ry1 = min(y0, y1) - half, max(y0, y1) + half
            for j, c in enumerate(cards):
                overlap = (c.x < rx1 and c.x + c.w > rx0
                           and c.y < ry1 and c.y + c.h > ry0)
                self.assertFalse(overlap, f"arm {i} is printed over card {j}")
