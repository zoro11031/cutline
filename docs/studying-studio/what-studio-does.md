# What Studio does

Findings about Silhouette Studio itself. All of them concern **Studio
5.0.556.1** (product version V5.0.556.001; `Silhouette Studio.exe` SHA-256
`4871EDB71FB28B02593850006992277E69E058D0A385B0AC6402F63B9514A47B`). Other
versions may differ, and each finding should be re-checked against the
version in hand.

## The executable

Studio is a Xojo application. Its executable ships with its full COFF symbol
table (2,416,951 symbols), so methods have readable names such as
`Condition_root.generate_materials_list` or
`bluetooth_device_manager.launch_ss_bluetooth`, and any of them can be
disassembled by name (`llvm-objdump --disassemble-symbols`). Names show where
to look; they do not show what runs or in which order, so a name alone is
never treated as a finding about behaviour.

Studio's free Basic edition refuses SVG files (a Designer Edition feature).

## Files and resources

- **Material settings** are in `Resources/Resources/SPEC_S/condition.csv`, a
  plain CSV. See [Materials and presets](../how-it-works/materials.md).
- **A second material resource**, `SPEC_CUTTER/materials` (3,333,472 bytes),
  has no recognisable header (not ZIP, gzip, zstd or SQLite) and close to
  random entropy. In a fresh Windows profile, a file-access trace showed
  Studio opening it, along with `user_materials/A.materials4`, which Studio
  creates (88 bytes) on first start. Its encoding is unknown.
- **`.studio3` documents**: 39 samples ship with Studio. See
  [Files](../how-it-works/files.md#studios-studio3-documents).

## Importing a DXF

On 2026-11-08 an eight-card Letter DXF written by Cutline opened in Studio
about 2.84% larger than its true size, on a square mat instead of a Letter
page. Rounded corners came through. Import preferences were not isolated, so
the cause is not known. See [Files](../how-it-works/files.md#dxf-export).

## Registering and cutting

Captured on the USB bus between Studio and a Cameo 4 (firmware 1.20) on
2026-11-17, while Studio registered and cut a four-card Letter landscape
sheet. The full mark-search sequence is on [The cut stream](../how-it-works/cut-stream.md#registration).
In order, Studio:

1. sent the mark settings and the search (`TB123,3918,5188,118,118`), then
   `TB99`;
2. got `"    0"` (found) about 14 s later, or `"    1"` (not found);
3. only then sent the clip bounds (0, 0 to 3918, 5188), the tool settings,
   and the cut movements, ending with `TB0`, `L0` and the park.

The first two attempts on that sheet failed and the third succeeded, with the
identical search each time. The capture also contains eight pen-up moves
before the park, some left of the four intended cards; Studio had several
documents open, and USB carries no document names, so the capture cannot
show that only the intended paths ran.

The device-side traffic is kept as
[`cameo4-studio-registration-2026-11-17.tsv`](../cameo4-studio-registration-2026-11-17.tsv).

## Bluetooth

Established on 2026-11-21, from the binaries first and then by watching
Studio search for and connect to a Cameo 4.

**The chain.** Studio's `bluetooth_device_manager` starts a separate helper,
`Resources/Resources/SPEC_CUTTER/bluetooth/ss_bluetooth.exe`, and talks to it
through shared-memory sockets. The helper loads `BluetoothIO.dll` from its
`ss_bluetooth Libs` folder. The DLL is a Xojo plugin built by Silhouette (its
classes include `ASPEX::Watcher`). Its only imports are Winsock (`socket`,
`connect`, `send`, `recv`, `select`, `shutdown`, `closesocket`) and
`bthprops.cpl`'s `BluetoothFindFirstDevice`, `BluetoothFindNextDevice` and
`BluetoothFindDeviceClose`. Nothing in the chain uses Bluetooth LE. The
other DLL in that folder, `aspx_util_lib.dll`, imports only kernel32,
advapi32 and OLE.

**The search.** Disassembly of the call to `BluetoothFindFirstDevice` gives
its parameters:

| Field | Value | Meaning |
|---|---|---|
| `fReturnAuthenticated` | 1 | include paired devices |
| `fReturnRemembered` | 0 | |
| `fReturnUnknown` | 1 | include newly found devices |
| `fReturnConnected` | 1 | include connected devices |
| `fIssueInquiry` | 1 | run a fresh radio inquiry |
| `cTimeoutMultiplier` | 10 | 10 × 1.28 s = 12.8 s |

The live capture matched: inquiries of exactly 12.8 s, repeated about every
22 s while the Send panel was open.

**The connection.** The connect path calls `socket(AF_BTH, SOCK_STREAM,
BTPROTO_RFCOMM)` and fills a `SOCKADDR_BTH` with the device address, the
service GUID `00000003-0000-1000-8000-00805F9B34FB` and port
`BT_PORT_ANY`. That GUID is the RFCOMM protocol UUID, not a service class,
and "any port" makes Windows look the channel up through SDP. The Cameo 4
offers one RFCOMM record, on channel 1.

**What happened live.** Studio listed the cutter as "CAMEO 4 (…)" with the
last four bytes of its classic address, the classic identity rather than the
LE one. On selecting it, Windows paired with Secure Simple Pairing (Just
Works), opened RFCOMM, and Studio showed "Firmware 1.20 · No media loaded".
The cutter took 18.35 s to accept the RFCOMM session. Then Studio sent:

| Studio sent | Cutter replied |
|---|---|
| `ESC 0x11` | `CAMEO 4 V1.20` |
| `ESC ENQ` ×3 | `2` (unloaded) |
| `FG` | `CAMEO 4 V1.20` |
| `TI` | empty |
| `TO` | `0` |
| `FG`, `TI`, `ESC ENQ` ×3 | as above |
| `TB71`, `FA`, `TC` | `    0,    0` each |
| `TI`, `ESC ENQ` | as above |
| `ESC NAK` | ` 255, 4` |
| `ESC SYN` (`0x16`) | `   0` |

After that it polled `ESC ENQ`, `ESC NAK` and `ESC SYN` about once a second
on the same connection. `ESC NAK` alternated between ` 255, 4` and ` 1, 4`,
and Studio showed holder 1 as "No tool detected". No initialisation
(`ESC EOT`) was sent, and no cut command.

## PixScan

Read statically on 2026-11-20; nothing was run. `PhotoScan_spec.!spec` builds
four mat specifications (keys 1, 2, 4 and 3, in code order). Each is a set of
`ScanLine_spec(type, name, marks, start, end)`: a 1.5 mm black bar
(`MARKER_THICKNESS_MM`), 1.5 mm outside a frame, broken by gaps. `marks` are
the switch points along the line, starting at 0 and ending at the line's
length. Lines `photo_left`, `photo_top` and `photo_right` (type 1) bound the
photo area; `scan_*` lines (types 2 and 3) run further along the mat.
`aspx_util_lib.dll` contains a C++ port (`ASPEX::Pixscan::*`) that Studio
calls from `PhotoScanThread.RunV2`; the Xojo path, `RunOriginal`, is the one
read.

The decode was checked three ways. Every line's last mark equals its length.
Where two lines lie on one physical edge (eleven pairs across the four mats,
some read in opposite directions), their gaps fall at the same mat
coordinates. And the DLL carries the same seven line names and all 132
distinct numbers.

The bundled calibration sheets (`PixScan Calibration` for Letter, A4, Ledger
and A3) are a single grid of 1 mm dots on a 5 mm pitch from (5.5, 5.5) mm.
Camera profiles (`SPEC_SS/cal/*.calibration`, 25 phones) are big-endian
`calibrate01` records: an id, a name, the image size, the photo's EXIF data,
then a lattice of dot records of three doubles each.

Not established: which physical mat each key is (Studio's telemetry names
`pixscan_cameo`, `pixscan_curio_large`, `pixscan_curio_small` and
`pixscan_curio_portrait`, but nothing read ties a name to a key), and where
each frame sits relative to the cutter's origin, so a scan gives true size
but not position.
