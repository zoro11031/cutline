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
    Machine division recorded for this variant. The factory snapshot retains
    the original row and category ancestry independently of the picker.

"""

from __future__ import annotations

import csv
import dataclasses
import pathlib
import os
import tempfile

from . import config
from .graphtec import CutSettings, su_to_mm

# A material heavier than anything else made of paper in the table; the
# closest published analogue to 330 gsm black-core stock.
HEAVIEST_PAPER = "Coverstock"

STUDIO_TITLES = {
    "media_SilhouetteHeatTransferFlocked_title": "Heat Transfer Material (Flocked)",
    "media_SilhouetteHeatTransferSmooth_title": "Heat Transfer Material (Smooth)",
    "media_SilhouettePrintableHeatTransferSheetDark_title": "Printable Heat Transfer Material (Dark)",
    "media_SilhouettePrintableHeatTransferSheetLight_title": "Printable Heat Transfer Material (Light)",
    "media_CottonCanvas_title": "Fabric (Cotton Canvas)",
    "media_FabricWithInterfacing_title": "Fabric (with Interfacing)",
}


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
    source_id: str = ""
    group_id: str = ""
    categories: tuple[str, ...] = ()
    holder: int | None = None
    action: str = ""
    acceleration: int | None = None
    track_enhancing: bool | None = None
    line_segment_overcut: bool | None = None
    corner_start_mm: float | None = None
    corner_end_mm: float | None = None
    raw: tuple[tuple[str, str], ...] = ()

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
            lift_head=bool(self.offset_su),
            holder=self.holder or 1,
            acceleration=self.acceleration,
            track_enhancing=self.track_enhancing or False,
            sharpen_corners=self.line_segment_overcut or False,
            **{key: value for key, value in (
                ("corner_start_mm", self.corner_start_mm),
                ("corner_end_mm", self.corner_end_mm)) if value is not None},

        )

    def describe(self) -> str:
        d = f"{self.depth_mm:g} mm" if self.depth_mm is not None else "-"
        return (f"force {self.force}, speed {self.speed}, "
                f"{self.passes} pass(es), depth {d}, "
                f"blade {self.blade_diameter_mm:g} mm")


def find_condition_csv() -> pathlib.Path | None:
    """Find the user's chosen CSV, repo copy, or a copy in the app configuration directory."""
    snapshot = config.CONFIG_DIR / "studio-materials" / "condition.csv"
    if snapshot.is_file():
        return snapshot
    repo_copy = pathlib.Path(__file__).parent / "assets" / "condition.csv"
    if repo_copy.is_file():
        return repo_copy
    selected = config.load_settings().get("material_csv")
    roots = [config.CONFIG_DIR, pathlib.Path("/Applications/Silhouette Studio.app/Contents")]
    if os.environ.get("ProgramFiles"):
        roots.append(pathlib.Path(os.environ["ProgramFiles"]) / "Silhouette America" / "Silhouette Studio")
    wine = pathlib.Path(os.environ.get("WINEPREFIX", pathlib.Path.home() / ".wine"))
    roots.append(wine / "drive_c/Program Files/Silhouette America/Silhouette Studio")
    paths = [pathlib.Path(selected).expanduser()] if isinstance(selected, str) and selected else []
    for root in roots:
        paths.extend((root / "condition.csv", root / "Resources/Resources/SPEC_S/condition.csv"))
    return next((path for path in paths if path.is_file()), None)


def select_condition_csv(path):
    """Validate and snapshot the factory source without touching personal settings."""
    path = pathlib.Path(path).resolve()
    if path.is_dir():
        candidates = [path / "Resources/Resources/SPEC_S/condition.csv", path / "condition.csv"]
        path = next((candidate for candidate in candidates if candidate.is_file()), candidates[0])
    data = path.read_bytes()
    destination = config.CONFIG_DIR / "studio-materials" / "condition.csv"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as pending:
        temporary = pathlib.Path(pending.name)
        pending.write(data)
    try:
        rows = load(temporary)
        if not rows:
            raise ValueError(f"{path} contains no usable material settings.")
        if not destination.is_file() or destination.read_bytes() != data:
            temporary.replace(destination)
        return rows
    finally:
        temporary.unlink(missing_ok=True)


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
    with open(path, encoding="utf-8-sig", newline="") as fh:
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
        except (ValueError, OverflowError):
            return None

    parents = {_cell(row, idx, "uuid"): _cell(row, idx, "parent_uuid") for row in body}

    def categories(parent):
        result = []
        visited = set()
        while parent and parent not in visited:
            visited.add(parent)
            if names.get(parent):
                result.append(names[parent])
            parent = parents.get(parent)
        return tuple(reversed(result[1:]))

    def flag(row, key):
        value = _cell(row, idx, key).lower()
        if value in ("on", "true", "1"):
            return True
        if value in ("off", "false", "0"):
            return False
        return None

    out: list[Material] = []
    rejected = []
    seen_ids = set()
    for lineno, row in enumerate(rows[2:], start=3):
        if _cell(row, idx, "category") != "leaf":
            continue
        name_id = _cell(row, idx, "name_id")
        name = (STUDIO_TITLES.get(name_id)
                or names.get(_cell(row, idx, "parent_uuid"))
                or _cell(row, idx, "name_resolved"))
        force = number(row, "(force.pressure)")
        speed = number(row, "(speed)")
        source_id = _cell(row, idx, "uuid")
        if not name or force is None or speed is None:
            rejected.append(f"row {lineno}: missing material name, force or speed")
            continue
        if source_id and source_id in seen_ids:
            rejected.append(f"row {lineno}: repeated variant identifier {source_id}")
            continue
        if source_id:
            seen_ids.add(source_id)
        out.append(Material(
            name=name,
            cutter_class=_cell(row, idx, "cutter_class_id"),
            force=force, speed=speed,
            passes=number(row, "(nth_pass)") or 1,
            depth_mm=number(row, "(blade_depth)", float),
            offset_su=number(row, "(tool.offset)") or 0,
            tool=_cell(row, idx, "(tool.type)"),
            source_id=source_id,
            group_id=_cell(row, idx, "parent_uuid"),
            holder=number(row, "(tool_holder)"),
            action=_cell(row, idx, "(action)"),
            acceleration=number(row, "(acceleration)"),
            track_enhancing=flag(row, "(track_enhancing)"),
            categories=categories(_cell(row, idx, "parent_uuid")),
            line_segment_overcut=flag(row, "(tangential_emulation)"),
            corner_start_mm=number(row, "(overcut.start_point_length)", float),
            corner_end_mm=number(row, "(overcut.end_point_length)", float),
            raw=tuple(zip(rows[0], row)),

        ))
    if rejected:
        raise ValueError("Studio material import rejected " + "; ".join(rejected))
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
    def normalize(value):
        return "".join(char for char in value.casefold() if char.isalnum())

    model = cutter_name.removeprefix("Silhouette ")
    family = normalize(model.split()[0]) if model else ""
    exact = normalize(model)
    ranks = {exact: 2, family: 1, "": 0}
    groups = {}
    for material in rows:
        division = normalize(material.cutter_class)
        if show_all or division in ranks:
            groups.setdefault(material.name, []).append(material)
    choices = {}
    for name, variants in groups.items():
        if not show_all:
            # A model override replaces only the same holder/tool/action entry.
            # Family defaults for the other tools and holders remain available.
            contexts = {}
            for row in variants:
                key = row.group_id, row.holder, normalize(row.tool), normalize(row.action)
                contexts.setdefault(key, []).append(row)
            variants = []
            for rows in contexts.values():
                rank = max(ranks[normalize(row.cutter_class)] for row in rows)
                variants.extend(row for row in rows if ranks[normalize(row.cutter_class)] == rank)
        for row in variants:
            label = name
            if show_all or len(variants) > 1:
                parts = []
                if show_all and row.cutter_class:
                    parts.append(row.cutter_class)
                if row.tool and row.tool != "adjustable":
                    parts.append(row.tool)
                if row.action:
                    parts.append(row.action)
                if row.holder and (row.holder > 1 or len(variants) > 1):
                    parts.append(f"Holder {row.holder}")
                details = " · ".join(filter(None, parts))
                if details:
                    label = f"{name} · {details}"
                if label in choices:
                    idx_num = 2
                    while f"{label} ({idx_num})" in choices:
                        idx_num += 1
                    label = f"{label} ({idx_num})"
            choices[label] = row
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
        "description": f"Studio settings for {material.name}.",
        "origin": "silhouette",
        "factory_id": material.source_id,
        "factory_group": material.group_id,
        "factory_holder": material.holder,
        "factory_tool": material.tool,
        "factory_action": material.action,
        "note": note or "From Silhouette Studio’s factory settings.",
        "settings": dataclasses.asdict(s),
    }
