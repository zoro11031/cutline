import hashlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import patch

from cutline import windows_update as updater


class WindowsUpdateTests(unittest.TestCase):
    payload = b"installer bytes"
    name = "Cutline-0.10.0-Windows-x86_64-Setup.exe"
    digest = hashlib.sha256(payload).hexdigest()
    update = updater.Update("0.10.0", name, len(payload), digest)

    def test_release_must_be_newer_and_have_digest(self):
        asset = dict(name=self.name, size=len(self.payload), digest="sha256:" + self.digest)
        for current, assets, expected in (("0.9.0", [asset], self.update), ("0.10.0", [], None),
                                          ("1.0.0", [], None)):
            with self.subTest(current=current), patch.object(
                updater, "request", return_value=io.BytesIO(json.dumps(
                    dict(tag_name="v0.10.0", assets=assets)).encode())
            ):
                self.assertEqual(updater.check(current), expected)
        with patch.object(updater, "request", return_value=io.BytesIO(b'{"tag_name":"v0.10.0"}')):
            with self.assertRaisesRegex(ValueError, "no verifiable Windows installer"):
                updater.check("0.9.0")

    def test_download_verifies_before_publishing_and_reuses_verified_file(self):
        with TemporaryDirectory() as directory:
            cache = Path(directory)
            cancel = Event()
            target = cache / self.name
            for payload in (b"short", b"x" * len(self.payload)):
                with self.subTest(payload=payload), patch.object(updater, "request", return_value=io.BytesIO(payload)):
                    with self.assertRaisesRegex(ValueError, "could not be verified"):
                        updater.download(self.update, cache, cancel, lambda _: None)
                    self.assertEqual(list(cache.iterdir()), [])
            with patch.object(updater, "request", return_value=io.BytesIO(self.payload)):
                self.assertIsNone(updater.download(self.update, cache, cancel, lambda _: cancel.set()))
                self.assertEqual(list(cache.iterdir()), [])
            cancel.clear()
            with patch.object(updater, "request", return_value=io.BytesIO(self.payload)) as request:
                self.assertEqual(updater.download(self.update, cache, cancel, lambda _: None), target)
                self.assertEqual(updater.download(self.update, cache, cancel, lambda _: None), target)
                request.assert_called_once()
                self.assertEqual(target.read_bytes(), self.payload)

try:
    from PySide6.QtGui import QCloseEvent
    from cutline import qt_gui, qt_update
except ImportError:
    qt_gui = None


@unittest.skipIf(qt_gui is None, "Qt is required")
class UpdateHandoffTests(unittest.TestCase):
    def test_setup_respects_the_active_job_and_unsaved_work(self):
        from types import SimpleNamespace
        from unittest.mock import Mock

        for busy, active_job, keep_editing in (
            (True, None, False), (False, object(), False), (False, None, True)
        ):
            with self.subTest(busy=busy, active_job=active_job, keep_editing=keep_editing):
                window = SimpleNamespace(busy=busy, active_job=active_job, update_setup=Path("Setup.exe"),
                                         design=Mock(exporting_stickers=False), message=Mock())
                window.design.may_replace.return_value = not keep_editing
                event = QCloseEvent()
                with patch.object(qt_update, "launch") as launch:
                    qt_gui.Window.closeEvent(window, event)
                    launch.assert_not_called()
                self.assertFalse(event.isAccepted())
