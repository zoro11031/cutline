from dataclasses import replace
import json
import pathlib
import tempfile
import unittest

from cut_proxies import actions, editor_model, job, regmarks, sticker_assets
from tests.test_sticker_print import make_image


def _encoded_png():
    import base64
    with tempfile.TemporaryDirectory() as folder:
        target = pathlib.Path(folder) / 'art.png'
        make_image(target)
        return base64.b64encode(target.read_bytes()).decode('ascii')


_PNG = _encoded_png()


class TestEditorModel(unittest.TestCase):
    def test_page_rotation_repositions_geometry_and_swaps_dimensions(self):
        shape = editor_model.Shape("Rectangle", 30, 40, 20, 10, holder=2, line_style="dashed")
        project = editor_model.Project(200, 100, (shape,))
        turned = editor_model.rotate_page(project, True)
        self.assertEqual((turned.width, turned.height), (100, 200))
        self.assertEqual(editor_model.bounds(turned.shapes), editor_model.geometry.Rect(50, 30, 10, 20))
        self.assertEqual(editor_model.rotate_page(turned, False), project)
        with tempfile.TemporaryDirectory() as folder:
            target = pathlib.Path(folder) / "holder.scc-template"
            editor_model.save_project(target, turned)
            self.assertEqual(editor_model.load_project(target), turned)

    def test_smart_snap_uses_nearby_edges_and_leaves_distant_moves_alone(self):
        shapes = (editor_model.Shape("Rectangle", 10, 10, 10, 10),
                  editor_model.Shape("Rectangle", 40, 40, 10, 10))
        self.assertEqual(editor_model.snap_move(shapes, (0,), 19.5, 19, 200, 100, 2), (20, 20))
        self.assertEqual(editor_model.snap_move(shapes, (0,), 17, 17, 200, 100, 2), (17, 17))

    def test_offset_keeps_the_source_and_refuses_shapes_that_cannot_be_offset(self):
        shape = editor_model.Shape("Rectangle", 30, 40, 20, 10, holder=2)
        expanded = editor_model.offset_shape(shape, 2.0)
        inset = editor_model.offset_shape(shape, -2.0)
        self.assertEqual(editor_model.bounds((expanded,)), editor_model.geometry.Rect(28, 38, 24, 14))
        self.assertEqual(editor_model.bounds((inset,)), editor_model.geometry.Rect(32, 42, 16, 6))
        self.assertEqual(expanded.holder, 2)
        with self.assertRaisesRegex(ValueError, "too large"):
            editor_model.offset_shape(shape, -6.0)
        with self.assertRaisesRegex(ValueError, "closed shape"):
            editor_model.offset_shape(editor_model.Shape("Line"), 2.0)

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
            saved = json.loads(target.read_text())
            self.assertEqual(saved['version'], 2)
            self.assertTrue({'holder', 'line_style', 'line_width_mm'}.isdisjoint(saved['objects'][0]))
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


    def test_opening_old_v2_shape_supplies_optional_color_and_mat_defaults(self):
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / 'old.scc-template'
            base = editor_model.Shape('Rectangle', x=25)
            editor_model.save_project(path, editor_model.Project(279.4, 215.9, (base,)))
            doc = json.loads(path.read_text())
            del doc['objects'][0]['fill'], doc['objects'][0]['stroke'], doc['objects'][0]['group'], doc['mat']
            path.write_text(json.dumps(doc))
            self.assertEqual(editor_model.load_project(path).shapes, (base,))
            self.assertEqual(editor_model.load_project(path).mat, 'auto')

    def test_spacing_and_align_use_transformed_edges(self):
        shapes = (editor_model.Shape('Rectangle', x=20, y=60, width=15, height=10, rotation=90),
                  editor_model.Shape('Rectangle', x=60, y=50, width=20, height=20),
                  editor_model.Shape('Rectangle', x=105, y=40, width=25, height=30))
        moved = editor_model.align(shapes, (0, 1, 2), 'Align Top')
        self.assertEqual([editor_model.bounds((s,)).y for s in moved], [40, 40, 40])
        moved = editor_model.align(shapes, (0, 1, 2), 'Space Horizontally')
        a, b, c = [editor_model.bounds((s,)) for s in moved]
        self.assertAlmostEqual(b.x - a.x2, c.x - b.x2)
        self.assertEqual((moved[0], moved[2]), (shapes[0], shapes[2]))

    def test_replicate_moves_selection_as_a_unit_with_physical_spacing(self):
        shapes = (editor_model.Shape('Rectangle', x=20, y=30, width=40, height=30),
                  editor_model.Shape('Ellipse', x=80, y=35, width=10, height=10))
        result = editor_model.replicate(shapes, (0, 1), 2, 2, 5, 7)
        self.assertEqual(len(result), 8)
        self.assertEqual([(s.x, s.y) for s in result[2:]], [(95, 30), (155, 35),
                         (20, 67), (80, 72), (95, 67), (155, 72)])
        self.assertEqual([s.kind for s in result[2:]], ['Rectangle', 'Ellipse'] * 3)

    def test_open_line_hit_uses_segments_and_vertex_edit_keeps_style_and_action(self):
        line = editor_model.Shape('Line', x=20, y=20, width=30, height=30,
                                  action='Score', stroke='#ff0000')
        self.assertFalse(editor_model.hit(line, (20, 44), 1))
        self.assertTrue(editor_model.hit(line, (40, 40), 1))
        edited = editor_model.move_vertex(line, 1, (60, 54))
        self.assertEqual(edited.path().points, ((20, 20), (60, 54)))
        self.assertFalse(edited.closed)
        self.assertEqual((edited.action, edited.stroke), ('Score', '#ff0000'))

    def test_line_and_fill_survive_project_save_import_and_send_groups(self):
        with tempfile.TemporaryDirectory() as folder:
            target = pathlib.Path(folder) / 'art.svg'
            target.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="210mm" height="297mm" '
                              'viewBox="0 0 210 297"><rect x="45" y="45" width="30" height="30" '
                              'stroke="#f00" fill="#00ff00"/></svg>')
            shapes, _ = editor_model.import_shapes(target)
            target = target.with_suffix('.scc-template')
            project = editor_model.Project(210, 297, shapes, mat='cameo-12x12')
            editor_model.save_project(target, project)
            reopened = editor_model.load_project(target)
        self.assertEqual(reopened.mat, 'cameo-12x12')
        self.assertEqual(reopened.artwork().paths[0].stroke, '#ff0000')
        self.assertEqual(reopened.artwork().paths[0].fill, '#00ff00')
        built = job.build(imported=reopened.artwork(), use_document_page=True, registration=False,
                          send_mode='fill', assignments=(actions.parse_assignment('#00ff00=Plain paper test'),))
        self.assertEqual(len(built.paths), 1)
        self.assertEqual(built.blocked, [])


    def test_grouped_objects_align_together_and_copies_remain_separate_groups(self):
        objects = tuple(editor_model.Shape('Rectangle', x=x, y=y, width=10, height=15)
                        for x, y in ((20, 35), (40, 60), (70, 30)))
        grouped = editor_model.group_selection(objects, (0, 1))
        self.assertEqual(editor_model.group_members(grouped, 1), (0, 1))
        aligned = editor_model.align(grouped, (0, 1, 2), 'Align Top')
        self.assertEqual([s.y for s in aligned], [30, 55, 30])
        copied = editor_model.replicate(aligned, (0, 1), 3, 1, 2, 2)
        self.assertEqual(editor_model.group_members(copied, 3), (3, 4))
        self.assertEqual(editor_model.group_members(copied, 5), (5, 6))
        released = editor_model.group_selection(copied, (3,), release=True)
        self.assertIsNone(released[4].group)
        self.assertEqual(editor_model.group_members(released, 0), (0, 1))

    def test_simplify_reduces_open_and_closed_paths_and_keeps_the_shape(self):
        curve = tuple((index / 20, (index / 20) ** 2) for index in range(21))
        opened = editor_model.Shape('Path', 10, 10, 60, 60, points=curve, closed=False)
        reduced = editor_model.convert_path(opened, simplify=True)
        self.assertLess(len(reduced.points), len(curve))
        self.assertGreaterEqual(len(reduced.points), 2)
        self.assertEqual(len(editor_model.convert_path(opened).points), len(curve))

        edge = tuple(index / 10 for index in range(10))
        ring = (*((step, 0) for step in edge), *((1, step) for step in edge),
                *((1 - step, 1) for step in edge), *((0, 1 - step) for step in edge))
        closed = editor_model.Shape('Path', 10, 10, 60, 60, points=ring, closed=True)
        corners = editor_model.convert_path(closed, simplify=True)
        self.assertEqual(len(corners.points), 4)
        self.assertTrue(corners.closed)
        self.assertEqual((corners.x, corners.y, corners.width, corners.height), (10, 10, 60, 60))

    def test_a_project_is_saved_at_the_highest_version_its_features_need(self):
        plain = editor_model.Shape('Rectangle', 10, 10, 20, 20)
        text = editor_model.Shape('Text', 10, 10, 20, 20, contours=(((0, 0), (1, 0), (1, 1)),),
                                  closed=True, text=editor_model.TextSpec('Hi', 'Sans', 10))
        layered = replace(plain, layer='Crease')
        sticker = editor_model.Shape('Sticker', 10, 10, 20, 20, closed=True,
                                     contours=(((0, 0), (1, 0), (1, 1)),),
                                     sticker=sticker_assets.Sticker(_PNG, 'art.png'))
        for shapes, guides, expected in (((plain,), (), 2), ((text,), (), 3), ((plain,), (('h', 20.0),), 5),
                                         ((layered,), (), 5), ((text, layered), (), 5),
                                         ((sticker,), (), 4),
                                         ((replace(sticker, layer='Print'),), (), 5),
                                         ((sticker, layered), (), 5),
                                         ((sticker,), (('v', 30.0),), 5)):
            project = editor_model.Project(279.4, 215.9, shapes, guides=guides)
            self.assertEqual(editor_model.project_version(project), expected)
            with tempfile.TemporaryDirectory() as folder:
                target = pathlib.Path(folder) / 'design.scc-template'
                editor_model.save_project(target, project)
                self.assertEqual(json.loads(target.read_text())['version'], expected)
                self.assertEqual(editor_model.load_project(target), project)

    def test_layers_and_guides_are_refused_by_versions_that_never_carried_them(self):
        with tempfile.TemporaryDirectory() as folder:
            target = pathlib.Path(folder) / 'old.scc-template'
            shape = dict(kind='Rectangle', x=10, y=10, width=20, height=20, radius=0, rotation=0,
                         flip_x=False, flip_y=False, visible=True, action='Cut', points=[],
                         closed=True)
            target.write_text(json.dumps(dict(version=3, page_mm=[279.4, 215.9], registration=None,
                                              mat='auto', objects=[dict(shape, layer='Crease')])))
            with self.assertRaisesRegex(ValueError, 'Invalid template shape'):
                editor_model.load_project(target)
            target.write_text(json.dumps(dict(version=3, page_mm=[279.4, 215.9], registration=None,
                                              mat='auto', objects=[shape], guides=[['h', 20.0]])))
            with self.assertRaisesRegex(ValueError, 'version 5'):
                editor_model.load_project(target)
            target.write_text(json.dumps(dict(version=5, page_mm=[279.4, 215.9], registration=None,
                                              mat='auto', objects=[dict(shape, layer='Crease')],
                                              guides=[['h', 20.0]])))
            reopened = editor_model.load_project(target)
            self.assertEqual(reopened.shapes[0].layer, 'Crease')
            self.assertEqual(reopened.guides, (('h', 20.0),))

    def test_legacy_stickers_with_layers_and_guides_reopen_and_save_at_version_five(self):
        sticker = editor_model.Shape('Sticker', 10, 10, 20, 20, closed=True, layer='Print',
                                     contours=(((0, 0), (1, 0), (1, 1)),),
                                     sticker=sticker_assets.Sticker(_PNG, 'art.png'))
        project = editor_model.Project(279.4, 215.9, (sticker,), guides=(('h', 20.0),))
        with tempfile.TemporaryDirectory() as folder:
            target = pathlib.Path(folder) / 'old.scc-template'
            legacy = dict(editor_model.project_document(project), version=4)
            target.write_text(json.dumps(legacy))
            reopened = editor_model.load_project(target)
            self.assertEqual(reopened, project)
            editor_model.save_project(target, reopened)
            self.assertEqual(json.loads(target.read_text())['version'], 5)
            for guides in (None, False, {"h": 20}, [["q", 20]], [["h"]]):
                with self.subTest(guides=guides):
                    target.write_text(json.dumps(dict(legacy, guides=guides)))
                    with self.assertRaisesRegex(ValueError, 'Invalid template guides'):
                        editor_model.load_project(target)
