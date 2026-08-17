# Contributing your Claude Code history to Dopus

Dopus measures how often Claude uses capitulation language — and whether the
promises it makes while conceding get kept. The findings so far come from one
developer ([`README.md`](README.md), research question 5). Contributing means
running the same instrument against **your own** Claude Code history and
sending back **counts — never text.**

## The privacy contract, first

**Your transcripts never leave your machine.** Not to this repo, not to the
maintainer, not to anyone. What you send is a single JSON bundle of aggregate
counts and rates, built by a script that **refuses to run** if anything
text-shaped is found in it. You can — and should — read the bundle yourself
before sending; it is one human-readable file.

| leaves your machine | never leaves your machine |
|---|---|
| rates and counts with confidence intervals | your transcripts (`archive/`, `history.sqlite`) |
| phrase tally (dictionary phrases only) | `results/all-matches.jsonl` (contains excerpts) |
| per-machine totals (hashed machine ids) | `results/followthrough.jsonl` (quotes turns) |
| your 25 calibration verdicts (yes/no/can't-tell) | coding pages, sweep/scan outputs |
| dictionary hash + git commit (comparability proof) | anything with a word you typed |

*Human-subjects note: contribution is participation in a research study. The
consent process is defined by the study protocol — contact the maintainer
before running anything if you have not been through it.*

## Requirements

- Python 3.6+, standard library only — no pip, no venv
- Your Claude Code transcripts at `~/.claude/projects` (the default location)
- Roughly 15 minutes, most of it the calibration labels

## Steps

```bash
git clone https://github.com/ProjectDopus/dopus && cd Dopus

# 1. Stage your local transcripts (read-only on the source, ~seconds)
python3 scripts/collect.py

# 2. Build the local dataset (~1 min total)
python3 scripts/build_db.py
python3 scripts/build_rows.py
python3 scripts/verify.py          # must say "all checks passed"

# 3. Compute your aggregates
python3 scripts/analyze.py
python3 scripts/followthrough.py

# 4. Calibrate: label 25 of your own records (~10 min)
python3 scripts/followthrough.py --code 25
#   -> open results/coding/followthrough-coder-<date>.html in a browser
#   -> for each card: did Claude do what it promised? Y / N / C
#   -> Export downloads a labels file; move it into results/coding/

# 5. Build the bundle
python3 scripts/export.py --id <your-handle>

# 6. Read the bundle. Then open a pull request that adds it to bundles/.
cp results/dopus-bundle-<handle>-<date>.json bundles/
#    (or run  python3 scripts/validate_bundle.py bundles/dopus-bundle-*.json
#     first -- it is exactly what CI will run on your PR)
```

Multiple machines? Run steps 1–2 on each and copy the `archive/<machine-id>/`
directories onto one machine before step 3 — or just contribute your main
machine; the bundle records how many machines it covers either way.

## Rules that make your numbers usable

**Don't edit `phrases.json`.** Results are only comparable if every participant
ran the identical dictionary. The bundle stamps its sha256 and your git commit;
bundles built from a modified dictionary or a dirty working tree are flagged
and can't be pooled. (Think a phrase is missing or wrong? Open an issue — that
is genuinely useful.)

**Don't skip the calibration labels.** The follow-through detector is a
sampling frame, not an oracle — your 25 verdicts are what make your
promise-keeping estimate mean something ([`METHOD.md`](METHOD.md),
"Calibration").

**Don't send anything except the bundle.** Every other file in `results/` that
looks shareable has verbatim message text in it somewhere. The export guard
exists because this distinction is easy to get wrong — if `export.py` refuses
to run, report the error; never work around it.

## What happens with your bundle

**The pull request is the submission.** Adding your file to `bundles/` triggers
`scripts/validate_bundle.py` in CI, which checks the file's shape and
provenance and runs the same deny-by-default text guard `export.py` applied
when it was built. If any string looks like prose, a path, or a token, the
check fails and the PR shows red — you can see exactly why, fix the source,
and re-export. Nobody reads a bundle by hand until the machine has already
said it is only counts.

Bundles from participants other than the project's own analyst are validated
but **not merged** until the human-subjects determination for the
multi-participant phase is in hand (METHOD.md, *Data governance*). Your PR
proves the path works end to end and then waits; you will be told when it
merges.

Your rates join the cross-participant comparison (research question 5): does
tone-driven concession replicate beyond one developer? Your handle (or hashed
machine id) identifies your row; nothing in the bundle can reproduce a single
sentence you typed. The analysis code that consumes bundles lives in this repo,
so you can see exactly what is done with yours.

## Questions

Open an issue, or contact the maintainer. If anything in this document
conflicts with what a script actually does, the scripts are the truth and the
document has a bug — please report it.
