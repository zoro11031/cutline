# Windows packaging

Cutline Setup installs for the current user. It bundles Python, Qt Widgets,
and cutter support so setup works offline. The release workflow tests fresh
installs and upgrades from v0.3.0 and v0.3.1 in Windows. Python, wheel, and
MSYS2 artifact names and SHA-256 digests are pinned in
`packaging/windows-dependencies.json`. Update the lock when changing runtime
dependencies and rerun the Windows install checks.

On November 26, 2026, an NSIS rebuild of the v0.3.2 payload with the Qt pruning
rules reduced setup from 90,127,519 to 80,967,189 bytes. The comparison used
the released installer and its extracted payload. Both builds opened a Qt
application in Wine.

A WiX MSI would support standard Windows Installer deployment and upgrades
between MSI versions. Switching Cutline would also require migrating the
registered NSIS install, preserving its custom directory and settings, and
updating the existing EXE-based in-app updater. Benchmark MSI and EXE with
the same payload before attributing size or speed differences to the
installer format. [WiX Toolset](https://www.firegiant.com/wixtoolset/) ·
[WiX MajorUpgrade](https://docs.firegiant.com/wix/schema/wxs/majorupgrade/)
