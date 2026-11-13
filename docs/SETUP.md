# Setting up cut-proxies

## Install

```sh
ln -sf ~/Projects/Printing/cut-proxies/bin/cut-proxies ~/.local/bin/cut-proxies
```

Nothing else is required to generate templates. The whole template path —
layout, marks, SVG, DXF, the byte stream and its verification — runs on the
Python standard library.

For the desktop entry and its icon:

```sh
install -Dm644 share/cut-proxies.desktop \
  ~/.local/share/applications/org.printing.CutProxies.desktop
install -Dm644 share/icons/cut-proxies.svg \
  ~/.local/share/icons/hicolor/scalable/apps/org.printing.CutProxies.svg
update-desktop-database ~/.local/share/applications 2>/dev/null || true
```

The `.desktop` basename, the icon name and `gui.APP_ID` all have to stay
`org.printing.CutProxies`, or the compositor cannot match the window to its
launcher and you get a placeholder icon.

## Driving the cutter directly

This part needs `pyusb`, which is not installed on this machine and which
`pip` cannot install here (there is no `pip`). Use the distribution package:

```sh
sudo pacman -S python-pyusb          # Arch / CachyOS
```

`cut-proxies status` will tell you whether it found it.

### udev

Without a rule, only root can claim the cutter's USB interface. Create
`/etc/udev/rules.d/99-silhouette.rules`:

```
# Graphtec / Silhouette cutters
SUBSYSTEM=="usb", ATTR{idVendor}=="0b4d", MODE="0664", TAG+="uaccess"
# Silhouette Cameo 5 Alpha, which uses a different vendor id
SUBSYSTEM=="usb", ATTR{idVendor}=="3844", MODE="0664", TAG+="uaccess"
```

Then `sudo udevadm control --reload-rules && sudo udevadm trigger`, and
unplug and replug the cutter.

`TAG+="uaccess"` hands the device to whoever is logged in at the seat, which
is better than a group: no logout, and no permanent widening of access.

### usblp

If the kernel's `usblp` driver binds first it holds the interface and `pyusb`
cannot claim it. `cut-proxies` tries to detach it automatically. If that
fails:

```sh
sudo modprobe -r usblp
```

A registration scan has to **read** the cutter's reply, and that is the one
thing the `/dev/usb/lp*` path cannot be relied on for. For a registered cut,
use `pyusb`.

## Cameo 4 over Bluetooth LE

On Arch or CachyOS, install `python-bleak` and start the BlueZ Bluetooth
service. Enable Bluetooth on the Cameo 4. In the desktop Cut tab, choose
**Bluetooth LE**, find cutters, select the Cameo 4, and refresh its status.
On the CLI:

```sh
cut-proxies status --transport ble
cut-proxies status --transport ble --bluetooth-id AA:BB:CC:DD:EE:FF
cut-proxies cut --transport ble --bluetooth-id AA:BB:CC:DD:EE:FF \
  --layout-preset mtg-cameo-letter --preset 'Plain paper test' --dry-run
```

Use the address reported by discovery. Once the matching proof is printed
at 100% and the cutter reports ready, remove `--dry-run` for the supervised
proof. The settings-matrix test uses USB.

## Printing the sheet

The marks `cut-proxies` expects have to be on the paper. Two routes:

**From Proxy Press.** Turn on *Layout → Silhouette Cameo* (its `silhouette`
setting), or pick its built-in "Silhouette Cameo · Letter landscape" preset.
Then generate the matching template with `--engine proxypress`, which uses
Proxy Press's own grid and its own mark geometry rather than Silhouette Card
Maker's. Read `docs/FINDINGS.md` section 3 first: the two are not the same
marks, and its preset only fits four cards on a sheet.

**From the Card Maker layout.** Use `cut-proxies template` to write the proof
SVG, and lay the sheet out to match it. This is the better sheet — eight cards
at a 10 mm inset, or nine at 3.5 mm — but Proxy Press has to be set up by hand
to produce it.

Print through `print-proxies` as usual. The marks are ordinary black ink; they
need no special treatment beyond being black and unscaled, so print at exactly
100% as that tool already enforces.

## Loading the cutter

1. Put the sheet on the mat **square and in the corner**, against the guide.
   The scan has a 10 mm search window; a sheet more than about a centimetre
   out will not be found.
2. Load the mat to the guide line and press the load button.
3. `cut-proxies status` — the cutter should say `ready`. `unloaded` means it
   has no media; `moving` means it is busy.

## First cut on a new template

Do these in order. Each one costs nothing and rules out a whole class of
error:

1. `cut-proxies template …` and look at the `-proof.svg` against the printed
   sheet. Hold it up to a window if you have to. The marks must sit on the
   marks.
2. `cut-proxies cut … --dry-run`. Read the check table. `card size error` and
   `card position error` should be under 0.05 mm.
3. `cut-proxies cut … --preset "Pen, no blade"` with a pen in the holder.
   This registers for real and draws where it would cut, on the actual sheet.
   Nothing is destroyed if it is wrong.
4. `cut-proxies cut … --preset "Plain paper test"` on a printed plain-paper
   copy of the sheet.
5. Only then, card.

## Masking cards for the scan

On every Letter and A4 layout that fills the sheet, three cards end up within
about 1.2 mm of a registration mark, and the optical sensor can read the dark
card edge as part of the mark. `cut-proxies` names them:

```
Cover these for the registration scan, then uncover them before the cut:
  - card 1 (row 1, column 1) clears the top-left mark by only 1.24 mm ...
```

A sticky note or a strip of masking tape over each one is enough. Put them on
before pressing Send, and **take them off after the marks are found and
before the cut starts** — the machine pauses between the two.

Two ways to avoid it entirely:

* **Legal portrait at a 10 mm inset** gets nine cards with nothing to mask.
  It is the only layout on paper this printer can feed that manages it.
* Print one slot blank and pass `--skip N` (0-based) so that card is not
  cut. This is the same idea as Card Maker's `create_pdf.py --skip`, and the
  two have to agree: a slot skipped here but printed leaves an uncut card,
  and a slot printed blank but not skipped gets cut out of nothing.

## Finding your blade settings

The reliable way, and the one the Card Maker guide describes, is to cut and
look rather than to trust a table. `cut-proxies testcut` does the whole
sweep in one pass of the mat:

```sh
cut-proxies testcut --force 25,29,33 --passes 1,2,3 --out ~/Documents
```

Nine 15 mm squares, each cut with its own settings, on a blank offcut of the
real stock. It prints the map and writes a labelled SVG of it.

Then, **without ejecting the mat**, lift a corner of each square and see
which separated cleanly. The first combination that comes away with a light
tug is your setting. Save it:

```sh
cut-proxies materials            # for reference
# then write the winner into ~/.config/cut-proxies/presets/ with
# "origin": "measured" and the date in the note
```

Vary one axis at a time; the combinations multiply fast and the sheet gets
unreadable past about 60 squares, which is where it refuses.

## Blade settings for 330 gsm

Start at the `330 gsm black-core` preset — force 33, speed 1, depth 7, three
passes — and work **down**. If the cards separate with a light tug after three
passes, try two, which is Silhouette's own figure for Coverstock.

Force 33 is the Cameo 4's maximum, so passes are the only axis left for stock
heavier than Coverstock. Tune in this order: **passes, force, speed, depth**.
Depth is only a ceiling on how far the AutoBlade extends.

**A warning about speed.** Silhouette's own material database never uses a
speed above 10; the Card Maker community guide recommends 25 on a Cameo 5.
The presets here follow the vendor because too slow only costs time. If your
cuts are clean but glacial, the `Cameo 4 starting point` preset is the
guide's numbers — see `docs/FINDINGS.md` section 11 before trusting either.

To see Silhouette's own settings for any material:

```sh
cut-proxies materials                      # the whole table
cut-proxies materials --save Coverstock    # save one as a preset
```

That reads `condition.csv` out of the Studio installation in the guest. It is
not shipped with this project.

The card presets are extrapolated and have never been cut (see
`docs/FINDINGS.md` sections 8 and 15). Both front ends mark them `[NOT CUT]`.
When you have cut one and looked at the edge, record it:

```sh
# ~/.config/cut-proxies/presets/330-gsm-black-core.json
{
  "name": "330 gsm black-core",
  "description": "...",
  "origin": "measured",
  "note": "2026-11-xx: three passes at force 30 cut clean, no embossing on the back.",
  "settings": {"force": 30, "speed": 3, "passes": 3, "blade_diameter_mm": 0.9}
}
```

A file in `~/.config/cut-proxies/presets/` overrides the built-in of the same
name.

**Blade diameter matters more than it looks.** It sets the circle the machine
swings the blade through on a direction change. 0.9 mm is the standard ratchet
blade; the deep-cut blade is 2.0 mm; a pen is 0. Getting it wrong is what
rounds off corners that should be square.

## Calibrating the machine

If every card is cut slightly off the printed art in the same direction, the
machine has a fixed offset between where the optical sensor reports the marks
and where the tool actually lands. Measure it once:

1. Make a template and print the sheet as usual.
2. Put a **pen** in the holder and cut with `--preset "Pen, no blade"`. The
   pen draws exactly where the blade would have gone.
3. Measure how far the drawn outline sits from the printed card edge. Right
   and down are positive.
4. `cut-proxies calibrate --x <dx> --y <dy>`

Every job afterwards is shifted by that amount, and the verification step
accounts for it so the job still passes its checks. `cut-proxies calibrate`
with no arguments prints the current value and the procedure;
`--clear` resets it.

This corrects a *translation*, and only that. Two things it cannot fix:

* **The error grows across the sheet** — the sheet printed at the wrong
  scale. Fix the print; no offset can correct a scale error.
* **The card is cut right but the back art is off-centre** — a front/back
  registration problem, not a cutting one. The blade cuts one outline
  through both faces, reading only the marks on the front, so the cut is
  correct and the back is wrong. Calibrate the printer's duplex offset in
  Proxy Press instead.

## The WinBoat fallback

Only needed if the direct USB path will not register or will not cut.

```sh
cut-proxies status              # is the guest up, is Studio installed
cut-proxies studio --prepare    # stage the guest helper, print the steps
cut-proxies studio --run-helper # stage it and run it in the guest over RDP
```

`--prepare` writes `prepare-guest.ps1` into the shared folder (your home
directory, which the guest sees as `\\host.lan\Data`). Run it inside Windows
in PowerShell; it reports whether Studio is installed, whether Windows can see
a cutter, and what templates are staged, into `~/cut-proxies/guest-report.txt`
where Linux can read it.

**Windows serves one RDP session at a time.** If something else already has
it — another tool, or a window you left open — a second connection silently
does nothing rather than failing. `cut-proxies` checks first and refuses with
the name of the process holding it, and `cut-proxies status` shows the
session as free or held.

**cut-proxies does not install Silhouette Studio.** Download it from
<https://www.silhouetteamerica.com/software> inside the guest and run the
installer yourself. Once it is there:

```sh
cut-proxies studio path/to/template.dxf
```

opens it as an RDP RemoteApp. In Studio: set the page to the same size and
orientation, turn on registration marks with the same inset, and check the
import landed at 100%.

The guest login is read from `~/.winboat/docker-compose.yml` at the moment it
is needed and is never printed. Override it with `CUT_PROXIES_GUEST_USER` and
`CUT_PROXIES_GUEST_PASSWORD` if you would rather it were not read from there.

## Environment variables

| Variable | What it does |
|---|---|
| `CUT_PROXIES_CONFIG` | config directory (default `~/.config/cut-proxies`) |
| `CUT_PROXIES_TEMPLATES` | where templates are written (default `~/Documents/cut-proxies`) |
| `CUT_PROXIES_GUEST_SHARE` | what Windows calls the share (default `\\host.lan\Data`) |
| `CUT_PROXIES_GUEST_USER` / `..._PASSWORD` | guest login, instead of reading the compose file |
| `PRINTING_SUITE_HOME` | where the sibling projects live |

## Inkscape Silhouette Print & Cut

1. In Inkscape, put the printed artwork in a layer called **Print** and the
   cut geometry in **Cut**. Convert cut text to paths, unlink clones and
   flatten clips. Run **Extensions → Render → Silhouette Regmarks** with
   the standard three-mark pattern for Cameo 4 and the same inset on every
   edge. Save each page as its own Inkscape SVG and print it at 100%.
2. In the desktop app, open that SVG under **Layout → What to cut**, select
   the matching paper and orientation, and review the cut preview. The
   importer excludes groups labelled Print, CuttingMat and Regmarks using
   the extension's naming convention. It derives the registration origin
   and window from the marker shapes on the page. Cut by stroke colour
   when several cut colours are present.
3. Choose the **Plain paper test** preset for the first proof. With a
   matching sheet on the mat, use **Cut** to check cutter status and confirm
   the job. For a Letter landscape sheet, the equivalent CLI flow is:

   ```sh
   cut-proxies import design.svg --verbose
   cut-proxies cut --from design.svg --sheet Letter --landscape \
     --preset 'Plain paper test' --dry-run
   cut-proxies cut --from design.svg --sheet Letter --landscape \
     --preset 'Plain paper test' --transport pyusb
   ```

The registration import checks the plugin's original 5 mm square and 20 mm
L-mark geometry in page coordinates. A mismatched page, asymmetric marks, or
four marks on a Cameo 4 block the registered job. For an unprinted blank
sheet, turn off registration and review the sheet-coordinate paths.

The plugin's command transcript is useful for offline protocol comparison.
For a cut job, hand off the SVG so the suite can rebuild and verify its
own command stream. [Inkscape Silhouette user guide](https://github.com/fablabnbg/inkscape-silhouette/blob/main/USERGUIDE.md)

Open paths in an SVG or DXF can be sent alongside closed outlines. Use
unfilled open paths for line work such as scoring or pen strokes. Select
the appropriate tool preset before confirming. The job preview and
verifier preserve each path's endpoint and check its vertices on every
pass.

## Create a cut template in the desktop app

Choose the paper and orientation in **Layout**, then open **Template editor**
under **What to cut**. Add rectangles, rounded rectangles, ellipses, or lines.
Drag shapes on the sheet or enter their dimensions and position in
millimetres. Use a signed horizontal or vertical change to draw a line
toward the left or top.

**Save project** keeps the shapes editable in a `.scc-template` file.
To reopen it, select the same paper and orientation and use **Open project**
inside the editor. **Use template** loads the geometry into the regular
checked job. From there, use **Export** for SVG, DXF and the print proof, or
choose your tool and send the checked job from **Cut**.
