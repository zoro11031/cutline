"""Measuring the printed sheet.

The unit tests build images directly rather than going through a PDF, so
they are fast and need no external tools. The end-to-end cases render real
PDFs and are skipped where `rsvg-convert` is not available — they are the
ones that matter, so the skip is reported rather than silent.

Two bugs are pinned here that both produced *plausible* wrong answers,
which is the kind worth keeping a test for:

* the raw ink edge of an L includes half the stroke width, reporting every
  correct sheet as displaced outward by 0.47 mm — just inside the 0.5 mm
  tolerance, so it passed while being wrong;
* bounding all the ink in a search window merges the mark with the card
  edge 1.2 mm away, which inflated an 8 mm arm to 12.95 mm and made the
  shape check reject good sheets.
"""

import shutil
import subprocess
import tempfile
import pathlib
import unittest

from cut_proxies import geometry, regmarks, sheet
from cut_proxies.geometry import Rect

try:
    from PIL import Image, ImageDraw
    HAVE_PIL = True
except ImportError:                              # pragma: no cover
    HAVE_PIL = False

DPI = 200
PX = DPI / 25.4


def blank(w_mm=60.0, h_mm=60.0):
    return Image.new("L", (int(w_mm * PX), int(h_mm * PX)), 255)


def fill(img, rect: Rect):
    ImageDraw.Draw(img).rectangle(
        [rect.x * PX, rect.y * PX, rect.x2 * PX, rect.y2 * PX], fill=0)


@unittest.skipUnless(HAVE_PIL, "Pillow is not installed")
class TestInkBlobs(unittest.TestCase):

    def test_one_patch_is_one_blob(self):
        img = blank()
        fill(img, Rect(10, 10, 5, 5))
        blobs = sheet.ink_blobs(img, Rect(0, 0, 60, 60), DPI)
        self.assertEqual(len(blobs), 1)
        # Two pixels of slack, not one: PIL's rectangle includes both end
        # coordinates, and a blob's width counts its last pixel. Both are
        # correct; they just stack in the fixture. The accuracy that
        # matters is measured end to end against a real PDF below, where
        # it comes out at 0.033 mm.
        self.assertAlmostEqual(blobs[0].w, 5.0, delta=2.5 / PX)

    def test_two_separated_patches_stay_separate(self):
        # The mark and a card edge about 1.2 mm apart, as on a full sheet.
        img = blank()
        fill(img, Rect(10, 10, 5, 5))
        fill(img, Rect(10, 16.2, 5, 20))
        blobs = sheet.ink_blobs(img, Rect(0, 0, 60, 60), DPI)
        self.assertEqual(len(blobs), 2)
        heights = sorted(round(b.h) for b in blobs)
        self.assertEqual(heights, [5, 20])

    def test_touching_patches_are_one_blob(self):
        img = blank()
        fill(img, Rect(10, 10, 5, 5))
        fill(img, Rect(10, 15, 5, 5))
        self.assertEqual(
            len(sheet.ink_blobs(img, Rect(0, 0, 60, 60), DPI)), 1)

    def test_a_blank_window_has_no_blobs(self):
        self.assertEqual(
            sheet.ink_blobs(blank(), Rect(0, 0, 60, 60), DPI), [])

    def test_a_window_off_the_image_is_empty_not_an_error(self):
        self.assertEqual(
            sheet.ink_blobs(blank(), Rect(500, 500, 10, 10), DPI), [])


@unittest.skipUnless(HAVE_PIL, "Pillow is not installed")
class TestBestBlobPicksBySize(unittest.TestCase):
    """Size, not proximity — the distinction that stopped a sheet with no
    marks being reported as a sheet with displaced marks."""

    def test_the_right_sized_patch_wins_over_a_nearer_wrong_one(self):
        near_but_wrong = Rect(9, 9, 2, 2)
        far_but_right = Rect(30, 30, 5, 5)
        got = sheet.best_blob([near_but_wrong, far_but_right], 5.0, 2.0)
        self.assertEqual(got, far_but_right)

    def test_it_still_returns_something_when_nothing_fits(self):
        # Reporting "found ink of the wrong shape" beats reporting nothing.
        only = Rect(9, 9, 2, 2)
        self.assertEqual(sheet.best_blob([only], 5.0, 2.0), only)

    def test_no_blobs_gives_nothing(self):
        self.assertIsNone(sheet.best_blob([], 5.0, 2.0))


class TestFoundMarkArithmetic(unittest.TestCase):

    def _mark(self, measured, expected=(10.0, 10.0), box=None, size=5.0):
        return sheet.FoundMark(name="top-left", expected=expected,
                               measured=measured, ink_box=box, kind="square",
                               expected_size_mm=size)

    def test_delta_and_error(self):
        m = self._mark((10.5, 9.7))
        self.assertAlmostEqual(m.delta_mm[0], 0.5)
        self.assertAlmostEqual(m.delta_mm[1], -0.3)
        self.assertAlmostEqual(m.error_mm, 0.5)

    def test_a_mark_that_was_not_found_has_no_delta(self):
        m = self._mark(None)
        self.assertIsNone(m.delta_mm)
        self.assertIsNone(m.error_mm)
        self.assertFalse(m.is_credible)
        self.assertIn("no ink found", m.describe())

    def test_ink_of_the_wrong_size_is_not_credible(self):
        m = self._mark((10.0, 10.0), box=Rect(10, 10, 1.5, 1.5), size=5.0)
        self.assertFalse(m.is_credible)
        self.assertIn("is not that mark", m.describe())

    def test_ink_of_the_right_size_is_credible(self):
        m = self._mark((10.0, 10.0), box=Rect(10, 10, 5.1, 4.9), size=5.0)
        self.assertTrue(m.is_credible)


def _render_pdf(svg_text, out):
    with tempfile.NamedTemporaryFile("w", suffix=".svg", delete=False) as fh:
        fh.write(svg_text)
        src = fh.name
    try:
        r = subprocess.run(["rsvg-convert", "-f", "pdf", src, "-o", str(out)],
                           capture_output=True, check=False)
        return r.returncode == 0
    finally:
        pathlib.Path(src).unlink(missing_ok=True)


@unittest.skipUnless(shutil.which("rsvg-convert") and shutil.which("pdftoppm"),
                     "needs rsvg-convert and pdftoppm")
class TestEndToEnd(unittest.TestCase):
    """Render a real sheet, measure it, and check each verdict."""

    @classmethod
    def setUpClass(cls):
        from cut_proxies import template
        cls.grid = geometry.scm_layout("Letter", "standard", False, 10.0)
        cls.mk = regmarks.for_grid(cls.grid, inset_mm=10.0)
        paths = template.cut_paths(cls.grid)
        cls.proof = template.write_svg(None, cls.grid, paths, mk=cls.mk,
                                       proof=True)
        cls.cutonly = template.write_svg(None, cls.grid, paths, mk=cls.mk,
                                         proof=False)
        cls.tmp = tempfile.TemporaryDirectory(prefix="cut-proxies-sheet-test.")
        cls.dir = pathlib.Path(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _measure(self, svg, name):
        out = self.dir / f"{name}.pdf"
        self.assertTrue(_render_pdf(svg, out), "rsvg-convert failed")
        return sheet.measure(out, self.mk, dpi=DPI)

    def _transformed(self, transform):
        head = self.proof[:self.proof.index(">", self.proof.index("<svg")) + 1]
        body = self.proof.split(">", 2)[2].rsplit("</svg>", 1)[0]
        return f'{head}<g transform="{transform}">{body}</g></svg>'

    def test_a_correct_sheet_is_accepted_to_sub_pixel_accuracy(self):
        r = self._measure(self.proof, "good")
        self.assertEqual(
            r.problems(self.grid.sheet_w_mm, self.grid.sheet_h_mm), [])
        # One pixel at 200 dpi is 0.127 mm; we should be inside that.
        self.assertLess(r.worst_error_mm, 0.13)
        self.assertIn("where the template expects", r.diagnosis())

    def test_every_mark_is_found(self):
        r = self._measure(self.proof, "good2")
        self.assertEqual(len(r.found), len(self.mk.ells)
                         + (1 if self.mk.square else 0))
        self.assertEqual(r.missing, [])
        self.assertEqual(r.wrong_shape, [])

    def test_a_sheet_with_no_marks_says_so(self):
        # The cut-only file: card outlines, no registration marks. This is
        # a Proxy Press export with its Silhouette option switched off.
        r = self._measure(self.cutonly, "nomarks")
        problems = r.problems(self.grid.sheet_w_mm, self.grid.sheet_h_mm)
        self.assertTrue(problems)
        self.assertEqual(r.found, [])
        self.assertIn("no registration marks", r.diagnosis())

    def test_a_scaled_sheet_is_called_a_scaling_error(self):
        r = self._measure(
            self._transformed("translate(4.191,3.2385) scale(0.97)"),
            "scaled")
        self.assertTrue(
            r.problems(self.grid.sheet_w_mm, self.grid.sheet_h_mm))
        self.assertIn("scaled or rotated", r.diagnosis())

    def test_a_shifted_sheet_is_called_a_correctable_offset(self):
        r = self._measure(self._transformed("translate(1.5,0.8)"), "shifted")
        self.assertTrue(
            r.problems(self.grid.sheet_w_mm, self.grid.sheet_h_mm))
        self.assertIn("correctable", r.diagnosis())
        # And it should report roughly the offset that was applied, so the
        # number can be handed to `cut-proxies calibrate`.
        dxs = [m.delta_mm[0] for m in r.found]
        dys = [m.delta_mm[1] for m in r.found]
        self.assertAlmostEqual(sum(dxs) / len(dxs), 1.5, delta=0.2)
        self.assertAlmostEqual(sum(dys) / len(dys), 0.8, delta=0.2)

    def test_the_wrong_page_size_is_caught(self):
        r = self._measure(self.proof, "good3")
        problems = r.problems(210.0, 297.0)       # A4 template, Letter sheet
        self.assertTrue(any("page is" in p for p in problems))

    def test_a_missing_page_is_an_error_not_a_pass(self):
        out = self.dir / "good.pdf"
        if not out.is_file():
            self._measure(self.proof, "good")
        with self.assertRaises(sheet.SheetError):
            sheet.measure(out, self.mk, page=9)

    def test_a_file_that_is_not_a_pdf_is_an_error(self):
        bad = self.dir / "not.pdf"
        bad.write_text("this is not a pdf")
        with self.assertRaises(sheet.SheetError):
            sheet.measure(bad, self.mk)


if __name__ == "__main__":
    unittest.main()
