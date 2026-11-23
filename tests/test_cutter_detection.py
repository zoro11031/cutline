import pathlib
import tempfile
import unittest
from unittest import mock

from cutline import ble, bluetooth, config, cutters, graphtec, materials, presets, rfcomm, transport


class TestExactEndpoint(unittest.TestCase):
    def setUp(self):
        # No test may run a real Classic inquiry: an LE-only cutter starts one.
        patcher = mock.patch.object(rfcomm, "nearby_candidates", return_value=[])
        self.inquiry = patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(cutters.forget_nearby)

    def test_automatic_selection_prefers_the_only_usb_cutter(self):
        cameo = graphtec.device_by_name("Silhouette Cameo 4")
        usb, second_usb = (1, 2), (1, 3)
        wireless = ("ble", "A")
        records = [(cameo, "", usb)]
        with mock.patch.object(transport, "usb_endpoints", return_value=records):
            choices = {**cutters.usb_choices(), wireless: "CAMEO 4-1234 · Bluetooth LE · A"}
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
        with mock.patch.object(ble, "discover", return_value=[("A", "CAMEO 4-1234")]), \
             mock.patch.object(rfcomm, "paired_candidates", return_value=[]), \
             mock.patch.object(transport, "open_transport") as opened:
            choices = cutters.wireless_choices()
            self.assertEqual(cutters.auto_model(("ble", "A"), choices[("ble", "A")]).product_id, 0x1137)
            opened.assert_not_called()


    def test_paired_classic_is_offered_without_a_live_connection(self):
        # Connections are opened per action, so a paired cutter is normally
        # disconnected; it must still be offered.
        classic = bluetooth.Candidate("AA:BB:CC:00:11:22", "CAMEO 4-1234")
        with mock.patch.object(ble, "discover", return_value=[]), \
             mock.patch.object(rfcomm, "paired_candidates", return_value=[classic]) as paired:
            choices = cutters.wireless_choices()
        self.assertIn(("bluetooth", classic.identifier), choices)
        self.assertNotEqual(paired.call_args, mock.call(connected=True))

    def test_classic_replaces_the_same_cutters_le_entry(self):
        # Studio 5.0.556.1 reaches a Cameo 4 over Classic only (2026-11-21);
        # no LE session to one has worked. Offer the route that works.
        classic = bluetooth.Candidate("AA:BB:CC:00:11:22", "CAMEO 4-1234")
        headset = bluetooth.Candidate("AA:BB:CC:99:88:77", "Headphones")
        le = [("C0:BB:CC:00:11:22", "CAMEO 4-1234"), ("C0:00:00:00:00:01", "CAMEO 4-777")]
        with mock.patch.object(ble, "discover", return_value=le), \
             mock.patch.object(rfcomm, "paired_candidates", return_value=[classic, headset]):
            choices = cutters.wireless_choices()
        self.assertEqual(set(choices), {("bluetooth", "AA:BB:CC:00:11:22"), ("ble", "C0:00:00:00:00:01")})
        self.assertEqual(next(iter(choices)), ("bluetooth", "AA:BB:CC:00:11:22"))
        self.assertEqual(cutters.detected_choice(choices, None), None)  # two cutters: ask
        only = {key: label for key, label in choices.items() if key[0] == "bluetooth"}
        self.assertEqual(cutters.detected_choice(only, None), ("bluetooth", classic.identifier))


    def test_only_a_search_runs_the_classic_inquiry(self):
        # The windows refresh the list every 30 s. An inquiry takes 13 s and
        # can make Bluetooth audio stutter, so only Scan for Cutters (or
        # Refresh Cutters) runs one; what it found stays listed until paired.
        nearby = bluetooth.Candidate("AA:BB:CC:00:11:22", "CAMEO 4-1234")
        endpoint = ("bluetooth", nearby.identifier)
        self.addCleanup(cutters.forget_nearby)
        with mock.patch.object(ble, "discover", return_value=[]), \
             mock.patch.object(rfcomm, "paired_candidates", return_value=[]) as paired, \
             mock.patch.object(rfcomm, "nearby_candidates", return_value=[nearby]) as inquiry:
            self.assertEqual(cutters.wireless_choices(), {})
            self.assertEqual(inquiry.call_count, 0)
            self.assertFalse(cutters.unpaired(endpoint))
            choices = cutters.wireless_choices(search=True)
            self.assertEqual(inquiry.call_count, 1)
            self.assertEqual(choices[endpoint], "CAMEO 4-1234 · Bluetooth Classic · AA:BB:CC:00:11:22")
            self.assertTrue(cutters.unpaired(endpoint))
            self.assertEqual(cutters.detected_choice(choices, None), endpoint)
            self.assertIn(endpoint, cutters.wireless_choices())          # a later refresh keeps it
            self.assertEqual(inquiry.call_count, 1)
            paired.return_value = [nearby]                                 # now paired
            self.assertEqual(list(cutters.wireless_choices()), [endpoint])
            self.assertFalse(cutters.unpaired(endpoint))

    def test_a_search_offers_the_classic_cutter_not_its_le_twin(self):
        nearby = bluetooth.Candidate("AA:BB:CC:00:11:22", "CAMEO 4-1234")
        self.addCleanup(cutters.forget_nearby)
        with mock.patch.object(ble, "discover", return_value=[("C0:BB:CC:00:11:22", "CAMEO 4-1234")]), \
             mock.patch.object(rfcomm, "paired_candidates", return_value=[]), \
             mock.patch.object(rfcomm, "nearby_candidates", return_value=[nearby]):
            self.assertEqual(list(cutters.wireless_choices(search=True)), [("bluetooth", nearby.identifier)])

    def test_a_link_to_a_found_cutter_marks_it_paired(self):
        nearby = bluetooth.Candidate("AA:BB:CC:00:11:22", "CAMEO 4-1234")
        endpoint = ("bluetooth", nearby.identifier)
        self.addCleanup(cutters.forget_nearby)
        self.addCleanup(cutters.close_held)
        link = mock.MagicMock(device=graphtec.device_by_name("Silhouette Cameo 4"))
        with mock.patch.object(ble, "discover", return_value=[]), \
             mock.patch.object(rfcomm, "paired_candidates", return_value=[]), \
             mock.patch.object(rfcomm, "nearby_candidates", return_value=[nearby]):
            cutters.wireless_choices(search=True)
        for opens, still_unpaired in ((transport.TransportError("Could not pair"), True), (link, False)):
            with self.subTest(still_unpaired=still_unpaired), \
                 mock.patch.object(transport, "open_transport", side_effect=[opens]):
                try:
                    with cutters.open_link(endpoint):
                        pass
                except transport.TransportError:
                    pass
                self.assertEqual(cutters.unpaired(endpoint), still_unpaired)

    def test_a_cutter_heard_only_over_le_is_searched_for_over_classic_once(self):
        # Seen 2026-11-22: an unpaired Cameo 4 is heard over LE only, and no
        # LE session to one has worked. One search finds its Classic side.
        classic = bluetooth.Candidate("AA:BB:CC:00:11:22", "CAMEO 4-1234")
        le = [("C0:BB:CC:00:11:22", "CAMEO 4-1234"), ("C0:00:00:00:00:09", "Headphones")]
        self.inquiry.return_value = [classic]
        with mock.patch.object(ble, "discover", return_value=le), \
             mock.patch.object(rfcomm, "paired_candidates", return_value=[]) as paired:
            choices = cutters.wireless_choices()
            self.assertIn(("bluetooth", classic.identifier), choices)
            self.assertNotIn(("ble", le[0][0]), choices)    # its LE twin gives way
            self.assertEqual(self.inquiry.call_count, 1)
            self.inquiry.return_value = []
            cutters.wireless_choices()                     # the same cutter: not again
            self.assertEqual(self.inquiry.call_count, 1)
            cutters.forget_nearby()
            paired.return_value = [classic]                # its Classic side is paired
            cutters.wireless_choices()
            self.assertEqual(self.inquiry.call_count, 1)
        with mock.patch.object(ble, "discover", return_value=[le[1]]), \
             mock.patch.object(rfcomm, "paired_candidates", return_value=[]):
            cutters.wireless_choices()                     # not a cutter
        self.assertEqual(self.inquiry.call_count, 1)

    def test_a_cutter_only_on_le_is_not_searched_for_every_refresh(self):
        # A cutter that really is LE-only: the search finds nothing, and the
        # 30 s refresh must not run a 13 s inquiry every time it hears it.
        with mock.patch.object(ble, "discover", return_value=[("C0:BB:CC:00:11:22", "CAMEO 5 ALPHA-1")]), \
             mock.patch.object(rfcomm, "paired_candidates", return_value=[]):
            for _ in range(3):
                self.assertIn(("ble", "C0:BB:CC:00:11:22"), cutters.wireless_choices())
        self.assertEqual(self.inquiry.call_count, 1)

    def test_scan_wording_is_shared_and_says_what_to_try(self):
        self.assertEqual(cutters.SCAN_LABEL, "Scan for Cutters")
        self.assertIn("USB", cutters.NO_CUTTER_TEXT)
        self.assertIn("Bluetooth", cutters.NO_CUTTER_TEXT)
        self.assertTrue(cutters.SCANNING_TEXT.endswith("…"))


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
