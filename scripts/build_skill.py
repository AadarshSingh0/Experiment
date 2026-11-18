#!/usr/bin/env python3
"""Build a self-contained copy of the carryover skill.

    python3 scripts/build_skill.py                 # -> dist/carryover/ and dist/carryover-skill.zip
    python3 scripts/build_skill.py --install       # also copy to ~/.claude/skills/carryover
    python3 scripts/build_skill.py --install DIR   # ...or to DIR/carryover

Upload dist/carryover-skill.zip to claude.ai (Settings -> Capabilities -> Skills),
or use --install for Claude Code.
"""

import argparse
import os
import shutil
import sys
import zipfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL_SRC = os.path.join(REPO, "skill", "carryover")
PKG_SRC = os.path.join(REPO, "src", "carryover")
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc")


def build(dist: str) -> str:
    out = os.path.join(dist, "carryover")
    if os.path.exists(out):
        shutil.rmtree(out)
    shutil.copytree(SKILL_SRC, out, ignore=IGNORE)
    shutil.copytree(PKG_SRC, os.path.join(out, "scripts", "carryover"), ignore=IGNORE)
    shutil.copyfile(os.path.join(REPO, "docs", "BUNDLE_FORMAT.md"),
                    os.path.join(out, "BUNDLE_FORMAT.md"))
    zpath = os.path.join(dist, "carryover-skill.zip")
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(out):
            for name in sorted(files):
                full = os.path.join(root, name)
                zf.write(full, os.path.relpath(full, dist))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dist", default=os.path.join(REPO, "dist"))
    ap.add_argument("--install", nargs="?", const=os.path.expanduser("~/.claude/skills"), metavar="DIR",
                    help="copy the built skill into DIR/carryover (default ~/.claude/skills)")
    args = ap.parse_args()
    built = build(args.dist)
    print(f"built {built} and {os.path.join(args.dist, 'carryover-skill.zip')}")
    if args.install:
        target = os.path.join(args.install, "carryover")
        if os.path.exists(target):
            shutil.rmtree(target)
        shutil.copytree(built, target)
        print(f"installed to {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
