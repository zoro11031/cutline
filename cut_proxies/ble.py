"""Graphtec cutter GATT transport."""

import asyncio
import re

from . import graphtec
from .transport import Transport, TransportError

SERVICE = "e2088282-4fde-42f9-bb22-6ec3c7ed8f91"
WRITE = "6d92661d-f429-4d67-929b-28e7a9780912"
READ = "8dcf199a-30e7-4bd4-beb6-beb57dca866c"
CONTROL = "61490654-b5b4-458c-a867-9e15bc1471e0"
# Advertised and firmware names mapped to the same device definitions used
# for USB. Source: inkscape-silhouette Graphtec.py BLUETOOTH_NAME_TO_PRODUCT_ID.
_MODEL_IDS = {
    "CAMEO 5 ALPHA PLUS": (graphtec.VENDOR_CAMEO5ALPHA, 0x0002),
    "CAMEO 5 ALPHA": (graphtec.VENDOR_CAMEO5ALPHA, 0x0001),
    "CAMEO 5 PLUS": (graphtec.VENDOR_GRAPHTEC, 0x1141),
    "CAMEO 5": (graphtec.VENDOR_GRAPHTEC, 0x1140),
    "CAMEO PRO MARK2": (graphtec.VENDOR_GRAPHTEC, 0x1146),
    "CAMEO 4 PLUS": (graphtec.VENDOR_GRAPHTEC, 0x1138),
    "CAMEO 4 PRO": (graphtec.VENDOR_GRAPHTEC, 0x1139),
    "CAMEO 4": (graphtec.VENDOR_GRAPHTEC, 0x1137),
    "CAMEO 3": (graphtec.VENDOR_GRAPHTEC, 0x112F),
    "PORTRAIT 4": (graphtec.VENDOR_GRAPHTEC, 0x113F),
    "PORTRAIT 3": (graphtec.VENDOR_GRAPHTEC, 0x113A),
    "PORTRAIT 2": (graphtec.VENDOR_GRAPHTEC, 0x1132),
}
_MODELS = {name.replace(" ", ""): graphtec.device_for(*ids) for name, ids in _MODEL_IDS.items()}
_FIRMWARE = re.compile(rb"(.+?)\s+V\d+(?:\.\d+)+", re.I)
_ADV_SUFFIX = re.compile(r"-[A-Z0-9]+$")


def advertised_model(name: str) -> graphtec.Device | None:
    model = _ADV_SUFFIX.sub("", "".join(name.upper().split()))
    return _MODELS.get(model)


def _bleak():
    try:
        import bleak
    except ImportError as exc:
        raise TransportError("Install Bleak and start BlueZ to use Bluetooth LE.") from exc
    return bleak


async def _scan():
    records = await _bleak().BleakScanner.discover(timeout=8, return_adv=True)
    found = {}
    for device, adv in records.values():
        name = adv.local_name or device.name or ""
        model = advertised_model(name)
        if model is not None:
            found[device.address.upper()] = (device, name, model)
    return found


def discover():
    async def scan():
        return await asyncio.wait_for(_scan(), 12)

    try:
        return [(address, name) for address, (_, name, _)
                in sorted(asyncio.run(scan()).items())]
    except Exception as exc:
        raise TransportError(f"Bluetooth discovery failed: {exc}") from exc


class BleTransport(Transport):
    name = "ble"
    can_read = True

    def __init__(self, identifier, device=None):
        if not identifier:
            raise TransportError("Select a Silhouette Bluetooth address first.")
        if device is not None and device not in _MODELS.values():
            raise TransportError(f"Bluetooth LE is unavailable for {device.name}.")
        self._runner = asyncio.new_event_loop()
        self._client = None
        self._buffer = bytearray()
        self._ready = asyncio.Event()
        try:
            model = self._run(self._connect(identifier, device), 25, "connection")
            self.write(graphtec.frame(graphtec.QUERY_FIRMWARE_VERSION))
            reply = self.read(timeout_ms=5000)
            match = _FIRMWARE.fullmatch(reply[:-1].strip()) if reply.endswith(graphtec.CMD_ETX) else None
            confirmed = advertised_model(match[1].decode("ascii", errors="replace")) if match else None
            if confirmed is None or confirmed != model:
                raise TransportError(f"Expected {model.name} firmware, received {reply!r}.")
            self.device = confirmed
        except BaseException:
            self.close()
            raise

    def _run(self, operation, seconds, action):
        try:
            return self._runner.run_until_complete(asyncio.wait_for(operation, seconds))
        except TransportError:
            raise
        except TimeoutError as exc:
            raise TransportError(f"Bluetooth {action} timed out.") from exc
        except Exception as exc:
            raise TransportError(f"Bluetooth {action} failed: {exc}") from exc

    async def _connect(self, identifier, expected):
        devices = await _scan()
        match = devices.get(identifier.upper())
        if match is None:
            raise TransportError(f"Silhouette cutter {identifier} was not found over Bluetooth.")
        peripheral, _name, model = match
        if expected is not None and model != expected:
            raise TransportError(f"{identifier} advertises {model.name}. "
                                 f"The checked job requires {expected.name}.")
        self._client = _bleak().BleakClient(
            peripheral, disconnected_callback=lambda _: self._ready.set())
        await self._client.connect()
        service = self._client.services.get_service(SERVICE)
        if service is None or any(service.get_characteristic(uuid) is None
                                  for uuid in (CONTROL, READ, WRITE)):
            raise TransportError("The device lacks the Silhouette Bluetooth GATT service.")
        await self._client.start_notify(CONTROL, lambda *_: None)
        await self._client.start_notify(READ, self._on_reply)
        for uuid in (CONTROL, READ, WRITE):
            await self._client.write_gatt_char(
                uuid, graphtec.CMD_ESC + graphtec.ESC_EOT, response=True)
        await asyncio.sleep(1)
        self._buffer.clear()
        return model

    def _on_reply(self, _characteristic, data):
        self._buffer.extend(data)
        self._ready.set()

    async def _write(self, data):
        for offset in range(0, len(data), 20):
            chunk = data[offset:offset + 20]
            for attempt in range(4):
                try:
                    await self._client.write_gatt_char(WRITE, chunk, response=True)
                    break
                except Exception as exc:
                    details = f"{exc} {getattr(exc, 'dbus_error_details', '')}".lower()
                    if attempt == 3 or ("att error: 0x0e" not in details
                                        and "unlikely error" not in details):
                        raise
                    await asyncio.sleep(0.1 * 2 ** attempt)
        return len(data)

    def write(self, data):
        return self._run(self._write(data), 60, "write")

    async def _read(self):
        while self._client.is_connected:
            end = self._buffer.find(graphtec.CMD_ETX) + 1
            if end:
                result = bytes(self._buffer[:end])
                del self._buffer[:end]
                return result
            self._ready.clear()
            await self._ready.wait()
        raise TransportError("Bluetooth cutter disconnected.")

    def read(self, size=64, timeout_ms=10000):
        return self._run(self._read(), timeout_ms / 1000, "reply")

    def close(self):
        if self._runner is None:
            return
        try:
            if self._client is not None:
                self._run(self._client.disconnect(), 5, "disconnect")
        except TransportError:
            pass
        finally:
            self._runner.close()
            self._runner = None
