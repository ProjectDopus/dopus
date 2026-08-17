#!/usr/bin/env python3
"""Intake gate for contributed bundles. Runs in CI on every pull request that
touches bundles/; runs locally the same way.

Usage:  python3 scripts/validate_bundle.py bundles/*.json
        python3 scripts/validate_bundle.py --selftest

A contribution IS a pull request adding one file to bundles/. This script is
the machine half of the review: it decides whether the file is the shape a
bundle must have and, above all, whether any string in it looks like text
that was never supposed to leave the contributor's machine. Human review sits
on top of that gate, not instead of it.

Checks, in order -- any failure rejects the file:
  1. parses as JSON, top-level object, schema version we know
  2. required sections present, participant id well-formed
  3. filename matches dopus-bundle-<participant>-<YYYYMMDD>.json and the
     participant inside matches the filename (no smuggling under a false name)
  4. provenance: phrases_sha256 is a dictionary version this repo has shipped
     (KNOWN_DICTIONARIES below -- add a hash when the dictionary changes on
     main), and the exported date is a real date, not in the future
  5. THE TEXT GUARD: export.py's guard, deny-by-default, over the whole file.
     Every string must be a dictionary phrase, a hex id, or a short structural
     token. This is the IRB line: only counts travel.
  6. no timestamps finer than a month anywhere (the bundle coarsens them at
     export; a fine timestamp means it was hand-edited or from an old exporter)
Exit 0 only if every file passes.
"""

import datetime
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paths as P
from export import SCHEMA_VERSION, dictionary_vocab, guard, sha256_file

# Every phrases.json sha256 that has ever been on main. A bundle built with a
# dictionary we never shipped is not comparable and could carry anything.
# The current dictionary is always accepted (computed live), so this list is
# for older-but-legitimate versions.
KNOWN_DICTIONARIES = {
    "0b5a82392e259d8ce22bc8dd9201c2834a80ab2f83cb0cb2a49a51e3264daab4",  # 2026-08-13
}

REQUIRED = ("dopus_bundle", "participant", "exported", "provenance",
            "analysis", "denominators", "tally", "followthrough")
NAME_RX = re.compile(r"^dopus-bundle-([A-Za-z0-9_-]{1,32})-(\d{8})\.json$")
FINE_TS = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")


def problems(path, vocab, current_dict):
    out = []
    name = os.path.basename(path)
    m = NAME_RX.match(name)
    if not m:
        return ["filename must be dopus-bundle-<participant>-<YYYYMMDD>.json"]
    try:
        b = json.load(open(path, encoding="utf-8"))
    except Exception as exc:
        return ["not valid JSON: %s" % exc]
    if not isinstance(b, dict):
        return ["top level must be an object"]
    if b.get("dopus_bundle") != SCHEMA_VERSION:
        out.append("schema version %r, expected %r" % (b.get("dopus_bundle"), SCHEMA_VERSION))
    for k in REQUIRED:
        if k not in b:
            out.append("missing section: %s" % k)
    if out:
        return out
    if b["participant"] != m.group(1):
        out.append("participant %r does not match filename %r" % (b["participant"], m.group(1)))
    try:
        d = datetime.date.fromisoformat(b["exported"])
        if d > datetime.date.today():
            out.append("exported date is in the future")
        if d.strftime("%Y%m%d") != m.group(2):
            out.append("exported date %s does not match filename" % b["exported"])
    except Exception:
        out.append("exported is not a YYYY-MM-DD date")
    sha = b["provenance"].get("phrases_sha256", "")
    if sha not in KNOWN_DICTIONARIES and sha != current_dict:
        out.append("phrases_sha256 %s… is not a dictionary this repo has shipped" % sha[:12])
    bad = guard(b, vocab)
    for jp, s in bad[:25]:
        out.append("TEXT GUARD %s: %r" % (jp, s))
    if len(bad) > 25:
        out.append("…and %d more text-guard hits" % (len(bad) - 25))
    raw = open(path, encoding="utf-8").read()
    if FINE_TS.search(raw):
        out.append("contains a sub-day timestamp; bundles coarsen to the month")
    return out


def main():
    vocab = dictionary_vocab(P.PHRASES)
    current = sha256_file(P.PHRASES)

    if "--selftest" in sys.argv:
        import tempfile
        good = {"dopus_bundle": SCHEMA_VERSION, "participant": "t1",
                "exported": "2026-08-01",
                "provenance": {"phrases_sha256": current, "git_commit": "abc", "working_tree_dirty": False},
                "analysis": {"corpus": {"messages": 5}}, "denominators": {"totals": {"user": 1}},
                "tally": [{"side": "user", "category": "profanity", "phrase": "fucking", "n": 2}],
                "followthrough": {"labels": {"honored": 1}}}
        evil = dict(good, tally=[{"phrase": "so anyway here is what the user actually typed"}],
                    exported="2026-08-01")
        with tempfile.TemporaryDirectory() as td:
            gp = os.path.join(td, "dopus-bundle-t1-20260801.json")
            ep = os.path.join(td, "dopus-bundle-t1-20260801.json")
            json.dump(good, open(gp, "w"))
            ok_good = not problems(gp, vocab, current)
            json.dump(evil, open(ep, "w"))
            ok_evil = any("TEXT GUARD" in p for p in problems(ep, vocab, current))
        print("clean bundle accepted:  %s" % ok_good)
        print("planted prose rejected: %s" % ok_evil)
        sys.exit(0 if ok_good and ok_evil else 1)

    files = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not files:
        print("no bundle files given")
        sys.exit(0)
    failed = 0
    for f in files:
        ps = problems(f, vocab, current)
        print("%s  %s" % ("REJECT" if ps else "ok    ", f))
        for p in ps:
            print("        - %s" % p)
        failed += bool(ps)
    print("\n%d file(s), %d rejected" % (len(files), failed))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
