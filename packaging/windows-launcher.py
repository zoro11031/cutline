"""Start Cutline on Windows with the runtime it was installed with.

The installer puts Cutline in the app folder and one runtime beside it:
qt/ (the official embedded Python with PySide6) or gtk/ (MSYS2's Python with
GTK 4). This script is run by that runtime's Python and points Cutline at
the DLLs, typelibs and schemas that came with it."""
import os
from pathlib import Path
import sys

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root))
python_home = Path(sys.executable).resolve().parent
gtk = root / "gtk"
uses_gtk = gtk in python_home.parents or python_home == gtk / "bin"

if uses_gtk:
    binary_dir = gtk / "bin"
    os.environ["GI_TYPELIB_PATH"] = str(gtk / "lib/girepository-1.0")
    os.environ["XDG_DATA_DIRS"] = str(gtk / "share")
    os.environ["GSETTINGS_SCHEMA_DIR"] = str(gtk / "share/glib-2.0/schemas")
    loaders = gtk / "lib/gdk-pixbuf-2.0/2.10.0/loaders.cache"
    if loaders.is_file():
        os.environ["GDK_PIXBUF_MODULE_FILE"] = str(loaders)
    frontend = "gtk"
else:
    binary_dir = root / "qt" / "native" / "bin"
    frontend = "qt"
os.add_dll_directory(str(binary_dir))
os.environ["PATH"] = str(binary_dir) + os.pathsep + os.environ.get("PATH", "")
os.environ["CUTLINE_LIBUSB"] = str(binary_dir / "libusb-1.0.dll")
os.environ.setdefault("CUTLINE_CONFIG", str(
    Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Cutline"))

from cutline.cli import main  # noqa: E402

raise SystemExit(main(sys.argv[1:] or ["gui", "--frontend", frontend]))
