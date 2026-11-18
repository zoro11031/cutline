import unittest
from unittest import mock

from cut_proxies import cli, config, job, layout_presets


class TestProxyPressCameoPreset(unittest.TestCase):
    def test_letter_matches_current_proxypress_coordinates_and_auto_arm(self):
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

    def test_a4_matches_current_proxypress_coordinates_and_auto_arm(self):
        j = job.build(**layout_presets.BUILTIN['mtg-cameo-a4'].options(), offset_mm=(0, 0))
        self.assertEqual((j.grid.sheet_w_mm, j.grid.sheet_h_mm), (297, 210))
        self.assertAlmostEqual(j.grid.cards[0].x, 20.625)
        self.assertAlmostEqual(j.grid.cards[0].y, 16.375)
        self.assertEqual(j.marks.length_mm, 9.398)
        self.assertEqual(j.marks.pattern, 'three')
        self.assertEqual(j.marks.thickness_mm, 1.0)
        self.assertFalse(j.blocked)

    def test_cli_applies_geometry_without_changing_blade_settings(self):
        build = job.build
        with mock.patch.object(cli.job_mod, 'build', wraps=build) as checked, \
             mock.patch.object(cli, 'say'):
            result = cli.main(['cut', '--layout-preset', 'mtg-cameo-letter',
                               '--force', '20', '--dry-run'])
        self.assertEqual(result, 0)
        options = checked.call_args.kwargs
        self.assertEqual(options['engine'], 'proxypress')
        self.assertEqual(options['radius_mm'], 3.2)
        self.assertEqual(options['mark_length_mm'], 8.0264)
        self.assertEqual(options['bleed_mm'], 0.625)
        self.assertEqual(options['force'], 20)
        self.assertEqual(options['preset_name'], config.DEFAULT_PRESET)
