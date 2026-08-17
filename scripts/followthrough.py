#!/usr/bin/env python3
"""
Follow-through detector v2: did a concession that PROMISED ACTION get honored?

Usage:  python3 scripts/followthrough.py            score the corpus
        python3 scripts/followthrough.py --sweep    label mix under each rule variant
        python3 scripts/followthrough.py --code N   write a calibration sample to label

The problem this exists for: the text of a concession carries no information
about whether it was kept. "Understood -- I had it backwards. Every placeholder
becomes a real, working feature" reads as a clean, correctly-scoped concession
and was followed by not doing it. Counting phrases cannot see that.

THE LABEL, and why it is this one
---------------------------------
Asking a user "were you right?" does not scale past one participant -- nobody can
verify anyone else's memory. So the label is not an interview. It is the user's
own later behaviour:

    If the user re-raises the same complaint downstream, the concession was not
    honored.

That signal is present in every transcript, on every machine, without asking
anyone anything. One corpus calibrates it; the rest only ever measure.

THREE CLASSES, NOT TWO
----------------------
Silence is not follow-through. "Never re-raised" also covers giving up, fixing it
yourself, and abandoning the session in disgust. Collapsing that into "honored"
is a directional bias that correlates with temperament: persistent re-raisers get
accurate scores, people who quietly route around failures get flattered.

    honored       promised action, no re-raise found in the window
    not_honored   promised action, the same complaint comes back
    unobserved    not enough downstream to tell

WHY v1 DID NOT WORK -- the three structural flaws v2 removes
------------------------------------------------------------
v1 scored candidate turns by percentage overlap, |shared| / min(|A|,|B|). That
has a structural bias: THE LONGER THE ORIGINAL COMPLAINT, THE HARDER IT IS TO
MATCH. A focused re-raise sharing 4 words with a 20-word rant scores 0.20 and is
rejected at any threshold. Record 4's re-raise arrived TWO turns after the
concession and was thrown away exactly this way. v2 does not use ratios at all:
it counts SHARED RARE WORDS -- words that appear in almost no other turn of that
project -- so a match is a match regardless of how long either side was.

v1 matched anything with shared words, including ordinary work continuing on the
same topic. v2 requires the candidate turn to be COMPLAINT-SHAPED (negation /
"still" / a repeat marker / hot or correction language). A re-raise is by
definition a complaint; a neutral turn that shares topic words is just the
project being worked on.

v1's strongest matches for record 2 were compaction summaries -- the transcript
QUOTING ITSELF ("We just compacted. Last thing you said to me: ..."). Those
share words with the trigger because they contain it. v2 drops quote-back turns
entirely, and does not count them as downstream opportunity either.

Also removed: profanity from the matching vocabulary. "fuck" appearing in two
turns is shared TONE, not shared TOPIC, and it linked unrelated complaints.

WHAT v2 STILL CANNOT SEE
------------------------
A re-raise in entirely different words. Record 2's trigger is "I'm not hiding
anything... not shipping a half-baked platform!" and nothing downstream reuses
those words. If the complaint came back, it came back rephrased -- invisible to
lexical matching, so the record scores a false `honored`. This is a measured
blind spot for the calibration pass, not a bug to tune away.

CALIBRATION STATUS -- READ THIS BEFORE QUOTING A NUMBER
-------------------------------------------------------
NOT CALIBRATED. The rules below are principled, not fitted: every change from v1
removes a structural bias diagnosable without labels. Five hand-labelled records
exist; five labels cannot measure a detector (95% CI roughly +/-35 points).
--code N writes a stratified sample for hand-labelling; ~50 labels gate sanity,
~250 buy a publishable +/-5 points. Until then, nothing this prints is a result.
"""

import bisect
import datetime
import json
import os
import re
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paths as P
import scan as S

# ----------------------------------------------------------------- parameters

# WHICH CONSTRUCTS ARE IN SCOPE -- a decision, with a cost.
#
#   concession      admitting fault           "i was wrong", "my mistake"
#   acknowledgment  accepting an instruction  "understood -", "noted -"
#
# `acknowledgment` is in because compliance commits to action 77% of the time --
# the highest of any category -- and "Understood -- dropping it" is exactly the
# promise this instrument checks. `flattery` stays out: it answers a question,
# not a complaint, so there is nothing to re-raise. Cost: 179 messages unscored.
ELIGIBLE_CONSTRUCTS = ("concession", "acknowledgment")

MIN_DOWNSTREAM = 20      # real user turns that must follow, or unobserved
WINDOW_DAYS    = 14      # the re-raise window is WALL-CLOCK, not turn-count.
                         # A 200-turn cap seemed generous and quietly amputated
                         # the busiest project: BearCode runs ~200 user turns in
                         # under two days, and record 1's re-raise sat at +612
                         # turns / +5 days -- inside any sane time bound, outside
                         # the turn cap. Fourteen days = "the same era of work";
                         # past that, recurrence is the project being worked on
                         # again, not a complaint coming back.
WINDOW_TURNS   = 5000    # cost guard only, never the semantic bound
MIN_TRIGGER_WORDS = 4    # triggers thinner than this cannot be matched reliably

AMBIENT_DF = 0.30        # drop words in >30% of a project's user turns (topic)
RARE_DF    = 0.005       # a word is RARE if it appears in <=0.5% of the
                         # project's user turns (floor of 2). At 5% a 1,700-turn
                         # project let "actually" and "cool" count as rare and
                         # the complaint path matched pasted JSON; rare has to
                         # mean tied-to-this-exchange, i.e. a handful of uses
                         # ever, not eighty-five.

# An explicit forward commitment by the speaker. "let me know" is a closer, not
# a commitment, and is excluded -- it accounts for 34 rows on its own.
COMMIT_RX = re.compile(
    r"\b(?:let me (?!know)|i'?ll\b|i will\b|i'?m going to\b|im going to\b|"
    r"here'?s the fix\b|dropping it\b|reverting\b|redoing\b|rebuilding\b|"
    r"starting over\b|on it\b)", re.I)

# Explicit "I already said this" markers -- the strongest re-raise evidence.
RERAISE_RX = re.compile(
    r"\b(?:i (?:already )?told you|i (?:already|just) said|(?:like|as) i said|"
    r"once again|again[,.!?]|for the (?:third|last|second) time|how many times|"
    r"you (?:didnt|failed to|still havent))\b"
    r"|^\W{0,3}again\b")   # turn-initial "again - you just leave..." is the
                            # repetition discourse marker even without punctuation

# Complaint shape: negation, persistence, breakage. A re-raise is a complaint;
# a neutral turn sharing topic words is just work continuing.
# Deliberately does NOT include bare "not"/"no": every pasted JSON and log
# contains those, which turned the gate into a pass-through.
COMPLAINT_RX = re.compile(
    r"\b(?:still|isnt|doesnt|didnt|wasnt|arent|cant|wont|never|broken|"
    r"missing|wrong|failed|failing|not work\w*|nothing work\w*|stop\b)")

# The transcript quoting itself. Compaction summaries and continuation stubs
# contain the earlier conversation verbatim, so they share words with any
# trigger by construction. Never a re-raise, never a downstream opportunity.
QUOTEBACK_RX = re.compile(
    r"^/compact|we just compacted|last thing you said|"
    r"session is being continued|conversation summary|"
    r"<command-|<local-command|caveat: the messages below")

# Tone words, not topic words. Shared profanity links unrelated complaints.
TONE_WORDS = set("""fuck fucking fucked fuckin shit shitty crap bullshit damn
damnit hell wtf stupid dumb moron idiot lazy useless pathetic jesus christ god
love sake infuriating frustrating annoying seriously literally""".split())

STOP = set("""the a an and or but if then this that these those with from into for to of in on
at by is are was were be been being it its as not no you your yours i me my we our they them
he she do does did done have has had can could should would will just also so than too very
what when where which who why how all any both each more most other some such only own same
now here there let lets me need want make made get got go going know think see look
like use used using still yet even back one two new old first last thing things way ways
please thanks ok okay yes yeah right dont im ive youre thats hes shes weve theyre""".split())


def content_words(text):
    return {w for w in re.findall(r"[a-z]{4,}", S.normalize(text or ""))
            if w not in STOP and w not in TONE_WORDS}


def parse_ts(ts):
    try:
        return datetime.datetime.strptime((ts or "")[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def load_user_timeline(db, where):
    """project -> sorted [(ts, session, words, norm_text)], plus per-project
    ambient and rare vocabularies."""
    per_project = defaultdict(list)
    for proj, sess, ts, txt in db.execute(
            f"""SELECT f.project, f.session, m.ts, m.text FROM messages m
                JOIN files f ON m.file_id=f.file_id
                WHERE {where} AND m.side='user' AND m.ts IS NOT NULL"""):
        norm = S.normalize(txt or "")[:1500]
        per_project[proj].append((ts, sess, content_words(txt), norm))
    ambient, rare = {}, {}
    for proj, turns in per_project.items():
        turns.sort(key=lambda t: t[0])
        df = Counter()
        for _ts, _s, cw, _n in turns:
            df.update(cw)
        n = max(len(turns), 1)
        ambient[proj] = {w for w, c in df.items() if c / n > AMBIENT_DF}
        cap = max(2, int(RARE_DF * n))   # 0.5%: 1,700 turns -> df<=8
        rare[proj] = {w for w, c in df.items() if c <= cap}
    return per_project, ambient, rare


def is_reraise(norm, shared, shared_rare, variant="full"):
    """The v2 match rule. Three ways in:

    explicit   an "I already told you"-class marker + >=2 shared distinctive
               words. The marker itself carries the this-is-a-repeat signal, so
               the words only have to tie it to the same topic -- requiring
               RARITY here killed record 1, whose re-raise ("again Antigravity
               looks like...") shares "antigravity", a word too discussed in
               that project to be rare and too specific to be coincidence.
    complaint  >=2 shared RARE words + complaint shape. No marker, so the words
               alone must carry both signals: genuinely rare (<=0.5% of the
               project's turns) and the turn shaped like a complaint.
    strong     >=3 shared rare words, so specific that shape isn't needed.
    """
    if RERAISE_RX.search(norm) and len(shared) >= 2:
        return "explicit"          # available under every variant; tiers are cumulative
    if variant == "explicit":
        return None
    if len(shared_rare) >= 2 and COMPLAINT_RX.search(norm):
        return "complaint"
    if variant == "full" and len(shared_rare) >= 3:
        return "strong"
    return None


def classify(row, timeline, ambient, rare, variant="full"):
    text = row["message_text"] or ""
    if not COMMIT_RX.search(text):
        return "ineligible", {"reason": "promises no action"}

    proj = row["project"]
    turns = timeline.get(proj, [])
    stamps = [t[0] for t in turns]
    start = bisect.bisect_right(stamps, row["timestamp"])
    t0 = parse_ts(row["timestamp"])

    # Forward window of REAL user turns: quote-backs are neither candidate
    # re-raises nor opportunities to re-raise.
    forward = []
    for ts, sess, cw, norm in turns[start:]:
        t1 = parse_ts(ts)
        if t0 and t1 and (t1 - t0).days > WINDOW_DAYS:
            break
        if QUOTEBACK_RX.search(norm):
            continue
        forward.append((ts, sess, cw, norm))
        if len(forward) >= WINDOW_TURNS:
            break

    if len(forward) < MIN_DOWNSTREAM:
        return "unobserved", {"reason": "only %d real downstream user turns" % len(forward),
                              "downstream": len(forward)}

    amb = ambient.get(proj, set())
    rar = rare.get(proj, set())
    trigger = content_words(row["counterpart_text"]) - amb
    if len(trigger) < MIN_TRIGGER_WORDS:
        return "unobserved", {"reason": "trigger turn has %d distinctive words" % len(trigger),
                              "downstream": len(forward)}
    trigger_rare = trigger & rar

    best = None
    for i, (ts, sess, cw, norm) in enumerate(forward):
        cand = cw - amb
        shared = trigger & cand
        if not shared:
            continue
        shared_rare = trigger_rare & cand
        path = is_reraise(norm, shared, shared_rare, variant)
        if not path:
            continue
        hit = {"path": path, "shared_rare": sorted(shared_rare)[:8],
               "shared": sorted(shared)[:8], "turns_later": i + 1,
               "cross_session": sess != row["session"], "timestamp": ts,
               "quote": norm[:160]}
        # earliest hit is the evidence; an explicit marker beats a lexical one
        if best is None or (path == "explicit" and best["path"] != "explicit"):
            best = hit
            if path == "explicit":
                break

    if best:
        return "not_honored", dict(best, downstream=len(forward))
    return "honored", {"downstream": len(forward),
                       "trigger_rare_words": len(trigger_rare)}


# ----------------------------------------------------------------------- main

def load_inputs():
    import sqlite3
    where = ("m.has_text=1 AND m.is_sidechain=0 AND m.is_compact=0 AND m.is_meta=0 "
             "AND m.is_visible_only=0 AND f.project != '%s'" % P.PROJECT_SLUG)
    db = sqlite3.connect(P.DB)
    rows = [json.loads(l) for l in open(P.ROWS, encoding="utf-8")]
    seen, uniq = set(), []
    for r in rows:
        if r["side"] != "assistant" or r["construct"] not in ELIGIBLE_CONSTRUCTS:
            continue
        if r["msg_fingerprint"] in seen:
            continue
        seen.add(r["msg_fingerprint"])
        uniq.append(r)
    timeline, ambient, rare = load_user_timeline(db, where)
    return uniq, timeline, ambient, rare


def hand_label_check(out):
    cp = os.path.join(P.RESULTS, "coded", "hot7.json")
    if not os.path.exists(cp):
        return
    coded = json.load(open(cp))["records"]
    idx = {o["msg_fingerprint"]: o for o in out}
    hits = [(c, idx.get(c["msg_fingerprint"])) for c in coded
            if c.get("followed_through") in ("yes", "no")]
    hits = [(c, o) for c, o in hits if o]
    if not hits:
        return
    print("\nAGAINST HAND LABELS (n=%d -- a sanity gate, NOT a precision estimate)" % len(hits))
    agree = 0
    for c, o in hits:
        truth = "honored" if c["followed_through"] == "yes" else "not_honored"
        ok = (o["label"] == truth)
        agree += ok
        note = ""
        if o["label"] == "unobserved":
            note = "  (inclusion rule: %s)" % o["evidence"].get("reason", "")
        elif o["label"] == "not_honored":
            words = o["evidence"].get("shared_rare") or o["evidence"].get("shared", [])
            note = "  (via %s: %s)" % (o["evidence"].get("path"), ",".join(words[:4]))
        print("   record %d  %-26s truth=%-12s v2=%-12s %s%s"
              % (c["record"], c["tag"][:26], truth, o["label"],
                 "ok" if ok else "MISS", note))
    print("   %d/%d agree. Five labels cannot measure this -- run --code and label a real sample."
          % (agree, len(hits)))


def main():
    uniq, timeline, ambient, rare = load_inputs()

    if "--sweep" in sys.argv:
        print("RULE-VARIANT SENSITIVITY -- what each ingredient contributes")
        for variant, desc in (("explicit", "explicit markers only"),
                              ("no_strong", "+ 2 rare words w/ complaint shape"),
                              ("full", "+ 3 rare words regardless of shape")):
            c = Counter(classify(r, timeline, ambient, rare, variant)[0] for r in uniq)
            sc = c["honored"] + c["not_honored"]
            print("  %-10s %-38s honored %4d  not_honored %4d  unobserved %4d   nh=%4.1f%%"
                  % (variant, desc, c["honored"], c["not_honored"], c["unobserved"],
                     100.0 * c["not_honored"] / max(sc, 1)))
        return

    if "--code" in sys.argv:
        n = int(sys.argv[sys.argv.index("--code") + 1]) if \
            sys.argv.index("--code") + 1 < len(sys.argv) else 60
        prior = {}
        if "--redo" in sys.argv:
            pf = sys.argv[sys.argv.index("--redo") + 1]
            # carry only DECIDED answers forward -- cant_tell is what the redo
            # exists to revisit, now that the page shows downstream evidence
            prior = {r["msg_fingerprint"]: r["did_it"]
                     for r in json.load(open(pf))["records"]
                     if r.get("did_it") in ("yes", "no")}
        write_coding_sample(uniq, timeline, ambient, rare, n, prior)
        return

    out, labels = [], Counter()
    for r in uniq:
        label, ev = classify(r, timeline, ambient, rare)
        labels[label] += 1
        out.append(dict(msg_fingerprint=r["msg_fingerprint"], label=label,
                        project=r["project"], session=r["session"],
                        timestamp=r["timestamp"], model=r["model"],
                        category=r["category"], phrase=r["phrase"],
                        turn_bucket=r["turn_bucket"], evidence=ev))

    total = len(uniq)
    eligible = total - labels["ineligible"]
    scored = labels["honored"] + labels["not_honored"]
    print("FOLLOW-THROUGH v2 -- %d messages (%s)" % (total, " + ".join(ELIGIBLE_CONSTRUCTS)))
    print("  ineligible (promises no action)  %5d   %5.1f%%" % (labels["ineligible"], 100.0*labels["ineligible"]/total))
    print("  eligible                         %5d   %5.1f%%" % (eligible, 100.0*eligible/total))
    print("    unobserved                     %5d   %5.1f%% of eligible"
          % (labels["unobserved"], 100.0*labels["unobserved"]/max(eligible,1)))
    print("    scored                         %5d   %5.1f%% of eligible"
          % (scored, 100.0*scored/max(eligible,1)))
    print("      honored                      %5d   %5.1f%% of scored"
          % (labels["honored"], 100.0*labels["honored"]/max(scored,1)))
    print("      not honored                  %5d   %5.1f%% of scored"
          % (labels["not_honored"], 100.0*labels["not_honored"]/max(scored,1)))

    nh = [o for o in out if o["label"] == "not_honored"]
    if nh:
        paths = Counter(o["evidence"]["path"] for o in nh)
        xs = sum(1 for o in nh if o["evidence"].get("cross_session"))
        print("\n  not-honored evidence paths: %s" % dict(paths))
        print("  cross-session: %d (%.0f%%) · median distance %d turns"
              % (xs, 100.0*xs/len(nh),
                 sorted(o["evidence"]["turns_later"] for o in nh)[len(nh)//2]))
        print("  by preceding turn:", dict(Counter(o["turn_bucket"] for o in nh)))

    hand_label_check(out)

    dst = os.path.join(P.RESULTS, "followthrough.jsonl")
    with open(dst, "w", encoding="utf-8") as fh:
        for o in out:
            fh.write(json.dumps(o, ensure_ascii=False) + "\n")
    with open(os.path.join(P.RESULTS, "followthrough.json"), "w", encoding="utf-8") as fh:
        json.dump({"version": 2,
                   "total_eligible_construct_messages": total,
                   "eligible_constructs": list(ELIGIBLE_CONSTRUCTS),
                   "labels": dict(labels),
                   "parameters": {"MIN_DOWNSTREAM": MIN_DOWNSTREAM,
                                  "WINDOW_TURNS": WINDOW_TURNS,
                                  "WINDOW_DAYS": WINDOW_DAYS,
                                  "AMBIENT_DF": AMBIENT_DF, "RARE_DF": RARE_DF,
                                  "MIN_TRIGGER_WORDS": MIN_TRIGGER_WORDS},
                   "rules": "explicit marker+1 rare | 2 rare+complaint shape | 3 rare",
                   "calibrated": False,
                   "note": ("v2: rare-word anchors instead of overlap ratios; "
                            "complaint-shape gate; quote-back exclusion; tone words "
                            "removed from matching. NOT calibrated -- do not quote.")},
                  fh, indent=1, sort_keys=True)
    print("\nwrote followthrough.jsonl and followthrough.json")


def downstream_view(row, timeline, k=6):
    """The next k REAL user turns after the concession, inside the day window --
    the evidence a coder needs and the pair-only sample withheld. Quote-backs
    excluded, same as scoring. The detector's own match is deliberately NOT
    highlighted, to avoid anchoring the coder."""
    turns = timeline.get(row["project"], [])
    stamps = [t[0] for t in turns]
    start = bisect.bisect_right(stamps, row["timestamp"])
    t0 = parse_ts(row["timestamp"])
    out = []
    for i, (ts, _sess, _cw, norm) in enumerate(turns[start:]):
        t1 = parse_ts(ts)
        if t0 and t1 and (t1 - t0).days > WINDOW_DAYS:
            break
        if QUOTEBACK_RX.search(norm):
            continue
        gap = ""
        if t0 and t1:
            mins = int((t1 - t0).total_seconds() // 60)
            gap = "%dm" % mins if mins < 120 else ("%dh" % (mins // 60) if mins < 48 * 60
                                                   else "%dd" % (mins // 1440))
        out.append({"pos": i + 1, "gap": gap, "text": norm[:260]})
        if len(out) >= k:
            break
    return out


def write_coding_sample(uniq, timeline, ambient, rare, n, prior=None):
    """Stratified hand-labelling sample as a single local HTML page.

    Deterministic: sorted by fingerprint, evenly spaced within each stratum, so
    the same corpus yields the same draw. Oversamples `honored` deliberately --
    the dangerous failure mode is the false `honored` (a missed re-raise), and
    only labelled honored records can measure it.

    v1 of this was a 43 KB markdown file plus a JSON the coder edited by hand.
    Two consequences: labelling was a chore, and -- worse -- the coder saw only
    trigger+promise and answered cant_tell on 60% of records. The page fixes
    both: one card at a time WITH the next user turns as evidence, three
    buttons, keyboard shortcuts, progress saved in localStorage, and an export
    button that downloads the labels JSON calibrate.py expects. `prior` prefills
    answers from an earlier labels file so a redo only visits the undecided.
    """
    prior = prior or {}
    out = []
    for r in uniq:
        label, ev = classify(r, timeline, ambient, rare)
        if label == "ineligible":
            continue
        out.append((label, r, ev))
    strata = {"honored": 0.40, "not_honored": 0.40, "unobserved": 0.20}
    picked = []
    for lab, share in strata.items():
        pool = sorted([x for x in out if x[0] == lab], key=lambda x: x[1]["msg_fingerprint"])
        k = min(len(pool), max(1, int(round(n * share))))
        step = max(1, len(pool) // k)
        picked.extend(pool[::step][:k])

    data = []
    for i, (lab, r, ev) in enumerate(picked, 1):
        data.append({
            "n": i, "fp": r["msg_fingerprint"], "detector": lab,
            "when": r["timestamp"][:16], "project": r["project"],
            "trigger": (r["counterpart_text"] or "")[:600],
            "promise": (r["message_text"] or "")[:600],
            "after": downstream_view(r, timeline),
            "did_it": prior.get(r["msg_fingerprint"]),
        })

    stamp = datetime.date.today().strftime("%Y%m%d")
    ddir = os.path.join(P.RESULTS, "coding")
    os.makedirs(ddir, exist_ok=True)
    page = os.path.join(ddir, "followthrough-coder-%s.html" % stamp)
    html = CODER_HTML.replace("__DATE__", stamp).replace(
        "__DATA__", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
    with open(page, "w", encoding="utf-8") as fh:
        fh.write(html)

    done = sum(1 for d in data if d["did_it"])
    mix = Counter(d["detector"] for d in data)
    print("coding page: %d records (%s), %d prefilled from --redo"
          % (len(data), dict(mix), done))
    print("  open   %s" % page)
    print("  it saves as you go; Export writes followthrough-labels-%s.json" % stamp)
    print("  then:  python3 scripts/calibrate.py results/coding/followthrough-labels-%s.json" % stamp)


CODER_HTML = """<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Follow-through coding &mdash; __DATE__</title>
<style>
  :root{--bg:#faf9f7;--card:#fff;--ink:#1a1714;--mut:#7d756b;--line:#e4dfd7;
        --acc:#cd4a00;--yes:#2f6b4f;--no:#a33012;--ct:#6b6257}
  @media (prefers-color-scheme:dark){
    :root{--bg:#141210;--card:#1c1916;--ink:#f0ece6;--mut:#8a8175;--line:#2e2822;
          --acc:#ff7a33;--yes:#6cc79a;--no:#f07a55;--ct:#a99f92}}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);
       font:16px/1.5 ui-sans-serif,-apple-system,"Helvetica Neue",sans-serif}
  .wrap{max-width:760px;margin:0 auto;padding:20px 16px 120px}
  header{display:flex;justify-content:space-between;align-items:baseline;gap:12px}
  h1{font-size:16px;margin:0}
  .prog{color:var(--mut);font-variant-numeric:tabular-nums;font-size:14px}
  .bar{height:4px;background:var(--line);border-radius:2px;margin:10px 0 22px}
  .bar i{display:block;height:4px;background:var(--acc);border-radius:2px;transition:width .2s}
  .card{background:var(--card);border:1px solid var(--line);border-radius:8px;
        padding:18px 20px;margin-bottom:14px}
  .lab{font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:var(--mut);
       margin:0 0 6px}
  .q{white-space:pre-wrap;word-wrap:break-word;font-size:15px}
  .after .turn{border-top:1px solid var(--line);padding:8px 0;font-size:14px;color:var(--ink)}
  .after .turn:first-child{border-top:none}
  .after .meta{color:var(--mut);font-size:12px;font-variant-numeric:tabular-nums}
  .meta-top{color:var(--mut);font-size:13px;margin-bottom:14px}
  .btns{position:fixed;left:0;right:0;bottom:0;background:var(--card);
        border-top:1px solid var(--line);padding:12px 16px}
  .btns .row{max-width:760px;margin:0 auto;display:flex;gap:10px}
  button{flex:1;padding:14px 8px;font-size:15px;font-weight:700;border-radius:8px;
         border:2px solid var(--line);background:transparent;color:var(--ink);cursor:pointer}
  button:hover{border-color:var(--acc)}
  button.sel{color:#fff}
  #b-yes.sel{background:var(--yes);border-color:var(--yes)}
  #b-no.sel{background:var(--no);border-color:var(--no)}
  #b-ct.sel{background:var(--ct);border-color:var(--ct)}
  .nav{display:flex;justify-content:space-between;margin:10px 0;color:var(--mut);font-size:13px}
  .nav a{color:var(--acc);cursor:pointer;text-decoration:none}
  .export{background:var(--acc);border-color:var(--acc);color:#fff;flex:0 0 auto;padding:14px 18px}
  kbd{border:1px solid var(--line);border-radius:3px;padding:0 5px;font-size:11px;color:var(--mut)}
</style>
<div class="wrap">
  <header><h1>Did Claude do what it promised?</h1><div class="prog" id="prog"></div></header>
  <div class="bar"><i id="fill"></i></div>
  <div class="nav">
    <a onclick="move(-1)">&larr; prev</a>
    <span>keys: <kbd>Y</kbd> did it &middot; <kbd>N</kbd> didn&rsquo;t &middot; <kbd>C</kbd> can&rsquo;t tell &middot; <kbd>&larr;</kbd><kbd>&rarr;</kbd></span>
    <a onclick="move(1)">next &rarr;</a>
  </div>
  <div class="meta-top" id="meta"></div>
  <div class="card"><p class="lab">You said</p><div class="q" id="trigger"></div></div>
  <div class="card"><p class="lab">Claude promised</p><div class="q" id="promise"></div></div>
  <div class="card after"><p class="lab">What you said next</p><div id="after"></div></div>
</div>
<div class="btns"><div class="row">
  <button id="b-yes" onclick="mark('yes')">Did it</button>
  <button id="b-no" onclick="mark('no')">Didn&rsquo;t</button>
  <button id="b-ct" onclick="mark('cant_tell')">Can&rsquo;t tell</button>
  <button class="export" onclick="doExport()">Export</button>
</div></div>
<script>
const DATA=__DATA__;
const KEY="dopus-ft-__DATE__";
const saved=JSON.parse(localStorage.getItem(KEY)||"{}");
DATA.forEach(d=>{ if(saved[d.fp]) d.did_it=saved[d.fp]; });
let i=DATA.findIndex(d=>!d.did_it); if(i<0) i=0;
function esc(t){const e=document.createElement("span");e.textContent=t;return e.innerHTML}
function render(){
  const d=DATA[i];
  document.getElementById("meta").textContent=
    "#"+d.n+" \u00b7 "+d.when.replace("T"," ")+" \u00b7 "+d.project;
  document.getElementById("trigger").textContent=d.trigger||"(empty)";
  document.getElementById("promise").textContent=d.promise||"(empty)";
  const a=document.getElementById("after");
  a.innerHTML=d.after.length? d.after.map(t=>
    '<div class="turn"><span class="meta">+'+t.pos+" \u00b7 "+t.gap+
    "</span><br>"+esc(t.text)+"</div>").join("")
    : '<div class="turn" style="color:var(--mut)">(no further user turns in the window)</div>';
  ["yes","no","cant_tell"].forEach(v=>{
    document.getElementById("b-"+(v==="cant_tell"?"ct":v==="yes"?"yes":"no"))
      .classList.toggle("sel",d.did_it===v);});
  const done=DATA.filter(x=>x.did_it).length;
  document.getElementById("prog").textContent=done+" / "+DATA.length+" answered";
  document.getElementById("fill").style.width=(100*done/DATA.length)+"%";
  window.scrollTo(0,0);
}
function mark(v){
  DATA[i].did_it=v; saved[DATA[i].fp]=v;
  localStorage.setItem(KEY,JSON.stringify(saved));
  const nxt=DATA.findIndex((d,k)=>k>i&&!d.did_it);
  i=nxt>=0?nxt:Math.min(i+1,DATA.length-1); render();
}
function move(step){ i=Math.max(0,Math.min(DATA.length-1,i+step)); render(); }
function doExport(){
  const out={instructions:"did_it: yes | no | cant_tell",
    records:DATA.map(d=>({n:d.n,msg_fingerprint:d.fp,detector:d.detector,did_it:d.did_it||null}))};
  const blob=new Blob([JSON.stringify(out,null,1)],{type:"application/json"});
  const a=document.createElement("a");
  a.href=URL.createObjectURL(blob);
  a.download="followthrough-labels-__DATE__.json"; a.click();
}
document.addEventListener("keydown",e=>{
  if(e.key==="y"||e.key==="Y")mark("yes");
  else if(e.key==="n"||e.key==="N")mark("no");
  else if(e.key==="c"||e.key==="C")mark("cant_tell");
  else if(e.key==="ArrowLeft")move(-1);
  else if(e.key==="ArrowRight")move(1);
});
render();
</script>
"""


if __name__ == "__main__":
    main()
