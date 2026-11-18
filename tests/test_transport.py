import types
import unittest
from unittest import mock

from cut_proxies import graphtec, transport


class TestUsbTransfers(unittest.TestCase):
    class UsbError(OSError):
        pass

    class UsbTimeout(UsbError):
        pass

    def setUp(self):
        self.cutter = transport.PyUsbTransport.__new__(transport.PyUsbTransport)
        self.cutter._usb = types.SimpleNamespace(USBError=self.UsbError,
                                                  USBTimeoutError=self.UsbTimeout)
        self.cutter._in = mock.Mock()
        self.cutter._out = mock.Mock()
        self.cutter._reply_buffer = bytearray()

    def test_short_write_resumes_at_the_confirmed_byte_across_chunks(self):
        payload = bytes(range(256)) * 21
        accepted = bytearray()

        def write(data, timeout):
            n = min(len(data), 1000)
            accepted.extend(data[:n])
            return n

        self.cutter._out.write.side_effect = write
        self.assertEqual(self.cutter.write(payload), len(payload))
        self.assertEqual(accepted, payload)
        self.assertEqual(len(self.cutter._out.write.call_args_list[0].args[0]), 4096)

    def test_failed_transfer_is_reported_without_replaying_it(self):
        for failure in (0, self.UsbTimeout("timeout"), self.UsbError("disconnected")):
            with self.subTest(failure=failure):
                self.cutter._out.write.reset_mock()
                self.cutter._out.write.side_effect = [3, failure]
                with self.assertRaisesRegex(transport.TransportError, "after 3"):
                    self.cutter.write(b"M100,200\x03")
                self.assertEqual(self.cutter._out.write.call_count, 2)
                self.assertEqual(self.cutter._out.write.call_args.args[0], b"0,200\x03")

    def test_read_preserves_partial_and_coalesced_replies(self):
        self.cutter._in.read.side_effect = [self.UsbTimeout(), b"  ", b"  0\x03" + b"0\x03"]
        self.assertEqual(self.cutter.read(), graphtec.RESP_REGMARK_OK)
        self.assertEqual(self.cutter.read(), b"0\x03")
        self.assertEqual(self.cutter._in.read.call_count, 3)

    def test_read_reports_a_disconnected_usb_device_immediately(self):
        self.cutter._in.read.side_effect = self.UsbError("disconnected")
        with self.assertRaisesRegex(transport.TransportError, "USB read failed: disconnected"):
            self.cutter.read()
        self.cutter._in.read.assert_called_once()


class TestPathBatches(unittest.TestCase):
    def setUp(self):
        self.cutter = transport.Transport()
        self.cutter.write = mock.Mock(side_effect=len)
        self.cutter.status = mock.Mock(return_value="ready")
        self.data = graphtec.frame("D123,456") * 250
        self.steps = [graphtec.Step("write", self.data, pace=True),
                      graphtec.Step("write", graphtec.frame("SO0"))]

    def test_splits_only_at_command_ends_and_waits_before_the_next_write(self):
        operations = []
        self.cutter.write.side_effect = lambda data: operations.append(data) or len(data)
        states = iter(("moving", "ready", "ready", "ready"))

        def status():
            state = next(states)
            operations.append(state)
            return state

        self.cutter.status.side_effect = status
        with mock.patch.object(transport.time, "sleep"):
            sent = self.cutter.run(self.steps)
        first, moving, ready, second, ready2, third, ready3, park = operations
        self.assertEqual((moving, ready, ready2, ready3), ("moving", "ready", "ready", "ready"))
        self.assertEqual(first + second + third, self.data)
        self.assertTrue(all(part.endswith(b"\x03") and len(part) <= 1024
                            for part in (first, second, third)))
        self.assertEqual(park, b"SO0\x03")
        self.assertEqual(sent, len(self.data) + len(park))

    def test_unloaded_status_stops_the_later_batches_and_park(self):
        self.cutter.status.return_value = "unloaded"
        with self.assertRaisesRegex(transport.TransportError, "unloaded"):
            self.cutter.run(self.steps)
        self.cutter.write.assert_called_once_with(self.data[:1024 - (1024 % 9)])
        self.cutter.status.assert_called_once()

    def test_moving_has_a_bounded_wait(self):
        self.cutter.status.return_value = "moving"
        with mock.patch.object(transport.time, "monotonic", side_effect=(0, 120)):
            with self.assertRaisesRegex(transport.TransportError, "stayed busy"):
                self.cutter.run(self.steps)
        self.cutter.write.assert_called_once()

    def test_malformed_long_block_is_rejected_before_writing_any_batch(self):
        with self.assertRaisesRegex(transport.TransportError, "incomplete"):
            self.cutter.run([graphtec.Step("write", self.data[:-1], pace=True)])
        self.cutter.write.assert_not_called()
