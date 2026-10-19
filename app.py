"""
app.py

Entry point for LifeSearch.

Responsible only for bootstrapping the Qt application and showing the
main window. All UI structure lives in ui/, all business logic will
live in core/. Keep this file thin.
"""

import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("MindVault")
    app.setOrganizationName("MindVault")

    base_dirs = [
        Path(getattr(sys, "_MEIPASS", "")),
        Path(sys.executable).parent / "_internal",
        Path(sys.executable).parent,
        Path(__file__).resolve().parent,
    ]

    icon_to_use = None
    for b in base_dirs:
        if not str(b) or not b.exists():
            continue
        ico_path = b / "assets" / "icon.ico"
        png_path = b / "assets" / "logo.png"
        if ico_path.exists():
            icon_to_use = QIcon(str(ico_path))
            break
        elif png_path.exists():
            icon_to_use = QIcon(str(png_path))
            break

    if icon_to_use:
        app.setWindowIcon(icon_to_use)

    window = MainWindow()
    if icon_to_use:
        window.setWindowIcon(icon_to_use)
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
