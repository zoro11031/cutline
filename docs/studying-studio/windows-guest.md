# The Windows guest

Silhouette Studio runs on Windows and macOS only. To study it, Studio was
installed in a Windows virtual machine on the Linux host, using
WinBoat, which runs Windows in a `dockur/windows` container under QEMU. This page records how that guest was driven and how its traffic was
captured, including the things that cost time.

## The setup

- The container publishes RDP and WinBoat's guest agent on loopback ports,
  maps the host's home folder into Windows as `\\host.lan\Data`, passes
  `/dev/bus/usb` through, and exposes QEMU's monitor (QMP) on a port.
- WinBoat's agent answers `/health`, `/apps`, `/metrics` and `/version`. It has
  **no endpoint to run a command**: `/launch`, `/run`, `/exec` and others all
  return 404. The only way to run something in the guest is an RDP
  RemoteApp session, which returns nothing. Every guest script therefore
  writes its results into the shared folder.
- The guest's disk can be read from the host without booting Windows, with
  `guestfish --ro` on WinBoat's disk image. That is how Studio's executables
  and resources were copied out for static reading.

## Running things in the guest

Each of these was found by losing a run to it:

- **Windows serves one interactive session at a time.** A second RDP
  connection does not queue and does not fail loudly: it does nothing, or it
  quietly ends the first one mid-job. Check for an existing session before
  connecting, and identify a client by the executable actually running, not
  by a command line that mentions it (a shell wrapper or a `timeout` around
  it is not the client).
- **Authenticate with `/sec:nla /d:.`** Without the `.` domain, login with the
  guest's local account takes the Kerberos path and fails ("Client's
  credentials have been revoked"). Disabling NLA looked like a fix only
  because `-sec-nla` is not a flag `xfreerdp3` accepts: it exits before
  connecting, so there is no error in the log.
- **Launch through `cmd.exe /c powershell -File …`**, not `powershell.exe`
  directly. Windows parses the RemoteApp argument string first, and a bare
  UNC path does not survive it reliably.
- **Never put the password on the command line.** Any local user can read
  `xfreerdp`'s command line in `ps`. Pass it on stdin or through
  `/args-from:fd:N`. A client launched that way has no visible target in its
  command line, so finding live sessions also means checking established
  sockets.
- **The published ports move.** WinBoat chooses host ports when the container
  is created, so recreating it renumbers them. Look them up every time
  (`docker port`), and check the port is actually listening; a published port
  can be dead while the guest boots.
- **Bound every launch.** Each session runs under `timeout`: minutes for a
  script, hours for interactive work. Clean up stale sessions at the *start*
  of a run, not the end, because the end is what gets skipped when something
  goes wrong.

## Driving Studio

Studio is a Xojo application, and Windows UI Automation sees only unnamed
panes (9 on the home screen, 15 after an import), with no named controls. It
cannot be driven through accessibility. What works is screenshots and
clicks: a small PowerShell loop running in the RDP session reads one command
at a time from a file in the shared folder (take a screenshot, click at x, y,
send keys, start Studio), carries it out, and writes the result and the
screenshot back. The screenshot is read on the host, the next click chosen,
and so on.

## Capturing USB traffic

The guest has no capture tools, so capture happens on the host, below the VM.
Everything the guest sends to a passed-through USB device crosses the host's
USB stack, where the kernel's `usbmon` records it in full:

- **Cutter over USB** (2026-11-17): the Cameo 4 was passed to the guest and
  its traffic recorded on the host while Studio registered and cut a sheet.
  That capture is the source of Cutline's registration sequence.
- **Cutter over Bluetooth** (2026-11-21): the host's Bluetooth adapter (an
  Intel AX200, `8087:0029`) was hot-plugged into the running guest over QMP
  (`device_add usb-host,vendorid=0x8087,productid=0x0029`) and removed again
  afterwards with `device_del`. Linux loses its Bluetooth while the adapter
  is in the guest. The capture on its USB bus then holds every Bluetooth HCI
  command, event and data packet Windows exchanged with the radio, which
  Wireshark decodes down to RFCOMM payloads.

Two cautions. `usbmon` records the whole bus, which can include a keyboard:
filter the capture to the device's address straight away and delete the raw
file. And a Bluetooth capture that includes pairing contains the link key, so
it stays private.

On the Linux side, `btmon` records the host's own Bluetooth traffic, which is
how Cutline's connections to the cutter were compared with Studio's.
