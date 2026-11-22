"""One-time setup. Everything it produces is gitignored.

  python scripts/setup.py balatrobot     # fetch balatrobot v1.5.2 (MIT) into vendor/balatrobot
  python scripts/setup.py host           # macOS: build engine/host/bhost-game against the game's LuaJIT
  python scripts/setup.py extract-src    # Windows/Linux: unpack the game's Lua source from Balatro.exe
  python scripts/setup.py luajit         # Windows/Linux: build the game's LuaJIT version (2.1.1700008891)
  python scripts/setup.py real-game      # assemble runtime/mods for driving the real game (balatrobot + helper mod)
  python scripts/setup.py profile --slot 2   # optional: use one of your own save profiles in the engine sandbox
  python scripts/setup.py all            # balatrobot + host (macOS), or extract-src + luajit (Windows/Linux)

jackdaw (the fast simulator) installs with `uv sync --extra sim` (pinned release wheels, hashes in uv.lock).

Paths can be overridden with BALATRO_GAME_DIR (the Steam install folder), BALATRO_SRC (the game's Lua
source folder) and BALATRO_SAVE_DIR (the game's save folder).
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import zipfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from balatro_bot.paths import game_dir, game_src, save_dir  # noqa: E402

BALATROBOT_REPO = "https://github.com/coder/balatrobot.git"
BALATROBOT_TAG = "v1.5.2"

# balatrobot bug fixes shared with the engine (engine/balatrobot_fixes.json)
FIXES_FILE = ROOT / "engine/balatrobot_fixes.json"


def run(cmd: list[str], **kw) -> None:
    print("+", " ".join(str(c) for c in cmd))
    subprocess.run(cmd, check=True, **kw)


def cmd_balatrobot(_args) -> None:
    dst = ROOT / "vendor/balatrobot"
    if (dst / "balatrobot.lua").exists():
        print(f"balatrobot already at {dst}")
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    run(["git", "clone", "--depth", "1", "--branch", BALATROBOT_TAG, BALATROBOT_REPO, str(dst)])


def cmd_host(_args) -> None:
    if platform.system() != "Darwin":
        print("The host is only needed on macOS. Elsewhere, the engine uses `luajit` on PATH "
              "(see README: Lua runtime) with BALATRO_SRC from `extract-src`.")
        return
    fw = game_dir() / "Balatro.app/Contents/Frameworks/Lua.framework/Versions/A"
    lib, headers = fw / "Lua", fw / "Headers"
    if not lib.exists():
        sys.exit(f"Balatro's LuaJIT not found at {lib}; set BALATRO_GAME_DIR")
    out = ROOT / "engine/host/bhost-game"
    tmp = out.with_suffix(".tmp")
    run(["cc", "-O2", f"-I{headers}", str(ROOT / "engine/host/bhost.c"), str(lib),
         f"-Wl,-rpath,{fw.parent.parent.parent}", "-o", str(tmp)])
    tmp.replace(out)  # atomic: never rewrite a binary that running engines have mapped
    print(f"built {out}")


def cmd_extract_src(args) -> None:
    exe = Path(args.exe) if args.exe else game_dir() / "Balatro.exe"
    dst = ROOT / "runtime/game_src"
    if not exe.exists():
        sys.exit(f"{exe} not found (pass --exe). On macOS the source is already a folder: {game_src()}")
    with zipfile.ZipFile(exe) as z:  # the game's .love archive is appended to the executable
        names = [n for n in z.namelist() if n.endswith(".lua") or n.startswith("localization/")
                 or n == "version.jkr"]
        z.extractall(dst, names)
    print(f"extracted {len(names)} files to {dst}\nset BALATRO_SRC={dst}")


def cmd_real_game(args) -> None:
    bb = ROOT / "vendor/balatrobot"
    if not (bb / "balatrobot.lua").exists():
        cmd_balatrobot(args)
    mods = ROOT / "runtime/mods"
    dst = mods / "balatrobot"
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    for f in ("balatrobot.json", "balatrobot.lua"):
        shutil.copy2(bb / f, dst / f)
    shutil.copytree(bb / "src/lua", dst / "src/lua")
    for fix in json.loads(FIXES_FILE.read_text()):
        target = dst / fix["file"]
        text = target.read_text()
        if text.count(fix["old"]) != fix["count"]:
            sys.exit(f"balatrobot changed upstream: fix for {fix['file']} no longer applies; review it")
        target.write_text(text.replace(fix["old"], fix["new"]))
    helper = mods / "bb_profile"
    helper.mkdir(parents=True, exist_ok=True)
    for f in (ROOT / "mods/bb_profile").iterdir():
        shutil.copy2(f, helper / f.name)
    shutil.copy2(ROOT / "engine/lua/determinism.lua", helper / "determinism.lua")
    smods = Path(args.mods_dir).expanduser() if args.mods_dir else save_dir() / "Mods/smods"
    link = mods / "smods"
    if not smods.exists():
        print(f"warning: Steamodded not found at {smods}; install it (balatrobot needs it) or pass --mods-dir")
    else:
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(smods)
    print(f"real-game mods ready in {mods} (your own Mods folder is untouched)")



LUAJIT_REPO = "https://github.com/LuaJIT/LuaJIT.git"
LUAJIT_COMMIT = "43d0a19158ceabaa51b0462c1ebc97612b420a2e"  # = LuaJIT 2.1.1700008891, the game's version


def cmd_luajit(_args) -> None:
    """Build the game's LuaJIT version from source (for Windows/Linux, or without the macOS host).
    Newer LuaJIT releases produce different random numbers, so a system luajit is not enough."""
    src = ROOT / "vendor/luajit"
    if not src.exists():
        run(["git", "clone", LUAJIT_REPO, str(src)])
    run(["git", "-C", str(src), "checkout", LUAJIT_COMMIT])
    env = dict(os.environ)
    if platform.system() == "Darwin":
        env.setdefault("MACOSX_DEPLOYMENT_TARGET", "13.0")
    run(["make", "-C", str(src), "-j4"], env=env)
    run(["make", "-C", str(src), "install", f"PREFIX={ROOT / 'runtime/luajit'}"], env=env)
    built = sorted((ROOT / "runtime/luajit/bin").glob("luajit-2.1*"))
    if built:
        link = ROOT / "runtime/luajit/bin/luajit"
        if link.exists() or link.is_symlink():
            link.unlink()
        link.symlink_to(built[-1].name)
    print(f"built LuaJIT at {ROOT / 'runtime/luajit/bin/luajit'}")


def _inflate(path: Path) -> str:
    data = path.read_bytes()
    if data.startswith(b"return"):
        return data.decode()
    return zlib.decompress(data, -15).decode()


def cmd_profile(args) -> None:
    src = save_dir() / str(args.slot)
    dst = ROOT / "runtime/engine_save" / str(args.slot)
    dst.mkdir(parents=True, exist_ok=True)
    for name in ("profile.jkr", "meta.jkr"):
        (dst / name).write_text(_inflate(src / name))
    print(f"copied profile {args.slot} into {dst}; run the engine with "
          f"ENGINE_UNLOCK_ALL=0 ENGINE_SAVE={dst.parent} BOT_PROFILE={args.slot}")


def cmd_all(args) -> None:
    cmd_balatrobot(args)
    if platform.system() == "Darwin":
        cmd_host(args)
    else:
        cmd_extract_src(args)
        cmd_luajit(args)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("balatrobot").set_defaults(fn=cmd_balatrobot)
    sub.add_parser("host").set_defaults(fn=cmd_host)
    sub.add_parser("luajit").set_defaults(fn=cmd_luajit)
    p = sub.add_parser("extract-src")
    p.add_argument("--exe")
    p.set_defaults(fn=cmd_extract_src)
    p = sub.add_parser("real-game")
    p.add_argument("--mods-dir", help="your Steamodded folder (default: <save dir>/Mods/smods)")
    p.set_defaults(fn=cmd_real_game)
    p = sub.add_parser("profile")
    p.add_argument("--slot", type=int, default=2)
    p.set_defaults(fn=cmd_profile)
    p = sub.add_parser("all")
    p.add_argument("--exe")
    p.set_defaults(fn=cmd_all)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
