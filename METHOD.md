# Dopus — method

How the measurement works. For what it found, see [`REPORT.md`](REPORT.md); for
how to run it, see [`INSTRUCTIONS.md`](INSTRUCTIONS.md).

---

## The corpus

```
3,930 transcript files · 423,759 messages · 9 machines · 2026-02-15 → 2026-08-15 (UTC)
```

Every `.jsonl` Claude Code transcript found on nine machines — laptops, a Mac
Studio, and university servers — collected by diff-based sync, stored verbatim
with a sha256 per file, then loaded into SQLite with every flag preserved.

By model: sonnet-5 109,230 · opus-4-8 74,769 · fable-5 35,476 · opus-5 30,111 ·
sonnet-4-6 9,851 · haiku-4.5 7,083 · opus-4-7 2,121.

Non-Anthropic models found on the same machines (`.claude-kimi`, `.claude-glm`)
are excluded by name and logged as skips. This measures Claude.

**Analysis population:** 26,108 assistant and 4,890 user messages — prose only,
after the exclusions below.

The user figure is low relative to the assistant figure because injected
wrappers are stripped before counting. Background-task completions in
particular arrive as *user* messages: 2,239 turns (31.8% of what used to be
counted) were pure machine markup with no human prose in any of them.

---

## THE RULE

**Run `python3 scripts/verify.py` after every change.** Nothing is reported as
fixed until it passes.

This exists because the same bug shipped three times in one day: a fix landed in
one code path while the dataset was produced by another. `SHOUT_RX`,
`shouting()`, and `USER_GUARD` were each "fixed" and each verified by running the
edited file rather than the artifact that matters. Every user-side number was
wrong until it was caught.

`verify.py` runs 33 checks — 18 known-answer fixtures through the same matcher
the dataset uses, 3 text-extraction checks that injected wrappers never reach
the dataset, and a live re-derivation of both sides from the database compared
against `results/all-matches.jsonl`. **A fix that does not move that
file did not happen.**

---

## Pipeline

All code lives in `scripts/`; all data lives at the repository root. Every script
resolves its paths through `scripts/paths.py`, so it behaves identically from any
working directory.

```
scripts/sync.py        pull new/grown transcripts from 9 machines
                       diff-based, never deletes, verifies every file on disk
scripts/build_db.py    archive/ -> history.sqlite   every message, flags kept  (~10s)
scripts/build_rows.py  history.sqlite -> results/all-matches.jsonl + denominators (~25s)
scripts/verify.py      33 invariant checks                                     (~40s)

scripts/analyze.py     full descriptive analysis -> results/analysis.json
scripts/tally.py       raw phrase counts, both sides, unfiltered, no taxonomy
scripts/stats.py       statistical appendix -- phi, OR, trend tests, from analysis.json
scripts/collect.py     contributor entry point: stage ~/.claude/projects into archive/
scripts/export.py      build the one shareable bundle; refuses if text-shaped
scripts/audit.py       enumerate + evenly sample any phrase, for precision work
scripts/sweep.py       mine the corpus for untracked constructions
scripts/followthrough.py  did a concession that promised action get honored?
```

`superseded/` holds retired scripts and stale outputs. Provenance only — **do not run.**

### Store everything, filter at query time

`history.sqlite` keeps *every* message with its flags (`is_sidechain`,
`is_compact`, `is_meta`, `is_visible_only`, `has_text`). Exclusions are therefore
visible `WHERE` clauses in one place, not decisions baked into a scanner:

```sql
m.has_text=1 AND m.is_sidechain=0 AND m.is_compact=0
AND m.is_meta=0 AND m.is_visible_only=0
AND f.project != '<own-project>'
```

The last clause excludes this project's own conversations, which quote every
target phrase and would contaminate the measurement. `<own-project>` is the
slug of the Dopus checkout itself, derived from the repo's location at runtime
(`paths.PROJECT_SLUG`) — so any contributor's clone excludes *their* Dopus
conversations automatically, and no filesystem path is baked into the source.

---

## How the dictionary works

Flat phrase lists are the wrong shape. Four mechanisms exist because a bare
string mixed things that aren't the same:

| mechanism | why it exists |
|---|---|
| `assistant_openers` | **Position.** `right —` at a sentence start is a concession; mid-sentence it is a direction. |
| `assistant_negation_guard` | **Denial twins.** "I introduced this bug" and "I introduced neither" share a stem. The guard voids a match within ±80 chars of a negation. |
| `assistant_patterns` | **Productive families.** Regex where the construction generalises and enumerating strings would not. |
| `USER_REQUIRE` | **Person-attachment.** `lazy` was reported as the user's #2 word at 58 occurrences. It is 6. The rest were `loading="lazy"`. |

Measured precision for 22 phrases lives in `phrases.json` under `_precision_audit`.

**Adjudicate on full enumeration, never first-N.** `i introduced` was rejected on
a 3-sample draw; full enumeration showed it 71% genuine. `exactly right` was added
on 4 samples; it was 44% and cost ~50 false positives before removal.

### Three constructs, not one

| construct | categories | hits | messages | rate |
|---|---|---|---|---|
| **concession** | agreement, wrong_approach, apology, reversal, fabrication, restart | 1,015 | 912 | **3.49%** |
| flattery | validation | 186 | 184 | 0.70% |
| acknowledgment | compliance | 64 | 64 | 0.25% |
| **user frustration** | profanity, insult, not_listening, blasphemy, shouting | 354 | 224 | **4.58%** |

A single "sycophancy" bucket mixed three different speech acts: admitting fault,
praising the user, and accepting an instruction. Grouping is reversible — the raw
counts in [`results/TALLY.md`](results/TALLY.md) never move.

---

## The dataset

`results/all-matches.jsonl` — one row per match, 25 fields, identical schema for
both sides. Beyond the obvious fields:

| field | meaning |
|---|---|
| `counterpart_text` | the other half of the exchange, 4,000 chars |
| `turn_bucket` | `neutral` / `correction` / `hot` |
| `repeat_markers` | explicit "I already said this" markers — **43% recall, see below** |
| `prior_turn_similarity` | Jaccard vs earlier user turns in the session |
| `unauthorized_markers` | "I never asked for this" |
| `user_repeated_prior_instruction` | **null — human coding slot** |
| `user_says_action_unauthorized` | **null — human coding slot** |

`results/denominators.json` carries per-machine totals and the exact `WHERE`
clause, so the file supports rates and not only composition.

---

## What the method cannot do

It counts phrases. It cannot tell a **warranted** concession from a **reflexive**
one — and that is the question worth answering.

**75.5%** of concessions follow a user turn carrying no lexical correction marker.
Four distinct behaviours produce identical dictionary hits:

```
warranted concession · reflexive concession
concession-after-denial · concession-after-a-repeated-instruction
```

A seven-record pilot on the hottest turns in the corpus made the problem sharper
rather than softer:

- **7/7** — the user's claim was correct
- **7/7** — the user was repeating an instruction already given
- **3/7** — the lexical repeat detector noticed. **43% recall**, and the
  similarity fallback does not separate the classes (missed: 0.077 / 0.094 /
  0.130 / 0.044 · caught: 0.080 / 0.091 / 0.128). Any analysis that stratifies on
  `repeat_markers` is stratifying on noise.
- **3/7** — concessions whose language was correctly scoped and which were then
  **not honored**

### It can only see confessed fabrication

The `fabrication` category (`i made it up`, `i guessed`, `i hallucinated`) has
**4 hits in 423,759 messages**. That is not a measurement of how often Claude
invents things. Those phrases appear only *after* someone catches it, so the
detector finds confessed fabrication and nothing else.

A worked example, from this repository: for most of 2026-08-11 the README,
REPORT, and INSTRUCTIONS all asserted that the project was named after the phrase
`you're absolutely right`, and three commit messages repeated it. Nobody had ever
said that. The claim was invented, never checked, and promoted to the headline
finding — displacing the question actually being asked. No detector here could
see it, because the text never said "I guessed."

---

## The instrument

Asking a user "were you right?" doesn't scale past one participant — nobody can
verify anyone else's memory. That constraint selects the design rather than
limiting it:

> **Downstream repeat detection is the only label present in every transcript
> without asking anyone anything.** If the user re-raises the same correction
> later, the concession wasn't kept. The user's own subsequent behaviour is the
> label.

**One corpus calibrates, the rest measure.** This corpus is the only one with
ground truth, so the detector is audited here until its precision is known, then
ships as a fixed instrument that never interviews anyone.

### Sized against the corpus

**Silence is not follow-through.** "Never re-raised" also covers giving up, fixing
it yourself, and abandoning the session. Labels are therefore three-class —
*honored / not honored / unobserved* — and the unobserved fraction is reported
next to the rate, never folded into it.

**Right-censoring.** A concession needs downstream turns to be scoreable at all:

| user turns left in session | concessions | share |
|---|---|---|
| 0 | 35 | 2.9% |
| 1–4 | 116 | 9.6% |
| 5–19 | 240 | 19.9% |
| 20–49 | 278 | 23.0% |
| 50–199 | 366 | 30.3% |
| 200+ | 173 | 14.3% |

A 20-turn inclusion rule makes **391 of 1,208 concessions (32.4%) unscoreable**.
Under a binary rule every one would default to *honored*. Hard abandonment —
session ends at the concession — is only 35 records (2.9%); the rest is simply
sessions running out of runway.

**Cross-session linkage** within the same project recovers **152 of the 391
(38.9%)**, cutting unscoreable to ~19.8%. It is bounded by a forward window,
because a distant topic recurrence is the project being worked on again rather
than a re-raise. Only 24 of 77 projects have more than one session.

**Follow-through applies only to concessions that promise action.** "You're right"
often commits to nothing; scoring it would manufacture false *not-honored*
labels. The filter is message-level, not category-level — because category is not
a proxy for it:

| category | commits to action | total | share |
|---|---|---|---|
| compliance | 47 | 61 | 77.0% |
| validation | 130 | 181 | 71.8% |
| apology | 22 | 34 | 64.7% |
| reversal | 23 | 37 | 62.2% |
| agreement | 380 | 630 | 60.3% |
| wrong_approach | 120 | 258 | 46.5% |
| **eligible** | **725** | **1,208** | **60.0%** |

`validation` commits to action *more often* than `wrong_approach`, because these
are sentence-initial openers: *"Good question — let me be precise"* scores
`validation` on four words and commits in the next six.

### Status: v2 built, NOT calibrated

`scripts/followthrough.py` implements the design above. Output in
`results/followthrough.jsonl`. **Its numbers must not be quoted until the
calibration pass.**

v1 scored **1 of 5** hand-labelled records — no better than a coin flip on the
scored subset, and worse than the constant baseline. Its failures were
structural, not parametric, and v2 removes them:

- **Ratios → rare-word anchors.** v1's percentage overlap meant the longer the
  original complaint, the harder it was to match; a focused re-raise sharing 4
  words with a 20-word rant scored 0.20 and was rejected at any threshold. v2
  counts shared *rare* words (≤0.5% of the project's user turns) — a match is a
  match regardless of length.
- **A complaint-shape gate.** A re-raise is by definition a complaint; ordinary
  work continuing on the same topic no longer qualifies. Bare `not`/`no` are
  deliberately excluded from the gate — every pasted JSON contains them.
- **Quote-back exclusion.** Compaction summaries and continuation stubs quote
  the conversation back verbatim, so they matched any trigger by construction.
  They are neither candidate re-raises nor downstream opportunity.
- **Tone stripped from matching.** Shared profanity is shared mood, not shared
  topic; it linked unrelated complaints.
- **The window is wall-clock, not turn-count.** A 200-turn cap silently
  amputated the busiest project (200 user turns ≈ two days in BearCode; one
  known re-raise sat at +612 turns / +5 days). Fourteen days is the semantic
  bound — past that, recurrence is the project being worked on again.

Three evidence paths, in descending precision: an explicit repeat marker
("i told you", turn-initial "again") plus ≥2 shared distinctive words; ≥2 shared
rare words in a complaint-shaped turn; ≥3 shared rare words. Every `not_honored`
carries its path, its matched words, and the quoted turn — the label is
auditable by reading one line.

Against the five pilot records v2 scored 3 of 4, with the fifth correctly
`unobserved` — but the full 60-label calibration below **superseded that**: as a
per-record classifier the detector fails; as a stratified sampling frame it
carried the actual measurement.

One legible behaviour to know: **fan-in.** Several concessions about the same
thing all match the same eventual re-raise — four separate promises about the
antigravity UI are all marked `not_honored` by one "again antigravity looks
like…" turn. That is correct: every one of those promises was in fact not kept.

Label mix at the current rules: 42 not-honored of 325 scored (12.9%);
rule-variant sensitivity runs 7.4% (explicit markers only) → 12.9% (all three
paths), via `--sweep`.

**Building it keeps paying for itself:** v1's first run surfaced the
`<task-notification>` contamination that was silently distorting the shipped
dataset (see the revision note in REPORT.md).

### Calibration: two rounds, done (n=60, 2026-08-11)

`--code` writes a stratified labelling page; `scripts/calibrate.py` scores the
detector against the answers. The draw is stratified **by detector label** with
known stratum sizes, which matters twice below.

**Round 1 — pair-only evidence.** The coder saw trigger + promise and answered
`cant_tell` on **36 of 60** — even the person who lived the conversations could
not decide from that evidence. Protocol finding, not coder failure.

**Round 2 — evidence-based.** The coding page became an HTML card view showing
the next user turns after each promise (without the detector's own match, to
avoid anchoring). `cant_tell` collapsed to **1 of 60**. 59 decided labels.

**Verdict on the detector as a classifier: not usable.**

| | |
|---|---|
| accuracy on scored ∩ decided | 28/47 = 60% · CI [45, 72] |
| always-say-honored baseline on the same records | 62% |
| when it says honored, right | 17/24 = 71% · CI [51, 85] |
| when it says not_honored, right | 11/23 = 48% · CI [29, 67] |

It does not beat the constant baseline. Both of its assumptions failed
measurably, in opposite directions:

- **7 of 18 truly-broken promises produced no lexical re-raise** — the subject
  gave up, rephrased, or moved on. Round 1's "zero false honored" was a
  small-sample artifact. *Silence is not follow-through* is now a measured
  fact, not a design caution: lexical re-raise detection misses roughly 4 in 10
  broken promises.
- **12 of 29 kept promises were flagged anyway**, almost all by one mechanism:
  a *new* complaint in the same topic area reads like the *old* complaint
  returning, plus fan-in (one matched turn refuting several promises, some of
  which had in fact been honored).

**The result that survives: the detector works as a sampling frame.** Stratum
sizes are known, so the labels weight back into a corpus estimate that does not
depend on the detector being right per-record:

| stratum | N | labelled broken | rate |
|---|---|---|---|
| detector said honored | 283 | 7/24 | 29% · CI 15–49 |
| detector said not_honored | 42 | 11/23 | 48% · CI 29–67 |
| detector said unobserved | 244 | 1/12 | 8% · CI 1–35 |

> **Of 569 action-promising concessions, an estimated 22% were not followed
> through — CI [10%, 45%]. Among the 325 with enough downstream to judge:
> ~32%.**

That is the number the instrument was built to produce, and it arrived by a
different road than designed: the human supplied the judgment, the detector
supplied the unbiased draw and the strata. Consistent in direction with the
original 7-record hot-turn pilot (3/7 broken).

Caveats attached to that estimate: single coder, who is also the study's
subject; deterministic even-spacing within strata rather than true random; the
unobserved stratum extrapolates from 12 labels. Tightening the interval is
purely a matter of more labels through the same page.

### Calibration is a sample-size problem

Wilson 95% half-width for a precision estimate at p≈0.80:

```
n=25   ±15.1        n=100  ±7.8        n=250  ±4.9
n=50   ±10.9        n=150  ±6.4        n=400  ±3.9
```

A ~25-record spot-check is a **sanity gate** — enough to catch a detector reading
80% against a truth of 40%, not enough to certify a per-person figure. Publishing
±5 points costs ~250 hand-coded records. An honest per-participant report is
*detector number + calibration error bar + local spot-check*.

---

## Roadmap

1. **Build the follow-through detector** to the spec above — three-class, 20-turn
   inclusion rule, windowed cross-session linkage, 725 eligible concessions.
2. **Calibrate it** against hand-coded records on this corpus. The target error
   bar sets the sample size.
3. **Ship a coding mode** in the tool, so participants can spot-check their own
   data without knowing the method. One stratified draw serves both the
   follow-through pass and the warranted-concession pass.
4. **Add participants.** Collection has to run without this repo's host list, SSH
   topology, or path assumptions before it goes to anyone else.

### Known-unfinished

- **The user side is audited only for its big false positives.** `profanity` and
  `insult` got guards after `lazy` turned out to be `loading="lazy"`, but the
  full enumeration pass the assistant side received has never been run.
- **The deny → verify → admit detector does not work.** 4 of 5 hits were false.
  Live specimens exist on one of the servers — the GLM restart exchange, see
  `ACCESS.local.md` — to rebuild it against.
- **`scripts/sweep.py` candidates below the top ~30 were never adjudicated.**
- **Nothing runs the weekly sync.** `scripts/sync.py` works and is safe to
  re-run, but whether it lives on cron or stays manual is undecided.

---

## Data governance

Recorded 2026-08-12, after consultation with the IRB: **raw transcripts may
never be part of a shared dataset.** That constraint maps onto a boundary this
repository already enforces, so it is stated here to keep it enforced on
purpose rather than by habit.

**An excerpt is a raw transcript.** The rule does not only cover `archive/`. It
covers every text-bearing tier: `history.sqlite`, the 4,000-character
`message_text`/`counterpart_text` fields in `results/all-matches.jsonl`, the
`snippet` field, and the coding pages under `results/coding/`. All of these are
deny-listed in `.gitignore`; all of them stay on the machine that produced
them. What travels — and all that may ever travel — is the counts tier:
category, construct, bucket, hashed identifiers, denominators, and hand-label
verdicts keyed by fingerprint.

**Two quasi-identifiers to scrub before any multi-party export.** Project slugs
embed usernames and paths (`-Users-<name>-...`) and must be hashed the way
machine IDs already are. Matched-phrase + timestamp + project can be
identifying in small corpora; coarsen timestamps to the week in anything
shared.

**For the multi-participant design this is confirmation, not constraint:**
participants' transcripts never leave their machines, the instrument ships
rather than the data, and no person — including the researchers — ever reads
another participant's words. The consent story and the architecture are the
same sentence.

**Enforced, not aspirational:** `scripts/export.py` builds the only artifact a
participant sends — aggregates, tally counts validated against the dictionary,
hashed identifiers, calibration verdicts, and a provenance stamp (dictionary
sha256 + git commit) — and refuses to write if any string in the assembled
bundle is text-shaped. `--selftest` proves the guard catches planted text.
Contributor workflow in [`CONTRIBUTING.md`](CONTRIBUTING.md).

---

## What is not in this repository

```
archive/            raw transcripts        2.4 GB
history.sqlite      every message          187 MB
results/*.jsonl     4,000-char excerpts     12 MB
hosts.json          machine inventory
ACCESS.local.md     jump host and sudo chain
```

Private working history, and past GitHub's file and push limits besides. What
ships is the method. Point the pipeline at your own `~/.claude/projects` and it
will build an equivalent corpus.

---

## Design rules, learned the hard way

- **Store everything, filter at query time.** Exclusions are `WHERE` clauses with
  visible flags, never decisions baked into a scanner.
- **Adjudicate on full enumeration, not first-N.** Both directions of this
  mistake cost real precision.
- **Fix the path the dataset uses.** Three bugs shipped because a fix landed in
  one code path while `scripts/build_rows.py` used another.
- **Ship scripts as files.** Quoting a heredoc through two SSH layers silently
  broke sync for a day. Project directories begin with `-`, so `tar -T` reads
  them as flags — prefix `./`.
- **A judgment call is not a bug fix.** Collapsing `shouting` from 390 to 5 was
  reported as cleanup; it deleted real signal. Anything that changes what counts
  gets surfaced as a decision with its cost attached, before it is made.
