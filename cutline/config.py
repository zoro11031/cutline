"""Defaults and paths."""

from __future__ import annotations

import json
import math
import os
import pathlib

from .geometry import CenteredGridSettings
from .presets import STARTING_NAME


def _moved_forward(new: pathlib.Path, old: pathlib.Path) -> pathlib.Path:
    """Earlier versions kept their files under `old`; move them once.

    Only when `new` does not exist yet, so nothing is ever overwritten, and
    a failed move leaves the old folder where it was."""
    if not new.exists() and old.is_dir():
        try:
            new.parent.mkdir(parents=True, exist_ok=True)
            old.rename(new)
        except OSError:
            return old
    return new


def _settled(new: pathlib.Path, *olds: pathlib.Path) -> pathlib.Path:
    """Move the first earlier folder that exists forward to `new`."""
    for old in olds:
        place = _moved_forward(new, old)
        if place != new:
            return place
    return new


if "CUTLINE_CONFIG" in os.environ:
    CONFIG_DIR = pathlib.Path(os.environ["CUTLINE_CONFIG"])
else:
    CONFIG_DIR = _settled(pathlib.Path.home() / ".config" / "cutline",
                          pathlib.Path.home() / ".config" / "kerf",
                          pathlib.Path.home() / ".config" / "cut-proxies")

if "CUTLINE_TEMPLATES" in os.environ:
    TEMPLATE_DIR = pathlib.Path(os.environ["CUTLINE_TEMPLATES"])
else:
    TEMPLATE_DIR = _settled(pathlib.Path.home() / "Documents" / "Cutline",
                            pathlib.Path.home() / "Documents" / "Kerf",
                            pathlib.Path.home() / "Documents" / "cut-proxies")


# Defaults: Letter landscape, registration on, marks at a 10 mm inset,
# automatic arm length.
DEFAULT_SHEET = "Letter"
DEFAULT_CARD = "standard"

# The sheet this workflow produces: eight 63x88 cards, four across and two
# down, on Letter or A4 turned landscape, with the marks at the standard
# 10 mm inset. Nine-up needs a 3.5 mm inset and a portrait sheet, and the
# stock does not come in a size where nine fit at 10 mm, so eight is not a
# compromise - it is the layout.
DEFAULT_PORTRAIT = False
CARDS_PER_SHEET = 8
DEFAULT_PRESET = STARTING_NAME
DEFAULT_MARK_INSET_MM = 10.0
DEFAULT_MARK_THICKNESS_MM = 1.0

# The eight-up centred grid. These settings put eight on the paper and place
# every card within 0.07 mm of where Silhouette Card Maker would, which is
# inside the quantisation difference between SCM's integer-pixel layout and
# a centred grid computed in floats.
#
# bleed 0.625 and gap 0 is the pair that matters: Card Maker spaces cards
# 1.25 mm apart, and a centred grid expresses that as bleed on both sides.
CARD_SHEET_8UP = CenteredGridSettings(
    card_w_mm=63.0, card_h_mm=88.0, bleed_mm=0.625, gap_mm=0.0,
    margin_mm=10.0, cols=4, rows=2, rotation="upright",
    corner_radius_mm=3.2)

# Registration settings for the eight-up sheet, using standard marks.
CARD_SHEET_MARKS = {
    "silhouette": True,
    "regPattern": 3,
    "regInset": 10,
    "regLen": 0,  # 0 = automatic; stock landscape presets carry the resolved lengths.
    "regThickness": 1,
}

def settings_path() -> pathlib.Path:
    return CONFIG_DIR / "settings.json"


def load_settings() -> dict:
    path = settings_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    if data.get("engine") == "proxypress":  # the centred grid's earlier name
        data["engine"] = "centered"
    return data


def save_settings(data: dict) -> pathlib.Path:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    path = settings_path()
    path.write_text(json.dumps(data, indent=2) + "\n")
    return path


# The standing blade-vs-sensor correction, in mm, measured once per machine
# with `cutline calibrate` and kept in settings.json. Positive x moves
# the cut right, positive y moves it down - the same sense as every other
# coordinate in this project.
def cut_offset() -> tuple[float, float]:
    s = load_settings().get("cut_offset_mm")
    if not isinstance(s, (list, tuple)) or len(s) < 2:
        return 0.0, 0.0
    try:
        x, y = float(s[0]), float(s[1])
        if not (math.isfinite(x) and math.isfinite(y)):
            return 0.0, 0.0
        return x, y
    except (TypeError, ValueError, IndexError):
        return 0.0, 0.0


def save_cut_offset(dx: float, dy: float) -> pathlib.Path:
    try:
        x, y = float(dx), float(dy)
    except (TypeError, ValueError):
        raise ValueError(f"cut_offset must be numbers, got ({dx!r}, {dy!r})") from None
    if not (math.isfinite(x) and math.isfinite(y)):
        raise ValueError(f"cut_offset must be finite numbers, got ({dx!r}, {dy!r})")
    data = load_settings()
    data["cut_offset_mm"] = [round(x, 3), round(y, 3)]
    return save_settings(data)


def template_dir() -> pathlib.Path:
    TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
    return TEMPLATE_DIR


def tested_presets() -> set[str]:
    """Return preset names the user has confirmed as tested on this stock."""
    raw = load_settings().get("tested_presets")
    if not isinstance(raw, list):
        return set()
    return {item.strip() for item in raw if isinstance(item, str) and item.strip()}


def set_presets_tested(names, tested: bool = True) -> pathlib.Path:
    """Persist one or more preset names as user-tested (or clear them) in settings.json."""
    cleaned = [name.strip() for name in names if isinstance(name, str) and name.strip()]
    current = tested_presets()
    if tested:
        current.update(cleaned)
    else:
        current.difference_update(cleaned)
    data = load_settings()
    if current:
        data["tested_presets"] = sorted(current)
    else:
        data.pop("tested_presets", None)
    return save_settings(data)


def set_preset_tested(name: str, tested: bool = True) -> pathlib.Path:
    """Persist a single preset name as user-tested (or clear it) in settings.json."""
    return set_presets_tested((name,), tested=tested)


def is_preset_tested(preset, tested: set[str] | frozenset[str] | None = None) -> bool:
    """True when a preset is measured or explicitly marked tested by the user."""
    if preset is None:
        return False
    if getattr(preset, "origin", None) == "measured":
        return True
    known = tested_presets() if tested is None else tested
    name = getattr(preset, "name", preset)
    return isinstance(name, str) and name in known


DESIGN_UI_DEFAULTS = {
    "sidebar_width": 300,
    "sidebar_width_user": False,
    "sidebar_collapsed": False,
    "properties_ratio": 0.45,
    "sidebar_split_user": False,
    "active_panel": "transform",
    "units": "mm",
    "rulers": True,
    "grid": False,
    "guides": True,
    "mat": True,
    "mat_grid": True,
    "cut_border": True,
    "print_border": False,
    "snap": False,
    "smart_snap": False,
}


def load_design_ui() -> dict:
    """Return persisted Design workspace layout and view preferences."""
    raw = load_settings().get("design_ui")
    state = dict(DESIGN_UI_DEFAULTS)
    if not isinstance(raw, dict):
        return state
    if isinstance(raw.get("sidebar_width"), (int, float)) and not isinstance(raw.get("sidebar_width"), bool):
        state["sidebar_width"] = max(220, min(560, int(round(raw["sidebar_width"]))))
    if isinstance(raw.get("properties_ratio"), (int, float)) and not isinstance(raw.get("properties_ratio"), bool):
        ratio = float(raw["properties_ratio"])
        if math.isfinite(ratio):
            state["properties_ratio"] = max(0.15, min(0.85, round(ratio, 3)))
    if isinstance(raw.get("active_panel"), str) and raw["active_panel"]:
        state["active_panel"] = raw["active_panel"]
    if raw.get("units") in ("mm", "in"):
        state["units"] = raw["units"]
    for key in ("sidebar_width_user", "sidebar_collapsed", "sidebar_split_user",
                "rulers", "grid", "guides", "mat", "mat_grid",
                "cut_border", "print_border", "snap", "smart_snap"):
        if isinstance(raw.get(key), bool):
            state[key] = raw[key]
    return state


def save_design_ui(updates: dict) -> pathlib.Path:
    """Persist Design workspace layout and view preferences into settings.json."""
    data = load_settings()
    current = load_design_ui()
    for key, value in updates.items():
        if key not in DESIGN_UI_DEFAULTS:
            continue
        if key == "sidebar_width" and isinstance(value, (int, float)) and not isinstance(value, bool):
            current[key] = max(220, min(560, int(round(value))))
        elif key == "properties_ratio" and isinstance(value, (int, float)) and not isinstance(value, bool):
            if math.isfinite(float(value)):
                current[key] = max(0.15, min(0.85, round(float(value), 3)))
        elif key == "active_panel" and isinstance(value, str) and value:
            current[key] = value
        elif key == "units" and value in ("mm", "in"):
            current[key] = value
        elif isinstance(DESIGN_UI_DEFAULTS[key], bool) and isinstance(value, bool):
            current[key] = value
    data["design_ui"] = current
    return save_settings(data)
