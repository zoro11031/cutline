"""Group imported contours by Studio's Line, Fill, and Layer modes."""

from __future__ import annotations

import dataclasses

from . import importer, presets
from .graphtec import CutSettings
from .template import CutPath

SEND_MODES = {"simple": "Simple", "line": "Line", "fill": "Fill", "layer": "Layer"}
CURRENT_SETTINGS = "Current Blade settings"
HOLDER_SETTINGS = {1: "Tool Holder 1 settings", 2: "Tool Holder 2 settings"}


@dataclasses.dataclass(frozen=True)
class Assignment:
    key: str
    preset: str = CURRENT_SETTINGS
    holder: int | None = None


@dataclasses.dataclass(frozen=True)
class CutGroup:
    key: str
    preset: presets.Preset
    settings: CutSettings
    paths: tuple[CutPath, ...]
    warnings: tuple[str, ...] = ()

    def describe(self) -> str:
        s = self.settings
        depth = f", AutoBlade {s.depth}" if s.depth is not None else ""
        return (f"{self.preset.name}, holder {s.holder}, force {s.force}, "
                f"speed {s.speed}, {s.passes} pass(es){depth}")


def _paint_key(value: str) -> str:
    return "(none)" if value in ("", "none", "(none)", "transparent") else importer.normalise_colour(value)


def key_for(path: CutPath, mode: str) -> str:
    if mode == "line":
        return _paint_key(path.stroke)
    if mode == "fill":
        return _paint_key(path.fill)
    if mode == "layer":
        return " / ".join(path.layer) or "(no layer)"
    raise ValueError(f"Unknown grouped send mode {mode!r}.")


def group_paths(paths, mode: str) -> dict[str, tuple[CutPath, ...]]:
    groups: dict[str, list[CutPath]] = {}
    for path in paths:
        groups.setdefault(key_for(path, mode), []).append(path)
    return {key: tuple(pieces) for key, pieces in groups.items()}


def parse_assignment(value: str) -> Assignment:
    key, separator, profile = value.partition("=")
    if not separator or not key.strip():
        raise ValueError("Use --group 'GROUP=PRESET' or 'GROUP=2:PRESET'.")
    profile = profile.strip()
    holder, colon, remaining = profile.partition(":")
    if colon and holder.strip().isdigit():
        if int(holder) not in (1, 2):
            raise ValueError("A group can use tool holder 1 or 2.")
        return Assignment(key.strip(), remaining.strip() or CURRENT_SETTINGS, int(holder))
    return Assignment(key.strip(), profile or CURRENT_SETTINGS)


def resolve(paths, mode, assignments, preset, current, device, holder_profiles=None,
            *, manual_depth=False) -> tuple[CutGroup, ...]:
    available = group_paths(paths, mode)
    if not assignments:
        raise ValueError("Enable at least one cut group or choose Simple.")
    resolved = []
    seen = set()
    holder_tools = {}
    for item in assignments:
        key = _paint_key(item.key) if mode in ("line", "fill") else item.key
        if key in seen:
            raise ValueError(f"Cut group {key!r} is assigned more than once.")
        if key not in available:
            raise ValueError(f"Cut group {item.key!r} is absent. Available groups are "
                             + ", ".join(available) + ".")
        seen.add(key)
        slot = next((slot for slot, label in HOLDER_SETTINGS.items() if label == item.preset), None)
        if slot is not None and holder_profiles is not None:
            if item.holder not in (None, slot):
                raise ValueError(f"Group {key!r} uses Tool Holder {slot} settings. Choose the same holder.")
            group_preset, settings = holder_profiles[slot]
        else:
            try:
                group_preset = preset if item.preset == CURRENT_SETTINGS else presets.get(item.preset, device.name)
            except KeyError:
                raise ValueError(f"Unknown preset {item.preset!r} in group {key!r}.") from None
            if item.preset != CURRENT_SETTINGS:
                group_preset = presets.for_context(group_preset, device.name,
                                                  item.holder or group_preset.cut_settings().holder)
            settings = current if item.preset == CURRENT_SETTINGS else group_preset.cut_settings()
            if item.holder is not None:
                settings = dataclasses.replace(settings, holder=item.holder)
        if manual_depth and settings.holder == 1:
            settings = dataclasses.replace(settings, depth=None)
        # A change of settings can adjust an installed AutoBlade between cuts,
        # but it cannot swap a pen and blade or undo physical manual depth.
        warnings = tuple(settings.warnings(device))
        settings = settings.clamped(device)
        physical_tool = (settings.blade_diameter_mm == 0, settings.depth is not None)
        if holder_tools.setdefault(settings.holder, physical_tool) != physical_tool:
            raise ValueError(f"Groups using holder {settings.holder} require different tools or "
                             "manual and automatic depth. Assign them to separate holders or sends.")
        resolved.append(CutGroup(key, group_preset, settings, available[key], warnings))
    return tuple(resolved)
