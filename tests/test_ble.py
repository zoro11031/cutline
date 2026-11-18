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

    def test_model_mismatch_fails_before_connecting(self):
        device = graphtec.device_by_name("Silhouette Portrait 2")
        with self.assertRaisesRegex(transport.TransportError, "requires.*Cameo 4"):
            ble.BleTransport("00:00:00:00:00:00", device)

    def test_wrong_firmware_closes_connection(self):
        with mock.patch.object(ble.BleTransport, "_connect", new_callable=mock.AsyncMock), \
             mock.patch.object(ble.BleTransport, "write"), \
             mock.patch.object(ble.BleTransport, "read", return_value=b"CAMEO 4 PLUS V1.05\x03"), \
             mock.patch.object(ble.BleTransport, "close", autospec=True,
                               side_effect=ble.BleTransport.close) as close:
            with self.assertRaisesRegex(transport.TransportError, "Expected Cameo 4"):
                ble.BleTransport("00:00:00:00:00:00")
            self.assertEqual(close.call_count, 1)
