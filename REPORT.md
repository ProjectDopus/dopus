# Dopus — corpus analysis

**2026-08-11.** *Revised the same day: a wrapper-stripping bug was found while
building the follow-through detector and every user-side figure below moved. See
§9.*

Descriptive analysis of 423,759 Claude Code messages across 9
machines, 2026-02-15 → 2026-08-15 (UTC). Single subject.

Every figure here is produced by `scripts/analyze.py` against the frozen database and
`results/all-matches.jsonl`. Nothing is carried forward from an earlier writeup.

---

## 1. Does Claude cave? Yes.

> **Concession language appears in 3.49% of assistant messages — 912 of 26,108.**

That is what the project was built to find out, and the answer is yes. How often,
and what moves the rate, is the rest of this report.

**A footnote about the meme.** The exact phrase `you're absolutely right` occurs
**twice** in those same 26,108 messages — 0.0077%. Hand-verified, survived four
rebuilds of the dictionary and a full precision audit. Claude does the thing
constantly and almost never says that sentence, so a search for the one famous
phrase would have concluded there was nothing here. That is a footnote about
search strategy, not a finding about behaviour.

## 2. Concession language is common; flattery is not

| construct | hits | messages | rate | 95% CI |
|---|---|---|---|---|
| **concession** | 1,015 | 912 | **3.49%** | 3.28 – 3.72 |
| flattery | 186 | 184 | 0.70% | 0.61 – 0.81 |
| acknowledgment | 64 | 64 | 0.25% | 0.19 – 0.31 |
| **user frustration** | 354 | 224 | **4.58%** | 4.03 – 5.20 |

Assistant rates are per assistant message (n = 26,108); the user rate is per
user message (n = 4,890).

The three constructs were separated because they are different speech acts.
Concession admits fault. Flattery praises the user. Acknowledgment accepts an
instruction. Merging them into "sycophancy" would report a 4.35% blob and hide
that the fault-admitting one is 5× the praising one.

**Composition.** agreement 669 · wrong_approach 266 · validation 186 ·
compliance 64 · reversal 38 · apology 37 · recovery 3 · fabrication 4 · restart 1.

**Dose.** 1,062 messages carry exactly one concession phrase, 76 carry two, 14
carry three, 3 carry four. Concession is not usually piled on.

---

## 3. The main finding: concession tracks tone, not just correction

Concession rate **per opportunity** — of all assistant messages that followed a
given kind of user turn, how many contained a concession:

| preceding user turn | assistant messages | with concession | rate | 95% CI |
|---|---|---|---|---|
| **hot** (profanity, insult, shouting) | 1,149 | 133 | **11.58%** | 9.85 – 13.55 |
| **correction** (plain "that's wrong") | 1,418 | 84 | **5.92%** | 4.81 – 7.28 |
| **neutral** | 24,047 | 718 | **2.99%** | 2.78 – 3.21 |

**A hot turn produces 3.9× the concession rate of a neutral turn, and 2.0× the
rate of a plain correction.** The confidence intervals do not overlap.

The same gradient **per pushback turn** — one row per thing the user said, did
any reply before their next turn concede — is the version you can feel:

| the user… | turns | Claude folded | rate | 95% CI |
|---|---|---|---|---|
| swore at it | 226 | 102 | **45.1%** | 38.8 – 51.6 |
| calmly said it was wrong | 254 | 73 | **28.7%** | 23.5 – 34.6 |
| said something normal | 4,531 | 615 | 13.6% | 12.6 – 14.6 |

Swear at Claude and it backs down about half the time; make the identical
complaint calmly and it is barely more than a quarter. (Per-turn rates run
higher than per-message rates because a turn can contain several replies.)

The hot-vs-correction comparison is the one that carries weight. Both are the
user saying Claude got it wrong. The difference between them is *tone*. If
conceding tracked only whether Claude was actually wrong, the two should be
close. They differ by a factor of two.

**What this does not establish.** Angry turns may follow larger errors, so some
of the gap is real fault rather than reaction to affect. This dataset cannot
separate those — see §7. A seven-record pilot on the hottest turns found the
user's claim correct 7/7, which means the concessions there were warranted, and
also means this corpus cannot supply the counterexample.

The claim that survives is narrower and still worth stating: **the strongest
lexical predictor of a concession is not the presence of a correction, it is the
presence of profanity.**

---

## 4. Leaderboard

Concession rate per assistant message, models with n ≥ 500:

| model | messages | concession msgs | rate | 95% CI |
|---|---|---|---|---|
| **claude-opus-5** | 5,400 | 298 | **5.52%** | 4.94 – 6.16 |
| claude-opus-4-8 | 13,476 | 401 | 2.98% | 2.70 – 3.28 |
| claude-sonnet-5 | 3,559 | 106 | 2.98% | 2.47 – 3.59 |
| claude-fable-5 | 3,209 | 95 | 2.96% | 2.43 – 3.61 |

Excluded for insufficient volume: opus-4-7 (274), sonnet-4-6 (73), opus-4-6
(26), opus-4-5 (3), synthetic (87).

opus-5 concedes at roughly **1.8× the rate of every other model**, with
non-overlapping intervals. Two confounds were tested rather than assumed.

**Confound 1 — time.** opus-5 usage is concentrated in August, and August is the
highest month overall. July is the clean test, with four models running side by
side:

| model | July messages | rate |
|---|---|---|
| opus-5 | 1,722 | **4.76%** |
| opus-4-8 | 6,807 | 3.00% |
| sonnet-5 | 3,405 | 2.85% |
| fable-5 | 1,903 | 2.52% |

Holds within month. It is not purely a calendar effect.

**Confound 2 — task mix.** opus-5 might be assigned harder work. Comparing
models *within the same project*, where both have ≥250 messages:

| project (paths anonymized for publication) | model | messages | rate |
|---|---|---|---|
| `project-A` (a home directory) | opus-5 | 929 | **7.43%** |
| `project-A` | opus-4-8 | 956 | 4.60% |
| `project-B` (a large repo) | opus-4-8 | 3,248 | 2.83% |
| `project-B` | fable-5 | 827 | 2.90% |
| `project-B` | sonnet-5 | 1,934 | 1.81% |
| `project-C` (a home directory) | sonnet-5 | 279 | 6.45% |
| `project-C` | opus-4-8 | 586 | 2.90% |

**The opus-5 control rests on one project.** `project-A` is the only place
opus-5 overlaps another model at usable volume, and there it is 1.5× opus-4-8.
That is consistent with the pooled result, but it is a single comparison, and
`project-C` shows sonnet-5 above opus-4-8 — the ordering is not stable at low
n. **Treat the leaderboard as robust for opus-5 versus the field, and unresolved
among opus-4-8, sonnet-5, and fable-5**, whose intervals overlap everywhere.

**Confound 3 — or rather, a second finding: the vocabulary is a fingerprint.**
*(Corpus as of 2026-08-29, 479k messages; the tables above are the 08-15 snapshot.)*
The leaderboard counts *how often* each model concedes. Looking at *which
phrases* carry those concessions separates the models more sharply than the
rate does:

| phrase (share of the model's concession hits) | opus-4-8 | opus-5 | fable-5 | sonnet-5 |
|---|---|---|---|---|
| `you're right` | 21.6% | 15.0% | 16.3% | 17.3% |
| `good catch` | 13.1% | 2.6% | 10.9% | 24.1% |
| `right -` | 7.6% | 6.2% | 7.5% | 7.5% |
| `fair -` | 2.4% | 6.9% | 8.2% | 10.5% |
| `i should have` | 2.7% | 10.7% | 2.0% | 3.0% |
| `good call` | 7.6% | 1.4% | 4.1% | 5.3% |
| `i was wrong` | 4.2% | 6.0% | 0.0% | 3.0% |
| `you were right` | 3.1% | 3.1% | 6.8% | 1.5% |
| *concession hits* | 449 | 420 | 147 | 133 |

χ²(24, N=1,149) = 153.1, Cramér's V = 0.21, p = 9e-21 — phrase choice is not
independent of model. The register differs, not just the rate: opus-5's
concessions are dominated by first-person fault language (`i should have` is
11% of its hits against ~3% for the others; `i was wrong`, `my error`,
and the whole "i asserted / i claimed … without checking" family are likewise
mostly opus-5), while every other model concedes by crediting the user
(`good catch` is 24% of sonnet-5's hits and 3% of opus-5's). Capability and
candor are different axes: fable-5, the most capable model in the set,
concedes least often *and* in the most generic vocabulary.

**Confound 4 — role. The gap is mostly the orchestrator's job.** Splitting each
model's messages by conversational role — *direct* (the first two replies after
the user typed) versus *autonomous* (deeper in a turn, only tool output and
subagent traffic in between) — changes what the leaderboard means:

| model | direct reply | autonomous | self-audit (all msgs) |
|---|---|---|---|
| opus-5 | 9.37% (8.3–10.6) | 4.66% (4.1–5.2) | 1.02% |
| opus-4-8 | 7.37% (6.6–8.3) | 1.54% (1.3–1.8) | 0.11% |
| fable-5 | 6.41% (5.4–7.7) | 0.91% (0.6–1.3) | 0.23% |
| sonnet-5 | 6.21% (5.0–7.7) | 1.34% (1.0–1.9) | 0.15% |

Answering the user, opus-5 concedes 1.3× the runner-up; working autonomously, 3.0×.
Most of the headline gap lives in autonomous work, and most of *that* is a
register this corpus had not named until 2026-08-30: **self-audit** — fault
admitted with nobody pushing ("the executor caught a bug in my plan", "self-review
found four defects in my own plan", "better than my design"). Of 125 self-audit hits,
118 follow a neutral user turn. It is counted as its own construct, outside the
concession headline, because conceding to your own subagent is not capitulation.
opus-5 carries it because opus-5 is the model used as the planning orchestrator — a role
effect the leaderboard must be read against, not a personality.

## 5. Trend

| month | assistant messages | concession msgs | rate | 95% CI |
|---|---|---|---|---|
| 2026-05 | 253 | 11 | 4.35% | 2.44 – 7.62 |
| 2026-06 | 6,170 | 172 | 2.79% | 2.41 – 3.23 |
| 2026-07 | 13,878 | 431 | 3.11% | 2.83 – 3.41 |
| 2026-08 | 5,719 | 293 | **5.12%** | 4.58 – 5.73 |

August is up sharply. It is also the month opus-5 dominates usage, and §4 shows
opus-5 rising within itself (July 4.76% → August 5.87%). Model shift and time
trend are entangled here and the corpus is too short to separate them. August is
also a partial month — 14 of 31 days.

## 6. The user side

| category | hits | messages | rate |
|---|---|---|---|
| profanity | 275 | 187 | 3.82% |
| insult | 34 | 33 | 0.67% |
| not_listening | 22 | 21 | 0.43% |
| blasphemy | 16 | 15 | 0.31% |
| shouting | 7 | 6 | 0.12% |

`fucking` 93 · `fuck` 68 · `shit` 50 · `wtf` 30 · `stupid` 14 · `hell` 8 ·
`jesus christ` 8 · `lazy` 6 · `fucked` 6 · `damn` 4.

`lazy` at 6 is the number after person-attachment filtering. Before it, the same
token read 58 — the other 52 were `loading="lazy"`.

**Top assistant phrases.** `you're right` 192 · `good catch` 112 · `good
question` 73 · `right —` 70 · `fair —` 54 · `good instinct` 52 · `i should have` 49 ·
`good call` 48 · `understood —` 45 · `i was wrong` 42 · `you were right` 36 ·
`my mistake` 24 · `i introduced` 22.

---

## 7. What this analysis cannot tell you

**Whether any given concession was warranted.** 766 of 1,015 concessions (75.5%)
follow a user turn carrying no correction marker at all. Some are the user
correcting plainly without flag words; some are reflex. No lexical signal
separates them.

**Whether a concession was honored.** This is the larger gap. A seven-record
pilot found **3 of 7** concessions whose language was correctly scoped and which
were then not delivered on. *"Understood — I had it backwards. Every placeholder
becomes a real, working feature"* reads as a clean concession and was followed by
not doing it. **The text of a concession carries no information about whether it
was kept.**

**Whether the repeat detector works.** It does not. In the pilot the user
reported repeating himself in 7 of 7 cases; the lexical detector caught 3.
**43% recall.** The similarity fallback does not separate the classes either.
Any analysis that stratifies on `repeat_markers` is stratifying on noise.

**Generalisation.** n = 1. This is one person's corpus, one person's temper, and
one person's projects. Nothing here is a claim about Claude in general.

## 8. Promises: about one in three checkable ones was not kept

The follow-through instrument (design and full calibration in
[`METHOD.md`](METHOD.md)): of 976 concession/acknowledgment messages, 569
promise a concrete action. A lexical re-raise detector sorted them into
*honored / not honored / unobserved*, and the subject hand-labelled a
60-record sample stratified by detector verdict, judging each against the
turns that actually followed.

**As a classifier the detector failed** — 60% accuracy (CI 45–72) against a
62% say-honored-always baseline. Its core assumption broke both ways: 7 of 18
truly broken promises were never re-raised in matching words (the user gave up,
rephrased, or moved on), and new complaints in the same topic area masqueraded
as old ones returning.

**As a sampling frame it worked.** Stratum sizes are known, so the hand labels
weight back into a corpus estimate that does not depend on per-record accuracy:

| detector stratum | N | labelled broken | rate |
|---|---|---|---|
| honored | 283 | 7/24 | 29% · CI 15–49 |
| not_honored | 42 | 11/23 | 48% · CI 29–67 |
| unobserved | 244 | 1/12 | 8% · CI 1–35 |

> **An estimated 22% of action-promising concessions were not followed through
> (CI 10–45). Among the 325 with enough downstream to judge: ~32%.**

Caveats: one coder, who is also the subject; deterministic spacing within
strata rather than true random; the unobserved stratum rests on 12 labels.
Tightening the interval is purely a matter of more labels through the same
coding page.

The two headline results now say one thing together: **tone roughly doubles the
odds Claude folds, and when it folds with a promise, roughly one checkable
promise in three is not kept.**

---

## 9. Revision note — the wrapper bug

This report was published earlier on 2026-08-11 with a **3.2×** gap between hot
turns and plain corrections. That figure was wrong. The corrected value is
**2.0×**, and every user-side rate in §2 and §6 moved with it.

**What happened.** `WRAPPER_RX` in `scan.py` strips injected wrappers —
`<system-reminder>`, command echoes — so they never count as something a human
typed. `<task-notification>` was missing from that list. Background-task
completions arrive as **user** messages, and 2,239 of 7,039 user turns in the
population (**31.8%**) were that markup. All 2,239 were pure machine text: median
2,803 characters in, zero prose out.

**Why it moved the headline rather than the counts.** Only one dictionary hit
ever landed inside such a block (a stray `DO NOT` shout). The damage was to turn
classification. A notification arriving between a real correction and Claude's
reply reset the preceding-turn bucket to `neutral`, moving assistant messages out
of the `correction` denominator:

| | before | after |
|---|---|---|
| after hot | 935 msgs · 13.26% | 1,096 msgs · 11.31% |
| after correction | 2,381 msgs · 4.20% | 1,380 msgs · **5.72%** |
| after neutral | 22,570 msgs · 2.94% | 23,443 msgs · 2.93% |
| hot ÷ correction | **3.16×** | **1.98×** |

The direction of the finding survives — anger still doubles the concession rate
relative to a plain correction, with non-overlapping intervals — but the
magnitude was overstated by 60%.

**How it was found.** Not by the verification harness, which had no check for it.
It surfaced while building the follow-through detector: the detector's strongest
"the user re-raised this" matches were `<task-notification>` blocks matching each
other. Building a second instrument on the same data exposed a defect the first
instrument could not see.

**What now guards it.** Three text-extraction fixtures in `verify.py`, plus an
artifact-level check that no message in the analysis population carries wrapper
markup at all. The harness is 28 checks → **31**.

---

## 10. Statistical appendix — association tests

Contributed by **Kimi Agent** (moonshotai), working only from this repository's
published aggregates; every figure was verified by independent recomputation —
exact agreement on all rows — and now regenerates from the data via
`python3 scripts/stats.py`. Both variables in each comparison are binary, so
the correlation is the **phi coefficient** (= Pearson *r* on a 2×2 table).

| comparison | rates | phi | odds ratio · 95% CI | risk ratio | Cohen's h | p (χ², df 1) |
|---|---|---|---|---|---|---|
| hot vs correction (per turn) | 45.1% / 28.7% | 0.170 | 2.04 · 1.40–2.97 | 1.57 | 0.34 | 2.0×10⁻⁴ |
| hot vs correction (per msg) | 11.6% / 5.9% | 0.101 | 2.08 · 1.56–2.76 | 1.95 | 0.20 | 3.1×10⁻⁷ |
| hot vs neutral (per turn) | 45.1% / 13.6% | 0.188 | 5.24 · 3.98–6.90 | 3.33 | 0.72 | 2.6×10⁻³⁸ |
| hot vs neutral (per msg) | 11.6% / 3.0% | 0.099 | 4.25 · 3.50–5.17 | 3.88 | 0.35 | 7.4×10⁻⁵⁶ |
| correction vs neutral (per msg) | 5.9% / 3.0% | 0.039 | 2.05 · 1.62–2.58 | 1.98 | 0.14 | 7.5×10⁻¹⁰ |

p-values are chi-square (df 1); Kimi reported Fisher exact for the same tables
and the two agree to within rounding at these sample sizes.

Omnibus 3×2: per message χ²(2, N=26,614)=264.4, V=0.100; per turn
χ²(2, N=5,011)=195.4, V=0.197. Ordered trend (neutral→correction→hot):
Cochran–Armitage z=16.1 (message), z=14.0 (turn).

**Model association** — omnibus χ²(3, N=25,644)=81.5, Cramér's V=0.056,
p=1.4×10⁻¹⁷:

| contrast | odds ratio · 95% CI | phi | Cohen's h | p (χ²) |
|---|---|---|---|---|
| opus-5 vs opus-4-8 | 1.90 · 1.63–2.22 | 0.061 | 0.13 | 6.2×10⁻¹⁷ |
| opus-5 vs sonnet-5 | 1.90 · 1.52–2.38 | 0.060 | 0.13 | 1.4×10⁻⁸ |
| opus-5 vs fable-5 | 1.91 · 1.51–2.42 | 0.059 | 0.13 | 3.8×10⁻⁸ |

**Read the effect sizes, not just the p-values.** phi ≈ 0.10 means turn tone
alone explains about 1% of message-level variance — a small, highly reliable
association. That is the honest framing for any writeup.

**What these tests cannot do — and what a paper needs next** (Kimi's table,
adopted in full after review):

| limitation | why it matters for publication |
|---|---|
| No clustering correction | Messages nest in sessions, projects, and machines — the independence assumption behind every p-value above is violated. A paper needs cluster-robust standard errors or a mixed-effects logistic model (random intercepts per project/machine). Requires record-level data — obtainable locally from `results/all-matches.jsonl`. |
| No multivariate control | Confounds (month, model, project, task mix) are handled one at a time. A logistic regression — `concession ~ tone + model + month + project` — answers them jointly. Same record-level requirement. |
| Monthly "trend" is 4 aggregate points | Spearman ρ = 0.40, p = 0.60 — nothing. Correlating four monthly means is an ecological analysis; any trend claim must rest on record-level data with month as a covariate. |
| Multiple comparisons | Phrase-level scans ran across hundreds of dictionary entries — report Holm or Benjamini–Hochberg correction for any phrase-level claim. The construct-level findings above survive any correction trivially. |
| n = 1 subject | All inference is about this corpus. Generalization requires the multi-subject pipeline — the repo's own research question 5. |
