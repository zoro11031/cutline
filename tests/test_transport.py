import types
import unittest
from unittest import mock

from cut_proxies import graphtec, job, transport


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

    def test_wait_done_checks_status_after_the_park_command(self):
        events = []
        self.cutter.can_read = True
        park = graphtec.frame("SO0")
        self.cutter.write.side_effect = lambda data: events.append(data) or len(data)
        self.cutter.status.side_effect = lambda: events.append("status") or "ready"
        self.assertEqual(self.cutter.run([graphtec.Step("write", park)], wait_done=True), len(park))
        self.assertEqual(events, [park, "status"])
        self.cutter.can_read = False
        with self.assertRaisesRegex(transport.TransportError, "readable"):
            self.cutter.run([graphtec.Step("write", park)], wait_done=True)
        self.assertEqual(events, [park, "status"])

    def test_track_enhancing_waits_for_ready_before_sending_a_scan(self):
        roller = graphtec.Step("write", graphtec.frame("FY0"), wait_ready=True)
        scan = graphtec.Step("write", graphtec.frame("TB123,100,100,0,0"))
        self.cutter.status.return_value = "unloaded"
        with self.assertRaisesRegex(transport.TransportError, "unloaded"):
            self.cutter.run([roller, scan])
        self.cutter.write.assert_called_once_with(roller.data)
        self.cutter.write.reset_mock()
        self.cutter.status.return_value = "ready"
        self.assertEqual(self.cutter.run([roller, scan]), len(roller.data) + len(scan.data))
        self.assertEqual(self.cutter.write.call_args_list, [mock.call(roller.data), mock.call(scan.data)])

    def test_letter_track_enhancing_blocks_cut_writes_until_registration_succeeds(self):
        checked = job.build(sheet="Letter", portrait=False, offset_mm=(0, 0),
                            blade_options={"track_enhancing": True})
        self.assertFalse(checked.blocked)
        prefix = checked.registration_steps()
        roller = graphtec.frame("FY0")
        clip = graphtec.frame((r"\0,0", "Z3918,5188"))
        for result in (graphtec.RESP_REGMARK_FAIL, graphtec.RESP_REGMARK_OK):
            with self.subTest(result=result):
                events = []
                states = iter(("moving", "ready"))
                self.cutter.write.side_effect = lambda data: events.append(data) or len(data)

                def status():
                    state = next(states, "ready")
                    events.append(state)
                    return state

                self.cutter.status.side_effect = status
                self.cutter.read = mock.Mock(side_effect=lambda **_: events.append("scan reply") or result)
                with mock.patch.object(transport.time, "sleep"):
                    if result == graphtec.RESP_REGMARK_FAIL:
                        with self.assertRaises(transport.RegistrationFailed):
                            self.cutter.run(checked.steps)
                    else:
                        self.assertEqual(self.cutter.run(checked.steps), len(checked.stream))

                roll_at = events.index(roller)
                self.assertEqual(events[roll_at + 1:roll_at + 3], ["moving", "ready"])
                self.assertEqual([e for e in events[:events.index("scan reply")] if isinstance(e, bytes)],
                                 [step.data for step in prefix if step.kind == "write"])
                if result == graphtec.RESP_REGMARK_FAIL:
                    self.assertEqual(events[-1], "scan reply")
                else:
                    self.assertEqual(events[events.index("scan reply") + 1], clip)

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
