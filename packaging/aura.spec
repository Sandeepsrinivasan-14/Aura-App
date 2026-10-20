# PyInstaller spec: builds two single-file executables.
#   pyinstaller packaging/aura.spec --noconfirm
# Output: dist/Aura(.exe)      desktop app (no console window)
#         dist/aura-cli(.exe)  command-line tool
# Paths are relative to this spec file, so the build works on any machine.
import os

from PyInstaller.utils.hooks import collect_submodules

SRC = os.path.normpath(os.path.join(SPECPATH, "..", "src"))  # SPECPATH = folder containing this spec
hidden = collect_submodules("aura") + ["watchdog.observers", "watchdog.observers.polling"]

gui = Analysis([os.path.join(SPECPATH, "aura_gui.py")], pathex=[SRC], hiddenimports=hidden,
               excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.Qt3DCore", "PySide6.QtQuick"])
gui_pyz = PYZ(gui.pure)
EXE(gui_pyz, gui.scripts, gui.binaries, gui.datas, name="Aura", console=False, upx=False)

cli = Analysis([os.path.join(SPECPATH, "aura_cli.py")], pathex=[SRC], hiddenimports=hidden,
               excludes=["tkinter", "PySide6"])
cli_pyz = PYZ(cli.pure)
EXE(cli_pyz, cli.scripts, cli.binaries, cli.datas, name="aura-cli", console=True, upx=False)
