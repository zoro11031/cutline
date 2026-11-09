"""The written files, and the refusal path.

Coordinate locking is the whole point of these tests: an SVG whose header
disagrees with its viewBox, or a DXF with no units, will import at some
fraction of the right size and cut a sheet of slightly wrong cards.
"""

import re
import tempfile
import unittest
import pathlib

from cut_proxies import (geometry, graphtec, job as job_mod, regmarks,
                         template, verify)
from cut_proxies.graphtec import CutSettings


class TestSvg(unittest.TestCase):

    def setUp(self):
        self.grid = geometry.scm_layout("Letter", "standard", False, 10.0)
        self.paths = template.cut_paths(self.grid)
        self.svg = template.write_svg(None, self.grid, self.paths)

    def test_width_and_height_are_in_millimetres(self):
        self.assertIn('width="279.4mm"', self.svg)
        self.assertIn('height="215.9mm"', self.svg)

    def test_the_viewbox_is_the_same_numbers_so_one_unit_is_one_mm(self):
        m = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', self.svg)
        self.assertIsNotNone(m)
        self.assertAlmostEqual(float(m.group(1)), 279.4)
        self.assertAlmostEqual(float(m.group(2)), 215.9)

    def test_one_path_per_card(self):
        self.assertEqual(self.svg.count("<path"), self.grid.count)

    def test_cut_paths_are_stroked_and_unfilled(self):
        # A filled shape is a print job to Studio, not a cut.
        self.assertIn('fill="none"', self.svg)

    def test_the_cut_file_carries_no_registration_marks(self):
        # Marks are printed by the sheet, never cut. A mark in the cut file is
        # a mark the blade goes through.
        mk = regmarks.for_grid(self.grid)
        plain = template.write_svg(None, self.grid, self.paths, mk=mk,
                                  proof=False)
        self.assertNotIn("registration-marks", plain)

    def test_a_proof_does_carry_them_and_says_not_to_cut_it(self):
        mk = regmarks.for_grid(self.grid)
        proof = template.write_svg(None, self.grid, self.paths, mk=mk,
                                   proof=True)
        self.assertIn("registration-marks-DO-NOT-CUT", proof)
        self.assertIn("<rect", proof)

    def test_card_rectangles_land_on_the_grid_coordinates(self):
        # The first card's path must start at its top-left corner plus the
        # corner radius.
        first = self.grid.cards[0]
        expected = f"M {first.x + 3.0:.4f}".rstrip("0").rstrip(".")
        self.assertIn(expected.split()[1][:6], self.svg)

    def test_it_writes_to_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "t.svg"
            template.write_svg(path, self.grid, self.paths)
            self.assertTrue(path.is_file())
            self.assertIn("<svg", path.read_text())


class TestDxf(unittest.TestCase):

    def setUp(self):
        self.grid = geometry.scm_layout("Letter", "standard", False, 10.0)
        self.dxf = template.write_dxf(None, self.grid)

    def test_units_are_declared_as_millimetres(self):
        self.assertIn("$INSUNITS", self.dxf)
        self.assertIn("$MEASUREMENT", self.dxf)
        # 4 is millimetres in the DXF units enumeration.
        idx = self.dxf.index("$INSUNITS")
        self.assertIn("4", self.dxf[idx:idx + 40])

    def test_cards_are_closed_polylines(self):
        self.assertEqual(self.dxf.count("POLYLINE"), self.grid.count)
        self.assertEqual(self.dxf.count("SEQEND"), self.grid.count)
        # 70 = 1 marks the polyline closed, so the last edge is cut too.
        # Count in ENTITIES only: $MEASUREMENT in the header is also " 70\n1".
        entities = self.dxf.split("ENTITIES", 1)[1]
        self.assertEqual(entities.count(" 70\n1\n"), self.grid.count)

    def test_corners_carry_a_bulge_not_separate_arcs(self):
        # LINE + ARC renders a visible sharp corner in Silhouette Studio; the
        # bulge-encoded polyline is what fixed that upstream.
        self.assertNotIn("\n  0\nARC\n", self.dxf)
        self.assertIn(" 42\n", self.dxf)
        self.assertIn(f"{template.BULGE_90:.6f}", self.dxf)

    def test_four_bulges_per_rounded_card(self):
        self.assertEqual(self.dxf.count(f"{template.BULGE_90:.6f}"),
                         4 * self.grid.count)

    def test_y_is_flipped_into_dxf_space(self):
        # Our top-left-origin y becomes a bottom-left-origin y, so the
        # topmost card ends up with the largest DXF y.
        # Only the ENTITIES section: the header's $EXTMAX also uses code 20.
        entities = self.dxf.split("ENTITIES", 1)[1]
        ys = [float(v) for v in re.findall(r"\n 20\n([-\d.]+)\n", entities)]
        top_card = min(c.y for c in self.grid.cards)
        self.assertAlmostEqual(max(ys), self.grid.sheet_h_mm - top_card,
                               delta=0.1)

    def test_square_mode_has_no_bulges(self):
        square = template.write_dxf(None, self.grid, template.SQUARE)
        self.assertNotIn(" 42\n", square)

    def test_cross_mode_emits_lines(self):
        cross = template.write_dxf(None, self.grid, template.CROSS)
        self.assertIn("LINE", cross)
        self.assertNotIn("POLYLINE", cross)


class TestJobBuilding(unittest.TestCase):

    def test_a_good_job_is_not_blocked(self):
        j = job_mod.build(preset_name="Plain paper test")
        self.assertEqual(j.blocked, [])
        self.assertTrue(j.ok)

    def test_an_over_long_mark_arm_blocks_the_job(self):
        j = job_mod.build(preset_name="Plain paper test",
                          mark_length_mm=regmarks.MAX_LENGTH_MM)
        self.assertTrue(j.blocked)

    def test_a_sheet_wider_than_the_cutter_is_caught(self):
        j = job_mod.build(sheet="Letter", portrait=False,
                          cutter="Silhouette Portrait 2",
                          preset_name="Plain paper test")
        self.assertTrue(any("cutter" in b.lower() for b in j.blocked))

    def test_the_proxypress_engine_is_the_eight_up_sheet(self):
        """What this workflow actually prints.

        Proxy Press's own b-cameo preset fits four cards: a 16 mm margin
        keeps the grid clear of the marks by brute force and costs half
        the sheet. `cut-proxies proxypress` gives the settings for eight
        on the same paper, and this engine assumes them.
        """
        j = job_mod.build(engine="proxypress", preset_name="Plain paper test")
        self.assertEqual((j.grid.cols, j.grid.rows), (4, 2))
        self.assertEqual(j.grid.count, 8)
        self.assertFalse(j.grid.portrait)

    def test_the_proxypress_engine_uses_card_maker_mark_geometry(self):
        """Not Proxy Press's old defaults.

        It used to draw a fourth mark in the bottom-right, belonging to
        neither Silhouette pattern, on a 0.5 mm line - the machine's thin
        limit, read off dark stock. It has a pattern setting now and this
        workflow sets it to three at 1.0 mm.
        """
        j = job_mod.build(engine="proxypress", preset_name="Plain paper test")
        self.assertEqual(j.marks.pattern, regmarks.THREE)
        self.assertAlmostEqual(j.marks.thickness_mm, 1.0)
        self.assertAlmostEqual(j.marks.inset_mm, 10.0)

    def test_an_older_proxypress_sheet_can_still_be_described(self):
        # A sheet exported before the pattern setting existed.
        j = job_mod.build(engine="proxypress", preset_name="Plain paper test",
                          pattern=regmarks.PROXYPRESS,
                          mark_thickness_mm=0.5)
        self.assertEqual(j.marks.pattern, regmarks.PROXYPRESS)
        self.assertAlmostEqual(j.marks.thickness_mm, 0.5)

    def test_both_engines_put_the_cards_in_the_same_place(self):
        """The seamless bit: a sheet from Proxy Press and a template from
        the Card Maker layout have to be the same sheet."""
        a = job_mod.build(engine="scm", preset_name="Plain paper test")
        b = job_mod.build(engine="proxypress", preset_name="Plain paper test")
        self.assertEqual(len(a.grid.cards), len(b.grid.cards))
        worst = max(max(abs(p.x - q.x), abs(p.y - q.y))
                    for p, q in zip(a.grid.cards, b.grid.cards))
        self.assertLess(worst, 0.1, f"grids differ by {worst:.3f} mm")

    def test_summary_mentions_the_origin_shift(self):
        j = job_mod.build(preset_name="Plain paper test")
        labels = dict(j.summary())
        self.assertIn("Origin shift", labels)

    def test_passes_multiply_the_stream(self):
        one = job_mod.build(preset_name="Plain paper test", passes=1)
        three = job_mod.build(preset_name="Plain paper test", passes=3)
        self.assertGreater(len(three.stream), 2 * len(one.stream))
        self.assertAlmostEqual(three.cut_length_mm, one.cut_length_mm * 3,
                               places=3)

    def test_writes_all_three_files(self):
        j = job_mod.build(preset_name="Plain paper test")
        with tempfile.TemporaryDirectory() as tmp:
            written = job_mod.write_templates(j, "test", tmp)
            self.assertEqual(set(written), {"svg", "dxf", "proof"})
            for path in written.values():
                self.assertTrue(path.is_file())
                self.assertGreater(path.stat().st_size, 0)


class TestVerificationRefuses(unittest.TestCase):
    """verify.py has to fail loudly on a stream that does not match."""

    def setUp(self):
        self.grid = geometry.scm_layout("Letter", "standard", False, 10.0)
        self.mk = regmarks.for_grid(self.grid)
        self.dev = graphtec.device_by_name("Silhouette Cameo 4")
        self.settings = CutSettings(force=20, speed=5, passes=1)
        self.paths = template.cut_paths(self.grid)
        self.job = graphtec.CutJob(
            self.dev, self.settings, origin_mm=self.mk.origin_mm,
            reg_width_mm=self.mk.reg_width_mm,
            reg_length_mm=self.mk.reg_length_mm)
        self.stream = self.job.stream(self.paths)

    def _check(self, stream, settings=None):
        return verify.expectations_met(stream, self.grid, self.mk,
                                       settings or self.settings, self.dev)

    def test_a_matching_stream_passes(self):
        ok, rows = self._check(self.stream)
        failed = [r.label for r in rows if not r.ok]
        self.assertTrue(ok, f"failed: {failed}")

    def test_a_different_force_is_caught(self):
        ok, rows = self._check(self.stream,
                               CutSettings(force=30, speed=5, passes=1))
        self.assertFalse(ok)
        self.assertTrue(any(r.label == "blade force" and not r.ok
                            for r in rows))

    def test_a_stream_without_the_preamble_is_caught(self):
        ok, rows = self._check(self.stream.replace(b"TB99\x03", b""))
        self.assertFalse(ok)
        self.assertTrue(any("preamble" in r.label and not r.ok for r in rows))

    def test_a_stream_built_on_the_wrong_origin_is_caught(self):
        # The failure mode this check exists for: the marks are found, the cut
        # runs, and every card is off by a constant 1 mm because the stream
        # was built against a different mark origin.
        wrong = graphtec.CutJob(
            self.dev, self.settings, origin_mm=(11.0, 10.0),
            reg_width_mm=self.mk.reg_width_mm,
            reg_length_mm=self.mk.reg_length_mm).stream(self.paths)
        ok, rows = self._check(wrong)
        self.assertFalse(ok)
        self.assertTrue(any(r.label == "card position error" and not r.ok
                            for r in rows))

    def test_the_wrong_scan_window_is_caught(self):
        wrong = regmarks.marks(self.grid.sheet_w_mm, self.grid.sheet_h_mm,
                               inset_mm=25.0, length_mm=5.0)
        ok, rows = verify.expectations_met(self.stream, self.grid, wrong,
                                           self.settings, self.dev)
        self.assertFalse(ok)
        self.assertTrue(any("scan window" in r.label and not r.ok
                            for r in rows))

    def test_decode_recovers_the_settings(self):
        d = verify.decode(self.stream)
        self.assertEqual(d.force, 20)
        self.assertEqual(d.speed, 5)
        self.assertEqual(d.holder, 1)
        self.assertTrue(d.preamble_present)
        self.assertEqual(d.mark_type, "Cameo/Portrait")
        self.assertEqual(d.scan_command, "TB123")


class TestPresets(unittest.TestCase):

    def test_every_preset_declares_where_its_numbers_came_from(self):
        from cut_proxies import presets
        for name, p in presets.all_presets().items():
            with self.subTest(preset=name):
                self.assertIn(p.origin, ("silhouette", "guide", "recovered",
                                         "derived", "measured"))

    def test_extrapolated_presets_say_so_in_their_note(self):
        from cut_proxies import presets
        for name, p in presets.all_presets().items():
            if p.origin == "derived":
                with self.subTest(preset=name):
                    self.assertIn("never cut", p.note.lower())

    def test_presets_build_valid_cut_settings(self):
        from cut_proxies import presets
        dev = graphtec.device_by_name("Silhouette Cameo 4")
        for name, p in presets.all_presets().items():
            with self.subTest(preset=name):
                s = p.cut_settings().clamped(dev)
                self.assertGreaterEqual(s.force, 1)
                self.assertGreaterEqual(s.passes, 1)

    def test_the_card_presets_use_more_passes_than_the_paper_one(self):
        from cut_proxies import presets
        card = presets.get("330 gsm black-core").cut_settings()
        paper = presets.get("Plain paper test").cut_settings()
        self.assertGreater(card.passes, paper.passes)
        self.assertGreater(card.force, paper.force)

    def test_speeds_are_inside_the_machine_range(self):
        # Silhouette's own database never exceeds 10; the community guide
        # uses 25 on a Cameo 5. Both are allowed to exist here - what is not
        # allowed is a speed the machine cannot take at all.
        from cut_proxies import presets
        for name, p in presets.all_presets().items():
            with self.subTest(preset=name):
                self.assertGreaterEqual(p.settings.get("speed", 1), 1)
                self.assertLessEqual(p.settings.get("speed", 1), 30)

    def test_the_vendor_presets_match_the_vendor_file(self):
        # condition.csv is Silhouette's and is not vendored, so this only
        # runs where a Studio installation has been read. When it does run it
        # is the strongest check in the file: an `origin: silhouette` preset
        # must be what the vendor actually ships, not a remembered number.
        from cut_proxies import materials, presets
        try:
            table = materials.by_name()
        except (FileNotFoundError, ValueError) as exc:
            self.skipTest(f"no Silhouette condition.csv available: {exc}")
        expected = {
            "Coverstock (Silhouette)": "Coverstock",
            "Cardstock (Silhouette)": "Cardstock",
            "Plain paper test": "Print Paper",
            "Pen, no blade": "Silhouette Sketch Pen",
        }
        for preset_name, material_name in expected.items():
            with self.subTest(preset=preset_name):
                m = table[material_name]
                s = presets.get(preset_name).settings
                self.assertEqual(s["force"], m.force)
                self.assertEqual(s["speed"], m.speed)
                self.assertEqual(s["passes"], m.passes)
                self.assertAlmostEqual(s["blade_diameter_mm"],
                                       m.blade_diameter_mm, places=3)
                if "depth" in s:
                    self.assertEqual(s["depth"], m.autoblade_depth)

    def test_the_pen_has_no_blade_offset_in_the_vendor_file(self):
        # The one place the vendor's data confirms a recovered protocol
        # value: FC is 0 for the pen and 18 for every blade.
        from cut_proxies import materials
        try:
            table = materials.by_name()
        except (FileNotFoundError, ValueError) as exc:
            self.skipTest(str(exc))
        self.assertEqual(table["Silhouette Sketch Pen"].offset_su, 0)
        for name in ("Coverstock", "Cardstock", "Chipboard"):
            self.assertEqual(table[name].offset_su, 18)
            self.assertAlmostEqual(table[name].blade_diameter_mm, 0.9,
                                   places=3)

    def test_a_guide_preset_matches_the_published_starting_point(self):
        from cut_proxies import presets
        s = presets.get("Cameo 4 starting point").settings
        self.assertEqual((s["force"], s["speed"], s["depth"], s["passes"]),
                         (25, 25, 7, 1))


if __name__ == "__main__":
    unittest.main()
