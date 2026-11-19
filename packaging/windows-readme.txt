Silhouette Control Center for 64-bit Windows

Extract the whole ZIP to a local folder. Open “Silhouette Control Center.cmd”
from the extracted folder to launch the app. Python and Qt are included.
Your settings and presets are stored in %LOCALAPPDATA%\Silhouette Control Center.

For command-line diagnostics, open a terminal in the extracted folder and run:
  .\python.exe launcher.py status
  .\python.exe launcher.py cut --dry-run

Bluetooth availability depends on the cutter, the Windows Bluetooth adapter,
and the active Windows session. USB uses libusb and requires a compatible
Windows driver for the cutter. This package does not install or replace a
driver. A VM needs to see the physical cutter or Bluetooth adapter first.
