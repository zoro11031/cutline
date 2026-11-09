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
been attached here to settle it. The presets below follow the vendor,
because on expensive stock too slow costs time and too fast costs the sheet.
`docs/FINDINGS.md` section 11 records both. AutoBlade depth is 0-10 and is a
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
import json
import pathlib

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
        name="330 gsm black-core",
        description="The stock this suite prints on. Coverstock plus a pass.",
        origin="derived",
        settings=dict(force=33, speed=1, depth=7, passes=3,
                      blade_diameter_mm=0.9, lift_head=True,
                      sharpen_corners=False, media=135, acceleration=1),
        note="Silhouette's own Coverstock setting (force 33, speed 1, two "
             "passes, 0.7 mm depth) with a third pass added, because 330 gsm "
             "black-core is heavier than Coverstock and force is already at "
             "the Cameo 4's maximum of 33 - passes are the only axis left. "
             "Never cut. Work DOWN: if three passes separate cleanly, try "
             "two, which is the vendor's own figure.",
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
    return pathlib.Path.home() / ".config" / "cut-proxies" / "presets"


def bundled_dir() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parent.parent / "profiles"


def all_presets() -> dict[str, Preset]:
    """Built-ins, then bundled JSON, then the user's own, last one winning."""
    out = dict(BUILTIN_BY_NAME)
    for folder in (bundled_dir(), user_dir()):
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.json")):
            try:
                raw = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            name = raw.get("name") or path.stem
            out[name] = Preset(
                name=name,
                description=raw.get("description", ""),
                origin=raw.get("origin", "derived"),
                settings=raw.get("settings", {}),
                note=raw.get("note", ""),
            )
    return out


def get(name: str) -> Preset:
    presets = all_presets()
    if name in presets:
        return presets[name]
    # Be forgiving about case and spacing, since these get typed.
    want = name.lower().strip()
    for key, value in presets.items():
        if key.lower() == want:
            return value
    raise KeyError(f"unknown preset {name!r}; known: "
                   + ", ".join(sorted(presets)))


def save(preset: Preset) -> pathlib.Path:
    folder = user_dir()
    folder.mkdir(parents=True, exist_ok=True)
    slug = preset.name.lower().replace(" ", "-").replace(",", "")
    path = folder / f"{slug}.json"
    path.write_text(json.dumps(dataclasses.asdict(preset), indent=2) + "\n")
    return path
