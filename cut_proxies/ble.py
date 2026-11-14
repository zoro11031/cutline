"""Graphtec cutter GATT transport."""

import asyncio
import concurrent.futures
import threading

from . import graphtec
from .bluetooth import Candidate, confirm_firmware, select_candidate
from .transport import Transport, TransportError

SERVICE = "e2088282-4fde-42f9-bb22-6ec3c7ed8f91"
WRITE = "6d92661d-f429-4d67-929b-28e7a9780912"
READ = "8dcf199a-30e7-4bd4-beb6-beb57dca866c"
CONTROL = "61490654-b5b4-458c-a867-9e15bc1471e0"


def _bleak():
    try:
        import bleak
    except ImportError as exc:
        raise TransportError("Install Bleak and start the Bluetooth service to use Bluetooth LE.") from exc
    return bleak


async def _scan():
    scanner = _bleak().BleakScanner
    try:
        records = await scanner.discover(timeout=8, return_adv=True)
    except TypeError as exc:
        if "return_adv" not in str(exc):
            raise
        records = await scanner.discover(timeout=8)
    found = {}
    for entry in records.values() if isinstance(records, dict) else records:
        device, adv = entry if isinstance(entry, tuple) else (entry, None)
        name = getattr(adv, "local_name", None) or device.name or ""
        uuids = getattr(adv, "service_uuids", None) or ()
        service = any(uuid.casefold() == SERVICE for uuid in uuids)
        found[device.address.casefold()] = Candidate(device.address, name, service, device)
    return list(found.values())


def discover(include_all=False):
    async def scan():
        return await asyncio.wait_for(_scan(), 19)

    try:
        devices = sorted(asyncio.run(scan()), key=lambda r: (r.name.casefold(), r.identifier))
        return [(r.identifier, r.name) for r in devices if include_all or r.model or r.service]
    except TransportError:
        raise
    except Exception as exc:
        raise TransportError(f"Bluetooth LE discovery failed. {exc}") from exc


class BleTransport(Transport):
    name = "ble"
    can_read = True

    def __init__(self, identifier=None, device=None, *, name=None, allow_unknown=False):
        self._client = None
        self._buffer = bytearray()
        self._start_worker()
        try:
            selected = self._run(self._connect(identifier, name, device), 30, "connection")
            self.identifier = selected.identifier
            self.device = confirm_firmware(self.firmware_version(), device, selected.model,
                                           allow_unknown=allow_unknown)
        except BaseException:
            self.close()
            raise

    def _start_worker(self):
        self._runner = asyncio.new_event_loop()
        loop = self._runner

        def run():
            try:
                loop.run_forever()
            finally:
                loop.close()

        self._worker = threading.Thread(target=run, name="silhouette-bluetooth-le", daemon=True)
        self._worker.start()

    def _run(self, operation, seconds, action):
        if self._runner is None:
            operation.close()
            raise TransportError("Bluetooth LE transport is closed.")
        future = asyncio.run_coroutine_threadsafe(operation, self._runner)
        try:
            return future.result(timeout=seconds)
        except TransportError:
            raise
        except concurrent.futures.TimeoutError as exc:
            future.cancel()
            raise TransportError(f"Bluetooth {action} timed out.") from exc
        except Exception as exc:
            raise TransportError(f"Bluetooth {action} failed: {exc}") from exc

    async def _connect(self, identifier, name, expected):
        selected = select_candidate(await _scan(), identifier=identifier, name=name, expected=expected)
        self._ready = asyncio.Event()
        self._client = _bleak().BleakClient(
            selected.peripheral, disconnected_callback=lambda _: self._ready.set())
        await self._client.connect()
        service = self._client.services.get_service(SERVICE)
        if service is None or any(service.get_characteristic(uuid) is None
                                  for uuid in (CONTROL, READ, WRITE)):
            raise TransportError("The device lacks the Silhouette Bluetooth GATT service.")
        # Inkscape's GATT transport keeps control notifications out of replies.
        await self._client.start_notify(CONTROL, lambda *_: None)
        await self._client.start_notify(READ, self._on_reply)
        for uuid in (CONTROL, READ, WRITE):
            await self._client.write_gatt_char(
                uuid, graphtec.CMD_ESC + graphtec.ESC_EOT, response=True)
        await asyncio.sleep(1)
        self._buffer.clear()
        return selected

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
        raise TransportError("Bluetooth LE cutter disconnected.")

    def read(self, size=64, timeout_ms=10000):
        return self._run(self._read(), timeout_ms / 1000, "reply")

    async def _shutdown(self):
        try:
            if self._client is not None:
                await self._client.disconnect()
        finally:
            this = asyncio.current_task()
            pending = [task for task in asyncio.all_tasks() if task is not this]
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

    def close(self):
        if self._runner is None:
            return
        try:
            self._run(self._shutdown(), 5, "disconnect")
        except TransportError:
            pass
        finally:
            self._runner.call_soon_threadsafe(self._runner.stop)
            self._worker.join(timeout=5)
            self._runner = None
