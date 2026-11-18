"""Blade presets, and where their numbers come from.

**Read this before trusting a number in this file.**

Silhouette publishes force/speed/pass recommendations per material, and its
heaviest stock entry is "Cardstock, Textured" territory - around 270 gsm.  The
330 gsm black-core stock this suite prints on is past the end of every
published table, exactly as it is past the end of the Canon's paper-weight
range.  The media table recovered by inkscape-silhouette stops at
"Tag 100 lbs (275g)" with force 20, speed 10.

So the presets here are **extrapolations, clearly labelled as such**, except
where a note says a cut was looked at.  `origin` on every preset says which
it is:

  "silhouette"  - from Silhouette's own published material settings
  "guide"       - from the Silhouette Card Maker cutting guide, which is
                  community documentation measured on someone else's machine
  "recovered"   - from inkscape-silhouette's media table
  "derived"     - extrapolated here from the above, never cut
  "measured"    - a sheet was cut on THIS machine and the edge was examined

Nothing is "measured" yet, because there is no cutter on this machine to cut
with.  When one is attached, cut the calibration sheet, look at the edge, and
move the preset you used from "derived" to "measured" with the date - the same
discipline `canon-suite/docs/FINDINGS.md` applies to the printer.

**Scales, and an unresolved disagreement.** Force and speed are unitless
machine indices. The machine accepts force 1-33 and, on the Cameo 4 and 5,
speed 1-30. But Silhouette's *own* shipped material database never uses a
speed above 10 for any material on any cutter class, the Cameo 4 and 5
classes included, while the Card Maker community guide recommends 25 on a
Cameo 5. Those cannot both be describing the same scale, and no cutter has
been attached here to settle it. The black-core presets use the requested
starting values. The project retains each cutter's force limit at send time.
`docs/FINDINGS.md` section 11 records both speed references. AutoBlade depth is 0-10 and is a
*ceiling*: it sets how far the blade can extend, and force and passes decide
whether that depth is reached. The vendor's file gives depth in millimetres,
and depth_mm x 10 lines up with the guide's index.

**Tune in this order:** passes, then force, then speed, then depth. That is
the guide's order and it matches what the machine actually does.

Multi-pass beats force on heavy stock.  A single very high force pass pushes
the blade deep enough to emboss the back of the sheet and to drag the card
sideways in the gap between the mat and the blade housing; two or three passes
at a moderate force cut cleaner and are kinder to both the blade and the mat.
"""

from __future__ import annotations

import dataclasses
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


BUILTIN: tuple[Preset, ...] = (
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

BUILTIN_BY_NAME = {p.name: p for p in BUILTIN}


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


def all_presets() -> dict[str, Preset]:
    """Built-ins, then bundled JSON, then the user's own, last one winning."""
    out = dict(BUILTIN_BY_NAME)
    for folder in (bundled_dir(), user_dir()):
        out.update((preset.name, preset) for _, preset in _read_presets(folder))
    return out


ALIASES = {
    "card maker reference": "Card Maker reference (cardstock + laminate)",
    "card maker": "Card Maker reference (cardstock + laminate)",
    "cardstock + laminate": "Card Maker reference (cardstock + laminate)",
    "cardstock and laminate": "Card Maker reference (cardstock + laminate)",
    "scm reference": "Card Maker reference (cardstock + laminate)",
    "scm starting point": "Card Maker starting point",
    "starting point": "Card Maker starting point",
}


def get(name: str) -> Preset:
    presets = all_presets()
    if name in presets:
        return presets[name]
    # Be forgiving about case and spacing, since these get typed.
    want = name.lower().strip()
    if want in ALIASES and ALIASES[want] in presets:
        return presets[ALIASES[want]]
    for key, value in presets.items():
        if key.lower() == want:
            return value
    raise KeyError(f"unknown preset {name!r}; known: "
                   + ", ".join(sorted(presets)))


def save_blade_settings(name: str, *, force: int, speed: int, depth: int | None,
                        passes: int) -> pathlib.Path | None:
    """Save the four visible blade settings while retaining the other options."""
    preset = get(name)
    settings = dict(preset.settings, force=force, speed=speed, depth=depth, passes=passes)
    if settings == preset.settings:
        return None
    return save(dataclasses.replace(
        preset, origin="derived", settings=settings, description="Locally edited preset.",
        note="User-selected settings. Never cut as part of this project's verification."))


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
