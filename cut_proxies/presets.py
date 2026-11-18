"""Factory, starting, and locally saved blade presets."""

from __future__ import annotations

import dataclasses
from datetime import date
from functools import lru_cache
import hashlib
import json
import pathlib
import re
import tempfile

from .graphtec import CutSettings


@dataclasses.dataclass(frozen=True)
class Preset:
    name: str
    description: str
    origin: str                 # silhouette | recovered | derived | measured
    settings: dict
    note: str = ""

    def cut_settings(self) -> CutSettings:
        return CutSettings(**self.settings)


STARTING_NAME = "Silhouette starting point"
DEFAULT_CUTTER = "Silhouette Cameo 4"

# Keep former names usable by existing projects and command lines.
_LEGACY: tuple[Preset, ...] = (
    Preset(
        name="310 gsm black-core",
        description="User-selected 310 gsm black-core settings.",
        origin="derived",
        settings=dict(force=35, speed=25, depth=7, passes=4,
                      blade_diameter_mm=0.9, lift_head=True,
                      sharpen_corners=False, media=135, acceleration=1),
        note="User-selected settings. Never cut as part of this project's verification.",
    ),
    Preset(
        name="330 gsm black-core",
        description="User-selected 330 gsm black-core settings.",
        origin="derived",
        settings=dict(force=35, speed=25, depth=7, passes=4,
                      blade_diameter_mm=0.9, lift_head=True,
                      sharpen_corners=False, media=135, acceleration=1),
        note="User-selected settings. Never cut as part of this project's verification.",
    ),
    Preset(
        name="Coverstock (Silhouette)",
        description="The heaviest paper material Silhouette publishes a "
                    "setting for.",
        origin="silhouette",
        settings=dict(force=33, speed=1, depth=7, passes=2,
                      blade_diameter_mm=0.9, lift_head=True, media=135,
                      acceleration=1),
        note="Verbatim from Silhouette Studio 5.0.556.1's own material "
             "database (Resources/Resources/SPEC_S/condition.csv). Published "
             "by the vendor, and identical for every cutter class in that "
             "file including the Cameo 4 and 5. It is their material, not "
             "330 gsm black-core, so it is a floor rather than an answer.",
    ),
    Preset(
        name="300 gsm cardstock",
        description="Lighter card: between Textured Cardstock and "
                    "Coverstock.",
        origin="derived",
        settings=dict(force=32, speed=2, depth=6, passes=2,
                      blade_diameter_mm=0.9, lift_head=True, media=135,
                      acceleration=1),
        note="Interpolated between Silhouette's Textured Cardstock (force "
             "30, speed 4, one pass, 0.3 mm) and its Coverstock (force 33, "
             "speed 1, two passes, 0.7 mm). Never cut.",
    ),
    Preset(
        name="Cardstock (Silhouette)",
        description="Silhouette's plain cardstock setting. Far too light for "
                    "330 gsm, included as the published baseline.",
        origin="silhouette",
        settings=dict(force=20, speed=4, depth=3, passes=1,
                      blade_diameter_mm=0.9, lift_head=True, media=122,
                      acceleration=3),
        note="Verbatim from condition.csv. Here so the distance between "
             "'cardstock' as Silhouette means it and the stock this suite "
             "actually prints on is visible rather than assumed.",
    ),
    Preset(
        name="Card Maker reference (cardstock + laminate)",
        description="Alan Cha's tested reference settings for Cameo with "
                    "AutoBlade on cardstock + laminate.",
        origin="guide",
        settings=dict(force=33, speed=25, depth=7, passes=4,
                      blade_diameter_mm=0.9, lift_head=True, media=135,
                      acceleration=3),
        note="From Alan Cha's Silhouette Card Maker cutting guide "
             "(https://alan-cha.github.io/silhouette-card-maker/tutorial/guide/): "
             "tested reference settings on a Cameo with AutoBlade on recommended "
             "110 lb / 300 gsm cardstock and laminate. The guide's reference is force "
             "35 (clamped here to 33, the Cameo 4 hardware ceiling; Cameo 5 Alpha "
             "accepts up to 40), speed 25, depth 7, and 4 passes. Prioritize tuning: "
             "passes (1-4), then force, then speed, then depth. If laminate edges "
             "ripple, ease off force. If the machine skips steps, reduce speed.",
    ),
    Preset(
        name="Card Maker starting point",
        description="The Card Maker guide's starting point for determining "
                    "settings on a test sheet.",
        origin="guide",
        settings=dict(force=25, speed=25, depth=7, passes=1,
                      blade_diameter_mm=0.9, lift_head=True, media=135,
                      acceleration=3),
        note="The Silhouette Card Maker cutting guide's starting point for finding "
             "your cut settings: force 25, speed 25, depth 7, 1 pass. Cut a test sheet, "
             "and WITHOUT ejecting the mat lift a corner and check the cut; repeat "
             "the job as many times as necessary to determine the required pass count. "
             "Speed 25 is from the community guide; Silhouette's own condition.csv "
             "never exceeds 10.",
    ),
    Preset(
        name="Cameo 4 starting point",
        description="The Card Maker guide's starting point. Much faster than "
                    "Silhouette's own figures - read the note.",
        origin="guide",
        settings=dict(force=25, speed=25, depth=7, passes=1,
                      blade_diameter_mm=0.9, lift_head=True, media=135,
                      acceleration=3),
        note="The Silhouette Card Maker cutting guide's documented starting "
             "point: force 25, speed 25, depth 7, one pass, measured on a "
             "Cameo 5 with an AutoBlade. Its SPEED CONTRADICTS Silhouette's "
             "own database, which never exceeds 10 for any material on any "
             "cutter class - see docs/FINDINGS.md section 11. Both are "
             "recorded; the defaults here follow the vendor, because slower "
             "is the safer error on expensive stock. Its depth of 7 does "
             "agree with Coverstock's 0.7 mm. Use the guide's method even if "
             "you use the vendor's numbers: cut a test sheet, and WITHOUT "
             "ejecting the mat lift a corner and look at it; re-send until "
             "it separates. Tune passes, then force, then speed, then depth.",
    ),
    Preset(
        name="Deep-cut blade, 330 gsm",
        description="For the deep-cut (black) blade rather than the standard "
                    "ratchet or AutoBlade.",
        origin="derived",
        settings=dict(force=30, speed=1, passes=2, blade_diameter_mm=2.0,
                      lift_head=True, media=135, acceleration=1),
        note="The deep-cut blade is 2.0 mm rather than 0.9 mm, which changes "
             "the cutter-offset circle the machine swings on a direction "
             "change; getting that number wrong is what rounds off corners "
             "that should be square. Silhouette publishes no setting for it, "
             "so this is Coverstock with the force eased off because the "
             "geometry cuts more readily, and no depth because it is not an "
             "AutoBlade. Never cut.",
    ),
    Preset(
        name="Plain paper test",
        description="Alignment proofing on ordinary paper. Cuts through, "
                    "costs nothing.",
        origin="silhouette",
        settings=dict(force=15, speed=10, depth=2, passes=1,
                      blade_diameter_mm=0.9, lift_head=True, media=132,
                      acceleration=3),
        note="Silhouette's own 'Print Paper' setting, verbatim from "
             "condition.csv. Use this for every first run of a new template: "
             "it proves the registration and the geometry without spending "
             "card.",
    ),
    Preset(
        name="Pen, no blade",
        description="Draw the cut lines instead of cutting them.",
        origin="silhouette",
        settings=dict(force=18, speed=10, passes=1, blade_diameter_mm=0.0,
                      lift_head=False, media=132, acceleration=3),
        note="Silhouette's own 'Silhouette Sketch Pen' setting. Its tool "
             "offset is 0 where every blade in the file is 18, which is "
             "exactly inkscape-silhouette's recovered note that the FC "
             "command is ' 0 for the pen, 18 for cutting' - the vendor's data "
             "confirming a captured value. The safest possible way to check "
             "a template against a printed sheet: put a pen in the holder "
             "and let it trace where it would have cut.",
    ),
)

_LEGACY_BY_NAME = {p.name: p for p in _LEGACY}

# The bundled subset is documented against Studio's material database.
# Loading the factory file adds its complete catalog and current revisions.
_STARTING = dataclasses.replace(
    _LEGACY_BY_NAME["Card Maker starting point"], name=STARTING_NAME,
    settings={**_LEGACY_BY_NAME["Card Maker starting point"].settings, "track_enhancing": True},
    description="One-pass baseline from the Silhouette Card Maker community guide.",
    note="Start here for the Card Maker method, or use a Studio factory preset for your material. "
         "Cut a test on matching stock before sending the job.")

_FACTORY_REFERENCE = {
    "Coverstock": "Coverstock (Silhouette)",
    "Cardstock": "Cardstock (Silhouette)",
    "Print Paper": "Plain paper test",
    "Silhouette Sketch Pen": "Pen, no blade",
}

BUILTIN = (_STARTING, *(dataclasses.replace(
    _LEGACY_BY_NAME[previous], name=name,
    settings={**_LEGACY_BY_NAME[previous].settings, "track_enhancing": name in ("Cardstock", "Coverstock")},
    note="Recorded from the Silhouette Studio 5.0.556.1 factory material database.")
    for name, previous in _FACTORY_REFERENCE.items()),
    # Values recorded in docs/FINDINGS.md, section 15.
    *(Preset(name, "Silhouette Studio factory setting.", "silhouette",
             dict(force=force, speed=speed, passes=passes, depth=depth, track_enhancing=True),
             "Recorded from the Silhouette Studio 5.0.556.1 factory material database.")
      for name, force, speed, passes, depth in (
          ("Adhesive-Backed Cardstock", 29, 3, 1, 4), ("Textured Cardstock", 30, 4, 1, 3),
          ("Glitter Cardstock", 33, 3, 2, 6), ("Chipboard", 33, 1, 2, 5))))
BUILTIN_BY_NAME = {preset.name: preset for preset in BUILTIN}


@lru_cache(maxsize=4)
def _factory_rows(path, signature):
    from . import materials
    return materials.load(path)


def factory_presets(cutter=DEFAULT_CUTTER):
    from . import materials

    path = materials.find_condition_csv()
    if path is None:
        return {}
    try:
        stat = path.stat()
        rows = _factory_rows(path, (stat.st_ino, stat.st_mtime_ns, stat.st_size))
    except (OSError, ValueError):
        return {}
    return {name: Preset(**materials.as_preset(row, name))
            for name, row in materials.cutter_divisions(rows, cutter, False).items()}


def user_dir() -> pathlib.Path:
    from . import config
    return config.CONFIG_DIR / "presets"


def bundled_dir() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parent.parent / "profiles"


def _read_presets(folder: pathlib.Path):
    for path in sorted(folder.glob("*.json")):
        try:
            raw = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(raw, dict) or not isinstance(raw.get("settings", {}), dict):
            continue
        name = raw.get("name") or path.stem
        if not isinstance(name, str):
            continue
        yield path, Preset(
            name=name,
            description=raw.get("description", ""),
            origin=raw.get("origin", "derived"),
            settings=raw.get("settings", {}),
            note=raw.get("note", ""),
        )


def all_presets(cutter=DEFAULT_CUTTER) -> dict[str, Preset]:
    """Starting point, factory materials for this cutter, and local presets."""
    out = {**BUILTIN_BY_NAME, **factory_presets(cutter)}
    for folder in (bundled_dir(), user_dir()):
        out.update((preset.name, preset) for _, preset in _read_presets(folder))
    return out


ALIASES = {
    "card maker reference": "Card Maker reference (cardstock + laminate)",
    "card maker": "Card Maker reference (cardstock + laminate)",
    "cardstock + laminate": "Card Maker reference (cardstock + laminate)",
    "cardstock and laminate": "Card Maker reference (cardstock + laminate)",
    "scm reference": "Card Maker reference (cardstock + laminate)",
    "scm starting point": STARTING_NAME,
    "starting point": STARTING_NAME,
}


def get(name: str, cutter=DEFAULT_CUTTER) -> Preset:
    presets = all_presets(cutter)
    if name in presets:
        return presets[name]
    # Be forgiving about case and spacing, since these get typed.
    want = name.lower().strip()
    for catalog in (presets, _LEGACY_BY_NAME):
        if ALIASES.get(want) in catalog:
            return catalog[ALIASES[want]]
        for key, value in catalog.items():
            if key.lower() == want:
                return value
    raise KeyError(f"unknown preset {name!r}; known: "
                   + ", ".join(sorted(presets)))


def save_blade_settings(name: str, *, force: int, speed: int, depth: int | None,
                        passes: int, blade_options=None, new_name: str | None = None,
                        cutter=DEFAULT_CUTTER) -> pathlib.Path | None:
    """Save edited blade settings, retaining any unspecified preset options."""
    preset = get(name, cutter)
    settings = dict(preset.settings, force=force, speed=speed, depth=depth, passes=passes)
    settings.update(blade_options or {})
    if new_name is not None:
        new_name = new_name.strip()
        if not new_name:
            raise ValueError("Enter a preset name.")
        if any(new_name.casefold() == name.casefold() for name in all_presets(cutter)):
            raise ValueError("A preset with that name already exists.")
        preset = dataclasses.replace(preset, name=new_name)
    elif CutSettings(**settings) == preset.cut_settings():
        return None
    return save(dataclasses.replace(
        preset, origin="derived", settings=settings, description="Locally edited preset.",
        note="User-selected settings. Never cut as part of this project's verification."))


def save_tested_settings(name, settings, cutter):
    name = name.strip()
    if not name:
        raise ValueError("Enter a material name.")
    if any(name.casefold() == existing.casefold() for existing in all_presets(cutter)):
        raise ValueError("That preset name already exists. Add a detail such as the weight or finish.")
    preset = Preset(name, f"Tested on {cutter}.", "measured", dataclasses.asdict(settings),
                    f"Clean cut confirmed by the operator on {date.today().isoformat()} using {cutter}.")
    return save(preset)


def restore_default_materials() -> tuple[int, pathlib.Path | None]:
    """Restore shipped presets and back up the overridden user files."""
    folder = user_dir()
    shipped = set(BUILTIN_BY_NAME) | set(factory_presets())
    shipped.update(preset.name for _, preset in _read_presets(bundled_dir()))
    overrides = [path for path, preset in _read_presets(folder) if preset.name in shipped]
    if not overrides:
        return 0, None
    backup = pathlib.Path(tempfile.mkdtemp(prefix="material-backup-", dir=folder))
    moved = []
    try:
        for path in overrides:
            path.rename(backup / path.name)
            moved.append(path)
    except OSError:
        for path in reversed(moved):
            (backup / path.name).rename(path)
        backup.rmdir()
        raise
    return len(moved), backup


def save(preset: Preset) -> pathlib.Path:
    folder = user_dir()
    folder.mkdir(parents=True, exist_ok=True)
    existing = {item.name: path for path, item in _read_presets(folder)}
    path = existing.get(preset.name)
    if path is None:
        slug = re.sub(r"[^a-z0-9._-]+", "-", preset.name.lower().replace(",", ""))
        slug = slug.strip(".-")[:100] or "preset"
        path = folder / f"{slug}.json"
        if path.exists():
            suffix = hashlib.sha256(preset.name.encode()).hexdigest()[:12]
            path = folder / f"{slug}-{suffix}.json"
    data = json.dumps(dataclasses.asdict(preset), indent=2) + "\n"
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=folder, suffix=".tmp",
                                         delete=False) as stream:
            temp = pathlib.Path(stream.name)
            stream.write(data)
        temp.replace(path)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)
    return path
