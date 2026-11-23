import errno
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
        # No test may reach the real bluetoothctl: a link reset is real, and
        # so are an inquiry and a pairing.
        patcher = mock.patch.object(rfcomm, "_run", return_value="")
        self.bluez = patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(rfcomm, "_call", return_value=(0, "", ""))
        self.bluez_call = patcher.start()
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

    def test_connect_waits_for_a_slow_rfcomm_open(self):
        # Studio's own capture (2026-11-21): the Cameo 4 took 18.35 s to
        # answer the RFCOMM multiplexer SABM. The connect must outlast that.
        self.socket.recv.side_effect = [b"CAMEO 4 V1.20\x03"]
        with rfcomm.ClassicTransport(self.address):
            pass
        connect_timeout = self.socket.settimeout.call_args_list[0].args[0]
        self.assertGreater(connect_timeout, 18.35)

    def test_link_still_closing_is_retried_on_a_fresh_socket(self):
        sockets = [mock.MagicMock(), mock.MagicMock(), mock.MagicMock()]
        sockets[0].connect.side_effect = OSError(errno.EBUSY, "Device or resource busy")
        sockets[1].connect.side_effect = OSError(errno.EHOSTDOWN, "Host is down")
        sockets[2].recv.side_effect = [b"CAMEO 4 V1.20\x03"]
        with mock.patch.object(rfcomm.socket, "socket", side_effect=sockets), \
             mock.patch.object(rfcomm.time, "sleep") as sleep:
            with rfcomm.ClassicTransport(self.address) as cutter:
                self.assertEqual(cutter.firmware_version(), "CAMEO 4 V1.20")
        for failed in sockets[:2]:
            self.assertEqual(failed.close.call_count, 1)
        self.assertEqual(sleep.call_count, 2)

    def test_a_cutter_that_does_not_answer_is_not_detected_after_two_pages(self):
        # Measured 2026-11-23 with the cutter off (or away, or its Bluetooth
        # off: the radio cannot tell these apart): each connect failed with
        # EHOSTDOWN after the 5.12 s page timeout. A closing link fails fast.
        for page_s, connects in ((5.13, rfcomm.PAGE_ATTEMPTS), (0.05, rfcomm.CONNECT_ATTEMPTS)):
            clock = iter(x * page_s for x in range(100))
            sockets = [mock.MagicMock() for _ in range(rfcomm.CONNECT_ATTEMPTS)]
            for each in sockets:
                each.connect.side_effect = OSError(errno.EHOSTDOWN, "Host is down")
            with self.subTest(page_s=page_s), \
                 mock.patch.object(rfcomm.socket, "socket", side_effect=sockets) as made, \
                 mock.patch.object(rfcomm.time, "monotonic", side_effect=lambda: next(clock)), \
                 mock.patch.object(rfcomm.time, "sleep"):
                with self.assertRaisesRegex(transport.NotFound, "No cutter detected"):
                    rfcomm.ClassicTransport(self.address)
                self.assertEqual(made.call_count, connects)

    def test_a_session_that_never_answers_is_reopened(self):
        # Captured on Linux 2026-11-21: a session opened soon after another
        # completes RFCOMM but the cutter never grants credits, so not even
        # FG goes out. It frees the stuck link ~20 s later (EBUSY until then).
        stalled, still_held, healthy = mock.MagicMock(), mock.MagicMock(), mock.MagicMock()
        stalled.recv.side_effect = TimeoutError()
        still_held.connect.side_effect = OSError(errno.EBUSY, "Device or resource busy")
        healthy.recv.side_effect = [b"CAMEO 4 V1.20\x03"]
        with mock.patch.object(rfcomm.socket, "socket", side_effect=[stalled, still_held, healthy]), \
             mock.patch.object(rfcomm.time, "sleep"):
            with rfcomm.ClassicTransport(self.address) as cutter:
                self.assertEqual(cutter.firmware_version(), "CAMEO 4 V1.20")
        self.assertEqual(stalled.close.call_count, 1)
        first_reply_wait = stalled.settimeout.call_args_list[-1].args[0]
        self.assertLessEqual(first_reply_wait, rfcomm.FIRST_REPLY_TIMEOUT_MS / 1000)

    def test_a_session_dropped_before_its_first_reply_is_reopened(self):
        # Seen 2026-11-22 right after pairing a Cameo 4: the session opened,
        # then FG failed with ECONNABORTED. BlueZ drops the ACL it paired on
        # a few seconds after pairing, and the session went down with it.
        dropped, healthy = mock.MagicMock(), mock.MagicMock()
        dropped.recv.side_effect = ConnectionAbortedError(errno.ECONNABORTED, "Software caused connection abort")
        healthy.recv.side_effect = [b"CAMEO 4 V1.20\x03"]
        with mock.patch.object(rfcomm.sys, "platform", "linux"), \
             mock.patch.object(rfcomm.socket, "socket", side_effect=[dropped, healthy]), \
             mock.patch.object(rfcomm.time, "sleep"):
            with rfcomm.ClassicTransport(self.address) as cutter:
                self.assertEqual(cutter.firmware_version(), "CAMEO 4 V1.20")
        self.assertEqual(dropped.close.call_count, 1)
        self.assertEqual(self.resets(), [])   # the link was going down by itself

    def test_a_session_dropped_every_time_gives_up(self):
        self.socket.recv.side_effect = ConnectionAbortedError(errno.ECONNABORTED, "Software caused connection abort")
        with mock.patch.object(rfcomm.time, "sleep"), \
             self.assertRaisesRegex(transport.TransportError, "dropped the Bluetooth connection"):
            rfcomm.ClassicTransport(self.address)
        self.assertEqual(self.socket.connect.call_count, rfcomm.OPEN_ATTEMPTS)

    def test_a_drop_after_the_session_opened_is_reported_not_retried(self):
        self.socket.recv.side_effect = [b"CAMEO 4 V1.20\x03",
                                        ConnectionAbortedError(errno.ECONNABORTED, "Software caused connection abort")]
        with rfcomm.ClassicTransport(self.address) as cutter:
            with self.assertRaisesRegex(transport.TransportError, "read failed"):
                cutter.read()
        self.assertEqual(self.socket.connect.call_count, 1)

    def test_a_cutter_that_never_answers_gives_up_with_advice(self):
        self.socket.recv.side_effect = TimeoutError()
        with mock.patch.object(rfcomm.time, "sleep"), \
             self.assertRaisesRegex(transport.TransportError, "did not answer.*off and on"):
            rfcomm.ClassicTransport(self.address)
        self.assertEqual(self.socket.connect.call_count, rfcomm.OPEN_ATTEMPTS)

    def resets(self):
        return [call.args[0] for call in self.bluez.call_args_list
                if call.args[0][:2] == ["bluetoothctl", "disconnect"]]

    def test_an_orphaned_session_is_cleared_by_resetting_the_link(self):
        # Captured 2026-11-21: after a stall the kernel kept an RFCOMM session
        # owned by no process; connects then timed out with nothing sent,
        # for minutes, until the classic link was dropped.
        orphaned, healthy = mock.MagicMock(), mock.MagicMock()
        orphaned.connect.side_effect = TimeoutError("timed out")
        healthy.recv.side_effect = [b"CAMEO 4 V1.20\x03"]
        with mock.patch.object(rfcomm.sys, "platform", "linux"), \
             mock.patch.object(rfcomm.socket, "socket", side_effect=[orphaned, healthy]), \
             mock.patch.object(rfcomm.time, "sleep"):
            with rfcomm.ClassicTransport(self.address) as cutter:
                self.assertEqual(cutter.firmware_version(), "CAMEO 4 V1.20")
        self.assertEqual(self.resets(), [["bluetoothctl", "disconnect", self.address]])

    def test_a_stalled_session_resets_the_link_before_reopening(self):
        stalled, healthy = mock.MagicMock(), mock.MagicMock()
        stalled.recv.side_effect = TimeoutError()
        healthy.recv.side_effect = [b"CAMEO 4 V1.20\x03"]
        with mock.patch.object(rfcomm.sys, "platform", "linux"), \
             mock.patch.object(rfcomm.socket, "socket", side_effect=[stalled, healthy]), \
             mock.patch.object(rfcomm.time, "sleep"):
            with rfcomm.ClassicTransport(self.address):
                pass
        self.assertEqual(self.resets(), [["bluetoothctl", "disconnect", self.address]])

    def test_busy_never_resets_another_programs_link(self):
        # EBUSY at once means another connection holds the cutter, perhaps
        # mid-cut in a Cutline window. Dropping the link would cut it off.
        self.socket.connect.side_effect = OSError(errno.EBUSY, "Device or resource busy")
        with mock.patch.object(rfcomm.sys, "platform", "linux"), \
             mock.patch.object(rfcomm.time, "sleep"), \
             self.assertRaisesRegex(transport.TransportError, "in use"):
            rfcomm.ClassicTransport(self.address)
        self.assertEqual(self.resets(), [])

    def test_a_link_that_still_times_out_after_one_reset_gives_up(self):
        self.socket.connect.side_effect = TimeoutError("timed out")
        with mock.patch.object(rfcomm.sys, "platform", "linux"), \
             mock.patch.object(rfcomm.time, "sleep"), \
             self.assertRaisesRegex(transport.TransportError, "timed out"):
            rfcomm.ClassicTransport(self.address)
        self.assertEqual(len(self.resets()), 1)
        self.assertEqual(self.socket.connect.call_count, 2)

    def test_no_link_reset_off_linux(self):
        self.socket.connect.side_effect = TimeoutError("timed out")
        with mock.patch.object(rfcomm.sys, "platform", "win32"), \
             mock.patch.object(rfcomm.time, "sleep"), \
             self.assertRaises(transport.TransportError):
            rfcomm.ClassicTransport(self.address)
        self.assertEqual(self.resets(), [])

    def test_busy_retries_outlast_the_cutters_release_of_a_stuck_link(self):
        self.assertGreater((rfcomm.CONNECT_ATTEMPTS - 1) * rfcomm.CONNECT_RETRY_DELAY_S, 20.3)

    def test_other_connect_failures_are_not_retried(self):
        self.socket.connect.side_effect = OSError(errno.ECONNREFUSED, "Connection refused")
        with mock.patch.object(rfcomm.time, "sleep") as sleep, \
             self.assertRaisesRegex(transport.TransportError, "RFCOMM channel 1"):
            rfcomm.ClassicTransport(self.address)
        self.assertEqual(self.socket.connect.call_count, 1)
        self.assertEqual(sleep.call_count, 0)

    def test_a_link_that_stays_busy_gives_up(self):
        self.socket.connect.side_effect = OSError(errno.EBUSY, "Device or resource busy")
        with mock.patch.object(rfcomm.time, "sleep"), \
             self.assertRaises(transport.TransportError) as raised:
            rfcomm.ClassicTransport(self.address)
        self.assertEqual(self.socket.connect.call_count, rfcomm.CONNECT_ATTEMPTS)
        # Busy is not a pairing problem: say what it is and what to do.
        message = str(raised.exception)
        self.assertIn("in use", message)
        self.assertIn("Cutline window", message)
        self.assertNotIn("paired", message)

    def test_bluez_5_88_bredr_fields_are_read(self):
        # bluetoothctl 5.88 prints "BREDR.Connected", not "BR/EDR.Connected".
        for connected, expected in (("yes", [self.address]), ("no", [])):
            output = {("devices", "Paired"): f"Device {self.address} CAMEO 4-1234",
                      ("info", self.address): f"BREDR.Paired: yes\nBREDR.Connected: {connected}"}
            with self.subTest(connected=connected), \
                 mock.patch.object(rfcomm.sys, "platform", "linux"), \
                 mock.patch.object(rfcomm, "_run", side_effect=lambda a, o=output: o.get(tuple(a[1:]), "")):
                self.assertEqual([r.identifier for r in rfcomm.paired_candidates(connected=True)], expected)

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


class TestPairing(unittest.TestCase):
    """Pairing from inside Cutline, as Studio does. Measured 2026-11-22 on a
    Cameo 4 with BlueZ 5.88: a 13 s Classic inquiry heard the unpaired cutter,
    and Device1.Pair bonded it by Just Works in about 5 s with no prompt. A
    plain RFCOMM connect also pairs, but leaves no key ("Bonded: no"), so the
    cutter never reappears in the paired list."""
    address = "AA:BB:CC:00:11:22"
    path = "/org/bluez/hci0/dev_AA_BB_CC_00_11_22"

    def setUp(self):
        self.real_run, self.real_call = rfcomm._run, rfcomm._call
        patcher = mock.patch.object(rfcomm.sys, "platform", "linux")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.devices = ("Device AA:BB:CC:00:11:22 CAMEO 4-1234\n"
                        "Device C0:BB:CC:00:11:22 CAMEO 4-1234\n"
                        "Device AA:BB:CC:99:88:77 Headphones\n"
                        "Device AA:BB:CC:55:55:55 CAMEO 4-5555\n"
                        "Device AA:BB:CC:77:77:77 CAMEO 4-7777")
        self.info = {
            self.address: "Device AA:BB:CC:00:11:22 (public)\n\tClass: 0x00100104\n"
                          "\tPaired: no\n\tBonded: no\n\tBREDR.Paired: no\n\tBREDR.Bonded: no",
            "C0:BB:CC:00:11:22": "Device C0:BB:CC:00:11:22 (random)\n\tPaired: no\n\tLE.Paired: no",
            "AA:BB:CC:99:88:77": "Device AA:BB:CC:99:88:77 (public)\n\tClass: 0x00240404\n\tPaired: no",
            "AA:BB:CC:55:55:55": "Device AA:BB:CC:55:55:55 (public)\n\tClass: 0x00100104\n"
                                 "\tPaired: yes\n\tBonded: yes",
            # An LE record with a public address: only Class marks Classic.
            "AA:BB:CC:77:77:77": "Device AA:BB:CC:77:77:77 (public)\n\tPaired: no\n\tLE.Paired: no",
        }
        self.tree = [self.path]
        patcher = mock.patch.object(rfcomm, "_run", side_effect=self.run_command)
        self.bluez = patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(rfcomm, "_call", return_value=(0, "", ""))
        self.call = patcher.start()
        self.addCleanup(patcher.stop)

    def run_command(self, args):
        if args == ["bluetoothctl", "devices"]:
            return self.devices
        if args[:2] == ["bluetoothctl", "info"]:
            return self.info.get(args[2], f"Device {args[2]} not available")
        if args == ["busctl", "--list", "tree", "org.bluez"]:
            return "\n".join(["/org", "/org/bluez", "/org/bluez/hci0", *self.tree])
        return ""

    def calls(self, *prefix):
        return [c.args[0] for c in self.call.call_args_list if c.args[0][:len(prefix)] == list(prefix)]

    def test_inquiry_is_studios_and_offers_only_unpaired_classic_cutters(self):
        found = rfcomm.nearby_candidates()
        self.assertEqual([r.identifier for r in found], [self.address])
        (scan,) = self.calls("bluetoothctl")
        self.assertEqual(scan[-2:], ["scan", "bredr"])
        # BluetoothIO.dll: cTimeoutMultiplier 10 x 1.28 s.
        self.assertGreaterEqual(int(scan[scan.index("--timeout") + 1]), 12.8)

    def test_no_inquiry_off_linux(self):
        with mock.patch.object(rfcomm.sys, "platform", "win32"):
            self.assertEqual(rfcomm.nearby_candidates(), [])
        self.assertEqual(self.call.call_count, 0)

    def test_bonded_reads_bluez_both_ways(self):
        self.assertIs(rfcomm.bonded(self.address), False)
        self.assertIs(rfcomm.bonded("AA:BB:CC:55:55:55"), True)
        self.assertIs(rfcomm.bonded("AA:BB:CC:12:34:56"), False)   # BlueZ has never seen it
        # The state a plain RFCOMM connect leaves: paired, but no key kept.
        self.info[self.address] = "Device AA:BB:CC:00:11:22 (public)\n\tPaired: yes\n\tBonded: no"
        self.assertIs(rfcomm.bonded(self.address), False)
        self.info[self.address] = "Device AA:BB:CC:00:11:22 (public)\n\tPaired: yes"   # BlueZ before Bonded
        self.assertIs(rfcomm.bonded(self.address), True)
        self.bluez.side_effect = lambda args: ""                    # no bluetoothctl at all
        self.assertIsNone(rfcomm.bonded(self.address))
        with mock.patch.object(rfcomm.sys, "platform", "win32"):
            self.assertIsNone(rfcomm.bonded(self.address))

    def test_pair_bonds_through_bluez(self):
        rfcomm.pair(self.address.lower())
        (pair,) = self.calls("busctl")
        self.assertEqual(pair[-4:], ["org.bluez", self.path, "org.bluez.Device1", "Pair"])
        self.assertEqual(self.calls("bluetoothctl"), [])   # known to BlueZ: no second search

    def test_already_paired_is_not_an_error(self):
        self.call.return_value = (1, "", "Call failed: Already Exists\n")
        rfcomm.pair(self.address)
        self.assertEqual(len(self.calls("busctl")), 1)

    def test_failed_pairing_says_why(self):
        self.call.return_value = (1, "", "Call failed: Authentication Rejected\n")
        with self.assertRaises(transport.TransportError) as raised:
            rfcomm.pair(self.address)
        self.assertNotIsInstance(raised.exception, transport.NotFound)
        for part in ("Could not pair", self.address, "Authentication Rejected"):
            self.assertIn(part, str(raised.exception))

    def test_pairing_nothing_answered_is_not_detected(self):
        self.call.return_value = (1, "", "Call failed: Page Timeout\n")
        with self.assertRaisesRegex(transport.NotFound, f"No cutter detected at {self.address}"):
            rfcomm.pair(self.address)

    def test_a_cutter_forgotten_since_the_search_is_searched_for_again(self):
        # BlueZ drops an unpaired device about 30 s after discovery stops.
        self.tree = []

        def call(args, timeout):
            if args[0] == "bluetoothctl":
                self.tree = [self.path]
            return 0, "", ""

        self.call.side_effect = call
        rfcomm.pair(self.address)
        self.assertEqual(len(self.calls("bluetoothctl")), 1)
        self.assertEqual(len(self.calls("busctl")), 1)

    def test_a_cutter_not_heard_again_is_not_paired(self):
        self.tree = []
        with self.assertRaisesRegex(transport.NotFound, "No cutter detected"):
            rfcomm.pair(self.address)
        self.assertEqual(self.calls("busctl"), [])

    def test_off_linux_pairing_is_left_to_the_system(self):
        with mock.patch.object(rfcomm.sys, "platform", "win32"), \
             self.assertRaisesRegex(transport.TransportError, "system's Bluetooth settings"):
            rfcomm.pair(self.address)
        self.assertEqual(self.call.call_count, 0)

    def test_coloured_bluetoothctl_lines_are_read(self):
        # bluetoothctl 5.88 colours devices it is not connected to, even into
        # a pipe; a coloured line would not parse as "Device <address>".
        self.bluez.side_effect = None
        coloured = mock.Mock(stdout="\x1b[1;30mDevice AA:BB:CC:55:55:55 CAMEO 4-5555\x1b[0m\n", stderr="")
        with mock.patch.object(rfcomm, "_run", self.real_run), mock.patch.object(rfcomm, "_call", self.real_call), \
             mock.patch.object(rfcomm.subprocess, "run", return_value=coloured):
            self.assertEqual(rfcomm._run(["bluetoothctl", "devices"]),
                             "Device AA:BB:CC:55:55:55 CAMEO 4-5555\n")
            self.assertEqual(rfcomm._call(["bluetoothctl", "devices"], 5)[1],
                             "Device AA:BB:CC:55:55:55 CAMEO 4-5555\n")


class TestPairBeforeConnect(unittest.TestCase):
    """The transport bonds a cutter BlueZ holds no key for, then connects."""
    address = TestClassic.address
    setUp = TestClassic.setUp

    def connect_with(self, bonded):
        order = mock.MagicMock()
        self.socket.connect.side_effect = lambda *a: order.connect()
        self.socket.recv.side_effect = [b"CAMEO 4 V1.20\x03"]
        with mock.patch.object(rfcomm, "bonded", return_value=bonded), \
             mock.patch.object(rfcomm, "pair", side_effect=lambda a: order.pair(a)):
            with rfcomm.ClassicTransport(self.address):
                pass
        return [c[0] for c in order.mock_calls]

    def test_an_unbonded_cutter_is_paired_first(self):
        self.assertEqual(self.connect_with(False), ["pair", "connect"])

    def test_a_bonded_or_unknown_cutter_is_not_paired(self):
        for bonded in (True, None):
            with self.subTest(bonded=bonded):
                self.assertEqual(self.connect_with(bonded), ["connect"])

    def test_a_failed_pairing_does_not_connect(self):
        with mock.patch.object(rfcomm, "bonded", return_value=False), \
             mock.patch.object(rfcomm, "pair", side_effect=transport.TransportError("Could not pair")), \
             self.assertRaisesRegex(transport.TransportError, "Could not pair"):
            rfcomm.ClassicTransport(self.address)
        self.assertEqual(self.socket.connect.call_count, 0)


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
