"""Imports must preserve what an SVG renderer draws, including its corners."""
import unittest
from tests.test_importer import load_text, svg


class SvgFidelity(unittest.TestCase):
    def test_default_viewbox_meets_and_centers_without_stretching(self):
        art = load_text(svg('<rect x="10" y="10" width="50" height="50"/>', w='200mm'))
        self.assertEqual(art.bounds, (60, 10, 110, 60))

    def test_explicit_none_can_stretch(self):
        art = load_text(svg('<rect x="10" y="10" width="50" height="50"/>', w='200mm', extra='preserveAspectRatio="none"'))
        self.assertEqual(art.bounds, (20, 10, 120, 60))

    def test_elliptical_card_corner_uses_both_radii(self):
        path = load_text(svg('<rect x="10" y="10" width="63" height="88" rx="3" ry="8"/>')).paths[0]
        self.assertTrue(any(abs(x-73)<0.001 and abs(y-18)<0.001 for x,y in path.points))
        corner = [(x,y) for x,y in path.points if x>70 and y<18]
        self.assertGreater(len(corner), 2)
        for x,y in corner:
            self.assertAlmostEqual(((x-70)/3)**2 + ((y-18)/8)**2, 1, places=5)

    def test_slice_and_nested_viewport_are_refused(self):
        from cutline.importer import ImportError_
        for body, extra in [('<rect width="50" height="50"/>', 'preserveAspectRatio="xMidYMid slice"'),
                            ('<svg x="30" viewBox="0 0 10 10"><rect width="5" height="5"/></svg>', '')]:
            with self.subTest(body=body), self.assertRaises(ImportError_):
                load_text(svg(body, w='200mm', extra=extra))

    def test_group_scale_preserves_curve_tolerance_in_millimetres(self):
        import math
        from cutline.template import CHORD_TOLERANCE_MM
        path = load_text(svg('<g transform="scale(20)"><circle cx="2" cy="2" r="1"/></g>')).paths[0]
        points = list(path.points) + [path.points[0]]
        sag = max(20 - math.hypot((a[0]+b[0])/2-40, (a[1]+b[1])/2-40)
                  for a,b in zip(points,points[1:]))
        self.assertLessEqual(sag, CHORD_TOLERANCE_MM + 1e-8)

    def test_viewport_alignments_and_origin(self):
        cases = [('100mm','200mm','', (10,60,60,110)),
                 ('200mm','100mm','preserveAspectRatio="xMaxYMin meet"', (110,10,160,60)),
                 ('100mm','200mm','preserveAspectRatio="xMinYMax meet"', (10,110,60,160))]
        for w,h,extra,want in cases:
            with self.subTest(w=w,h=h,extra=extra):
                art=load_text(svg('<rect x="10" y="10" width="50" height="50"/>',w=w,h=h,extra=extra))
                self.assertEqual(art.bounds,want)
        art=load_text(svg('<rect x="20" y="30" width="50" height="50"/>',w='200mm',viewbox='10 20 100 100'))
        self.assertEqual(art.bounds,(60,10,110,60))
