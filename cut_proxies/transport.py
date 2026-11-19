"""Getting bytes to the cutter, and reading its answers back.

Three transports, in the order they are tried:

`PyUsbTransport`
    Bulk endpoints over `pyusb`. This is the preferred USB connection for
    reading registration replies. It needs either a udev rule
    or root, and it needs the kernel's `usblp` driver *not* to be holding the
    interface.

`UsbLpTransport`
    `/dev/usb/lp0`.  Writes fine and will read on a device that answers, but
    the kernel driver claims the interface, so a scan that needs a reply may
    time out depending on how `usblp` buffers.  Kept because it needs no
    extra application dependency. Both registration modes need a reply.

`FileTransport`
    Writes the stream to a file and never pretends to read.  This is what
    `--dry-run` uses, and what makes the whole pipeline testable with no
    hardware attached at all.

USB status and firmware queries have been exercised on a Cameo 4.
See docs/FINDINGS.md for the hardware results.
"""

from __future__ import annotations

import os
import threading
import time

from . import graphtec

# inkscape-silhouette Graphtec.py safe_write(): keep long path batches
# command-aligned and wait for the cutter before sending more motion.
PATH_BATCH_BYTES = 1024
PATH_READY_TIMEOUT = 120


class TransportError(RuntimeError):
    pass


class RegistrationFailed(TransportError):
    """The cutter rejected the whole scan without a per-mark result."""

    def __init__(self):
        super().__init__("Registration scan failed. The cutter did not report which "
                         "mark it missed. No cut paths were sent.")


class NotFound(TransportError):
    pass


class CutCancelled(TransportError):
    pass


class CutControl:
    """Cooperate with the sender at boundaries between command writes."""

    def __init__(self, on_change):
        self._condition = threading.Condition()
        self._state = "running"
        self._on_change = on_change

    @property
    def state(self):
        with self._condition:
            return self._state

    def toggle_pause(self):
        with self._condition:
            if self._state == "running":
                self._set_state("pausing")
            elif self._state in ("pausing", "paused"):
                self._set_state("running")

    def cancel(self):
        with self._condition:
            if self._state != "finishing":
                self._set_state("cancelling")

    def finishing(self):
        with self._condition:
            self._set_state("finishing")

    @property
    def status_text(self):
        return {"running": "Sending to the cutter…",
                "pausing": "Pausing after the current cutter operation…",
                "paused": "Cut paused. Leave the mat loaded to resume.",
                "cancelling": "Stopping after the current cutter operation…",
                "finishing": "Waiting for the cutter to finish…"}[self.state]

    def _set_state(self, state):
        self._state = state
        self._condition.notify_all()
        self._on_change()

    def checkpoint(self):
        with self._condition:
            paused = self._state == "pausing"
            if paused:
                self._set_state("paused")
            self._condition.wait_for(lambda: self._state != "paused")
            if self._state == "cancelling":
                raise CutCancelled("Cut cancelled. No more commands were sent.")
            return paused


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
    def run(self, steps, log=lambda m: None, *, wait_done: bool = False,
            control: CutControl | None = None) -> int:
        """Execute a checked plan, optionally waiting after its final command."""
        if (wait_done or control is not None) and not self.can_read:
            raise TransportError("Waiting for completion requires a readable cutter connection.")
        steps = tuple(steps)
        # Query before motion, once for the whole job (including test matrices).
        # A depth command is always a complete ETX-delimited TF command.
        if self.device and self.device.detects_tools and any(
                cmd.startswith(b"TF") for step in steps if step.kind == "write"
                for cmd in step.data.split(graphtec.CMD_ETX)):
            check_autoblade(self.tool_codes())
        sent = 0
        for step in steps:
            if control is not None and step.kind == "write":
                self._cut_checkpoint(control)
            if step.kind == "write":
                if step.note:
                    log(step.note)
                sent += self._write_paths(step.data, control) if step.pace else self.write(step.data)
                paced_wait = step.pace and (control is not None or len(step.data) > PATH_BATCH_BYTES)
                if step.wait_ready and not paced_wait:
                    self._wait_until_ready()
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
        if wait_done:
            if control is not None:
                control.finishing()
            log("Waiting for the cutter to finish")
            try:
                self._wait_until_ready()
            except TransportError as exc:
                raise TransportError("The commands were sent, but cutter completion could not "
                                     f"be confirmed. {exc}") from exc
        return sent

    def _cut_checkpoint(self, control):
        if control.checkpoint():
            state = self.status()
            if state != "ready":
                raise TransportError(f"The cutter is {state}. The paused cut could not resume.")

    def _write_paths(self, data: bytes, control=None) -> int:
        if len(data) <= PATH_BATCH_BYTES and control is None:
            return self.write(data)
        # Validate framing before the first batch, including truncated input.
        commands = data.split(graphtec.CMD_ETX)
        if commands[-1] or any(len(cmd) >= PATH_BATCH_BYTES for cmd in commands[:-1]):
            raise TransportError("Cannot batch an incomplete or oversized cut command.")

        sent = 0
        while sent < len(data):
            if control is not None:
                self._cut_checkpoint(control)
            end = data.rfind(graphtec.CMD_ETX, sent, sent + PATH_BATCH_BYTES) + 1
            written = self.write(data[sent:end])
            if written != end - sent:
                raise TransportError("Incomplete path write. The rest of the job was stopped.")
            sent = end
            self._wait_until_ready()
        return sent

    def _wait_until_ready(self) -> None:
        deadline = time.monotonic() + PATH_READY_TIMEOUT
        while True:
            state = self.status()
            if state == "ready":
                return
            if state != "moving":
                raise TransportError(f"The cutter reported {state} during movement. "
                                     "The rest of the job was stopped.")
            if time.monotonic() >= deadline:
                raise TransportError("The cutter stayed busy after a movement command. "
                                     "The rest of the job was stopped.")
            time.sleep(0.05)

    def query_text(self, command, timeout_ms=5000):
        """Read one ETX-framed reply to a known Graphtec query."""
        self.write(graphtec.frame(command))
        reply = self.read(timeout_ms=timeout_ms)
        if not reply.endswith(graphtec.CMD_ETX):
            raise TransportError(f"Unexpected reply to {command!r}. {reply!r}")
        return reply[:-1].decode("ascii", errors="replace").strip()

    def firmware_version(self):
        if not getattr(self, "_firmware", None):
            self._firmware = self.query_text(graphtec.QUERY_FIRMWARE_VERSION)
        return self._firmware

    def diagnostics(self):
        """Read the same firmware and raw calibration queries as Graphtec.py."""
        result = [("Firmware", self.firmware_version())]
        if self.device and self.device.cameo3_on:
            for query in ("TB71", "FA"):
                result.append((query, self.query_text(query, timeout_ms=1000)))
        if self.device and self.device.product_id == 0x112F:
            result.append(("TC", self.query_text("TC", timeout_ms=1000)))
        return result

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

    def __init__(self, device: graphtec.Device | None = None, *, usb_endpoint=None):
        try:
            import usb.core
            import usb.util
        except ImportError as exc:       # pragma: no cover - env dependent
            raise TransportError(
                "pyusb is not installed, so the cutter cannot be driven "
                "directly over USB. Install PyUSB, use --dry-run, or select Bluetooth.") from exc
        self._usb = usb.core
        self._util = usb.util

        found = None
        for dev in usb.core.find(find_all=True):
            known = graphtec.device_for(dev.idVendor, dev.idProduct)
            if usb_endpoint is not None and (dev.bus, dev.address) != usb_endpoint:
                continue
            if known and (device is None or known == device):
                found = (dev, known)
                break
        if found is None:
            raise NotFound(
                "No Silhouette cutter found on USB. Known ids: "
                + ", ".join(sorted({d.usb_id for d in graphtec.DEVICES})))
        self._dev, self.device = found
        self._reply_buffer = bytearray()
        self._detached_driver = False

        try:
            if self._dev.is_kernel_driver_active(0):
                self._dev.detach_kernel_driver(0)
                self._detached_driver = True
        except (NotImplementedError, usb.core.USBError):
            # usblp may not be loaded, or we may not be allowed to detach; the
            # claim below will fail with a clearer message either way.
            pass
        try:
            self._dev.set_configuration()
        except usb.core.USBError as exc:
            self.close()
            raise TransportError(
                f"Could not claim the {self.device.name}: {exc}. This is "
                f"usually permissions - install the udev rule from "
                f"docs/SETUP.md - or the usblp kernel driver holding the "
                f"interface.") from exc

        try:
            intf = self._dev.get_active_configuration()[(0, 0)]
            endpoints = {
                self._util.endpoint_direction(ep.bEndpointAddress): ep
                for ep in intf if self._util.endpoint_type(ep.bmAttributes) == self._util.ENDPOINT_TYPE_BULK
            }
            self._out = endpoints.get(self._util.ENDPOINT_OUT)
            self._in = endpoints.get(self._util.ENDPOINT_IN)
            if self._out is None or self._in is None:
                raise TransportError("The cutter needs both USB bulk endpoints.")
        except (usb.core.USBError, KeyError, IndexError) as exc:
            self.close()
            raise TransportError(f"Could not open the cutter's USB interface: {exc}") from exc
        except TransportError:
            self.close()
            raise

    def write(self, data: bytes) -> int:
        total = 0
        while total < len(data):
            chunk = data[total:total + 4096]
            try:
                written = self._out.write(chunk, timeout=30000)
            except self._usb.USBError as exc:
                # A timeout can occur after bytes have reached the cutter.
                # Do not replay a transfer whose accepted length is unknown.
                raise TransportError(f"USB write failed after {total} confirmed bytes: {exc}") from exc
            if not 0 < written <= len(chunk):
                raise TransportError(f"USB write made no valid progress after {total} bytes.")
            total += written
        return total

    def read(self, size: int = 64, timeout_ms: int = 10000) -> bytes:
        deadline = time.monotonic() + timeout_ms / 1000.0
        while True:
            end = self._reply_buffer.find(graphtec.CMD_ETX) + 1
            if end:
                reply = bytes(self._reply_buffer[:end])
                del self._reply_buffer[:end]
                return reply
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TransportError(f"The cutter did not answer within {timeout_ms} ms "
                                     f"(got {bytes(self._reply_buffer)!r})")
            try:
                chunk = self._in.read(size, timeout=max(1, min(1000, int(remaining * 1000))))
            except self._usb.USBTimeoutError:
                continue
            except self._usb.USBError as exc:
                raise TransportError(f"USB read failed: {exc}") from exc
            self._reply_buffer.extend(chunk)

    def close(self) -> None:
        try:
            self._util.dispose_resources(self._dev)
            if self._detached_driver:
                self._dev.attach_kernel_driver(0)
        except (NotImplementedError, self._usb.USBError):
            pass
        finally:
            self._detached_driver = False


# --------------------------------------------------------------------------
# usblp
# --------------------------------------------------------------------------

class UsbLpTransport(Transport):
    name = "usblp"
    can_read = True

    def __init__(self, path: str | None = None, device=None, *, usb_endpoint=None):
        discovered = (find_devices() if usb_endpoint is None else
                      [(known, node) for known, node, endpoint in usb_endpoints()
                       if endpoint == usb_endpoint])
        candidates = [(known, node) for known, node in discovered
                      if node.startswith("/dev/usb/lp")
                      and (path is None or path == node)
                      and (device is None or device == known)]
        for known, cand in candidates:
            if os.path.exists(cand):
                try:
                    self._fd = os.open(cand, os.O_RDWR | os.O_NONBLOCK)
                except OSError:
                    continue
                if usb_endpoint is not None and not any(
                        entry == (known, cand, usb_endpoint) for entry in usb_endpoints()):
                    self.close()
                    raise NotFound("The selected USB cutter disconnected.")
                self.path = cand
                self.device = known
                self._reply_buffer = bytearray()
                return
        raise NotFound(
            "No writable /dev/usb/lp* device. Either the cutter is not "
            "attached or the usblp driver did not bind to it.")

    def write(self, data: bytes) -> int:
        total = 0
        deadline = time.monotonic() + 30
        while total < len(data):
            try:
                written = os.write(self._fd, data[total:total + 4096])
            except BlockingIOError:
                written = 0
            except OSError as exc:
                raise TransportError(f"Write to {self.path} failed after {total} bytes: {exc}") from exc
            if written:
                total += written
                deadline = time.monotonic() + 30
            elif time.monotonic() >= deadline:
                raise TransportError(f"No write progress on {self.path} after {total} bytes.")
            else:
                time.sleep(0.01)
        return total

    def read(self, size: int = 64, timeout_ms: int = 10000) -> bytes:
        deadline = time.monotonic() + timeout_ms / 1000.0
        while True:
            end = self._reply_buffer.find(graphtec.CMD_ETX) + 1
            if end:
                reply = bytes(self._reply_buffer[:end])
                del self._reply_buffer[:end]
                return reply
            if time.monotonic() >= deadline:
                break
            try:
                chunk = os.read(self._fd, size)
            except BlockingIOError:
                time.sleep(0.02)
                continue
            except OSError as exc:
                raise TransportError(f"Read from {self.path} failed: {exc}") from exc
            if chunk:
                self._reply_buffer.extend(chunk)
            else:
                time.sleep(0.02)
        raise TransportError(
            f"No answer on {self.path} within {timeout_ms} ms (got {bytes(self._reply_buffer)!r}). "
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

def usb_endpoints(*, include_unknown=False):
    """Return model, printer node, and this enumeration's bus/address."""
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
            if not include_unknown or vid not in (graphtec.VENDOR_GRAPHTEC, graphtec.VENDOR_CAMEO5ALPHA):
                continue
            dev = (vid, pid)
        try:
            with open(os.path.join(base, "busnum")) as fh:
                bus = int(fh.read())
            with open(os.path.join(base, "devnum")) as fh:
                address = int(fh.read())
            children = sorted(os.listdir(base))
        except (OSError, ValueError):
            continue
        lp = ""
        for sub in children:
            cand = os.path.join(base, sub, "usbmisc")
            if os.path.isdir(cand):
                try:
                    names = os.listdir(cand)
                except OSError:
                    continue
                if names:
                    lp = f"/dev/usb/{names[0]}"
        out.append((dev, lp or "bulk endpoints only", (bus, address)))
    return out


def find_devices() -> list[tuple[graphtec.Device, str]]:
    return [(device, node) for device, node, _ in usb_endpoints()]


def open_transport(prefer: str = "auto", dump_to=None,
                   device: graphtec.Device | None = None,
                   bluetooth_id: str | None = None, *, bluetooth_name: str | None = None,
                   bluetooth_channel: int | None = None, allow_unknown_firmware: bool = False,
                   usb_endpoint=None) -> Transport:
    """Open the best available transport.

    Automatic selection tries USB. Bluetooth Classic and LE are explicit
    choices so the job is sent to the intended cutter.
    """
    if prefer == "file" or dump_to is not None:
        if dump_to is None:
            raise TransportError("the file transport needs a path to write to")
        return FileTransport(dump_to, device)
    if prefer == "ble":
        from .ble import BleTransport
        return BleTransport(bluetooth_id, device, name=bluetooth_name,
                            allow_unknown=allow_unknown_firmware)
    if prefer == "bluetooth":
        from .rfcomm import ClassicTransport
        return ClassicTransport(bluetooth_id, device, name=bluetooth_name,
                                channel=bluetooth_channel, allow_unknown=allow_unknown_firmware)
    errors = []
    order = {"auto": ("pyusb", "usblp"),
             "pyusb": ("pyusb",), "usblp": ("usblp",)}.get(prefer)
    if order is None:
        raise TransportError(f"unknown transport {prefer!r}")
    for kind in order:
        try:
            if kind == "pyusb":
                return PyUsbTransport(device, usb_endpoint=usb_endpoint) if usb_endpoint else PyUsbTransport(device)
            if usb_endpoint:
                return UsbLpTransport(device=device, usb_endpoint=usb_endpoint)
            return UsbLpTransport(device=device)
        except TransportError as exc:
            errors.append(f"{kind}: {exc}")
    raise NotFound("No usable transport to the cutter.\n  "
                   + "\n  ".join(errors))
