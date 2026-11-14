"""Getting bytes to the cutter, and reading its answers back.

Three transports, in the order they are tried:

`PyUsbTransport`
    Bulk endpoints over `pyusb`.  This is the only one that can read, so it is
    the only one that can do a registration scan.  It needs either a udev rule
    or root, and it needs the kernel's `usblp` driver *not* to be holding the
    interface.

`UsbLpTransport`
    `/dev/usb/lp0`.  Writes fine and will read on a device that answers, but
    the kernel driver claims the interface, so a scan that needs a reply may
    time out depending on how `usblp` buffers.  Kept because it needs no
    extra permissions, and a manual-registration cut does not need to read.

`FileTransport`
    Writes the stream to a file and never pretends to read.  This is what
    `--dry-run` uses, and what makes the whole pipeline testable with no
    hardware attached at all.

USB status and firmware queries have been exercised on a Cameo 4.
See docs/FINDINGS.md for the hardware results.
"""

from __future__ import annotations

import os
import time

from . import graphtec


class TransportError(RuntimeError):
    pass


class RegistrationFailed(TransportError):
    """The cutter rejected the whole scan without a per-mark result."""

    def __init__(self):
        super().__init__("Registration scan failed. The cutter did not report which "
                         "mark it missed. No cut paths were sent.")


class NotFound(TransportError):
    pass


def check_autoblade(codes):
    if not codes or codes[0] != 2:
        detected = graphtec.tool_name(codes[0]) if codes else "Unavailable"
        raise TransportError(f"Holder 1 reports {detected}. Automatic depth requires an "
                             "AutoBlade. For a manual blade, set its physical depth "
                             "and choose depth -1, then review the job again.")


class Transport:
    """What every transport has to provide."""

    name = "none"
    can_read = False
    device: graphtec.Device | None = None

    def write(self, data: bytes) -> int:
        raise NotImplementedError

    def read(self, size: int = 64, timeout_ms: int = 10000) -> bytes:
        raise TransportError(f"{self.name} cannot read from the cutter")

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---- the shared run loop ----------------------------------------
    def run(self, steps, log=lambda m: None) -> int:
        """Execute a `CutJob` plan, stopping on the first failed expectation."""
        steps = tuple(steps)
        # Query before motion, once for the whole job (including test matrices).
        # A depth command is always a complete ETX-delimited TF command.
        if self.device and self.device.detects_tools and any(
                cmd.startswith(b"TF") for step in steps if step.kind == "write"
                for cmd in step.data.split(graphtec.CMD_ETX)):
            check_autoblade(self.tool_codes())
        sent = 0
        for step in steps:
            if step.kind == "write":
                if step.note:
                    log(step.note)
                sent += self.write(step.data)
            elif step.kind == "expect":
                got = self.read(timeout_ms=step.timeout_ms)
                if got != step.expect:
                    if step.expect == graphtec.RESP_REGMARK_OK and got == graphtec.RESP_REGMARK_FAIL:
                        raise RegistrationFailed()
                    raise TransportError(
                        f"{step.note or 'expected reply'}: wanted "
                        f"{step.expect!r}, got {got!r}")
                if step.note:
                    log(f"{step.note}: ok")
        return sent

    def tool_codes(self) -> tuple[int, ...]:
        """Query the installed holders (inkscape-silhouette Graphtec.py 927–941)."""
        if self.device is None or not self.device.detects_tools:
            return ()
        self.write(graphtec.CMD_ESC + graphtec.ESC_NAK)
        reply = self.read(timeout_ms=1000)
        fields = [part.strip() for part in reply[:-1].split(b",")]
        if (not reply.endswith(graphtec.CMD_ETX) or len(fields) > 2
                or (self.device.two_tools and len(fields) != 2)
                or not all(f.isdigit() and int(f) <= 255 for f in fields)):
            raise TransportError(f"Unexpected tool detection reply {reply!r}")
        return tuple(int(f) for f in fields)

    def status_with_tools(self) -> str:
        state = self.status()
        if state not in ("ready", "unloaded"):
            return state
        codes = self.tool_codes()
        if not codes:
            return f"{state} · tool detection unavailable on this model"
        holders = [f"Holder {i}: {graphtec.tool_name(code)}"
                   for i, code in enumerate(codes, 1)]
        return " · ".join([state, *holders])

    def status(self) -> str:
        """'ready', 'moving', 'unloaded', or whatever came back."""
        if not self.can_read:
            return "unknown"
        self.write(graphtec.CMD_ESC + graphtec.ESC_ENQ)
        resp = self.read(timeout_ms=5000)
        if not resp.endswith(graphtec.CMD_ETX):
            return f"unparsed: {resp!r}"
        return graphtec.RESP_DECODING.get(resp[:-1], f"unknown: {resp[:-1]!r}")


# --------------------------------------------------------------------------
# pyusb
# --------------------------------------------------------------------------

class PyUsbTransport(Transport):
    name = "pyusb"
    can_read = True

    def __init__(self, device: graphtec.Device | None = None):
        try:
            import usb.core
            import usb.util
        except ImportError as exc:       # pragma: no cover - env dependent
            raise TransportError(
                "pyusb is not installed, so the cutter cannot be driven "
                "directly. Install python-pyusb, or use --dry-run, or go "
                "through Silhouette Studio with the WinBoat bridge.") from exc
        self._usb = usb.core
        self._util = usb.util

        found = None
        for dev in usb.core.find(find_all=True):
            known = graphtec.device_for(dev.idVendor, dev.idProduct)
            if known and (device is None or known == device):
                found = (dev, known)
                break
        if found is None:
            raise NotFound(
                "No Silhouette cutter found on USB. Known ids: "
                + ", ".join(sorted({d.usb_id for d in graphtec.DEVICES})))
        self._dev, self.device = found

        try:
            if self._dev.is_kernel_driver_active(0):
                self._dev.detach_kernel_driver(0)
        except (NotImplementedError, usb.core.USBError):
            # usblp may not be loaded, or we may not be allowed to detach; the
            # claim below will fail with a clearer message either way.
            pass
        try:
            self._dev.set_configuration()
        except usb.core.USBError as exc:
            raise TransportError(
                f"Could not claim the {self.device.name}: {exc}. This is "
                f"usually permissions - install the udev rule from "
                f"docs/SETUP.md - or the usblp kernel driver holding the "
                f"interface.") from exc

        cfg = self._dev.get_active_configuration()
        intf = cfg[(0, 0)]
        self._out = usb.util.find_descriptor(
            intf, custom_match=lambda e:
            usb.util.endpoint_direction(e.bEndpointAddress)
            == usb.util.ENDPOINT_OUT)
        self._in = usb.util.find_descriptor(
            intf, custom_match=lambda e:
            usb.util.endpoint_direction(e.bEndpointAddress)
            == usb.util.ENDPOINT_IN)
        if self._out is None:
            raise TransportError("The cutter has no bulk OUT endpoint.")

    def write(self, data: bytes) -> int:
        # Long command lists have to go out in chunks or the device stalls;
        # 4 KiB is what inkscape-silhouette settled on.
        total = 0
        for i in range(0, len(data), 4096):
            total += self._out.write(data[i:i + 4096], timeout=30000)
        return total

    def read(self, size: int = 64, timeout_ms: int = 10000) -> bytes:
        if self._in is None:
            raise TransportError("The cutter has no bulk IN endpoint.")
        deadline = time.monotonic() + timeout_ms / 1000.0
        buf = b""
        while time.monotonic() < deadline:
            try:
                chunk = bytes(self._in.read(size, timeout=1000))
            except self._usb.USBError:
                continue
            buf += chunk
            if buf.endswith(graphtec.CMD_ETX):
                return buf
        raise TransportError(
            f"The cutter did not answer within {timeout_ms} ms "
            f"(got {buf!r})")

    def close(self) -> None:
        try:
            self._util.dispose_resources(self._dev)
        except Exception:            # noqa: BLE001 - closing must not raise
            pass


# --------------------------------------------------------------------------
# usblp
# --------------------------------------------------------------------------

class UsbLpTransport(Transport):
    name = "usblp"
    can_read = True

    def __init__(self, path: str | None = None, device=None):
        candidates = [(known, node) for known, node in find_devices()
                      if node.startswith("/dev/usb/lp")
                      and (path is None or path == node)
                      and (device is None or device == known)]
        for known, cand in candidates:
            if os.path.exists(cand):
                try:
                    self._fd = os.open(cand, os.O_RDWR | os.O_NONBLOCK)
                except OSError:
                    continue
                self.path = cand
                self.device = known
                return
        raise NotFound(
            "No writable /dev/usb/lp* device. Either the cutter is not "
            "attached or the usblp driver did not bind to it.")

    def write(self, data: bytes) -> int:
        total = 0
        while total < len(data):
            try:
                total += os.write(self._fd, data[total:total + 4096])
            except BlockingIOError:
                time.sleep(0.01)
        return total

    def read(self, size: int = 64, timeout_ms: int = 10000) -> bytes:
        deadline = time.monotonic() + timeout_ms / 1000.0
        buf = b""
        while time.monotonic() < deadline:
            try:
                chunk = os.read(self._fd, size)
            except BlockingIOError:
                time.sleep(0.02)
                continue
            if chunk:
                buf += chunk
                if buf.endswith(graphtec.CMD_ETX):
                    return buf
        raise TransportError(
            f"No answer on {self.path} within {timeout_ms} ms (got {buf!r}). "
            f"usblp buffers replies unhelpfully; for a registered cut use "
            f"pyusb.")

    def close(self) -> None:
        try:
            os.close(self._fd)
        except OSError:
            pass


# --------------------------------------------------------------------------
# file
# --------------------------------------------------------------------------

class FileTransport(Transport):
    """Capture the stream instead of sending it."""

    name = "file"
    can_read = False

    def __init__(self, path, device: graphtec.Device | None = None):
        self.path = str(path)
        self.device = device
        self._fh = open(self.path, "wb")

    def write(self, data: bytes) -> int:
        return self._fh.write(data)

    def close(self) -> None:
        self._fh.close()

    def run(self, steps, log=lambda m: None) -> int:
        """Write everything and report, rather than check, each expectation."""
        sent = 0
        for step in steps:
            if step.kind == "write":
                if step.note:
                    log(step.note)
                sent += self.write(step.data)
            elif step.kind == "expect":
                log(f"{step.note or 'reply'}: not checked (dry run; the "
                    f"cutter would answer {step.expect!r})")
        return sent


# --------------------------------------------------------------------------
# discovery
# --------------------------------------------------------------------------

def find_devices() -> list[tuple[graphtec.Device, str]]:
    """Every known cutter currently on the USB bus, with how it is reachable.

    Uses sysfs rather than pyusb so that discovery works even when pyusb is
    missing - which is the common case, and the case where a clear message
    matters most.
    """
    out: list[tuple[graphtec.Device, str]] = []
    root = "/sys/bus/usb/devices"
    if not os.path.isdir(root):
        return out
    for entry in sorted(os.listdir(root)):
        base = os.path.join(root, entry)
        try:
            with open(os.path.join(base, "idVendor")) as fh:
                vid = int(fh.read().strip(), 16)
            with open(os.path.join(base, "idProduct")) as fh:
                pid = int(fh.read().strip(), 16)
        except (OSError, ValueError):
            continue
        dev = graphtec.device_for(vid, pid)
        if dev is None:
            continue
        lp = ""
        for sub in sorted(os.listdir(base)):
            cand = os.path.join(base, sub, "usbmisc")
            if os.path.isdir(cand):
                names = os.listdir(cand)
                if names:
                    lp = f"/dev/usb/{names[0]}"
        out.append((dev, lp or "bulk endpoints only"))
    return out


def open_transport(prefer: str = "auto", dump_to=None,
                   device: graphtec.Device | None = None,
                   bluetooth_id: str | None = None) -> Transport:
    """Open the best available transport.

    `prefer` is "auto", "pyusb", "usblp" or "file".  "auto" takes pyusb when
    it can, because it is the only one that can complete a registration scan.
    """
    if prefer == "file" or dump_to is not None:
        if dump_to is None:
            raise TransportError("the file transport needs a path to write to")
        return FileTransport(dump_to, device)
    if prefer == "ble":
        from .ble import BleTransport
        return BleTransport(bluetooth_id, device)
    errors = []
    order = {"auto": ("pyusb", "usblp"),
             "pyusb": ("pyusb",), "usblp": ("usblp",)}.get(prefer)
    if order is None:
        raise TransportError(f"unknown transport {prefer!r}")
    for kind in order:
        try:
            return PyUsbTransport(device) if kind == "pyusb" \
                else UsbLpTransport(device=device)
        except TransportError as exc:
            errors.append(f"{kind}: {exc}")
    raise NotFound("No usable transport to the cutter.\n  "
                   + "\n  ".join(errors))
