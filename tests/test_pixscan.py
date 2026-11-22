"""PixScan: the recovered mat geometry, and straightening photos of it.

The photos here are synthetic: `pixscan.render` draws the recovered bars,
which are then tilted, rotated and blurred. That proves the detector reads
the geometry it was given; it does not prove a real mat looks like the
drawing."""

import base64
import dataclasses
import io
import unittest

from cutline import pixscan

try:
    from PIL import Image, ImageDraw, ImageFilter
except ImportError:  # pragma: no cover - Pillow is how sticker artwork works too
    Image = None

ITEM = (40, 50, 90, 80)  # a rectangle drawn on the mat, mm


def photograph(mat, *, rotate=0, pixels_per_mm=4.0):
    """The mat drawn flat with an item on it, then seen at an angle."""
    flat, (left, top) = pixscan.render(mat, pixels_per_mm)
    x1, y1, x2, y2 = ITEM
    ImageDraw.Draw(flat).rectangle([((x1 - left) * pixels_per_mm, (y1 - top) * pixels_per_mm),
                                    ((x2 - left) * pixels_per_mm - 1, (y2 - top) * pixels_per_mm - 1)],
                                   fill=(150, 30, 30))
    width, height = flat.size
    tilt = pixscan.homography([(60, 40), (width - 10, 90), (width + 30, height - 20), (20, height + 40)],
                              [(0, 0), (width, 0), (width, height), (0, height)])
    photo = flat.transform((width + 120, height + 120), Image.Transform.PERSPECTIVE,
                           [value / tilt[2][2] for row in tilt for value in row][:8],
                           Image.Resampling.BICUBIC, fillcolor=(200, 200, 200))
    if rotate:
        photo = photo.rotate(rotate, expand=True, fillcolor=(200, 200, 200))
    return photo.filter(ImageFilter.GaussianBlur(0.8))


def item_box(scan):
    """Where the item landed in the straightened photo, mm."""
    image = scan.image.convert("RGB")
    found = [(x, y) for y in range(image.height) for x in range(image.width)
             if (lambda r, g, b: r - g > 70 and r - b > 70)(*image.getpixel((x, y)))]
    p = scan.pixels_per_mm
    xs, ys = [x for x, _ in found], [y for _, y in found]
    return min(xs) / p, min(ys) / p, (max(xs) + 1) / p, (max(ys) + 1) / p


def slid(mat, name, by):
    """The mat with one line moved along x: markers no single perspective explains."""
    return dataclasses.replace(mat, lines=tuple(
        dataclasses.replace(line, start=(line.start[0] + by, line.start[1]), end=(line.end[0] + by, line.end[1]))
        if line.name == name else line for line in mat.lines))


class TestRecoveredGeometry(unittest.TestCase):
    def test_every_line_ends_at_its_own_length(self):
        for mat in pixscan.MATS.values():
            for line in mat.lines:
                with self.subTest(mat=mat.key, line=line.name):
                    self.assertEqual(line.marks[0], 0)
                    self.assertAlmostEqual(line.marks[-1], line.length)
                    self.assertEqual(list(line.marks), sorted(line.marks))

    def test_lines_sharing_a_bar_agree_about_its_gaps(self):
        """No line's gap may fall on another line's bar on the same edge."""
        checked = 0
        for mat in pixscan.MATS.values():
            for first in mat.lines:
                for second in mat.lines:
                    if first is second or pixscan._key(_edge_of(first)) != pixscan._key(_edge_of(second)):
                        continue
                    axis = 1 if _edge_of(first).axis == "x" else 0
                    for a, b in first.gaps():
                        lo, hi = sorted((first.at(a)[axis], first.at(b)[axis]))
                        for c, d in second.bars():
                            blo, bhi = sorted((second.at(c)[axis], second.at(d)[axis]))
                            self.assertFalse(lo + 1e-6 < bhi and blo < hi - 1e-6,
                                             f"mat {mat.key}: {first.name} gap {lo}-{hi} on {second.name} bar")
                            checked += 1
        self.assertGreater(checked, 100)

    def test_the_four_mats_and_their_photo_areas(self):
        self.assertEqual({key: mat.photo_size for key, mat in pixscan.MATS.items()},
                         {1: (212, 218), 2: (275, 215), 3: (314.5, 186), 4: (225, 123)})
        self.assertEqual(pixscan.MARKER_THICKNESS_MM, 1.5)


def _edge_of(line):
    (x1, y1), (x2, y2) = line.start, line.end
    return pixscan.Edge("x", x1, (), ()) if x1 == x2 else pixscan.Edge("y", y1, (), ())


@unittest.skipIf(Image is None, "Install Pillow to straighten PixScan photos")
class TestStraightening(unittest.TestCase):
    def test_each_mat_is_recognised_and_straightened_to_true_size(self):
        for key, mat in pixscan.MATS.items():
            with self.subTest(mat=key):
                scan = pixscan.scan(photograph(mat), pixels_per_mm=4)
                self.assertEqual(scan.mat.key, key)
                self.assertLess(scan.residual_mm, 0.2)
                width, height = mat.photo_size
                self.assertEqual(scan.image.size, (round(width * 4), round(height * 4)))
                for found, wanted in zip(item_box(scan), ITEM):
                    self.assertAlmostEqual(found, wanted, delta=0.5)

    def test_a_photo_taken_from_another_side_reads_the_same(self):
        for angle in (90, 180):
            with self.subTest(angle=angle):
                scan = pixscan.scan(photograph(pixscan.MATS[3], rotate=angle), pixels_per_mm=4)
                self.assertEqual(scan.mat.key, 3)
                for found, wanted in zip(item_box(scan), ITEM):
                    self.assertAlmostEqual(found, wanted, delta=0.5)

    def test_markers_no_perspective_explains_are_refused(self):
        with self.assertRaisesRegex(pixscan.PixScanError, "fit only to within"):
            pixscan.scan(photograph(slid(pixscan.MATS[3], "photo_top", 2.0)), pixels_per_mm=4)

    def test_a_slightly_imperfect_photo_is_still_accepted(self):
        scan = pixscan.scan(photograph(slid(pixscan.MATS[3], "photo_top", 0.5)), pixels_per_mm=4)
        self.assertEqual(scan.mat.key, 3)
        self.assertLessEqual(scan.residual_mm, pixscan.MAX_RESIDUAL_MM)

    def test_a_photo_without_a_mat_is_refused(self):
        blank = Image.new("RGB", (900, 700), (240, 240, 235))
        ImageDraw.Draw(blank).rectangle([200, 200, 500, 400], fill=(30, 30, 30))
        with self.assertRaisesRegex(pixscan.PixScanError, "No PixScan markers"):
            pixscan.scan(blank)

    def test_a_scan_becomes_a_true_size_design_image_that_is_not_cut(self):
        scan = pixscan.scan(photograph(pixscan.MATS[4]), pixels_per_mm=4)
        shape = scan.sticker_shape("blank.jpg")
        self.assertEqual((shape.kind, shape.x, shape.y, shape.width, shape.height), ("Sticker", 0, 0, 225, 123))
        self.assertEqual(shape.action, "None")
        with Image.open(io.BytesIO(base64.b64decode(shape.sticker.png))) as stored:
            self.assertEqual(stored.size, scan.image.size)
        self.assertIn("PixScan mat 4", scan.report())


class TestFitGuard(unittest.TestCase):
    def test_a_close_fit_on_two_edges_is_allowed(self):
        self.assertIsNone(pixscan.check_fit(0.1, 0.3, 2))
        self.assertIsNone(pixscan.check_fit(pixscan.MAX_RESIDUAL_MM, pixscan.MAX_POINT_ERROR_MM, 3))

    def test_a_loose_fit_or_one_edge_is_refused(self):
        self.assertIn("fit only", pixscan.check_fit(pixscan.MAX_RESIDUAL_MM + 0.01, 0.3, 3))
        self.assertIn("fit only", pixscan.check_fit(0.1, pixscan.MAX_POINT_ERROR_MM + 0.01, 3))
        self.assertIn("Fewer than two", pixscan.check_fit(0.05, 0.1, 1))


class TestHomography(unittest.TestCase):
    def test_four_points_are_mapped_exactly_and_more_by_least_squares(self):
        source = [(0, 0), (100, 0), (100, 50), (0, 50), (50, 25)]
        target = [(10, 20), (310, 5), (330, 190), (-5, 170)]
        h = pixscan.homography(source[:4], target)
        for p, q in zip(source, target):
            self.assertAlmostEqual(pixscan.apply(h, p)[0], q[0], places=6)
            self.assertAlmostEqual(pixscan.apply(h, p)[1], q[1], places=6)
        again = pixscan.homography(source, [pixscan.apply(h, p) for p in source])
        self.assertAlmostEqual(pixscan.apply(again, (70, 10))[0], pixscan.apply(h, (70, 10))[0], places=6)

    def test_too_few_or_collinear_points_are_refused(self):
        with self.assertRaises(pixscan.PixScanError):
            pixscan.homography([(0, 0), (1, 0), (2, 0)], [(0, 0), (1, 0), (2, 0)])
        with self.assertRaises(pixscan.PixScanError):
            pixscan.homography([(0, 0), (1, 0), (2, 0), (3, 0)], [(0, 0), (1, 1), (2, 2), (3, 3)])


if __name__ == "__main__":
    unittest.main()
