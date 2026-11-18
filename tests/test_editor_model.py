import pathlib
import tempfile
import unittest

from cut_proxies import editor_model, job


class TestEditorModel(unittest.TestCase):
    def test_shapes_go_through_the_checked_job_pipeline(self):
        shapes = tuple(editor_model.Shape(kind, x=40 + i * 47)
                       for i, kind in enumerate(editor_model.KINDS))
        design = editor_model.artwork(shapes, 279.4, 215.9)
        self.assertEqual([p.closed for p in design.paths], [True, True, True, False])
        self.assertEqual(design.bounds, (40, 45, 221, 75))
        checked = job.build(imported=design, preset_name="Plain paper test")
        self.assertEqual(checked.blocked, [])

    def test_project_round_trip_preserves_signed_line_dimensions(self):
        with tempfile.TemporaryDirectory() as folder:
            target = pathlib.Path(folder) / "design.scc-template"
            shape = editor_model.Shape("Line", x=60, y=40, width=-10, height=0)
            editor_model.write_project(target, [shape], 279.4, 215.9)
            self.assertEqual(editor_model.read_project(target, 279.4, 215.9), (shape,))
            with self.assertRaisesRegex(ValueError, "size and orientation"):
                editor_model.read_project(target, 215.9, 279.4)

    def test_incomplete_project_shape_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            target = pathlib.Path(folder) / "design.scc-template"
            target.write_text('{"version":1,"page_mm":[279.4,215.9],"shapes":[{"kind":"Line"}]}')
            with self.assertRaisesRegex(ValueError, "Invalid template shape"):
                editor_model.read_project(target, 279.4, 215.9)
