"""
utils/__init__.py

Common utility helpers for MindVault.
Cross-platform utilities supporting Windows, macOS, and Linux.
"""

import os
import shutil
import string
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import List

APP_NAME = "MindVault"
APP_VERSION = "1.0.0"
APP_TAGLINE = "Search your files by what you remember, completely offline."
APP_AUTHOR = "Jashandeep Singh"
APP_EMAIL = "jashandeep.gr@gmail.com"
APP_WEBSITE = "https://github.com/jashandeep-gr/MindVault"


def get_available_drives() -> List[str]:
    """
    Return a list of accessible root locations / storage drives
    tailored for Windows, macOS, or Linux.
    """
    drives = []

    if sys.platform == "win32":
        for letter in string.ascii_uppercase:
            drive_path = f"{letter}:\\"
            if os.path.exists(drive_path):
                drives.append(drive_path)
    elif sys.platform == "darwin":  # macOS
        drives.append("/")
        volumes_dir = Path("/Volumes")
        if volumes_dir.exists() and volumes_dir.is_dir():
            try:
                for p in volumes_dir.iterdir():
                    if p.is_dir() and not p.name.startswith("."):
                        drives.append(str(p))
            except Exception:
                pass
        home = str(Path.home())
        if home not in drives:
            drives.append(home)
    else:  # Linux and Unix
        drives.append("/")
        for mount_root in ["/media", "/mnt"]:
            m_path = Path(mount_root)
            if m_path.exists() and m_path.is_dir():
                try:
                    for p in m_path.iterdir():
                        if p.is_dir() and not p.name.startswith("."):
                            drives.append(str(p))
                except Exception:
                    pass
        home = str(Path.home())
        if home not in drives:
            drives.append(home)

    return drives or ["/"]


def reveal_in_file_manager(full_path: str) -> None:
    """
    Cross-platform reveal in file manager:
      - Windows: Windows Explorer with file selected
      - macOS: Finder with file selected (-R)
      - Linux: Nautilus / Dolphin / xdg-open
    """
    if not full_path:
        return

    path_obj = Path(full_path)
    norm = str(path_obj.resolve())

    if sys.platform == "win32":
        if os.path.exists(norm):
            subprocess.Popen(["explorer", f"/select,{norm}"])
        elif path_obj.parent.exists():
            subprocess.Popen(["explorer", str(path_obj.parent)])
    elif sys.platform == "darwin":  # macOS
        if os.path.exists(norm):
            subprocess.Popen(["open", "-R", norm])
        elif path_obj.parent.exists():
            subprocess.Popen(["open", str(path_obj.parent)])
    else:  # Linux
        if shutil.which("nautilus") and os.path.exists(norm):
            subprocess.Popen(["nautilus", "--select", norm])
        elif shutil.which("xdg-open"):
            target_dir = norm if os.path.isdir(norm) else str(path_obj.parent)
            subprocess.Popen(["xdg-open", target_dir])


def format_file_size(size_bytes: int) -> str:
    """Format bytes into human-readable string (KB, MB, GB)."""
    if not size_bytes or size_bytes < 0:
        return "0 B"
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def format_timestamp(timestamp: float) -> str:
    """Format epoch timestamp to human-readable date string."""
    if not timestamp:
        return "Unknown date"
    try:
        dt = datetime.fromtimestamp(timestamp)
        return dt.strftime("%b %d, %Y %I:%M %p")
    except Exception:
        return "Unknown date"
