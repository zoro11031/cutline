# How Silhouette Studio was studied

Silhouette publishes none of its cutters' protocol, file formats or material
data. Everything Cutline knows about them was recovered, and this page covers
how that was done and what was found in Studio itself.

## Rules

- **Nothing is a lookalike.** A command byte, file field or constant enters
  Cutline only with a source: a capture of Studio's traffic, Studio's own
  files, or a published project that recovered it. A value whose meaning is
  unknown is sent as Studio sends it and labelled unknown.
- **Byte claims and hardware claims are different claims.** "The stream
  decodes correctly" is checked by decoding it; "the cutter cuts in the right
  place" only by a cutter cutting. [Status](status.md) keeps them apart.
- **Change one thing at a time.** To learn what a setting does, capture
  Studio before and after changing only that, and repeat the unchanged run as
  a control.
- **Record what failed**, and date every finding with how it was made.
- **Studio's files stay Studio's.** Findings record sizes, hashes and decoded
  values, not copies. The one bundled vendor file, `condition.csv`, is only
  ever replaced whole.

The same discipline applies to Cutline's own checks. A verifier that always
passes prints the same table as one that works. A guard has to be tested in
both directions. Two computations that agree may just share an assumption.

## Sources

| Source | What it supplied |
|---|---|
| [inkscape-silhouette](https://github.com/fablabnbg/inkscape-silhouette) | The Graphtec command set, device IDs, tool codes, the LE transport, mark conventions. |
| Silhouette Card Maker ([guide](https://alan-cha.github.io/silhouette-card-maker/tutorial/guide/)) | The layout algorithm and table, DXF corners, `.studio3` templates, tuning advice. |
| [studio3svg](https://github.com/zaydmulani09/studio3svg) | An independent `.studio3` format description. |
| [Sillinux](https://github.com/racxusdev/Sillinux) | A Linux RFCOMM transport on channel 1. |
| [Cuthulhu](https://github.com/vcolombo/cuthulhu) | Cameo 5 Alpha hardware notes. |
| Silhouette Studio 5.0.556.1 | Its files, its symbols, and captures of its traffic. |

## The Windows guest

Studio runs only on Windows and macOS, so it was installed in a Windows VM on
the Linux host using WinBoat. Its files were read straight off the guest's
disk image. Studio exposes nothing to UI automation, so it was driven with
screenshots and clicks through an RDP session, by a script that read commands
from a shared folder.

Traffic was captured on the host, beneath the VM. With the cutter passed
through to the guest, the kernel's `usbmon` records everything Studio sends
over USB (2026-11-17). For Bluetooth, the host's adapter was passed to the
guest instead, so the same capture holds every packet Windows exchanged with
the radio, decoded down to RFCOMM (2026-11-21). These captures can include
other devices on the bus and pairing keys, so they are filtered at once and
kept private.

## What Studio does

All of this concerns Studio 5.0.556.1 (`Silhouette Studio.exe` SHA-256
`4871EDB7…14A47B`). Other versions may differ.

**It is a Xojo application that ships its full symbol table**, so methods have
readable names and can be disassembled by name. A name alone is never treated
as evidence of behaviour.

**Registering and cutting** (USB capture, Cameo 4 firmware 1.20, 2026-11-17).
Studio sends the mark settings and the search, waits about 14 s for "found",
and only then sends the cut area, tool settings and cuts. On that sheet it
failed twice before succeeding, with an identical command each time. This is
the sequence Cutline now uses; see [the cut stream](how-it-works.md#the-cut-stream).

**Bluetooth** (binaries, then a live capture, 2026-11-21). A helper program
loads Silhouette's `BluetoothIO.dll`, which uses only Winsock and Windows'
classic Bluetooth API, with no LE anywhere. It searches with a 12.8 s classic
inquiry, repeated about every 22 s, and connects RFCOMM, letting Windows find
channel 1 through SDP. After connecting it only sends queries, then polls
status and tools about once a second on the same connection, never sending
initialisation.

**Files.** Material settings live in a plain CSV, alongside a second, encoded
materials resource that Studio was seen opening. Its free edition refuses SVG.
A Cutline DXF imported about 2.84% too large (2026-11-08). See
[Files](how-it-works.md#files) and [Materials](how-it-works.md#materials-and-presets).

**PixScan** (read from the code, 2026-11-20; nothing run). The four mats'
bar patterns were decoded from Studio's specification code and cross-checked
against the copy in its DLL. Which mat each one belongs to, and where its
frame sits relative to the cutter, are still unknown.

## Compared with inkscape-silhouette

Compared at commit [`e782d59`](https://github.com/fablabnbg/inkscape-silhouette/tree/e782d599b799861b8e2aaacc49bb2ccc7ec78633),
Cutline covers the same ground for USB, Bluetooth, device queries,
registration, tools and media, path ordering and SVG import. It adds the
captured Cameo 4 registration sequence, a held Bluetooth Classic link, and
`.studio3` and PixScan support. It still lacks the extension's support for
older cutters (Craft Robo, Silhouette SD), dashed-line perforation, clipping at
the boundary (Cutline refuses jobs outside the sheet instead), and some path
pre-processing. To compare a job, run the extension with `--dry_run` and diff
the command transcripts one variable at a time.
