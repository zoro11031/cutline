"""One Bluetooth Classic connection held across actions, as Studio does.

Measured 2026-11-21: an idle RFCOMM link to the Cameo 4 answered status in
~20 ms after 10, 30 and 60 s idle, while opening a new session took 3-55 s
when the cutter stalled. The front ends open a link per action, so
cutters.open_link lends one held link instead.
"""
import threading
import unittest
from unittest import mock

from cutline import cutters, graphtec, transport

CAMEO4 = graphtec.device_by_name("Silhouette Cameo 4")
PORTRAIT3 = graphtec.device_by_name("Silhouette Portrait 3")
LINK = ("bluetooth", "AA:BB:CC:00:11:22", 1)


def fake(device=CAMEO4, alive=True):
    link = mock.MagicMock(device=device, can_read=True)
    link.status.return_value = "ready" if alive else None
    if not alive:
        link.status.side_effect = transport.TransportError("Bluetooth Classic cutter disconnected.")
    return link


class TestHeldLink(unittest.TestCase):
    def setUp(self):
        self.addCleanup(cutters.close_held)
        timer = mock.patch.object(cutters.threading, "Timer")
        self.timer = timer.start()
        self.addCleanup(timer.stop)

    def open(self, *links):
        return mock.patch.object(transport, "open_transport", side_effect=list(links))

    def test_second_action_reuses_the_open_link(self):
        link = fake()
        with self.open(link) as opener:
            with cutters.open_link(LINK) as first:
                self.assertIs(first, link)
            with cutters.open_link(LINK, CAMEO4) as second:
                self.assertIs(second, link)
        self.assertEqual(opener.call_count, 1)
        self.assertEqual(link.close.call_count, 0)
        self.assertEqual(opener.call_args, mock.call("bluetooth", device=None, bluetooth_id=LINK[1],
                                                     bluetooth_channel=1))

    def test_a_dead_held_link_is_replaced_before_use(self):
        dead, fresh = fake(), fake()
        with self.open(dead, fresh) as opener:
            with cutters.open_link(LINK):
                pass
            dead.status.side_effect = transport.TransportError("Bluetooth Classic cutter disconnected.")
            with cutters.open_link(LINK) as cutter:
                self.assertIs(cutter, fresh)
        self.assertEqual(opener.call_count, 2)
        self.assertEqual(dead.close.call_count, 1)

    def test_a_failure_during_an_action_drops_the_link(self):
        broken, fresh = fake(), fake()
        with self.open(broken, fresh) as opener:
            with self.assertRaises(transport.TransportError):
                with cutters.open_link(LINK):
                    broken.status.side_effect = transport.TransportError("Bluetooth Classic cutter disconnected.")
                    raise transport.TransportError("Bluetooth Classic write failed.")
            self.assertEqual(broken.close.call_count, 1)
            with cutters.open_link(LINK) as cutter:
                self.assertIs(cutter, fresh)
        self.assertEqual(opener.call_count, 2)

    def test_a_refusal_on_a_link_that_still_answers_keeps_it(self):
        # The front ends raise TransportError for "no media is loaded" and
        # "not ready"; the link itself is fine and must not be reopened.
        link = fake()
        with self.open(link) as opener:
            with self.assertRaisesRegex(transport.TransportError, "no media"):
                with cutters.open_link(LINK):
                    raise transport.TransportError("the cutter says no media is loaded")
            with cutters.open_link(LINK) as cutter:
                self.assertIs(cutter, link)
        self.assertEqual(opener.call_count, 1)
        self.assertEqual(link.close.call_count, 0)

    def test_a_non_transport_error_keeps_the_link(self):
        link = fake()
        with self.open(link) as opener:
            with self.assertRaises(ValueError):
                with cutters.open_link(LINK):
                    raise ValueError("not about the link")
            with cutters.open_link(LINK) as cutter:
                self.assertIs(cutter, link)
        self.assertEqual(opener.call_count, 1)
        self.assertEqual(link.close.call_count, 0)

    def test_another_cutter_closes_the_held_one(self):
        first, second = fake(), fake()
        with self.open(first, second):
            with cutters.open_link(LINK):
                pass
            with cutters.open_link(("bluetooth", "11:22:33:44:55:66", 1)) as cutter:
                self.assertIs(cutter, second)
        self.assertEqual(first.close.call_count, 1)

    def test_a_different_model_is_refused_and_the_link_kept(self):
        link = fake()
        with self.open(link):
            with cutters.open_link(LINK):
                pass
            with self.assertRaisesRegex(transport.TransportError, "Cameo 4"):
                with cutters.open_link(LINK, PORTRAIT3):
                    pass
            with cutters.open_link(LINK, CAMEO4) as cutter:
                self.assertIs(cutter, link)
        self.assertEqual(link.close.call_count, 0)

    def test_idle_timer_closes_the_link_and_a_new_action_cancels_it(self):
        link = fake()
        with self.open(link, fake()) as opener:
            with cutters.open_link(LINK):
                pass
            delay, closer, args = self.timer.call_args.args
            self.assertEqual(delay, cutters.IDLE_CLOSE_S)
            with cutters.open_link(LINK):
                self.assertEqual(self.timer.return_value.cancel.call_count, 1)
                # A timer that fires (on its own thread) while the link is lent is ignored.
                fired = threading.Thread(target=closer, args=args)
                fired.start()
                fired.join(5)
                self.assertEqual(link.close.call_count, 0)
            _, closer, args = self.timer.call_args.args
            closer(*args)
            self.assertEqual(link.close.call_count, 1)
            with cutters.open_link(LINK):
                pass
        self.assertEqual(opener.call_count, 2)

    def test_one_borrower_at_a_time(self):
        link = fake()
        entered, release, order = threading.Event(), threading.Event(), []

        def first():
            with cutters.open_link(LINK):
                order.append("first in")
                entered.set()
                release.wait(5)
                order.append("first out")

        def second():
            entered.wait(5)
            with cutters.open_link(LINK):
                order.append("second in")

        with self.open(link):
            threads = [threading.Thread(target=first), threading.Thread(target=second)]
            for thread in threads:
                thread.start()
            entered.wait(5)
            release.set()
            for thread in threads:
                thread.join(5)
        self.assertEqual(order, ["first in", "first out", "second in"])

    def test_usb_and_le_are_not_held(self):
        for connection in (("ble", "local-id"),):
            with self.subTest(connection=connection):
                link = fake()
                with self.open(link, fake()) as opener:
                    with cutters.open_link(connection):
                        pass
                    with cutters.open_link(connection):
                        pass
                self.assertEqual(opener.call_count, 2)
                self.assertEqual(link.__exit__.call_count, 1)

    def test_close_held_closes_and_forgets(self):
        link = fake()
        with self.open(link, fake()) as opener:
            with cutters.open_link(LINK):
                pass
            cutters.close_held()
            self.assertEqual(link.close.call_count, 1)
            with cutters.open_link(LINK):
                pass
        self.assertEqual(opener.call_count, 2)


if __name__ == "__main__":
    unittest.main()
