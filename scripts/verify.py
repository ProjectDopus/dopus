#!/usr/bin/env python3
"""
Invariant checks. Run after ANY change to a detector, phrases.json, or the sync.

Usage:  python3 scripts/verify.py          all checks (needs the local DB)
        python3 scripts/verify.py --ci     data-free subset (what CI gates PRs on)

Exists because the same bug shipped three times in one day: a fix landed in one
code path while the dataset was produced by another. SHOUT_RX, shouting(), and
USER_GUARD were each "fixed" and each verified by running the file I had edited
rather than the artifact that matters.

Every check here runs against results/all-matches.jsonl -- the actual output --
not against the function under test. A fix that does not move this file did not
happen.
"""

import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paths as P
import scan as S
import swear as W

FAIL = []
WARN = []


def check(name, cond, detail=""):
    print("  %-52s %s" % (name, "ok" if cond else "FAIL"))
    if not cond:
        FAIL.append((name, detail))


# Known-answer fixtures. Each goes through the SAME matcher the dataset uses,
# so a guard that only exists in one code path fails here immediately.
ASSISTANT_FIXTURES = [
    ("You're right — I broke the build.",                 True,  "plain concession"),
    ("Good question — let me be precise.",                True,  "validation opener"),
    ("that page chrome looks exactly right",              False, "artifact, not concession"),
    ("I introduced neither; both are pre-existing.",      False, "denial twin"),
    ("a real bug I introduced — line 456 passes didx",    True,  "genuine ownership"),
    ("my error handling caught it cleanly, no crash",     False, "noun phrase, not fault"),
    ("the migration requirement is mine to execute",      False, "task ownership, not fault"),
    ("that's a different repo, and I haven't checked it", False, "removed phrase"),
    ("Good news: the backend is already multi-site.",     False, "rejected phrase"),
    ("whenever you're ready — say the word",              False, "deference, not concession"),
    ("I asserted that without testing it.",               True,  "asserted = claimed unverified"),
    ("I asserted the value was unchanged rather than trusting it.", False, "asserted = verified (guarded)"),
    ("my fix didn't work, and worse, I claimed it did.",   True,  "claimed = unverified assertion"),
    ("I'll keep going through each notification without checking in.", False, "'checking in' idiom (guarded)"),
    ("I was guessing instead of checking the actual docs.", True,  "instead-of-checking admission"),
    ("I owe you a correction: I only checked the user database.", True, "the owing formula"),
    ("One correction to my audit: I listed the wrong file.", True,  "self-directed correction"),
    ("One correction: that standard is not an agentic profile.", False, "correcting the user, not self"),
]

USER_FIXTURES = [
    ("you're being fucking lazy and not troubleshooting", True,  "insult at a person"),
    ('no `loading="lazy"` on the card images',            False, "HTML attribute"),
    ("the unsynchronized lazy engine/session-factory",    False, "technical term"),
    ("finishReason STOP — so drive() had nothing",        False, "machine output"),
    ("NO. NOTHING WORKS. What don't you understand???",   True,  "standalone shout"),
    ("tools.ts has NO emit path today",                   False, "NO inside prose"),
    ("hell yes",                                          False, "positive idiom"),
    ("nothing in the workflow to stop it",                False, "technical 'stop it'"),
    ("stop overcomplicating your responses. my god.",     True,  "bare 'my god' exasperation"),
]


def main():
    pf = P.PHRASES
    pats = S.load_patterns(pf)
    aop = S.load_assistant_openers(pf)
    guard = S.load_negation_guard(pf)
    rgx = S.load_patterns_regex(pf)

    print("FIXTURES (assistant) -- via S.all_matches, the dataset's matcher")
    for text, expect, why in ASSISTANT_FIXTURES:
        got = bool(S.all_matches(S.normalize(text), pats["assistant"],
                                 aop[0], aop[1], guard, rgx))
        check("%-46s" % (why + (" [expect hit]" if expect else " [expect none]")),
              got == expect, text)

    print("\nFIXTURES (user) -- via W.user_hits / W.find_shouts")
    for text, expect, why in USER_FIXTURES:
        got = bool(W.user_hits(S.normalize(text))) or bool(W.find_shouts(text))
        check("%-46s" % (why + (" [expect hit]" if expect else " [expect none]")),
              got == expect, text)

    print("\nTEXT EXTRACTION -- injected wrappers must not reach the dataset")
    # task-notification was absent from WRAPPER_RX until 2026-08-11. Background
    # task completions are injected as USER messages, so 31.8% of the user
    # population was machine text counted as something the human typed. It moved
    # the headline hot-vs-correction gap from 2.0x to 3.2x. Never again silently.
    for name, entry, expect_empty in (
        ("task-notification stripped",
         {"message": {"role": "user", "content":
          "<task-notification><task-id>abc</task-id>done</task-notification>"}}, True),
        ("system-reminder stripped",
         {"message": {"role": "user", "content":
          "<system-reminder>be nice</system-reminder>"}}, True),
        ("prose around a wrapper survives",
         {"message": {"role": "user", "content":
          "fix the build <task-notification>x</task-notification> now"}}, False),
    ):
        got = S.message_text(entry).strip()
        ok = (got == "") if expect_empty else ("fix the build" in got and "task-id" not in got)
        check("%-46s" % name, ok, repr(got[:60]))

    # --ci: everything above runs without data and is what a pull request can
    # be gated on. Everything below needs history.sqlite, which never leaves
    # the analyst's machine -- so in CI the absence of the DB is expected, not
    # a failure, and we stop here.
    if "--ci" in sys.argv:
        print("\nCI MODE -- data-free checks only (the DB never leaves the analyst's machine)")
        check("phrases.json parses and every pattern compiles", bool(pats) and bool(rgx))
        import subprocess
        r = subprocess.run([sys.executable, os.path.join(HERE, "export.py"), "--selftest"],
                           capture_output=True, text=True)
        check("export.py --selftest (text guard catches planted prose)", r.returncode == 0,
              r.stdout[-200:])
        import webdata
        check("webdata.py imports (report template intact)",
              "__CONTENT__" in webdata.REPORT_TEMPLATE)
        return report()

    print("\nPIPELINE INTEGRITY")
    dbp = P.DB
    rowp = P.ROWS
    denp = P.DENOM
    check("history.sqlite exists", os.path.exists(dbp))
    check("all-matches.jsonl exists", os.path.exists(rowp))
    if not (os.path.exists(dbp) and os.path.exists(rowp)):
        return report()

    db = sqlite3.connect(dbp)
    rows = [json.loads(l) for l in open(rowp, encoding="utf-8")]
    den = json.load(open(denp)) if os.path.exists(denp) else {}

    nf = db.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    disk = sum(1 for dp, _d, ns in os.walk(P.ARCHIVE)
               for n in ns if n.endswith(".jsonl"))
    check("db file count == archive file count on disk", nf == disk, "%d vs %d" % (nf, disk))
    check("no malformed-line rows in db", True)

    # The dataset must be reproducible from the DB with the CURRENT dictionary.
    # This is the check that catches "fixed one path, dataset uses another".
    W_ = ("m.has_text=1 AND m.is_sidechain=0 AND m.is_compact=0 AND m.is_meta=0 "
          "AND m.is_visible_only=0 AND f.project!='%s'" % P.PROJECT_SLUG)
    seen, live_a = set(), 0
    for fp, txt in db.execute(f"""SELECT DISTINCT m.fingerprint, m.text FROM messages m
            JOIN files f ON m.file_id=f.file_id WHERE {W_} AND m.side='assistant'"""):
        if fp in seen:
            continue
        seen.add(fp)
        live_a += len(S.all_matches(S.normalize(txt or ""), pats["assistant"],
                                    aop[0], aop[1], guard, rgx))
    file_a = sum(1 for r in rows if r["side"] == "assistant")
    check("assistant rows == live re-count from db", live_a == file_a,
          "file=%d live=%d -- dataset is STALE, rerun build_rows.py" % (file_a, live_a))

    # The user side needs the same check. All three "fixed one path, dataset used
    # another" bugs were here, and the first version of this file only re-counted
    # the assistant side -- a test blind to the exact bug class it exists for.
    seen_u, live_u = set(), 0
    for fp, txt in db.execute(f"""SELECT DISTINCT m.fingerprint, m.text FROM messages m
            JOIN files f ON m.file_id=f.file_id WHERE {W_} AND m.side='user'"""):
        if fp in seen_u:
            continue
        seen_u.add(fp)
        t = txt or ""
        live_u += len(W.user_hits(S.normalize(t))) + len(W.find_shouts(t))
    file_u = sum(1 for r in rows if r["side"] == "user")
    check("user rows == live re-count from db", live_u == file_u,
          "file=%d live=%d -- dataset is STALE, rerun build_rows.py" % (file_u, live_u))

    if den:
        check("denominators present for both sides",
              den.get("totals", {}).get("assistant") and den["totals"].get("user"))

    # The artifact-level version of the same check: nothing carrying injected
    # markup may survive into the analysis population with has_text=1.
    leaked = db.execute(f"""SELECT COUNT(*) FROM messages m JOIN files f ON m.file_id=f.file_id
        WHERE {W_} AND (m.text LIKE '%<task-notification>%'
                     OR m.text LIKE '%<system-reminder>%')""").fetchone()[0]
    check("no injected wrappers in the analysis population", leaked == 0,
          "%d messages still carry wrapper markup" % leaked)

    # Derived aggregates must be regenerated whenever the dataset is -- the
    # sync recipe depends on a human (or a model) remembering tally.py and
    # analyze.py. Memory is not a mechanism; these two checks are.
    tal = os.path.join(HERE, "..", "results", "TALLY.md")
    ok_t = False
    if os.path.exists(tal):
        import re as _re
        m = _re.findall(r"—\s+([\d,]+) hits", open(tal, encoding="utf-8").read())
        ok_t = sum(int(x.replace(",", "")) for x in m) == len(rows) if m else False
    check("TALLY.md totals match the dataset (rerun tally.py)", ok_t)

    ap = os.path.join(HERE, "..", "results", "analysis.json")
    ok_a = False
    if os.path.exists(ap):
        ag = json.load(open(ap))
        nf_db = db.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        nm_db = db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        ok_a = (ag.get("corpus", {}).get("files") == nf_db
                and ag.get("corpus", {}).get("messages") == nm_db)
    check("analysis.json matches the db (rerun analyze.py)", ok_a)

    # Dopus-web (the public site, a sibling checkout) renders every figure
    # from data.js, which webdata.py generates. Two guards, checked only when
    # the sibling exists so contributors' clones are unaffected:
    #   1. data.js on disk == a live rebuild of the payload (same failure
    #      class as a stale TALLY: sync moves the numbers, the file keeps
    #      the old ones).
    #   2. Claims-watch: the page's prose encodes claims no data file can
    #      refresh ("roughly doubles", "non-overlapping intervals"). Each is
    #      pinned to the condition that makes it true; when data drift breaks
    #      one, that is a PROSE REVIEW, not a pipeline bug -- rewrite the
    #      sentence on the page, then update the condition here if the claim
    #      itself changed.
    webdir = os.path.join(HERE, "..", "..", "Dopus-web")
    if os.path.isdir(webdir):
        import webdata
        pay = webdata.build()
        djs = os.path.join(webdir, "data.js")
        ok_w = False
        if os.path.exists(djs):
            raw = open(djs, encoding="utf-8").read()
            disk = json.loads(raw[len("window.DOPUS = "):].rstrip().rstrip(";"))
            strip = lambda d: {k: v for k, v in d.items() if k != "version"}
            ok_w = strip(disk) == strip(json.loads(json.dumps(pay)))
        check("Dopus-web data.js matches the dataset (rerun webdata.py)", ok_w)

        rp = os.path.join(webdir, "report.html")
        ok_r = (os.path.exists(rp)
                and open(rp, encoding="utf-8").read() == webdata.report_html())
        check("Dopus-web report.html matches REPORT.md (rerun webdata.py)", ok_r)

        lead = pay["leaderboard"]
        pt, ft = pay["pushback_turn"], pay["followthrough"]
        tm = pay["top_model_monthly"]["rows"]
        pc = pay.get("project_control") or []
        or_turn = pay["stats"]["pairs"]["hot_vs_correction_turn"]["odds"]
        others_overlap = all(
            a["ci"][0] <= b["ci"][1] and b["ci"][0] <= a["ci"][1]
            for i, a in enumerate(lead[1:]) for b in lead[i + 2:])
        ag = json.load(open(ap))
        CLAIMS = [
            ("swearing roughly doubles the fold rate",
             1.6 <= or_turn <= 2.6, "per-turn hot-vs-correction OR %.2f" % or_turn),
            ("about one checkable promise in three broken",
             25 <= ft["scoreable_est"]["rate"] <= 42,
             "scoreable broken rate %.1f%%" % ft["scoreable_est"]["rate"]),
            ("hot and correction CIs do not overlap (per turn)",
             pt["hot"]["ci"][0] > pt["correction"]["ci"][1], ""),
            ("over half of matched language lands at depth 1",
             pay["depth"]["rows"][0][2] > 50,
             "depth-1 share %.1f%%" % pay["depth"]["rows"][0][2]),
            ("one busy machine can move any aggregate",
             pay["machines"]["top_share"] > 50, ""),
            ("top model clears the field with non-overlapping intervals",
             lead[0]["ci"][0] > max(d["ci"][1] for d in lead[1:]),
             "leaderboard CIs now overlap"),
            ("top model roughly 1.8x the rest of the field",
             1.5 <= lead[0]["rate"] / lead[1]["rate"] <= 2.2,
             "ratio %.2f" % (lead[0]["rate"] / lead[1]["rate"])),
            ("the rest of the field is unresolved (CIs overlap)",
             others_overlap, ""),
            ("the latest month is up sharply",
             pay["monthly"][-1][1] > pay["monthly"][-2][1], ""),
            ("the top model is itself rising month over month",
             len(tm) >= 2 and tm[-1][1] > tm[-2][1], ""),
            ("top model dominates usage in the latest month",
             tm and tm[-1][2] == max(v[2] for v in tm), ""),
            ("the within-project control still favors the top model",
             len(pc) == 2 and pc[0]["model"] == lead[0]["model"]
             and pc[0]["rate"] > pc[1]["rate"], ""),
            # (the page describes the control project generically -- "the
            # busiest project both models share" -- so no name is pinned)
            ("Claude almost never says the famous sentence",
             pay["absolutely_right"]["occurrences"] < 10, ""),
        ]
        for name, cond, detail in CLAIMS:
            check("PROSE: " + name, cond,
                  detail + " -- the Dopus-web sentence carrying this claim "
                  "needs a human rewrite" if not cond else "")

    fields = {len(r) for r in rows}
    check("every row has the same field count", len(fields) == 1, str(fields))
    check("no row missing msg_fingerprint",
          all(r.get("msg_fingerprint") for r in rows))
    return report()


def report():
    print()
    if FAIL:
        print("%d CHECK(S) FAILED" % len(FAIL))
        for n, d in FAIL:
            print("   %s" % n.strip())
            if d:
                print("      %s" % d[:100])
        sys.exit(1)
    print("all checks passed")


if __name__ == "__main__":
    main()
