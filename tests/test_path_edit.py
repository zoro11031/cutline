"""Point editing (Studio user manual chapter 04): nodes, handles and the toolbar actions."""

import json
import math
import pathlib
import tempfile
import unittest

from cut_proxies import editor_model as model, path_edit


def outline(shape):
    return [p for path in shape.paths() for p in path.points]


def segments(shape):
    for path in shape.paths():
        pts = list(path.points) + ([path.points[0]] if path.closed else [])
        yield from zip(pts, pts[1:])


def to_segment(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = dx * dx + dy * dy
    t = 0 if not length else max(0, min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length))
    return math.dist(p, (a[0] + dx * t, a[1] + dy * t))


def near(points, target, tolerance):
    return min(math.dist(p, target) for p in points) <= tolerance


def same_outline(test, first, second, tolerance=0.05):
    """Every vertex of each outline lies on the other's line segments."""
    for here, there in ((first, second), (second, first)):
        edges = list(segments(there))
        for p in outline(here):
            test.assertLessEqual(min(to_segment(p, a, b) for a, b in edges), tolerance,
                                 f"{p} is not on the other outline")


class TestNodes(unittest.TestCase):
    def test_an_ellipse_becomes_four_smooth_points_on_the_same_circle(self):
        circle = model.Shape("Ellipse", 10, 10, 40, 40)
        nodes = path_edit.nodes(circle)
        self.assertEqual(len(nodes.points), 4)
        self.assertTrue(all(entry[2] for entry in nodes.handles))
        path = path_edit.to_shape(nodes, circle)
        for p in outline(path):
            self.assertAlmostEqual(math.dist(p, (30, 30)), 20, delta=0.02)

    def test_rotated_rectangles_and_rounded_rectangles_keep_their_outline(self):
        for shape in (model.Shape("Rectangle", 10, 20, 50, 30, rotation=30),
                      model.Shape("Rounded rectangle", 10, 20, 50, 30, radius=6, flip_x=True),
                      model.Shape("Line", 5, 5, 40, 10)):
            with self.subTest(kind=shape.kind):
                path = path_edit.to_shape(path_edit.nodes(shape), shape)
                self.assertEqual((path.kind, path.rotation), ("Path", 0))
                same_outline(self, shape, path, tolerance=0.05)

    def test_style_action_and_group_survive_conversion(self):
        shape = model.Shape("Rectangle", 0, 0, 10, 10, action="Score", stroke="#ff0000", group=3, holder=2)
        path = path_edit.to_shape(path_edit.nodes(shape), shape)
        self.assertEqual((path.action, path.stroke, path.group, path.holder), ("Score", "#ff0000", 3, 2))

    def test_compound_paths_are_not_edited_as_one_outline(self):
        compound = model.compound_shape((((0, 0), (10, 0), (10, 10)), ((20, 0), (30, 0), (30, 10))))
        with self.assertRaisesRegex(ValueError, "Release"):
            path_edit.nodes(compound)


class TestToolbar(unittest.TestCase):
    square = model.Shape("Rectangle", 0, 0, 40, 40)

    def test_adding_a_point_leaves_the_outline_where_it_was(self):
        for shape in (self.square, model.Shape("Ellipse", 0, 0, 40, 30)):
            with self.subTest(kind=shape.kind):
                nodes = path_edit.nodes(shape)
                target = outline(shape)[3]
                added, index = path_edit.insert_point(nodes, (target[0] + 0.3, target[1]), 1.0)
                self.assertEqual(len(added.points), len(nodes.points) + 1)
                self.assertLess(math.dist(added.points[index], target), 1.0)
                same_outline(self, path_edit.to_shape(nodes, shape), path_edit.to_shape(added, shape))

    def test_a_point_is_only_added_on_the_outline(self):
        with self.assertRaisesRegex(ValueError, "on the outline"):
            path_edit.insert_point(path_edit.nodes(self.square), (20, 20), 1.0)

    def test_deleting_a_point_joins_its_neighbours_down_to_the_minimum(self):
        nodes = path_edit.nodes(self.square)
        three = path_edit.delete_point(nodes, 2)
        self.assertEqual(three.points, ((0, 0), (40, 0), (0, 40)))
        with self.assertRaisesRegex(ValueError, "at least"):
            path_edit.delete_point(three, 0)

    def test_breaking_a_closed_path_opens_it_and_breaking_an_open_one_splits_it(self):
        nodes = path_edit.nodes(self.square)
        opened, = path_edit.break_path(nodes, 1)
        self.assertFalse(opened.closed)
        self.assertEqual(opened.points[0], opened.points[-1])
        self.assertEqual(opened.points[0], (40, 0))
        same_outline(self, self.square, path_edit.to_shape(opened, self.square))
        first, second = path_edit.break_path(opened, 2)
        self.assertEqual(first.points[-1], second.points[0])
        self.assertEqual(len(first.points) + len(second.points), len(opened.points) + 1)
        with self.assertRaisesRegex(ValueError, "already an end"):
            path_edit.break_path(opened, 0)

    def test_dragging_one_end_onto_the_other_closes_the_path(self):
        opened, = path_edit.break_path(path_edit.nodes(self.square), 0)
        moved = path_edit.move_point(opened, len(opened.points) - 1, (0.4, 0.2))
        joined = path_edit.join_ends(moved, 1.0)
        self.assertTrue(joined.closed)
        self.assertEqual(len(joined.points), 4)
        self.assertIsNone(path_edit.join_ends(path_edit.move_point(opened, 4, (5, 5)), 1.0))

    def test_make_curve_bends_only_once_a_handle_moves_and_make_flat_undoes_it(self):
        nodes = path_edit.nodes(self.square)
        curved = path_edit.make_curve(nodes, 0)
        same_outline(self, path_edit.to_shape(nodes, self.square), path_edit.to_shape(curved, self.square))
        bent = path_edit.move_handle(curved, 0, 1, (13, -12))
        top = [p for p in outline(path_edit.to_shape(bent, self.square)) if 1 < p[0] < 39 and p[1] < 1]
        self.assertTrue(any(p[1] < -3 for p in top), "the top edge bows outward")
        flat = path_edit.make_flat(bent, 0)
        same_outline(self, self.square, path_edit.to_shape(flat, self.square))
        self.assertEqual(flat.handles, ())

    def test_make_curve_leaves_a_segment_that_is_already_curved_alone(self):
        circle = model.Shape("Ellipse", 0, 0, 40, 40)
        nodes = path_edit.nodes(circle)
        self.assertEqual(path_edit.make_curve(nodes, 1), nodes)
        same_outline(self, circle, path_edit.to_shape(path_edit.make_curve(nodes, 1), circle))

    def test_smooth_lines_handles_up_and_a_smooth_handle_drags_its_partner(self):
        nodes = path_edit.smooth(path_edit.nodes(self.square), 1)
        a, b, smooth = nodes.handles[1]
        p = nodes.points[1]
        self.assertTrue(smooth)
        cross = (a[0] - p[0]) * (b[1] - p[1]) - (a[1] - p[1]) * (b[0] - p[0])
        self.assertAlmostEqual(cross, 0, places=6)
        moved = path_edit.move_handle(nodes, 1, 1, (p[0] + 10, p[1]))
        a2 = moved.handles[1][0]
        self.assertAlmostEqual(a2[1], p[1])
        self.assertLess(a2[0], p[0], "the in handle swings to the opposite side")
        cornered = path_edit.move_handle(path_edit.corner(nodes, 1), 1, 1, (p[0] + 10, p[1]))
        self.assertEqual(cornered.handles[1][0], a, "a corner's handles move independently")

    def test_curve_clicks_become_smooth_points_the_outline_passes_through(self):
        clicks = ((0, 0), (30, -10), (50, 20), (10, 30))
        nodes = path_edit.from_clicks(clicks, closed=True)
        shape = path_edit.to_shape(nodes, model.Shape("Path"))
        self.assertEqual(len(shape.points), 4)
        flattened = outline(shape)
        self.assertGreater(len(flattened), 20)
        for click in clicks:
            self.assertTrue(near(flattened, click, 1e-6))


class TestCurvesInProjects(unittest.TestCase):
    def test_curves_save_as_version_six_and_reopen_unchanged(self):
        shape = path_edit.to_shape(path_edit.nodes(model.Shape("Ellipse", 0, 0, 30, 20)), model.Shape("Path"))
        project = model.Project(279.4, 215.9, (shape,))
        self.assertEqual(model.project_version(project), 6)
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / "curve.scc-template"
            model.save_project(path, project)
            self.assertEqual(model.load_project(path), project)
            document = json.loads(path.read_text())
            document["version"] = 5
            path.write_text(json.dumps(document))
            with self.assertRaisesRegex(ValueError, "Invalid template shape"):
                model.load_project(path)

    def test_malformed_handles_are_refused(self):
        shape = path_edit.to_shape(path_edit.nodes(model.Shape("Ellipse", 0, 0, 30, 20)), model.Shape("Path"))
        document = model.project_document(model.Project(279.4, 215.9, (shape,)))
        for broken in (document["objects"][0]["handles"][:2], [[[0, 0], [1, 1], "yes"]] * 4,
                       [[[0, 0], [99, 0], True]] * 4):
            with self.subTest(broken=str(broken)[:40]), tempfile.TemporaryDirectory() as folder:
                document["objects"][0]["handles"] = broken
                path = pathlib.Path(folder) / "bad.scc-template"
                path.write_text(json.dumps(document))
                with self.assertRaisesRegex(ValueError, "Invalid template shape"):
                    model.load_project(path)

    def test_a_straight_path_still_saves_in_the_older_format(self):
        path = path_edit.to_shape(path_edit.nodes(model.Shape("Rectangle", 0, 0, 10, 10)), model.Shape("Path"))
        self.assertEqual(path.handles, ())
        self.assertEqual(model.project_version(model.Project(279.4, 215.9, (path,))), 2)


if __name__ == "__main__":
    unittest.main()
