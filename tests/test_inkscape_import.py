from pathlib import Path
import tempfile
import unittest

from cut_proxies import graphtec, importer, job


class TestInkscapeImport(unittest.TestCase):
    def load_sheet(self, right=264.4, extra_pages=""):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "design.svg"
            # Same element IDs, winding, dimensions and group structure as
            # inkscape-silhouette's single-page standard mark renderer.
            path.write_text(f"""<svg xmlns="http://www.w3.org/2000/svg"
                xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape"
                width="279.4mm" height="215.9mm" viewBox="0 0 279.4 215.9">
              {extra_pages}
              <defs><rect width="100" height="100"/></defs>
              <g inkscape:label="Print"><rect width="100" height="100"/></g>
              <g inkscape:label="CuttingMat"><path d="M 0,0 L 300,0"/></g>
              <g inkscape:label="Regmarks" id="regmark">
                <rect id="regmark-tl" x="15" y="15" width="5" height="5"/>
                <path id="regmark-tr" d="M {right-20},15 {right},15 {right},35"/>
                <path id="regmark-bl" d="M 35,200.9 15,200.9 15,180.9"/>
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

    def test_bad_registration_blocks_registered_job(self):
        design = self.load_sheet(right=260)
        self.assertTrue(design.inkscape_mark_error)
        checked = job.build(imported=design, offset_mm=(0, 0))
        self.assertIn(design.inkscape_mark_error, checked.blocked)
        self.assertEqual(job.build(imported=design, registration=False,
                                   offset_mm=(0, 0)).blocked, [])

    def test_four_marks_require_a_four_mark_cutter(self):
        design = self.load_sheet()
        design.inkscape_marks = importer.InkscapeMarks(15, "four")
        cameo4 = graphtec.device_by_name("Silhouette Cameo 4")
        alpha = graphtec.device_by_name("Silhouette Cameo 5 Alpha")
        self.assertEqual(len(job.inkscape_registration_errors(design, cameo4)), 1)
        self.assertEqual(job.inkscape_registration_errors(design, alpha), [])

    def test_multi_page_requires_exporting_pages_separately(self):
        with self.assertRaisesRegex(importer.ImportError_, "separate SVG"):
            self.load_sheet(extra_pages="<inkscape:page/><inkscape:page/>")
