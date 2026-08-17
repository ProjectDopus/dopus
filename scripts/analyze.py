#!/usr/bin/env python3
"""Full descriptive analysis of the corpus. Reads the frozen DB + the row dataset.

Usage:  python3 scripts/analyze.py            # prints report, writes results/analysis.json

Every rate in here is computed against a denominator derived with the SAME WHERE
clause build_rows.py used. Nothing is copied forward from a previous run -- the
headline denominator drifted once already between a writeup and the live DB.

Emits results/analysis.json (aggregates only, no message text -- safe to track).
"""

import json
import os
import re
import sqlite3
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paths as P
import scan as S

WHERE = ("m.has_text=1 AND m.is_sidechain=0 AND m.is_compact=0 AND m.is_meta=0 "
         "AND m.is_visible_only=0 AND f.project != '-Users-zach-GitHub-Dopus'")

# Models with fewer than this many messages can't carry a rate worth printing.
MIN_MODEL_N = 500


def pct(n, d):
    return 100.0 * n / d if d else 0.0


def wilson(k, n, z=1.96):
    """95% CI for a proportion. Small-n model slices need the interval shown."""
    if not n:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = (z / d) * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return (100 * max(0.0, c - h), 100 * min(1.0, c + h))


def main():
    db = sqlite3.connect(P.DB)
    rows = [json.loads(l) for l in
            open(P.ROWS, encoding="utf-8")]

    out = {}

    # ---------------------------------------------------------------- corpus
    files, msgs = db.execute("SELECT COUNT(*) FROM files").fetchone()[0], \
                  db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    lo, hi = db.execute(
        f"SELECT MIN(m.ts), MAX(m.ts) FROM messages m JOIN files f ON m.file_id=f.file_id "
        f"WHERE {WHERE}").fetchone()
    pop = {}
    for side in ("assistant", "user"):
        pop[side] = db.execute(
            f"""SELECT COUNT(DISTINCT m.fingerprint) FROM messages m
                JOIN files f ON m.file_id=f.file_id WHERE {WHERE} AND m.side=?""",
            (side,)).fetchone()[0]
    out["corpus"] = dict(files=files, messages=msgs, first=lo, last=hi,
                         population=pop,
                         machines=db.execute(
                             "SELECT COUNT(DISTINCT machine_id) FROM files").fetchone()[0])

    # ------------------------------------------------------------ constructs
    con_hits, con_msgs = Counter(), defaultdict(set)
    for r in rows:
        con_hits[r["construct"]] += 1
        con_msgs[r["construct"]].add(r["msg_fingerprint"])
    out["constructs"] = {}
    for c, hits in con_hits.most_common():
        side = "user" if c == "frustration" else "assistant"
        n = len(con_msgs[c])
        out["constructs"][c] = dict(hits=hits, messages=n, side=side,
                                    denom=pop[side], rate=pct(n, pop[side]),
                                    ci=wilson(n, pop[side]))

    # -------------------------------------------------- per-model leaderboard
    # The original ask: which model capitulates most. Denominators per model come
    # from the DB under the same WHERE, never from the row file.
    mdenom = dict(db.execute(
        f"""SELECT m.model, COUNT(DISTINCT m.fingerprint) FROM messages m
            JOIN files f ON m.file_id=f.file_id
            WHERE {WHERE} AND m.side='assistant' AND m.model IS NOT NULL
              AND m.model != '' GROUP BY m.model"""))
    mhits = defaultdict(Counter)
    mmsgs = defaultdict(lambda: defaultdict(set))
    for r in rows:
        if r["side"] != "assistant" or not r["model"]:
            continue
        mhits[r["model"]][r["construct"]] += 1
        mmsgs[r["model"]][r["construct"]].add(r["msg_fingerprint"])
    lead = []
    for model, n in sorted(mdenom.items(), key=lambda kv: -kv[1]):
        if n < MIN_MODEL_N or model == "<synthetic>":
            continue
        c = len(mmsgs[model]["concession"])
        f_ = len(mmsgs[model]["flattery"])
        a = len(mmsgs[model]["acknowledgment"])
        lead.append(dict(model=model, messages=n,
                         concession_msgs=c, concession_rate=pct(c, n),
                         concession_ci=wilson(c, n),
                         flattery_rate=pct(f_, n), acknowledgment_rate=pct(a, n),
                         concession_hits=mhits[model]["concession"]))
    out["leaderboard"] = sorted(lead, key=lambda d: -d["concession_rate"])
    out["models_excluded_small"] = {m: n for m, n in mdenom.items()
                                    if n < MIN_MODEL_N or m == "<synthetic>"}

    # -------------------------------------------------------------- temporal
    mon_denom = dict(db.execute(
        f"""SELECT substr(m.ts,1,7), COUNT(DISTINCT m.fingerprint) FROM messages m
            JOIN files f ON m.file_id=f.file_id
            WHERE {WHERE} AND m.side='assistant' AND m.ts IS NOT NULL
            GROUP BY 1 ORDER BY 1"""))
    mon_msgs = defaultdict(set)
    for r in rows:
        if r["side"] == "assistant" and r["construct"] == "concession" and r["timestamp"]:
            mon_msgs[r["timestamp"][:7]].add(r["msg_fingerprint"])
    out["monthly"] = [dict(month=m, messages=n, concession_msgs=len(mon_msgs[m]),
                           rate=pct(len(mon_msgs[m]), n), ci=wilson(len(mon_msgs[m]), n))
                      for m, n in sorted(mon_denom.items()) if n >= 200]

    # ------------------------------------------- model x month (confound test)
    # Denominators from the DB, hits from rows, leaderboard models only. Feeds
    # the same-month head-to-head (webdata.py) that controls the leaderboard
    # for the calendar shift.
    lead_models = {d["model"] for d in lead}
    mm_denom = {(mo, m): n for mo, m, n in db.execute(
        f"""SELECT m.model, substr(m.ts,1,7), COUNT(DISTINCT m.fingerprint)
            FROM messages m JOIN files f ON m.file_id=f.file_id
            WHERE {WHERE} AND m.side='assistant' AND m.ts IS NOT NULL
              AND m.model IS NOT NULL AND m.model != '' GROUP BY 1, 2""")}
    mm_msgs = defaultdict(set)
    for r in rows:
        if (r["side"] == "assistant" and r["construct"] == "concession"
                and r["model"] and r["timestamp"]):
            mm_msgs[(r["model"], r["timestamp"][:7])].add(r["msg_fingerprint"])
    out["model_monthly"] = {}
    for (mo, m), n in sorted(mm_denom.items()):
        if mo not in lead_models:
            continue
        c = len(mm_msgs[(mo, m)])
        out["model_monthly"].setdefault(mo, {})[m] = dict(
            messages=n, concession_msgs=c, rate=pct(c, n))

    # -------------------------------------- within-project control (confound)
    # Busiest single project where the top two leaderboard models both have
    # enough volume: does the leaderboard gap survive holding the project fixed?
    if len(lead) >= 2:
        top2 = [lead[0]["model"], lead[1]["model"]]
        pj = defaultdict(dict)
        for model, proj, n in db.execute(
                f"""SELECT m.model, f.project, COUNT(DISTINCT m.fingerprint)
                    FROM messages m JOIN files f ON m.file_id=f.file_id
                    WHERE {WHERE} AND m.side='assistant' AND m.model IN (?, ?)
                    GROUP BY 1, 2""", top2):
            pj[proj][model] = n
        shared = [(p, d) for p, d in pj.items()
                  if all(d.get(m, 0) >= 200 for m in top2)]
        if shared:
            proj, d_ = max(shared, key=lambda pd: sum(pd[1].values()))
            pj_msgs = defaultdict(set)
            for r in rows:
                if (r["side"] == "assistant" and r["construct"] == "concession"
                        and r["model"] in top2 and r.get("project") == proj):
                    pj_msgs[r["model"]].add(r["msg_fingerprint"])
            out["project_control"] = dict(
                project=proj,
                models={m: dict(messages=d_[m],
                                concession_msgs=len(pj_msgs[m]),
                                rate=pct(len(pj_msgs[m]), d_[m]))
                        for m in top2})

    # ------------------------------------------------- the pushback control
    # Concessions per user turn, split by whether that turn was hot. This is the
    # least flattering metric in the project and it stays.
    bt = Counter()
    for side, txt in db.execute(
            f"""SELECT m.side, m.text FROM messages m JOIN files f ON m.file_id=f.file_id
                WHERE {WHERE} AND m.side='user'"""):
        bt["total"] += 1
    buckets = Counter(r["turn_bucket"] for r in rows if r["side"] == "assistant")
    out["turn_bucket"] = {
        "concession_hits_by_preceding_bucket": dict(
            Counter(r["turn_bucket"] for r in rows
                    if r["side"] == "assistant" and r["construct"] == "concession")),
        "all_assistant_hits_by_bucket": dict(buckets),
    }

    # The ~70% claim, restated exactly rather than repeated.
    conc = [r for r in rows if r["side"] == "assistant" and r["construct"] == "concession"]
    no_marker = [r for r in conc if r["turn_bucket"] == "neutral"]
    out["unexplained_concessions"] = dict(
        total=len(conc), neutral_preceding=len(no_marker),
        share=pct(len(no_marker), len(conc)))

    # ------------------------------------------------ the pushback control
    # THE question: does Claude concede more when the user is angry? Needs a
    # per-bucket DENOMINATOR -- how many assistant messages followed a hot turn
    # at all -- which nothing else emits. Without it you can only say where
    # concessions land, not how often they land per opportunity.
    import swear as W_
    conc_fp = {r["msg_fingerprint"] for r in rows
               if r["side"] == "assistant" and r["construct"] == "concession"}
    bden, bhit = Counter(), Counter()
    for (fid,) in db.execute(
            f"""SELECT DISTINCT f.file_id FROM files f JOIN messages m ON m.file_id=f.file_id
                WHERE {WHERE}"""):
        cur = None
        for side, txt, fp in db.execute(
                f"""SELECT m.side, m.text, m.fingerprint FROM messages m
                    JOIN files f ON m.file_id=f.file_id
                    WHERE m.file_id=? AND {WHERE} ORDER BY m.line_no""", (fid,)):
            if side == "user":
                n_ = S.normalize(txt or "")
                if W_.user_hits(n_) or W_.find_shouts(txt or ""):
                    cur = "hot"
                elif W_.CORRECTION_RX.search(n_):
                    cur = "correction"
                else:
                    cur = "neutral"
            elif cur:
                bden[cur] += 1
                if fp in conc_fp:
                    bhit[cur] += 1
    out["pushback_control"] = {
        b: dict(assistant_messages=bden[b], with_concession=bhit[b],
                rate=pct(bhit[b], bden[b]), ci=wilson(bhit[b], bden[b]))
        for b in ("hot", "correction", "neutral")}

    # Per-TURN version: one row per thing the user said -- did any assistant
    # reply before their next turn contain a concession. Rates run higher than
    # per-message because a turn can hold several replies. Emitted here so the
    # statistical appendix (scripts/stats.py) derives from analysis.json alone.
    tden, tfold = Counter(), Counter()
    for (fid,) in db.execute(
            f"""SELECT DISTINCT f.file_id FROM files f JOIN messages m ON m.file_id=f.file_id
                WHERE {WHERE}"""):
        cur, folded = None, False
        for side, txt, fp in db.execute(
                f"""SELECT m.side, m.text, m.fingerprint FROM messages m
                    JOIN files f ON m.file_id=f.file_id
                    WHERE m.file_id=? AND {WHERE} ORDER BY m.line_no""", (fid,)):
            if side == "user":
                if cur:
                    tden[cur] += 1; tfold[cur] += folded
                n_ = S.normalize(txt or "")
                if W_.user_hits(n_) or W_.find_shouts(txt or ""):
                    cur = "hot"
                elif W_.CORRECTION_RX.search(n_):
                    cur = "correction"
                else:
                    cur = "neutral"
                folded = False
            elif cur and fp in conc_fp:
                folded = True
        if cur:
            tden[cur] += 1; tfold[cur] += folded
    out["pushback_per_turn"] = {
        b: dict(turns=tden[b], folded=tfold[b],
                rate=pct(tfold[b], tden[b]), ci=wilson(tfold[b], tden[b]))
        for b in ("hot", "correction", "neutral")}

    # ------------------------------------------------------------ depth/dose
    out["depth_in_turn"] = dict(Counter(
        min(r["depth_in_turn"], 6) for r in rows if r["side"] == "assistant"))
    per_msg = Counter()
    for r in rows:
        if r["side"] == "assistant":
            per_msg[r["msg_fingerprint"]] += 1
    out["hits_per_message"] = dict(Counter(per_msg.values()))

    # ------------------------------------------------------------- user side
    ucat = Counter(r["category"] for r in rows if r["side"] == "user")
    umsgs = {c: len({r["msg_fingerprint"] for r in rows
                     if r["side"] == "user" and r["category"] == c}) for c in ucat}
    out["user"] = {c: dict(hits=n, messages=umsgs[c], rate=pct(umsgs[c], pop["user"]))
                   for c, n in ucat.most_common()}

    # --------------------------------------------------------------- phrases
    out["top_phrases"] = {
        "assistant": Counter(r["phrase"] for r in rows
                             if r["side"] == "assistant").most_common(20),
        "user": Counter(r["phrase"] for r in rows if r["side"] == "user").most_common(20),
    }
    out["categories"] = dict(Counter(r["category"] for r in rows if r["side"] == "assistant"))

    # ------------------------------------------------- the named phrase itself
    RX = re.compile(r"youre absolutely right")
    seen, occ, m_ = set(), 0, 0
    for fp, txt in db.execute(
            f"""SELECT DISTINCT m.fingerprint, m.text FROM messages m
                JOIN files f ON m.file_id=f.file_id WHERE {WHERE} AND m.side='assistant'"""):
        if fp in seen:
            continue
        seen.add(fp)
        k = len(RX.findall(S.normalize(txt or "")))
        if k:
            occ += k
            m_ += 1
    out["absolutely_right"] = dict(occurrences=occ, messages=m_, denom=len(seen),
                                   rate=pct(m_, len(seen)))

    # ---------------------------------------------------------------- output
    p = os.path.join(P.RESULTS, "analysis.json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)

    # ------------------------------------------------------------- print it
    c = out["corpus"]
    print("CORPUS  %d files : %d messages : %d machines : %s -> %s"
          % (c["files"], c["messages"], c["machines"], c["first"][:10], c["last"][:10]))
    print("        analysis population: %d assistant / %d user"
          % (pop["assistant"], pop["user"]))
    ar = out["absolutely_right"]
    print("\n\"you're absolutely right\"  %d occurrences in %d messages  (%.4f%%)"
          % (ar["occurrences"], ar["denom"], ar["rate"]))

    print("\nCONSTRUCTS")
    for k, v in out["constructs"].items():
        print("  %-15s %5d hits  %5d msgs  %6.2f%%  [%.2f-%.2f]  (%s)"
              % (k, v["hits"], v["messages"], v["rate"], v["ci"][0], v["ci"][1], v["side"]))

    print("\nLEADERBOARD -- concession rate per assistant message")
    print("  %-26s %8s %8s %8s %16s" % ("model", "msgs", "conc", "rate", "95% CI"))
    for d in out["leaderboard"]:
        print("  %-26s %8d %8d %7.2f%%  [%5.2f - %5.2f]"
              % (d["model"], d["messages"], d["concession_msgs"], d["concession_rate"],
                 d["concession_ci"][0], d["concession_ci"][1]))
    if out["models_excluded_small"]:
        print("  excluded (n<%d): %s" % (MIN_MODEL_N, out["models_excluded_small"]))

    print("\nMONTHLY")
    for d in out["monthly"]:
        print("  %s  %6d msgs  %5d conc  %5.2f%%  [%.2f-%.2f]"
              % (d["month"], d["messages"], d["concession_msgs"], d["rate"],
                 d["ci"][0], d["ci"][1]))

    print("\nPRECEDING USER TURN (concessions)")
    for k, v in sorted(out["turn_bucket"]["concession_hits_by_preceding_bucket"].items(),
                       key=lambda kv: -kv[1]):
        print("  %-12s %5d" % (k, v))
    u = out["unexplained_concessions"]
    print("  -> %d of %d concessions (%.1f%%) follow a turn with NO correction marker"
          % (u["neutral_preceding"], u["total"], u["share"]))

    print("\nPUSHBACK PER TURN -- did any reply before the user's next turn concede")
    for b in ("hot", "correction", "neutral"):
        d = out["pushback_per_turn"][b]
        print("  %-11s %5d turns  %4d folded  %5.1f%%  [%.1f-%.1f]"
              % (b, d["turns"], d["folded"], d["rate"], d["ci"][0], d["ci"][1]))

    print("\nPUSHBACK CONTROL -- concession rate PER OPPORTUNITY, by preceding turn")
    for b in ("hot", "correction", "neutral"):
        d = out["pushback_control"][b]
        print("  after %-11s %6d assistant msgs  %5d w/ concession  %5.2f%%  [%.2f-%.2f]"
              % (b, d["assistant_messages"], d["with_concession"], d["rate"],
                 d["ci"][0], d["ci"][1]))

    print("\nUSER SIDE")
    for k, v in out["user"].items():
        print("  %-14s %5d hits  %5d msgs  %5.2f%%" % (k, v["hits"], v["messages"], v["rate"]))

    print("\nTOP PHRASES")
    print("  assistant: " + " | ".join("%s %d" % (p_, n) for p_, n in out["top_phrases"]["assistant"][:10]))
    print("  user:      " + " | ".join("%s %d" % (p_, n) for p_, n in out["top_phrases"]["user"][:10]))
    print("\nwrote results/analysis.json")


if __name__ == "__main__":
    main()
