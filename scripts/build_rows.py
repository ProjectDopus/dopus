#!/usr/bin/env python3
"""
Generate the row-level dataset from the frozen DB. No SSH, no per-machine files.

Usage:  python3 scripts/build_rows.py [history.sqlite] [out.jsonl]

Supersedes rows.py + merge_raw.py for dataset generation. rows.py walked a
transcript directory and stamped every row with the LOCAL machine_id, so
pointing it at archive/ would label all 3,887 files as this laptop. Here machine
and root come from the files table, where they were recorded at ingest.

One command regenerates the whole dataset after any phrases.json change --
which is the entire reason the archive exists.
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
import swear as W

MAX_TEXT = 4000          # raised from 1200: 43% of rows were truncated, and the
MAX_COUNTERPART = 4000   # counterpart is what a human coder must judge.

# The default analysis population. Every exclusion is visible here rather than
# buried in a scanner: no tool-only turns, no subagent sidechains, no compaction
# replays, and not this research project (which quotes every target phrase).
WHERE = ("m.has_text=1 AND m.is_sidechain=0 AND m.is_compact=0 AND m.is_meta=0 "
         "AND m.is_visible_only=0 AND f.project != '-Users-zach-GitHub-Dopus'")


# Explicit "I already said this" markers. Cheap, high-precision signal that the
# user is repeating rather than informing.
REPEAT_RX = [
    ("i told you",      re.compile(r"\bi (?:already )?told you\b")),
    ("i already said",  re.compile(r"\bi (?:already|just) said\b")),
    ("like i said",     re.compile(r"\b(?:like|as) i said\b")),
    ("again",           re.compile(r"\b(?:once again|again[,.]|for the (?:third|last|second) time)\b")),
    ("how many times",  re.compile(r"\bhow many times\b")),
    ("i asked for",     re.compile(r"\bi (?:asked|said) (?:for|to)\b")),
    ("you didn't",      re.compile(r"\byou (?:didnt|failed to|still havent)\b")),
    ("i keep",          re.compile(r"\bi keep (?:telling|saying|asking)\b")),
]

# "I never asked for this" is a different failure from "I told you twice":
# unauthorized action vs ignored instruction. Rare in the archive (4 hits) but
# appearing live, so it gets its own axis rather than being folded into repeats.
UNAUTH_RX = [
    ("i never asked",    re.compile(r"\bi never (?:once )?(?:asked|told|said|gave|wanted|requested)\b")),
    ("you never",        re.compile(r"\byou never (?:asked|confirmed|checked|told|said|gave|even)\b")),
    ("without asking",   re.compile(r"\bwithout (?:asking|being asked|my ok|permission|checking)\b")),
    ("i didnt ask",      re.compile(r"\bi (?:didnt|did not) ask\b")),
    ("who told you",     re.compile(r"\bwho told you\b")),
    ("i never once",     re.compile(r"\bi never once\b")),
]

STOP = set("""the a an and or but if then this that these those with from into for to of in on
at by is are was were be been being it its as not no you your yours i me my we our they them
he she do does did done have has had can could should would will just also so than too very
what when where which who why how all any both each more most other some such only own same
now here there let lets me""".split())


def content_words(text):
    """Content-word set for similarity. Short/stop words carry no topic signal."""
    return {w for w in re.findall(r"[a-z]{4,}", S.normalize(text)) if w not in STOP}


def repeat_signal(text, prior_sets):
    """(markers, max Jaccard vs earlier user turns in this session).

    A concession after a REPEATED instruction is a different failure from a
    concession after new evidence -- the phrase match is identical either way.
    This gives a coder a prior; the human field decides.
    """
    norm = S.normalize(text)
    markers = [name for name, rx in REPEAT_RX if rx.search(norm)]
    unauth = [name for name, rx in UNAUTH_RX if rx.search(norm)]
    cw = content_words(text)
    best = 0.0
    if cw:
        for prev in prior_sets:
            if not prev:
                continue
            inter = len(cw & prev)
            if inter:
                j = inter / float(len(cw | prev))
                if j > best:
                    best = j
    return markers, unauth, round(best, 3)


def classify_user(raw):
    hits, directed, shouts, _caps = W.analyze(raw)
    if hits or shouts:
        return "hot", [w for _c, w in hits] + list(shouts), directed
    if W.CORRECTION_RX.search(S.normalize(raw)):
        return "correction", [], False
    return "neutral", [], False


def main():
    dbp = sys.argv[1] if len(sys.argv) > 1 else P.DB
    out = sys.argv[2] if len(sys.argv) > 2 else P.ROWS
    pf = P.PHRASES

    CONSTRUCT = {}
    for c, cats in json.load(open(pf)).get("_constructs", {}).items():
        if not c.startswith("_"):
            for cat in cats:
                CONSTRUCT[cat] = c

    pats = S.load_patterns(pf)
    aop = S.load_assistant_openers(pf)
    guard = S.load_negation_guard(pf)
    rgx = S.load_patterns_regex(pf)

    db = sqlite3.connect(dbp)
    rows = []
    seen_fp = set()

    files = db.execute(
        f"""SELECT DISTINCT f.file_id, f.machine_id, f.root_slug, f.project, f.session
            FROM files f JOIN messages m ON m.file_id=f.file_id
            WHERE {WHERE} ORDER BY f.file_id""").fetchall()

    for file_id, machine_id, root_slug, project, session in files:
        msgs = db.execute(
            f"""SELECT m.side, m.text, m.ts, m.model, m.fingerprint
                FROM messages m JOIN files f ON m.file_id=f.file_id
                WHERE m.file_id=? AND {WHERE} ORDER BY m.line_no""", (file_id,)).fetchall()

        prev_user = None
        depth = 0
        prior_sets = []          # content-word sets of earlier user turns, this session
        for i, (side, text, ts, model, fp) in enumerate(msgs):
            if fp and fp in seen_fp:
                continue
            if fp:
                seen_fp.add(fp)
            flat = text or ""
            norm = S.normalize(flat)

            base = {
                "machine_id": machine_id, "root_slug": root_slug,
                "project": project, "session": session,
                "timestamp": ts or "", "model": model or "",
                "msg_fingerprint": fp,
            }
            _con = CONSTRUCT

            if side == "user":
                bucket, trigger, directed = classify_user(flat)
                markers, unauth, sim = repeat_signal(flat, prior_sets)
                prior_sets.append(content_words(flat))
                prev_user = (flat, bucket, trigger, directed, markers, sim, unauth)
                depth = 0
                nxt = ""
                for j in range(i + 1, len(msgs)):
                    if msgs[j][0] == "assistant":
                        nxt = msgs[j][1] or ""
                        break
                # W.user_hits applies the guards; iterating W.PATTERNS here is
                # what let "lazy" (loading="lazy") into the dataset.
                hits = list(W.user_hits(norm))
                # Position rule: a bare "STOP." on its own is a shout; the same
                # token inside pasted log output is not.
                for tok, tpos in W.find_shouts(flat):
                    hits.append((tok, "shouting", tpos))
                for phrase, cat, pos in hits:
                    rows.append(dict(base, side="user", category=cat,
                                     construct="frustration", phrase=phrase,
                                     char_offset=pos, snippet=flat[max(0, pos-40):pos+200],
                                     message_text=flat[:MAX_TEXT],
                                     message_truncated=len(flat) > MAX_TEXT,
                                     counterpart_side="assistant",
                                     counterpart_text=nxt[:MAX_COUNTERPART],
                                     turn_bucket=bucket, trigger_words=trigger,
                                     directed_at_claude=directed, depth_in_turn=0,
                                     repeat_markers=markers,
                                     unauthorized_markers=unauth,
                                     prior_turn_similarity=sim,
                                     user_repeated_prior_instruction=None,
                                     user_says_action_unauthorized=None))
                continue

            depth += 1
            pu = prev_user or ("", None, [], False, [], 0.0, [])
            for phrase, cat, pos in S.all_matches(norm, pats["assistant"],
                                                  aop[0], aop[1], guard, rgx):
                rows.append(dict(base, side="assistant", category=cat,
                                 construct=_con.get(cat, "other"), phrase=phrase,
                                 char_offset=pos, snippet=flat[max(0, pos-40):pos+200],
                                 message_text=flat[:MAX_TEXT],
                                 message_truncated=len(flat) > MAX_TEXT,
                                 counterpart_side="user",
                                 counterpart_text=pu[0][:MAX_COUNTERPART],
                                 turn_bucket=pu[1], trigger_words=pu[2],
                                 directed_at_claude=pu[3], depth_in_turn=depth,
                                 repeat_markers=pu[4],
                                 unauthorized_markers=pu[6],
                                 prior_turn_similarity=pu[5],
                                 user_repeated_prior_instruction=None,
                                 user_says_action_unauthorized=None))

    # Denominators, so the file supports RATES and not just composition.
    denom = {}
    for side in ("assistant", "user"):
        for mid, n in db.execute(
                f"""SELECT f.machine_id, COUNT(DISTINCT m.fingerprint)
                    FROM messages m JOIN files f ON m.file_id=f.file_id
                    WHERE {WHERE} AND m.side=? GROUP BY f.machine_id""", (side,)):
            denom.setdefault(mid, {})[side] = n

    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    dpath = os.path.join(os.path.dirname(out), "denominators.json")
    with open(dpath, "w", encoding="utf-8") as fh:
        json.dump({"per_machine": denom,
                   "totals": {s: sum(v.get(s, 0) for v in denom.values())
                              for s in ("assistant", "user")},
                   "where_clause": WHERE}, fh, indent=1, sort_keys=True)

    sides = Counter(r["side"] for r in rows)
    trunc = sum(1 for r in rows if r["message_truncated"])
    print("rows            %d  -> %s" % (len(rows), os.path.basename(out)))
    print("  assistant     %d" % sides["assistant"])
    print("  user          %d" % sides["user"])
    print("machines        %d" % len(set(r["machine_id"] for r in rows)))
    print("truncated       %d  (%.1f%%)" % (trunc, 100.0 * trunc / max(len(rows), 1)))
    print("denominators    %s" % os.path.basename(dpath))


if __name__ == "__main__":
    main()
