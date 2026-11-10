"""Registration uses the paper coordinates, even for a one-card partial sheet."""
import dataclasses
import json
from pathlib import Path
import tempfile
import unittest
from cut_proxies import importer, job, layout_presets


class ImportedPage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        cases = json.loads((Path(__file__).parent / 'fixtures/proxypress-geometry.json').read_text())
        path = Path(self.tmp.name) / 'sheet.svg'
        path.write_text(cases[0]['svgs'][0])
        self.art = importer.load(path)
        self.options = layout_presets.BUILTIN['mtg-cameo-letter'].options()

    def test_matching_and_conflicting_pages(self):
        self.assertEqual(self.art.page_size_mm, (279.4, 215.9))
        self.assertFalse(job.build(**self.options, imported=self.art).blocked)
        wrong = dict(self.options, sheet='A4')
        built = job.build(**wrong, imported=self.art)
        self.assertTrue(any('SVG page' in e for e in built.blocked), built.blocked)
        portrait = dict(self.options, portrait=True)
        self.assertTrue(any('SVG page' in e for e in job.build(**portrait, imported=self.art).blocked))

    def test_tolerance_and_unspecified_page(self):
        for size in ((279.45, 215.95), None):
            art = dataclasses.replace(self.art, page_size_mm=size)
            self.assertFalse(job.build(**self.options, imported=art).blocked)
        art = dataclasses.replace(self.art, page_size_mm=(279.6, 215.9))
        self.assertTrue(any('SVG page' in e for e in job.build(**self.options, imported=art).blocked))

    def test_svg_without_viewport_does_not_claim_a_page(self):
        path = Path(self.tmp.name) / 'unitless.svg'
        path.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><rect x="10" y="10" width="20" height="30"/></svg>')
        self.assertIsNone(importer.load(path).page_size_mm)

    def test_unregistered_art_keeps_existing_behavior(self):
        art = dataclasses.replace(self.art, page_size_mm=(100, 100))
        self.assertFalse(job.build(**self.options, imported=art, registration=False).blocked)

    def test_dxf_extents_are_not_a_page(self):
        built = job.build(**self.options)
        paths = job.write_templates(built, 'sheet', self.tmp.name, proof=False)
        self.assertIsNone(importer.load(paths['dxf']).page_size_mm)

    def test_cli_refuses_before_opening_transport(self):
        from unittest.mock import patch
        from cut_proxies import cli
        with patch.object(cli.transport, 'open_transport') as transport, patch.object(cli, 'say'):
            with self.assertRaises(SystemExit):
                cli.main(['cut', '--from', str(Path(self.tmp.name) / 'sheet.svg'),
                          '--sheet', 'A4', '--landscape', '--mark-length', '7.25'])
            transport.assert_not_called()
