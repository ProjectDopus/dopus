#!/usr/bin/env python3
"""Emit Dopus-web/data.js -- every number the public site renders, from the
pipeline artifacts. The site's index.html carries no figures of its own; it
populates from this file, so a sync that regenerates data.js has synced the
site by construction.

Usage:  python3 scripts/webdata.py          (writes ../../Dopus-web/data.js)

Sources (nothing computed here that a repo artifact doesn't already carry,
except small derivations like weighted follow-through rates and Wilson CIs):
  results/analysis.json        corpus, constructs, leaderboard, monthly, tone
  results/denominators.json    per-machine populations, tally denominators
  results/all-matches.jsonl    phrase-level counts (local file; only counts leave)
  results/followthrough.json   detector strata sizes
  results/coding/followthrough-labels-20260811.json
                               the FINAL follow-through coding (the HTML-round
                               recode; the file named *-rescored.json is the
                               abandoned round-1 pair-only pass -- do not use it)
  scripts/stats.py             the statistical appendix, via stats.compute()

Privacy: the payload passes export.py's text guard -- every string must be a
dictionary phrase, a hex id, or a short structural token. Project paths never
enter the payload (project_control ships numbers only). No transcript text,
ever; this file is destined for a public host.

Determinism: no wall-clock timestamps. Version = corpus last-day + git commit,
so verify.py can rebuild the payload and demand an exact match.
"""

import json
import math
import os
import re
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paths as P
import stats as ST
from analyze import MIN_MODEL_N
from export import dictionary_vocab, guard, git_stamp

WEB_DIR = os.path.normpath(os.path.join(P.ROOT, "..", "Dopus-web"))
OUT = os.path.join(WEB_DIR, "data.js")
LABELS = os.path.join(P.RESULTS, "coding", "followthrough-labels-20260811.json")

DAYS_IN_MONTH = {1: 31, 2: 28, 3: 31, 4: 30, 5: 31, 6: 30,
                 7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31}


def wilson(k, n, z=1.96):
    if not n:
        return [0.0, 0.0]
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = (z / d) * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return [100 * max(0.0, c - h), 100 * min(1.0, c + h)]


def short(model):
    return model.replace("claude-", "")


def spearman(values):
    """rho of values vs their index, exact permutation p for small n."""
    n = len(values)
    if n < 3:
        return None
    rank = {v: i for i, v in enumerate(sorted(values))}
    r = [rank[v] for v in values]

    def rho_of(seq):
        d2 = sum((s - i) ** 2 for i, s in enumerate(seq))
        return 1 - 6 * d2 / (n * (n * n - 1))

    rho = rho_of(r)
    if n <= 7:
        from itertools import permutations
        perms = list(permutations(range(n)))
        p = sum(1 for pm in perms if abs(rho_of(pm)) >= abs(rho) - 1e-12) / len(perms)
    else:
        t = rho * math.sqrt((n - 2) / (1 - rho * rho)) if abs(rho) < 1 else float("inf")
        p = math.erfc(abs(t) / math.sqrt(2))  # normal approx, fine at these n
    return dict(rho=rho, p=p, n=n)


def month_span(first, last):
    (y1, m1, d1), (y2, m2, d2) = ([int(x) for x in s[:10].split("-")] for s in (first, last))
    return (y2 - y1) * 12 + (m2 - m1) - (1 if d2 < d1 else 0)


def build():
    a = json.load(open(os.path.join(P.RESULTS, "analysis.json")))
    den = json.load(open(os.path.join(P.RESULTS, "denominators.json")))
    ft = json.load(open(os.path.join(P.RESULTS, "followthrough.json")))
    labels = json.load(open(LABELS))["records"]
    rows = [json.loads(l) for l in open(P.ROWS, encoding="utf-8")]
    s = ST.compute(a)
    commit, dirty = git_stamp()

    out = {}

    # ------------------------------------------------------------- corpus
    c = a["corpus"]
    first, last = c["first"][:10], c["last"][:10]
    last_m = int(last[5:7])
    out["corpus"] = dict(
        messages=c["messages"], files=c["files"], machines=c["machines"],
        first=first, last=last, months=month_span(first, last),
        assistant=c["population"]["assistant"], user=c["population"]["user"],
        partial_month=dict(day=int(last[8:10]), days=DAYS_IN_MONTH[last_m]))

    # -------------------------------------------------------------- tally
    out["tally"] = {}
    for side in ("assistant", "user"):
        sub = [r for r in rows if r["side"] == side]
        msgs = len({r["msg_fingerprint"] for r in sub})
        denom = den["totals"][side]
        out["tally"][side] = dict(hits=len(sub), messages=msgs, denom=denom,
                                  pct=100.0 * msgs / denom)

    # --------------------------------------------------- constructs et al.
    out["constructs"] = {k: dict(rate=v["rate"], ci=v["ci"], hits=v["hits"],
                                 messages=v["messages"], denom=v["denom"])
                         for k, v in a["constructs"].items()}
    ar = a["absolutely_right"]
    out["absolutely_right"] = dict(occurrences=ar["occurrences"], rate=ar["rate"])
    out["categories"] = sorted(a["categories"].items(), key=lambda kv: -kv[1])
    out["user_categories"] = sorted(
        ((k, v["hits"]) for k, v in a["user"].items()), key=lambda kv: -kv[1])
    out["top_phrases"] = {side: a["top_phrases"][side][:12]
                          for side in ("assistant", "user")}
    conc = Counter(r["phrase"] for r in rows
                   if r["side"] == "assistant" and r["construct"] == "concession")
    out["top_concession_phrases"] = conc.most_common(6)

    # ------------------------------------------------- machines and depth
    mach = sorted(((m, v["assistant"]) for m, v in den["per_machine"].items()),
                  key=lambda kv: -kv[1])
    tot = sum(n for _, n in mach)
    out["machines"] = dict(
        rows=[[m, n, 100.0 * n / tot] for m, n in mach], total=tot,
        top_share=100.0 * mach[0][1] / tot,
        top2_share=100.0 * (mach[0][1] + mach[1][1]) / tot)
    dep = a["depth_in_turn"]
    dtot = sum(dep.values())
    out["depth"] = dict(
        rows=[[("%s+" % k if k == max(dep, key=int) else k), n, 100.0 * n / dtot]
              for k, n in sorted(dep.items(), key=lambda kv: int(kv[0]))],
        total=dtot)

    # ------------------------------------------------------ tone buckets
    out["pushback_turn"] = {k: dict(rate=v["rate"], ci=v["ci"],
                                    folded=v["folded"], turns=v["turns"])
                            for k, v in a["pushback_per_turn"].items()}
    out["pushback_msg"] = {k: dict(rate=v["rate"], ci=v["ci"],
                                   k=v["with_concession"], n=v["assistant_messages"])
                           for k, v in a["pushback_control"].items()}

    tb_all = a["turn_bucket"]["all_assistant_hits_by_bucket"]
    tb_conc = a["turn_bucket"]["concession_hits_by_preceding_bucket"]
    out["hitcomp"] = {k: dict(k=tb_conc[k], n=tb_all[k],
                              share=100.0 * tb_conc[k] / tb_all[k],
                              ci=wilson(tb_conc[k], tb_all[k]))
                      for k in ("hot", "correction", "neutral")}
    x, N, V = ST.omnibus([(tb_conc[k], tb_all[k]) for k in tb_conc])
    out["hitcomp_test"] = dict(chi2=x, n=N, v=V, p=ST.sf_df2(x))
    out["hitcomp_or"] = {
        "hot_vs_neutral": ST.pair_stats(tb_conc["hot"], tb_all["hot"],
                                        tb_conc["neutral"], tb_all["neutral"]),
        "correction_vs_neutral": ST.pair_stats(tb_conc["correction"], tb_all["correction"],
                                               tb_conc["neutral"], tb_all["neutral"])}
    out["unexplained"] = a["unexplained_concessions"]

    # ---------------------------------------------------------- leaderboard
    out["leaderboard"] = [dict(model=short(d["model"]), rate=d["concession_rate"],
                               ci=d["concession_ci"], k=d["concession_msgs"],
                               n=d["messages"])
                          for d in a["leaderboard"]]
    out["leaderboard_excluded"] = sorted(a["models_excluded_small"].items(),
                                         key=lambda kv: -kv[1])
    out["leaderboard_min_n"] = MIN_MODEL_N

    # Same-month head-to-head: the month where the most leaderboard models
    # carry >=500 messages (ties -> latest month). Controls for the calendar.
    mm = a["model_monthly"]
    by_month = defaultdict(list)
    for model, months in mm.items():
        for m, v in months.items():
            if v["messages"] >= 500:
                by_month[m].append([short(model), v["rate"], v["messages"]])
    month = max(by_month, key=lambda m: (len(by_month[m]), m))
    out["headtohead"] = dict(month=month,
                             rows=sorted(by_month[month], key=lambda r: -r[1]))
    top_model = a["leaderboard"][0]["model"]
    out["top_model_monthly"] = dict(
        model=short(top_model),
        rows=[[m, v["rate"], v["messages"]]
              for m, v in sorted(mm[top_model].items()) if v["messages"] >= 500])

    # Within-project control: numbers only. The project's name is a filesystem
    # path and never enters the payload; verify.py watches the prose that names
    # it on the page.
    pc = a.get("project_control")
    if pc:
        ms = sorted(pc["models"].items(), key=lambda kv: -kv[1]["rate"])
        out["project_control"] = [dict(model=short(m), rate=v["rate"], n=v["messages"])
                                  for m, v in ms]

    out["monthly"] = [[d["month"], d["rate"], d["ci"][0], d["ci"][1], d["messages"]]
                      for d in a["monthly"]]

    # Leaderboard by conversational role + self-audit per model. Rows are in
    # leaderboard (rate) order so the site's tables agree with each other.
    order = [d["model"] for d in a["leaderboard"]]
    out["leaderboard_by_role"] = [
        dict(model=short(m),
             direct=a["leaderboard_by_role"][m]["direct"],
             autonomous=a["leaderboard_by_role"][m]["autonomous"],
             self_audit=a["self_audit_by_model"][m])
        for m in order]

    # Concession vocabulary by model: shares per phrase, plus for each model
    # the phrase whose share most exceeds the pooled share of the other models
    # (min 10 hits, so a fingerprint is a habit, not a fluke).
    pb = a["phrase_by_model"]
    ms = [short(m) for m in pb["models"]]
    phrases = [p for p in pb["phrases"] if p != "other"]
    shares = {short(m): pb["shares"][m] for m in pb["models"]}
    counts = {short(m): pb["counts"][m] for m in pb["models"]}
    totals = {short(m): pb["totals"][m] for m in pb["models"]}
    fp = {}
    for m in ms:
        best = None
        for p in phrases:
            if counts[m][p] < 10:
                continue
            others_k = sum(counts[o][p] for o in ms if o != m)
            others_n = sum(totals[o] for o in ms if o != m)
            other_share = 100.0 * others_k / others_n if others_n else 0.0
            ratio = shares[m][p] / other_share if other_share else float("inf")
            if best is None or ratio > best[1]:
                best = (p, ratio, shares[m][p], other_share)
        if best:
            fp[m] = dict(phrase=best[0], ratio=best[1], share=best[2], others=best[3])
    out["phrase_by_model"] = dict(
        models=ms, phrases=phrases, shares=shares, counts=counts, totals=totals,
        chi2=pb["chi2"], df=pb["df"], n=pb["n"], v=pb["v"], p=pb["p"],
        fingerprint=fp)

    # ------------------------------------------------------- follow-through
    strata_n = {k: v for k, v in ft["labels"].items() if k != "ineligible"}
    per = defaultdict(Counter)
    for r in labels:
        per[r["detector"]][r["did_it"]] += 1
    strata, action = {}, sum(strata_n.values())
    for name, N in strata_n.items():
        yes, no = per[name]["yes"], per[name]["no"]
        strata[name] = dict(N=N, broken=no, decided=yes + no,
                            rate=100.0 * no / (yes + no), ci=wilson(no, yes + no))
    wsum = lambda names, f: sum(strata_n[n_] * f(strata[n_]) for n_ in names) / sum(
        strata_n[n_] for n_ in names)
    all_s = list(strata)
    scoreable_s = [n_ for n_ in all_s if n_ != "unobserved"]
    scoreable = sum(strata_n[n_] for n_ in scoreable_s)
    est_rate = wsum(all_s, lambda st: st["rate"])
    sc_rate = wsum(scoreable_s, lambda st: st["rate"])
    # Detector-as-classifier accuracy on the labelled records the detector
    # actually scored (honored/not_honored) and the coder decided; the
    # "say honored always" baseline is the majority-class rate on the same
    # records. These are the two figures the page quotes when it says the
    # detector failed as a classifier and was used as a sampling frame.
    scored = [r for r in labels if r["detector"] in ("honored", "not_honored")
              and r["did_it"] in ("yes", "no")]
    truth = {"yes": "honored", "no": "not_honored"}
    agree = sum(1 for r in scored if truth[r["did_it"]] == r["detector"])
    n_yes = sum(1 for r in scored if r["did_it"] == "yes")
    acc = 100.0 * agree / len(scored) if scored else 0.0
    base = 100.0 * max(n_yes, len(scored) - n_yes) / len(scored) if scored else 0.0
    out["followthrough"] = dict(
        eligible=ft["total_eligible_construct_messages"],
        action=action, scoreable=scoreable, strata=strata,
        labels_total=len(labels),
        detector=dict(accuracy=acc, baseline=base, n=len(scored),
                      min_downstream=ft["parameters"]["MIN_DOWNSTREAM"]),
        labels_decided=sum(v["decided"] for v in strata.values()),
        est=dict(rate=est_rate, lo=wsum(all_s, lambda st: st["ci"][0]),
                 hi=wsum(all_s, lambda st: st["ci"][1]),
                 count=round(est_rate / 100.0 * action)),
        scoreable_est=dict(rate=sc_rate, count=round(sc_rate / 100.0 * scoreable)))

    # ------------------------------------------------------ stats appendix
    out["stats"] = dict(
        pairs=s["pairs"], omnibus_msg=s["omnibus_msg"], omnibus_turn=s["omnibus_turn"],
        trend_msg_z=s["trend_msg_z"], trend_turn_z=s["trend_turn_z"],
        trend_msg_p=math.erfc(abs(s["trend_msg_z"]) / math.sqrt(2)),
        trend_turn_p=math.erfc(abs(s["trend_turn_z"]) / math.sqrt(2)),
        model_omnibus=s["model_omnibus"], model_top=s["model_top"],
        model_pairs=s["model_pairs"],
        monthly_spearman=spearman([m[1] for m in out["monthly"]]))

    out["version"] = dict(corpus_last=last, commit=commit, dirty=dirty)
    return out


META_RX = re.compile(r'(<meta name="description" content="[^"]*?)\d[\d,]*k?\+? messages')


def stamp_meta(payload):
    """The <meta name=description> on index.html can't be populated by JS
    (crawlers read the raw file), so it is the one number the page carries
    literally. Stamp it here at sync time, rounded down to the nearest 10k
    so it reads as a scale, not a count that is wrong by Tuesday."""
    ip = os.path.join(WEB_DIR, "index.html")
    if not os.path.exists(ip):
        return
    html = open(ip, encoding="utf-8").read()
    n = payload["corpus"]["messages"] // 10000 * 10
    new = META_RX.sub(lambda m: m.group(1) + "%dk+ messages" % n, html, count=1)
    if new != html:
        open(ip, "w", encoding="utf-8").write(new)


def render(payload):
    return "window.DOPUS = %s;\n" % json.dumps(payload, sort_keys=True,
                                               separators=(",", ": "), indent=1)


# ---------------------------------------------------------------- report page
# REPORT.md rendered with the site's styling at sync time. Same freshness as
# data.js (the report only changes when the pipeline runs) and it works with
# the repo private -- only the rendered HTML ships. Markdown support covers
# exactly what REPORT.md uses: h1-h3, hr, blockquotes, tables, bold/italic/
# code, links. Anything fancier shows up literally, which verify's equality
# check makes impossible to miss.

import html as _html

GH = "https://github.com/ProjectDopus/dopus/blob/main/"


def md_inline(s):
    s = _html.escape(s, quote=False)
    s = re.sub(r"\*\*([^*]+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<!\*)\*([^*\n]+?)\*(?!\*)", r"<em>\1</em>", s)
    s = re.sub(r"`([^`]+?)`", r"<code>\1</code>", s)
    s = re.sub(r"\[([^\]]+)\]\((?!https?://)([^)]+)\)",
               lambda m: '<a href="%s%s">%s</a>' % (GH, m.group(2), m.group(1)), s)
    s = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r'<a href="\2">\1</a>', s)
    return s


def md_to_html(md):
    out, para, table = [], [], []

    def flush_para():
        if para:
            out.append("<p>%s</p>" % md_inline(" ".join(para)))
            para.clear()

    def flush_table():
        if table:
            head, rows = table[0], table[2:]
            cells = lambda r: [c.strip() for c in r.strip().strip("|").split("|")]
            out.append('<table class="data">')
            out.append("<tr>%s</tr>" % "".join(
                "<th>%s</th>" % md_inline(c) for c in cells(head)))
            for r in rows:
                out.append("<tr>%s</tr>" % "".join(
                    "<td>%s</td>" % md_inline(c) for c in cells(r)))
            out.append("</table>")
            table.clear()

    for line in md.splitlines():
        if line.startswith("|"):
            flush_para()
            table.append(line)
            continue
        flush_table()
        stripped = line.strip()
        if not stripped:
            flush_para()
        elif stripped == "---":
            flush_para()
            out.append("<hr>")
        elif stripped.startswith("### "):
            flush_para()
            out.append("<h3>%s</h3>" % md_inline(stripped[4:]))
        elif stripped.startswith("## "):
            flush_para()
            out.append("<h2>%s</h2>" % md_inline(stripped[3:]))
        elif stripped.startswith("# "):
            flush_para()
            out.append("<h1>%s</h1>" % md_inline(stripped[2:]))
        elif stripped.startswith("> "):
            flush_para()
            out.append('<blockquote>%s</blockquote>' % md_inline(stripped[2:]))
        else:
            para.append(stripped)
    flush_para()
    flush_table()
    return "\n".join(out)


REPORT_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Dopus — corpus analysis</title>
<meta name="description" content="The full Dopus report: capitulation language, tone effects, model leaderboard, and follow-through across one complete Claude Code history.">
<meta property="og:type" content="article">
<meta property="og:title" content="Dopus — corpus analysis">
<meta property="og:description" content="The full report behind the findings: every figure, every caveat.">
<meta property="og:image" content="https://projectdopus.org/img/hero.jpg">
<meta name="twitter:card" content="summary_large_image">
<link rel="icon" type="image/svg+xml" href="img/dopus-glint.svg">
<style>
  :root{--ink:#141412;--gray:#6e6e68;--faint:#9b9b93;--hair:#e9e9e4;--accent:#cd4a00}
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:#fff;color:var(--ink);
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Roboto,"Helvetica Neue",Arial,sans-serif;
    line-height:1.65;-webkit-font-smoothing:antialiased}
  nav{position:fixed;top:0;left:0;right:0;z-index:50;margin:0 auto;width:100%;
    background:transparent;border:1px solid transparent;
    transition:width .42s cubic-bezier(.22,.61,.36,1),margin .42s cubic-bezier(.22,.61,.36,1),
      border-radius .42s cubic-bezier(.22,.61,.36,1),background .26s,border-color .26s,box-shadow .26s}
  nav.scrolled{margin-top:14px;width:min(1100px,calc(100% - 32px));border-radius:999px;
    border-color:rgba(20,20,18,.08);background:rgba(255,255,255,.72);
    backdrop-filter:blur(14px) saturate(1.4);
    box-shadow:inset 0 0 2px 1px rgba(255,255,255,.6),0 6px 24px rgba(17,17,26,.06),0 12px 40px rgba(17,17,26,.05)}
  .nav-in{max-width:1240px;margin:0 auto;padding:0 28px;height:62px;display:flex;align-items:center;
    transition:height .42s cubic-bezier(.22,.61,.36,1),padding .42s cubic-bezier(.22,.61,.36,1)}
  nav.scrolled .nav-in{height:50px;padding:0 24px}
  body{padding-top:62px}
  @media(prefers-reduced-motion:reduce){nav,.nav-in{transition:none}}
  .nav-in img{height:30px;width:auto;display:block}
  .nav-in .back{margin-left:auto;font-size:14px;color:var(--gray);text-decoration:none}
  .nav-in .back:hover{color:var(--ink)}
  .doc{max-width:760px;margin:0 auto;padding:56px 28px 90px;font-size:16px;color:#2c2c28}
  .doc h1{font-size:34px;font-weight:650;letter-spacing:-0.02em;line-height:1.15;color:var(--ink);margin-bottom:18px}
  .doc h2{font-size:23px;font-weight:650;letter-spacing:-0.015em;color:var(--ink);margin:52px 0 14px}
  .doc h3{font-size:17px;font-weight:650;color:var(--ink);margin:34px 0 10px}
  .doc p{margin:0 0 16px}
  .doc hr{border:none;border-top:1px solid var(--hair);margin:44px 0}
  .doc blockquote{border-left:3px solid var(--accent);padding:4px 0 4px 20px;margin:24px 0;
    font-size:18px;font-style:italic;color:#2c2c28}
  .doc code{font-size:14px;background:#f6f6f2;border-radius:4px;padding:1.5px 5px}
  .doc a{color:inherit}
  table.data{width:100%;border-collapse:collapse;font-size:13.5px;
    font-variant-numeric:tabular-nums;margin:8px 0 22px}
  table.data th{text-align:left;font-size:11px;letter-spacing:.08em;text-transform:uppercase;
    color:var(--faint);padding:10px 10px;border-bottom:1px solid var(--ink);font-weight:650}
  table.data td{padding:10px;border-bottom:1px solid var(--hair);vertical-align:top}
  footer{border-top:1px solid var(--hair);padding:40px 0 64px;text-align:center;
    font-size:12.5px;color:var(--faint)}
  footer img{height:22px;width:auto;display:block;margin:0 auto 10px;opacity:.9}
  footer a{color:var(--ink);text-decoration:none;border-bottom:1px solid var(--hair)}
  @media(max-width:640px){
    .doc{padding:40px 16px 70px;font-size:15px}
    table.data{display:block;overflow-x:auto;-webkit-overflow-scrolling:touch}
  }
</style>
</head>
<body>
<nav><div class="nav-in">
  <a href="index.html"><img src="img/dopus-logo.svg" alt="Dopus"></a>
  <a class="back" href="index.html">← Back to the findings</a>
</div></nav>
<div class="doc">
__CONTENT__
</div>
<footer>
  <img src="img/dopus-glint.svg" alt="">
  <div>Project Dopus · generated from <a href="__GH__REPORT.md">REPORT.md</a></div>
</footer>
<script>
const nav = document.querySelector("nav");
addEventListener("scroll", ()=>nav.classList.toggle("scrolled", scrollY>24), {passive:true});
</script>
</body>
</html>
"""


def report_html():
    md = open(os.path.join(P.ROOT, "REPORT.md"), encoding="utf-8").read()
    return (REPORT_TEMPLATE.replace("__CONTENT__", md_to_html(md))
            .replace("__GH__", GH))


def main():
    if not os.path.isdir(WEB_DIR):
        sys.exit("Dopus-web not found at %s -- nothing to write" % WEB_DIR)
    payload = build()
    bad = guard(payload, dictionary_vocab(P.PHRASES))
    if bad:
        for path, sample in bad:
            print("  GUARD %s: %r" % (path, sample))
        sys.exit("text guard rejected the payload -- data.js NOT written")
    open(OUT, "w", encoding="utf-8").write(render(payload))
    stamp_meta(payload)
    print("wrote %s (%d bytes, corpus through %s)"
          % (OUT, os.path.getsize(OUT), payload["corpus"]["last"]))
    rp = os.path.join(WEB_DIR, "report.html")
    open(rp, "w", encoding="utf-8").write(report_html())
    print("wrote %s (%d bytes, from REPORT.md)" % (rp, os.path.getsize(rp)))


if __name__ == "__main__":
    main()
