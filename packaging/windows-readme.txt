Cutline for 64-bit Windows

Open Cutline from the Start menu. Python and Qt are included.
Settings and presets are stored in %LOCALAPPDATA%\Cutline.
You can remove the app from Windows Settings under Installed apps.

For command-line diagnostics, open a terminal in this folder and run:
  .\python.exe launcher.py status
  .\python.exe launcher.py cut --dry-run

Bluetooth availability depends on the cutter, the Windows Bluetooth adapter,
and the active Windows session. USB uses libusb and requires a compatible
Windows driver for the cutter. Setup does not install or replace a driver.
A VM needs to see the physical cutter or Bluetooth adapter first.
