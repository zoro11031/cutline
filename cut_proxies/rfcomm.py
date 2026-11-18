"""Bluetooth Classic transport using the cutter's RFCOMM serial profile."""

import re
import socket
import subprocess
import sys
import time

from . import graphtec
from .bluetooth import Candidate, confirm_firmware, select_candidate
from .transport import Transport, TransportError

DEFAULT_CHANNEL = 1
_ADDRESS = re.compile(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", re.I)


def available():
    return hasattr(socket, "AF_BLUETOOTH") and hasattr(socket, "BTPROTO_RFCOMM")


def _run(args):
    try:
        options = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)} if sys.platform == "win32" else {}
        return subprocess.run(args, capture_output=True, text=True, timeout=5, **options).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def paired_candidates(*, connected=False):
    if sys.platform.startswith("linux"):
        # BlueZ has used both paired-device commands across releases.
        # Device details distinguish Classic profiles from LE-only records.
        for args in (("devices", "Paired"), ("paired-devices",)):
            for line in _run(["bluetoothctl", *args]).splitlines():
                tag, _, rest = line.strip().partition(" ")
                address, _, name = rest.partition(" ")
                if tag == "Device" and _ADDRESS.fullmatch(address) and name:
                    details = _run(["bluetoothctl", "info", address]).lower()
                    if connected and "br/edr.connected: yes" not in details:
                        continue
                    if ("00001101-0000-1000-8000-00805f9b34fb" in details
                            or ("br/edr.paired: yes" in details)):
                        yield Candidate(address.upper(), name.strip())
    elif sys.platform == "win32" and not connected:
        command = ("Get-PnpDevice -Class Bluetooth -ErrorAction SilentlyContinue | "
                   "ForEach-Object { $_.FriendlyName + '|' + $_.InstanceId }")
        for line in _run(["powershell", "-NoProfile", "-Command", command]).splitlines():
            name, _, instance = line.partition("|")
            match = re.search(r"BTHENUM\\DEV_([0-9a-f]{12})", instance, re.I)
            if match and name:
                mac = match[1].upper()
                yield Candidate(":".join(mac[i:i + 2] for i in range(0, 12, 2)), name.strip())


def _inquiry():
    try:
        import bluetooth as pybluez
        return [Candidate(address.upper(), name or "") for address, name in
                pybluez.discover_devices(duration=8, lookup_names=True) if _ADDRESS.fullmatch(address)]
    except (ImportError, OSError, AttributeError):
        return []


def _candidates(include_all=False):
    seen = {}
    for record in paired_candidates():
        if record.model is not None or record.identifier not in seen:
            seen[record.identifier] = record
    if not any(record.model is not None for record in seen.values()):
        for record in _inquiry():
            if record.model is not None or record.identifier not in seen:
                seen[record.identifier] = record
    return sorted((r for r in seen.values() if include_all or r.model is not None),
                  key=lambda r: (r.name.casefold(), r.identifier))


def discover(include_all=False):
    return [(r.identifier, r.name) for r in _candidates(include_all)]


class ClassicTransport(Transport):
    name = "bluetooth"
    can_read = True

    def __init__(self, identifier=None, device=None, *, name=None, channel=None, allow_unknown=False):
        if not available():
            raise TransportError("This Python build has no Bluetooth Classic RFCOMM socket support.")
        if identifier and name:
            raise TransportError("Select a Bluetooth cutter by identifier or name.")
        channel = DEFAULT_CHANNEL if channel is None else channel
        if not 1 <= channel <= 30:
            raise TransportError("The RFCOMM channel must be between 1 and 30.")
        if identifier and not _ADDRESS.fullmatch(identifier):
            raise TransportError("Bluetooth Classic requires a MAC address such as AA:BB:CC:DD:EE:FF.")
        known = select_candidate(_candidates(include_all=bool(name)), name=name, expected=device) if not identifier else None
        self.identifier = identifier.upper() if identifier else known.identifier
        self._socket = None
        self._buffer = bytearray()
        try:
            self._socket = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
            self._socket.settimeout(10)
            self._socket.connect((self.identifier, channel))
            version = self.firmware_version()
            self.device = confirm_firmware(version, device, known.model if known else None,
                                           allow_unknown=allow_unknown)
        except OSError as exc:
            self.close()
            raise TransportError(f"Could not connect to {self.identifier} on RFCOMM channel {channel}. "
                                 f"Check that the cutter is paired. {exc}") from exc
        except BaseException:
            self.close()
            raise

    def write(self, data):
        try:
            self._socket.settimeout(30)
            self._socket.sendall(data)
        except OSError as exc:
            raise TransportError(f"Bluetooth Classic write failed. {exc}") from exc
        return len(data)

    def read(self, size=64, timeout_ms=10000):
        deadline = time.monotonic() + timeout_ms / 1000
        while True:
            end = self._buffer.find(graphtec.CMD_ETX) + 1
            if end:
                reply = bytes(self._buffer[:end])
                del self._buffer[:end]
                return reply
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TransportError("Bluetooth Classic reply timed out.")
            try:
                self._socket.settimeout(remaining)
                chunk = self._socket.recv(size)
            except TimeoutError as exc:
                raise TransportError("Bluetooth Classic reply timed out.") from exc
            except OSError as exc:
                raise TransportError(f"Bluetooth Classic read failed. {exc}") from exc
            if not chunk:
                raise TransportError("Bluetooth Classic cutter disconnected.")
            self._buffer.extend(chunk)

    def close(self):
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
            self._socket = None
