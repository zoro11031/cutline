"""Shared cutter identity and Bluetooth discovery selection."""
import dataclasses
import re

from . import graphtec
from .transport import TransportError

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
_FIRMWARE = re.compile(r"(.+?)\s+V\d+(?:\.\d+)*", re.I)
_ADV_SUFFIX = re.compile(r"-[A-Z0-9]+$")
_MODEL_ALIASES = {"CAMEOPROMKII": "CAMEOPROMARK2", "CAMEOPROMARKII": "CAMEOPROMARK2"}
BLUETOOTH_MODES = {"ble": "Bluetooth LE", "bluetooth": "Bluetooth Classic"}


def advertised_model(name: str) -> graphtec.Device | None:
    name = "".join((name or "").upper().split()).removeprefix("SILHOUETTE")
    name = name.replace("Α", "ALPHA").replace("_", "")
    for variant in (name, _ADV_SUFFIX.sub("", name)):
        model = variant.replace("-", "")
        if found := _MODELS.get(_MODEL_ALIASES.get(model, model)):
            return found
    return None



def confirm_firmware(version, expected=None, advertised=None, *, allow_unknown=False):
    match = _FIRMWARE.fullmatch(version)
    confirmed = advertised_model(match[1]) if match else None
    if confirmed is None:
        if allow_unknown and expected is not None:
            return expected
        raise TransportError(f"Unrecognized Silhouette Bluetooth firmware {version!r}.")
    for selected in (expected, advertised):
        if selected is not None and confirmed != selected:
            raise TransportError(f"Expected {selected.name} firmware, received {version!r}.")
    return confirmed


@dataclasses.dataclass(frozen=True)
class Candidate:
    identifier: str
    name: str
    service: bool = False
    peripheral: object = None

    @property
    def catalog_name(self):
        if model := self.model:
            return model.name
        for name in ("Curio 2",):
            compact = "".join(self.name.upper().split()).removeprefix("SILHOUETTE")
            if compact == name.upper().replace(" ", "") or re.fullmatch(r"CURIO2-[A-Z0-9]+", compact):
                return f"Silhouette {name}"
        return None

    @property
    def model(self):
        return advertised_model(self.name)


def select_candidate(records, identifier=None, name=None, expected=None):
    """Use exact ID, then exact or partial name, then a unique known cutter."""
    if identifier and name:
        raise TransportError("Select a Bluetooth cutter by identifier or name.")
    if identifier:
        matches = [r for r in records if r.identifier.casefold() == identifier.casefold()]
    elif name:
        search = name.casefold()
        exact = [r for r in records if r.name.casefold() == search]
        matches = exact or [r for r in records if search in r.name.casefold()]
    else:
        matches = [r for r in records if (r.model is not None and (expected is None or r.model == expected))
                   or (r.model is None and r.service)]
    if len(matches) > 1 and not identifier:
        advertised = [r for r in matches if r.service]
        matches = advertised or matches
    if not matches:
        raise TransportError(f"No Bluetooth cutter matched {identifier or name or 'the selected model'}.")
    if len(matches) != 1:
        names = ", ".join(f"{r.name or 'Unnamed'} ({r.identifier})" for r in matches)
        raise TransportError(f"More than one Bluetooth cutter matched. Use its exact identifier. {names}")
    selected = matches[0]
    if expected is not None and selected.model is not None and selected.model != expected:
        raise TransportError(f"{selected.identifier} advertises {selected.model.name}. "
                             f"The checked job requires {expected.name}.")
    return selected
