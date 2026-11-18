import unittest

from cut_proxies import corner_test, graphtec, importer, job, regmarks, template, verify
from cut_proxies.geometry import Rect


def rectangle(x, y, width, height):
    return template.CutPath(((x, y), (x + width, y), (x + width, y + height), (x, y + height)), closed=True)


class TestCornerTest(unittest.TestCase):
    def test_letter_and_a4_use_one_verified_three_mm_square_and_keep_registration(self):
        for name in ("Letter", "A4"):
            with self.subTest(name=name):
                active = job.build(sheet=name, passes=4)
                sample = corner_test.build(active)
                checked = sample.job
                self.assertEqual(checked.blocked, [])
                self.assertEqual((sample.rect.w, sample.rect.h), (3, 3))
                self.assertEqual(checked.settings.passes, 1)
                self.assertEqual(checked.marks, active.marks)
                self.assertEqual(checked.registration_steps(), active.registration_steps())
                self.assertEqual(len(verify.decode(checked.stream).paths), 1)
                self.assertEqual(checked.steps[-1].data,
                                 graphtec.frame(graphtec.return_to_start_commands(active.device)))
                self.assertFalse(any(sample.rect.overlaps(box.grown(1)) for box in (
                    *active.grid.cards, *active.marks.corner_zones())))

    def test_unregistered_corner_keeps_clear_when_offset_is_applied(self):
        active = job.build(registration=False, offset_mm=(2, -1))
        sample = corner_test.build(active, bleed_mm=0.6)
        rect = sample.rect
        swept = Rect(rect.x, rect.y - 1, rect.w + 2, rect.h + 1)
        self.assertFalse(sample.job.registration_steps())
        self.assertLessEqual(swept.x2, active.grid.sheet_w_mm)
        self.assertFalse(any(swept.overlaps(box.grown(1.6)) for box in active.grid.cards))

    def test_unselected_artwork_is_an_obstacle_and_a_full_page_is_refused(self):
        active = job.build()
        width, height = active.grid.sheet_w_mm, active.grid.sheet_h_mm
        usual = corner_test.build(active)
        other = importer.Imported((rectangle(width - 25, height - 25, 25, 25),),
                                  width, height, "Complete artwork", "mm")
        shifted = corner_test.build(active, artwork=other)
        self.assertNotEqual(usual.rect, shifted.rect)
        full = importer.Imported((rectangle(0, 0, width, height),), width, height, "Complete artwork", "mm")
        with self.assertRaisesRegex(ValueError, "No clear corner"):
            corner_test.build(active, artwork=full)

    def test_bad_layout_is_refused_and_four_mark_jobs_keep_the_same_window(self):
        with self.assertRaisesRegex(ValueError, "Review the current layout"):
            corner_test.build(None)
        active = job.build(cutter="Silhouette Cameo 5 Alpha", pattern=regmarks.FOUR, sheet="A4")
        self.assertEqual(active.blocked, [])
        sample = corner_test.build(active)
        self.assertEqual(sample.job.marks, active.marks)
        self.assertFalse(any(sample.rect.overlaps(zone.grown(1)) for zone in active.marks.corner_zones()))
