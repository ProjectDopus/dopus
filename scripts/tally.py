#!/usr/bin/env python3
"""
Running tally of every phrase, both sides. Raw counts, no construct filtering.

Usage:  python3 scripts/tally.py [--md]

Deliberately unfiltered: the construct split governs what belongs in a
capitulation headline, but it should never hide a count. If you want to know how
many times you said "fuck", that number lives here regardless of taxonomy.
"""
import json, os, sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paths as P

rows = [json.loads(l) for l in open(P.ROWS, encoding="utf-8")]
den = json.load(open(P.DENOM))

out = []
def w(s=""): out.append(s)

for side, label, total in (("user", "USER", den["totals"]["user"]),
                           ("assistant", "CLAUDE", den["totals"]["assistant"])):
    sub = [r for r in rows if r["side"] == side]
    w("## %s  —  %d hits over %d messages (%.2f%%)"
      % (label, len(sub), total, 100.0 * len({r["msg_fingerprint"] for r in sub}) / total))
    w()
    bycat = defaultdict(Counter)
    for r in sub:
        bycat[r["category"]][r["phrase"]] += 1
    for cat in sorted(bycat, key=lambda c: -sum(bycat[c].values())):
        w("**%s** (%d)" % (cat, sum(bycat[cat].values())))
        w()
        w("| phrase | n |")
        w("|---|---|")
        for ph, n in bycat[cat].most_common():
            w("| `%s` | %d |" % (ph, n))
        w()

w("## By construct (assistant)")
w()
w("| construct | hits | messages | rate |")
w("|---|---|---|---|")
a = [r for r in rows if r["side"] == "assistant"]
c = Counter(r["construct"] for r in a)
for k, n in c.most_common():
    m = len({r["msg_fingerprint"] for r in a if r["construct"] == k})
    w("| %s | %d | %d | %.2f%% |" % (k, n, m, 100.0 * m / den["totals"]["assistant"]))

text = "\n".join(out)
p = os.path.join(P.RESULTS, "TALLY.md")
open(p, "w", encoding="utf-8").write("# Phrase tally\n\n" + text + "\n")
print(text if "--md" in sys.argv else text[:1800])
print("\nwrote %s" % p)
