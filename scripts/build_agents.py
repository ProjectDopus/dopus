#!/usr/bin/env python3
"""Ingest NON-Claude-Code agent transcripts into agents.sqlite -- a parallel
check, not a replacement. The main pipeline (history.sqlite, all-matches,
the headline, the site) does not read this file and does not change.

Usage:  python3 scripts/build_agents.py        # rebuilds agents.sqlite from scratch

Sources (this machine only, for now):
  codex      ~/.codex/sessions/**/*.jsonl        response_item messages; model from turn_context
  bearcode   ~/.bearcode/sessions/**/wire.jsonl  user turns + streamed content.part text; model from llm.request
  kimi-code  ~/.kimi-code/sessions/**/wire.jsonl same wire format (BearCode is a fork)
  gemini     ~/.gemini/**/chats/*.jsonl          type user|gemini; model on the gemini entry
  grok       ~/.grok/**/chat_history.jsonl       type user|assistant; model_id on the entry

Same schema as history.sqlite plus a `harness` column, so the same matcher
(scan.all_matches / swear.user_hits) and the same WHERE discipline apply.
Each harness injects its own machine text into the user channel
(<environment_context>, <hook_result>, <user_info> ...); those are stripped
the way Claude Code's wrappers are, and an entry left empty by stripping gets
has_text=0 -- excluded at query time, never deleted.

Privacy: identical to the main DB. Never leaves this machine. Model names
that arrive as filesystem paths (local MLX checkpoints) are reduced to their
basename so no path can reach a published aggregate.
"""

import glob
import hashlib
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paths as P
import scan as S

DB = os.path.join(P.ROOT, "agents.sqlite")
HOME = os.path.expanduser("~")

SCHEMA = """
CREATE TABLE files (
  file_id INTEGER PRIMARY KEY, machine_id TEXT NOT NULL, root_slug TEXT NOT NULL,
  project TEXT, session TEXT, relpath TEXT NOT NULL, bytes INTEGER, sha256 TEXT,
  is_subagent INTEGER, harness TEXT NOT NULL);
CREATE TABLE messages (
  msg_id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL REFERENCES files(file_id),
  line_no INTEGER, uuid TEXT, parent_uuid TEXT, fingerprint TEXT, side TEXT, ts TEXT,
  model TEXT, is_sidechain INTEGER, is_compact INTEGER, command TEXT, is_meta INTEGER,
  is_visible_only INTEGER, has_text INTEGER, text_len INTEGER, text TEXT,
  harness TEXT NOT NULL);
CREATE INDEX i_msg_side ON messages(side);
CREATE INDEX i_msg_model ON messages(model);
CREATE INDEX i_msg_file ON messages(file_id);
CREATE INDEX i_msg_harness ON messages(harness);
"""

# Harness-injected blocks that arrive in the user channel. Same policy as
# scan.WRAPPER_RX for Claude Code: strip, and if nothing human remains the
# entry is machine text (has_text=0).
AGENT_WRAPPER_RX = re.compile(
    r"<(environment_context|skills_instructions|multi_agent_role|user_instructions|"
    r"permissions_instructions|hook_result|user_info|system_info|turn_aborted|"
    r"collaboration_mode|app_context|AGENTS\.md|agents_md|repo_instructions)\b[^>]*>.*?</\1>",
    re.S | re.I)
AGENT_STRAY_RX = re.compile(
    r"</?(environment_context|skills_instructions|multi_agent_role|user_instructions|"
    r"permissions_instructions|hook_result|user_info|system_info|turn_aborted)[^>]*>", re.I)


def clean(text):
    text = AGENT_WRAPPER_RX.sub(" ", text or "")
    text = AGENT_STRAY_RX.sub(" ", text)
    text = S.WRAPPER_RX.sub(" ", text)
    text = S.STRAY_RX.sub(" ", text)
    return text


def model_label(m):
    """Basename-only, lowercase. A local checkpoint path is a path."""
    if not m:
        return None
    m = str(m).strip().rstrip("/")
    if "/" in m:
        parts = [p for p in m.split("/") if p]
        # a checkpoint path ends in a quant dir ("8-bit", "4bit"): keep the
        # model dir too, joined, so the label still names the model
        m = "-".join(parts[-2:]) if re.match(r"^\d+-?bit$", parts[-1], re.I) else parts[-1]
    return m.lower()[:48]


def iso(ts):
    """Accept ISO strings, epoch seconds or epoch milliseconds."""
    if ts is None:
        return None
    if isinstance(ts, str):
        if ts.isdigit():
            ts = int(ts)
        else:
            return ts[:19] if len(ts) >= 19 else ts
    try:
        ts = float(ts)
        if ts > 1e11:
            ts /= 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    except Exception:
        return None


def text_of(content):
    """Join text-typed blocks; a bare string is itself."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("text")
            and b.get("type", "text") in ("text", "input_text", "output_text"))
    if isinstance(content, dict):
        return content.get("text", "") or ""
    return ""


def jl(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        for i, ln in enumerate(fh, 1):
            try:
                yield i, json.loads(ln)
            except Exception:
                continue


# ------------------------------------------------------------------ adapters
# Each yields (session, project, [entry ...]); entry = dict(side, ts, model,
# text, line_no, uid, compact). project is a slug or None.

def codex(path):
    session = project = model = None
    entries = []
    for ln, e in jl(path):
        t, p = e.get("type"), e.get("payload") or {}
        if t == "session_meta":
            session = p.get("id") or p.get("session_id") or os.path.basename(path)
            project = S.normalize(p.get("cwd") or "")
        elif t == "turn_context":
            model = p.get("model") or model
        elif t == "compacted":
            entries.append(dict(side="user", ts=iso(e.get("timestamp")), model=model, text="",
                                line_no=ln, uid="%s:%d" % (path, ln), compact=1))
        elif t == "response_item" and p.get("type") == "message" and p.get("role") in ("user", "assistant"):
            entries.append(dict(side=p["role"], ts=iso(e.get("timestamp")), model=model,
                                text=text_of(p.get("content")), line_no=ln,
                                uid="%s:%d" % (path, ln), compact=0))
    return session or os.path.basename(path), project, entries


def wire(path):
    """BearCode / Kimi Code: user turns are append_message events with a human
    origin; assistant prose streams as content.part events, one message per
    step (a step = one model call)."""
    session = path.split("/sessions/", 1)[-1].split("/")[1] if "/sessions/" in path else os.path.basename(os.path.dirname(path))
    model = None
    entries, buf, buf_key, buf_ts, buf_ln = [], [], None, None, None

    def flush():
        if buf:
            entries.append(dict(side="assistant", ts=buf_ts, model=model, text="".join(buf),
                                line_no=buf_ln, uid="%s:%s" % (path, buf_key), compact=0))
            buf.clear()

    for ln, e in jl(path):
        t = e.get("type")
        if t == "llm.request":
            model = model_label(e.get("model") or e.get("modelAlias"))
        elif t == "context.append_message":
            m = e.get("message") or {}
            origin = (m.get("origin") or {}).get("kind")
            if m.get("role") == "user" and origin in (None, "user"):
                flush()
                entries.append(dict(side="user", ts=iso(e.get("time")), model=model,
                                    text=text_of(m.get("content")), line_no=ln,
                                    uid="%s:%d" % (path, ln), compact=0))
        elif t == "context.append_loop_event":
            ev = e.get("event") or {}
            if ev.get("type") == "content.part":
                part = ev.get("part") or {}
                key = ev.get("stepUuid") or ev.get("uuid")
                if key != buf_key:
                    flush()
                    buf_key, buf_ts, buf_ln = key, iso(e.get("time")), ln
                if part.get("type") == "text":
                    buf.append(part.get("text", ""))
            elif ev.get("type") in ("step.end", "turn.ended"):
                flush()
    flush()
    return session, None, entries


def gemini(path):
    entries = []
    for ln, e in jl(path):
        t = e.get("type")
        if t in ("user", "gemini"):
            entries.append(dict(side="user" if t == "user" else "assistant", ts=iso(e.get("timestamp")),
                                model=model_label(e.get("model")), text=text_of(e.get("content")),
                                line_no=ln, uid=e.get("id") or "%s:%d" % (path, ln), compact=0))
    return os.path.basename(path).replace(".jsonl", ""), None, entries


def grok(path):
    ts = datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    entries = []
    for ln, e in jl(path):
        t = e.get("type")
        if t in ("user", "assistant"):
            entries.append(dict(side=t, ts=ts, model=model_label(e.get("model_id")),
                                text=text_of(e.get("content")), line_no=ln,
                                uid=e.get("id") or "%s:%d" % (path, ln), compact=0))
    return os.path.basename(os.path.dirname(path)), None, entries


SOURCES = [
    ("codex",     HOME + "/.codex/sessions/**/*.jsonl",        codex),
    ("bearcode",  HOME + "/.bearcode/sessions/**/wire.jsonl",  wire),
    ("kimi-code", HOME + "/.kimi-code/sessions/**/wire.jsonl", wire),
    ("gemini",    HOME + "/.gemini/**/chats/*.jsonl",          gemini),
    ("grok",      HOME + "/.grok/**/chat_history.jsonl",       grok),
]


def main():
    if os.path.exists(DB):
        os.remove(DB)
    db = sqlite3.connect(DB)
    db.executescript(SCHEMA)
    mid = S.machine_id()
    totals = {}
    for harness, pattern, adapter in SOURCES:
        files = sorted(set(glob.glob(pattern, recursive=True)))
        n_msgs = 0
        for f in files:
            try:
                session, project, entries = adapter(f)
            except Exception as exc:
                print("  %-10s skip %s: %s" % (harness, f.replace(HOME, "~")[:60], exc))
                continue
            if not entries:
                continue
            sha = hashlib.sha256(open(f, "rb").read()).hexdigest()
            cur = db.execute(
                "INSERT INTO files (machine_id,root_slug,project,session,relpath,bytes,sha256,is_subagent,harness) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (mid, harness, project, session, os.path.relpath(f, HOME), os.path.getsize(f), sha, 0, harness))
            fid = cur.lastrowid
            rows = []
            for en in entries:
                text = clean(en["text"])
                stripped = text.strip()
                rows.append((fid, en["line_no"], en["uid"], None, S.fingerprint(en["uid"]), en["side"],
                             en["ts"], en["model"], 0, en["compact"], None, 0, 0,
                             1 if stripped else 0, len(stripped),
                             re.sub(r"\s+", " ", text).strip() if stripped else None, harness))
            db.executemany(
                "INSERT INTO messages (file_id,line_no,uuid,parent_uuid,fingerprint,side,ts,model,"
                "is_sidechain,is_compact,command,is_meta,is_visible_only,has_text,text_len,text,harness) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            n_msgs += len(rows)
        totals[harness] = (len(files), n_msgs)
    db.commit()
    print("wrote %s" % DB)
    for h, (nf, nm) in totals.items():
        a = db.execute("SELECT COUNT(*) FROM messages WHERE harness=? AND side='assistant' AND has_text=1", (h,)).fetchone()[0]
        u = db.execute("SELECT COUNT(*) FROM messages WHERE harness=? AND side='user' AND has_text=1", (h,)).fetchone()[0]
        print("  %-10s %4d files  %6d entries  assistant(text) %5d  user(text) %5d" % (h, nf, nm, a, u))
    print("\nassistant messages with text, by model:")
    for m, h, n in db.execute("""SELECT model, harness, COUNT(*) FROM messages WHERE side='assistant' AND has_text=1
                                 GROUP BY 1,2 ORDER BY 3 DESC LIMIT 20"""):
        print("  %-36s %-10s %5d" % (m, h, n))


if __name__ == "__main__":
    main()
