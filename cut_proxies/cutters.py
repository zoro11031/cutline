"""Shared desktop cutter selection and exact endpoint binding."""

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
    return transport.open_transport(mode, device=device, bluetooth_id=identifier,
                                    bluetooth_channel=channel[0] if channel else None)


def wireless_choices():
    """Discover wireless candidates without opening a cutter session."""
    from . import ble, bluetooth, rfcomm

    try:
        records = ble.discover()
    except transport.TransportError:
        records = ()
    choices = {("ble", address): f"{name or 'Unknown Silhouette cutter'} · Bluetooth LE · {address}"
               for address, name in records}
    for record in rfcomm.paired_candidates(connected=True):
        if record.catalog_name:
            choices[("bluetooth", record.identifier)] = (
                f"{record.name} · Bluetooth Classic · {record.identifier}")
    return choices


def auto_model(endpoint, label):
    from . import bluetooth
    if endpoint and endpoint[0] in bluetooth.BLUETOOTH_MODES:
        return bluetooth.advertised_model(label.split(" · ", 1)[0])
    target = usb_target(endpoint) if endpoint else None
    return target[0] if target else None
