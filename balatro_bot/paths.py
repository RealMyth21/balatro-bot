"""Where Balatro lives on this machine. Override with environment variables:

  BALATRO_GAME_DIR  Steam install folder (contains Balatro.app or Balatro.exe)
  BALATRO_SRC       the game's Lua source folder (macOS: inside the .app; elsewhere: scripts/setup.py extract-src)
  BALATRO_SAVE_DIR  the game's save folder (profiles, Mods)
"""

from __future__ import annotations

import os
import platform
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def game_dir() -> Path:
    if os.environ.get("BALATRO_GAME_DIR"):
        return Path(os.environ["BALATRO_GAME_DIR"]).expanduser()
    home = Path.home()
    system = platform.system()
    if system == "Darwin":
        return home / "Library/Application Support/Steam/steamapps/common/Balatro"
    if system == "Windows":
        return Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Steam/steamapps/common/Balatro"
    return home / ".local/share/Steam/steamapps/common/Balatro"


def game_src() -> Path:
    if os.environ.get("BALATRO_SRC"):
        return Path(os.environ["BALATRO_SRC"]).expanduser()
    if platform.system() == "Darwin":
        return game_dir() / "Balatro.app/Contents/Resources/Balatro.love"
    return ROOT / "runtime/game_src"


def save_dir() -> Path:
    if os.environ.get("BALATRO_SAVE_DIR"):
        return Path(os.environ["BALATRO_SAVE_DIR"]).expanduser()
    home = Path.home()
    system = platform.system()
    if system == "Darwin":
        return home / "Library/Application Support/Balatro"
    if system == "Windows":
        return Path(os.environ.get("APPDATA", home / "AppData/Roaming")) / "Balatro"
    # Linux: Proton prefix of the Steam (Windows) build
    return home / ".local/share/Steam/steamapps/compatdata/2379780/pfx/drive_c/users/steamuser/AppData/Roaming/Balatro"
