# Set up Silhouette Control Center

## Install

From the repository folder, try the app first.

```sh
./bin/silhouette-control-center gui
```

Install PySide6 for Qt, or GTK 4, libadwaita, PyGObject, and the Python Cairo
binding for GTK. Use `gui --frontend qt` or `gui --frontend gtk` to pick one.
Filled path editing uses GEOS 3.10 or later. Editable text uses Pango and Cairo.
PDF checks also use Pillow and Poppler. For a packaged install, see
[the RPM, Arch, and AppImage recipes](../packaging/README.md).

To add a launcher from your checkout:

```sh
mkdir -p ~/.local/bin
ln -sf "$PWD/bin/silhouette-control-center" ~/.local/bin/silhouette-control-center
install -Dm644 share/cut-proxies.desktop \
  ~/.local/share/applications/org.printing.SilhouetteControlCenter.desktop
install -Dm644 share/icons/cut-proxies.svg \
  ~/.local/share/icons/hicolor/scalable/apps/org.printing.SilhouetteControlCenter.svg
```

## Driving the cutter directly

Install PyUSB from your package manager. Then plug in the cutter and use
**Send → Check Status** or `silhouette-control-center status`.

### USB permissions

For a manual install, add the following lines to
`/etc/udev/rules.d/99-silhouette.rules`. The second line covers the Cameo 5 Alpha.

```udev
SUBSYSTEM=="usb", ATTR{idVendor}=="0b4d", MODE="0664", TAG+="uaccess"
SUBSYSTEM=="usb", ATTR{idVendor}=="3844", MODE="0664", TAG+="uaccess"
```

Run `sudo udevadm control --reload-rules` and `sudo udevadm trigger`, then
unplug and reconnect the cutter. If the status says the interface is busy,
make sure another app is out of the way. If the kernel's printer driver is
holding it, try `sudo modprobe -r usblp` and reconnect the cutter.

Use PyUSB for print-and-cut. The fallback printer connection can write a job,
but it cannot reliably read the result of a registration scan.

## Bluetooth

Turn on Bluetooth on the cutter and make sure BlueZ is running on Linux.
Install Bleak for **Bluetooth LE**. For **Bluetooth Classic (RFCOMM)**, pair
the cutter in your system settings first.

**Send** looks for USB and Bluetooth cutters automatically. Use **Refresh Cutters**
and choose your device under **Connected cutter**. For a manual Bluetooth
connection, open **Send → Advanced Options → Connection**, choose the method,
then select the detected device or enter its Bluetooth identifier.

```sh
silhouette-control-center status --transport ble
silhouette-control-center status --transport bluetooth --bluetooth-id AA:BB:CC:DD:EE:FF
```

Classic defaults to channel 1. Use `--bluetooth-channel` to try another.
**Bluetooth LE** usually works by picking a device from the scan.
See [Bluetooth testing notes](INKSCAPE_COMPATIBILITY.md) for model coverage.

## Printing the sheet

Print at **100%**. Keep the cut file and PDF from the same export. For
ProxyPress, open its ZIP in **Sheet** and pick the sheet you printed. Use
**Close File** to clear the print preview or release the matched layout.
For a generated layout, use **Layout → Advanced Options → Export** to save
the cut files and proof.

ProxyPress's **Silhouette Card Maker** export can also be cut against the stock
Studio template named on its PDF. In Control Center, its ZIP keeps the exact
page and registration dimensions, including A4's small difference from the
nominal paper size. Load the paper at the top-left of the mat as shown in
**Sheet**. When using the stock borderless template in Studio, follow SCM's own
mat placement instructions.

Load the print face-up and square to the top-left grid of the mat. Choose
that mat in **Send → Advanced Options → Media handling**. Load it into the cutter and check
that the cutter says it’s ready.

## First cut on a new template

1. Open the layout and check that the outlines match the print.
2. Choose **Test Registration** to scan the printed marks.
3. Use **Silhouette Sketch Pen** on a plain-paper copy to check alignment.
4. Set up your blade, choose a material, and try it on a scrap.
5. Review **Send** before cutting the good sheet.

If a scan fails, check **Sheet** for a PDF problem or copy the cutter’s scan
details. Print the marks dark and keep them clear of glare and nearby artwork.

## Cut paths and media handling

Under **Send → Advanced Options → Cut paths**, you can cut holes first, shorten travel between
shapes, or reduce roller movement. **Seam Overcut** overlaps the ends of a
closed line. **Line Segment Overcut** helps cuts meet at sharp corners.

Under **Send → Advanced Options → Media handling**, select the mat and choose where the cutter
stops. **Return to Origin** parks it at the start. **Feed** moves it past the
last cut.

For imported artwork or a design, **Send → Advanced Options → Weed Settings** can add a border
or lines through open gaps. Look for blue dashed lines in the preview.

## Finding your blade settings

Choose **Send → Dial In a Material** and load a sheet of the same material.
The first spot is at the bottom-left. Leave the mat loaded and use
**Repeat One Pass** as needed. The preview counts confirmed passes by spot,
and **Use Tested Settings** applies the current spot’s pass count.

If you want to reload the sheet later, turn on **Test Options → Use
Registration Marks** and save the marked test sheet before printing it at
actual size. Every pass scans the marks. Reload the same sheet to resume,
including after reopening the app. Choose **New Test Sheet** for fresh stock.

Use **Corner Test Cut** for a 3 mm square on the current sheet. Use
**Test-Cut Matrix** to try several combinations on one blank sheet.
To run a matrix from the terminal:

```sh
silhouette-control-center testcut --force 25,29,33 --passes 1,2,3 --out ~/Documents
```

## Calibrating the machine

1. Print an alignment sheet and trace the cut lines with **Silhouette Sketch Pen**.
2. Measure the move needed to bring the pen line onto the printed edge.
3. Enter that move under **Send → Advanced Options → Calibration**. Right and down are positive.

For example, if the pen lands 0.2 mm to the right, enter **X −0.2 mm**.
Use `silhouette-control-center calibrate --x -0.2 --y 0` at the terminal.
If the error changes across the sheet, check the print size first.

## Exporting files and reading material settings

Use **Layout → Advanced Options → Export** for an SVG, DXF, and proof. Use **Sheet → Save Paired
Cut File** to get the original cut file from a ProxyPress ZIP.

In **Send**, leave **Cutter model** on **Auto Detect** or choose a model for
offline planning. If multiple cutters appear, select one under
**Connected cutter**. Use **Check Status** to see whether the cutter is ready.

Factory materials load from an accessible Studio installation or a local
import. In **Materials**, choose **Import CSV**
for a standalone `condition.csv` or **Import Folder** for the Studio
installation or the folder containing that CSV. A validated local copy stays
available when the original file moves. **Restore Default Materials** backs
up edited defaults and keeps personal presets. You can also inspect the CSV
with `silhouette-control-center materials --file PATH`.
[Saving and sharing presets](../profiles/README.md)

## Environment variables

| Variable | What it changes |
|---|---|
| `CUT_PROXIES_CONFIG` | Settings folder. Default is `~/.config/cut-proxies`. |
| `CUT_PROXIES_TEMPLATES` | Export folder. Default is `~/Documents/cut-proxies`. |
| `PRINTING_SUITE_HOME` | Folder containing the other Printing Suite projects. |

## Inkscape Silhouette Print & Cut

1. In Inkscape, put printed artwork in **Print** and cut lines in **Cut**.
   Convert text you want to cut to paths. Unlink external clones and flatten clips.
2. Run **Extensions → Render → Silhouette Regmarks**. Choose three marks for
   Cameo 4. Save the SVG and print the selected page at 100%.
3. Open the SVG in **Layout** and choose the page if prompted. Review the
   marks and outlines, then test registration in **Send**.

```sh
silhouette-control-center import design.svg --verbose
silhouette-control-center cut --from design.svg --sheet Letter --landscape --dry-run
```

For a multipage SVG, add `--svg-page 2` to choose page 2. Split paths at page
edges in Inkscape before importing. For a manual scan, position the head
over the first mark and switch off **Search automatically**.
[Compatibility details](INKSCAPE_COMPATIBILITY.md)

## Create a cut template in the desktop app

Choose **Design**. Draw on the page or open an existing document. Use
**Page Setup** for paper size, mat, and registration marks. **Save** creates
an editable `.scc-template` project. Choose **Send** to review the cut.

## Send by Line, Fill, or Layer

Open artwork and choose **Send → Advanced Options → Send by**. **Standard** uses the current
cut settings. **Line**, **Fill**, and **Layer** let you choose which groups
to cut and set a material, holder, and order for each one. A design with both
Cut and Score lines gives each its own group when you switch to Send. Use
**Object → Tool Holder** to assign selected shapes to a holder before switching.

```sh
silhouette-control-center import design.svg --list-groups line
silhouette-control-center cut --from design.svg --send-by line \
  --group 'blue=2:Silhouette Sketch Pen' --group 'red=Print Paper' --dry-run
```

The cutter scans the marks once for the job. Use separate holders or sends
for a pen and blade.
