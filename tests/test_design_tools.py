"""Tests for Milestone 3 design tools: named layers, cut-by-layer, guides, and slicing."""

from pathlib import Path
import tempfile
import unittest

from cut_proxies import actions, editor_model as model, job


class TestDesignTools(unittest.TestCase):
    def test_named_layers_and_cut_by_layer_in_send(self):
        cut_box = model.Shape("Rectangle", 20, 20, 60, 40, layer="Outer Cut")
        crease_line = model.Shape("Line", 50, 20, 0, 40, layer="Score Line", action="Score")
        perforation = model.Shape("Line", 20, 40, 60, 0, layer="Perforation")
        project = model.Project(279.4, 215.9, (cut_box, crease_line, perforation))

        # Check artwork() assigns the layer to CutPaths
        art = project.artwork(action=model.ALL_ACTIONS)
        self.assertEqual(len(art.paths), 3)

        layer_groups = actions.group_paths(art.paths, "layer")
        self.assertIn("Outer Cut", layer_groups)
        self.assertIn("Score Line", layer_groups)
        self.assertIn("Perforation", layer_groups)

        # Build checked job by layer
        assignments = (
            actions.Assignment("Outer Cut", "Plain paper test"),
            actions.Assignment("Score Line", "Plain paper test"),
            actions.Assignment("Perforation", "Plain paper test"),
        )
        plan = job.build(imported=art, send_mode="layer", assignments=assignments, registration=False)
        self.assertFalse(plan.blocked)
        self.assertEqual(len(plan.action_groups), 3)

    def test_layer_persists_through_save_and_reopen(self):
        shape = model.Shape("Rounded rectangle", 30, 30, 40, 30, layer="Kiss Cut")
        project = model.Project(279.4, 215.9, (shape,))

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "layers.scc-template"
            model.save_project(target, project)
            reopened = model.load_project(target)

        self.assertEqual(len(reopened.shapes), 1)
        self.assertEqual(reopened.shapes[0].layer, "Kiss Cut")

    def test_guides_persist_and_snap_move_respects_them(self):
        shape = model.Shape("Rectangle", 20, 20, 40, 30)
        project = model.Project(279.4, 215.9, (shape,), guides=(("h", 60.0), ("v", 100.0)))

        # Move shape near the vertical guide at x=100
        # shape is currently at x=20, width=40 (right edge at 60).
        # Move right by 38 mm -> right edge would be at 98 mm.
        # Tolerance is 3 mm -> should snap right edge exactly to guide at 100 mm (dx = 40 mm).
        dx, dy = model.snap_move((shape,), (0,), 38, 0, project.width, project.height, tolerance=3, guides=project.guides)
        self.assertEqual(dx, 40.0)
        self.assertEqual(dy, 0)

        # Move shape near the horizontal guide at y=60
        # shape top is at y=20. Move down by 38 mm -> top would be at 58 mm.
        # Should snap top edge to 60 mm (dy = 40 mm).
        dx, dy = model.snap_move((shape,), (0,), 0, 38, project.width, project.height, tolerance=3, guides=project.guides)
        self.assertEqual(dx, 0)
        self.assertEqual(dy, 40.0)

        # Save and reopen guides
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "guides.scc-template"
            model.save_project(target, project)
            reopened = model.load_project(target)

        self.assertEqual(reopened.guides, (("h", 60.0), ("v", 100.0)))

    def test_knife_slices_shape_into_independent_pieces(self):
        rect = model.Shape("Rectangle", 20, 20, 60, 40, stroke="#0000ff", layer="Border")
        # Slice vertically at x=40
        pieces = model.slice_shape(rect, (40, 10), (40, 70))
        self.assertEqual(len(pieces), 2)

        # Both pieces retain original properties (stroke, layer, action)
        self.assertEqual(pieces[0].stroke, "#0000ff")
        self.assertEqual(pieces[0].layer, "Border")
        self.assertEqual(pieces[1].stroke, "#0000ff")
        self.assertEqual(pieces[1].layer, "Border")

        # Bounds of the two pieces
        b0 = model.bounds((pieces[0],))
        b1 = model.bounds((pieces[1],))
        self.assertAlmostEqual(b0.w, 20.0, places=3)
        self.assertAlmostEqual(b1.w, 40.0, places=3)
        self.assertAlmostEqual(b0.h, 40.0, places=3)
        self.assertAlmostEqual(b1.h, 40.0, places=3)

        # Cut line that misses does not slice
        miss = model.slice_shape(rect, (100, 10), (100, 70))
        self.assertEqual(len(miss), 1)
        self.assertEqual(miss[0], rect)

    def test_knife_slices_shape_with_hole(self):
        outside = model.Shape("Rectangle", 20, 20, 60, 60)
        inside = model.Shape("Rectangle", 35, 35, 30, 30)
        compound, _ = model.modify_selection((outside, inside), (0, 1), "Make Compound Path")
        ring = compound[0]

        # Slice through the center
        halves = model.slice_shape(ring, (50, 10), (50, 90))
        self.assertEqual(len(halves), 2)
        for half in halves:
            self.assertTrue(half.closed)
            # Center of each half is solid, but the previous hole region is open
            self.assertFalse(model.hit(half, (50, 50), 0))
