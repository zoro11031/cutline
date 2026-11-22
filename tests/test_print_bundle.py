import contextlib
import io
import json
import os
import pathlib
import tempfile
import unittest
from unittest import mock
import zipfile

from cutline import bundle, cli, graphtec, job, sheet


class BundleRules(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = pathlib.Path(tmp.name) / 'sample.zip'
        self.manifest = dict(format=bundle.CARD_FORMAT, version=1, units='mm',
                             origin='top left', pdf='cards.pdf',
                             pdfPage=dict(width=279.4, height=215.9, orientation='landscape'),
                             mat=dict(width=279.4, height=215.9, orientation='landscape'),
                             registration=dict(pattern=3, inset=10, armLength=8.0264, thickness=1),
                             sheets=[dict(sheet=2, frontPage=1, backPage=2, cards=1, svg='sheet-2.svg')])

    def write(self, svg_size=(279.4, 215.9)):
        w, h = svg_size
        with zipfile.ZipFile(self.path, 'w') as z:
            z.writestr('registration.json', json.dumps(self.manifest))
            z.writestr('cards.pdf', b'%PDF-1.4')
            z.writestr('sheet-2.svg', '<svg xmlns="http://www.w3.org/2000/svg" '
                       f'width="{w}mm" height="{h}mm" viewBox="0 0 {w} {h}">'
                       '<rect x="20" y="22" width="63" height="88"/></svg>')

    def test_uses_project_sheet_number_and_actual_pdf_page(self):
        self.write()
        loaded = bundle.load(self.path)
        self.assertEqual(loaded.row()['sheet'], 2)
        self.assertEqual(loaded.row(2)['frontPage'], 1)
        self.assertEqual(loaded.artwork(loaded.row()).page_size_mm, (279.4, 215.9))
        self.assertEqual(loaded.layout['mark_length_mm'], 8.0264)
        with self.assertRaisesRegex(bundle.BundleError, 'Choose --bundle-sheet'):
            loaded.row(1)

    def test_refuses_mismatched_cut_count_and_page_mapping(self):
        self.manifest['sheets'][0]['cards'] = 2
        self.write()
        loaded = bundle.load(self.path)
        with self.assertRaisesRegex(bundle.BundleError, 'SVG does not match'):
            loaded.artwork(loaded.row())
        self.manifest['sheets'][0]['frontPage'] = 2
        self.write()
        with self.assertRaisesRegex(bundle.BundleError, 'PDF page'):
            bundle.load(self.path)

    def test_pdf_page_count_must_match_the_full_bundle_mapping(self):
        image = mock.Mock(size=(2200, 1700))
        for back_page, page_count in ((None, 1), (2, 2), (2, 1), (2, 3)):
            with self.subTest(back_page=back_page, page_count=page_count):
                self.manifest['sheets'][0]['backPage'] = back_page
                self.write()
                matched = bundle.load(self.path)
                with mock.patch.object(sheet, 'render', return_value=image), \
                     mock.patch.object(sheet, 'page_size_mm',
                                       return_value=(279.4, 215.9, page_count)), \
                     mock.patch.object(sheet, 'ink_blobs', return_value=[]):
                    if page_count == (back_page or 1):
                        report, _ = matched.measure(matched.row(), matched.marks(), 200)
                        self.assertEqual(report.page, 1)
                    else:
                        with self.assertRaisesRegex(bundle.BundleError,
                                                    rf'contains {page_count} page\(s\).*maps {back_page}'):
                            matched.measure(matched.row(), matched.marks(), 200)

    def test_scm_letter_bundle_keeps_captured_cameo4_registration_with_track_selected(self):
        # A standard Letter card bundle uses the SCM 8.04 mm arm.
        self.manifest['registration']['armLength'] = 8.04
        self.write()
        matched = bundle.load(self.path)
        options = dict(**matched.layout, imported=matched.artwork(matched.row()),
                       cutter='Silhouette Cameo 4', offset_mm=(0, 0))
        selected = job.build(**options, blade_options={'track_enhancing': True})
        off = job.build(**options, blade_options={'track_enhancing': False})
        self.assertFalse(selected.blocked)
        self.assertTrue(selected.settings.track_enhancing)
        roller, = [step for step in selected.steps if step.data == graphtec.frame('FY0')]
        self.assertTrue(roller.wait_ready)
        self.assertEqual(selected.steps[selected.steps.index(roller) + 1:], off.steps)
        self.assertNotIn(graphtec.frame('FY0'), off.stream)
        scan = next(step for step in selected.steps if step.note.startswith('scan for marks'))
        self.assertIn(b'TB123,3918,5188,118,118\x03', scan.data)
        reply = next(i for i, step in enumerate(selected.steps) if step.kind == 'expect')
        self.assertEqual(selected.steps[reply + 1].note, 'registered cut window')

    def test_uses_the_exact_scm_a4_frame_and_rejects_unmatched_svg(self):
        size = 3507 * 25.4 / 300, 2480 * 25.4 / 300
        for section in ['pdfPage', 'mat']:
            self.manifest[section].update(width=size[0], height=size[1])
        self.write()
        matched = bundle.load(self.path)
        self.assertEqual(matched.layout['sheet'], 'A4')
        with self.assertRaisesRegex(bundle.BundleError, 'SVG does not match'):
            matched.artwork(matched.row())
        self.write(size)
        matched = bundle.load(self.path)
        plan = job.build(**matched.layout, imported=matched.artwork(matched.row()))
        self.assertFalse(plan.blocked)
        for marks in [matched.marks(), plan.marks]:
            self.assertAlmostEqual(marks.sheet_w_mm, size[0])
            self.assertAlmostEqual(marks.sheet_h_mm, size[1])

    def test_unversioned_studio_zip_still_loads_its_pdf_and_studio_template(self):
        del self.manifest['format']
        del self.manifest['version']
        row = self.manifest['sheets'][0]
        del row['svg']
        row['cutFile'] = 'sheet-2.studio3'
        self.write()
        with zipfile.ZipFile(self.path, 'a') as z:
            z.writestr(row['cutFile'], b'silhouette05;fixture')
        loaded = bundle.load(self.path)
        self.assertEqual(loaded.marks().length_mm, 8.0264)
        self.assertEqual(loaded.save_cut_file(loaded.row(), self.path.parent / row['cutFile']).name, row['cutFile'])
        with self.assertRaisesRegex(bundle.BundleError, 'direct cutting'):
            loaded.artwork(loaded.row())

    def test_paired_studio_file_is_copied_byte_for_byte(self):
        row = self.manifest['sheets'][0]
        row['cutFile'] = 'sheet-2.studio3'
        self.write()
        payload = b'silhouette05;fixture'
        with zipfile.ZipFile(self.path, 'a') as z:
            z.writestr(row['cutFile'], payload)
        loaded = bundle.load(self.path)
        staged = loaded.save_cut_file(loaded.row(2), self.path.parent / row['cutFile'])
        self.assertEqual(staged.name, row['cutFile'])
        self.assertEqual(staged.read_bytes(), payload)
        with self.assertRaisesRegex(bundle.BundleError, 'no matching cut file'):
            loaded.save_cut_file(dict(row, cutFile='sheet-1.studio3'), self.path.parent / 'test.studio3')

    def test_paired_svg_can_be_saved_without_overwriting_the_bundle(self):
        self.write()
        loaded = bundle.load(self.path)
        row = loaded.row()
        target = loaded.save_cut_file(row, self.path.parent / 'sheet-2.svg')
        with zipfile.ZipFile(self.path) as archive:
            self.assertEqual(target.read_bytes(), archive.read(row['svg']))
        with self.assertRaisesRegex(bundle.BundleError, 'separate from the source ZIP'):
            loaded.save_cut_file(row, self.path)
        self.assertTrue(zipfile.is_zipfile(self.path))

    def test_cut_refuses_a_failed_pdf_check_and_accepts_a_matching_one(self):
        self.write()
        with mock.patch.object(bundle.Bundle, 'measure') as measure, \
             contextlib.redirect_stderr(io.StringIO()):
            report = mock.Mock()
            measure.return_value = report, None
            report.problems.return_value = ['wrong mark']
            with self.assertRaises(SystemExit):
                cli.main(['cut', '--from', str(self.path), '--dry-run'])
            report.problems.return_value = []
            with mock.patch.object(cli, 'say'):
                self.assertEqual(cli.main(['cut', '--from', str(self.path), '--dry-run']), 0)


@unittest.skipUnless(os.environ.get('CUTLINE_CARD_BUNDLE_DIR'), 'generate fresh card print bundles first')
class ExportedBundles(unittest.TestCase):
    def test_actual_svg_pdf_and_cut_stream_agree(self):
        archives = sorted(pathlib.Path(os.environ['CUTLINE_CARD_BUNDLE_DIR']).glob('*.zip'))
        self.assertGreaterEqual(len(archives), 8)
        for file in archives:
            matched = bundle.load(file)
            for row in matched.rows:
                with self.subTest(file=file.name, sheet=row['sheet']):
                    cutter = 'Silhouette Cameo 4 Pro' if matched.mat_size[0] > 304.8 else 'Silhouette Cameo 4'
                    j = job.build(**matched.layout, imported=matched.artwork(row), offset_mm=(0, 0),
                                  cutter=cutter)
                    self.assertFalse(j.blocked)
                    self.assertEqual(len(j.paths), row['cards'])
                    report, _ = matched.measure(row, j.marks, sheet.DEFAULT_DPI)
                    self.assertEqual(report.page, row['frontPage'])
                    self.assertFalse(report.problems(j.grid.sheet_w_mm, j.grid.sheet_h_mm))
