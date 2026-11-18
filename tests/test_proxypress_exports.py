"""Actual Proxy Press SVG exports, captured 2026-11-10 with tools/export-interop.js.

Set PROXYPRESS_INTEROP_JSON to a fresh export to test another checkout.
The default fixtures require no sibling repository, browser or network.
"""
import base64
import json
import os
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET
from cut_proxies import geometry, importer, job, layout_presets


class ProxyPressExports(unittest.TestCase):
    def test_exported_sheets_import_and_verify(self):
        source = Path(os.environ.get('PROXYPRESS_INTEROP_JSON',
                      Path(__file__).parent / 'fixtures/proxypress-geometry.json'))
        cases = json.loads(source.read_text())
        self.assertGreaterEqual(len(cases), 8)
        with tempfile.TemporaryDirectory() as tmp:
            for case in cases:
                for page, svg in enumerate(case['svgs']):
                    with self.subTest(paper=case['paper'], count=case['count'], page=page,
                                      calibrated=case.get('calibrated')):
                        path = Path(tmp) / 'cuts.svg'
                        path.write_text(svg)
                        art = importer.load(path)
                        portrait = case.get('orientation') == 'portrait'
                        cols, rows = (3, 3) if portrait else (4, 2)
                        per = cols * rows
                        self.assertEqual(len(art.paths), min(per, case['count'] - page * per))
                        options = layout_presets.BUILTIN['mtg-cameo-' + case['paper']].options()
                        marked = case.get('marks', {}).get('rects', [])
                        if portrait:
                            options.update(portrait=True, engine='scm',
                                           mark_inset_mm=marked[0][0], bleed_mm=0)
                        if case.get('arm') is not None:
                            options['mark_length_mm'] = case['arm']
                        reference = job.build(**options, offset_mm=(0, 0))
                        if case.get('calibrated'):
                            rect = ET.fromstring(svg).find('{http://www.w3.org/2000/svg}rect')
                            self.assertAlmostEqual(float(rect.get('rx')), 3.168, places=3)
                            self.assertAlmostEqual(float(rect.get('ry')), 3.232, places=3)
                            rx, ry = float(rect.get('rx')), float(rect.get('ry'))
                            cx = float(rect.get('x')) + float(rect.get('width')) - rx
                            cy = float(rect.get('y')) + ry
                            corner = [(x,y) for x,y in art.paths[0].points if x > cx and y < cy]
                            self.assertGreater(len(corner), 2)
                            for x,y in corner:
                                self.assertAlmostEqual(((x-cx)/rx)**2 + ((y-cy)/ry)**2, 1, places=5)
                        else:
                            self.assertEqual(case['arm'], reference.marks.length_mm)
                        for i, cut in enumerate(art.paths):
                            xs, ys = zip(*cut.points)
                            width, height = geometry.SHEETS[options['sheet']].size(portrait)
                            w, h = 63, 88
                            bleed = 0.5 if portrait else 0.625
                            x = (width - cols*(w+2*bleed))/2 + (i % cols)*(w+2*bleed) + bleed
                            y = (height - rows*(h+2*bleed))/2 + (i // cols)*(h+2*bleed) + bleed
                            if case.get('calibrated'):
                                x = 297/2 + (x-297/2)*0.99 + 2
                                y = 210/2 + (y-210/2)*1.01 - 3
                                w *= 0.99; h *= 1.01
                            for got, want in zip((min(xs), min(ys), max(xs)-min(xs), max(ys)-min(ys)), (x,y,w,h)):
                                self.assertAlmostEqual(got, want, delta=0.002)
                        if case.get('pdf'):
                            from cut_proxies import sheet
                            pdf = Path(tmp) / 'cards.pdf'
                            pdf.write_bytes(base64.b64decode(case['pdf'], validate=True))
                            report = sheet.measure(pdf, reference.marks, page=page * 2 + 1)
                            self.assertFalse(report.problems(reference.grid.sheet_w_mm,
                                                             reference.grid.sheet_h_mm, 0.5))
                        # Actual imports take the ordinary verified job path.
                        if not case.get('calibrated'):
                            for offset in ((0, 0), (0.2, -0.1)):
                                built = job.build(**options, imported=art, offset_mm=offset)
                                self.assertFalse(built.blocked)
