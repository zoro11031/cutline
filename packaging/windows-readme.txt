Cutline for 64-bit Windows

Open Cutline from the Start menu (or Desktop shortcut, if selected during setup).
Python and Qt are included. Settings and presets are stored in %LOCALAPPDATA%\Cutline.
Cutline is licensed under the GNU General Public License v3 (see LICENSE).
You can remove the app from Windows Settings under Installed apps.

For command-line diagnostics, open a terminal in this folder and run:
  .\python.exe launcher.py status
  .\python.exe launcher.py cut --dry-run

Bluetooth availability depends on the cutter, the Windows Bluetooth adapter,
and the active Windows session. USB uses libusb and requires a compatible
Windows driver for the cutter. Setup does not install or replace a driver.
A VM needs to see the physical cutter or Bluetooth adapter first.
