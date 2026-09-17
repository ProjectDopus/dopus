#!/usr/bin/env python3
"""The parallel check: run the SAME dictionary and matchers over non-Claude
agents' transcripts (agents.sqlite) and emit counts-only aggregates for the
models page, alongside the Claude models from the main pipeline.

Usage:  python3 scripts/agents.py            # writes results/agents.json
        python3 scripts/agents.py --sweep    # dictionary-coverage sweep (local, prints text)

Nothing here touches history.sqlite, all-matches.jsonl, analysis.json or the
main site. The unit of comparison is the MODEL; the harness a model ran in
is carried as a label and reported, never used as the axis.

Validity, stated up front: the dictionary was enumerated from Claude's
phrasing. --sweep measures how much of each other model's fold-shaped
language the dictionary already hears, and lists the untracked forms with
counts so they can be audited and added (with origin="cross-model") before
any rate is compared. results/agents.json records the coverage figure so the
page can show it next to every non-Claude row.
"""

import json
import math
import os
import re
import sqlite3
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paths as P
import scan as S
import swear as W
from export import dictionary_vocab, guard

DB = os.path.join(P.ROOT, "agents.sqlite")
OUT = os.path.join(P.RESULTS, "agents.json")
MIN_N = 500          # same leaderboard floor as the main pipeline
POP = "m.has_text=1 AND m.is_compact=0"

# Fold-shaped language that a Claude-built dictionary may not hear. Broad on
# purpose: --sweep reports which of these fire per model and whether the
# dictionary already claims the same message.
CANDIDATES = [
    r"\bapolog(y|ies|ize|ise)\b", r"\bsorry\b", r"\bmy (bad|mistake|error|fault|apologies)\b",
    r"\byou(re| are) (absolutely |completely |totally )?(right|correct)\b", r"\bgood catch\b",
    r"\bi (was|am) wrong\b", r"\bi mis(read|understood|spoke|judged)\b", r"\bi (overlooked|missed)\b",
    r"\bfair (point|enough)\b", r"\bthanks for (catching|flagging|pointing)\b", r"\bcorrection\b",
    r"\bi should(nt| not)? have\b", r"\bthat(s| is) on me\b", r"\bi stand corrected\b",
    r"\bgood point\b", r"\bpoint taken\b", r"\bnoted\b", r"\bunderstood\b",
]


def wilson(k, n, z=1.96):
    if not n:
        return [0.0, 0.0]
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = (z / d) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return [100 * max(0.0, c - h), 100 * min(1.0, c + h)]


def pct(n, d):
    return 100.0 * n / d if d else 0.0


def load():
    db = sqlite3.connect(DB)
    q = f"""SELECT m.file_id, m.line_no, m.side, m.model, m.harness, m.text, m.fingerprint
            FROM messages m WHERE {POP} ORDER BY m.file_id, m.line_no"""
    return db, list(db.execute(q))


def main():
    pf = P.PHRASES
    pats = S.load_patterns(pf)
    aop = S.load_assistant_openers(pf)
    gd = S.load_negation_guard(pf)
    rgx = S.load_patterns_regex(pf)
    cons = json.load(open(pf))["_constructs"]
    cat2con = {c: k for k, cs in cons.items() if not k.startswith("_") for c in cs}

    db, rows = load()
    sweep = "--sweep" in sys.argv
    cand_rx = [re.compile(c) for c in CANDIDATES]

    # per model: assistant messages, concession / self_audit messages, phrase counts
    n_asst, conc_msgs, sa_msgs = Counter(), Counter(), Counter()
    phrases = defaultdict(Counter)
    harness_of = defaultdict(Counter)
    # per harness: user messages, frustration
    n_user, frus_msgs = Counter(), Counter()
    # tone-conditioned: (model, bucket) -> n, k
    tone_n, tone_k = Counter(), Counter()
    # sweep: (model, candidate) -> [n, already_tracked]
    sw = defaultdict(lambda: [0, 0]); sw_samples = defaultdict(list)

    cur_file, last_bucket = None, "neutral"
    for fid, ln, side, model, harness, text, fp in rows:
        if fid != cur_file:
            cur_file, last_bucket = fid, "neutral"
        norm = S.normalize(text or "")
        if side == "user":
            n_user[harness] += 1
            hits, directed, shouts, caps = W.analyze(text or "")
            if hits or shouts:
                frus_msgs[harness] += 1
                last_bucket = "hot"
            elif W.CORRECTION_RX.search(norm):
                last_bucket = "correction"
            else:
                last_bucket = "neutral"
            continue
        if side != "assistant" or not model:
            continue
        n_asst[model] += 1
        harness_of[model][harness] += 1
        found = S.all_matches(norm, pats["assistant"], aop[0], aop[1], gd, rgx)
        cats = {c for _ph, c, _p in found}
        is_conc = any(cat2con.get(c) == "concession" for c in cats)
        is_sa = any(cat2con.get(c) == "self_audit" for c in cats)
        conc_msgs[model] += is_conc
        sa_msgs[model] += is_sa
        for ph, c, _p in found:
            if cat2con.get(c) == "concession":
                phrases[model][ph] += 1
        tone_n[(model, last_bucket)] += 1
        tone_k[(model, last_bucket)] += is_conc
        if sweep:
            for c, r in zip(CANDIDATES, cand_rx):
                if r.search(norm):
                    sw[(model, c)][0] += 1
                    sw[(model, c)][1] += is_conc
                    if len(sw_samples[(model, c)]) < 3:
                        i = r.search(norm).start()
                        sw_samples[(model, c)].append(norm[max(0, i - 50):i + 60].replace("\n", " "))

    models = []
    for m in sorted(n_asst, key=lambda m: -n_asst[m]):
        n = n_asst[m]
        models.append(dict(
            model=m, harness=harness_of[m].most_common(1)[0][0],
            harnesses={h: c for h, c in harness_of[m].items()},
            messages=n, concession_msgs=conc_msgs[m], concession_rate=pct(conc_msgs[m], n),
            concession_ci=wilson(conc_msgs[m], n),
            self_audit_msgs=sa_msgs[m], self_audit_rate=pct(sa_msgs[m], n),
            top_phrases=phrases[m].most_common(8),
            tone={b: dict(n=tone_n[(m, b)], k=tone_k[(m, b)], rate=pct(tone_k[(m, b)], tone_n[(m, b)]),
                          ci=wilson(tone_k[(m, b)], tone_n[(m, b)]))
                  for b in ("hot", "correction", "neutral") if tone_n[(m, b)]},
            above_floor=n >= MIN_N))
    harnesses = {h: dict(user_msgs=n_user[h], frustration_msgs=frus_msgs[h],
                         frustration_rate=pct(frus_msgs[h], n_user[h]),
                         frustration_ci=wilson(frus_msgs[h], n_user[h]))
                 for h in n_user}

    # dictionary coverage per model: of assistant messages carrying any
    # fold-shaped candidate, how many does the dictionary already claim?
    coverage = {}
    if sweep:
        any_c = defaultdict(lambda: [0, 0])
        seen_msg = set()
        # recompute per message (a message can match several candidates)
        cur_file = None
        for fid, ln, side, model, harness, text, fp in rows:
            if side != "assistant" or not model:
                continue
            norm = S.normalize(text or "")
            if any(r.search(norm) for r in cand_rx):
                found = S.all_matches(norm, pats["assistant"], aop[0], aop[1], gd, rgx)
                is_conc = any(cat2con.get(c) == "concession" for _ph, c, _p in found)
                any_c[model][0] += 1
                any_c[model][1] += is_conc
        coverage = {m: dict(candidate_msgs=v[0], tracked=v[1], coverage=pct(v[1], v[0]))
                    for m, v in any_c.items()}
        print("DICTIONARY COVERAGE -- of assistant messages carrying fold-shaped language,")
        print("how many does the Claude-built dictionary already count as concession?")
        for m in sorted(coverage, key=lambda m: -n_asst[m]):
            v = coverage[m]
            print("  %-28s %4d candidate msgs  tracked %4d  (%.0f%%)" % (m, v["candidate_msgs"], v["tracked"], v["coverage"]))
        print("\nUNTRACKED CANDIDATE FORMS (n>=5) -- audit these before comparing rates:")
        for (m, c), (n, tracked) in sorted(sw.items(), key=lambda kv: -(kv[1][0] - kv[1][1])):
            if n - tracked < 5:
                continue
            print("  %-26s %-48s n=%3d  untracked=%3d" % (m, c, n, n - tracked))
            for s in sw_samples[(m, c)][:2]:
                print("        ...%s" % s[:110])

    out = dict(min_n=MIN_N, models=models, harnesses=harnesses, coverage=coverage)
    bad = guard(out, dictionary_vocab(pf))
    if bad:
        for p_, s_ in bad[:10]:
            print("  GUARD %s: %r" % (p_, s_))
        sys.exit("text guard rejected agents.json -- NOT written")
    json.dump(out, open(OUT, "w"), indent=1, sort_keys=True)
    print("\nwrote %s" % OUT)
    for d in models:
        print("  %-28s %-10s %5d msgs  concession %5.2f%% [%.1f-%.1f]  self-audit %.2f%%%s" % (
            d["model"], d["harness"], d["messages"], d["concession_rate"], *d["concession_ci"],
            d["self_audit_rate"], "" if d["above_floor"] else "  (below floor)"))
        if d["messages"] >= 300:
            print("      tone:", "  ".join("%s %.1f%% (n=%d)" % (b, v["rate"], v["n"]) for b, v in d["tone"].items()))
            print("      top phrases:", ", ".join("%s %d" % (p, n) for p, n in d["top_phrases"][:6]))
    print("\nuser side, by harness (how the user talks to each):")
    for h, v in sorted(harnesses.items(), key=lambda kv: -kv[1]["user_msgs"]):
        print("  %-10s %4d user msgs  frustration %5.2f%% [%.1f-%.1f]" % (h, v["user_msgs"], v["frustration_rate"], *v["frustration_ci"]))


if __name__ == "__main__":
    main()
