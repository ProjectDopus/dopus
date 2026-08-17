#!/usr/bin/env python3
"""
Build the contribution bundle: everything a participant sends, in one vetted file.

Usage:  python3 scripts/export.py [--id HANDLE]
        python3 scripts/export.py --selftest      prove the text guard works

THE CONTRACT. Raw transcripts may never be part of a shared dataset (IRB), and
an excerpt is a raw transcript. Several files under results/ *look* like
results but carry verbatim message text (all-matches.jsonl, followthrough.jsonl,
sweep samples, coding pages). A contributor should never have to know which is
which -- so they never hand-pick files. This script assembles the counts tier
only, then REFUSES TO WRITE if anything text-shaped is found in the assembled
bundle. The safe path is the only path.

WHAT GOES IN                                WHAT NEVER LEAVES THE MACHINE
  aggregate rates + CIs (analysis.json)       archive/ and history.sqlite
  phrase tally (counts of dictionary          all-matches.jsonl (4,000-char
    phrases only, validated as such)            excerpts)
  per-machine denominators (hashed ids;       followthrough.jsonl (quoted turns)
    the WHERE clause is dropped -- it          sweep/counts/apology outputs
    embeds a repo path)                        coding pages (real text)
  follow-through label mix + parameters
  calibration verdicts (fingerprints +
    yes/no/cant_tell only)
  provenance: dictionary sha256, git
    commit, dirty flag, schema version

THE TEXT GUARD. Every string in the bundle must be one of: a dictionary phrase
(checked against phrases.json), a known constant, or a short identifier-shaped
token. Anything with a newline, an @, a URL, or a path-shaped prefix
(-Users-/-home-//Users//home/) aborts the export and prints every offender.
Guard failures mean a pipeline file changed shape -- report it, don't force it.
"""

import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paths as P
import scan as S

SCHEMA_VERSION = 1

# Exact strings the bundle is allowed to carry despite tripping the shape
# checks: "<synthetic>" is the model key Claude Code writes for synthetic
# messages (angle brackets look like markup to the guard).
ALLOWED_CONSTANTS = {"<synthetic>"}

# Pure hex (fingerprints, sha256) is data, not prose, at any length.
HEXY = re.compile(r"^[0-9a-f]{8,64}$")

PATHY = re.compile(r"-Users-|-home-|/Users/|/home/|https?://|@[A-Za-z]|<[a-z-]+>")
TOKEN = re.compile(r"^[A-Za-z0-9 ._%+:()<>\[\]|/-]{1,48}$")


def dictionary_vocab(pf):
    """Every string phrases.json could legitimately put in a tally."""
    raw = json.load(open(pf, encoding="utf-8"))
    vocab = set()

    def walk(o):
        if isinstance(o, str):
            vocab.add(S.normalize(o))
        elif isinstance(o, list):
            for x in o:
                walk(x)
        elif isinstance(o, dict):
            for k, v in o.items():
                vocab.add(S.normalize(k))
                walk(v)
    walk(raw)
    return vocab


def guard(bundle, vocab):
    """Return list of (json-path, offending string). Empty means clean."""
    bad = []

    def visit(o, path):
        if isinstance(o, dict):
            for k, v in o.items():
                check(str(k), path + "." + str(k)[:24])
                visit(v, path + "." + str(k)[:24])
        elif isinstance(o, list):
            for i, v in enumerate(o):
                visit(v, "%s[%d]" % (path, i))
        elif isinstance(o, str):
            check(o, path)

    def check(s, path):
        if s in ALLOWED_CONSTANTS or HEXY.match(s):
            return
        if "\n" in s or PATHY.search(s):
            bad.append((path, s[:80]))
            return
        if S.normalize(s) in vocab:
            return
        if len(s) > 48 or not TOKEN.match(s) or len(s.split()) > 5:
            bad.append((path, s[:80]))

    visit(bundle, "$")
    return bad


def tally_counts():
    """(side, category, phrase) -> n, phrases validated later by the guard."""
    out = Counter()
    for line in open(P.ROWS, encoding="utf-8"):
        r = json.loads(line)
        out[(r["side"], r["category"], r["phrase"])] += 1
    return [{"side": s, "category": c, "phrase": p, "n": n}
            for (s, c, p), n in sorted(out.items(), key=lambda kv: -kv[1])]


def git_stamp():
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=P.ROOT,
                                capture_output=True, text=True, timeout=10).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=P.ROOT,
                                    capture_output=True, text=True, timeout=10).stdout.strip())
        return commit[:12] or None, dirty
    except Exception:
        return None, None


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build(participant):
    analysis = json.load(open(os.path.join(P.RESULTS, "analysis.json")))
    # Nothing finer than month granularity leaves the machine (IRB.md makes
    # this claim; this line is what makes it true). corpus.first/last are
    # second-precision in analysis.json.
    for k in ("first", "last"):
        if analysis.get("corpus", {}).get(k):
            analysis["corpus"][k] = analysis["corpus"][k][:7]
    denom = json.load(open(P.DENOM))
    denom.pop("where_clause", None)   # embeds a repo path; documented in METHOD.md

    ft_path = os.path.join(P.RESULTS, "followthrough.json")
    ft = json.load(open(ft_path)) if os.path.exists(ft_path) else None
    if ft:
        for k in ("note", "rules"):          # prose constants; counts travel, prose doesn't
            ft.pop(k, None)

    labels = []
    cdir = os.path.join(P.RESULTS, "coding")
    if os.path.isdir(cdir):
        for n in sorted(os.listdir(cdir)):
            if re.match(r"followthrough-labels-.*\.json$", n):
                d = json.load(open(os.path.join(cdir, n)))
                labels.append({"file": n,
                               "records": [{"msg_fingerprint": r["msg_fingerprint"],
                                            "detector": r.get("detector"),
                                            "did_it": r.get("did_it")}
                                           for r in d.get("records", [])]})

    commit, dirty = git_stamp()
    return {
        "dopus_bundle": SCHEMA_VERSION,
        "participant": participant,
        "exported": datetime.date.today().isoformat(),
        "provenance": {
            "phrases_sha256": sha256_file(P.PHRASES),
            "git_commit": commit,
            "working_tree_dirty": dirty,
        },
        "analysis": analysis,
        "denominators": denom,
        "tally": tally_counts(),
        "followthrough": ft,
        "calibration_labels": labels,
    }


def main():
    vocab = dictionary_vocab(P.PHRASES)

    if "--selftest" in sys.argv:
        clean = {"tally": [{"side": "user", "category": "profanity",
                            "phrase": "fucking", "n": 89}]}
        planted = {"tally": [{"phrase": "well this is what antigravity looks like "
                                        "but maybe we proceed and finish"}],
                   "path": "-Users-zach-GitHub-BearCode",
                   "multiline": "line one\nline two"}
        ok_clean = not guard(clean, vocab)
        caught = guard(planted, vocab)
        print("clean bundle passes:   %s" % ok_clean)
        print("planted text caught:   %d of 3 offenders" % len(caught))
        for p, s in caught:
            print("   %-28s %r" % (p, s[:60]))
        sys.exit(0 if (ok_clean and len(caught) == 3) else 1)

    participant = (sys.argv[sys.argv.index("--id") + 1]
                   if "--id" in sys.argv else S.machine_id())
    if not re.match(r"^[A-Za-z0-9_-]{1,32}$", participant):
        sys.exit("--id must be 1-32 chars of [A-Za-z0-9_-]")

    bundle = build(participant)
    bad = guard(bundle, vocab)
    if bad:
        print("EXPORT REFUSED -- %d text-shaped value(s) in the bundle:" % len(bad))
        for p, s in bad[:20]:
            print("   %-40s %r" % (p, s))
        print("A pipeline file has changed shape. Fix the source, do not force this.")
        sys.exit(1)

    out = os.path.join(P.RESULTS, "dopus-bundle-%s-%s.json"
                       % (participant, bundle["exported"].replace("-", "")))
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(bundle, fh, indent=1, sort_keys=True)

    kb = os.path.getsize(out) / 1024.0
    n_lab = sum(len(l["records"]) for l in bundle["calibration_labels"])
    print("bundle       %s  (%.0f KB)" % (out, kb))
    print("participant  %s" % participant)
    print("dictionary   sha256 %s%s" % (bundle["provenance"]["phrases_sha256"][:16],
          "  (WORKING TREE DIRTY -- results may not be comparable)"
          if bundle["provenance"]["working_tree_dirty"] else ""))
    print("contents     analysis aggregates · %d tally rows · denominators for %d machine(s)"
          % (len(bundle["tally"]), len(bundle["denominators"].get("per_machine", {}))))
    print("             follow-through mix · %d calibration verdict(s)" % n_lab)
    print("guard        every string checked against the dictionary -- no message text")
    print("\nReview it yourself (it is one readable JSON file), then send it per")
    print("CONTRIBUTING.md. Nothing else from results/ should ever be shared.")


if __name__ == "__main__":
    main()
