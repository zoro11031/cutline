"""Opaque credential-pipe clients must still reserve their RDP socket."""
import subprocess
import unittest
from unittest import mock
from cut_proxies import winboat


class TestHiddenRdp(unittest.TestCase):
    def check(self, sockets, port=47301, command='/usr/bin/xfreerdp3 /args-from:fd:3'):
        def run(args, **kwargs):
            return subprocess.CompletedProcess(args, 0,
                stdout=f'123 {command}\n' if args[0] == 'pgrep' else sockets,
                stderr='')
        with mock.patch.object(winboat.subprocess, 'run', side_effect=run):
            return winboat.rdp_busy(port)

    def test_connected_pipe_client_blocks(self):
        self.assertIn('123', self.check('ESTAB 0 0 127.0.0.1:40000 127.0.0.1:47301 users:(("xfreerdp3",pid=123,fd=20))'))

    def test_other_port_is_free(self):
        self.assertEqual(self.check('ESTAB 0 0 127.0.0.1:40000 127.0.0.1:47302 users:(("xfreerdp3",pid=123,fd=20))'), '')

    def test_other_pid_is_free(self):
        self.assertEqual(self.check('ESTAB 0 0 127.0.0.1:40000 127.0.0.1:47301 users:(("xfreerdp3",pid=1234,fd=20))'), '')

    def test_wrapper_is_free(self):
        self.assertEqual(self.check('', command='/bin/sh -c xfreerdp3 /args-from:fd:3'), '')

    def test_hidden_client_is_never_reaped(self):
        with mock.patch.object(winboat.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, stdout='123 /usr/bin/xfreerdp3 /args-from:fd:3\n')):
            self.assertEqual(winboat.our_stale_sessions(47301), [])

    def test_local_port_does_not_count_as_peer(self):
        self.assertEqual(self.check('ESTAB 0 0 127.0.0.1:47301 127.0.0.1:40000 users:(("xfreerdp3",pid=123,fd=20))'), '')

    def test_socket_inspection_failure_refuses_launch(self):
        listing = subprocess.CompletedProcess([], 0, stdout='123 /usr/bin/xfreerdp3 /args-from:fd:3\n')
        for failure in (OSError('unavailable'), subprocess.CompletedProcess([], 1, stdout='')):
            with self.subTest(failure=type(failure).__name__):
                with mock.patch.object(winboat.subprocess, 'run', side_effect=[listing, failure]):
                    self.assertIn('cannot inspect', winboat.rdp_busy(47301))
