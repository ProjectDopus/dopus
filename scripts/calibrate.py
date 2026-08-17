#!/usr/bin/env python3
"""
Score the follow-through detector against hand labels.

Usage:  python3 scripts/calibrate.py results/coding/followthrough-labels-<date>[-suffix].json

Maps the coder's answer to the detector's classes:
    did_it: yes        -> truth = honored
    did_it: no         -> truth = not_honored
    did_it: cant_tell  -> excluded from accuracy; reported as its own result,
                          because "even the human can't tell" is a property of
                          the instrument's raw material, not coder laziness.

Reports accuracy with Wilson intervals, the full confusion matrix, and the two
error rates that matter asymmetrically:
    false honored      detector said kept, coder said it wasn't  (the dangerous one)
    false not_honored  detector said broken promise, coder says it was kept
"""

import json
import math
import os
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paths as P


def wilson(k, n, z=1.96):
    if not n:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = (z / d) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (100 * max(0.0, c - h), 100 * min(1.0, c + h))


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: calibrate.py <labels.json>")
    labels = json.load(open(sys.argv[1]))["records"]
    det = {}
    for l in open(os.path.join(P.RESULTS, "followthrough.jsonl"), encoding="utf-8"):
        o = json.loads(l)
        det[o["msg_fingerprint"]] = o

    TRUTH = {"yes": "honored", "no": "not_honored"}
    rows = []
    for r in labels:
        d = det.get(r["msg_fingerprint"])
        if not d:
            print("  WARNING: %s not in current detector output (rules changed since the draw?)"
                  % r["msg_fingerprint"])
            continue
        rows.append((r["n"], r["did_it"], TRUTH.get(r["did_it"]), d["label"],
                     d["evidence"], r.get("detector")))

    n_all = len(rows)
    ct = [r for r in rows if r[1] == "cant_tell"]
    decided = [r for r in rows if r[2]]

    print("CALIBRATION -- %d labelled records" % n_all)
    print("  cant_tell        %3d  (%.0f%%)  <- excluded from accuracy, see below"
          % (len(ct), 100.0 * len(ct) / n_all))
    print("  decided          %3d" % len(decided))

    # Where did the detector's classes land among the decided?
    scored = [r for r in decided if r[3] in ("honored", "not_honored")]
    unobs = [r for r in decided if r[3] == "unobserved"]
    agree = sum(1 for r in scored if r[2] == r[3])

    print("\nOF THE DECIDED RECORDS")
    print("  detector scored them   %3d" % len(scored))
    print("  detector unobserved    %3d  (it refused; coder could decide)" % len(unobs))
    if scored:
        lo, hi = wilson(agree, len(scored))
        print("\nACCURACY on scored+decided:  %d/%d = %.0f%%   95%% CI [%.0f, %.0f]"
              % (agree, len(scored), 100.0 * agree / len(scored), lo, hi))

        cm = Counter((r[2], r[3]) for r in scored)
        print("\nCONFUSION (truth -> detector)")
        for t in ("honored", "not_honored"):
            for p_ in ("honored", "not_honored"):
                if cm[(t, p_)]:
                    tag = "ok" if t == p_ else ("FALSE HONORED" if p_ == "honored" else "false not_honored")
                    print("   %-12s -> %-12s %3d   %s" % (t, p_, cm[(t, p_)], tag))

        # The sample was drawn BY DETECTOR LABEL, so the estimable quantities
        # are predictive values per stratum -- P(truth | detector said X).
        # Recall/specificity would need truth-stratified sampling, which is
        # impossible until truth is known. Do not report them from this draw.
        print("\nWHAT EACH DETECTOR LABEL IS WORTH (predictive value per stratum)")
        for lab in ("not_honored", "honored"):
            strat = [r for r in scored if r[3] == lab]
            if not strat:
                continue
            k = sum(1 for r in strat if r[2] == lab)
            lo, hi = wilson(k, len(strat))
            print("  when it says %-12s it is right %d/%d = %.0f%%   CI [%.0f, %.0f]"
                  % (lab, k, len(strat), 100.0 * k / len(strat), lo, hi))
        fh = cm[("not_honored", "honored")]
        print("  FALSE HONORED found: %d  (the dangerous direction)" % fh)

        print("\nDISAGREEMENTS, each one auditable")
        for n_, did, truth, pred, ev, _orig in scored:
            if truth != pred:
                w = ev.get("shared_rare") or ev.get("shared", [])
                extra = ("matched %r +%s turns: \"%s\"" % (",".join(w[:4]),
                         ev.get("turns_later"), ev.get("quote", "")[:90])
                         if pred == "not_honored" else
                         "no re-raise found in %s downstream turns" % ev.get("downstream"))
                print("   #%-3d truth=%-12s detector=%-12s  %s" % (n_, truth, pred, extra))

    # ------------------------------------------------------------ extrapolate
    # The draw was stratified by detector label with KNOWN stratum sizes, so
    # the labels support a weighted estimate of the true broken-promise rate:
    # P(broken) = sum over strata of  N_stratum/N_total * P(broken | stratum).
    # This works EVEN IF the detector is a poor classifier -- it only has to be
    # a sampling frame. Wilson bounds propagated per stratum (conservative).
    agg_p = os.path.join(P.RESULTS, "followthrough.json")
    if os.path.exists(agg_p):
        agg = json.load(open(agg_p))["labels"]
        strata = {}
        for lab in ("honored", "not_honored", "unobserved"):
            drawn = [r for r in decided if r[3] == lab]
            k = sum(1 for r in drawn if r[2] == "not_honored")
            if drawn and agg.get(lab):
                strata[lab] = (agg[lab], k, len(drawn))
        if strata:
            print("\nEXTRAPOLATION -- weighted true broken-promise rate")
            tot = sum(N for N, _k, _n in strata.values())
            mid = lo = hi = 0.0
            for lab, (N, k, n) in sorted(strata.items()):
                w_lo, w_hi = wilson(k, n)
                print("  %-12s N=%4d  labelled %2d/%2d broken  (%.0f%%, CI %.0f-%.0f)"
                      % (lab, N, k, n, 100.0 * k / n, w_lo, w_hi))
                mid += N * (k / n); lo += N * w_lo / 100; hi += N * w_hi / 100
            print("  -> of %d eligible promises: %.0f%% broken   CI [%.0f%%, %.0f%%]"
                  % (tot, 100.0 * mid / tot, 100.0 * lo / tot, 100.0 * hi / tot))
            sc_tot = strata.get("honored", (0, 0, 1))[0] + strata.get("not_honored", (0, 0, 1))[0]
            if "honored" in strata and "not_honored" in strata:
                m2 = sum(N * k / n for lab, (N, k, n) in strata.items() if lab != "unobserved")
                print("  -> of the %d the detector could score: %.1f%% broken"
                      % (sc_tot, 100.0 * m2 / sc_tot))

    # cant_tell is a finding, not noise
    per_det = Counter(r[3] for r in ct)
    print("\nCANT_TELL by detector label: %s" % dict(per_det))
    print("  %.0f%% of a stratified sample was undecidable by the person who lived it."
          % (100.0 * len(ct) / n_all))
    print("  That is the ceiling for ANY labelling effort on pair-only evidence --")
    print("  the coder sees trigger+promise, not what happened after. Deciding those")
    print("  requires reading downstream, which is exactly the detector's job.")


if __name__ == "__main__":
    main()
