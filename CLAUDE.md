# Dopus — Claude Code transcript analysis

## THE RULE

**Run `python3 scripts/verify.py` after every change. No exception.**

Nothing is reported as fixed until verify passes. A claim of "fixed" without a
verify run is worth nothing and should be treated that way.

```
change a detector or phrases.json
  → python3 scripts/build_rows.py
  → python3 scripts/verify.py            # 48 checks, ~40s
```

## Why this rule exists

The same bug shipped three times in one day: a fix landed in one code path while
the dataset was produced by another. `SHOUT_RX`, `shouting()`, and `USER_GUARD`
were each "fixed" and each verified by running the edited file rather than the
artifact that matters. Every user-side number was wrong until this was caught.

`scripts/verify.py` re-derives the dataset live from the DB and compares it to
`results/all-matches.jsonl`. A fix that does not move that file did not happen.

## Pipeline

```
scripts/sync.py        pull new/grown transcripts (diff-based, never deletes, verifies on disk)
scripts/build_db.py    archive/ -> history.sqlite   (every message, flags preserved)
scripts/build_rows.py  history.sqlite -> results/all-matches.jsonl + denominators.json
scripts/verify.py      invariant checks (--ci = data-free subset; GitHub Actions gates PRs on it)
scripts/validate_bundle.py  intake gate for bundles/ (CI runs it on any PR touching bundles/)
```

Support: `scripts/scan.py` (matchers), `scripts/swear.py` (user-side detectors), `scripts/audit.py`
(enumerate + evenly sample any phrase), `scripts/sweep.py` (find untracked constructions),
`scripts/followthrough.py` (did a promised action get honored -- **built, NOT calibrated,
do not quote its numbers**), `scripts/webdata.py` (regenerates `../Dopus-web/data.js`,
the only figures the public site carries; run after every sync).

Retired scripts, paper drafts, IRB material, and internal notes live in the
private `ProjectDopus/dopus-lab` repo (locally `~/GitHub/dopus-lab`) — this
repo is public and carries the instrument only.

## Design rules learned the hard way

- **Store everything, filter at query time.** Exclusions are `WHERE` clauses with
  visible flags, never decisions baked into a scanner.
- **Flat phrase lists are the wrong shape.** Position (`assistant_openers`),
  denial twins (`assistant_negation_guard`), productive families
  (`assistant_patterns`), and person-attachment (`USER_REQUIRE`) each exist
  because a bare string was mixed.
- **Adjudicate on full enumeration, not first-N.** Rejecting `i introduced` on a
  3-sample draw was wrong; it is 71% genuine. Measured precision lives in
  `phrases.json` under `_precision_audit`.
- **Ship scripts as files.** Quoting a heredoc through two SSH layers silently
  broke sync for a day.
- **A judgment call is not a bug fix.** Anything that changes what counts gets
  surfaced as a decision with its cost, not reported as cleanup.
