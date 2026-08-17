#!/usr/bin/env python3
"""
Stage local Claude Code transcripts into archive/ for a single-machine run.

Usage:  python3 scripts/collect.py [transcript_dir]     (default ~/.claude/projects)

This is the contributor's entry point. The maintainer's sync.py pulls from a
fleet of machines over SSH and needs an untracked host inventory; a contributor
has exactly one machine and no inventory, so this script does the one thing
sync.py would have done locally: copy transcripts into the layout build_db.py
expects --

    archive/<machine_id>/<root_slug>/<project>/<session>.jsonl

Read-only on the source. Copies only *.jsonl, skips macOS AppleDouble sidecars
(._*), and never deletes anything already in archive/.
"""

import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paths as P
import scan as S


def root_slug(path):
    """/Users/x/.claude/projects -> Users-x--claude-projects (matches sync.py)."""
    return re.sub(r"[/.]", "-", os.path.abspath(path).lstrip("/"))


def main():
    src = os.path.abspath(os.path.expanduser(
        sys.argv[1] if len(sys.argv) > 1 else "~/.claude/projects"))
    if not os.path.isdir(src):
        sys.exit("no transcript directory at %s" % src)

    mid = S.machine_id()
    dst_root = os.path.join(P.ARCHIVE, mid, root_slug(src))

    copied = skipped = total = 0
    for dp, _dirs, names in os.walk(src):
        for n in names:
            if not n.endswith(".jsonl") or n.startswith("._"):
                continue
            total += 1
            sp = os.path.join(dp, n)
            rel = os.path.relpath(sp, src)
            tp = os.path.join(dst_root, rel)
            if os.path.exists(tp) and os.path.getsize(tp) == os.path.getsize(sp):
                skipped += 1
                continue
            os.makedirs(os.path.dirname(tp), exist_ok=True)
            shutil.copy2(sp, tp)
            copied += 1

    print("machine id   %s" % mid)
    print("source       %s" % src)
    print("staged       %d files copied, %d already current, %d total"
          % (copied, skipped, total))
    print("next:        python3 scripts/build_db.py && python3 scripts/build_rows.py "
          "&& python3 scripts/verify.py")


if __name__ == "__main__":
    main()
