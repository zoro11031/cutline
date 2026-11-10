"""The actual transport plan must be the stream that was verified."""
import types
import unittest
from unittest import mock

from cut_proxies import cli, job

try:
    from cut_proxies import gui
except ImportError:
    gui = None


class ImmediateThread:
    def __init__(self, target, **kwargs):
        self.target = target

    def start(self):
        self.target()


def writes(steps):
    return b''.join(s.data for s in steps if s.kind == 'write')


class TestCliCheckedPlan(unittest.TestCase):
    def test_transport_plan_preserves_calibration(self):
        j = job.build(offset_mm=(0.35, -0.2))
        self.assertFalse(j.blocked)
        self.assertEqual(writes(cli._plan(j)), j.stream)

    def test_transport_plan_preserves_registration_off(self):
        j = job.build(registration=False, offset_mm=(0, 0))
        self.assertFalse(j.blocked)
        self.assertEqual(writes(cli._plan(j)), j.stream)


@unittest.skipUnless(gui is not None, 'PyGObject is not installed')
class TestGuiCheckedPlan(unittest.TestCase):
    def send(self, checked):
        window = types.SimpleNamespace(job=checked, _sending=False,
            cut_button=mock.Mock(), r_auto=mock.Mock(), toast=mock.Mock(),
            rebuild=mock.Mock(), _send_done=mock.Mock())
        window.r_auto.get_active.return_value = True
        transport = mock.MagicMock()
        transport.__enter__.return_value = transport
        transport.status.return_value = 'ready'
        with mock.patch.object(gui.threading, 'Thread', ImmediateThread), \
             mock.patch.object(gui.transport, 'open_transport', return_value=transport), \
             mock.patch.object(gui.GLib, 'idle_add'):
            gui.Window._cut_response(window, None, 'cut', checked)
        return transport

    def test_actual_send_preserves_calibration(self):
        j = job.build(offset_mm=(0.35, -0.2))
        transport = self.send(j)
        self.assertEqual(writes(transport.run.call_args.args[0]), j.stream)

    def test_actual_send_preserves_registration_off(self):
        j = job.build(registration=False, offset_mm=(0, 0))
        transport = self.send(j)
        self.assertEqual(writes(transport.run.call_args.args[0]), j.stream)

    def test_response_rechecks_a_blocked_job(self):
        j = job.build(cutter='Silhouette Portrait', offset_mm=(0, 0))
        self.assertTrue(j.blocked)
        transport = self.send(j)
        transport.run.assert_not_called()
