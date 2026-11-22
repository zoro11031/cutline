"""Silhouette Studio's Text Style options (manual chapter 05) and Text to Path."""

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from cutline import design_text, editor_model as model

FAMILY = "DejaVu Sans"


def _font_or_skip(case):
    try:
        families = design_text.installed_families()
    except ValueError:
        families = ()
    if FAMILY not in families:
        case.skipTest(f"test font {FAMILY} is not installed")


class TestTextSpec(unittest.TestCase):
    def test_plain_text_is_not_styled_and_any_option_is(self):
        plain = model.TextSpec("A", FAMILY, 10)
        self.assertFalse(plain.styled)
        for change in ({"bold": True}, {"italic": True}, {"align": "center"}, {"char_spacing": 150.0},
                       {"line_spacing": 80.0}, {"wrap_mm": 40.0}, {"path": ((0, 0), (10, 0))},
                       {"path_offset": 1.0}, {"path_start": 2.0}):
            self.assertTrue(replace(plain, **change).styled, change)

    def test_valid_text_bounds_the_new_options(self):
        plain = model.TextSpec("A", FAMILY, 10)
        self.assertTrue(model.valid_text(plain))
        self.assertTrue(model.valid_text(replace(plain, char_spacing=10, line_spacing=500, path_offset=-100)))
        for bad in ({"align": "justify"}, {"bold": 1}, {"char_spacing": 9}, {"line_spacing": 501},
                    {"wrap_mm": -1}, {"path_offset": 101}, {"path": ((0, 0),)},
                    {"path": ((0, 0), (1, float("nan")))}, {"path": ((0, 0, 0), (1, 1))}):
            self.assertFalse(model.valid_text(replace(plain, **bad)), bad)


class TestTextProjectVersion(unittest.TestCase):
    """Styled text needs version 6; an older file claiming it is refused."""

    def setUp(self):
        _font_or_skip(self)

    def save(self, spec, version=None):
        project = model.Project(279.4, 215.9, (design_text.shape(spec, 20, 30),))
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        target = Path(folder.name) / "text.scc-template"
        model.save_project(target, project)
        if version is not None:
            document = json.loads(target.read_text())
            document["version"] = version
            target.write_text(json.dumps(document))
        return target, project

    def test_styled_text_saves_as_version_6_and_round_trips(self):
        target, project = self.save(model.TextSpec("Ab", FAMILY, 10, bold=True, char_spacing=140.0))
        self.assertEqual(json.loads(target.read_text())["version"], 6)
        self.assertEqual(model.load_project(target).shapes[0].text, project.shapes[0].text)

    def test_plain_text_still_saves_as_an_older_version_and_opens_as_one(self):
        target, project = self.save(model.TextSpec("Ab", FAMILY, 10))
        self.assertLess(json.loads(target.read_text())["version"], 6)
        target, _ = self.save(model.TextSpec("Ab", FAMILY, 10), version=5)
        self.assertEqual(model.load_project(target).shapes[0].text, project.shapes[0].text)

    def test_a_version_5_file_with_styled_text_is_refused(self):
        target, _ = self.save(model.TextSpec("Ab", FAMILY, 10, italic=True), version=5)
        with self.assertRaises(ValueError):
            model.load_project(target)


class TestTextOutlines(unittest.TestCase):
    def setUp(self):
        _font_or_skip(self)

    def outline(self, **options):
        return design_text.shape(model.TextSpec("Hi Hi", FAMILY, 10, **options), 0, 0)

    def test_character_spacing_widens_the_line_without_changing_its_height(self):
        plain, wide = self.outline(), self.outline(char_spacing=200.0)
        self.assertGreater(wide.width, plain.width + 10)
        self.assertAlmostEqual(wide.height, plain.height, delta=0.01)

    def test_bold_and_italic_change_the_outline(self):
        plain = self.outline()
        self.assertNotEqual(self.outline(bold=True).contours, plain.contours)
        self.assertNotEqual(self.outline(italic=True).contours, plain.contours)

    def test_a_text_box_wraps_onto_more_lines_and_line_spacing_spreads_them(self):
        one_line = self.outline()
        wrapped = self.outline(wrap_mm=15.0)
        self.assertLess(wrapped.width, one_line.width)
        self.assertGreater(wrapped.height, one_line.height * 1.5)
        spread = self.outline(wrap_mm=15.0, line_spacing=200.0)
        self.assertGreater(spread.height, wrapped.height + 5)

    def test_text_on_a_path_follows_the_guide_line(self):
        level = self.outline(path=((10, 50), (110, 50)))
        self.assertAlmostEqual(level.x, 10, delta=2, msg="starts at the guide's first point")
        self.assertLess(level.y + level.height, 50 + 3, "sits on the line")
        self.assertGreater(level.y + level.height, 50 - 3)
        upright = self.outline(path=((50, 10), (50, 110)))
        self.assertGreater(upright.height, upright.width, "a vertical guide stands the text up")
        lifted = self.outline(path=((10, 50), (110, 50)), path_offset=5.0)
        self.assertAlmostEqual(lifted.y, level.y - 5, delta=0.05)
        later = self.outline(path=((10, 50), (110, 50)), path_start=20.0)
        self.assertAlmostEqual(later.x, level.x + 20, delta=0.05)


if __name__ == "__main__":
    unittest.main()
