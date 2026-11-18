"""Silhouette's own material cut settings, read from a Studio installation.

Silhouette Studio ships its material database as plain CSV at

    Resources/Resources/SPEC_S/condition.csv

inside its install directory. That file is the vendor's published answer to
"what force and speed does this material want", and it is the only source in
this project that is neither recovered by packet capture nor extrapolated. A
preset built from it is the one kind that can honestly be labelled
`origin: "silhouette"`.

The file is **not vendored into this repository** — it is Silhouette's, it
ships with their software, and copying it here would be redistributing it.
`load()` reads a file chosen by the user or a local copy in the app configuration directory.

What the columns mean, and how they map onto what the machine is sent:

`(force.pressure)`
    The force index, 1-33. Sent verbatim as `FX`.

`(speed)`
    The speed index. **Every row in the file is between 1 and 10**, including
    the rows for the Cameo 4 and Cameo 5 cutter classes — see
    `docs/FINDINGS.md` section 11, because this is the opposite of what a
    community guide written for a Cameo 5 will tell you.

`(blade_depth)`
    Millimetres of blade extension, 0.1 to 0.9. The AutoBlade depth command
    (`TF`) takes an index 0-10, and the mapping is depth_mm x 10: Coverstock's
    0.7 mm is the "depth 7" that the Card Maker guide recommends for
    cardstock. That correspondence is an inference, clearly, but it is an
    inference between two numbers that agree.

`(tool.offset)`
    Takes exactly two values in the whole file: 18 for every blade and 0 for
    the sketch pen. That is precisely inkscape-silhouette's note on the `FC`
    command — "0 for the pen, 18 for cutting" — so this column is `FC` in
    Silhouette units, and 18 SU is the 0.9 mm standard blade. Independent
    confirmation of a recovered value from the vendor's own data.

`cutter_class_id`
    One row per material per machine class (`cameo`, `cc300-20`, `cc330-20`,
    `portrait`, ...). In release 5.0.556.1 the settings are **identical
    across every class** for all but four materials (Fabric, both Heat
    Transfer variants and the Sketch Pen), so the class is recorded but is
    not used to pick a row unless asked for.
"""

from __future__ import annotations

import csv
import dataclasses
import pathlib

from . import config
from .graphtec import CutSettings, su_to_mm

# A material heavier than anything else made of paper in the table; the
# closest published analogue to 330 gsm black-core stock.
HEAVIEST_PAPER = "Coverstock"


@dataclasses.dataclass(frozen=True)
class Material:
    name: str
    cutter_class: str
    force: int
    speed: int
    passes: int
    depth_mm: float | None
    offset_su: int
    tool: str

    @property
    def blade_diameter_mm(self) -> float:
        """The `FC` value in millimetres: 0.9 for a blade, 0 for a pen."""
        return su_to_mm(self.offset_su)

    @property
    def autoblade_depth(self) -> int | None:
        """The AutoBlade `TF` index, 0-10, from the millimetre extension."""
        if self.depth_mm is None:
            return None
        return max(0, min(10, round(self.depth_mm * 10)))

    def cut_settings(self) -> CutSettings:
        return CutSettings(
            force=self.force, speed=self.speed, passes=self.passes,
            depth=self.autoblade_depth,
            blade_diameter_mm=self.blade_diameter_mm,
            lift_head=True,
        )

    def describe(self) -> str:
        d = f"{self.depth_mm:g} mm" if self.depth_mm is not None else "-"
        return (f"force {self.force}, speed {self.speed}, "
                f"{self.passes} pass(es), depth {d}, "
                f"blade {self.blade_diameter_mm:g} mm")


def find_condition_csv() -> pathlib.Path | None:
    """Find the user's chosen CSV or a copy in the app configuration directory."""
    selected = config.load_settings().get("material_csv")
    if isinstance(selected, str) and selected:
        path = pathlib.Path(selected).expanduser()
        if path.is_file():
            return path
    default = config.CONFIG_DIR / "condition.csv"
    return default if default.is_file() else None


def select_condition_csv(path):
    """Remember a valid CSV explicitly selected in the app."""
    path = pathlib.Path(path).resolve()
    rows = load(path)
    if not rows:
        raise ValueError(f"{path} contains no usable material settings.")
    settings = config.load_settings()
    settings["material_csv"] = str(path)
    config.save_settings(settings)
    return rows


def _cell(row, idx, key):
    i = idx.get(key)
    if i is None or i >= len(row):
        return ""
    return row[i].strip()


def load(path=None) -> list[Material]:
    """Parse a Studio `condition.csv` into materials.

    The file has two header rows - machine keys, then human labels - then a
    tree: `root`, `folder` and `group` rows carry names, and `leaf` rows carry
    the settings and point back at their group through `parent_uuid`.
    """
    path = pathlib.Path(path) if path else find_condition_csv()
    if path is None:
        raise FileNotFoundError(
            "No Silhouette condition.csv found. Choose a local copy in Blade or pass --file.")
    with open(path, encoding="utf-8-sig", errors="replace", newline="") as fh:
        rows = list(csv.reader(fh))
    if len(rows) < 3:
        raise ValueError(f"{path} is not a Studio condition.csv")
    idx = {h: i for i, h in enumerate(rows[0])}
    if "(force.pressure)" not in idx:
        raise ValueError(
            f"{path} has no (force.pressure) column; not a condition.csv")

    body = [r for r in rows[2:] if len(r) > 5]
    names = {_cell(r, idx, "uuid"): (_cell(r, idx, "name_resolved")
                                     or _cell(r, idx, "full_name"))
             for r in body}

    def number(row, key, cast=int):
        raw = _cell(row, idx, key)
        if raw in ("", "-"):
            return None
        try:
            return cast(float(raw))
        except ValueError:
            return None

    out: list[Material] = []
    for row in body:
        if _cell(row, idx, "category") != "leaf":
            continue
        name = (names.get(_cell(row, idx, "parent_uuid"))
                or _cell(row, idx, "name_resolved"))
        force = number(row, "(force.pressure)")
        speed = number(row, "(speed)")
        if not name or force is None or speed is None:
            continue
        out.append(Material(
            name=name,
            cutter_class=_cell(row, idx, "cutter_class_id"),
            force=force, speed=speed,
            passes=number(row, "(nth_pass)") or 1,
            depth_mm=number(row, "(blade_depth)", float),
            offset_su=number(row, "(tool.offset)") or 0,
            tool=_cell(row, idx, "(tool.type)"),
        ))
    return out


def by_name(path=None, cutter_class: str = "") -> dict[str, Material]:
    """One material per name, preferring a given cutter class if asked.

    Settings are identical across classes for all but four materials in the
    shipped file, so the default picks whichever row comes first and says so
    rather than pretending the choice matters.
    """
    out: dict[str, Material] = {}
    for m in load(path):
        if m.name in out:
            if cutter_class and m.cutter_class == cutter_class:
                out[m.name] = m
            continue
        out[m.name] = m
    return out


def cutter_divisions(rows, cutter_name: str, show_all: bool) -> dict[str, Material]:
    """Expose the material divisions recorded by Studio's database."""
    if show_all:
        choices = {}
        for material in rows:
            division = material.cutter_class or "General"
            tool = f" · {material.tool}" if material.tool else ""
            label = f"{material.name} · {division}{tool}"
            choices.setdefault(label, material)
        return choices

    def normalize(value):
        return "".join(char for char in value.lower() if char.isalnum())

    model = cutter_name.removeprefix("Silhouette ")
    family = normalize(model.split()[0]) if model else ""
    exact = normalize(model)

    def rank(material):
        division = normalize(material.cutter_class)
        if division and division == exact:
            return 2
        return int(bool(division) and division == family)

    choices = {}
    for material in rows:
        current = choices.get(material.name)
        if current is None or rank(material) > rank(current):
            choices[material.name] = material
    return choices


def varies_by_cutter(path=None) -> list[str]:
    """Materials whose settings are not the same for every machine class."""
    seen: dict[str, set] = {}
    for m in load(path):
        seen.setdefault(m.name, set()).add(
            (m.force, m.speed, m.passes, m.depth_mm, m.offset_su, m.tool))
    return sorted(n for n, v in seen.items() if len(v) > 1)


def as_preset(material: Material, name: str = "", note: str = "") -> dict:
    """A preset dictionary, ready for `presets.Preset`, citing its source."""
    s = material.cut_settings()
    return {
        "name": name or f"{material.name} (Silhouette)",
        "description": f"Silhouette's own setting for {material.name}.",
        "origin": "silhouette",
        "note": (note or
                 f"Read verbatim from Silhouette Studio's own material "
                 f"database (Resources/Resources/SPEC_S/condition.csv, "
                 f"cutter class {material.cutter_class!r}): "
                 f"{material.describe()}. Published by the vendor, but for "
                 f"their material, not for 330 gsm black-core stock."),
        "settings": {
            "force": s.force, "speed": s.speed, "passes": s.passes,
            "depth": s.depth, "blade_diameter_mm": s.blade_diameter_mm,
            "lift_head": True,
        },
    }
