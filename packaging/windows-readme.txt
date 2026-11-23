Cutline for 64-bit Windows

Open Cutline from the Start menu. Setup installs the Qt window, or the GTK
window (experimental) if you chose it; each comes with its own Python.
Settings and presets are stored in %LOCALAPPDATA%\Cutline.
You can remove the app from Windows Settings under Installed apps.

For command-line diagnostics, open a terminal in this folder and run:
  .\qt\python.exe launcher.py status         (Qt)
  .\gtk\bin\python.exe launcher.py status    (GTK)
  add "cut --dry-run" instead of "status" to check a job without the cutter

Bluetooth availability depends on the cutter, the Windows Bluetooth adapter,
and the active Windows session. USB uses libusb and requires a compatible
Windows driver for the cutter. Setup does not install or replace a driver.
A VM needs to see the physical cutter or Bluetooth adapter first.
