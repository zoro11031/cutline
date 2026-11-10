# Studio reverse engineering execution record

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
