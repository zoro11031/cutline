"""Windows update dialog shared by the two Qt workspaces."""
import os
from threading import Event

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QProgressBar, QVBoxLayout

from . import __version__, config, windows_update
from .qt_controls import button


class UpdateDialog(QDialog):
    progress_changed = Signal(int)

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.cancelled = Event()
        self.running = False
        self.setWindowTitle("Cutline Updates")
        self.setMinimumWidth(360)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Installed version: {__version__}"))
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.hide()
        self.progress_changed.connect(self.progress.setValue)
        layout.addWidget(self.progress)
        controls = QHBoxLayout()
        layout.addLayout(controls)
        controls.addWidget(button("GitHub Releases", self.open_releases))
        controls.addStretch()
        self.close_button = button("Close", self.reject)
        self.primary = button("Check Again", lambda: self.action(), primary=True)
        controls.addWidget(self.close_button)
        controls.addWidget(self.primary)
        self.primary.setDefault(True)
        self.action = self.check
        self.update_release = None
        self.cache = config.CONFIG_DIR / "updates"

    def open_releases(self):
        QDesktopServices.openUrl(QUrl(windows_update.RELEASES))

    def reject(self):
        if self.running:
            self.cancelled.set()
        super().reject()

    def run(self, message, work, done):
        if self.running:
            return
        self.running = True
        self.cancelled.clear()
        self.status.setText(message)
        self.primary.setEnabled(False)

        def finished(result, error):
            self.running = False
            self.primary.setEnabled(True)
            self.close_button.setText("Close")
            self.progress.hide()
            done(result, error)

        self.window.tasks.run(work, finished)

    def check(self):
        self.run("Checking for updates…", lambda: windows_update.check(__version__), self.checked)

    def checked(self, release, error):
        self.update_release = release
        self.action = self.check
        self.primary.setText("Check Again")
        if error:
            self.status.setText(f"Couldn’t check for updates. {error}")
        elif release is None:
            self.status.setText("You’re up to date.")
        else:
            self.status.setText(f"Cutline {release.version} is available. Download size {release.size / 1024**2:.1f} MiB.")
            self.primary.setText("Download Update")
            self.action = self.download

    def download(self):
        release = self.update_release
        self.progress.setValue(0)
        self.progress.show()
        self.close_button.setText("Cancel Download")
        self.run("Downloading update…", lambda: windows_update.download(
            release, self.cache, self.cancelled,
            lambda received: self.progress_changed.emit(received * 100 // release.size)),
            self.downloaded)

    def downloaded(self, path, error):
        if error or path is None:
            self.status.setText(f"Couldn’t download the update. {error}" if error else "Download cancelled.")
            return
        self.path = path
        self.status.setText(f"Cutline {self.update_release.version} is ready to install.")
        self.close_button.setDefault(True)
        self.primary.setText("Quit and Run Setup")
        self.action = self.install

    def install(self):
        self.run("Verifying the installer…", lambda: windows_update.verified(
            self.path, self.update_release), self.verified)

    def verified(self, valid, error):
        if self.cancelled.is_set():
            return
        if not valid or error:
            self.status.setText("The installer could not be verified. Download it again.")
            self.primary.setText("Download Update")
            self.action = self.download
            return
        self.hide()
        self.window.update_setup = self.path
        try:
            if not self.window.close():
                self.show()
        finally:
            self.window.update_setup = None


def launch(path):
    os.startfile(str(path))
