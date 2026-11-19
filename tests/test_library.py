"""Tests for the offline design library."""

from pathlib import Path
import tempfile
import unittest

from cut_proxies import editor_model as model, library


class TestLibrary(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp_dir.name)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_save_and_list_entries(self):
        rect = model.Shape("Rectangle", 20, 20, 40, 30, stroke="#ff0000", layer="Crease")
        circle = model.Shape("Ellipse", 70, 20, 30, 30, action="Score")
        project = model.Project(279.4, 215.9, (rect, circle), guides=(("h", 50.0),))

        path = library.save_to_library("Card Box", project, category="Boxes", tags=["gift", "template"], directory=self.folder)
        self.assertTrue(path.is_file())

        entries = library.list_entries(self.folder)
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry.name, "Card Box")
        self.assertEqual(entry.category, "Boxes")
        self.assertIn("gift", entry.tags)
        self.assertEqual(entry.shapes_count, 2)
        self.assertEqual(entry.width, 279.4)
        self.assertEqual(entry.height, 215.9)

    def test_search_and_category_filter(self):
        s1 = model.Shape("Rectangle", 10, 10, 20, 20)
        library.save_to_library("Hexagon Sticker", (s1,), category="Stickers", tags=["star", "vinyl"], directory=self.folder)
        library.save_to_library("Greeting Card", (s1,), category="Cards", tags=["envelope", "holiday"], directory=self.folder)
        library.save_to_library("Gift Tag", (s1,), category="Tags", tags=["holiday", "ribbon"], directory=self.folder)

        all_entries = library.list_entries(self.folder)
        self.assertEqual(len(all_entries), 3)

        cards = library.list_entries(self.folder, category="Cards")
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0].name, "Greeting Card")

        holiday = library.list_entries(self.folder, query="holiday")
        self.assertEqual(len(holiday), 2)
        holiday_names = {e.name for e in holiday}
        self.assertEqual(holiday_names, {"Greeting Card", "Gift Tag"})

        vinyl = library.list_entries(self.folder, query="vinyl")
        self.assertEqual(len(vinyl), 1)
        self.assertEqual(vinyl[0].name, "Hexagon Sticker")

    def test_load_and_roundtrip(self):
        rect = model.Shape("Rectangle", 30, 30, 50, 40, layer="Outline")
        project = model.Project(210.0, 297.0, (rect,), guides=(("v", 105.0),))
        path = library.save_to_library("A4 Frame", project, directory=self.folder)

        loaded = library.load_entry("A4 Frame", self.folder)
        self.assertEqual(loaded.width, 210.0)
        self.assertEqual(loaded.height, 297.0)
        self.assertEqual(len(loaded.shapes), 1)
        self.assertEqual(loaded.shapes[0].layer, "Outline")
        self.assertEqual(loaded.guides, (("v", 105.0),))

    def test_delete_entry(self):
        s = model.Shape("Line", 10, 10, 50, 50)
        library.save_to_library("To Delete", (s,), directory=self.folder)
        self.assertEqual(len(library.list_entries(self.folder)), 1)

        success = library.delete_entry("To Delete", self.folder)
        self.assertTrue(success)
        self.assertEqual(len(library.list_entries(self.folder)), 0)

        # Deleting nonexistent returns False
        self.assertFalse(library.delete_entry("Nonexistent", self.folder))

    def test_export_and_import_archive(self):
        s1 = model.Shape("Rectangle", 10, 10, 30, 30)
        s2 = model.Shape("Ellipse", 50, 50, 20, 20)
        library.save_to_library("Design One", (s1,), directory=self.folder)
        library.save_to_library("Design Two", (s2,), directory=self.folder)

        zip_target = self.folder / "export.zip"
        library.export_library(zip_target, self.folder)
        self.assertTrue(zip_target.is_file())

        with tempfile.TemporaryDirectory() as dest_folder:
            dest = Path(dest_folder)
            count = library.import_library(zip_target, dest)
            self.assertEqual(count, 2)
            imported_entries = library.list_entries(dest)
            self.assertEqual(len(imported_entries), 2)
            imported_names = {e.name for e in imported_entries}
            self.assertEqual(imported_names, {"Design One", "Design Two"})

    def test_directory_traversal_guards(self):
        with self.assertRaises(ValueError):
            library.load_entry(Path("/etc/passwd"), self.folder)

        with self.assertRaises(ValueError):
            library.delete_entry(Path("/etc/passwd"), self.folder)

        with self.assertRaises(ValueError):
            library.save_to_library("../malicious", (model.Shape("Rectangle"),), directory=self.folder)
