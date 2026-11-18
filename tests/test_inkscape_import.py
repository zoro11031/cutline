from io import StringIO
from pathlib import Path
import tempfile
import unittest

from cut_proxies import graphtec, importer, job


class TestInkscapeImport(unittest.TestCase):
    def load_sheet(self, right=264.4, top=15, right_top=None, bottom=200.9, mark_style=""):
        right_top = top if right_top is None else right_top
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "design.svg"
            # Same element IDs, winding, dimensions and group structure as
            # inkscape-silhouette's single-page standard mark renderer.
            path.write_text(f"""<svg xmlns="http://www.w3.org/2000/svg"
                xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape"
                width="279.4mm" height="215.9mm" viewBox="0 0 279.4 215.9">
              <defs><rect width="100" height="100"/></defs>
              <g inkscape:label="Print"><rect width="100" height="100"/></g>
              <g inkscape:label="CuttingMat"><path d="M 0,0 L 300,0"/></g>
              <g inkscape:label="Regmarks" id="regmark" style="{mark_style}">
                <rect id="regmark-tl" x="15" y="{top}" width="5" height="5"/>
                <path id="regmark-tr" d="M {right-20},{right_top} {right},{right_top} {right},{right_top+20}"/>
                <path id="regmark-bl" d="M 35,{bottom} 15,{bottom} 15,{bottom-20}"/>
                <path id="regmark-safe-area" d="M 0,0 L 279,0 L 279,215 Z"/>
                <text id="regmark-notes">Left=1mm</text>
              </g>
              <g inkscape:label="Cut" transform="translate(50,45)">
                <rect width="63" height="88" stroke="red"/>
                <rect x="70" width="63" height="88" style="display:none"/>
              </g>
            </svg>""")
            return importer.load(path)

    def test_excludes_non_cut_layers_and_uses_measured_marks(self):
        design = self.load_sheet()
        self.assertEqual(len(design.paths), 1)
        self.assertEqual(design.only("red").paths, design.paths)
        self.assertEqual(design.bounds, (50, 45, 113, 133))
        checked = job.build(imported=design, offset_mm=(0, 0))
        self.assertEqual(checked.blocked, [])
        self.assertEqual(checked.marks.origin_mm, (15, 15))
        self.assertEqual(checked.marks.length_mm, 20)
        self.assertEqual(checked.marks.thickness_mm, 0.3)
        self.assertAlmostEqual(checked.marks.reg_width_mm, 249.4)

    def test_custom_origin_and_spacing_are_used_in_the_scan_and_paths(self):
        design = self.load_sheet(top=19, right=260, bottom=188)
        checked = job.build(imported=design, offset_mm=(0, 0))
        self.assertEqual(checked.blocked, [])
        self.assertEqual(checked.marks.origin_mm, (15, 19))
        self.assertEqual(checked.marks.reg_width_mm, 245)
        self.assertEqual(checked.marks.reg_length_mm, 169)
        self.assertIn(b"TB123,3380,4900,180,100\x03", checked.stream)
        self.assertIn(graphtec.frame(graphtec.move_mm(35, 26)), checked.stream)

    def test_bad_registration_blocks_registered_job(self):
        design = self.load_sheet(right_top=20)
        self.assertTrue(design.inkscape_mark_error)
        checked = job.build(imported=design, offset_mm=(0, 0))
        self.assertIn(design.inkscape_mark_error, checked.blocked)
        self.assertEqual(job.build(imported=design, registration=False,
                                   offset_mm=(0, 0)).blocked, [])

    def test_hidden_registration_layer_blocks_a_registered_cut(self):
        for style in ("display:none", "opacity:0", "visibility:hidden"):
            with self.subTest(style=style):
                design = self.load_sheet(mark_style=style)
                self.assertEqual(len(design.paths), 1)
                self.assertTrue(design.inkscape_mark_error)
                checked = job.build(imported=design, offset_mm=(0, 0))
                self.assertIn(design.inkscape_mark_error, checked.blocked)

    def test_hidden_geometry_and_visible_descendants(self):
        svg = """<svg xmlns="http://www.w3.org/2000/svg"
                width="100mm" height="100mm" viewBox="0 0 100 100">
          <g visibility="hidden">
            <rect x="10" y="10" width="3" height="3"/>
            <rect x="20" y="20" width="3" height="3" style="visibility: visible"/>
          </g>
          <g style="opacity:0% !important">
            <rect x="30" y="30" width="3" height="3" opacity="1"/>
          </g>
          <rect x="40" y="40" width="3" height="3" visibility="collapse"/>
          <rect x="50" y="50" width="3" height="3" display="none" stroke="blue" style="display:inline;stroke:red"/>
        </svg>"""
        design = importer.load_svg(StringIO(svg))
        self.assertEqual(tuple(p.points[0] for p in design.paths), ((20, 20), (50, 50)))
        self.assertEqual(design.only("red").paths, (design.paths[1],))

    def test_four_marks_require_a_four_mark_cutter(self):
        design = self.load_sheet()
        design.inkscape_marks = importer.InkscapeMarks(15, "four")
        cameo4 = graphtec.device_by_name("Silhouette Cameo 4")
        alpha = graphtec.device_by_name("Silhouette Cameo 5 Alpha")
        self.assertEqual(len(job.inkscape_registration_errors(design, cameo4)), 1)
        self.assertEqual(job.inkscape_registration_errors(design, alpha), [])

    def multi_page_svg(self, extra=""):
        return f"""<svg xmlns="http://www.w3.org/2000/svg"
                  xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape"
                  xmlns:sodipodi="http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd"
                  width="279.4mm" height="215.9mm" viewBox="0 0 279.4 215.9">
          <sodipodi:namedview>
            <inkscape:page x="0" y="0" width="279.4" height="215.9"/>
            <inkscape:page x="310" y="-50" width="279.4" height="215.9"/>
          </sodipodi:namedview>
          <g id="regmark">
            <g id="regmark-page-1">
              <rect id="regmark-tl-page-1" x="10" y="10" width="5" height="5"/>
              <path id="regmark-tr-page-1" d="M249.4,10 H269.4 V30"/>
              <path id="regmark-bl-page-1" d="M30,205.9 H10 V185.9"/>
            </g>
            <g id="regmark-page-2" transform="translate(310,-50)">
              <rect id="regmark-tl-page-2" x="15" y="19" width="5" height="5"/>
              <path id="regmark-tr-page-2" d="M240,19 H260 V39"/>
              <path id="regmark-bl-page-2" d="M35,188 H15 V168"/>
            </g>
          </g>
          <g transform="translate(310,-50)">
            <rect x="50" y="45" width="63" height="88"/>
          </g>
          <rect x="70" y="30" width="63" height="88"/>
          {extra}
        </svg>"""

    def test_multi_page_requires_a_valid_page_selection(self):
        with self.assertRaisesRegex(importer.InkscapePageRequired, "--svg-page"):
            importer.load_svg(StringIO(self.multi_page_svg()))
        with self.assertRaisesRegex(importer.ImportError_, "page 3 does not exist"):
            importer.load_svg(StringIO(self.multi_page_svg()), page=3)

    def test_each_page_uses_only_its_paths_and_marks_in_local_coordinates(self):
        for page, expected_bounds, expected_origin, scan in (
                (1, (70, 30, 133, 118), (10, 10), b"TB123,3918,5188,0,0"),
                (2, (50, 45, 113, 133), (15, 19), b"TB123,3380,4900,180,100")):
            with self.subTest(page=page):
                design = importer.load_svg(StringIO(self.multi_page_svg()), page=page)
                self.assertEqual(design.bounds, expected_bounds)
                self.assertEqual(len(design.paths), 1)
                checked = job.build(imported=design, offset_mm=(0, 0))
                self.assertEqual(checked.blocked, [])
                self.assertEqual(checked.marks.origin_mm, expected_origin)
                self.assertIn(scan, checked.stream)

    def test_crossing_path_is_refused_for_that_page_only(self):
        svg = self.multi_page_svg(extra='<path d="M300,0 L330,0"/>')
        self.assertEqual(len(importer.load_svg(StringIO(svg), page=1).paths), 1)
        with self.assertRaisesRegex(importer.ImportError_, "crosses the edge of Inkscape page 2"):
            importer.load_svg(StringIO(svg), page=2)

    def test_four_corner_template_generates_the_quad_scan(self):
        svg = """<svg xmlns="http://www.w3.org/2000/svg"
                width="210mm" height="297mm" viewBox="0 0 210 297">
          <g id="regmark">
            <path id="regmark-tl" d="M30,10 H10 V30"/>
            <path id="regmark-tr" d="M180,10 H200 V30"/>
            <path id="regmark-bl" d="M30,287 H10 V267"/>
            <path id="regmark-br" d="M180,287 H200 V267"/>
          </g>
          <rect x="60" y="80" width="63" height="88"/>
        </svg>"""
        design = importer.load_svg(StringIO(svg))
        checked = job.build(imported=design, sheet="A4", portrait=True,
                            cutter="Silhouette Cameo 5 Alpha", offset_mm=(0, 0))
        self.assertEqual(checked.blocked, [])
        self.assertEqual(len(design.paths), 1)
        self.assertIn(b"TB124,5540,3800,0,0", checked.stream)
