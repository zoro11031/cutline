# Silhouette Control Center

Design cut paths, line them up with a print, and send them to a Silhouette
cutter from Linux. Use **Design** to draw or edit a template and **Send** to
choose a material, check the cutter, and cut. Both Qt and GTK have the same
workflow and share projects, presets, and calibration.

![Silhouette Control Center Send workspace](screenshots/send-workspace.png)

## Install

From a checkout, run `./bin/silhouette-control-center gui`. See the
[setup guide](docs/SETUP.md#install) to install the desktop and USB
dependencies, or use one of the [package recipes](packaging/README.md).
For vector editing from a checkout, install GEOS 3.10 or later and Pango/Cairo.

If you have both frontends installed, choose one with
`gui --frontend qt` or `gui --frontend gtk`. The launcher tries Qt first.

## Use

Open **Setup Guide** in either app for a quick walkthrough. For your first cut:

1. Draw in **Design**, open SVG, DXF, or Studio3 cut artwork in **Layout**,
   or open a ProxyPress ZIP in **Sheet**.
2. Open **Send** and choose your connection, material, and tool holder.
   Try **Dial In a Material** with a scrap of the same material.
3. Cutting a printed sheet? Load it face-up, check the PDF in **Sheet**,
   and choose **Test Registration** at the bottom of **Send**.
4. Check the preview and choose **Send to Cutter**.

The preview has **Fit**, zoom buttons, and Ctrl+scroll zoom. When a PDF is
open, choose **Cut paths**, **Print PDF**, or **Overlay**. Open **Preview
Options** for the job checks or colorblind mode.

### From the terminal

| To do this | Run this |
|---|---|
| Check the connection | `silhouette-control-center status` |
| Try the default cut without moving the cutter | `silhouette-control-center cut --dry-run` |
| Export a Letter template | `silhouette-control-center template --sheet Letter --landscape` |
| Check a print PDF | `silhouette-control-center check-sheet printed.pdf` |
| Preview an imported drawing | `silhouette-control-center cut --from drawing.svg --dry-run` |
| Use a pen to check alignment | `silhouette-control-center cut --preset "Silhouette Sketch Pen"` |

Run `silhouette-control-center --help` to see all commands. The template
export includes a proof SVG with registration marks. Use that proof as a
visual guide and send the separate cut SVG or DXF to your cutter.

## Design workspace

![Silhouette Control Center Design workspace](screenshots/design-workspace.png)

Open **Design** to edit the current layout or choose **New** for a blank page.
In **File**, **Open** loads a project, SVG, DXF, or supported Studio3 file.
**Import Artwork** adds paths to the open project. **Save** keeps shapes editable in an
`.scc-template` file.

| Panel | What you can do |
|---|---|
| Page Setup | Set paper size, orientation, mat, printed marks, and grid. |
| Drawing Tool | Draw shapes, add text, or use Point Edit to move nodes on a path. |
| Transform | Move, resize, rotate, mirror, align, or space objects. |
| Replicate | Make rows and columns of the selection. |
| Line and Fill | Set colors and pick Cut, Score, or None. |

Use the **Design / Send** switch to move between workspaces while keeping your
draft open. Choose **Review in Send** to apply the design to the cut job.
Choose **All actions** to give Cut and Score their own settings, then review
them in **Cut Job** before sending them to the cutter.

Use **Edit** to undo, redo, duplicate, or delete objects. Use **View** for
zoom, page rotation, grid, snapping, and borders. Set your
printer margin under **Page Setup → Advanced Options** to see the print
border. **Panels** jumps to colors, line styles, Transform, and Replicate.
**Object → Modify** has Weld, Subtract, Intersect, and Offset for closed shapes.
Subtract keeps the lowest selected object and removes the parts covered by the
other selected objects. **Make Compound Path** combines contours into one object
with holes. **Release Compound Path** makes its outlines separate objects.

Select a text object and choose **Edit Text** to change the words, font, or size.
Projects save the exact outlines used for the preview and cut. If a font is
missing, you can still cut the saved outlines. Install the font or choose a
replacement to edit the text.

Studio3 import brings in supported vectors and page size.

## Sticker Print & Cut

1. In **Design**, choose **File → New** for a blank sheet. Choose **Place Image** and open a PNG, JPEG, or WebP. Move or resize it on the sheet.
2. Select the image and choose **Trace and Bleed**. Automatic uses transparency when present. Use **Dark artwork** or **Light artwork** for opaque images, and choose whether to cut interior holes. Bleed extends the print past the cut path in millimetres.
3. Choose **Export Print & Cut** to save the ZIP. It contains the full-size PDF with registration marks and a separate SVG of the Cut outlines. The project keeps the source image, so it can be reopened and traced again.
4. Open the ZIP in **Send → Sheet**, choose **Save Print PDF**, and print at **Actual size** or **100%**. Load the sheet face-up as shown. Check the registration marks before sending the cut job.

## Silhouette Card Maker templates

Under **Send → Layout → Layout preset**, choose an **SCM** template for an existing Silhouette Card Maker print. Letter and A4 each include Standard, Poker, Japanese, Bridge, Catan, Tarot, and nine-card Standard Borderless. Match the paper, card size, and borderless setting used to print your sheet.

These templates use cut outlines and registration settings from Alan Cha’s Silhouette Card Maker. Open the matching PDF in **Sheet** to check the marks and see which edge goes into the cutter. The preview turns sideways PDFs into the template’s loading orientation. [Upstream templates](https://github.com/Alan-Cha/silhouette-card-maker/tree/main/cutting_templates)

## Working with Proxy Press

![Checking printed registration marks and cut alignment](screenshots/sheet-alignment.png)

Open the export in **Sheet → Open PDF or ZIP** and choose the printed sheet
number. The ZIP supplies its matching PDF page, artwork, and mark settings.
Load the print face-up, aligned with the top-left of the mat grid.

Select **Test Registration** before cutting. If the cutter misses the marks,
review the PDF check in **Sheet**. The PDF check can point out a mark that
looks wrong. The cutter reports whether the scan passed.

Older ZIPs may contain a Studio3 file or DXF. Use **Sheet → Save Paired Cut
File** to save that file. For direct cutting from the ZIP, export one that
includes a cut SVG.

For the stock eight-card layout, choose **Silhouette Card Maker** under
**Print Settings → Cutting** in Proxy Press and select Letter or A4. The
standard template uses a 10 mm registration inset, 0.625 mm bleed, and no
printed cut guides. Export the **print & cut ZIP** so the cut file matches
the PDF.

For an existing Proxxied print, use the cut file that matches its PDF.
If you want to try a stock Card Maker template with Proxxied, turn off
guides, set bleed to 0.625 mm, and set the registration inset to 10 mm.
Check that the card positions and registration marks match the template.
The official templates are designed for the corresponding Card Maker PDF.
[Silhouette Card Maker cutting guide](https://alan-cha.github.io/silhouette-card-maker/tutorial/guide/)

```sh
silhouette-control-center check-sheet cards-print-and-cut.zip --bundle-sheet 2
silhouette-control-center cut --from cards-print-and-cut.zip --bundle-sheet 2 --dry-run
```

For Inkscape layouts, see [Inkscape Silhouette Print & Cut](docs/SETUP.md#inkscape-silhouette-print--cut).

## Finding your settings

![Guided material tuning and test cuts](screenshots/material-guide.png)

Open **Send → Dial In a Material**. Choose a factory preset for your stock
or try **Silhouette starting point**. With an AutoBlade in holder 1 on a
Cameo 4, it starts at force 25, speed 25, depth 7, and one pass.

1. Load the same material you plan to cut. The preview starts at the bottom-left.
2. Choose **Cut One Pass**. Keep the mat loaded and use **Repeat One Pass**
   as needed. The preview records the confirmed pass count for each spot.
3. Choose **Next Test Spot** to try different settings. When the cut looks
   right, choose **Use Tested Settings** to apply the current spot’s count.

To unload and reload a test sheet, open **Test Options → Use Registration
Marks** and **Save Marked Test Sheet**. Print the SVG at actual size. Each
pass locates the sheet using its marks. Reload the same sheet when resuming.
Control Center keeps the marked sheet’s passes and spot positions when you
close the app. **New Test Sheet** clears that sheet’s history.

Use **Corner Test Cut** for a single 3 mm square on the current sheet.
Control Center looks for a spare corner and shows the spot before cutting.
Use the **Test-Cut Matrix** to try several settings on one blank sheet.

## Material presets

The app includes the Silhouette starting point and a set of Studio factory
presets for cardstock, chipboard, paper, and pens. To add the full factory
list from your own Studio installation, go to **Materials → Import CSV**
and choose its `condition.csv`. See [where to find it](docs/SETUP.md#exporting-files-and-reading-material-settings).

Edit the active cut settings in **Send**. In **Materials**, **Save as New**
keeps a copy of those settings under a new name. Presets stay
available after you quit. [About preset files](profiles/README.md)

**Track Enhancing** runs the rollers before cutting. For standard Letter
Print & Cut on a Cameo 4, Control Center waits for the rollers to finish,
then uses the same registration scan and cut area as it does with Track
Enhancing off. The guided test skips the roller cycle.

During a full desktop cut, **Pause Cut** holds the next command batch
after the current cutter operation. Leave the mat loaded and choose
**Resume Cut** to continue in the same session. **Cancel Cut** ends the
job after the current operation. The cutter can finish motion it has
already accepted.

**Tool Holder 1** and **Tool Holder 2** keep separate settings while the app
is open. Save named presets to keep those settings for later. **Standard**
uses the selected holder. To use both, assign selected shapes under
**Design → Object → Tool Holder** or choose holder settings for groups in
**Send → Advanced Options → Send by**.

### Studio cutting settings on the Cameo 4

| Setting | Where to find it |
|---|---|
| Force, Speed, Passes, Acceleration, Blade Depth, Tool Holder | **Send → Cut Settings** |
| Track Enhancing and Line Segment Overcut | **Send → Cut Settings**. The extension lengths are under **Send → Advanced Options → Blade details**. |
| Cut Order Sorting | **Send → Advanced Options → Cut paths** |
| Seam Overcut and Weed Settings | **Send → Advanced Options** |
| Cutting mat, Feed, Return to Origin | **Send → Advanced Options → Media handling** |
| Installed tools | **Send → Check Status** |

For a manual blade, set the depth on the tool. If **Blade Depth** is available,
choose **Manual** to leave the depth as set.
Rotary tool actions still need Silhouette Studio.

## Calibration

If the cut is off by the same amount everywhere, measure it with the sketch
pen and save the correction under **Send → Advanced Options → Calibration**. If the error grows
across the page, check the print size or printer alignment first.
[How to measure an offset](docs/SETUP.md#calibrating-the-machine)

## Checks

```sh
./run-tests.sh
silhouette-control-center cut --dry-run
```

The tests use simulated cutter connections. A dry run checks the planned
paths. To check physical alignment, run a pen test on a printed sheet.
[Hardware findings](docs/FINDINGS.md) · [Inkscape compatibility](docs/INKSCAPE_COMPATIBILITY.md)

## Independent application handoff

Use Proxy Press to make the PDF and cut paths, Canon Suite to print, and
Control Center to cut. Print at 100% and keep the front PDF page and cut
file from the same export. Printer corrections belong in the print layout.
The cutter’s X/Y offset is for a small, consistent shift seen on a pen test.

### Rechecking compatibility

From the Printing workspace, generate fresh SVG fixtures using the actual
Proxy Press functions and pass them to the cutter's offline test:

```sh
node proxypress/tools/export-interop.js > /tmp/proxypress-geometry.json
(cd cut-proxies && PROXYPRESS_INTEROP_JSON=/tmp/proxypress-geometry.json python3 -m unittest tests.test_proxypress_exports -v)
```

The normal cutter suite uses a dated local fixture so it remains independent.
The optional Proxy Press browser probe also exercises sheet selection and
real duplex PDF export:

```sh
cd proxypress
node tools/console-check.js --probe tools/interop-probe.js --artifacts /tmp/proxypress-browser.json
```

Pass `/tmp/proxypress-browser.json` through `PROXYPRESS_INTEROP_JSON` to the
same cutter test to raster-check front-page registration marks as well.
Browser artifacts contain each exported PDF as base64 and the sheet SVGs.
A successful geometry-only check does not establish browser or PDF behavior.

To exercise the versioned ZIP handoff, including the actual PDF checker and
cut-stream verification, generate fresh bundles using ProxyPress's test
exporter:

```sh
node proxypress/tools/cameo-bundle-check.js /tmp/proxypress-bundles
(cd cut-proxies && PROXYPRESS_BUNDLE_DIR=/tmp/proxypress-bundles python3 -m unittest tests.test_proxypress_bundle -v)
```

For registered cuts, an SVG with declared page dimensions must match the
selected sheet size and orientation within 0.1 mm. A one-card final sheet
still uses the full paper coordinates for registration. Choose the matching
sheet or re-export; fitting artwork inside another sheet is insufficient.
DXF drawing bounds and SVGs without declared dimensions do not establish a
paper size, so select their sheet and registration settings explicitly.

Canon Suite's shared spool builder also disables CUPS automatic page fitting.
Older versions could retain the original PDF yet shrink its raster by about
5%. For matching printed sheets and cuts, run the current Canon handoff
checks and measure a physical print before cutting stock.

SVG imports preserve both rounded-corner radii and the viewport's aspect
ratio/alignment. Flatten nested SVG viewports and slice-clipped drawings
before import; these are refused rather than cut with different visible
geometry. Verification measures every cutting pass, including repeated
passes through a partial-sheet SVG.
