#!/usr/bin/env python3
"""
Statistical appendix: association tests for the main findings.

Usage:  python3 scripts/stats.py          (reads results/analysis.json only)

Both variables in every comparison are binary (turn tone x concession present),
so the "correlation" is the phi coefficient -- identical to Pearson r on a 2x2
table. Reported alongside: odds ratio with Woolf 95% CI, risk ratio, Cohen's h,
and a chi-square p (df=1). Omnibus 3x2 / 4x2 tables get chi-square + Cramer's V;
the ordered gradient (neutral < correction < hot) gets Cochran-Armitage.

Provenance: this analysis design was contributed by Kimi Agent (moonshotai),
working only from the published aggregates in this repository. Every figure it
reported was verified by independent recomputation before being adopted --
exact agreement across all rows -- and this script re-derives them from
results/analysis.json so they regenerate with the corpus.

READ THE EFFECT SIZES, NOT JUST THE p-VALUES. phi ~= 0.10 means turn tone alone
explains about 1% of message-level variance: a small, highly reliable
association. That is the honest framing, and any writeup that quotes the
p-values without it is overclaiming.

What these tests cannot do (the aggregate ceiling):
- No clustering correction: messages nest in sessions/projects/machines, so
  the independence assumption behind every p here is violated. A paper needs
  cluster-robust SEs or mixed-effects logistic regression on the row data.
- No multivariate control: confounds (month, model, project) are handled one
  at a time; concession ~ tone + model + month + project answers them jointly.
- The monthly "trend" is four aggregate points -- ecological, not evidence.
- Phrase-level claims need multiple-comparison correction (Holm / BH); the
  construct-level findings above survive any correction trivially.
- n = 1 subject: all inference is about THIS corpus; generalization needs the
  multi-subject pipeline (research question 5).
p-values here are chi-square (df=1); Kimi Agent reported Fisher exact for the
same tables and the two agree to within rounding at these sample sizes.
"""

import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paths as P


def phi(a, b, c, d):
    return (a * d - b * c) / math.sqrt((a + b) * (c + d) * (a + c) * (b + d))


def odds_ratio(a, b, c, d):
    o = (a * d) / (b * c)
    se = math.sqrt(1 / a + 1 / b + 1 / c + 1 / d)
    return o, o * math.exp(-1.96 * se), o * math.exp(1.96 * se)


def risk_ratio(a, b, c, d):
    return (a / (a + b)) / (c / (c + d))


def cohen_h(p1, p2):
    return abs(2 * math.asin(math.sqrt(p1)) - 2 * math.asin(math.sqrt(p2)))


def chi2_2x2(a, b, c, d):
    n = a + b + c + d
    return n * (a * d - b * c) ** 2 / ((a + b) * (c + d) * (a + c) * (b + d))


def sf_df1(x):
    return math.erfc(math.sqrt(x / 2))


def sf_df2(x):
    return math.exp(-x / 2)


def sf_df3(x):
    return math.erfc(math.sqrt(x / 2)) + math.sqrt(2 * x / math.pi) * math.exp(-x / 2)


def omnibus(groups):
    """chi-square for k x 2 table of (k, n) rows."""
    K = sum(k for k, _ in groups)
    N = sum(n for _, n in groups)
    x = 0.0
    for k, n in groups:
        for obs, tot in ((k, K), (n - k, N - K)):
            e = n * tot / N
            x += (obs - e) ** 2 / e
    return x, N, math.sqrt(x / N)


def cochran_armitage(ordered):
    """z for trend over (k, n) rows in score order 0,1,2..."""
    N = sum(n for _, n in ordered)
    K = sum(k for k, _ in ordered)
    pbar = K / N
    scores = list(range(len(ordered)))
    sbar = sum(s * n for s, (_, n) in zip(scores, ordered)) / N
    num = sum(s * (k - n * pbar) for s, (k, n) in zip(scores, ordered))
    var = pbar * (1 - pbar) * sum(n * (s - sbar) ** 2 for s, (_, n) in zip(scores, ordered))
    return num / math.sqrt(var)


def pair_stats(k1, n1, k2, n2):
    """Every 2x2 statistic for (k1/n1) vs (k2/n2), as data."""
    a, b, c, d = k1, n1 - k1, k2, n2 - k2
    o, lo, hi = odds_ratio(a, b, c, d)
    x = chi2_2x2(a, b, c, d)
    return dict(p1=100 * k1 / n1, p2=100 * k2 / n2, phi=phi(a, b, c, d),
                odds=o, odds_lo=lo, odds_hi=hi, rr=risk_ratio(a, b, c, d),
                h=cohen_h(k1 / n1, k2 / n2), p=sf_df1(x))


def pair(name, k1, n1, k2, n2):
    s = pair_stats(k1, n1, k2, n2)
    print("  %-26s %5.1f%% vs %5.1f%%   phi %.3f   OR %.2f (%.2f-%.2f)   RR %.2f   h %.2f   p %.1e"
          % (name, s["p1"], s["p2"], s["phi"], s["odds"], s["odds_lo"], s["odds_hi"],
             s["rr"], s["h"], s["p"]))


def compute(a):
    """The full appendix as data, from an analysis.json dict. main() prints
    from this; webdata.py ships it to the site. One derivation, two outlets."""
    pm = {k: (v["with_concession"], v["assistant_messages"])
          for k, v in a["pushback_control"].items()}
    pt = {k: (v["folded"], v["turns"]) for k, v in a["pushback_per_turn"].items()}
    lead = {d["model"].replace("claude-", ""): (d["concession_msgs"], d["messages"])
            for d in a["leaderboard"]}

    out = {"pm": pm, "pt": pt, "lead": lead, "pairs": {}}
    for name, args in (("hot_vs_correction_turn", (*pt["hot"], *pt["correction"])),
                       ("hot_vs_correction_msg", (*pm["hot"], *pm["correction"])),
                       ("hot_vs_neutral_turn", (*pt["hot"], *pt["neutral"])),
                       ("hot_vs_neutral_msg", (*pm["hot"], *pm["neutral"])),
                       ("correction_vs_neutral_turn", (*pt["correction"], *pt["neutral"])),
                       ("correction_vs_neutral_msg", (*pm["correction"], *pm["neutral"]))):
        out["pairs"][name] = pair_stats(*args)

    x, N, V = omnibus(list(pm.values()))
    out["omnibus_msg"] = dict(chi2=x, n=N, v=V, p=sf_df2(x))
    x, N, V = omnibus(list(pt.values()))
    out["omnibus_turn"] = dict(chi2=x, n=N, v=V, p=sf_df2(x))
    out["trend_msg_z"] = cochran_armitage([pm["neutral"], pm["correction"], pm["hot"]])
    out["trend_turn_z"] = cochran_armitage([pt["neutral"], pt["correction"], pt["hot"]])

    x, N, V = omnibus(list(lead.values()))
    out["model_omnibus"] = dict(df=len(lead) - 1, chi2=x, n=N, v=V, p=sf_df3(x))
    top = next(iter(lead))  # leaderboard arrives rate-sorted; top model first
    out["model_top"] = top
    out["model_pairs"] = {other: pair_stats(*lead[top], *lead[other])
                          for other in lead if other != top}
    return out


def main():
    a = json.load(open(os.path.join(P.RESULTS, "analysis.json")))
    s = compute(a)
    pm, pt, lead = s["pm"], s["pt"], s["lead"]

    print("TONE x CONCESSION -- pairwise")
    pair("hot vs correction (turn)", *pt["hot"], *pt["correction"])
    pair("hot vs correction (msg)", *pm["hot"], *pm["correction"])
    pair("hot vs neutral (turn)", *pt["hot"], *pt["neutral"])
    pair("hot vs neutral (msg)", *pm["hot"], *pm["neutral"])
    pair("correction vs neutral (msg)", *pm["correction"], *pm["neutral"])

    print("\nOMNIBUS + ORDERED TREND")
    o = s["omnibus_msg"]
    print("  per message: chi2(2, N=%d) = %.1f   V = %.3f   p = %.1e" % (o["n"], o["chi2"], o["v"], o["p"]))
    o = s["omnibus_turn"]
    print("  per turn:    chi2(2, N=%d) = %.1f    V = %.3f   p = %.1e" % (o["n"], o["chi2"], o["v"], o["p"]))
    print("  Cochran-Armitage (neutral<correction<hot):  z = %.1f (msg)   z = %.1f (turn)"
          % (s["trend_msg_z"], s["trend_turn_z"]))

    print("\nMODEL x CONCESSION")
    o = s["model_omnibus"]
    print("  omnibus: chi2(%d, N=%d) = %.1f   V = %.3f   p = %.1e"
          % (o["df"], o["n"], o["chi2"], o["v"], o["p"]))
    for other in ("opus-4-8", "sonnet-5", "fable-5"):
        pair("opus-5 vs %s" % other, *lead["opus-5"], *lead[other])

    print("\nRead the effect sizes, not just the p-values: phi ~0.10 means tone")
    print("alone explains ~1% of message-level variance -- small, and highly")
    print("reliable. See the module docstring for what these tests cannot do.")


if __name__ == "__main__":
    main()
