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

from . import graphtec
from .graphtec import CutSettings


@dataclasses.dataclass(frozen=True)
class Preset:
    name: str
    description: str
    origin: str                 # silhouette | recovered | derived | measured
    settings: dict
    note: str = ""
    factory_id: str = ""
    factory_group: str = ""
    factory_holder: int | None = None
    factory_tool: str = ""
    factory_action: str = ""

    def cut_settings(self) -> CutSettings:
        return CutSettings(**self.settings)


STARTING_NAME = "Silhouette starting point"
DEFAULT_CUTTER = "Silhouette Cameo 4"

# Keep former names usable by existing projects and command lines.
_LEGACY: tuple[Preset, ...] = (
    Preset(
        name="310 gsm black-core",
        description="Older settings for 310 gsm black-core stock.",
        origin="derived",
        settings=dict(force=35, speed=25, depth=7, passes=4,
                      blade_diameter_mm=0.9, lift_head=True,
                      sharpen_corners=False, media=135, acceleration=1),
        note="Never cut in this project.",
    ),
    Preset(
        name="330 gsm black-core",
        description="Older settings for 330 gsm black-core stock.",
        origin="derived",
        settings=dict(force=35, speed=25, depth=7, passes=4,
                      blade_diameter_mm=0.9, lift_head=True,
                      sharpen_corners=False, media=135, acceleration=1),
        note="Never cut in this project.",
    ),
    Preset(
        name="Coverstock (Silhouette)",
        description="Factory preset for heavy cardstock.",
        origin="silhouette",
        settings=dict(force=33, speed=1, depth=7, passes=2,
                      blade_diameter_mm=0.9, lift_head=True, media=135,
                      acceleration=1),
        note="From Silhouette Studio.",
    ),
    Preset(
        name="300 gsm cardstock",
        description="Older settings for 300 gsm cardstock.",
        origin="derived",
        settings=dict(force=32, speed=2, depth=6, passes=2,
                      blade_diameter_mm=0.9, lift_head=True, media=135,
                      acceleration=1),
        note="Based on Studio’s cardstock settings. Never cut.",
    ),
    Preset(
        name="Cardstock (Silhouette)",
        description="Factory preset for plain cardstock.",
        origin="silhouette",
        settings=dict(force=20, speed=4, depth=3, passes=1,
                      blade_diameter_mm=0.9, lift_head=True, media=122,
                      acceleration=3),
        note="From Silhouette Studio.",
    ),
    Preset(
        name="Card Maker reference (cardstock + laminate)",
        description="Settings for laminated cardstock.",
        origin="guide",
        settings=dict(force=33, speed=25, depth=7, passes=4,
                      blade_diameter_mm=0.9, lift_head=True, media=135,
                      acceleration=3),
        note="From Alan Cha’s Silhouette Card Maker guide.",
    ),
    Preset(
        name="Card Maker starting point",
        description="One pass for a material test.",
        origin="guide",
        settings=dict(force=25, speed=25, depth=7, passes=1,
                      blade_diameter_mm=0.9, lift_head=True, media=135,
                      acceleration=3),
        note="From the Silhouette Card Maker guide.",
    ),
    Preset(
        name="Cameo 4 starting point",
        description="One pass for a material test.",
        origin="guide",
        settings=dict(force=25, speed=25, depth=7, passes=1,
                      blade_diameter_mm=0.9, lift_head=True, media=135,
                      acceleration=3),
        note="From the Silhouette Card Maker guide.",
    ),
    Preset(
        name="Deep-cut blade, 330 gsm",
        description="Older settings for a deep-cut blade and heavy card.",
        origin="derived",
        settings=dict(force=30, speed=1, passes=2, blade_diameter_mm=2.0,
                      lift_head=True, media=135, acceleration=1),
        note="Never cut in this project.",
    ),
    Preset(
        name="Plain paper test",
        description="Cut a sample from ordinary paper.",
        origin="silhouette",
        settings=dict(force=15, speed=10, depth=2, passes=1,
                      blade_diameter_mm=0.9, lift_head=True, media=132,
                      acceleration=3),
        note="Studio’s Print Paper preset.",
    ),
    Preset(
        name="Pen, no blade",
        description="Draw the cut lines with a sketch pen.",
        origin="silhouette",
        settings=dict(force=18, speed=10, passes=1, blade_diameter_mm=0.0,
                      lift_head=False, media=132, acceleration=3),
        note="Studio’s Silhouette Sketch Pen preset.",
    ),
)

_LEGACY_BY_NAME = {p.name: p for p in _LEGACY}

# The bundled subset is documented against Studio's material database.
# Loading the factory file adds its complete catalog and current revisions.
_STARTING = dataclasses.replace(
    _LEGACY_BY_NAME["Card Maker starting point"], name=STARTING_NAME,
    settings={**_LEGACY_BY_NAME["Card Maker starting point"].settings, "track_enhancing": True},
    description="One pass to see how your material cuts.",
    note="From the Silhouette Card Maker guide. Try it on a scrap of the same stock.")

_FACTORY_REFERENCE = {
    "Coverstock": "Coverstock (Silhouette)",
    "Cardstock": "Cardstock (Silhouette)",
    "Print Paper": "Plain paper test",
    "Silhouette Sketch Pen": "Pen, no blade",
}

_FORMER_BUILTIN = (_STARTING, *(dataclasses.replace(
    _LEGACY_BY_NAME[previous], name=name,
    settings={**_LEGACY_BY_NAME[previous].settings, "track_enhancing": name in ("Cardstock", "Coverstock")},
    note="From Silhouette Studio’s factory settings.")
    for name, previous in _FACTORY_REFERENCE.items()),
    # Values recorded in docs/FINDINGS.md, section 15.
    *(Preset(name, "Silhouette Studio factory setting.", "silhouette",
             dict(force=force, speed=speed, passes=passes, depth=depth, track_enhancing=True),
             "From Silhouette Studio’s factory settings.")
      for name, force, speed, passes, depth in (
          ("Adhesive-Backed Cardstock", 29, 3, 1, 4), ("Textured Cardstock", 30, 4, 1, 3),
          ("Glitter Cardstock", 33, 3, 2, 6), ("Chipboard", 33, 1, 2, 5))))
BUILTIN = (_STARTING,)
_LEGACY_BY_NAME.update((preset.name, preset) for preset in _FORMER_BUILTIN if preset != _STARTING)
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
            factory_id=raw.get("factory_id", ""),
            factory_group=raw.get("factory_group", ""),
            factory_holder=raw.get("factory_holder"),
            factory_tool=raw.get("factory_tool", ""),
            factory_action=raw.get("factory_action", ""),
        )


def all_presets(cutter=DEFAULT_CUTTER) -> dict[str, Preset]:
    """Starting point, factory materials for this cutter, and local presets."""
    out = {**BUILTIN_BY_NAME, **factory_presets(cutter)}
    for folder in (bundled_dir(), user_dir()):
        for _, preset in _read_presets(folder):
            if preset.factory_id:
                for label, factory in out.items():
                    if factory.factory_id == preset.factory_id:
                        out[label] = dataclasses.replace(preset, name=label)
                        break
            else:
                label = preset.name
                if label in out and out[label].factory_id:
                    label += " · Personal"
                out[label] = dataclasses.replace(preset, name=label)
    return out


def for_context(preset, cutter, holder, *, tool=None, action=None):
    """Resolve one compatible variant of a selected factory material."""
    if not preset.factory_group:
        if preset.factory_holder not in (None, holder):
            raise ValueError(f"No compatible variant of {preset.name} for Holder {holder}.")
        return preset
    matches = [item for item in all_presets(cutter).values()
               if item.factory_group == preset.factory_group
               and item.factory_holder in (None, holder)
               and (not tool or item.factory_tool in ("", tool))
               and (not action or item.factory_action in ("", action))]
    if not matches:
        raise ValueError(f"No compatible variant of {preset.name} for Holder {holder}.")
    def specificity(item):
        return (item.factory_holder == holder, bool(tool and item.factory_tool == tool),
                bool(action and item.factory_action == action))
    best = max(map(specificity, matches))
    choices = [item for item in matches if specificity(item) == best]
    if preset in choices:
        return preset
    if len(choices) == 1:
        return choices[0]
    raise ValueError(f"Choose a {preset.name} variant for Holder {holder}.")


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
        preset = dataclasses.replace(preset, name=new_name, factory_id="", factory_group="",
                                     factory_holder=None, factory_tool="", factory_action="")
    elif CutSettings(**settings) == preset.cut_settings():
        return None
    return save(dataclasses.replace(
        preset, origin="derived", settings=settings, description="Settings you saved.",
        note="Saved without a recorded test. Never cut as part of this project’s verification."))


def save_tested_settings(name, settings, cutter):
    name = name.strip()
    if not name:
        raise ValueError("Enter a material name.")
    if any(name.casefold() == existing.casefold() for existing in all_presets(cutter)):
        raise ValueError("That preset name already exists. Add a detail such as the weight or finish.")
    preset = Preset(name, f"Tested on {cutter}.", "measured", dataclasses.asdict(settings),
                    f"Clean cut checked on {date.today().isoformat()} with {cutter}.")
    return save(preset)


def restore_default_materials() -> tuple[int, pathlib.Path | None]:
    """Restore shipped presets and back up the overridden user files."""
    folder = user_dir()
    shipped = set(BUILTIN_BY_NAME) | {preset.name for preset in _FORMER_BUILTIN}
    for device in graphtec.DEVICES:
        shipped.update(factory_presets(device.name))
    shipped.update(preset.name for _, preset in _read_presets(bundled_dir()))
    overrides = [path for path, preset in _read_presets(folder)
                 if preset.factory_id or (preset.name in shipped and preset.origin == "derived"
                                          and preset.description == "Settings you saved.")]
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


def is_deletable(name: str, cutter=DEFAULT_CUTTER) -> bool:
    """True if the preset is a user-created preset or a user-overridden factory preset."""
    folder = user_dir()
    if not folder.is_dir():
        return False
    factory = factory_presets(cutter)
    target_factory_id = factory[name].factory_id if name in factory else None
    for path, preset in _read_presets(folder):
        if preset.name == name:
            return True
        if target_factory_id and preset.factory_id == target_factory_id:
            return True
    return False


def delete_preset(name: str, cutter=DEFAULT_CUTTER) -> bool:
    """Delete a user-saved preset or user override, reverting to factory/builtin if applicable."""
    folder = user_dir()
    if not folder.is_dir():
        return False
    factory = factory_presets(cutter)
    target_factory_id = factory[name].factory_id if name in factory else None
    deleted = False
    for path, preset in _read_presets(folder):
        if preset.name == name or (target_factory_id and preset.factory_id == target_factory_id):
            try:
                path.unlink(missing_ok=True)
                deleted = True
            except OSError:
                return False
    return deleted


def save(preset: Preset) -> pathlib.Path:
    folder = user_dir()
    folder.mkdir(parents=True, exist_ok=True)
    identity = preset.factory_id or preset.name
    existing = {(item.factory_id or item.name): path for path, item in _read_presets(folder)}
    path = existing.get(identity)
    if path is None:
        slug = re.sub(r"[^a-z0-9._-]+", "-", preset.name.lower().replace(",", ""))
        slug = slug.strip(".-")[:100] or "preset"
        path = folder / f"{slug}.json"
        if path.exists():
            suffix = hashlib.sha256(identity.encode()).hexdigest()[:12]
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
