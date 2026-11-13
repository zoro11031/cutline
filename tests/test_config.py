"""Settings and offset configuration safety."""

import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cut_proxies import config


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


if __name__ == '__main__':
    unittest.main()
