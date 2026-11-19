"""Settings and offset configuration safety."""

import argparse
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cut_proxies import cli, config, job, materials, presets, verify


class TestConfig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config_dir = Path(self.tmp.name)
        self.patch = mock.patch.object(config, 'CONFIG_DIR', self.config_dir)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_load_settings_missing_file(self):
        self.assertEqual(config.load_settings(), {})

    def test_load_settings_corrupted_json(self):
        (self.config_dir / 'settings.json').write_text('{invalid json')
        self.assertEqual(config.load_settings(), {})

    def test_load_settings_non_dict_roots(self):
        for payload in ('null', '[]', '[1, 2]', '"a string"', '123', 'true', 'false'):
            with self.subTest(payload=payload):
                (self.config_dir / 'settings.json').write_text(payload)
                self.assertEqual(config.load_settings(), {})

    def test_load_settings_valid_dict(self):
        (self.config_dir / 'settings.json').write_text('{"colorblind_mode": true}')
        self.assertEqual(config.load_settings(), {"colorblind_mode": True})

    def test_cut_offset_defaults_on_missing_or_malformed(self):
        self.assertEqual(config.cut_offset(), (0.0, 0.0))
        for malformed in ({}, [], [1.0], {"x": 1.0, "y": 2.0}, "0.5, 0.5", 42, None):
            with self.subTest(malformed=malformed):
                (self.config_dir / 'settings.json').write_text(json.dumps({"cut_offset_mm": malformed}))
                self.assertEqual(config.cut_offset(), (0.0, 0.0))

    def test_cut_offset_rejects_non_finite(self):
        for bad in (float('nan'), float('inf'), float('-inf'), 'nan', 'inf', '-inf'):
            with self.subTest(bad=bad):
                (self.config_dir / 'settings.json').write_text(json.dumps({"cut_offset_mm": [bad, 1.0]}))
                self.assertEqual(config.cut_offset(), (0.0, 0.0))

    def test_cut_offset_valid_floats(self):
        (self.config_dir / 'settings.json').write_text('{"cut_offset_mm": [0.125, -0.25]}')
        self.assertEqual(config.cut_offset(), (0.125, -0.25))

    def test_save_cut_offset_rounds_and_persists(self):
        config.save_cut_offset(0.12345, -0.6789)
        self.assertEqual(config.cut_offset(), (0.123, -0.679))
        loaded = json.loads((self.config_dir / 'settings.json').read_text())
        self.assertEqual(loaded["cut_offset_mm"], [0.123, -0.679])

    def test_save_cut_offset_refuses_non_finite_or_invalid(self):
        for bad in (float('nan'), float('inf'), float('-inf'), 'abc', None):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    config.save_cut_offset(bad, 0.0)
                with self.assertRaises(ValueError):
                    config.save_cut_offset(0.0, bad)


class TestBladePresets(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patch = mock.patch.object(config, "CONFIG_DIR", Path(tmp.name))
        patch.start()
        self.addCleanup(patch.stop)

    def test_black_core_defaults_keep_requested_force_and_send_model_limit(self):
        for name in ("310 gsm black-core", "330 gsm black-core"):
            with self.subTest(preset=name):
                preset = presets.get(name).cut_settings()
                self.assertEqual((preset.force, preset.speed, preset.depth, preset.passes),
                                 (35, 25, 7, 4))
                for cutter, force in (("Silhouette Cameo 4", 33),
                                      ("Silhouette Cameo 5 Alpha", 35)):
                    cut = job.build(preset_name=name, cutter=cutter)
                    self.assertFalse(cut.blocked)
                    decoded = verify.decode(cut.stream)
                    self.assertEqual((decoded.force, decoded.speed, decoded.depth,
                                      len(decoded.paths)), (force, 25, 7, len(cut.paths) * 4))
                self.assertEqual(presets.get(name).cut_settings().force, 35)

    def test_cli_overrides_reach_selected_holder_in_checked_stream(self):
        parser = argparse.ArgumentParser()
        cli.common_layout_args(parser)
        cli.common_cut_args(parser)
        args = parser.parse_args("--holder 2 --depth -1 --speed 8 --force 19 --passes 2 "
                                 "--blade-diameter-mm 0.65 --no-lift-head --sharpen-corners "
                                 "--corner-start-mm 0.4 --corner-end-mm 0.7 --acceleration -1".split())
        checked = cli.build_from_args(args)
        self.assertFalse(checked.blocked)
        commands = verify.decode(checked.stream).commands
        for expected in ("J2", "FX19,2", "!8,2", "FC13,1,2", "FE1,2", "FF4,7,2", "TJ0", "TJ3"):
            self.assertIn(expected, commands)
        self.assertIsNone(verify.decode(checked.stream).depth)
        self.assertEqual(len(verify.decode(checked.stream).paths), 2 * len(checked.paths))

    def test_edits_reload_and_preserve_other_settings_and_custom_filename(self):
        name = "330 gsm black-core"
        original = presets.get(name)
        folder = presets.user_dir()
        folder.mkdir()
        override = folder / "workshop-name.json"
        override.write_text(json.dumps({"name": name, "settings": original.settings}))
        saved = presets.save_blade_settings(name, force=31, speed=22, depth=6, passes=5)
        self.assertEqual(saved, override)
        read_back = presets.get(name).cut_settings()
        self.assertEqual((read_back.force, read_back.speed, read_back.depth, read_back.passes),
                         (31, 22, 6, 5))
        self.assertEqual(read_back.blade_diameter_mm, original.settings["blade_diameter_mm"])
        self.assertEqual(read_back.acceleration, original.settings["acceleration"])
        self.assertEqual(set(folder.iterdir()), {override})

    def test_depth_off_works_for_live_edits_and_saved_presets(self):
        name = "310 gsm black-core"
        self.assertEqual(verify.decode(job.build(preset_name=name).stream).depth, 7)
        self.assertIsNone(verify.decode(job.build(preset_name=name, depth=None).stream).depth)
        presets.save_blade_settings(name, force=35, speed=25, depth=None, passes=4)
        self.assertIsNone(verify.decode(job.build(preset_name=name).stream).depth)


    def test_restore_defaults_backs_up_shipped_overrides_and_keeps_custom_presets(self):
        name = "Coverstock"
        built_in = presets.get(name)
        override = presets.save_blade_settings(name, force=23, speed=8, passes=2, depth=3)
        personal = presets.save_blade_settings(name, force=24, speed=9, passes=3, depth=4,
                                              new_name="My test")
        count, backup = presets.restore_default_materials()
        self.assertEqual(count, 1)
        self.assertEqual(presets.get(name), built_in)
        self.assertEqual(presets.get("My test").settings["force"], 24)
        self.assertTrue(personal.exists())
        self.assertEqual(json.loads((backup / override.name).read_text())["settings"]["force"], 23)
        self.assertEqual(presets.restore_default_materials(), (0, None))

    def test_measured_material_can_use_a_retired_name_and_survives_factory_reset(self):
        name = "330 gsm black-core"
        self.assertNotIn(name, presets.all_presets())
        tested = presets.get(presets.STARTING_NAME).cut_settings()
        path = presets.save_tested_settings(name, tested, "Silhouette Cameo 4")
        self.assertEqual(presets.restore_default_materials(), (0, None))
        self.assertTrue(path.is_file())
        self.assertEqual(presets.get(name).cut_settings(), tested)
        with self.assertRaisesRegex(ValueError, "already exists"):
            presets.save_tested_settings(name.upper(), tested, "Silhouette Cameo 4")

    def test_is_deletable_and_delete_preset(self):
        name = presets.STARTING_NAME
        self.assertFalse(presets.is_deletable(name))
        self.assertFalse(presets.delete_preset(name))

        custom = presets.save_blade_settings(name, force=20, speed=10, passes=1, depth=2,
                                             new_name="Deletable Custom")
        self.assertTrue(presets.is_deletable("Deletable Custom"))
        self.assertTrue(presets.delete_preset("Deletable Custom"))
        self.assertFalse(custom.exists())
        self.assertFalse(presets.is_deletable("Deletable Custom"))

        override = presets.save_blade_settings(name, force=29, speed=12, passes=2, depth=4)
        self.assertTrue(presets.is_deletable(name))
        self.assertTrue(presets.delete_preset(name))
        self.assertFalse(override.exists())
        self.assertFalse(presets.is_deletable(name))


class TestCutterDivisions(unittest.TestCase):
    def test_known_prefers_matching_material_division_and_all_lists_vendor_rows(self):
        common = materials.Material("Coverstock", "cameo", 28, 3, 1, 0.4, 18, "blade")
        portrait = materials.Material("Coverstock", "portrait", 25, 2, 1, 0.4, 18, "blade")
        cameo4 = materials.Material("Coverstock", "cameo4", 30, 4, 1, 0.4, 18, "blade")
        rows = (portrait, common, cameo4)
        self.assertEqual(materials.cutter_divisions(rows, "Silhouette Cameo 4", False),
                         {"Coverstock": cameo4})
        self.assertEqual(materials.cutter_divisions(rows, "Silhouette Portrait 4", False),
                         {"Coverstock": portrait})
        self.assertEqual(materials.cutter_divisions(rows, "Silhouette Cameo 3", False),
                         {"Coverstock": common})
        choices = materials.cutter_divisions(rows, "Silhouette Cameo 4", True)
        self.assertEqual(len(choices), 3)
        self.assertEqual(set(choices.values()), set(rows))


if __name__ == '__main__':
    unittest.main()
