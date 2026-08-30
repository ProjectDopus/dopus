#!/usr/bin/env python3
"""
Scan Claude Code session transcripts and count apology / agreement phrases.

Usage:  python3 scripts/scan.py [transcript_dir] [phrases.json]
Writes: results/apology-<machine_id>.json  (next to this script)

Python 3.6+, standard library only. Read-only on the transcripts.
"""

import datetime
import hashlib
import json
import os
import re
import socket
import sys
from collections import Counter, defaultdict


def machine_id():
    """Stable per-machine id.

    hostname is NOT stable -- a laptop reports one name on a home network and a
    DHCP-assigned name on campus wifi, which would make one machine look like
    two in the merge. Use the hardware UUID where we can get it.
    """
    try:
        import subprocess
        if sys.platform == "darwin":
            out = subprocess.run(
                ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                capture_output=True, text=True, timeout=10).stdout
            m = re.search(r'"IOPlatformUUID"\s*=\s*"([^"]+)"', out)
            if m:
                return hashlib.sha1(m.group(1).encode()).hexdigest()[:8]
        for p in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
            if os.path.exists(p):
                with open(p) as fh:
                    v = fh.read().strip()
                if v:
                    return hashlib.sha1(v.encode()).hexdigest()[:8]
    except Exception:
        pass
    return hashlib.sha1(socket.gethostname().encode()).hexdigest()[:8]


def fingerprint(uuid):
    """Short stable hash of a message uuid.

    Emitted so the merge step can dedup ACROSS machines AND across weekly runs --
    Migration Assistant, synced home dirs, and repeated scans all mean the same
    message can arrive many times, and counting it twice inflates the leaderboard.

    16 hex chars (64 bits), not 10. At 10 a million-message corpus has a ~45%
    chance of at least one collision, and a collision silently DISCARDS a real
    message as a duplicate -- undercounting with no error and no symptom.
    merge.py truncates to the shortest width present, so files written at the
    old width still reconcile against new ones.
    """
    return hashlib.sha1(uuid.encode("utf-8")).hexdigest()[:16]

DEFAULT_DIR = os.path.expanduser("~/.claude/projects")

_HERE = os.path.dirname(os.path.abspath(__file__))

# scan.py resolves its own paths and never imports scripts/paths.py, because
# sync.py scps THIS FILE to /tmp on remote hosts where no repository exists
# around it. One rule covers both cases: if the parent directory holds
# phrases.json we are inside the repo (running from scripts/), so data lives one
# level up; otherwise this is a loose file and data lives beside it.
_ROOT = (os.path.dirname(_HERE)
         if os.path.exists(os.path.join(os.path.dirname(_HERE), "phrases.json"))
         else _HERE)

# Anchored to the resolved root, not the working directory, so
# `scp scan.py phrases.json host:/tmp/ && ssh host 'python3 /tmp/scan.py'` drops
# results in /tmp/results/ no matter where it was invoked from -- one
# predictable path to scp back off each of the machines.
OUTDIR = os.path.join(_ROOT, "results")
PHRASE_FILE = os.path.join(_ROOT, "phrases.json")


def outpath(name):
    """Absolute path inside results/, creating the directory on first use."""
    if not os.path.isdir(OUTDIR):
        os.makedirs(OUTDIR)
    return os.path.join(OUTDIR, name)


def file_tag(root):
    """Output filename discriminator: machine id, plus the root if non-default.

    EVERY script must use this. raw.py once omitted the root part and silently
    overwrote a completed 41-row scan with a 0-row one when a second directory
    was scanned on the same box.

    The suffix names the meaningful directory, not the literal basename -- every
    transcript path ends in ".../.claude/projects", so a basename suffix would
    label two different home directories on one host identically as "-projects".
    """
    tag = machine_id()
    if os.environ.get("APOLOGY_INCLUDE_META") == "1":
        tag += "-META"
    # Date-stamped so a weekly run never overwrites an earlier one. That matters
    # because retention (cleanupPeriodDays) eventually deletes old sessions: once
    # it does, the ONLY copy of that history is in a previous run's output.
    # Override with APOLOGY_RUN_DATE to re-create a specific run.
    tag += "-" + (os.environ.get("APOLOGY_RUN_DATE")
                  or datetime.date.today().strftime("%Y%m%d"))
    if os.path.abspath(root) == os.path.abspath(DEFAULT_DIR):
        return tag
    parts = [p for p in os.path.abspath(root).split(os.sep)
             if p and p not in (".claude", "projects")]
    return "%s-%s" % (tag, re.sub(r"[^A-Za-z0-9_.-]", "_", parts[-1] if parts else "alt"))

# Transcripts of THIS research project quote every target phrase in their own
# prompts, so they match themselves. Excluded at scan time, where the project
# name is actually known -- trying to subtract them later is impossible because
# phrase counts are not broken down per project. The slug is derived from this
# file's location (scan.py must stay import-free of paths.py -- sync.py ships
# it to /tmp on remote hosts, where the value is meaningless and unused).
EXCLUDE_PROJECTS = {re.sub(r"[^A-Za-z0-9]", "-",
                           os.path.dirname(os.path.dirname(os.path.abspath(__file__))))}

# Deliberate escape hatch: APOLOGY_INCLUDE_META=1 scores the meta-session too.
# Only ever for measuring the observer effect as a LABELED control -- the output
# is tagged "-META" so a merged leaderboard can never absorb it by accident.
if os.environ.get("APOLOGY_INCLUDE_META") == "1":
    EXCLUDE_PROJECTS = set()

# ---------------------------------------------------------------- normalizing

SMART = {
    "’": "'", "‘": "'",      # curly single quotes
    "“": '"', "”": '"',      # curly double quotes
    "—": "-", "–": "-",      # em / en dash
    " ": " ",                     # nbsp
}


def normalize(text):
    """Lowercase, straighten smart quotes, collapse whitespace.

    The apostrophe swap matters more than it looks: "You're" is written with
    U+2019 often enough that an ASCII-only pattern silently misses a chunk of
    every 'you're ...' phrase.
    """
    for bad, good in SMART.items():
        text = text.replace(bad, good)
    # Drop apostrophes entirely rather than just straightening them. The user types
    # "didnt" / "doesnt" / "isnt", and requiring the apostrophe silently scored
    # real corrections as neutral. Patterns get the same treatment so both
    # spellings collapse to one token. "youre" stays distinct from "your".
    text = text.replace("'", "")
    return re.sub(r"\s+", " ", text.lower())


def compile_phrase(phrase):
    """Whole-phrase match, flexible whitespace, not inside a longer word."""
    body = re.escape(normalize(phrase)).replace("\\ ", r"\s+")
    return re.compile(r"(?<![a-z0-9])" + body + r"(?![a-z0-9'])")


def load_openers(phrase_file):
    """Pushback openers, anchored to the START of a user message.

    Matched anywhere in the message these are useless -- "stop" is almost always
    "stop the server", "read the" is "read the docs". Anchoring drops the count
    from 1622 to 73 and turns a vocabulary measurement into a real one.
    """
    with open(phrase_file, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    words = raw.get("user_pushback_openers", [])
    if not words:
        return None
    body = "|".join(re.escape(normalize(w)) for w in sorted(words, key=len, reverse=True))
    return re.compile(r"^\W{0,3}(?:" + body + r")(?![a-z0-9'])")


def load_patterns(phrase_file):
    with open(phrase_file, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    out = {}
    for side in ("assistant", "user"):
        pats = []
        for category, phrases in raw.get(side, {}).items():
            if category.startswith("_"):
                continue
            for phrase in phrases:
                pats.append((phrase, category, compile_phrase(phrase)))
        # Longest phrase first so "that's my fault" wins over "my fault".
        pats.sort(key=lambda p: -len(p[0]))
        out[side] = pats
    return out


def load_assistant_openers(phrase_file):
    """Sentence-initial concession openers -> (regex, word->category).

    "Right - he's talking, so the tail should come off the left" is a concession.
    "from the top right - the badge" is not. Only position separates them, which a
    flat phrase list cannot express: as a free phrase "right -" scored 83 hits,
    roughly half of them false.
    """
    with open(phrase_file, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    spec = raw.get("assistant_openers", {})
    cat = {}
    for category, words in spec.items():
        if category.startswith("_"):
            continue
        for w in words:
            cat[normalize(w)] = category
    if not cat:
        return None, {}
    body = "|".join(re.escape(w) for w in sorted(cat, key=len, reverse=True))
    # Sentence start = string start or after terminal punctuation.
    # Terminator widened to [-,.:] so "Exactly right." and "Right: ..." count,
    # not only the dash form.
    rx = re.compile(r"(?:^|[.!?]\s+)(" + body + r")\s*[-,.:]")
    return rx, cat


def find_openers(text, rx, cat):
    """[(phrase, category, pos)] for sentence-initial openers."""
    if rx is None:
        return []
    return [(m.group(1) + " -", cat[m.group(1)], m.start(1))
            for m in rx.finditer(text)]


def load_patterns_regex(phrase_file):
    """Regex families -> [(name, category, compiled)].

    A flat phrase list always trails a productive construction: "fair callout"
    was said for the first time tonight and appears zero times in the archive.
    Matching the family catches the next variant without waiting for someone to
    spot it.
    """
    with open(phrase_file, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    out = []
    for category, fams in raw.get("assistant_patterns", {}).items():
        if category.startswith("_") or not isinstance(fams, dict):
            continue
        for name, pat in fams.items():
            out.append((name, category, re.compile(pat)))
    return out


def load_negation_guard(phrase_file):
    """phrase -> compiled regex whose presence nearby voids the match.

    Needed because some admissions have an exact denial twin: "a real bug i
    introduced" versus "i introduced neither". Counting the second as an
    admission of fault is worse than missing the first.
    """
    with open(phrase_file, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    out = {}
    for phrase, words in raw.get("assistant_negation_guard", {}).items():
        if phrase.startswith("_") or not isinstance(words, list):
            continue
        out[normalize(phrase)] = re.compile("|".join(re.escape(normalize(w)) for w in words))
    return out


def all_matches(text, patterns, opener_rx=None, opener_cat=None, guard=None,
                regexes=None):
    """Phrase matches plus opener matches, with overlaps resolved.

    Both scan.py and raw.py must call THIS, not find_matches -- if they disagree
    about what counts, the row-level dataset stops reconciling with the aggregate
    and the integrity check that has caught two real bugs stops working.
    """
    hits = find_matches(text, patterns)
    if guard:
        kept = []
        for ph, c, pos in hits:
            g = guard.get(normalize(ph))
            if g and g.search(text[max(0, pos - 80):pos + 80]):
                continue          # denial twin, not an admission
            kept.append((ph, c, pos))
        hits = kept
    claimed0 = [(p, p + len(ph)) for ph, _c, p in hits]
    for _name, cat, rx in (regexes or []):
        for m in rx.finditer(text):
            if any(m.start() < ce and cs < m.end() for cs, ce in claimed0):
                continue
            hits.append((m.group(0).strip(), cat, m.start()))
    if opener_rx is None:
        return hits
    claimed = [(p, p + len(ph)) for ph, _c, p in hits]
    for ph, c, pos in find_openers(text, opener_rx, opener_cat):
        end = pos + len(ph)
        # "Exactly right -" must count once, as the longer phrase.
        if any(pos < ce and cs < end for cs, ce in claimed):
            continue
        hits.append((ph, c, pos))
    return hits


def find_matches(text, patterns):
    """Non-overlapping, longest-wins. Returns [(phrase, category, start)]."""
    claimed = []
    hits = []
    for phrase, category, rx in patterns:
        for m in rx.finditer(text):
            span = (m.start(), m.end())
            if any(span[0] < c[1] and c[0] < span[1] for c in claimed):
                continue
            claimed.append(span)
            hits.append((phrase, category, m.start()))
    return hits


# ------------------------------------------------------------- text extraction

# Injected wrappers that ride along inside otherwise-genuine messages.
# Strip the wrapper, keep whatever the human or Claude actually wrote around it.
WRAPPER_RX = re.compile(
    r"<(system-reminder|command-name|command-message|command-args"
    r"|local-command-stdout|local-command-stderr|user-prompt-submit-hook"
    r"|task-notification)>"
    r".*?</\1>",
    re.S,
)
# Self-closing / unterminated variants.
STRAY_RX = re.compile(r"</?(system-reminder|command-[a-z]+|local-command-[a-z]+"
                      r"|task-notification|task-id|tool-use-id|output-file)>")

# task-notification was missing here until 2026-08-11 and it is the expensive
# one. Background-task completions are injected as USER messages, so 2,239 of
# 7,039 user turns in the population (31.8%) were machine text counted as
# something the human typed. They are pure markup -- stripping leaves zero prose
# in all 2,239 -- so they now fall out of the population entirely via has_text=0.
#
# The cost of NOT stripping them was not in the phrase counts (one false
# `DO NOT` shout). It was in the turn classification: a notification landing
# between a real correction and Claude's reply reset the preceding-turn bucket
# to `neutral`, moving assistant messages out of the `correction` denominator
# and inflating the headline gap between anger and plain correction from 2.0x
# to 3.2x.


def is_synthetic(entry):
    """True for entries the human did not type and Claude did not say.

    Compaction summaries are the dangerous one: they are injected as *user*
    messages and they QUOTE the earlier conversation, so anything counted in
    them is counted twice -- once when said, once when summarized.
    """
    return bool(
        entry.get("isMeta")
        or entry.get("isSidechain")
        or entry.get("isCompactSummary")
        or entry.get("isVisibleInTranscriptOnly")
    )


COMMAND_RX = re.compile(r"<command-name>\s*([^<]+?)\s*</command-name>")


def command_name(entry):
    """The slash-command / skill a user entry invoked, or None. Read from the
    raw content before message_text() strips the wrapper -- the invocation is
    machine text and rightly leaves the analysis population, but WHICH command
    ran is a fact worth keeping (does concession differ inside skill runs?)."""
    msg = entry.get("message")
    if not isinstance(msg, dict):
        return None
    content = msg.get("content")
    if isinstance(content, str):
        parts = [content]
    elif isinstance(content, list):
        parts = [b.get("text", "") for b in content
                 if isinstance(b, dict) and b.get("type") == "text"]
    else:
        return None
    m = COMMAND_RX.search("\n".join(p for p in parts if p))
    return m.group(1).lower().lstrip("/") if m else None


def message_text(entry):
    """Pull only genuine prose out of a transcript entry.

    Content is either a bare string or a list of typed blocks. We keep 'text'
    blocks only -- never tool_use inputs (that is code and file contents Claude
    *read*, not words Claude *said*) and never tool_result payloads on the user
    side (command output, not something the human typed).
    """
    msg = entry.get("message")
    if not isinstance(msg, dict):
        return ""
    content = msg.get("content")

    if isinstance(content, str):
        parts = [content]
    elif isinstance(content, list):
        parts = [
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        ]
    else:
        return ""

    text = "\n".join(p for p in parts if p)
    text = WRAPPER_RX.sub(" ", text)
    text = STRAY_RX.sub(" ", text)
    return text


# Recall audit. A deliberately over-broad net: any sentence that smells like an
# admission or a concession. Sentences caught here that matched NO phrase are
# exactly the phrases missing from phrases.json.
#
# The earlier version of this audit only looked at a message's opening sentence,
# which is useless -- concessions land mid-message, after the actual answer.
NEAR_MISS_RX = re.compile(
    r"\byou\s+(?:were|are|re|had|got|did)\b"
    r"|\byou\s+caught\b|\byou\s+called\b"
    r"|\bi\s+(?:was|were|had|should|shouldnt|didnt|failed|missed|misread"
    r"|misunderstood|assumed|overcomplicat\w*|screwed|broke|jumped)\b"
    r"|\bmy\s+(?:bad|fault|mistake|error|apolog\w*)\b"
    r"|\b(?:sorry|apolog\w+|oversight|regress\w+)\b"
    r"|\bthats?\s+(?:on me|my)\b"
    r"|\bgood\s+(?:catch|point|call)\b"
    r"|\bfair\s+(?:point|enough)\b"
)
SENT_SPLIT = re.compile(r"[.!?\n]+")


def near_misses(text):
    """Sentences that look like concessions but matched no configured phrase."""
    out = []
    for sent in SENT_SPLIT.split(normalize(text)):
        sent = sent.strip(" -*#`>")
        if 8 <= len(sent) <= 200 and NEAR_MISS_RX.search(sent):
            out.append(sent[:70])
    return out


# --------------------------------------------------------------------- scanner

def scan(root, patterns, openers=None, aopen=(None, {}), guard=None, regexes=None):
    seen_uuids = set()          # migration-assistant copies duplicate sessions
    counts = defaultdict(Counter)          # side -> phrase -> n
    by_category = defaultdict(Counter)     # side -> category -> n
    by_month = defaultdict(Counter)        # side -> YYYY-MM -> n
    by_model = Counter()                   # model -> n (assistant hits)
    by_project = Counter()                 # project dir -> n (assistant hits)
    msgs_by_month = defaultdict(Counter)   # side -> YYYY-MM -> messages
    msgs_by_model = Counter()
    msgs_by_project = Counter()
    unmatched = Counter()
    pushback = Counter()
    counted_fp = defaultdict(list)   # side -> fingerprints of counted messages
    hit_fp = []                      # fingerprints of messages containing a hit
    examples = defaultdict(list)
    totals = Counter()
    bad_lines = 0

    files = []
    for dirpath, _dirs, names in os.walk(root):
        for name in names:
            if name.endswith(".jsonl"):
                files.append(os.path.join(dirpath, name))

    for path in files:
        project = os.path.basename(os.path.dirname(path))
        if project in EXCLUDE_PROJECTS:
            totals["files_excluded_meta"] += 1
            continue
        totals["files"] += 1
        try:
            fh = open(path, "r", encoding="utf-8", errors="replace")
        except OSError:
            totals["files_unreadable"] += 1
            continue

        with fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except ValueError:
                    bad_lines += 1
                    continue
                if not isinstance(entry, dict):
                    continue

                side = entry.get("type")
                if side not in ("assistant", "user"):
                    continue
                if is_synthetic(entry):
                    totals["skipped_synthetic"] += 1
                    continue

                uid = entry.get("uuid")
                if uid:
                    if uid in seen_uuids:
                        totals["duplicate_messages"] += 1
                        continue
                    seen_uuids.add(uid)

                text = message_text(entry)
                if not text.strip():
                    continue

                stamp = entry.get("timestamp") or ""
                month = stamp[:7] if len(stamp) >= 7 else "unknown"
                model = (entry.get("message") or {}).get("model") or "unknown"

                totals[side + "_messages"] += 1
                totals[side + "_chars"] += len(text)
                if uid:
                    counted_fp[side].append(fingerprint(uid))
                msgs_by_month[side][month] += 1
                if side == "assistant":
                    msgs_by_model[model] += 1
                    msgs_by_project[project] += 1

                if side == "user" and openers is not None:
                    if openers.match(normalize(text).strip()):
                        pushback["opening"] += 1
                        pushback[month] += 1

                nt = normalize(text)
                hits = (all_matches(nt, patterns[side], aopen[0], aopen[1], guard, regexes)
                        if side == "assistant" else find_matches(nt, patterns[side]))
                if hits:
                    totals[side + "_messages_with_hit"] += 1
                    if uid and side == "assistant":
                        hit_fp.append(fingerprint(uid))
                    for phrase, category, pos in hits:
                        counts[side][phrase] += 1
                        by_category[side][category] += 1
                        by_month[side][month] += 1
                        if side == "assistant":
                            by_model[model] += 1
                            by_project[project] += 1
                        if len(examples[phrase]) < 3:
                            raw = re.sub(r"\s+", " ", text)
                            lo = max(0, pos - 30)
                            examples[phrase].append({
                                "snippet": raw[lo:lo + 160],
                                "project": project,
                                "timestamp": stamp,
                                "file": os.path.relpath(path, root),
                            })
                elif side == "assistant":
                    for sent in near_misses(text):
                        unmatched[sent] += 1

    return {
        "host": socket.gethostname(),
        "machine_id": machine_id(),
        "root": root,
        "totals": dict(totals),
        "malformed_lines": bad_lines,
        "counts": {s: dict(c) for s, c in counts.items()},
        "by_category": {s: dict(c) for s, c in by_category.items()},
        "hits_by_month": {s: dict(c) for s, c in by_month.items()},
        "msgs_by_month": {s: dict(c) for s, c in msgs_by_month.items()},
        "hits_by_model": dict(by_model),
        "msgs_by_model": dict(msgs_by_model),
        "hits_by_project": dict(by_project),
        "msgs_by_project": dict(msgs_by_project),
        # List, not dict -- json sort_keys would otherwise destroy the ranking.
        "user_pushback_strict": dict(pushback),
        "near_miss_audit": unmatched.most_common(80),
        "fingerprints": {s: sorted(set(v)) for s, v in counted_fp.items()},
        "hit_fingerprints": sorted(set(hit_fp)),
        "examples": dict(examples),
    }


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DIR
    phrase_file = sys.argv[2] if len(sys.argv) > 2 else PHRASE_FILE

    if not os.path.isdir(root):
        sys.exit("no transcript directory at %s" % root)

    result = scan(root, load_patterns(phrase_file),
                  load_openers(phrase_file),
                  load_assistant_openers(phrase_file),
                  load_negation_guard(phrase_file),
                  load_patterns_regex(phrase_file))

    # Name the output for host AND scanned root. Naming it by host alone means
    # scanning a second directory silently clobbers the first machine's results.
    out = outpath("apology-%s.json" % file_tag(root))
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=1, sort_keys=True)

    t = result["totals"]
    a = t.get("assistant_messages", 0)
    hit = t.get("assistant_messages_with_hit", 0)
    print("host              %s" % result["host"])
    print("files             %s" % t.get("files", 0))
    print("assistant msgs    %s" % a)
    print("  with a match    %s (%.2f%%)" % (hit, 100.0 * hit / a if a else 0))
    print("user msgs         %s" % t.get("user_messages", 0))
    print("duplicates skipped%s" % t.get("duplicate_messages", 0))
    print("wrote             %s" % os.path.relpath(out, os.path.dirname(OUTDIR)))


if __name__ == "__main__":
    main()
