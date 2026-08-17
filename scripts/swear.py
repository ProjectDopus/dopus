#!/usr/bin/env python3
"""
The other side of the ledger: how often the human swears, shouts, or gets mean.

Usage:  python3 scripts/swear.py [transcript_dir]
Writes: results/swear-<machine_id>.json  (next to this script)

Shares extraction with scan.py so both sides measure the same text. Read-only.

Two things this does that a plain word count cannot:

  DIRECTED vs AMBIENT -- "this is fucking broken" is aimed at the code,
  "you're not listening" is aimed at me. Same word list, different meaning.
  Split on whether a second-person pronoun shares the sentence.

  DOES IT WORK -- for every user message, look at my very next reply and check
  whether it contains a concession. Comparing that rate after a swear against
  the baseline says whether profanity actually moves me.
"""

import json
import os
import re
import socket
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scan as S

# ------------------------------------------------------------------- word lists

PROFANITY = [
    "fuck", "fucking", "fucked", "fuckin", "fucks", "fuck's",
    "shit", "shitty", "shits", "bullshit", "horseshit",
    "damn", "damnit", "dammit", "goddamn", "god damn", "god damnit",
    "god fucking damnit", "goddammit",
    "hell", "crap", "crappy", "piss", "pissed", "ass", "asshole", "arse",
    "bloody", "bugger", "wtf", "ffs", "stfu",
]

BLASPHEMY = [
    "jesus christ", "jesus fucking christ", "jesus", "christ",
    "for fuck's sake", "for christ's sake", "for god's sake", "for the love of god",
    "oh my god", "omfg", "good lord",
]

INSULT = [
    "moron", "moronic", "idiot", "idiotic", "stupid", "dumb", "dumbass",
    "incompetent", "useless", "worthless", "pathetic", "clueless",
    "lazy", "sloppy", "careless", "amateur", "braindead", "brain dead",
]

NOT_LISTENING = [
    "you're not listening", "you are not listening", "not listening",
    "you're not reading", "are you even reading", "did you even read",
    "did you even look", "i just said", "i already said", "i already told you",
    "i told you", "how many times", "for the third time", "for the last time",
    "listen to me", "pay attention", "read what i wrote", "read it again",
    "as i said", "like i said", "i keep telling you", "you keep",
    "stop doing that", "stop it", "just stop",
]

# Case-SENSITIVE. Lowercase "stop" is "stop the server" -- we proved that at
# 634 false positives. Uppercase STOP is a person shouting.
# Case-sensitive shouting.
#
# History, because this has been wrong twice in opposite directions:
#   v1  bare STOP/NO/WHAT matched ANYWHERE in raw text -> 390 hits, ~283 of them
#       `finishReason STOP` and `tools.ts has NO emit path`. Pasted machine output.
#   v2  restricted to multi-word forms -> killed the false positives AND the real
#       signal. A bare "STOP." typed as its own sentence is unambiguous, and it
#       was the single most common genuine shout.
#   v3  (this) position rule instead of a vocabulary rule: a short shout counts
#       only when it stands alone as a whole sentence or the whole message,
#       outside code fences. Multi-word shouts still match inline.
SOLO_SHOUTS = ["STOP", "NO", "NOPE", "WHY", "WHAT", "ENOUGH", "SERIOUSLY",
               "WRONG", "READ IT", "LISTEN", "DONT", "DON'T"]
INLINE_SHOUTS = ["JUST STOP", "STOP IT", "DO NOT", "READ THE", "WHAT THE",
                 "WHY THE", "LISTEN TO ME", "I TOLD YOU", "I SAID"]

FENCE_RX  = re.compile(r"```.*?```", re.S)
INLINE_CODE_RX = re.compile(r"`[^`]*`")
SEGMENT_RX = re.compile(r"[\n.!?]+")

SOLO_SET = set(SOLO_SHOUTS)
INLINE_RX = re.compile(r"(?<![A-Za-z])(" + "|".join(
    re.escape(w).replace("\\ ", r"\s+") for w in sorted(INLINE_SHOUTS, key=len, reverse=True)
) + r")(?![A-Za-z])")

# Still voids a match sitting in obvious machine output.
LOG_CTX = re.compile(r"finishreason|stop condition|stop sequence|stop token|"
                     r"exit code|status:|returncode|emit path|stop_reason|max_tokens")


def strip_code(raw):
    """Remove fenced blocks and inline code before looking for shouts."""
    return INLINE_CODE_RX.sub(" ", FENCE_RX.sub(" ", raw))


def find_shouts(raw):
    """[(token, position)] -- solo shouts must be a whole segment; multi-word may be inline."""
    body = strip_code(raw)
    out = []
    pos = 0
    for seg in SEGMENT_RX.split(body):
        start = body.find(seg, pos) if seg else pos
        pos = start + len(seg)
        bare = seg.strip().strip("*_-–— \t")
        if bare in SOLO_SET:
            out.append((bare, max(start, 0)))
    for m in INLINE_RX.finditer(body):
        ctx = m.group(0)
        if not LOG_CTX.search(body[max(0, m.start()-60):m.start()+60].lower()):
            out.append((ctx, m.start()))
    return out


# All-caps words that are just vocabulary in this line of work, not shouting.
TECH_CAPS = set("""
API JSON HTML CSS SQL HTTP HTTPS URL URI ID UUID CSV PDF CLI MCP LLM AI UI UX
JS TS PR CI CD OK TODO FIXME NOTE XXX README MIT GPU CPU RAM SSH SSL TLS DNS IP
OS VM DB ORM CRUD REST GET POST PUT DELETE PATCH HEAD XML YAML TOML ENV PATH
HOME USER NULL NAN EOF ASCII UTF PNG JPG JPEG SVG GIF WEBP MB GB KB TB MS AM PM
UTC USA FIPSE DOE JV PIDM WTE SSO MFA GEO SEO GSD UAT ADR PRD SPEC DOC DOCX XLSX
NPM PIP GIT SDK IDE JVM DOM CORS CSRF JWT OAUTH SAML LDAP SMTP IMAP FTP TCP UDP
RSS ATOM CDN DNS VPN LAN WAN USB SSD HDD BIOS UEFI ARM X86 AMD INTEL MAC IOS
ONLY ALL AND OR NOT IF THEN ELSE TRUE FALSE NONE YES NEW OLD ADD FIX USE RUN
""".split())

CATEGORIES = [
    ("profanity", PROFANITY),
    ("blasphemy", BLASPHEMY),
    ("insult", INSULT),
    ("not_listening", NOT_LISTENING),
]

# A CALM correction: contradicting me without heat. This is the bucket that
# matters most -- "hot vs calm" is a bad comparison because the calm side is
# mostly not corrections at all ("run the tests", "what's next"), which drags
# the baseline down and flatters any effect attributed to profanity.
CORRECTION_RX = re.compile(
    r"\bactually\b|\bnot quite\b|\bthats not\b|\bthat isnt\b|\bits not\b"
    r"|\bdoesnt work\b|\bdidnt work\b|\bstill broken\b|\bstill not\b"
    r"|\bstill doesnt\b|\bstill isnt\b|\byou missed\b|\byou forgot\b"
    r"|\byou didnt\b|\byoure not\b|\byou never\b|\bthats wrong\b"
    r"|\bincorrect\b|\bi meant\b|\bi said\b|\bi asked for\b|\bnot what i\b"
    r"|\bisnt right\b|\bwasnt right\b|\bnope\b|\bno,|\bexcept\b"
    r"|\bbut it (?:still|doesnt|didnt|isnt)\b|\bthat broke\b|\byou broke\b"
)

# User-side guards. Same failure mode as the assistant side: a word that is an
# insult in prose and vocabulary in code. "lazy" was reported as Zach's #2 term
# (58 hits) when most are loading="lazy" and "lazy engine".
USER_GUARD = {
    "lazy":    re.compile(r'loading=|lazy load|lazy-load|lazily|lazy engine|lazy init|'
                          r'lazy monaco|lazy import|lazy eval|`lazy'),
    "stop it": re.compile(r"cannot stop it|to stop it|can'?t stop it|nothing .{0,20}stop it|"
                          r"no way to stop it"),
    "hell":    re.compile(r"hell yes|hell yeah|hell of a"),
    "damn":    re.compile(r"hot damn|damn near|damn good|damn fine"),
    "ass":     re.compile(r"pain in the ass|sweet ass|random ass|badass|kick ass|"
                          r"ass\w|sucks ass"),
    "crap":    re.compile(r"marketing crap|crap like"),
    "dumb":    re.compile(r"dumb as hell|dumbest thing|dumb fuck"),
    "piss":    re.compile(r"pissed off at (?!you|claude)"),
}

# Positive requirement, not a blacklist. Some words are insults ONLY when
# attached to a person; blacklisting technical uses is endless ("lazy load",
# "lazy engine", "lazy-transcript", "create_engine is lazy", "lazy singletons").
# Require the person instead.
USER_REQUIRE = {
    "lazy":     re.compile(r"\b(?:being|youre|you are|are you|you're|so)\s+\w{0,8}\s*lazy"),
    "sloppy":   re.compile(r"\b(?:being|youre|you are|thats|this is|so)\s+\w{0,8}\s*sloppy"),
    "careless": re.compile(r"\b(?:being|youre|you are|thats|that was|so)\s+\w{0,8}\s*careless"),
}

SECOND_PERSON = re.compile(r"\byou\b|\byoure\b|\byour\b|\byouve\b|\byoud\b|\bu\b")
SENT_SPLIT = re.compile(r"[.!?\n]+")
CAPS_WORD = re.compile(r"\b[A-Z][A-Z']{2,}\b")


def build(words):
    body = "|".join(
        re.escape(S.normalize(w)).replace("\\ ", r"\s+")
        for w in sorted(words, key=len, reverse=True)
    )
    return re.compile(r"(?<![a-z0-9])(" + body + r")(?![a-z0-9])")


PATTERNS = [(name, build(words)) for name, words in CATEGORIES]
# SHOUT_RX removed: superseded by find_shouts(), which applies a POSITION rule
# (solo shouts must stand alone as a segment) rather than a vocabulary rule.


# Caps words that carry frustration on their own. A bare all-caps heuristic is
# useless here: Zach's own prompt templates are full of LOW / HIGH / MED / DONE /
# PASS / FILES CHANGED headers, which flagged 1027 messages as "shouting" when
# only a couple hundred were angry.
FRUSTRATION_CAPS = set("""
STOP NO NOPE WHY WHAT DONT READ LISTEN SERIOUSLY AGAIN ENOUGH WRONG NEVER
FUCK FUCKING SHIT JESUS CHRIST GOD DAMN DAMNIT GODDAMN HELL WTF FFS
""".split())


def shouting(raw):
    """Real shouting, not template headers.

    Either a caps word that is itself angry, or a short message that is
    predominantly caps (a whole sentence yelled, not a label embedded in prose).
    """
    caps = [w for w in CAPS_WORD.findall(raw) if w not in TECH_CAPS]
    angry = [w for w in caps if w.replace("'", "") in FRUSTRATION_CAPS]
    if angry:
        return angry
    words = raw.split()
    if 0 < len(words) <= 25:
        longish = [w for w in words if len(re.sub(r"\W", "", w)) >= 3]
        if longish and len(caps) >= 0.6 * len(longish):
            return caps
    return []


def user_hits(norm_text):
    """[(token, category, pos)] with guards applied. THE single entry point.

    Guards previously lived only inside analyze(), while build_rows.py iterated
    PATTERNS directly -- so the dataset never saw them. Third time a fix landed
    in one code path while the dataset used another; both callers now come here.
    """
    out = []
    for name, rx in PATTERNS:
        for m in rx.finditer(norm_text):
            tok = m.group(1)
            win = norm_text[max(0, m.start()-40):m.start()+40]
            g = USER_GUARD.get(tok)
            if g and g.search(win):
                continue
            req = USER_REQUIRE.get(tok)
            if req and not req.search(win):
                continue          # word present, but not aimed at a person
            out.append((tok, name, m.start()))
    return out


def analyze(raw):
    """Return (hits, directed, shout_words, caps_words) for one user message."""
    norm = S.normalize(raw)
    hits = []
    directed = False
    for sent in SENT_SPLIT.split(norm):
        aimed = bool(SECOND_PERSON.search(sent))
        for tok, name, _p in user_hits(sent):
            hits.append((name, tok))
            if aimed:
                directed = True
    shouts = [tok for tok, _p in find_shouts(raw)]
    return hits, directed, shouts, shouting(raw)


# ---------------------------------------------------------------------- scanner

def main():
    root = sys.argv[1] if len(sys.argv) > 1 else S.DEFAULT_DIR
    patterns = S.load_patterns(S.PHRASE_FILE)

    seen = set()
    by_category = Counter()
    by_word = Counter()
    by_month = Counter()
    msgs_by_month = Counter()
    directed_n = ambient_n = shout_n = caps_n = 0
    user_msgs = 0
    heat = Counter()          # session -> number of hot messages
    examples = defaultdict(list)

    # Does swearing move me? Concession rate in my NEXT reply.
    # Three buckets, not two: neutral / calm correction / hot.
    buckets = {"neutral": [0, 0], "correction": [0, 0], "hot": [0, 0]}
    conceded_after = Counter()
    # Fingerprints so merge_swear.py can dedup shared history across boxes.
    # Without these the profanity totals double-count any session that lives
    # on two machines -- the exact failure merge.py already guards against on
    # the apology side (it caught 478 duplicated messages from one archive).
    user_fp = []
    hit_fp = []
    bucket_fp = {"neutral": [], "correction": [], "hot": []}
    bucket_conceded_fp = {"neutral": [], "correction": [], "hot": []}

    files = []
    for dp, _d, names in os.walk(root):
        if os.path.basename(dp) in S.EXCLUDE_PROJECTS:
            continue
        files += [os.path.join(dp, n) for n in names if n.endswith(".jsonl")]

    for path in files:
        session = os.path.basename(path)[:-6]
        pending = None        # True if the last user message was hot
        try:
            fh = open(path, encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(e, dict) or e.get("type") not in ("user", "assistant"):
                    continue
                if S.is_synthetic(e):
                    continue
                uid = e.get("uuid")
                if uid:
                    if uid in seen:
                        continue
                    seen.add(uid)
                raw = S.message_text(e)
                if not raw.strip():
                    continue
                stamp = e.get("timestamp") or ""
                month = stamp[:7] if len(stamp) >= 7 else "unknown"

                if e["type"] == "assistant":
                    if pending is not None:
                        conceded = bool(S.find_matches(
                            S.normalize(raw), patterns["assistant"]))
                        buckets[pending][0] += 1
                        if uid:
                            bucket_fp[pending].append(S.fingerprint(uid))
                        if conceded:
                            buckets[pending][1] += 1
                            conceded_after[pending] += 1
                            if uid:
                                bucket_conceded_fp[pending].append(S.fingerprint(uid))
                        pending = None
                    continue

                # user message
                user_msgs += 1
                if uid:
                    user_fp.append(S.fingerprint(uid))
                msgs_by_month[month] += 1
                hits, directed, shouts, caps = analyze(raw)
                # caps alone is NOT heat -- it was mostly prompt templates
                hot = bool(hits or shouts)
                if hot:
                    pending = "hot"
                elif CORRECTION_RX.search(S.normalize(raw)):
                    pending = "correction"
                else:
                    pending = "neutral"

                if hits:
                    if uid:
                        hit_fp.append(S.fingerprint(uid))
                    heat[session] += 1
                    by_month[month] += 1
                    for name, word in hits:
                        by_category[name] += 1
                        by_word[word] += 1
                        if len(examples[word]) < 3:
                            flat = re.sub(r"\s+", " ", raw)
                            examples[word].append({
                                "snippet": flat[:150],
                                "session": session,
                                "timestamp": stamp,
                            })
                    if directed:
                        directed_n += 1
                    else:
                        ambient_n += 1
                if shouts:
                    shout_n += 1
                if caps:
                    caps_n += 1

    def rate(b):
        return 100.0 * b[1] / b[0] if b[0] else 0.0

    result = {
        "host": socket.gethostname(),
        "machine_id": S.machine_id(),
        "root": root,
        "user_messages": user_msgs,
        "by_category": dict(by_category),
        "by_word": dict(by_word.most_common()),
        "by_month": dict(by_month),
        "msgs_by_month": dict(msgs_by_month),
        "directed": directed_n,
        "ambient": ambient_n,
        "shouted": shout_n,
        "all_caps": caps_n,
        "hot_sessions": len(heat),
        "worst_sessions": heat.most_common(10),
        "concession_by_bucket": {k: {"replies": v[0], "conceded": v[1],
                                     "rate": round(rate(v), 2)}
                                 for k, v in buckets.items()},
        "conceded_after": dict(conceded_after),
        "fingerprints": {"user": sorted(set(user_fp))},
        "hit_fingerprints": sorted(set(hit_fp)),
        "bucket_fingerprints": {k: sorted(set(v)) for k, v in bucket_fp.items()},
        "bucket_conceded_fingerprints": {k: sorted(set(v))
                                        for k, v in bucket_conceded_fp.items()},
        "examples": dict(examples),
    }

    out = S.outpath("swear-%s.json" % S.file_tag(root))
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=1, sort_keys=True)

    total = sum(by_category.values())
    print("user messages       %d" % user_msgs)
    print("total hits          %d  (%.2f%% of messages)"
          % (total, 100.0 * (directed_n + ambient_n) / user_msgs if user_msgs else 0))
    for k, v in by_category.most_common():
        print("  %-16s %d" % (k, v))
    print("directed at me      %d" % directed_n)
    print("ambient             %d" % ambient_n)
    print("shouted (caps kw)   %d" % shout_n)
    print("all-caps messages   %d" % caps_n)
    print()
    print("concession in my next reply, by what preceded it:")
    for k in ("neutral", "correction", "hot"):
        b = buckets[k]
        print("  %-11s %6.1f%%  (%4d of %5d)" % (k, rate(b), b[1], b[0]))
    tot = sum(conceded_after.values()) or 1
    print()
    print("of ALL my concessions, what came before:")
    for k in ("neutral", "correction", "hot"):
        print("  %-11s %5.1f%%  (%d)"
              % (k, 100.0 * conceded_after[k] / tot, conceded_after[k]))
    print("wrote %s" % os.path.relpath(out, os.path.dirname(S.OUTDIR)))


if __name__ == "__main__":
    main()
