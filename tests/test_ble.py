import asyncio
import unittest
from unittest import mock

from cut_proxies import ble, graphtec, transport


class TestBle(unittest.TestCase):
    def setUp(self):
        self.cutter = ble.BleTransport.__new__(ble.BleTransport)
        self.cutter._runner = asyncio.new_event_loop()
        self.cutter._ready = asyncio.Event()
        self.cutter._buffer = bytearray()
        self.cutter._client = mock.Mock(is_connected=True)
        self.cutter._client.disconnect = mock.AsyncMock()
        self.cutter._client.write_gatt_char = mock.AsyncMock()
        self.addCleanup(self.cutter.close)

    def test_fragmented_and_coalesced_replies(self):
        self.cutter._on_reply(None, b"    ")
        self.cutter._on_reply(None, b"0\x030\x03")
        self.assertEqual(self.cutter.read(), graphtec.RESP_REGMARK_OK)
        self.assertEqual(self.cutter.read(), b"0\x03")

    def test_missing_reply_times_out(self):
        with self.assertRaisesRegex(transport.TransportError, "timed out"):
            self.cutter.read(timeout_ms=10)

    def test_disconnected_reply_fails(self):
        self.cutter._client.is_connected = False
        with self.assertRaisesRegex(transport.TransportError, "disconnected"):
            self.cutter.read()

    def test_scan_failure_prevents_path_writes(self):
        self.cutter._on_reply(None, b"1\x03")
        steps = [graphtec.Step("expect", expect=graphtec.RESP_REGMARK_OK),
                 graphtec.Step("write", b"paths")]
        with self.assertRaises(transport.TransportError):
            self.cutter.run(steps)
        self.assertEqual(self.cutter._client.write_gatt_char.await_count, 0)

    def test_interrupted_write_is_not_replayed(self):
        client = self.cutter._client
        client.write_gatt_char.side_effect = [None, OSError("disconnected")]
        with self.assertRaises(transport.TransportError):
            self.cutter.write(b"x" * 60)
        self.assertEqual(client.write_gatt_char.await_count, 2)

    def test_detects_reference_models_without_confusing_close_names(self):
        for name, pid in (("CAMEO4-ABCD", 0x1137), ("Cameo 4 Plus", 0x1138),
                          ("CAMEO 5 ALPHA-9876", 0x0001), ("Cameo 5 Alpha Plus", 0x0002),
                          ("Portrait4-C250", 0x113F), ("Portrait 2", 0x1132),
                          ("Cameo Pro Mark2", 0x1146)):
            with self.subTest(name=name):
                self.assertEqual(ble.advertised_model(name).product_id, pid)
        self.assertIsNone(ble.advertised_model("CAMEO 40"))

    def test_scan_accepts_advertised_name_or_device_name_and_preserves_address(self):
        matched = mock.Mock(address="ab:cd:ef")
        matched.name = "Cameo 5"
        other = mock.Mock(address="xx:yy:zz")
        other.name = "Speaker"
        bleak = mock.Mock()
        bleak.BleakScanner.discover = mock.AsyncMock(return_value={
            matched.address: (matched, mock.Mock(local_name=None)),
            other.address: (other, mock.Mock(local_name="Speaker")),
        })
        with mock.patch.object(ble, "_bleak", return_value=bleak):
            self.assertEqual(ble.discover(), [("AB:CD:EF", "Cameo 5")])

    def test_unsupported_model_fails_before_discovery(self):
        device = graphtec.device_by_name("Silhouette Cameo 2")
        with mock.patch.object(ble, "_scan", new_callable=mock.AsyncMock) as scan:
            with self.assertRaisesRegex(transport.TransportError, "unavailable.*Cameo 2"):
                ble.BleTransport("00:00:00:00:00:00", device)
        scan.assert_not_awaited()

    def test_model_mismatch_fails_before_connecting(self):
        address = "00:00:00:00:00:00"
        model = graphtec.device_by_name("Silhouette Cameo 4 Plus")
        expected = graphtec.device_by_name("Silhouette Cameo 4")
        with mock.patch.object(ble, "_scan", new_callable=mock.AsyncMock,
                               return_value={address: (mock.Mock(), model.name, model)}), \
             mock.patch.object(ble, "_bleak") as bleak:
            with self.assertRaisesRegex(transport.TransportError, "advertises.*Cameo 4 Plus"):
                ble.BleTransport(address, expected)
        bleak.assert_not_called()

    def test_firmware_is_checked_against_advertised_model(self):
        model = graphtec.device_by_name("Silhouette Cameo 5 Alpha")
        address = "00:00:00:00:00:00"
        with mock.patch.object(ble.BleTransport, "_connect", new_callable=mock.AsyncMock,
                               return_value=model), \
             mock.patch.object(ble.BleTransport, "write") as write, \
             mock.patch.object(ble.BleTransport, "read", return_value=b"CAMEO 5 ALPHA V1.02\x03"):
            with ble.BleTransport(address) as link:
                self.assertEqual(link.device, model)
                self.assertTrue(link.device.quad_regmarks)
            write.assert_called_once_with(b"FG\x03")

    def test_wrong_firmware_closes_connection(self):
        model = graphtec.device_by_name("Silhouette Cameo 4")
        with mock.patch.object(ble.BleTransport, "_connect", new_callable=mock.AsyncMock,
                               return_value=model), \
             mock.patch.object(ble.BleTransport, "write"), \
             mock.patch.object(ble.BleTransport, "read", return_value=b"CAMEO 4 PLUS V1.05\x03"), \
             mock.patch.object(ble.BleTransport, "close", autospec=True,
                               side_effect=ble.BleTransport.close) as close:
            with self.assertRaisesRegex(transport.TransportError, "Expected Silhouette Cameo 4"):
                ble.BleTransport("00:00:00:00:00:00")
            self.assertEqual(close.call_count, 1)
