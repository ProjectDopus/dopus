#!/usr/bin/env python3
"""
Build the frozen working database from the archive.

Usage:  python3 scripts/build_db.py [archive_dir] [out.sqlite]

Stores EVERY user/assistant message with its flags rather than filtering on the
way in. Every exclusion this project has argued about -- sidechain subagents,
compaction summaries, this research session, tool-only messages -- becomes a
visible WHERE clause instead of a decision buried in a scanner that has to be
remembered and re-applied over SSH.

Also records a sha256 per file, so "frozen" is provable rather than asserted.
"""

import hashlib
import json
import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths as P
import scan as S

SCHEMA = """
PRAGMA journal_mode=OFF;
PRAGMA synchronous=OFF;

CREATE TABLE files (
  file_id     INTEGER PRIMARY KEY,
  machine_id  TEXT NOT NULL,
  root_slug   TEXT NOT NULL,   -- which .claude store on that machine
  project     TEXT,            -- transcript's project dir
  session     TEXT,            -- session uuid (filename)
  relpath     TEXT NOT NULL,
  bytes       INTEGER,
  sha256      TEXT,
  is_subagent INTEGER          -- lives under a subagents/ directory
);

CREATE TABLE messages (
  msg_id        INTEGER PRIMARY KEY,
  file_id       INTEGER NOT NULL REFERENCES files(file_id),
  line_no       INTEGER,
  uuid          TEXT,
  parent_uuid   TEXT,
  fingerprint   TEXT,          -- sha1(uuid)[:16], for cross-machine dedup
  side          TEXT,          -- 'user' | 'assistant'
  ts            TEXT,
  model         TEXT,
  is_sidechain  INTEGER,
  is_compact    INTEGER,
  is_meta       INTEGER,
  is_visible_only INTEGER,
  has_text      INTEGER,       -- 0 for tool-only turns
  text_len      INTEGER,
  text          TEXT
);
"""

INDEXES = """
CREATE INDEX i_msg_side  ON messages(side);
CREATE INDEX i_msg_fp    ON messages(fingerprint);
CREATE INDEX i_msg_model ON messages(model);
CREATE INDEX i_msg_ts    ON messages(ts);
CREATE INDEX i_msg_file  ON messages(file_id);
CREATE INDEX i_msg_flags ON messages(is_sidechain, is_compact, has_text);
CREATE INDEX i_file_mid  ON files(machine_id);
"""


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    arc = sys.argv[1] if len(sys.argv) > 1 else P.ARCHIVE
    out = sys.argv[2] if len(sys.argv) > 2 else P.DB
    if os.path.exists(out):
        os.remove(out)

    db = sqlite3.connect(out)
    db.executescript(SCHEMA)

    files = []
    for dp, _d, names in os.walk(arc):
        for n in names:
            if n.endswith(".jsonl") and not n.startswith("._"):
                files.append(os.path.join(dp, n))
    files.sort()

    fid = 0
    mid_total = 0
    bad_lines = 0
    for path in files:
        rel = os.path.relpath(path, arc)
        parts = rel.split(os.sep)
        machine_id = parts[0]
        root_slug = parts[1] if len(parts) > 2 else ""
        project = parts[2] if len(parts) > 3 else ""
        session = os.path.basename(path)[:-6]
        is_sub = 1 if "subagents" in parts else 0

        fid += 1
        db.execute(
            "INSERT INTO files VALUES (?,?,?,?,?,?,?,?,?)",
            (fid, machine_id, root_slug, project, session, rel,
             os.path.getsize(path), sha256(path), is_sub))

        rows = []
        with open(path, encoding="utf-8", errors="replace") as fh:
            for ln, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    bad_lines += 1
                    continue
                if not isinstance(e, dict) or e.get("type") not in ("user", "assistant"):
                    continue
                text = S.message_text(e)
                stripped = text.strip()
                uid = e.get("uuid")
                rows.append((
                    fid, ln, uid, e.get("parentUuid"),
                    S.fingerprint(uid) if uid else None,
                    e.get("type"), e.get("timestamp"),
                    (e.get("message") or {}).get("model"),
                    1 if e.get("isSidechain") else 0,
                    1 if e.get("isCompactSummary") else 0,
                    1 if e.get("isMeta") else 0,
                    1 if e.get("isVisibleInTranscriptOnly") else 0,
                    1 if stripped else 0,
                    len(stripped),
                    re.sub(r"\s+", " ", text).strip() if stripped else None,
                ))
        if rows:
            db.executemany(
                "INSERT INTO messages (file_id,line_no,uuid,parent_uuid,fingerprint,"
                "side,ts,model,is_sidechain,is_compact,is_meta,is_visible_only,"
                "has_text,text_len,text) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            mid_total += len(rows)
        if fid % 400 == 0:
            db.commit()
            print("  ... %d files, %d messages" % (fid, mid_total))

    db.commit()
    db.executescript(INDEXES)
    db.commit()

    print()
    print("files    : %d" % fid)
    print("messages : %d" % mid_total)
    print("malformed lines skipped : %d" % bad_lines)
    print("db       : %s (%.1f MB)" % (out, os.path.getsize(out) / 1e6))
    db.close()


if __name__ == "__main__":
    main()
