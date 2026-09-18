# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import sys
from PyInstaller.utils.hooks import collect_submodules


project_root = Path(SPECPATH).resolve()
python_root = Path(sys.base_prefix)
tcl_root = python_root / "tcl"
dll_root = python_root / "DLLs"

gui_analysis = Analysis(
    [str(project_root / "gui.py")],
    pathex=[str(project_root)],
    binaries=[
        (str(dll_root / "_tkinter.pyd"), "."),
        (str(dll_root / "tcl86t.dll"), "."),
        (str(dll_root / "tk86t.dll"), "."),
    ],
    datas=[
        (str(project_root / "answers.json"), "."),
        (str(tcl_root / "tcl8.6"), "_tcl_data"),
        (str(tcl_root / "tk8.6"), "_tk_data"),
    ],
    hiddenimports=[
        "_tkinter",
        "tkinter",
        "tkinter.filedialog",
        "tkinter.messagebox",
        "tkinter.scrolledtext",
        "tkinter.ttk",
    ],
    hookspath=[str(project_root / "pyinstaller_hooks")],
    hooksconfig={},
    runtime_hooks=[str(project_root / "pyi_rth_tkinter_custom.py")],
    excludes=[],
    noarchive=False,
    optimize=0,
)
gui_pyz = PYZ(gui_analysis.pure)
gui_exe = EXE(
    gui_pyz,
    gui_analysis.scripts,
    gui_analysis.binaries,
    gui_analysis.datas,
    [],
    name="UCAS-MOOC-Assistant",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

worker_analysis = Analysis(
    [str(project_root / "worker.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=[(str(project_root / "filler.js"), ".")],
    hiddenimports=collect_submodules("selenium.webdriver"),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "IPython",
        "cv2",
        "jupyter",
        "matplotlib",
        "notebook",
        "numpy",
        "pandas",
        "pytest",
        "scipy",
        "tkinter",
        "torch",
        "torchaudio",
        "torchvision",
    ],
    noarchive=False,
    optimize=0,
)
worker_pyz = PYZ(worker_analysis.pure)
worker_exe = EXE(
    worker_pyz,
    worker_analysis.scripts,
    worker_analysis.binaries,
    worker_analysis.datas,
    [],
    name="UCAS-MOOC-Worker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
