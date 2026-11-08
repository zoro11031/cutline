"""Reading SVG and DXF into cut paths.

The risk with an importer is not that it fails — a failure is visible. It
is that it succeeds and produces the right shape at the wrong size, which
cuts a sheet of almost-cards. So most of these tests are about units and
placement rather than about parsing.

The strongest check in the file is `test_svg_and_dxf_agree_exactly`: the
same drawing written out in both formats, read back by two completely
independent parsers, must land on the same millimetres.
"""

import math
import pathlib
import tempfile
import unittest

from cut_proxies import geometry, importer, job as job_mod, template
from cut_proxies.importer import ImportError_


def svg(body, w="100mm", h="100mm", viewbox="0 0 100 100", extra=""):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" '
            f'height="{h}" viewBox="{viewbox}" {extra}>{body}</svg>')


def load_text(text, suffix=".svg"):
    with tempfile.NamedTemporaryFile("w", suffix=suffix,
                                     delete=False) as fh:
        fh.write(text)
        path = fh.name
    try:
        return importer.load(path)
    finally:
        pathlib.Path(path).unlink(missing_ok=True)


class TestLengths(unittest.TestCase):

    def test_known_units(self):
        self.assertAlmostEqual(importer.length_mm("10mm"), 10.0)
        self.assertAlmostEqual(importer.length_mm("1cm"), 10.0)
        self.assertAlmostEqual(importer.length_mm("1in"), 25.4)
        self.assertAlmostEqual(importer.length_mm("72pt"), 25.4)
        self.assertAlmostEqual(importer.length_mm("96px"), 25.4)

    def test_a_bare_number_is_a_css_pixel(self):
        self.assertAlmostEqual(importer.length_mm("96"), 25.4)

    def test_percentages_and_junk_fall_back(self):
        self.assertIsNone(importer.length_mm("50%"))
        self.assertIsNone(importer.length_mm("wide"))
        self.assertEqual(importer.length_mm(None, 7.0), 7.0)


class TestTransforms(unittest.TestCase):

    def test_translate(self):
        m = importer.parse_transform("translate(3,4)")
        self.assertEqual(importer.apply(m, 1, 1), (4.0, 5.0))

    def test_scale(self):
        m = importer.parse_transform("scale(2,3)")
        self.assertEqual(importer.apply(m, 1, 1), (2.0, 3.0))

    def test_rotate_about_a_point(self):
        m = importer.parse_transform("rotate(90,10,10)")
        x, y = importer.apply(m, 20, 10)
        self.assertAlmostEqual(x, 10.0, places=6)
        self.assertAlmostEqual(y, 20.0, places=6)

    def test_composition_is_outermost_first(self):
        m = importer.parse_transform("translate(10,0) scale(2)")
        self.assertEqual(importer.apply(m, 1, 0), (12.0, 0.0))

    def test_nothing_is_identity(self):
        self.assertEqual(importer.parse_transform(None), importer.IDENTITY)


class TestSvgUnits(unittest.TestCase):
    """The failure that cuts a sheet of almost-cards."""

    def test_viewbox_maps_to_the_declared_size(self):
        im = load_text(svg('<rect x="0" y="0" width="50" height="50"/>',
                           w="200mm", h="200mm", viewbox="0 0 100 100"))
        x0, y0, x1, y1 = im.bounds
        self.assertAlmostEqual(x1 - x0, 100.0, places=3)   # 50 units x 2
        self.assertIn("2.0000 mm per unit", im.scale_note)

    def test_a_one_to_one_viewbox_is_millimetres(self):
        im = load_text(svg('<rect x="10" y="10" width="63" height="88"/>',
                           w="100mm", h="100mm", viewbox="0 0 100 100"))
        x0, y0, x1, y1 = im.bounds
        self.assertAlmostEqual(x1 - x0, 63.0, places=3)
        self.assertAlmostEqual(y1 - y0, 88.0, places=3)

    def test_a_viewbox_origin_is_subtracted(self):
        im = load_text(svg('<rect x="20" y="20" width="10" height="10"/>',
                           viewbox="10 10 100 100"))
        x0, y0, _x1, _y1 = im.bounds
        self.assertAlmostEqual(x0, 10.0, places=3)
        self.assertAlmostEqual(y0, 10.0, places=3)

    def test_no_units_anywhere_is_css_pixels_and_says_so(self):
        im = load_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 96 96">'
            '<rect x="0" y="0" width="96" height="96"/></svg>')
        x0, _y0, x1, _y1 = im.bounds
        self.assertAlmostEqual(x1 - x0, 25.4, places=3)
        self.assertIn("CSS px", im.scale_note)

    def test_the_scale_is_always_reported(self):
        im = load_text(svg('<rect x="0" y="0" width="10" height="10"/>'))
        self.assertTrue(im.scale_note)


class TestSvgShapes(unittest.TestCase):

    def _one(self, body):
        im = load_text(svg(body))
        self.assertEqual(len(im.paths), 1)
        return im.paths[0]

    def test_rect(self):
        p = self._one('<rect x="10" y="20" width="30" height="40"/>')
        self.assertTrue(p.closed)
        xs = [x for x, _ in p.points]
        self.assertAlmostEqual(min(xs), 10.0)
        self.assertAlmostEqual(max(xs), 40.0)

    def test_rounded_rect_keeps_its_extent(self):
        p = self._one('<rect x="0" y="0" width="63" height="88" rx="3"/>')
        xs = [x for x, _ in p.points]
        ys = [y for _, y in p.points]
        self.assertAlmostEqual(max(xs) - min(xs), 63.0, delta=0.05)
        self.assertAlmostEqual(max(ys) - min(ys), 88.0, delta=0.05)

    def test_line_is_open(self):
        p = self._one('<line x1="0" y1="0" x2="10" y2="10"/>')
        self.assertFalse(p.closed)
        self.assertEqual(len(p.points), 2)

    def test_polygon_closes_and_polyline_does_not(self):
        poly = self._one('<polygon points="0,0 10,0 10,10"/>')
        line = self._one('<polyline points="0,0 10,0 10,10"/>')
        self.assertTrue(poly.closed)
        self.assertFalse(line.closed)

    def test_circle_is_round_to_tolerance(self):
        p = self._one('<circle cx="50" cy="50" r="20"/>')
        for x, y in p.points:
            self.assertAlmostEqual(math.hypot(x - 50, y - 50), 20.0,
                                   delta=0.05)

    def test_a_group_transform_reaches_its_children(self):
        im = load_text(svg('<g transform="translate(10,10)">'
                           '<rect x="0" y="0" width="5" height="5"/></g>'))
        x0, y0, _x1, _y1 = im.bounds
        self.assertAlmostEqual(x0, 10.0)
        self.assertAlmostEqual(y0, 10.0)

    def test_nested_transforms_compose(self):
        im = load_text(svg('<g transform="translate(10,0)">'
                           '<g transform="scale(2)">'
                           '<rect x="0" y="0" width="5" height="5"/>'
                           '</g></g>'))
        x0, _y0, x1, _y1 = im.bounds
        self.assertAlmostEqual(x0, 10.0)
        self.assertAlmostEqual(x1, 20.0)


class TestSvgPathData(unittest.TestCase):

    def _pts(self, d):
        im = load_text(svg(f'<path d="{d}"/>'))
        return im.paths[0].points

    def test_absolute_and_relative_lines_agree(self):
        a = self._pts("M10,10 L20,10 L20,20")
        b = self._pts("M10,10 l10,0 l0,10")
        self.assertEqual([tuple(round(v, 6) for v in p) for p in a],
                         [tuple(round(v, 6) for v in p) for p in b])

    def test_h_and_v(self):
        pts = self._pts("M0,0 H10 V10")
        self.assertEqual(pts[-1], (10.0, 10.0))

    def test_z_closes_the_subpath(self):
        im = load_text(svg('<path d="M0,0 H10 V10 Z"/>'))
        self.assertTrue(im.paths[0].closed)

    def test_two_subpaths_become_two_paths(self):
        im = load_text(svg('<path d="M0,0 H10 M20,20 H30"/>'))
        self.assertEqual(len(im.paths), 2)

    def test_implicit_lineto_after_moveto(self):
        pts = self._pts("M0,0 10,0 10,10")
        self.assertEqual(pts[-1], (10.0, 10.0))

    def test_a_cubic_ends_where_it_should(self):
        pts = self._pts("M0,0 C0,10 10,10 10,0")
        self.assertAlmostEqual(pts[-1][0], 10.0, places=6)
        self.assertAlmostEqual(pts[-1][1], 0.0, places=6)

    def test_a_quadratic_ends_where_it_should(self):
        pts = self._pts("M0,0 Q5,10 10,0")
        self.assertAlmostEqual(pts[-1][0], 10.0, places=6)

    def test_an_arc_ends_where_it_should(self):
        pts = self._pts("M0,0 A10,10 0 0 1 20,0")
        self.assertAlmostEqual(pts[-1][0], 20.0, places=4)
        self.assertAlmostEqual(pts[-1][1], 0.0, places=4)

    def test_an_arc_actually_bulges(self):
        pts = self._pts("M0,0 A10,10 0 0 1 20,0")
        self.assertGreater(max(abs(y) for _x, y in pts), 5.0)

    def test_malformed_data_is_refused_not_guessed(self):
        with self.assertRaises(ImportError_):
            load_text(svg('<path d="M0,0 L10"/>'))

    def test_data_without_a_command_is_refused(self):
        with self.assertRaises(ImportError_):
            load_text(svg('<path d="10,10 20,20"/>'))


class TestSvgRefusals(unittest.TestCase):

    def test_a_file_with_no_geometry_is_refused(self):
        with self.assertRaises(ImportError_):
            load_text(svg("<title>nothing here</title>"))

    def test_a_non_svg_root_is_refused(self):
        with self.assertRaises(ImportError_):
            load_text("<html><body/></html>")

    def test_broken_xml_is_refused(self):
        with self.assertRaises(ImportError_):
            load_text("<svg><rect")

    def test_text_is_reported_rather_than_silently_dropped(self):
        im = load_text(svg('<rect x="0" y="0" width="5" height="5"/>'
                           '<text x="1" y="1">hello</text>'))
        self.assertTrue(any("text" in w for w in im.warnings))

    def test_an_unknown_extension_is_refused(self):
        with self.assertRaises(ImportError_):
            importer.load("/tmp/nothing.eps")


class TestDxf(unittest.TestCase):

    def _dxf(self, entities, extra_header=""):
        return ("  0\nSECTION\n  2\nHEADER\n" + extra_header
                + "  0\nENDSEC\n  0\nSECTION\n  2\nENTITIES\n"
                + entities + "  0\nENDSEC\n  0\nEOF\n")

    def test_a_line(self):
        im = load_text(self._dxf(
            "  0\nLINE\n 10\n0.0\n 20\n0.0\n 11\n10.0\n 21\n0.0\n"),
            suffix=".dxf")
        self.assertEqual(len(im.paths), 1)
        self.assertEqual(len(im.paths[0].points), 2)

    def test_a_closed_polyline_with_bulges_round_trips_its_size(self):
        grid = geometry.scm_layout("Letter", "standard", False, 10.0)
        dxf = template.write_dxf(None, grid)
        im = load_text(dxf, suffix=".dxf")
        self.assertEqual(len(im.paths), grid.count)
        for p in im.paths:
            xs = [x for x, _ in p.points]
            ys = [y for _, y in p.points]
            self.assertAlmostEqual(max(xs) - min(xs), 63.0, delta=0.06)
            self.assertAlmostEqual(max(ys) - min(ys), 88.0, delta=0.06)

    def test_insunits_inches_are_converted(self):
        im = load_text(self._dxf(
            "  0\nLINE\n 10\n0.0\n 20\n0.0\n 11\n1.0\n 21\n0.0\n",
            extra_header="  9\n$INSUNITS\n 70\n1\n"), suffix=".dxf")
        xs = [x for x, _ in im.paths[0].points]
        self.assertAlmostEqual(max(xs) - min(xs), 25.4, places=3)
        self.assertIn("inches", im.scale_note)

    def test_missing_insunits_is_assumed_mm_and_said_so(self):
        im = load_text(self._dxf(
            "  0\nLINE\n 10\n0.0\n 20\n0.0\n 11\n1.0\n 21\n0.0\n"),
            suffix=".dxf")
        self.assertIn("millimetres assumed", im.scale_note)

    def test_a_file_with_no_entities_section_is_refused(self):
        with self.assertRaises(ImportError_):
            load_text("  0\nSECTION\n  2\nHEADER\n  0\nENDSEC\n  0\nEOF\n",
                      suffix=".dxf")

    def test_an_empty_entities_section_is_refused(self):
        with self.assertRaises(ImportError_):
            load_text(self._dxf(""), suffix=".dxf")

    def test_a_circle_is_round(self):
        im = load_text(self._dxf(
            "  0\nCIRCLE\n 10\n50.0\n 20\n50.0\n 40\n20.0\n"), suffix=".dxf")
        pts = im.paths[0].points
        cx = sum(x for x, _ in pts) / len(pts)
        cy = sum(y for _, y in pts) / len(pts)
        for x, y in pts:
            self.assertAlmostEqual(math.hypot(x - cx, y - cy), 20.0,
                                   delta=0.06)


class TestFlatteningIsFineEnough(unittest.TestCase):
    """Points on the curve is not the same as a smooth curve.

    Every curve test here originally checked that the sampled points lie
    on the true shape, which stays true no matter how few of them there
    are. A mutation that coarsened the flattening by 40000x passed the
    whole suite: the cards would have been visibly polygonal and nothing
    would have objected. What matters is the sag between consecutive
    points, which is the error the blade actually follows.
    """

    def _max_sag(self, points, centre, radius):
        worst = 0.0
        pts = list(points) + [points[0]]
        for a, b in zip(pts, pts[1:]):
            mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
            worst = max(worst, radius - math.dist(mid, centre))
        return worst

    def test_an_svg_circle_is_flattened_below_the_tolerance(self):
        im = load_text(svg('<circle cx="50" cy="50" r="20"/>'))
        sag = self._max_sag(im.paths[0].points, (50.0, 50.0), 20.0)
        self.assertLessEqual(sag, template.CHORD_TOLERANCE_MM * 1.5,
                             f"chords sag {sag:.4f} mm")

    def test_an_svg_arc_is_flattened_below_the_tolerance(self):
        im = load_text(svg('<path d="M30,50 A20,20 0 0 1 70,50"/>'))
        pts = im.paths[0].points
        worst = 0.0
        for a, b in zip(pts, pts[1:]):
            mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
            worst = max(worst, 20.0 - math.dist(mid, (50.0, 50.0)))
        self.assertLessEqual(worst, template.CHORD_TOLERANCE_MM * 1.5,
                             f"chords sag {worst:.4f} mm")

    def test_an_svg_cubic_is_not_one_straight_line(self):
        im = load_text(svg('<path d="M0,50 C0,0 100,0 100,50"/>'))
        self.assertGreater(len(im.paths[0].points), 12)

    def test_a_dxf_bulge_is_flattened_below_the_tolerance(self):
        # A rounded card corner: 3 mm radius, a quarter turn.
        grid = geometry.scm_layout("Letter", "standard", False, 10.0)
        im = load_text(template.write_dxf(None, grid), suffix=".dxf")
        card = im.paths[0]
        xs = [x for x, _ in card.points]
        ys = [y for _, y in card.points]
        cx, cy = max(xs) - 3.0, min(ys) + 3.0        # a corner arc centre
        on_arc = [p for p in card.points
                  if p[0] > cx - 1e-9 and p[1] < cy + 1e-9]
        self.assertGreater(len(on_arc), 3, "corner is barely flattened")
        worst = 0.0
        for a, b in zip(on_arc, on_arc[1:]):
            mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
            worst = max(worst, 3.0 - math.dist(mid, (cx, cy)))
        self.assertLessEqual(worst, template.CHORD_TOLERANCE_MM * 1.5,
                             f"corner chords sag {worst:.4f} mm")

    def test_a_dxf_circle_is_flattened_below_the_tolerance(self):
        body = ("  0\nSECTION\n  2\nENTITIES\n"
                "  0\nCIRCLE\n 10\n50.0\n 20\n50.0\n 40\n20.0\n"
                "  0\nENDSEC\n  0\nEOF\n")
        im = load_text(body, suffix=".dxf")
        pts = im.paths[0].points
        cx = sum(x for x, _ in pts) / len(pts)
        cy = sum(y for _, y in pts) / len(pts)
        sag = self._max_sag(pts, (cx, cy), 20.0)
        self.assertLessEqual(sag, template.CHORD_TOLERANCE_MM * 1.5,
                             f"chords sag {sag:.4f} mm")


class TestCutByColour(unittest.TestCase):
    """Studio's cut-by-line-colour, which is how one file carries both
    cut lines and print-only guides."""

    SAMPLE = svg(
        '<g stroke="#ff0000">'
        '<rect x="10" y="10" width="20" height="20"/>'
        '<rect x="40" y="10" width="20" height="20"/></g>'
        '<rect x="10" y="40" width="20" height="20" stroke="black"/>'
        '<rect x="40" y="40" width="20" height="20" '
        'style="stroke:#F00;fill:none"/>')

    def setUp(self):
        self.im = load_text(self.SAMPLE)

    def test_colours_are_counted(self):
        self.assertEqual(self.im.colours(), {"#ff0000": 3, "#000000": 1})

    def test_a_group_stroke_is_inherited(self):
        self.assertEqual(self.im.colours()["#ff0000"], 3)

    def test_spellings_of_one_colour_compare_equal(self):
        for spelling in ("red", "#f00", "#FF0000", "rgb(255,0,0)"):
            with self.subTest(spelling=spelling):
                self.assertEqual(len(self.im.only(spelling).paths), 3)

    def test_a_style_attribute_stroke_is_read(self):
        # The fourth rect declares its stroke in `style`, not `stroke`.
        self.assertEqual(len(self.im.only("#ff0000").paths), 3)

    def test_selecting_keeps_only_that_colour(self):
        self.assertEqual(len(self.im.only("black").paths), 1)

    def test_an_absent_colour_is_refused_and_lists_what_is_there(self):
        with self.assertRaises(ImportError_) as ctx:
            self.im.only("blue")
        self.assertIn("#ff0000", str(ctx.exception))

    def test_selection_does_not_disturb_the_geometry(self):
        red = self.im.only("red")
        for p in red.paths:
            xs = [x for x, _ in p.points]
            self.assertAlmostEqual(max(xs) - min(xs), 20.0, places=3)

    def test_normalisation(self):
        self.assertEqual(importer.normalise_colour("#F00"), "#ff0000")
        self.assertEqual(importer.normalise_colour("RED"), "#ff0000")
        self.assertEqual(importer.normalise_colour("rgb(0, 128, 0)"),
                         "#008000")
        self.assertEqual(importer.normalise_colour(None), "")


class TestFormatsAgree(unittest.TestCase):
    """What the SVG/DXF comparison does and does not establish.

    It is tempting to call this "two independent parsers agreeing", and
    that overstates it. A second path only buys something it if does not
    share an assumption with the first, and these share two:

    * both files are written by `template.py` from the same rectangles, so
      the literal corner coordinates are the same numbers twice. Their
      agreement shows the writers and readers round-trip, not that the
      numbers are right.
    * both arc flatteners pick their segment count with the same formula,
      `ceil(sweep / 2*acos(1 - tol/r))`, so they sample identical angles.

    That second one is why the agreement is *exactly* zero rather than
    nearly zero, and exact agreement between supposedly separate
    computations is the tell that they are not separate.

    What it does establish is narrower and still worth having: the SVG
    endpoint-to-centre arc conversion and the DXF bulge-to-centre
    conversion are genuinely different derivations, and they put the arc
    in the same place. Absolute scale is confirmed elsewhere, by a third
    party - `test_sheet.TestEndToEnd` renders a PDF with `rsvg-convert`
    and measures it, which is the only check here that does not go through
    this project's own arithmetic.
    """

    def test_svg_and_dxf_round_trip_to_the_same_geometry(self):
        grid = geometry.scm_layout("Letter", "standard", False, 10.0)
        paths = template.cut_paths(grid)
        a = load_text(template.write_svg(None, grid, paths), suffix=".svg")
        b = load_text(template.write_dxf(None, grid), suffix=".dxf")
        self.assertEqual(len(a.paths), len(b.paths))
        for va, vb in zip(a.bounds, b.bounds):
            self.assertAlmostEqual(va, vb, places=2)

    def test_the_two_arc_derivations_put_the_corner_in_the_same_place(self):
        """The part of the comparison that is a real cross-check.

        SVG arcs arrive endpoint-parameterised and are converted to a
        centre by the spec's implementation notes; DXF arcs arrive as a
        bulge and are converted by a chord-perpendicular offset. Different
        derivations, and they have to agree.
        """
        grid = geometry.scm_layout("Letter", "standard", False, 10.0)
        paths = template.cut_paths(grid)
        a = load_text(template.write_svg(None, grid, paths), suffix=".svg")
        b = load_text(template.write_dxf(None, grid), suffix=".dxf")
        for pa, pb in list(zip(a.paths, b.paths))[:2]:
            for pt in pa.points:
                nearest = min(math.dist(pt, q) for q in pb.points)
                self.assertLess(nearest, 0.01)

    def test_the_absolute_scale_rests_on_an_outside_measurement(self):
        """A guard on the claim, not on the code.

        If the end-to-end render test ever disappears, this project has no
        confirmation of absolute size that does not come from its own
        arithmetic, and the SVG/DXF agreement would be the only thing left
        looking like one.
        """
        import tests.test_sheet as ts
        self.assertTrue(
            hasattr(ts.TestEndToEnd,
                    "test_a_correct_sheet_is_accepted_to_sub_pixel_accuracy"),
            "the only outside measurement of absolute scale has gone")


class TestImportedJobsAreVerified(unittest.TestCase):
    """An imported file gets the same checks as a generated layout."""

    def _import(self):
        grid = geometry.scm_layout("Letter", "standard", False, 10.0)
        return load_text(template.write_dxf(None, grid), suffix=".dxf")

    def test_an_imported_job_builds_and_passes(self):
        j = job_mod.build(sheet="Letter", portrait=False,
                          preset_name="Plain paper test",
                          imported=self._import())
        self.assertEqual(j.blocked, [])
        self.assertEqual(j.grid.engine, "imported")

    def test_the_liveness_probes_still_run_on_an_import(self):
        j = job_mod.build(sheet="Letter", portrait=False,
                          preset_name="Plain paper test",
                          imported=self._import())
        self.assertEqual(j.liveness_failures, [])

    def test_the_summary_says_it_was_imported(self):
        j = job_mod.build(sheet="Letter", portrait=False,
                          preset_name="Plain paper test",
                          imported=self._import())
        self.assertEqual(dict(j.summary())["Source"], "imported file")

    def test_geometry_outside_the_marks_is_still_refused(self):
        # An import is the case where the geometry is least trusted, so the
        # in-bounds check has to apply to it.
        im = load_text(svg('<rect x="2" y="2" width="20" height="20"/>',
                           w="279.4mm", h="215.9mm",
                           viewbox="0 0 279.4 215.9"), suffix=".svg")
        j = job_mod.build(sheet="Letter", portrait=False,
                          preset_name="Plain paper test", imported=im)
        self.assertTrue(j.blocked)


if __name__ == "__main__":
    unittest.main()
