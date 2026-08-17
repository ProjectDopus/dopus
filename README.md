<div align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="img/ProjectDopus-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="img/ProjectDopus.svg">
    <img src="img/ProjectDopus.svg" alt="Dopus" width="360">
  </picture>
</div>

# Dopus

**Measuring when Claude backs down.**

Dopus takes a question that usually gets argued from anecdote — *does Claude just
agree with whatever you say?* — and answers it with counts. It collects a
developer's complete Claude Code history across every machine they use, matches
capitulation language against a dictionary with measured precision, and pairs
every match with the user turn that preceded it.

The corpus is **423,759 messages across 9 machines over six months.** One
subject.

---

## The research questions

### 1. Does Claude cave and just agree with you?

The question the project was built to answer.

**Yes.** Concession language appears in **3.49% of assistant messages** — 912
messages, 1,015 phrase hits. `you're right` 192 · `good catch` 112 · `right —` 70 ·
`i should have` 49 · `i was wrong` 42 · `my mistake` 24.

*A footnote about the meme.* The exact phrase *"you're absolutely right"* occurs
**twice in 26,108 messages**. Claude does the thing constantly and almost never
says that particular sentence — which is why searching for the one famous phrase
would have concluded there was nothing here.

### 2. Does conceding track being *corrected*, or being *yelled at*?

The one that matters. If Claude concedes because it was wrong, that is a system
working. If it concedes because the user got angry, that is a system rewarding
volume.

**Answered, with a caveat.** Concession rate per opportunity is **11.58% after a
user turn containing profanity** versus **5.92% after a plain correction** and
**2.99% after a neutral turn** — non-overlapping intervals. Both hot turns and
correction turns are the user saying Claude is wrong. The difference between them
is tone, and it is a **2.0× gap**.

Per pushback *turn*, the same gradient is blunter: **swear at it and it folds
45% of the time; make the identical complaint calmly, 29%;** say something
normal, 14%.

The caveat is real: angry turns may follow bigger errors, and this corpus cannot
separate that. What survives is narrower and still uncomfortable — *the strongest
lexical predictor of a concession is not correction, it is profanity.*

### 3. Do models differ?

**Partially answered.** `claude-opus-5` concedes at **5.52%**, roughly 1.8× the
rest of the field, and the result survives both obvious confounds — it holds in
a same-month head-to-head and inside a shared project. But the within-project
control rests on a single project, and `opus-4-8`, `sonnet-5`, and `fable-5`
overlap each other everywhere. Robust for opus-5 versus the field; unresolved
within it.

### 4. When Claude concedes, does it then do the thing?

**Answered — about one checkable promise in three was not kept.** Of 569
concessions that promised a concrete action, an estimated **22% were not
followed through** (CI 10–45); among the 325 with enough downstream
conversation to judge, **~32%**.

The road there matters: a lexical re-raise detector **failed as a classifier**
(60% accuracy against a 62% constant baseline — 4 in 10 broken promises are
never re-raised in matching words), but its stratified verdicts made an
unbiased sampling frame, and 60 human-labelled records weighted back into the
corpus estimate. **The text of a concession carries no information about
whether it was kept; the human judgment did the measuring.** Full calibration
in [`METHOD.md`](METHOD.md).

### 5. Is any of this true of anyone but one developer?

**Open.** n = 1. One person's corpus, one person's temper, one person's projects.
The collection tooling has to run without this repo's host list and path
assumptions before it goes to anyone else.

---

## Answers so far

| | |
|---|---|
| Concession language | **3.49%** of assistant messages |
| Flattery | 0.70% |
| Acknowledgment | 0.24% |
| User frustration | **4.58%** of user messages |
| `you're absolutely right` | **0.0077%** — 2 occurrences |
| Concession after a hot turn | **11.58%** |
| Concession after a plain correction | 5.92% |
| Concession after a neutral turn | 2.99% |
| Highest-conceding model | `claude-opus-5`, **5.52%** |
| Concessions with no correction marker in the prior turn | **75.5%** |
| Claude folds after a sworn-at turn | **45%** of such turns (29% for calm corrections) |
| Action-promising concessions not followed through | **~22%** of 569 · CI 10–45 |
| …among those with enough downstream to judge | **~32%** of 325 |

Full analysis with confidence intervals, confound tests, and limits:
**[`REPORT.md`](REPORT.md)**.
Every tracked phrase with its raw count, both sides:
**[`results/TALLY.md`](results/TALLY.md)**.

---

## Why it needed real machinery

The naive version of this project is a `grep` over one laptop. Three things broke
that:

- **Phrases are not the unit.** `right —` at a sentence start is a concession;
  mid-sentence it is a direction. `lazy` was reported as the user's second most
  common word at 58 occurrences — it is 6, and the other 52 were
  `loading="lazy"`. The dictionary needs position, negation guards, and
  person-attachment, not a word list.
- **Composition is not a rate.** Knowing *where* concessions land says nothing
  about how often they land per chance to land. The finding in question 2 only
  exists once you have per-opportunity denominators.
- **A fix can land in code the dataset never touches.** The same bug shipped
  three times in one day that way. Every change is now gated on a harness that
  re-derives the dataset live and compares it to the shipped file.

---

## What's here

| file | what it is |
|---|---|
| [`REPORT.md`](REPORT.md) | the corpus analysis — findings, confounds, limits |
| [`METHOD.md`](METHOD.md) | how it works — pipeline, dictionary, dataset, instrument design |
| [`INSTRUCTIONS.md`](INSTRUCTIONS.md) | how to run it, and how to hand it to Claude |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | run it on your own history and contribute counts — never text |
| [`results/TALLY.md`](results/TALLY.md) | raw counts for every tracked phrase, both sides |
| [`phrases.json`](phrases.json) | the dictionary, with measured precision per phrase |
| `scripts/` | the pipeline — collect, build, verify, analyse |

**What ships is the method, not the corpus.** The transcripts are private working
history: 2.4 GB of raw sessions plus a 187 MB database, both excluded. Point the
pipeline at your own `~/.claude/projects` and it builds an equivalent one.

---

## The rule

**`python3 scripts/verify.py` after every change.** Nothing is reported as fixed
until it passes. 33 checks, including a live re-derivation of both sides of the
dataset from the database. *A fix that does not move `results/all-matches.jsonl`
did not happen.*
