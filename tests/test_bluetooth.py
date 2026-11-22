import unittest
from unittest import mock

from cutline import bluetooth, cli, graphtec, rfcomm, transport


class TestIdentity(unittest.TestCase):
    cameo4 = graphtec.device_by_name("Silhouette Cameo 4")
    records = (bluetooth.Candidate("local-one", "Cameo 4"),
               bluetooth.Candidate("local-two", "Cameo 4-1234"),
               bluetooth.Candidate("local-three", "Unnamed", service=True))

    def test_exact_name_then_unique_service_then_identifier(self):
        self.assertEqual(bluetooth.select_candidate(self.records, name="cameo 4").identifier,
                         "local-one")
        self.assertEqual(bluetooth.select_candidate(self.records).identifier, "local-three")
        self.assertEqual(bluetooth.select_candidate(self.records, identifier="LOCAL-TWO").identifier,
                         "local-two")

    def test_partial_name_rejects_two_cameos(self):
        with self.assertRaisesRegex(transport.TransportError, "More than one"):
            bluetooth.select_candidate(self.records, name="cameo")

    def test_expected_firmware_allows_compatible_and_rejects_a_known_different_model(self):
        self.assertEqual(bluetooth.confirm_firmware("CAMEO 4 V1.20", self.cameo4), self.cameo4)
        with self.assertRaisesRegex(transport.TransportError, "Expected Silhouette Cameo 4"):
            bluetooth.confirm_firmware("CAMEO 4 PLUS V1.20", self.cameo4, allow_unknown=True)

    def test_unknown_firmware_requires_explicit_override_and_model(self):
        self.assertEqual(bluetooth.confirm_firmware("Development V0.01", self.cameo4,
                                                    allow_unknown=True), self.cameo4)
        for model, allowed in ((None, True), (self.cameo4, False)):
            with self.subTest(model=model, allowed=allowed):
                with self.assertRaisesRegex(transport.TransportError, "Unrecognized"):
                    bluetooth.confirm_firmware("Development V0.01", model, allow_unknown=allowed)


class TestClassic(unittest.TestCase):
    address = "01:23:45:AB:CD:EF"

    def setUp(self):
        for name, value in (("AF_BLUETOOTH", 31), ("BTPROTO_RFCOMM", 3)):
            patcher = mock.patch.object(rfcomm.socket, name, value, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(rfcomm.socket, "socket")
        self.socket = patcher.start().return_value
        self.addCleanup(patcher.stop)

    def test_address_channel_and_fragmented_stream(self):
        self.socket.recv.side_effect = [b"CAMEO 4 V", b"1.20\x030\x03    ", b"0\x03"]
        with rfcomm.ClassicTransport(self.address.lower(), channel=3) as cutter:
            self.assertEqual(cutter.device.product_id, 0x1137)
            self.assertEqual(cutter.firmware_version(), "CAMEO 4 V1.20")
            self.assertEqual(cutter.read(), b"0\x03")
            self.assertEqual(cutter.read(), graphtec.RESP_REGMARK_OK)
            self.assertEqual(self.socket.connect.call_args.args, ((self.address, 3),))
            self.assertEqual(self.socket.sendall.call_args.args, (b"FG\x03",))
        self.assertEqual(self.socket.close.call_count, 1)

    def test_rejects_bad_channel_and_invalid_mac(self):
        for identifier, channel, expected in (("local-id", 1, "MAC address"),
                                              (self.address, 0, "channel")):
            with self.subTest(identifier=identifier, channel=channel):
                with self.assertRaisesRegex(transport.TransportError, expected):
                    rfcomm.ClassicTransport(identifier, channel=channel)

    def test_disconnect_and_failed_write_are_reported_without_replay(self):
        self.socket.recv.side_effect = [b"PORTRAIT 2 V1.02\x03", b""]
        with rfcomm.ClassicTransport(self.address) as cutter:
            self.assertEqual(cutter.device.product_id, 0x1132)
            with self.assertRaisesRegex(transport.TransportError, "disconnected"):
                cutter.read()
            self.socket.sendall.side_effect = OSError("closed")
            with self.assertRaisesRegex(transport.TransportError, "write failed"):
                cutter.write(b"cut")
            self.assertEqual(self.socket.sendall.call_count, 2)

    def test_linux_discovery_prefers_paired_models_and_deduplicates(self):
        def commands(args):
            output = {("devices", "Paired"): f"Device {self.address} Cameo 4",
                      ("info", self.address): "UUID: 00001101-0000-1000-8000-00805f9b34fb",
                      ("devices",): f"Device {self.address} Silhouette Cameo 4\n"
                                     "Device 22:23:45:AB:CD:EF Headset"}
            return output.get(tuple(args[1:]), "")

        with mock.patch.object(rfcomm.sys, "platform", "linux"), \
             mock.patch.object(rfcomm, "_run", side_effect=commands):
            self.assertEqual(rfcomm.discover(), [(self.address, "Cameo 4")])
            self.assertEqual(len(rfcomm.discover(include_all=True)), 1)


class TestDiagnostics(unittest.TestCase):
    def test_sends_only_model_specific_read_queries_and_caches_firmware(self):
        for model, expected in (("Silhouette Cameo 2", [b"FG\x03"]),
                                ("Silhouette Cameo 4", [b"FG\x03", b"TB71\x03", b"FA\x03"]),
                                ("Silhouette Cameo 3", [b"FG\x03", b"TB71\x03", b"FA\x03", b"TC\x03"])):
            with self.subTest(model=model):
                link = transport.Transport()
                link.device = graphtec.device_by_name(model)
                with mock.patch.object(link, "write") as write, \
                     mock.patch.object(link, "read", side_effect=[b"firmware\x03", *[b"0,0\x03"] * 3]):
                    self.assertEqual(link.diagnostics()[0], ("Firmware", "firmware"))
                    self.assertEqual(link.firmware_version(), "firmware")
                self.assertEqual([call.args[0] for call in write.call_args_list], expected)


class TestBluetoothCli(unittest.TestCase):
    def test_dry_run_does_not_discover_or_open_bluetooth(self):
        with mock.patch.object(transport, "open_transport") as open_link, mock.patch.object(cli, "say"):
            self.assertEqual(cli.main(["cut", "--transport", "bluetooth", "--dry-run"]), 0)
        self.assertEqual(open_link.call_count, 0)

    def test_live_cut_uses_identified_cutter_and_the_same_connection(self):
        model = graphtec.device_by_name("Silhouette Cameo 4 Plus")
        cutter = mock.MagicMock(device=model, identifier="local-id", can_read=True)
        cutter.__enter__.return_value = cutter
        cutter.status.return_value = "ready"
        cutter.run.return_value = 99
        with mock.patch.object(transport, "open_transport", return_value=cutter) as open_link, \
             mock.patch.object(cli, "say"):
            self.assertEqual(cli.main(["cut", "--transport", "ble", "--bluetooth-id", "local-id", "-y"]), 0)
        self.assertEqual(open_link.call_args_list, [mock.call("ble", bluetooth_id="local-id")])
        self.assertEqual(cutter.run.call_count, 1)
        self.assertEqual(cutter.__exit__.call_count, 1)

    def test_explicit_classic_channel_is_forwarded_and_validated(self):
        args = ["status", "--transport", "bluetooth", "--bluetooth-channel"]
        with mock.patch.object(rfcomm, "discover", return_value=[("address", "Cameo 4")]) as discover, \
             mock.patch.object(cli, "say"):
            self.assertEqual(cli.main([*args, "5"]), 0)
            self.assertEqual(discover.call_args, mock.call(include_all=False))
            with self.assertRaises(SystemExit):
                cli.main([*args, "31"])
