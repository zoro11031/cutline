from pathlib import Path
import tempfile
import unittest
from unittest import mock
from cutline import config, confirmation, geometry, graphtec, job, presets


class TestConfirmation(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        patch = mock.patch.object(config, "CONFIG_DIR", Path(folder.name))
        patch.start()
        self.addCleanup(patch.stop)
        self.plan = job.build(
            sheet="Letter",
            portrait=False,
            cutter="Silhouette Cameo 4",
            preset_name=presets.STARTING_NAME,
        )

    def test_verification_statement_plural(self):
        statement = confirmation.verification_statement(self.plan)
        self.assertIn(f"{len(self.plan.paths)} paths decoded and measured inside the sheet.", statement)

    def test_verification_statement_singular(self):
        single_path_job = mock.MagicMock()
        single_path_job.paths = [object()]
        statement = confirmation.verification_statement(single_path_job)
        self.assertEqual("1 path decoded and measured inside the sheet.", statement)

    def test_send_confirmation_normal_cut(self):
        conf = confirmation.build_send_confirmation(
            self.plan,
            ("auto", "/dev/usb/lp0"),
            scan_only=False,
        )
        self.assertEqual(conf.title, "Send this cut?")
        self.assertEqual(conf.lead, "This moves the blade.")
        self.assertEqual(conf.confirm_label, "Cut")
        self.assertTrue(conf.destructive)
        self.assertIn("Connection: Silhouette Cameo 4 · USB printer /dev/usb/lp0", conf.body)
        self.assertIn("paths decoded and measured inside the sheet.", conf.body)
        self.assertIn("This moves the blade.", conf.summary)
        self.assertIn("paths decoded and measured inside the sheet.", conf.details)
        self.assertIn("Connection: Silhouette Cameo 4 · USB printer /dev/usb/lp0", conf.details)

        # Ordering check: lead comes before warnings, warnings before settings, settings before connection
        body = conf.body
        lead_pos = body.index("This moves the blade.")
        ver_pos = body.index("decoded and measured")
        settings_pos = body.index(self.plan.grid.describe())
        conn_pos = body.index("Connection:")
        self.assertLess(lead_pos, ver_pos)
        self.assertLess(ver_pos, settings_pos)
        self.assertLess(settings_pos, conn_pos)

    def test_send_confirmation_scan_only(self):
        conf = confirmation.build_send_confirmation(
            self.plan,
            ("auto", "/dev/usb/lp0"),
            scan_only=True,
        )
        self.assertEqual(conf.title, "Test registration?")
        self.assertEqual(conf.lead, "This test ends after the cutter reports the scan result.")
        self.assertEqual(conf.confirm_label, "Scan")
        self.assertFalse(conf.destructive)
        self.assertIn("This scans the printed marks without sending cut paths.", conf.warnings)
        self.assertIn("Connection: Silhouette Cameo 4 · USB printer /dev/usb/lp0", conf.body)
        self.assertIn(self.plan.grid.describe(), conf.body)

    def test_discrimination_scan_vs_cut(self):
        cut_conf = confirmation.build_send_confirmation(self.plan, ("auto", None), scan_only=False)
        scan_conf = confirmation.build_send_confirmation(self.plan, ("auto", None), scan_only=True)
        self.assertNotEqual(cut_conf.title, scan_conf.title)
        self.assertNotEqual(cut_conf.lead, scan_conf.lead)
        self.assertNotEqual(cut_conf.confirm_label, scan_conf.confirm_label)
        self.assertNotEqual(cut_conf.destructive, scan_conf.destructive)
        self.assertEqual(cut_conf.untested_presets, (presets.STARTING_NAME,))
        self.assertEqual(scan_conf.untested_presets, ())

    def test_marking_preset_tested_silences_and_unmarking_restores_hardware_test_warning(self):
        warning = "The selected settings need a hardware test on this stock."
        initial = confirmation.build_send_confirmation(self.plan, ("auto", None), scan_only=False)
        self.assertIn(warning, initial.warnings)
        self.assertEqual(initial.untested_presets, (presets.STARTING_NAME,))

        config.set_preset_tested(presets.STARTING_NAME, True)
        silenced = confirmation.build_send_confirmation(self.plan, ("auto", None), scan_only=False)
        self.assertNotIn(warning, silenced.warnings)
        self.assertEqual(silenced.untested_presets, ())

        config.set_preset_tested(presets.STARTING_NAME, False)
        restored = confirmation.build_send_confirmation(self.plan, ("auto", None), scan_only=False)
        self.assertIn(warning, restored.warnings)
        self.assertEqual(restored.untested_presets, (presets.STARTING_NAME,))


class TestEndpointText(unittest.TestCase):
    def test_every_kind_of_connection_reads_as_words_not_a_tuple(self):
        self.assertEqual(confirmation.endpoint_text("auto", (1, 2)), "USB bus 1, device 2")
        self.assertEqual(confirmation.endpoint_text("ble", "AA:BB"), "Bluetooth LE AA:BB")
        self.assertEqual(confirmation.endpoint_text("bluetooth", "AA:BB"), "Bluetooth Classic AA:BB")
        self.assertEqual(confirmation.endpoint_text("auto", "/dev/usb/lp0"), "USB printer /dev/usb/lp0")

