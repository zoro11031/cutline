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
