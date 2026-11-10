"""Defaults, paths, and the bridge to the rest of the suite."""

from __future__ import annotations

import json
import math
import os
import pathlib

from .geometry import ProxyPressSettings

CONFIG_DIR = pathlib.Path(
    os.environ.get("CUT_PROXIES_CONFIG",
                   pathlib.Path.home() / ".config" / "cut-proxies"))

TEMPLATE_DIR = pathlib.Path(
    os.environ.get("CUT_PROXIES_TEMPLATES",
                   pathlib.Path.home() / "Documents" / "cut-proxies"))

# Where the sibling projects live, for cross-checking a sheet against the
# grid that produced it.  Resolved the same way the former print-proxies resolved the
# Canon suite: an env var, then the usual checkout layout.
SUITE_ROOT = pathlib.Path(
    os.environ.get("PRINTING_SUITE_HOME",
                   pathlib.Path(__file__).resolve().parents[2]))


def proxypress_html() -> pathlib.Path | None:
    """Proxy Press's single page, if it is where it usually is."""
    candidate = SUITE_ROOT / "proxypress" / "proxy-press.html"
    return candidate if candidate.is_file() else None


# Defaults, chosen to match the Silhouette preset Proxy Press already ships
# (`BUILTIN_PRESETS`: Letter landscape, silhouette on, regInset 10, regLen 20).
DEFAULT_SHEET = "Letter"
DEFAULT_CARD = "standard"

# The sheet this workflow produces: eight 63x88 cards, four across and two
# down, on Letter or A4 turned landscape, with the marks at the standard
# 10 mm inset. Nine-up needs a 3.5 mm inset and a portrait sheet, and the
# stock does not come in a size where nine fit at 10 mm, so eight is not a
# compromise - it is the layout.
DEFAULT_PORTRAIT = False
CARDS_PER_SHEET = 8
DEFAULT_PRESET = "330 gsm black-core"
DEFAULT_MARK_INSET_MM = 10.0
DEFAULT_MARK_THICKNESS_MM = 1.0

# Current Proxy Press Cameo setup, read from the local project's Cameo
# mode/apply handlers on 2026-11-09. These settings put eight on the same
# paper and place every card within 0.07 mm of where Silhouette Card
# Maker would, which is inside the quantisation difference between SCM's
# integer-pixel layout and Proxy Press's floats.
#
# bleed 0.625 and gap 0 is the pair that matters: Card Maker spaces cards
# 1.25 mm apart, and Proxy Press expresses that as bleed on both sides.
PROXYPRESS_8UP = ProxyPressSettings(
    card_w_mm=63.0, card_h_mm=88.0, bleed_mm=0.625, gap_mm=0.0,
    margin_mm=10.0, cols=4, rows=2, rotation="upright",
    corner_radius_mm=3.2)

# Current Proxy Press Cameo registration settings, using standard marks.
PROXYPRESS_8UP_MARKS = {
    "silhouette": True,
    "regPattern": 3,
    "regInset": 10,
    "regLen": 0,          # auto: the longest arm that clears the cards
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
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(data: dict) -> pathlib.Path:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    path = settings_path()
    path.write_text(json.dumps(data, indent=2) + "\n")
    return path


# The standing blade-vs-sensor correction, in mm, measured once per machine
# with `cut-proxies calibrate` and kept in settings.json. Positive x moves
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
