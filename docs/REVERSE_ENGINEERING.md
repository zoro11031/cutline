# Studio reverse engineering execution record

References to the desktop app's WinBoat bridge describe an earlier workflow.
For local file export, see the current setup guide.

## Starting point (2026-11-09)

Execution of `REVERSE_ENGINEERING_BRIEF.md` starts with an evidence audit.
The offline baseline is 317 passing tests, including the assertion audit.
The generated Letter eight-card job passes verification and all eight
liveness probes. These establish software behavior, not successful cutting.
`cut-proxies status` reports no USB cutter and no pyusb. WinBoat is running.

Several brief statements are hypotheses rather than established facts:

| Target | Already present in this repository | Experiment still needed |
|---|---|---|
| A: AutoBlade | `Tool.depth` emits `TF{depth},{holder}`; `_setup_commands` includes it for holder 1 on modern devices. | Capture Studio at several depths with every other setting fixed. Determine whether it delegates adjustment to firmware or emits additional commands. No tapping sequence is established. |
| B: registration | Three/four-mark scan commands and a success reply (`    0` + ETX). | Preserve reads as well as writes. Establish whether coordinates are returned at all and whether correction happens in firmware or Studio before attempting affine reconstruction. |
| C: overcut | Recovered `FE`, `FF`, and `FC` commands already exist. | Toggle Studio overcut alone on a square and compare both settings and path commands. Do not equate these commands with Studio's UI feature without evidence. |
| D: tool 2 | Holder selection `J` and holder-qualified settings already exist. | Capture a compatible tool in each holder; establish tool-specific setup and paths. Holder selection alone does not prove Kraft or Rotary support. |
| E: native format | DXF import has an unresolved size discrepancy (FINDINGS 26–27). | Inspect actual `.studio3` samples before claiming ZIP, SQLite, obfuscation, or writing a generator. |

The brief names Studio 4.5.815; the prior live experiment recorded
5.0.556.1. Record the executable version and hash on every capture instead
of treating either version as current. The active share is discovered with
`winboat.guest_path`, not hardcoded from the brief.

## First experiment: guest inventory

`tools/survey-studio-re.ps1` records the installed executable version and
SHA-256, checks known capture-tool locations, inventories USB devices, and
searches the Studio installation, Public Documents, and the user's Documents
for `.studio`/`.studio3` files. For each sample it records length, SHA-256,
and its first 64 bytes. It does not copy proprietary files into this repo.
No UI interaction, installation, print, or cut is performed.

Stage this script and a wrapper passing `-OutputDirectory` through the
existing `winboat.stage_dir`/`guest_path` functions, and launch the wrapper
with `winboat.run_script`. This preserves the existing session guard and
600-second RDP bound. The launcher returning is not proof of execution:
inspect `inventory.json`, require `complete: true`, inspect `errors`, and
check the start/finish timestamps against the launch. Keep reports under
ignored `guest-staging/`; filenames may reveal personal document names.

An empty sample list only describes the scanned directories. Missing tools
at the known locations do not prove that no capture tools are installed.

The first run completed without reported errors on 2026-11-09. It found
Studio 5.0.556.1, 39 bundled native samples with the common outer header
`silhouette05;`, no capture tools at the five checked locations, and no
Cameo in the guest USB inventory. See FINDINGS section 29 for evidence
identifiers. Target E can proceed with these local samples; targets A–D
still need a connected device and a working capture facility.

## Differential capture sequence

Once a cutter and capture facility are available, start with target A:

1. Record Studio executable version/hash, cutter model and firmware, tool,
   material, page/mat, registration state, and every cut setting.
2. Capture a baseline, repeat it unchanged as a control, then change only
   depth (1, 2, 7, 10), retaining a repeat at the baseline depth afterward.
3. Preserve original capture files and SHA-256 hashes. Export endpoint,
   direction, timestamp and payload bytes without dropping read responses
   or interpreting USB packet boundaries as command boundaries.
4. Compare complete streams, including initialization and replies. Separate
   repeat-to-repeat variation from changes correlated with depth. Compare
   with the verified `cut-proxies` job stream as a separate source.
5. Cite capture and byte offsets for any new command. Add implementation,
   verification and liveness probes only after the semantics are established.

Follow with registration on/off and three/four-mark comparisons on compatible
devices, then overcut off/on, then tool selection. Never infer a Cameo 5
Alpha result from a Cameo 4. A disconnected Studio UI is useful for native
file experiments but is not a substitute for device traffic.

For native files, obtain controlled saves (blank, one known square, translated
square, one changed cut setting) in the exact installed Studio version. Test
container signatures and repeated-save stability before assigning meaning to
binary differences. Keep vendor/user samples local and use synthetic fixtures
for any eventual parser tests.

### Cameo 4 Bluetooth comparison

The Linux host discovers the V1.05 Cameo 4. Even with USB disconnected and
a fresh BlueZ discovery entry, an LE connection reported zero resolved
GATT services. The documented vendor GATT service is therefore an
unconfirmed match for this unit. See [upstream BLE transport](https://github.com/fablabnbg/inkscape-silhouette/blob/main/silhouette/BLETransport.py).

For a differential test, expose a dedicated USB Bluetooth adapter to the
Windows guest and verify that Windows sees its radio. Keep the cutter's USB
cable disconnected. Start an adapter capture before opening Studio's Send
panel, then record discovery, connection, and its idle status query. Record
whether Studio uses ATT/GATT or Classic RFCOMM, the service and
characteristics or channel, and the firmware response. Use those
observations to choose the transport for this firmware before testing
registration or blade motion.

### Inkscape Silhouette import

The upstream extension reads the SVG document directly. Its traversal skips
labelled Print, CuttingMat and Regmarks groups. The registration renderer
writes corner IDs `regmark-tl`, `regmark-tr`, `regmark-bl` and optionally
`regmark-br`. Its default square is 5 mm, its L centreline arms reach 20 mm,
and its L stroke is 0.3 mm. The importer recognizes that geometry after
parent transforms and uses it for the existing scan window and clearance
checks. The generated text note is advisory. A fixture with a stale note
proves the import is based on the shapes. [Traversal and command export](https://github.com/fablabnbg/inkscape-silhouette/blob/main/sendto_silhouette.py) · [Registration renderer](https://github.com/fablabnbg/inkscape-silhouette/blob/main/render_silhouette_regmarks.py)

For a protocol comparison, use a single-page SVG and an offline upstream
`--dry_run` command transcript. Compare model setup, tool settings, mark
search and decoded coordinates one variable at a time. Then repeat the
same design in Studio and compare observable status and command traffic.
The SVG import is software-verified. The optical scan, AutoBlade behavior,
and final alignment on a printed sheet still require a hardware proof.
