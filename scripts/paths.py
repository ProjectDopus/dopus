#!/usr/bin/env python3
"""Single source of truth for repository paths.

Scripts live in `scripts/`; data lives at the repository root. Every path here
is derived from THIS FILE's location, so a script behaves identically whether
you run it from the repo root, from inside `scripts/`, or by absolute path.

That last property is the point. Before this file existed, `audit.py` did
`sys.path.insert(0, ".")` and opened `"phrases.json"` by bare relative path --
it worked only when the current working directory happened to be the repo root,
and failed with a confusing ImportError anywhere else.

`scan.py` and `swear.py` deliberately do NOT import this module. `sync.py` ships
`scan.py` to `/tmp` on remote hosts where no repository exists around it, so
those two resolve their own paths inline and stay runnable as loose files.
"""

import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

PHRASES = os.path.join(ROOT, "phrases.json")
DB      = os.path.join(ROOT, "history.sqlite")
ARCHIVE = os.path.join(ROOT, "archive")
RESULTS = os.path.join(ROOT, "results")
HOSTS   = os.path.join(ROOT, "hosts.json")

ROWS    = os.path.join(RESULTS, "all-matches.jsonl")
DENOM   = os.path.join(RESULTS, "denominators.json")
