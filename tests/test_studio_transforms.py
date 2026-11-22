"""Silhouette Studio's Transform, Replicate, Modify, Offset, Trace and Nesting (user manual chapter 06)."""

import base64
import io
import math
import unittest

from cutline import editor_model as model, transforms

try:
    from PIL import Image, ImageDraw
except ImportError:  # pragma: no cover
    Image = None

SQUARE = model.Shape("Rectangle", 10, 10, 20, 20)
OTHER = model.Shape("Rectangle", 20, 20, 20, 20)


def box(shape):
    b = model.bounds((shape,))
    return round(b.x, 3), round(b.y, 3), round(b.w, 3), round(b.h, 3)


class TestTransformPanel(unittest.TestCase):
    def test_move_by_and_move_to(self):
        self.assertEqual(box(transforms.move_by((SQUARE,), (0,), 5, -2)[0]), (15, 8, 20, 20))
        self.assertEqual(box(transforms.move_to((SQUARE, OTHER), (0, 1), 0, 0)[1]), (10, 10, 20, 20))

    def test_rotate_by_turns_the_selection_as_one_and_rotate_to_sets_each_angle(self):
        turned = transforms.rotate_by((SQUARE, OTHER), (0, 1), 90)
        self.assertEqual([s.rotation for s in turned], [90, 90])
        self.assertEqual(box(model.Shape("Path", 0, 0)) if False else box(turned[0]), (20, 10, 20, 20))
        about = transforms.rotate_by((SQUARE,), (0,), 180, centre=(0, 0))
        self.assertEqual(box(about[0]), (-30, -30, 20, 20))
        absolute = transforms.rotate_to((model.Shape("Rectangle", 0, 0, 10, 10, rotation=30),), (0,), 45)
        self.assertAlmostEqual(absolute[0].rotation, 45)

    def test_scale_by_percent_about_the_centre(self):
        scaled = transforms.scale_by((SQUARE,), (0,), 50)[0]
        self.assertEqual(box(scaled), (15, 15, 10, 10))
        with self.assertRaisesRegex(ValueError, "above 0%"):
            transforms.scale_by((SQUARE,), (0,), 0)

    def test_shear_slants_into_a_path_and_refuses_images_and_silly_angles(self):
        sheared = transforms.shear((SQUARE,), (0,), 45, 0)[0]
        self.assertEqual(sheared.kind, "Path")
        top = min(p[1] for p in sheared.paths()[0].points)
        top_xs = sorted(p[0] for p in sheared.paths()[0].points if abs(p[1] - top) < 1e-6)
        self.assertAlmostEqual(top_xs[0], 0, places=5, msg="the top edge slides by its height")
        with self.assertRaisesRegex(ValueError, "between"):
            transforms.shear((SQUARE,), (0,), 89.5, 0)
        image = model.Shape("Sticker", 0, 0, 10, 10, sticker=None)
        with self.assertRaisesRegex(ValueError, "Images"):
            transforms.shear((image,), (0,), 10, 0)


class TestReplicatePanel(unittest.TestCase):
    def test_duplicate_and_mirror_copies_sit_beside_the_original(self):
        for side, expected in (("left", (-12, 10)), ("right", (32, 10)), ("above", (10, -12)), ("below", (10, 32))):
            with self.subTest(side=side):
                shapes, made = transforms.duplicate_side((SQUARE,), (0,), side, 2)
                self.assertEqual(made, (1,))
                self.assertEqual(box(shapes[1])[:2], expected)
        shapes, _ = transforms.mirror_copy((model.Shape("Rectangle", 10, 10, 20, 20, rotation=10),), (0,), "right", 2)
        self.assertTrue(shapes[1].flip_x)

    def test_rows_columns_and_rotated_copies(self):
        shapes, made = transforms.replicate_line((SQUARE,), (0,), 4, 0, 1)
        self.assertEqual([box(s)[0] for s in shapes], [10, 31, 52, 73])
        shapes, made = transforms.replicate_line((SQUARE,), (0,), 3, 1, 0)
        self.assertEqual([box(s)[1] for s in shapes], [10, 30, 50])
        shapes, made = transforms.rotate_copies((SQUARE,), (0,), 3)
        self.assertEqual(sorted(s.rotation for s in shapes), [0, 90, 180, 270])

    def test_fill_page_and_any_direction(self):
        shapes, made = transforms.fill_page((SQUARE,), (0,), (0, 0, 100, 50), 5)
        self.assertEqual(len(shapes), 4 * 2)
        self.assertEqual(box(shapes[0])[:2], (0, 0), "the original moves into the first place")
        with self.assertRaisesRegex(ValueError, "larger than"):
            transforms.fill_page((SQUARE,), (0,), (0, 0, 10, 10))
        shapes, made = transforms.replicate_direction((SQUARE,), (0,), 2, 0, 90)
        self.assertEqual([round(box(s)[1]) for s in shapes], [10, 30, 50])

    def test_object_to_path_spaces_copies_along_the_line(self):
        line = model.Shape("Line", 0, 100, 90, 0)
        shapes, made = transforms.along_path((SQUARE, line), (0, 1), 1, 4)
        centres = [model.bounds((shapes[i],)) for i in made]
        self.assertEqual([round(b.x + b.w / 2) for b in centres], [0, 30, 60, 90])
        self.assertTrue(all(round(b.y + b.h / 2) == 100 for b in centres))
        with self.assertRaisesRegex(ValueError, "Select the object"):
            transforms.along_path((line,), (0,), 0, 3)


class TestNesting(unittest.TestCase):
    def test_shapes_pack_inside_the_area_without_overlapping(self):
        shapes = tuple(model.Shape("Rectangle", 200 + 5 * i, 200, 30, 20) for i in range(6))
        out, placed, left = transforms.nest(shapes, range(6), (0, 0, 100, 50), padding=1, rotations=1)
        self.assertEqual(len(placed) + len(left), 6)
        boxes = [model.bounds((out[i],)) for i in placed]
        for b in boxes:
            self.assertTrue(0 <= b.x and b.x + b.w <= 100 and 0 <= b.y and b.y + b.h <= 50)
        for i, a in enumerate(boxes):
            for b in boxes[i + 1:]:
                self.assertFalse(a.x < b.x + b.w and b.x < a.x + a.w and a.y < b.y + b.h and b.y < a.y + a.h)
        self.assertEqual(len(placed), 6, "three across and two down fit")

    def test_rotation_lets_a_tall_shape_fit_a_wide_area(self):
        tall = (model.Shape("Rectangle", 0, 0, 10, 60),)
        self.assertEqual(transforms.nest(tall, (0,), (0, 0, 80, 20), padding=0, rotations=1)[2], (0,))
        out, placed, left = transforms.nest(tall, (0,), (0, 0, 80, 20), padding=0, rotations=4)
        self.assertEqual(placed, (0,))
        self.assertEqual(round(out[0].rotation) % 180, 90)


class TestModifyAndOffset(unittest.TestCase):
    pair = (model.Shape("Rectangle", 0, 0, 40, 40), model.Shape("Rectangle", 20, 20, 40, 40))

    def area(self, shapes):
        total = 0.0
        for shape in shapes:
            for path in shape.paths():
                pts = path.points
                total += abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]))) / 2
        return total

    def test_divide_makes_every_overlap_its_own_piece(self):
        out, made = model.modify_selection(self.pair, (0, 1), "Divide")
        self.assertEqual(len(out), 3)
        self.assertAlmostEqual(self.area(out), 40 * 40 * 2 - 20 * 20, delta=0.01)

    def test_subtract_all_cuts_each_shape_by_what_is_in_front(self):
        out, made = model.modify_selection(self.pair, (0, 1), "Subtract All")
        self.assertEqual(len(out), 2)
        self.assertAlmostEqual(self.area(out[:1]), 1600 - 400, delta=0.01)
        self.assertAlmostEqual(self.area(out[1:]), 1600, delta=0.01)

    def test_crop_keeps_shared_areas_once(self):
        out, made = model.modify_selection(self.pair, (0, 1), "Crop")
        self.assertEqual(len(out), 1)
        self.assertAlmostEqual(self.area(out), 400, delta=0.01)
        apart = (model.Shape("Rectangle", 0, 0, 10, 10), model.Shape("Rectangle", 50, 0, 10, 10))
        with self.assertRaisesRegex(ValueError, "no cut area"):
            model.modify_selection(apart, (0, 1), "Crop")

    def test_offset_corner_and_round_joins(self):
        corner = model.offset_shape(self.pair[0], 2, "corner")
        rounded = model.offset_shape(self.pair[0], 2, "round")
        self.assertAlmostEqual(self.area((corner,)), 44 * 44, delta=0.5)
        self.assertLess(self.area((rounded,)), 44 * 44 - 3, "round joins cut the corners")

    def test_detach_lines_splits_fill_from_outline(self):
        filled = model.Shape("Ellipse", 0, 0, 10, 10, fill="#ff0000", stroke="#000000", line_width_mm=1)
        out, made = model.detach_lines((filled,), (0,))
        fill, outline = out
        self.assertEqual((fill.fill, fill.stroke, fill.action), ("#ff0000", "", "None"))
        self.assertEqual((outline.fill, outline.stroke, outline.action), ("", "#000000", "Cut"))
        with self.assertRaisesRegex(ValueError, "fill"):
            model.detach_lines((model.Shape("Rectangle"),), (0,))


@unittest.skipIf(Image is None, "Pillow is needed to trace images")
class TestTrace(unittest.TestCase):
    def image(self):
        from cutline import sticker_assets

        picture = Image.new("RGBA", (200, 100), (255, 255, 255, 255))
        draw = ImageDraw.Draw(picture)
        draw.rectangle([20, 20, 80, 80], fill=(0, 0, 0, 255))
        draw.rectangle([40, 40, 60, 60], fill=(255, 255, 255, 255))
        draw.ellipse([120, 20, 180, 80], fill=(30, 30, 30, 255))
        data = io.BytesIO()
        picture.save(data, "PNG")
        return model.Shape("Sticker", 10, 10, 100, 50,
                           sticker=sticker_assets.Sticker(base64.b64encode(data.getvalue()).decode(), "t.png"))

    def test_trace_styles_and_area(self):
        from cutline import sticker_trace

        shape = self.image()
        image, traced = sticker_trace.trace_shapes(shape, style="Trace")
        self.assertEqual((traced[0].kind, len(traced[0].paths())), ("Compound", 3), "the square's hole is kept")
        self.assertIs(image, shape)
        _, outer = sticker_trace.trace_shapes(shape, style="Trace Outer Edge")
        self.assertEqual(len(outer), 2)
        self.assertAlmostEqual(model.bounds(outer[:1]).x, 20, delta=0.6, msg="traced at the image's place on the page")
        detached, _ = sticker_trace.trace_shapes(shape, style="Trace and Detach")
        self.assertEqual(detached.sticker.image().getpixel((5, 5))[3], 0, "the background is cut away")
        _, right = sticker_trace.trace_shapes(shape, area=(0.5, 0, 1, 1))
        self.assertGreater(model.bounds(right).x, 60)
        with self.assertRaisesRegex(ValueError, "Nothing"):
            sticker_trace.trace_shapes(shape, area=(0, 0, 0.08, 0.15))


if __name__ == "__main__":
    unittest.main()
