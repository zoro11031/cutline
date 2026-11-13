"""Cameo 4 GATT transport, based on inkscape-silhouette's BLETransport.py."""

import asyncio
import re

from . import graphtec
from .transport import Transport, TransportError

SERVICE = "e2088282-4fde-42f9-bb22-6ec3c7ed8f91"
WRITE = "6d92661d-f429-4d67-929b-28e7a9780912"
READ = "8dcf199a-30e7-4bd4-beb6-beb57dca866c"
CONTROL = "61490654-b5b4-458c-a867-9e15bc1471e0"
MODEL = re.compile(r"CAMEO\s*4(?:-[A-Z0-9]+)?", re.I)
FIRMWARE = re.compile(rb"CAMEO\s*4\s+V\d+(?:\.\d+)+", re.I)


def _bleak():
    try:
        import bleak
    except ImportError as exc:
        raise TransportError("Install Bleak and start BlueZ to use Bluetooth LE.") from exc
    return bleak


async def _scan():
    records = await _bleak().BleakScanner.discover(timeout=8, return_adv=True)
    return {device.address: (device, adv.local_name or device.name)
            for device, adv in records.values()
            if MODEL.fullmatch(adv.local_name or device.name or "")}


def discover():
    async def scan():
        return await asyncio.wait_for(_scan(), 12)

    try:
        return [(address, name) for address, (_, name)
                in sorted(asyncio.run(scan()).items())]
    except Exception as exc:
        raise TransportError(f"Bluetooth discovery failed: {exc}") from exc


class BleTransport(Transport):
    name = "ble"
    can_read = True
    device = graphtec.device_for(0x0b4d, 0x1137)

    def __init__(self, identifier, device=None):
        if not identifier:
            raise TransportError("Select a Cameo 4 Bluetooth address first.")
        if device is not None and device != self.device:
            raise TransportError("Bluetooth LE requires the Silhouette Cameo 4 model.")
        self._runner = asyncio.new_event_loop()
        self._client = None
        self._buffer = bytearray()
        self._ready = asyncio.Event()
        try:
            self._run(self._connect(identifier), 25, "connection")
            self.write(graphtec.frame(graphtec.QUERY_FIRMWARE_VERSION))
            reply = self.read(timeout_ms=5000)
            if not FIRMWARE.fullmatch(reply[:-1].strip()):
                raise TransportError(f"Expected Cameo 4 firmware, received {reply!r}.")
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

    async def _connect(self, identifier):
        devices = await _scan()
        match = devices.get(identifier.upper())
        if match is None:
            raise TransportError(f"Cameo 4 {identifier} was not found over Bluetooth.")
        self._client = _bleak().BleakClient(
            match[0], disconnected_callback=lambda _: self._ready.set())
        await self._client.connect()
        service = self._client.services.get_service(SERVICE)
        if service is None or any(service.get_characteristic(uuid) is None
                                  for uuid in (CONTROL, READ, WRITE)):
            raise TransportError("The device lacks the Cameo Bluetooth GATT service.")
        await self._client.start_notify(CONTROL, lambda *_: None)
        await self._client.start_notify(READ, self._on_reply)
        for uuid in (CONTROL, READ, WRITE):
            await self._client.write_gatt_char(
                uuid, graphtec.CMD_ESC + graphtec.ESC_EOT, response=True)
        await asyncio.sleep(1)
        self._buffer.clear()

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
