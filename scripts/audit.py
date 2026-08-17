#!/usr/bin/env python3
"""
Precision audit: enumerate every occurrence of every phrase and sample evenly.

Usage:  python3 scripts/audit.py [--min N] [--max N] [--samples K] [--phrase P]

Adjudicating from the first 2-4 hits encountered is how "i introduced" (71%
genuine) got rejected on the same evidence that correctly rejected "worth
checking" (0% genuine). First-N draws also cluster inside whichever file sorted
first. This walks the whole corpus and samples at even intervals.
"""
import os, sqlite3, re, sys, json
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths as P
import scan as S

W = ("side='assistant' AND has_text=1 AND is_sidechain=0 AND is_compact=0 "
     "AND is_meta=0 AND is_visible_only=0 AND f.project!='-Users-zach-GitHub-Dopus'")

def main():
    a = sys.argv
    lo = int(a[a.index("--min")+1]) if "--min" in a else 0
    hi = int(a[a.index("--max")+1]) if "--max" in a else 10**9
    k  = int(a[a.index("--samples")+1]) if "--samples" in a else 8
    only = a[a.index("--phrase")+1] if "--phrase" in a else None

    pats = S.load_patterns(P.PHRASES)["assistant"]
    aop  = S.load_assistant_openers(P.PHRASES)
    guard = S.load_negation_guard(P.PHRASES)
    rgx = S.load_patterns_regex(P.PHRASES)
    db = sqlite3.connect(P.DB)

    occ = defaultdict(list)
    seen = set()
    for fp, txt in db.execute(f"SELECT DISTINCT m.fingerprint,m.text FROM messages m "
                              f"JOIN files f ON m.file_id=f.file_id WHERE {W}"):
        if fp in seen:
            continue
        seen.add(fp)
        n = S.normalize(txt)
        for ph, cat, pos in S.all_matches(n, pats, aop[0], aop[1], guard, rgx):
            occ[ph].append(n[max(0, pos-75):pos+65])

    rows = sorted(occ.items(), key=lambda x: -len(x[1]))
    for ph, ctxs in rows:
        N = len(ctxs)
        if not (lo <= N <= hi):
            continue
        if only and ph != only:
            continue
        step = max(1, N // k)
        picks = ctxs[::step][:k]
        print("=== %-24s n=%-4d (showing %d evenly spaced) ===" % (ph, N, len(picks)))
        for c in picks:
            print("   ...%s" % c.replace("\n", " ")[:122])
        print()

if __name__ == "__main__":
    main()
