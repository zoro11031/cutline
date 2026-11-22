"""Offline checks for filled paths, font outlines and the checked cut pipeline."""

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from cutline import design_text, editor_model as model, job


def compound_project():
    outside = model.Shape("Rectangle", 35, 40, 58, 40)
    inside = model.Shape("Rectangle", 50, 50, 20, 18)
    shapes, _ = model.modify_selection((outside, inside), (0, 1), "Make Compound Path")
    return model.Project(279.4, 215.9, shapes)


class TestVectorEditing(unittest.TestCase):
    def test_holes_survive_point_edit_mirroring_history_project_files_and_send(self):
        original = compound_project()
        shape, = original.shapes
        self.assertTrue(model.hit(shape, (40, 56), 0))
        self.assertFalse(model.hit(shape, (60, 56), 0))
        hole = next(i for i, path in enumerate(shape.paths()) if all(50 <= x <= 70 for x, _ in path.points))
        point = shape.paths()[hole].points[0]
        self.assertEqual(model.nearest_vertex(shape, point), (0, (hole, 0)))
        edited = model.move_vertex(shape, (hole, 0), (point[0], point[1] + 2))
        self.assertNotEqual(edited.paths()[hole], shape.paths()[hole])
        changed = replace(original, shapes=model.transform_selection((edited,), (0,), "horizontal"))
        history = model.History(original)
        history.commit(changed)
        history.undo()
        self.assertEqual(history.current, original)
        history.redo()
        self.assertEqual(history.current, changed)
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "compound.scc-template"
            model.save_project(target, history.current)
            self.assertEqual(json.loads(target.read_text())["version"], 3)
            loaded = model.load_project(target)
        self.assertEqual(loaded, changed)
        self.assertFalse(model.hit(loaded.shapes[0], (68, 58), 0))
        checked = job.build(imported=loaded.artwork(), preset_name="Plain paper test", registration=False)
        self.assertFalse(checked.blocked)
        self.assertEqual(checked.liveness_failures, [])
        self.assertCountEqual(checked.paths, loaded.artwork().paths)
        self.assertEqual(len(checked.paths), 2)

    def test_weld_intersect_and_subtract_respect_shape_order_and_disconnected_pieces(self):
        shapes = (model.Shape("Rectangle", 30, 30, 50, 30, holder=2),
                  model.Shape("Rectangle", 50, 20, 10, 50))
        welded, _ = model.modify_selection(shapes, (1, 0), "Weld")
        intersected, _ = model.modify_selection(shapes, (0, 1), "Intersect")
        subtracted, _ = model.modify_selection(shapes, (1, 0), "Subtract")
        self.assertEqual(welded[0].holder, 2)
        self.assertTrue(model.hit(welded[0], (55, 22), 0))
        self.assertFalse(model.hit(welded[0], (46, 22), 0))
        self.assertEqual(model.bounds(intersected), model.geometry.Rect(50, 30, 10, 30))
        self.assertEqual(len(subtracted[0].paths()), 2)
        self.assertFalse(model.hit(subtracted[0], (55, 50), 0))
        released, selection = model.modify_selection(subtracted, (0,), "Release Compound Path")
        self.assertEqual([p.kind for p in released], ["Path", "Path"])
        self.assertEqual(tuple(selection), (0, 1))
        separate = (shapes[0], replace(shapes[1], x=100))
        with self.assertRaisesRegex(ValueError, "no cut area"):
            model.modify_selection(separate, (0, 1), "Intersect")

    def test_offsets_preserve_concave_notches_and_grow_or_shrink_holes(self):
        concave = model.compound_shape((((30, 30), (65, 30), (65, 40), (40, 40), (40, 65), (30, 65)),))
        self.assertFalse(model.hit(model.offset_shape(concave, 2), (53, 53), 0))
        ring, = compound_project().shapes
        expanded = model.offset_shape(ring, 2)
        inset = model.offset_shape(ring, -2)
        self.assertTrue(model.hit(expanded, (51, 56), 0))
        self.assertFalse(model.hit(expanded, (59, 56), 0))
        self.assertFalse(model.hit(inset, (49, 56), 0))
        self.assertEqual(len(inset.paths()), 2)
        with self.assertRaisesRegex(ValueError, "no cut area"):
            model.offset_shape(ring, -25)

    def test_saved_editable_text_retains_outline_with_missing_font(self):
        spec = model.TextSpec("B O", "DejaVu Sans", 16)
        if spec.family not in design_text.installed_families():
            self.skipTest("test font DejaVu Sans is not installed")
        lettering = design_text.shape(spec, 35, 40)
        self.assertEqual(len(lettering.paths()), 5)
        edited = design_text.shape(replace(spec, value="O"), 0, 0, lettering)
        self.assertEqual(len(edited.paths()), 2)
        self.assertEqual((edited.x, edited.y), (lettering.x, lettering.y))
        portable = replace(edited, text=replace(edited.text, family="Missing Font For Test"))
        project = model.Project(279.4, 215.9, (portable,))
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "text.scc-template"
            model.save_project(target, project)
            reopened = model.load_project(target)
        self.assertIn(portable.text.family, design_text.unavailable_fonts(reopened))
        self.assertEqual(reopened.artwork().paths, project.artwork().paths)
        with self.assertRaisesRegex(ValueError, portable.text.family):
            design_text.shape(reopened.shapes[0].text, 0, 0, reopened.shapes[0])
        with self.assertRaisesRegex(ValueError, "missing characters"):
            design_text.shape(replace(spec, value="\U0010fffd"), 35, 40)
