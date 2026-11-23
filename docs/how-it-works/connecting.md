# Connecting to a cutter

Cutline reaches a cutter three ways: USB, Bluetooth Classic and Bluetooth LE.
Everything here was observed on one Cameo 4 (USB ID `0b4d:1137`), first on
firmware 1.05 and then 1.20 after Studio updated it.

## USB

The Cameo 4 enumerates as a bidirectional printer-class interface with bulk
OUT `0x01` and bulk IN `0x82`. Cutline talks to it through PyUSB, or through
the kernel's `usblp` driver (`/dev/usb/lpN`) when PyUSB is missing. Both
answered the status query with `unloaded` and the firmware query with
`CAMEO 4 V1.05` (2026-11-17), then `CAMEO 4 V1.20` after the update.

## Bluetooth Classic: how Studio does it

Studio 5.0.556.1 talks to a Cameo 4 over **Bluetooth Classic RFCOMM**, the
Serial Port profile, not over Bluetooth LE. Established on 2026-11-21, first
by reading Studio's binaries and then by watching it work
([how](../studying-studio/what-studio-does.md#bluetooth)):

- Studio starts a helper program, `ss_bluetooth.exe`, which loads
  Silhouette's `BluetoothIO.dll`. The DLL uses only Winsock and Windows'
  classic Bluetooth device API; there is no LE code in the chain.
- **Search** is a 12.8-second classic inquiry that also returns paired and
  connected devices.
- **Connect** opens an RFCOMM socket and lets Windows find the channel through
  SDP. The cutter advertises one record, "Serial Port Service - Channel 1".
- After connecting, Studio sends only queries (firmware, status, tools,
  calibration reads), then polls status and tools about once a second **on
  the same connection** for as long as it stays connected. It sends no
  initialisation over Classic.

## Bluetooth Classic in Cutline

Cutline does the same on Linux with the kernel's RFCOMM sockets: connect to
channel 1, confirm the model with the firmware query (`FG`), then send. No
extra driver is needed. Choose **Bluetooth Classic** as the connection, or
let the cutter list pick it.

The Cameo 4's Bluetooth module is slow and moody, and the transport is built
around what it actually does (all measured 2026-11-21):

- **The first connection can be slow.** The cutter took 18.35 s to accept
  Studio's RFCOMM session. Cutline waits up to 40 s.
- **A connection opened a few seconds after the previous one can stall.** The
  link and the RFCOMM channel open, but the cutter never grants the flow
  control credits that let data through, so not even the firmware query goes
  out. The cutter frees the stuck link about 20 s later; until then a new
  connection is refused as busy. If the first reply has not come within 5 s
  (a healthy one takes about 0.3 s), Cutline drops the classic link and opens
  a new session.
- **A stall can leave an orphaned session behind.** After one stall the Linux
  kernel kept an RFCOMM session that no process owned, and every new
  connection timed out with nothing sent, for minutes, until the classic link
  was dropped. A connection that times out makes Cutline drop the link once
  (through BlueZ, `bluetoothctl disconnect`) and try again.
- **A cutter that does not answer is reported as not detected.** With the
  cutter switched off, every connect failed with "Host is down" after
  5.13–5.15 s, the Classic page timeout (measured 2026-11-23). The radio cannot
  tell why nothing answered: the cutter may be off, out of range, or have its
  Bluetooth off. A link that is still closing gives the same error but at
  once, and is retried. After two failures that took the full page time,
  Cutline says "No cutter detected", in 13.5 s, where the retries for a
  closing link used to take 78 s.
- **Busy is never reset.** A connection refused as busy straight away means
  another connection holds the cutter, perhaps a Cutline window part-way
  through a cut. Cutline waits for it for about half a minute, then says the
  cutter is in use instead of dropping someone else's link.

**One link is held open between actions** (`cutters.open_link`), the way
Studio keeps its connection. An idle link still answered a status query in
about 20 ms after 60 s. Opening a session can take seconds, or much longer
when the cutter stalls, so the windows keep one Bluetooth Classic link open
across status checks, tool reads, test cuts and sends:

- Before each use, one status query confirms the link still answers; if not,
  it is reopened.
- A failed action drops the link only if the link has stopped answering. The
  windows report ordinary refusals ("no media is loaded") as errors too, and
  those leave a healthy link alone.
- Choosing another cutter closes the old link. A link unused for 5 minutes is
  closed, so Studio or a terminal command can reach the cutter, and so is a
  link still open when Cutline exits.
- One action uses the link at a time.

With the link held, status checks in both windows take 0.10–0.16 s. Every
check used to open a new connection, taking anywhere from 3 to 55 s.

**The cutter list offers Classic.** Every paired cutter with the Serial Port
service appears, connected or not, because Cutline connects only when it needs
to. Being listed therefore does not mean the cutter is on: as in Studio,
choosing a cutter reads its status, and one that does not answer is shown
as not detected. **Refresh Cutters** scans again. When the same cutter is
also visible over LE, only the Classic entry is listed.

**With no cutter available, the windows offer a scan.** As in Studio's Send
panel, the Cutter status row shows **Scan for Cutters** while the list is
empty. It looks for USB and Bluetooth cutters, says "Scanning for cutters…"
while it runs, and selects the cutter it finds, or says what to try: turn the
cutter on, then connect USB or turn on its Bluetooth and scan again.

## Pairing a new cutter

On Linux, Cutline pairs a cutter itself, the way Windows did when Studio
selected one. All of this was measured on 2026-11-22 on a Cameo 4 (firmware
1.20) with BlueZ 5.88, starting each time from a cutter removed from BlueZ.

**Finding it.** An unpaired cutter is heard only by a Classic inquiry, which
Cutline runs as Studio does: 13 s (Studio's is 12.8 s), Classic only. The
inquiry runs when you press **Scan for Cutters** or **Refresh Cutters**, and
once on its own when the 30-second refresh hears a cutter over LE that it
does not know over Classic. An unpaired Cameo 4 advertises over LE, so
opening **Send** is enough: the window found it and paired it with no click,
in both front ends. The periodic refresh does not otherwise run an inquiry,
because it takes 13 s and can make Bluetooth audio stutter. The inquiry runs
alongside the LE scan, so a search takes about 13 s rather than 21 s.

A cutter found this way is listed like a paired one, and its LE entry gives
way to it. The inquiry did not always hear the cutter: 2 of about 20 searches that
day missed it, and the next one found it. Studio repeats its search about
every 22 s for the same reason.

**Pairing it.** Choosing the cutter pairs it and then reads its status. The
window says "Pairing with CAMEO 4-…" meanwhile. Cutline asks BlueZ to pair
(`org.bluez.Device1.Pair`, through `busctl`), and the cutter pairs by Secure
Simple Pairing, Just Works: no PIN and no prompt on either side. Pairing took
about 5 s; pairing and the first status read together took 2 to 8 s, and
once 56 s, when the cutter stalled the new session as described above.

- **Pairing must come first.** Connecting RFCOMM to an unpaired cutter also
  works, because the kernel runs Just Works itself, but BlueZ keeps no key
  ("Paired: yes, Bonded: no"). The cutter then drops out of the paired list
  as soon as it disconnects. So Cutline checks the bond before every new
  connection and pairs first if there is none. The command line does the
  same: `cutline status --transport bluetooth --bluetooth-id ADDRESS` pairs
  an unpaired cutter before reading it.
- **BlueZ forgets an unpaired device** about 30 s after a search ends. A
  cutter chosen later than that is searched for again, then paired (18 s in
  all, measured).
- **A session opened just after pairing can be dropped.** Once, the first
  query failed with "Software caused connection abort": BlueZ drops the
  connection it paired over a few seconds after pairing, and the new session
  went with it. A session dropped before its first reply is now reopened
  once. This has not been seen again since, so the retry itself is tested
  only offline.
- A cutter BlueZ cannot find after one more search, or that does not answer
  the pairing, is reported as not detected.

On Windows and macOS, Cutline still lists only paired cutters: pair the
cutter in the system's Bluetooth settings first.

## Bluetooth LE

Cutline also has an LE transport (`ble.py`) for the vendor GATT service that
inkscape-silhouette uses: service `e2088282-4fde-42f9-bb22-6ec3c7ed8f91`, with
write, read and control characteristics. Replies arrive by indication.
inkscape-silhouette reports plotting over it with a Cameo 5 Alpha on macOS.

On this Cameo 4 under Linux it has never worked. The cutter advertises and the
LE link comes up. BlueZ discovers and caches the vendor service (handles
0x15–0x1d) but never publishes it to applications, and fresh LE connections
are dropped during service discovery. Windows does expose the same service.
Since Studio does not use LE for a Cameo 4, this was not pursued further.
Cutters seen only over LE are still listed.

## Status queries and their replies

| Query | Reply on a Cameo 4 (firmware 1.20) |
|---|---|
| `FG` | `CAMEO 4 V1.20` (padded with spaces) |
| `ESC ENQ` | `0` ready, `1` moving, `2` unloaded |
| `ESC NAK` | tools in holders 1 and 2, e.g. ` 2, 0` |
| `TB71`, `FA`, `TC` | `    0,    0` |
| `TI` | empty |
| `TO` | `0` |
| `ESC 0x11` | the firmware string again (sent by Studio over Bluetooth) |
| `ESC 0x16` | `   0` (sent by Studio over Bluetooth; meaning unknown) |
