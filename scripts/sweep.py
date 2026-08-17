#!/usr/bin/env python3
"""
Recall sweep: find praise/concession constructions the phrase list MISSES.

Usage:  python3 scripts/sweep.py [transcript_dir]
Writes: results/sweep-<tag>.json

Twice now a hand-written candidate list has been the wrong tool -- past tense
("you were right") and question-validation ("good question", 47 hits) were both
found only after someone noticed them by eye. This mines the corpus instead:
extract every praise-shaped construction, subtract what phrases.json already
matches, rank the remainder by frequency.

A hit here is a CANDIDATE, not a phrase to add. "worth checking" surfaced 38
times and was correctly rejected on inspection as advisory rather than praise,
so every candidate ships with sample context for that judgement call.
"""

import json
import os
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scan as S

# "exactly" and "perfect" removed: they are intensifiers, not praise --
# "exactly the same thing" generated 292 false candidates on the first pass.
PRAISE = r"(?:good|great|nice|excellent|fair|valid|solid|sharp|smart|wise|clever|strong|keen|astute)"

# Openers must come from a concession vocabulary. Matching ANY leading word
# caught file labels ("swift:", "tsx:", "css:") and commit chatter.
OPENERS = r"(?:good|right|fair|yes|yep|yeah|correct|agreed|true|indeed|noted|understood|exactly|ah|oh|oops|ouch|touche|granted|admittedly|ok|okay)"

PATTERNS = [
    # "good question", "sharp eye", "valid concern"
    # \b prefix required: without it "otherwise the" matched as "wise the".
    ("praise+noun", re.compile(r"\b" + PRAISE + r"\s+([a-z]{3,14})\b")),
    # "good catch to check", "smart to ask", "right to push back"
    ("praise+to+verb", re.compile(r"\b(?:" + PRAISE + r"|right)\s+(?:\w+\s+)?to\s+([a-z]{2,14})\b")),
    # "you were right to call that out", "youre onto something"
    ("you+concede", re.compile(r"\byou(?:re|\s+(?:are|were|had|caught|called|nailed|spotted))"
                               r"\s+([a-z]{2,14})\b")),
    # "i was wrong about", "i should have checked"
    ("i+fault", re.compile(r"\bi\s+(?:was|were|should|shouldnt|didnt|failed|missed)"
                           r"\s+([a-z]{2,14})\b")),
    # opening concession before a dash: "Fair -- the build is broken"
    ("opener", re.compile(r"^\W{0,3}(" + OPENERS + r"(?:\s+[a-z]{2,14}){0,2})\s*[-,:]")),
    # "exactly right", "exactly so" -- intensifier PLUS a concession word
    ("exactly+", re.compile(r"\bexactly\s+(right|correct|so|my|the point|what i)\b")),
]

SENT_SPLIT = re.compile(r"[.!?\n]+")


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else S.DEFAULT_DIR
    known = S.load_patterns(S.PHRASE_FILE)["assistant"]

    counts = Counter()
    kinds = {}
    samples = defaultdict(list)
    seen = set()
    msgs = 0

    files = []
    for dp, _d, names in os.walk(root):
        if os.path.basename(dp) in S.EXCLUDE_PROJECTS:
            continue
        files += [os.path.join(dp, n) for n in names if n.endswith(".jsonl")]

    for path in files:
        try:
            fh = open(path, encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(e, dict) or e.get("type") != "assistant":
                    continue
                if S.is_synthetic(e):
                    continue
                uid = e.get("uuid")
                if uid:
                    if uid in seen:
                        continue
                    seen.add(uid)
                text = S.message_text(e)
                if not text.strip():
                    continue
                msgs += 1
                norm = S.normalize(text)
                flat = re.sub(r"\s+", " ", text)

                for sent in SENT_SPLIT.split(norm):
                    sent = sent.strip(" -*#`>|")
                    if not (4 <= len(sent) <= 300):
                        continue
                    for kind, rx in PATTERNS:
                        for m in rx.finditer(sent):
                            span = m.group(0).strip()
                            # Skip anything the phrase list already catches.
                            if S.find_matches(span, known):
                                continue
                            counts[span] += 1
                            kinds[span] = kind
                            if len(samples[span]) < 2:
                                i = flat.lower().find(span.split()[0])
                                samples[span].append(
                                    flat[max(0, i - 40):i + 90] if i >= 0 else flat[:110])

    tag = S.file_tag(root)
    out = S.outpath("sweep-%s.json" % tag)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({
            "host": S.machine_id(), "root": root, "assistant_messages": msgs,
            "candidates": [
                {"phrase": p, "count": n, "kind": kinds[p], "samples": samples[p]}
                for p, n in counts.most_common(400)
            ],
        }, fh, indent=1)

    print("assistant messages scanned : %d" % msgs)
    print("distinct untracked constructions : %d" % len(counts))
    print()
    print("%6s  %-16s %s" % ("count", "kind", "construction"))
    for p, n in counts.most_common(30):
        if n >= 3:
            print("%6d  %-16s %s" % (n, kinds[p], p))
    print()
    print("wrote %s" % out)


if __name__ == "__main__":
    main()
