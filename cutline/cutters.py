"""Shared desktop cutter selection and exact endpoint binding."""

import atexit
import sys
import threading

from . import graphtec, transport

AUTO = "Auto Detect"
FAMILIES = ("Cameo", "Portrait", "Curio", "Legacy")
LEGACY = {
    "Silhouette Curio": ("Curio", ((0x0B4D, 0x112C),)),
    "Silhouette Curio 2": ("Curio", ()),
    "Silhouette SD": ("Legacy", ((0x0B4D, 0x111C), (0x0B4D, 0x111D))),
    "Original Silhouette": ("Legacy", ()),
    "Craft ROBO CC200-20": ("Legacy", ((0x0B4D, 0x110A),)),
    "Craft ROBO CC300-20": ("Legacy", ((0x0B4D, 0x111A),)),
    **{name: ("Legacy", ()) for name in (
        "Craft ROBO CC100-20", "Craft ROBO CC300L-30", "Craft ROBO CC330L-30",
        "Craft ROBO Lite CC300-30", "Craft ROBO Lite CC330-30", "Graphtec CE Lite-50",
        "Xyron Wishblade")},
}
MODELS = {AUTO: AUTO}
for family in FAMILIES:
    MODELS.update((device.name, f"{family} · {device.name.removeprefix('Silhouette ')}")
                  for device in graphtec.DEVICES if device.name.startswith(f"Silhouette {family}"))
    MODELS.update((name, f"{family} · {name}") for name, (group, _) in LEGACY.items()
                  if group == family)


# With no cutter available, both windows offer a scan in the status row, as
# Silhouette Studio does, and say what to try when it finds nothing.
SCAN_LABEL = "Scan for Cutters"
SCANNING_TEXT = "Scanning for cutters…"
NOT_DETECTED_TEXT = "Cutter did not respond. Turn it on, then scan for cutters again."
# On Linux a scan also finds and pairs Bluetooth cutters; elsewhere they are
# paired in the system's settings first.
NO_CUTTER_TEXT = ("No cutter found. Turn the cutter on and connect it by USB, or turn on its "
                  "Bluetooth and scan again." if sys.platform.startswith("linux") else
                  "No cutter found. Turn the cutter on and connect it by USB, "
                  "or pair it in your system's Bluetooth settings.")


def usb_choices():
    choices = {}
    for model, _, endpoint in transport.usb_endpoints(include_unknown=True):
        if isinstance(model, tuple):
            names = [name for name, (_, ids) in LEGACY.items() if model in ids]
            name = " / ".join(names) or f"Unknown cutter {model[0]:04x}:{model[1]:04x}"
        else:
            name = model.name
        choices[endpoint] = f"{name} · USB {endpoint[0]}:{endpoint[1]}"
    return choices


def usb_target(selected=None):
    devices = transport.usb_endpoints()
    if selected is not None:
        return next((item for item in devices if item[2] == selected), None)
    return devices[0] if len(devices) == 1 else None


def usb_refusal(connection, expected, model=AUTO):
    if model in LEGACY:
        return f"{model}: sending unavailable."
    if connection[0] != "auto":
        return ""
    target = usb_target(connection[1])
    if connection[1] is None or target is None:
        return "Select a connected USB cutter." if usb_choices() else "Connect a cutter."
    if target[0] != expected:
        return f"Connected: {target[0].name}. Review the selected cutter."
    return ""


def open_link(connection, device=None):
    mode, identifier, *channel = connection
    if mode == "auto":
        target = usb_target(identifier) if identifier is not None else None
        if target is None:
            raise transport.NotFound("Select a connected USB cutter.")
        if device is not None and target[0] != device:
            raise transport.TransportError(f"Connected cutter is {target[0].name}.")
        return transport.open_transport(mode, device=target[0], usb_endpoint=identifier)
    if not identifier:
        raise transport.NotFound("Select a Bluetooth cutter.")
    channel = channel[0] if channel else None
    if mode in HELD_MODES:
        return _HeldLink((mode, identifier, channel), device)
    return transport.open_transport(mode, device=device, bluetooth_id=identifier,
                                    bluetooth_channel=channel)


# One Bluetooth Classic connection is kept open across actions, as Studio
# 5.0.556.1 does (captured 2026-11-21: one link, status polled about 1 Hz).
# Measured the same day on a Cameo 4: an idle link answered status in ~20 ms
# after 60 s, while a new session took 3-55 s whenever the cutter stalled it.
# LE is not held: no LE session to a Cameo has worked, so nothing is known
# about how one behaves when kept.
HELD_MODES = frozenset({"bluetooth"})
# Release the cutter after a while unused, so Studio or a terminal command
# can reach it; the next action reopens.
IDLE_CLOSE_S = 300
_held_lock = threading.Lock()   # guards _held and its timer
_use_lock = threading.RLock()   # one action on the link at a time
_held = None                    # (key, link)
_idle_timer = None


def _answers(link):
    """Whether the link still answers a status query (ESC ENQ). The front
    ends raise TransportError for refusals such as "no media is loaded" on a
    link that is perfectly healthy; only a link that no longer answers goes."""
    try:
        link.status()
        return True
    except (transport.TransportError, OSError):
        return False


def _drop_held():
    """Close and forget the held link. Caller holds _held_lock."""
    global _held
    if _held is not None:
        try:
            _held[1].close()
        except Exception:
            pass
        _held = None


def _close_if_idle(link):
    if not _use_lock.acquire(blocking=False):
        return  # lent out; the next release starts a new timer
    try:
        with _held_lock:
            if _held is not None and _held[1] is link:
                _drop_held()
    finally:
        _use_lock.release()


def close_held():
    """Close the held link, if any (application exit, or a test)."""
    global _idle_timer
    with _held_lock:
        if _idle_timer is not None:
            _idle_timer.cancel()
            _idle_timer = None
        _drop_held()


atexit.register(close_held)


class _HeldLink:
    """Lend the held link for one action. A transport failure during the
    action drops it, so the next action opens a fresh one."""

    def __init__(self, key, device):
        self.key, self.device, self.link = key, device, None

    def __enter__(self):
        global _held, _idle_timer
        _use_lock.acquire()
        try:
            with _held_lock:
                if _idle_timer is not None:
                    _idle_timer.cancel()
                    _idle_timer = None
                if _held is not None and _held[0] != self.key:
                    _drop_held()
                if _held is not None and not _answers(_held[1]):
                    _drop_held()
                if _held is None:
                    mode, identifier, channel = self.key
                    _held = (self.key, transport.open_transport(
                        mode, device=self.device, bluetooth_id=identifier, bluetooth_channel=channel))
                self.link = _held[1]
                _nearby.pop(self.key[1], None)   # opening it paired it
            if self.device is not None and self.link.device != self.device:
                raise transport.TransportError(f"Connected cutter is {self.link.device.name}.")
            return self.link
        except BaseException:
            _use_lock.release()
            raise

    def __exit__(self, kind, error, trace):
        global _idle_timer
        try:
            with _held_lock:
                if isinstance(error, (transport.TransportError, OSError)) and not _answers(self.link):
                    if _held is not None and _held[1] is self.link:
                        _drop_held()
                elif _held is not None and _held[1] is self.link:
                    _idle_timer = threading.Timer(IDLE_CLOSE_S, _close_if_idle, (self.link,))
                    _idle_timer.daemon = True
                    _idle_timer.start()
        finally:
            _use_lock.release()
        return False


# Classic cutters the last search heard that BlueZ keeps no key for, by
# address. Listed until paired: BlueZ itself forgets them ~30 s after a search.
_nearby = {}
# Cutter names heard over LE and searched for over Classic once already.
_searched_le = set()


def forget_nearby():
    _nearby.clear()
    _searched_le.clear()


def unpaired(endpoint):
    """Whether choosing this endpoint pairs a cutter first (it was found by a
    search and not yet paired), so the windows can say so."""
    return bool(endpoint) and endpoint[0] == "bluetooth" and endpoint[1] in _nearby


def wireless_choices(search=False):
    """Discover wireless candidates without opening a cutter session. Only a
    search runs a Classic inquiry: it takes 13 s and can make Bluetooth audio
    stutter, so the periodic refresh does not."""
    from . import ble, bluetooth, rfcomm

    heard = []
    inquiry = threading.Thread(
        target=lambda: heard.extend(rfcomm.nearby_candidates(fresh=True)), daemon=True)
    if search:
        inquiry.start()
    try:
        records = ble.discover()
    except transport.TransportError:
        records = ()
    paired = {record.identifier: record for record in rfcomm.paired_candidates()
              if record.catalog_name}
    name_key = lambda name: (name or "").strip().casefold()
    le_names = {name_key(name) for _, name in records}
    connected = {record.identifier: record for record in rfcomm.paired_candidates(connected=True)
                 if record.catalog_name}
    known_names = {name_key(record.name) for record in paired.values()}
    nearby_names = {name_key(record.name) for record in _nearby.values()}
    le_cutters = {name_key(name) for _, name in records
                  if bluetooth.advertised_model(name or "") is not None}
    _searched_le.intersection_update(le_cutters)
    if not search and (new := le_cutters - known_names - nearby_names - _searched_le):
        _searched_le.update(new)
        search = True
        inquiry.start()
    if search:
        inquiry.join()
        _nearby.clear()
        _nearby.update((record.identifier, record) for record in heard)
    else:
        for address, record in tuple(_nearby.items()):
            if name_key(record.name) not in le_names:
                _nearby.pop(address)
    classic = {**_nearby, **connected}
    for address, record in paired.items():
        if name_key(record.name) in le_names:
            classic[address] = record
        _nearby.pop(address, None)
    classic_names = {name_key(record.name) for record in classic.values()}
    choices = {("bluetooth", address): f"{record.name} · Bluetooth Classic · {address}"
               for address, record in classic.items()}
    choices.update({("ble", address): f"{name or 'Unknown Silhouette cutter'} · Bluetooth LE · {address}"
                    for address, name in records if name_key(name) not in classic_names})
    return choices


def discover_desktop(mode):
    if mode == "ble":
        from . import ble
        return ble.discover()
    return [(endpoint[1], label.split(" · ", 1)[0])
            for endpoint, label in wireless_choices(search=True).items()
            if endpoint[0] == mode]


def wireless_endpoint(endpoint):
    from . import bluetooth

    return bool(endpoint) and endpoint[0] in bluetooth.BLUETOOTH_MODES


def wireless_refusal(connection, verified):
    if wireless_endpoint(connection) and connection != verified:
        return "Check the cutter connection first."
    return ""


def auto_model(endpoint, label):
    from . import bluetooth
    if endpoint and endpoint[0] in bluetooth.BLUETOOTH_MODES:
        return bluetooth.advertised_model(label.split(" · ", 1)[0])
    target = usb_target(endpoint) if endpoint else None
    return target[0] if target else None


def detected_choice(choices, preferred):
    """Keep an explicit choice or prefer an unambiguous USB connection."""
    from . import bluetooth

    if preferred is not None:
        return preferred if preferred in choices else None
    usb = {endpoint: label for endpoint, label in choices.items()
           if endpoint[0] not in bluetooth.BLUETOOTH_MODES}
    candidates = usb
    if len(candidates) == 1:
        endpoint, label = next(iter(candidates.items()))
        if auto_model(endpoint, label):
            return endpoint
    return None
