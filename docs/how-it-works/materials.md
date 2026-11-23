# Materials and presets

## Silhouette's material table

Silhouette Studio 5.0.556.1 ships its material cut settings as a plain CSV,
`Resources/Resources/SPEC_S/condition.csv`: the table behind the material
picker in Studio's Send panel. It was found in a read-only survey of a Studio
installation on 2026-11-07. Its columns include `(force.pressure)`, `(speed)`,
`(nth_pass)`, `(blade_depth)`, `(tool.offset)` and `cutter_class_id`, with one
row per material per machine class.

A copy of that file is bundled as `cutline/assets/condition.csv`, so the
material library works without Studio. It is Silhouette's file, replaced only
as a whole from a named Studio version, never edited by hand.
`materials.find_condition_csv` looks first for a copy the user imported (under
Cutline's settings folder), then the bundled copy, then a Studio installation:
the configured path, the macOS app bundle, `%ProgramFiles%`, or a Wine
prefix. Importing through the window writes the first, so it wins.

What the file says (2026-11-07 and 2026-11-19):

- Settings are identical across every cutter class for 22 of 26 materials;
  only Fabric, the two Heat Transfer materials and the Sketch Pen vary.
- 290 distinct leaf materials in 29 groups, 29 per cutter class across ten
  classes. Holder and action are unspecified in every row, Overcut is off in
  every row, and there are no columns for Acceleration or Track Enhancing.
- The cardstock family:

  | Material | Force | Speed | Passes | Depth |
  |---|---|---|---|---|
  | Cardstock | 20 | 4 | 1 | 0.3 mm |
  | Adhesive-Backed Cardstock | 29 | 3 | 1 | 0.4 mm |
  | Textured Cardstock | 30 | 4 | 1 | 0.3 mm |
  | Glitter Cardstock | 33 | 3 | 2 | 0.6 mm |
  | Chipboard | 33 | 1 | 2 | 0.5 mm |
  | Coverstock | 33 | 1 | 2 | 0.7 mm |

  Coverstock is the heaviest paper Silhouette publishes a setting for, and 33
  is the Cameo 4's maximum force.

Two values recovered from USB traffic are confirmed by the vendor's own data:

- `(tool.offset)` takes exactly two values: 18 for every blade material and 0
  for the Sketch Pen. inkscape-silhouette's note on the `FC` command reads
  "It is 0 for the pen, 18 for cutting"; 18 units is 0.9 mm, the standard
  blade's offset.
- `(blade_depth)` runs 0.1 to 0.9 mm, and depth × 10 is the 0–10 AutoBlade
  index that `TF` takes. Coverstock's 0.7 mm is the "depth 7" the Card Maker
  guide recommends for cardstock, from an unrelated source.

Studio also installs a 3.3 MB `SPEC_CUTTER/materials` resource with no
recognisable header and about 7.98 bits of entropy per byte. In a clean
Windows profile, a file-access trace showed Studio opening it and not
obviously the CSV, but that trace was not attributed per process, so whether
Studio still reads the CSV, and how the two relate, is open.

## Where each preset's numbers come from

Every preset declares its `origin`:

| Origin | Meaning |
|---|---|
| `silhouette` | Verbatim from Studio's `condition.csv`. A test checks it still matches. |
| `guide` | From community documentation, measured on someone else's machine. |
| `recovered` | From inkscape-silhouette's media table. |
| `derived` | Worked out here and never cut. Its note must say so, and a test checks it does. |
| `measured` | A sheet was cut on this project's machine and the edge looked at, with the date. |

Built in today: Plain paper test, Cardstock, Coverstock and Pen from
Silhouette; the Card Maker guide's starting point and its cardstock-plus-
laminate reference; and derived settings for 300 gsm cardstock and the
deep-cut blade. Older black-core preset names stay available, marked derived
and never cut, so projects that use them still open. No preset is `measured`
yet. A preset moves there only when a sheet has been cut and examined.

The reasoning used for heavy stock, so it can be argued with: passes beat
force. One very high-force pass drives the blade deep enough to emboss the
back and drag the sheet; several passes at moderate force should cut cleaner
and are kinder to the blade and the mat. The Card Maker guide gives the same
tuning order: passes, then force, then speed, then depth. Depth is a ceiling
on how far the AutoBlade extends; force and passes decide whether it gets
there.

## The speed scale is disputed

Two real sources disagree:

| Source | Cardstock speed | What it is |
|---|---|---|
| Silhouette's `condition.csv` | 1–4 | The vendor's defaults. Speed never exceeds 10 for any material or cutter class. |
| The Card Maker cutting guide | 25 | Community settings measured on a Cameo 5 with an AutoBlade. |

They cannot both describe the same scale. Perhaps the shipped table is a
legacy 1–10 scale that Studio rescales for newer machines, or the Cameo 4 and
5 really accept 1–30 and the vendor's defaults are cautious. This history is
worth recording: all presets were once rescaled to speed 20–25 on the guide's
word, with a test requiring at least 15. Studio's table contradicted that, the
change was reverted, and the test now only checks the machine's accepted
range. The question stays open until a cut decides it. The Silhouette presets
follow the vendor and the guide presets follow the guide, each saying where
its number came from.

## Staged passes on coated, core and foil stock (reported 2026-11-20)

A community thread, not a cut made here. Two users cutting coated core
stock on a Cameo reported:

- **Light first, then full.** The printed coating is weaker than the core,
  so cutting it at full force chips or tears it. Several light passes get the
  blade through the coating, then fewer full-force passes finish the core,
  where edge quality does not show. One run of 1 pass at force 10, speed 30
  then 6 at force 20, speed 25 was "so much better than before" but left
  fibers; the suggestion was more light passes and fewer heavy ones, e.g.
  4 at force 10 then 3 at 25.
- **Foil wants more of it.** One user runs foil at 8 passes at force 8 then
  4 at force 22, because the foil layer is thicker and more delicate. That is
  12 passes; Cutline's job and material test allow 10, so it has to be scaled.
- **Leftover fibers come from the bottom of the card.** Coating cut, core
  not quite through. Fix it with one more full pass or a little more force on
  the full passes only; otherwise trim with a hobby knife.
- **Speed mattered little** to either user; one runs everything at 20 and
  advised against maxing it. This sits on the Card Maker side of the
  disputed speed scale above and does not settle it.
- **Blades.** One user got raised edges from the AutoBlade whatever the
  settings and cleaner cuts from a different, cheaper replacement blade that
  dulls quickly; the other thought the same light-first approach should work
  on the AutoBlade. No conclusion.

Cutline's material-test advice and **Help → Troubleshoot a Cut** use this as
advice to try, marked as reported: **Lighter first passes** under Passes for a
job, and a light Cut Settings A then a full Cut Settings B in the material
test. None of the numbers is a preset.
