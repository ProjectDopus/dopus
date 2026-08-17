# Dopus — operating instructions

How to run this project, and how to hand it to Claude so it runs it correctly.

If you read nothing else: **`python3 scripts/verify.py` after every change.**

---

## Hand it to Claude

Paste this at the start of a fresh session, with your task filled in:

```
You're working in the Dopus repo (~/GitHub/Dopus). Before doing anything, read
INSTRUCTIONS.md, CLAUDE.md, and METHOD.md.

Task: <what you want>

Non-negotiable:
- Run `python3 scripts/verify.py` after every change and paste the result.
  Nothing is "fixed" until it passes. A fix that does not move
  results/all-matches.jsonl did not happen.
- If you change a detector or phrases.json, run `python3 scripts/build_rows.py`
  BEFORE verify, or you are verifying stale data.
- Do not run anything in superseded/. It is provenance only.
- Do not commit archive/, history.sqlite, hosts.json, or any results/ file that
  contains message text. Check `git status` before committing, not after.
- Anything that changes WHAT COUNTS is a decision, not a cleanup. Tell me the
  cost before you make it, don't report it afterwards as a bug fix.
- Adjudicate phrases on full enumeration (scripts/audit.py), never on the first
  few hits you happen to see.
```

The last three lines exist because each one has already gone wrong at least once.
See "Known traps" below.

---

## What this measures

How often Claude uses capitulation language — `you're right`, `good catch`,
`my mistake` — across a corpus of Claude Code transcripts, alongside how often
the user is swearing at it. Claude concedes in **3.49%** of its messages, and
the rate tracks tone: **11.58%** after a user turn containing profanity,
**5.92%** after a plain correction, **2.99%** after a neutral turn. The
hot-versus-correction comparison is the one that carries weight — both are the
user saying Claude is wrong, and only the tone differs.

Full findings in [`REPORT.md`](REPORT.md). Method in [`METHOD.md`](METHOD.md).
What the project is asking, in [`README.md`](README.md).

---

## Layout

```
Dopus/
├── INSTRUCTIONS.md      you are here — how to run it
├── README.md            what the project is and the research questions
├── METHOD.md            how the measurement works
├── REPORT.md            the corpus analysis writeup
├── CLAUDE.md            the rules Claude must follow in this repo
├── phrases.json         THE DICTIONARY — the file you actually edit
│
├── scripts/             all executable code
│   ├── paths.py           where everything lives; imported by the rest
│   ├── scan.py            matcher library (also shipped to remote hosts)
│   ├── swear.py           user-side detectors
│   ├── sync.py            pull transcripts from the machines
│   ├── build_db.py        archive/ -> history.sqlite
│   ├── build_rows.py      history.sqlite -> results/all-matches.jsonl
│   ├── verify.py          33 invariant checks  ← THE GATE
│   ├── analyze.py         full descriptive analysis
│   ├── tally.py           raw phrase counts, both sides
│   ├── audit.py           enumerate + evenly sample one phrase
│   ├── sweep.py           mine the corpus for untracked phrases
│   └── followthrough.py   did a promised action get honored? v2, NOT CALIBRATED
│
├── results/             outputs (mostly untracked — they contain message text)
├── archive/             raw transcripts, 2.4 GB, untracked
├── history.sqlite       every message, 187 MB, untracked
├── hosts.json           machine inventory, untracked
├── ACCESS.local.md      jump host and sudo chain, untracked
└── superseded/          retired code. Provenance only. DO NOT RUN.
```

**Scripts live in `scripts/`, data lives at the root.** Every script resolves
paths through `scripts/paths.py`, so it works from any working directory —
`python3 scripts/verify.py`, `cd scripts && python3 verify.py`, and an absolute
path all behave identically.

`scan.py` is the one exception: it resolves its own paths inline because
`sync.py` copies it to `/tmp` on remote hosts, where no repository exists
around it.

---

## Prerequisites

- **Python 3.6+**, standard library only. No pip install, no virtualenv.
- **For syncing only:** SSH access to the machines, plus `hosts.json` and
  `ACCESS.local.md` (both untracked — they hold the host inventory and the
  jump-host chain).
- Everything except `sync.py` runs offline against the local database.

---

## THE RULE

```
python3 scripts/verify.py
```

Run it after every change. 33 checks: 18 known-answer fixtures pushed through
the *same* matcher the dataset uses, 3 text-extraction checks that injected
wrappers never reach the dataset, and a live re-derivation of both sides from
the database compared against the shipped `results/all-matches.jsonl`.

**Why it exists.** The same bug shipped three times in one day. A fix landed in
one code path while the dataset was produced by another — `SHOUT_RX`, then
`shouting()`, then `USER_GUARD`. Each was "verified" by running the edited file
rather than the artifact that matters. Every user-side number was wrong until it
was caught.

If verify passes but you didn't run `build_rows.py` after editing a detector,
you verified the old dataset against the old dataset. Order matters.

---

## Recipes

### Change a phrase or a detector

The common case. Edit `phrases.json` (or `scan.py` / `swear.py`), then:

```bash
python3 scripts/build_rows.py     # regenerate the dataset   ~25s
python3 scripts/verify.py         # 33 checks                ~40s
python3 scripts/tally.py          # refresh the raw counts
python3 scripts/analyze.py        # refresh the aggregates
```

Before adding or removing a phrase, **enumerate it first** (see below). Adding
`exactly right` on a 4-sample look cost ~50 false positives; rejecting
`i introduced` on a 3-sample look threw away a phrase that is 71% genuine.

### Check whether a phrase is any good

```bash
python3 scripts/audit.py --phrase "good catch" --samples 12
python3 scripts/audit.py --min 5 --max 200 --samples 8      # sweep a range
```

Walks the whole corpus and samples at even intervals rather than showing you
whatever sorted first. Record the verdict in `phrases.json` under
`_precision_audit`.

### Find phrases nobody is tracking yet

```bash
python3 scripts/sweep.py
```

Extracts praise-shaped constructions and subtracts what `phrases.json` already
covers. Candidates below roughly the top 30 have never been adjudicated.

### Weekly sync

```bash
python3 scripts/sync.py            # pull new/grown transcripts
python3 scripts/build_db.py        # rebuild the database        ~10s
python3 scripts/build_rows.py      # rebuild the dataset         ~25s
python3 scripts/tally.py && python3 scripts/analyze.py
python3 scripts/webdata.py         # regenerate ../Dopus-web/data.js (the public site)
python3 scripts/verify.py          # invariants + Dopus-web freshness + prose claims
```

The public site (`../Dopus-web`, deployed on Cloudflare) renders every figure
from `data.js`; regenerating that one file IS the site sync. verify.py fails
if data.js is stale, and its `PROSE:` checks flag when a data shift breaks a
sentence on the page ("roughly doubles", "non-overlapping intervals") — those
failures mean *rewrite the sentence on the page*, not rerun a script. Commit
and push Dopus-web after a sync so the deploy picks it up.

`sync.py` is diff-based and **never deletes**. It compares what is on each
machine against what is already in `archive/`, fetches only what is new or has
grown, and verifies every fetched file on disk afterwards — because a command
can exit 0 and still not produce what you asked for.

Useful flags: `--host <name>` for one machine, `--discover` to re-derive
transcript roots (slow; only when a machine's layout changed).

### Label the follow-through calibration sample

```bash
python3 scripts/followthrough.py            # score the corpus
python3 scripts/followthrough.py --sweep    # rule-variant sensitivity
python3 scripts/followthrough.py --code 60  # build the labelling page
python3 scripts/followthrough.py --code 60 --redo <old-labels.json>
                                            # same, prefilling decided answers
python3 scripts/calibrate.py results/coding/followthrough-labels-<date>.json
```

`--code` writes `results/coding/followthrough-coder-<date>.html` — open it in a
browser. One card per record: what you said, what Claude promised, and the next
user turns as evidence. Three buttons (`Y`/`N`/`C` keys work), progress saved in
localStorage, and Export downloads the labels JSON that `calibrate.py` reads.
Drop the exported file into `results/coding/`. The draw is deterministic, so
`--redo` maps prior answers onto the same records and only the undecided ones
need visiting.

### Rebuild everything from the archive

```bash
python3 scripts/build_db.py && python3 scripts/build_rows.py && python3 scripts/verify.py
```

Safe at any time. `archive/` is the source of truth; the database and the
dataset are both derived and disposable.

### Regenerate the numbers in the writeups

```bash
python3 scripts/analyze.py     # -> results/analysis.json, prints the report
python3 scripts/tally.py       # -> results/TALLY.md
```

`results/TALLY.md` is generated, so it needs nothing further. `REPORT.md`,
`METHOD.md`, and the answer table in `README.md` are written by hand from these
outputs — if you regenerate, update all three and cross-check the counts rather
than trusting the previous text. The headline denominator has silently drifted
between documents once already.

---

## Command reference

| command | reads | writes | time |
|---|---|---|---|
| `scripts/sync.py` | the machines | `archive/` | minutes |
| `scripts/build_db.py` | `archive/` | `history.sqlite` | ~10s |
| `scripts/build_rows.py` | `history.sqlite`, `phrases.json` | `results/all-matches.jsonl`, `denominators.json` | ~25s |
| `scripts/verify.py` | everything | nothing | ~40s |
| `scripts/followthrough.py` | db + rows | `results/followthrough.jsonl` | ~30s |
| `scripts/stats.py` | `analysis.json` | nothing (prints) | instant |
| `scripts/collect.py` | `~/.claude/projects` | `archive/` | seconds |
| `scripts/export.py` | aggregates only | `results/dopus-bundle-*.json` | instant |
| `scripts/analyze.py` | db + rows | `results/analysis.json` | ~20s |
| `scripts/tally.py` | rows | `results/TALLY.md` | instant |
| `scripts/audit.py` | db + `phrases.json` | nothing | ~15s |
| `scripts/sweep.py` | transcripts | `results/sweep-*.json` | ~30s |

---

## Known traps

Each of these has already cost real time.

**Fix the path the dataset uses.** Three bugs shipped because a fix landed in one
code path while `build_rows.py` used another. Run the pipeline, not the file you
just edited.

**Adjudicate on full enumeration, never first-N.** `i introduced` was rejected on
a 3-sample draw and is 71% genuine. `exactly right` was added on 4 samples, was
44% precise, and cost ~50 false positives.

**A judgment call is not a bug fix.** Collapsing `shouting` from 390 hits to 5
was reported as cleanup. It deleted real signal. Anything that changes what
counts gets surfaced as a decision with its cost attached, *before*.

**Flat phrase lists are the wrong shape.** Position, denial twins, productive
families, and person-attachment each exist as separate mechanisms because a bare
string mixed things that aren't the same. `lazy` was reported as the user's #2
word at 58 occurrences; it is 6, and the other 52 were `loading="lazy"`.

**Check `git status` before committing.** `results/` is deny-by-default in
`.gitignore` for a reason: `counts-*.csv` embeds message snippets and
`sweep-*.json` embeds samples containing internal IPs and server hostnames. Only
`denominators.json`, `analysis.json`, and `TALLY.md` are whitelisted.

**Ship scripts as files over SSH.** Quoting a heredoc through two shells broke
sync silently for a day. Project directories begin with `-`, so `tar -T` reads
them as flags — prefix `./`.

---

## Troubleshooting

**`verify.py` says the dataset is STALE.** You edited a detector and didn't
rebuild. Run `python3 scripts/build_rows.py`, then verify again.

**`db file count != archive file count`.** The database is older than the
archive. Run `python3 scripts/build_db.py`.

**`sync.py` reports fetching but nothing changed.** Check the `FAILURES` list it
prints at the end. Every subprocess is return-code checked and every fetched
file is verified on disk, specifically because this failure used to be silent.

**A phrase count moved and you don't know why.** `git log -p phrases.json` and
`results/TALLY.md` — the tally is tracked precisely so counts are diffable
across commits.

**Numbers in the docs don't match the scripts.** Trust the scripts. Regenerate
with `analyze.py` and `tally.py`, then fix the document.

---

## Known rough edge

`tally.py` runs at import time rather than under a `main()` guard, so
`import tally` will write `results/TALLY.md` as a side effect. Harmless when run
as a script, which is the only way it is used. Left alone rather than silently
restructured.
