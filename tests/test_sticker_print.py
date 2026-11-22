from dataclasses import replace
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from cutline import bundle, editor_model, job, regmarks, sheet, sticker_print, sticker_trace


def make_image(path):
    from PIL import Image, ImageDraw

    image = Image.new('RGBA', (140, 100))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((10, 12, 101, 88), radius=10, fill='#a52664')
    draw.ellipse((36, 30, 70, 65), fill=(0, 0, 0, 0))
    draw.rectangle((115, 46, 135, 62), fill='#134ea2')
    image.save(path)


def trace_fixture(path, keep_holes=True, bleed=1.5):
    shape = sticker_trace.place(path, 215.9, 279.4)
    shape = replace(shape, x=50, y=72, width=70, height=50)
    return sticker_trace.trace(shape, source='Automatic', threshold=128, keep_holes=keep_holes, bleed_mm=bleed)


class TestStickerPrint(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.folder = Path(directory.name)
        self.image = self.folder / 'art.png'
        make_image(self.image)

    def test_trace_separates_components_and_optionally_keeps_holes(self):
        complete = trace_fixture(self.image)
        outlines = trace_fixture(self.image, keep_holes=False)
        self.assertEqual(len(complete.paths()), 3)
        self.assertEqual(len(outlines.paths()), 2)
        self.assertEqual(complete.frame().points, outlines.frame().points)
        path = self.folder / 'portable.scc-template'
        project = editor_model.Project(215.9, 279.4, (complete,))
        editor_model.save_project(path, project)
        self.image.unlink()
        self.assertEqual(editor_model.load_project(path), project)
        self.assertEqual(editor_model.load_project(path).shapes[0].sticker.image().size, (140, 100))

    def test_trace_opaque_light_and_dark_sources_and_reports_no_match(self):
        from PIL import Image, ImageDraw

        im = Image.new('RGB', (24, 24), '#eeeeee')
        ImageDraw.Draw(im).rectangle((8, 8, 15, 15), fill='#111111')
        im.save(self.image)
        source = sticker_trace.place(self.image, 215.9, 279.4)
        dark = sticker_trace.trace(source, source='Dark artwork', threshold=128, keep_holes=True, bleed_mm=0)
        light = sticker_trace.trace(source, source='Light artwork', threshold=128, keep_holes=True, bleed_mm=0)
        self.assertEqual(len(dark.paths()), 1)
        self.assertEqual(len(light.paths()), 2)
        with self.assertRaisesRegex(ValueError, 'No artwork matches'):
            sticker_trace.trace(source, source='Dark artwork', threshold=253, keep_holes=False, bleed_mm=0)

    @unittest.skipUnless(shutil.which('pdftoppm') and shutil.which('pdfinfo'), 'requires Poppler')
    def test_pdf_size_registration_bleed_and_cut_paths_round_trip(self):
        sticker = trace_fixture(self.image)
        project = editor_model.Project(215.9, 279.4, (sticker,))
        archive = sticker_print.export(self.folder / 'stickers.zip', project, cutter='Silhouette Cameo 4')
        matched = bundle.load(archive)
        row = matched.row()
        self.assertEqual(matched.mat_size, (215.9, 279.4))
        pdf = matched.save_print_pdf(self.folder / 'print.pdf')
        description = subprocess.check_output(['pdfinfo', str(pdf)], text=True)
        self.assertRegex(description, r'Page size:\s+612 x 792 pts')
        report, image = matched.measure(row, matched.marks(), 300)
        self.assertFalse(report.problems(215.9, 279.4), report.diagnosis())
        self.assertEqual(image.size, (2550, 3300))

        def rgb(x, y):
            return image.getpixel((round(x * 300 / 25.4), round(y * 300 / 25.4)))

        # The left trim edge is x=55. Printed bleed extends outside it.
        self.assertLess(rgb(54.4, 96)[1], 75)
        self.assertEqual(rgb(52.5, 96), (255, 255, 255))
        self.assertEqual(rgb(77, 95), (255, 255, 255))  # hole stays clear
        art = matched.artwork(row)
        for expected, actual in zip(project.artwork().paths, art.paths, strict=True):
            self.assertTrue(actual.closed)
            for a, b in zip(expected.points, actual.points, strict=True):
                for first, second in zip(a, b):
                    self.assertAlmostEqual(first, second, delta=0.0001)
        plan = job.build(imported=art, offset_mm=(0, 0), **matched.layout)
        self.assertFalse(plan.blocked)
        self.assertFalse(plan.liveness_failures)

    def test_export_refuses_to_overwrite_archive_if_print_covers_a_mark(self):
        active = trace_fixture(self.image)
        print_only = replace(active, x=7, y=7, width=28, height=22, contours=(), action='None')
        project = editor_model.Project(215.9, 279.4, (active, print_only))
        target = self.folder / 'keep.zip'
        target.write_bytes(b'previous export')
        with self.assertRaisesRegex(ValueError, 'registration marks'):
            sticker_print.export(target, project, cutter='Silhouette Cameo 4')
        self.assertEqual(target.read_bytes(), b'previous export')
        sane = replace(project, shapes=(active, replace(print_only, x=100, y=170)))
        self.assertIsInstance(sticker_print.export(target, sane, cutter='Silhouette Cameo 4'), Path)
        matched = bundle.load(target)
        self.assertEqual(len(matched.artwork(matched.row()).paths), 3)

    @unittest.skipUnless(shutil.which('pdftoppm') and shutil.which('pdfinfo'), 'requires Poppler')
    def test_scm_preview_rotates_a_sideways_pdf_to_the_registered_page(self):
        shape = trace_fixture(self.image)
        project = editor_model.Project(215.9, 279.4, (shape,))
        archive = sticker_print.export(self.folder / 'page.zip', project, cutter='Silhouette Cameo 4')
        matched = bundle.load(archive)
        marks = matched.marks()
        _, portrait = matched.measure(matched.row(), marks, 150)
        sideways = self.folder / 'sideways.pdf'
        portrait.rotate(90, expand=True).save(sideways, 'PDF', resolution=150)
        original, _ = sheet.measure_preview(sideways, marks, dpi=150)
        self.assertTrue(original.problems(215.9, 279.4))
        turned, preview = sheet.measure_preview(sideways, marks, dpi=150, orient_scm=True)
        self.assertEqual(turned.preview_rotation, 270)
        self.assertEqual(preview.size, portrait.size)
        self.assertFalse(turned.problems(215.9, 279.4), turned.diagnosis())

    def test_print_area_checks_actual_ink_and_rejects_off_sheet(self):
        marks = regmarks.marks(215.9, 279.4)
        shape = trace_fixture(self.image, bleed=0)
        safe = sticker_print.stamp(replace(shape, x=-2, y=50))
        self.assertIsNone(sticker_print.check_print_area('art.png', safe, marks))
        outside = sticker_print.stamp(replace(shape, x=-8, y=50))
        with self.assertRaisesRegex(ValueError, 'off the page'):
            sticker_print.check_print_area('art.png', outside, marks)
