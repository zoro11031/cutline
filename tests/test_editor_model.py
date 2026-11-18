import json
import pathlib
import tempfile
import unittest

from cut_proxies import editor_model, job, regmarks


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

    def test_v1_opens_on_the_saved_page_with_editable_defaults(self):
        with tempfile.TemporaryDirectory() as folder:
            target = pathlib.Path(folder) / 'old.scc-template'
            target.write_text(json.dumps(dict(version=1, page_mm=[210,297], shapes=[
                dict(kind='Rectangle', x=30, y=35, width=20, height=15, radius=3)])))
            setup = editor_model.Registration.from_marks(regmarks.marks(210,297))
            project = editor_model.load_project(target, legacy_registration=setup)
            self.assertEqual(project, editor_model.Project(210,297,(
                editor_model.Shape('Rectangle', x=30, y=35, width=20, height=15),), setup))

    def test_v2_preserves_page_marks_and_transformed_paths(self):
        setup = editor_model.Registration(10,8.0264,1,'three')
        shape = editor_model.Shape('Path', x=40, y=45, width=20, height=10,
                                   points=((0,0),(1,1)), closed=False,
                                   rotation=90, flip_x=True, action='Score')
        project = editor_model.Project(279.4,215.9,(shape,),setup)
        with tempfile.TemporaryDirectory() as folder:
            target = pathlib.Path(folder) / 'design.scc-template'
            editor_model.save_project(target,project)
            self.assertEqual(json.loads(target.read_text())['version'],2)
            reopened = editor_model.load_project(target)
        self.assertEqual(reopened, project)
        path, = reopened.artwork('Score').paths
        self.assertEqual(path.points, ((55,60),(45,40)))
        self.assertFalse(path.closed)
        self.assertEqual(reopened.artwork().paths, ())

    def test_history_coalesces_a_drag_snapshot_and_clears_redo_after_an_edit(self):
        def project(x):
            return editor_model.Project(279.4,215.9,(editor_model.Shape('Rectangle',x=x),))

        history = editor_model.History(project(20))
        history.commit(project(35))
        history.commit(project(35))
        history.undo()
        self.assertEqual(history.current,project(20))
        history.redo()
        self.assertEqual(history.current,project(35))
        history.undo()
        history.commit(project(60))
        history.redo()
        self.assertEqual(history.current,project(60))

    def test_import_preserves_millimetres_and_stops_on_partial_svg(self):
        with tempfile.TemporaryDirectory() as folder:
            target = pathlib.Path(folder) / 'drawing.svg'

            def write(extra):
                target.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="100mm" '
                                  'height="50mm" viewBox="0 0 200 100">'
                                  '<line x1="20" y1="50" x2="100" y2="50"/>' + extra + '</svg>')

            write('')
            shapes, scale = editor_model.import_shapes(target)
            self.assertEqual(shapes[0].path().points, ((10,25),(50,25)))
            self.assertIn('0.5',scale)
            write('<text x="30" y="40">missing contour</text>')
            with self.assertRaisesRegex(ValueError,'unsupported element'):
                editor_model.import_shapes(target)

    def test_each_review_contains_one_visible_action(self):
        objects = tuple(editor_model.Shape('Rectangle',x=x, y=45, action=action, visible=visible)
                        for x, action, visible in ((40,'Cut',True),(95,'Score',True),
                                                   (150,'Cut',False),(200,'None',True)))
        project = editor_model.Project(279.4,215.9,objects)
        for action, x in (('Cut',40),('Score',95)):
            with self.subTest(action=action):
                design = project.artwork(action)
                self.assertEqual(design.bounds,(x,45,x+40,75))
                result = job.build(imported=design, preset_name='Plain paper test', offset_mm=(0,0))
                self.assertFalse(result.blocked)
                self.assertEqual(len(result.paths),1)
