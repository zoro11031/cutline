"""Launch the Windows portable package using its bundled DLLs and tools."""
import os
from pathlib import Path
import sys

root = Path(__file__).resolve().parent
binary_dir = root / "native" / "bin"
dll_search = os.add_dll_directory(str(binary_dir))
os.environ["PATH"] = str(binary_dir) + os.pathsep + os.environ.get("PATH", "")
os.environ["CUT_PROXIES_LIBUSB"] = str(binary_dir / "libusb-1.0.dll")
os.environ.setdefault("CUT_PROXIES_CONFIG", str(
    Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Silhouette Control Center"))

from cut_proxies.cli import main

raise SystemExit(main(sys.argv[1:] or ["gui", "--frontend", "qt"]))
