import os
from pathlib import Path
import unittest
from unittest import mock

from cutline import cli, config, importer, job, layout_presets


class TestCardSheetPreset(unittest.TestCase):
    def test_letter_matches_centred_card_sheet_coordinates_and_auto_arm(self):
        options = layout_presets.BUILTIN['mtg-cameo-letter'].options()
        j = job.build(**options, offset_mm=(0, 0))
        self.assertEqual((j.grid.cols, j.grid.rows), (4, 2))
        self.assertEqual((j.grid.sheet_w_mm, j.grid.sheet_h_mm), (279.4, 215.9))
        self.assertAlmostEqual(j.grid.cards[0].x, 11.825)
        self.assertAlmostEqual(j.grid.cards[0].y, 19.325)
        self.assertAlmostEqual(j.grid.cards[-1].x2, 267.575)
        self.assertAlmostEqual(j.grid.cards[-1].y2, 196.575)
        self.assertEqual(j.marks.length_mm, 8.0264)
        self.assertEqual(j.grid.card.radius_mm, 3.2)
        self.assertFalse(j.blocked)

    def test_a4_matches_centred_card_sheet_coordinates_and_auto_arm(self):
        j = job.build(**layout_presets.BUILTIN['mtg-cameo-a4'].options(), offset_mm=(0, 0))
        self.assertEqual((j.grid.sheet_w_mm, j.grid.sheet_h_mm), (297, 210))
        self.assertAlmostEqual(j.grid.cards[0].x, 20.625)
        self.assertAlmostEqual(j.grid.cards[0].y, 16.375)
        self.assertEqual(j.marks.length_mm, 9.398)
        self.assertEqual(j.marks.pattern, 'three')
        self.assertEqual(j.marks.thickness_mm, 1.0)
        self.assertFalse(j.blocked)

    def test_stock_landscape_defaults_share_the_printed_arm(self):
        for preset in layout_presets.BUILTIN.values():
            for engine in ("scm", "centered"):
                with self.subTest(sheet=preset.sheet, engine=engine):
                    checked = job.build(sheet=preset.sheet, engine=engine)
                    self.assertEqual(checked.marks.length_mm, preset.arm_mm)
                    self.assertFalse(checked.blocked)

    def test_cli_applies_geometry_without_changing_blade_settings(self):
        build = job.build
        with mock.patch.object(cli.job_mod, 'build', wraps=build) as checked, \
             mock.patch.object(cli, 'say'):
            result = cli.main(['cut', '--layout-preset', 'mtg-cameo-letter',
                               '--force', '20', '--dry-run'])
        self.assertEqual(result, 0)
        options = checked.call_args.kwargs
        self.assertEqual(options['engine'], 'centered')
        self.assertEqual(options['radius_mm'], 3.2)
        self.assertEqual(options['mark_length_mm'], 8.0264)
        self.assertEqual(options['bleed_mm'], 0.625)
        self.assertEqual(options['force'], 20)
        self.assertEqual(options['preset_name'], config.DEFAULT_PRESET)


class TestPublishedTemplates(unittest.TestCase):
    def test_common_letter_and_a4_templates_build_with_their_published_registration(self):
        expected = {'letter': (8, 8, 8, 8, 9, 4, 9), 'a4': (8, 8, 9, 9, 10, 4, 9)}
        cards = ('standard', 'poker', 'japanese', 'bridge', 'catan', 'tarot', 'standard-borderless')
        for paper, counts in expected.items():
            for card, count in zip(cards, counts, strict=True):
                key = f'scm-{paper}-{card}'
                with self.subTest(template=key):
                    preset = layout_presets.available()[key]
                    plan = job.build(**preset.options(), imported=preset.artwork(), use_document_page=True)
                    self.assertEqual(len(plan.paths), count)
                    self.assertEqual((plan.marks.inset_mm, plan.marks.length_mm), (preset.inset_mm, preset.arm_mm))
                    self.assertFalse(plan.blocked, plan.blocked)
                    self.assertFalse(plan.liveness_failures)

    @unittest.skipUnless(os.environ.get('SCM_REFERENCE_DIR'), 'requires upstream Studio3 templates')
    def test_installed_paths_match_original_studio3_coordinates(self):
        source = Path(os.environ['SCM_REFERENCE_DIR'])
        for preset in layout_presets.available().values():
            if not isinstance(preset, layout_presets.PublishedTemplate):
                continue
            with self.subTest(source=preset.source):
                original = importer.load(source / preset.source)
                shift = 10 - preset.inset_mm
                for original_path, built_in in zip(original.paths, preset.paths, strict=True):
                    self.assertEqual(original_path.closed, built_in.closed)
                    for a, b in zip(original_path.points, built_in.points, strict=True):
                        for actual, expected in zip(b, a, strict=True):
                            self.assertAlmostEqual(actual, expected - shift, delta=0.00001)

    def test_cli_selects_published_template_without_user_artwork(self):
        with mock.patch.object(cli.job_mod, 'build', wraps=job.build) as build, mock.patch.object(cli, 'say'):
            self.assertEqual(cli.main(['cut', '--layout-preset', 'scm-a4-bridge', '--dry-run']), 0)
        options = build.call_args.kwargs
        self.assertTrue(layout_presets.is_published(options['imported']))
        self.assertTrue(options['use_document_page'])
        self.assertTrue(options['portrait'])
