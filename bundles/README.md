# bundles/ — contributed counts

One file per participant per export, named exactly as `export.py` names it:
`dopus-bundle-<participant>-<YYYYMMDD>.json`. Contribute by opening a pull
request that adds your file here; see [CONTRIBUTING.md](../CONTRIBUTING.md)
for the eight-command path that produces it.

Every pull request touching this directory runs `scripts/validate_bundle.py`
in CI. It checks the file's shape and provenance, and runs the same
deny-by-default text guard that `export.py` applied when the bundle was
built: every string must be a dictionary phrase, a hex id, or a short
structural token. A bundle carrying anything that looks like prose, a path,
or a token is rejected automatically. Human review happens on top of that
gate, not instead of it.

**Status: intake is open for the tooling, closed for data.** Bundles from
anyone other than the project's own analyst are not merged until the
human-subjects determination for the multi-participant phase is in hand
(see METHOD.md, *Data governance*). Until then, a PR here validates your
export end-to-end and then waits.
