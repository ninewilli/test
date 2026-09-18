"""Keep tkinter discoverable when PyInstaller's Tcl-only probe fails."""

from pathlib import Path
import sys


def pre_find_module_path(hook_api):
    standard_library = Path(sys.base_prefix) / "Lib"
    hook_api.search_dirs = [str(standard_library)]
