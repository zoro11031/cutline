import pathlib
import tempfile
import unittest
from unittest import mock

from cutline import ble, config, cutters, graphtec, materials, presets, rfcomm, transport


class TestExactEndpoint(unittest.TestCase):
    def test_automatic_selection_prefers_the_only_usb_cutter(self):
        cameo = graphtec.device_by_name("Silhouette Cameo 4")
        usb, second_usb = (1, 2), (1, 3)
        wireless = ("ble", "A")
        records = [(cameo, "", usb)]
        with mock.patch.object(transport, "usb_endpoints", return_value=records):
            choices = {**cutters.usb_choices(), wireless: "CAMEO 4-499341P · Bluetooth LE · A"}
            self.assertEqual(cutters.detected_choice(choices, None), usb)
            self.assertEqual(cutters.detected_choice(choices, wireless), wireless)
            self.assertEqual(cutters.detected_choice({wireless: choices[wireless]}, None), wireless)
            records.append((cameo, "", second_usb))
            choices.update(cutters.usb_choices())
            self.assertIsNone(cutters.detected_choice(choices, None))

    def test_refusal_changes_with_endpoint_and_model(self):
        cameo = graphtec.device_by_name("Silhouette Cameo 4")
        portrait = graphtec.device_by_name("Silhouette Portrait 4")
        records = [(cameo, "", (1, 2)), (cameo, "", (1, 3))]
        with mock.patch.object(transport, "usb_endpoints", return_value=records):
            self.assertIsNone(cutters.usb_target())
            self.assertEqual(cutters.usb_refusal(("auto", (1, 2)), cameo), "")
            self.assertIn("Connected", cutters.usb_refusal(("auto", (1, 2)), portrait))
            self.assertIn("unavailable", cutters.usb_refusal(("ble", "a"), cameo, "Silhouette Curio"))
            with mock.patch.object(transport, "open_transport") as opened:
                cutters.open_link(("auto", (1, 3)), cameo)
                opened.assert_called_once_with("auto", device=cameo, usb_endpoint=(1, 3))
                records.pop()
                with self.assertRaises(transport.NotFound):
                    cutters.open_link(("auto", (1, 3)), cameo)
                self.assertEqual(opened.call_count, 1)

    def test_wireless_discovery_does_not_open_transport(self):
        with mock.patch.object(ble, "discover", return_value=[("A", "CAMEO 4-499341P")]), \
             mock.patch.object(rfcomm, "paired_candidates", return_value=[]), \
             mock.patch.object(transport, "open_transport") as opened:
            choices = cutters.wireless_choices()
            self.assertEqual(cutters.auto_model(("ble", "A"), choices[("ble", "A")]).product_id, 0x1137)
            opened.assert_not_called()


class TestFactorySnapshot(unittest.TestCase):
    def test_model_override_keeps_the_family_variant_for_the_other_holder(self):
        shared = dict(name="Vinyl", group_id="v", speed=3, passes=1,
                      depth_mm=None, offset_su=0, tool="blade", action="cut")
        rows = [materials.Material(**shared, cutter_class=division, force=force,
                                   source_id=key, holder=holder)
                for division, force, key, holder in (("cameo", 15, "base", 1),
                    ("cameo4", 18, "exact", 1), ("cameo", 20, "second", 2))]
        choices = materials.cutter_divisions(rows, "Silhouette Cameo 4", False)
        self.assertEqual({m.source_id: m.force for m in choices.values()}, {"exact": 18, "second": 20})
        self.assertEqual(len(materials.cutter_divisions(rows, "Silhouette Cameo 4", True)), 3)

    def test_preserves_variants_and_keeps_a_valid_snapshot_on_failed_refresh(self):
        source = """category,uuid,parent_uuid,name_resolved,cutter_class_id,(force.pressure),(speed),(tangential_emulation),(tool_holder)
Category,ID,Parent,Name,Class,Force,Speed,Overcut,Holder
folder,f,,Vinyl,,,,,
group,g,f,Sample,,,,,
leaf,a,g,,cameo,16,3,on,1
leaf,b,g,,cameo,20,3,off,2
leaf,c,g,,portrait,12,2,off,1
"""
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            path = root / "condition.csv"
            path.write_text(source)
            with mock.patch.object(config, "CONFIG_DIR", root / "config"):
                rows = materials.select_condition_csv(root)
                self.assertEqual(len(rows), 3)
                self.assertEqual(rows[0].categories, ("Vinyl",))
                self.assertTrue(rows[0].cut_settings().sharpen_corners)
                self.assertEqual(len(materials.cutter_divisions(rows, "Silhouette Cameo 4", False)), 2)
                self.assertEqual(materials.cutter_divisions(rows, "Silhouette Portrait 4", False)["Sample"].force, 12)
                self.assertEqual(materials.select_condition_csv(root), rows)
                cutter = "Silhouette Cameo 4"
                variants = list(presets.factory_presets(cutter).values())
                selected = presets.for_context(variants[0], cutter, 2)
                self.assertEqual(selected.factory_id, "b")
                override = presets.save_blade_settings(selected.name, force=25, speed=3, depth=None,
                                                       passes=1, cutter=cutter)
                personal = presets.save(presets.Preset("Personal sample", "", "measured", {"force": 9}))
                materials.select_condition_csv(root)
                self.assertEqual(presets.get(selected.name, cutter).cut_settings().force, 25)
                count, backup = presets.restore_default_materials()
                self.assertEqual(count, 1)
                self.assertTrue((backup / override.name).exists())
                self.assertTrue(personal.exists())
                self.assertEqual(presets.get(selected.name, cutter).cut_settings().force, 20)
                path.write_text(source + "leaf,a,g,,cameo,16,3,on,1\nleaf,d,g,,cameo,?,4,off,1\n")
                with self.assertRaisesRegex(ValueError, "row 8: repeated.*row 9: missing"):
                    materials.select_condition_csv(root)
                path.write_text("bad CSV")
                with self.assertRaises(ValueError):
                    materials.select_condition_csv(root)
                self.assertEqual(materials.load(), rows)
