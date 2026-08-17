#!/usr/bin/env python3
"""
Incremental sync of every machine's Claude transcripts into archive/.

Usage:  python3 scripts/sync.py [--dry-run] [--host NAME]

Diffs against what the archive already holds rather than filtering by date.
A date window ("last 7 days") would miss two things that matter: a file that is
old but was never copied (a machine added later, or a root discovered later),
and a file deleted upstream by retention, where the archive is the only surviving
copy. Diffing is self-correcting; a date window is not.

Transcripts are append-only, so a file is fetched when it is NEW or has GROWN.
Nothing is ever deleted from the archive: files that vanish upstream are reported
as archive-only, which is the decay insurance doing its job.

After syncing, rebuild the index:  python3 scripts/build_db.py
"""

import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paths as P

# Every fetch subprocess used to run with capture_output and NO return-code
# check. A failed tar/scp printed nothing and sync reported success -- 911 KB
# from one host went missing while the summary said "1 grown, fetched". Commands are
# checked here, and the files are VERIFIED on disk afterwards, because a command
# can exit 0 and still not produce what you asked for.
FAILURES = []


def run(cmd, **kw):
    kw.setdefault("capture_output", True)
    kw.setdefault("text", True)
    try:
        r = subprocess.run(cmd, **kw)
    except Exception as exc:
        FAILURES.append((" ".join(map(str, cmd))[:70], "EXC", str(exc)[:150]))
        return None
    if r.returncode != 0:
        FAILURES.append((" ".join(map(str, cmd))[:70], r.returncode,
                         (r.stderr or "").strip()[:150]))
    return r

ARCHIVE = P.ARCHIVE
SSHOPT = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=20",
          "-o", "StrictHostKeyChecking=accept-new"]

# Emits: machine_id|root|relpath|bytes  for every Claude transcript.
# Kimi/GLM excluded by decision -- this archive is Claude only.
MID_CMD = ('MID=$(python3 -c "import sys;sys.path.insert(0,\'/tmp\');import scan;'
           'print(scan.machine_id())" 2>/dev/null); '
           '[ -z "$MID" ] && MID=$(hostname | tr -c \'A-Za-z0-9\' \'-\')\n')

# Fast path: inventory the KNOWN roots from hosts.json. A bare `find /` costs
# minutes on a multi-terabyte fileserver and re-derives roots we already
# recorded -- fine occasionally (--discover), wrong for a weekly job.
def inventory_script(roots):
    body = MID_CMD
    for r in roots:
        body += ('D=%s\n'
                 'if [ -d "$D" ]; then find "$D" -name \'*.jsonl\' -not -name \'._*\' '
                 '2>/dev/null | while read f; do '
                 'echo "$MID|$D|${f#$D/}|$(wc -c < "$f" | tr -d \' \')"; done; fi\n') % r
    return body

# Slow path, only on --discover.
DISCOVER = r'''
find / \( -path /proc -o -path /sys -o -path /dev -o -path /snap \
     -o -path /var/lib/containerd -o -path /var/lib/docker \) -prune -o \
     -type d -name projects -print 2>/dev/null \
  | while read d; do
      case "$d" in
        *"/.claude-kimi/"*|*"/.claude-glm/"*) continue ;;
        *"/.claude"*) ;; *) continue ;;
      esac
      [ "$(find "$d" -name '*.jsonl' 2>/dev/null | wc -l)" -gt 0 ] && echo "$d"
    done
'''


def slug(path):
    """One dash per non-alphanumeric char -- NOT collapsed.

    Must match the naming used when the archive was first written by tar
    (`tr -c 'A-Za-z0-9' '-'`). A collapsing regex here made every file
    look simultaneously new and deleted.
    """
    return re.sub(r"[^A-Za-z0-9]", "-", path).strip("-")


def addr(h):
    """What we actually dial: the 'ssh' field (e.g. the Tailscale alias
    umzflash.ts) when present, else the host's name. Names stay stable
    labels; addressing lives in ~/.ssh/config."""
    return h.get("ssh", h["name"])


def remote_cmd(h, inner):
    """Build the ssh argv for a host, honouring jump + sudo."""
    sudo = "sudo -n " if h.get("sudo") else ""
    if h["kind"] == "direct":
        return ["ssh"] + SSHOPT + [addr(h), sudo + inner]
    jump = h["jump"]
    inner_q = inner.replace("'", "'\"'\"'")
    return ["ssh"] + SSHOPT + [jump,
            "sudo -n ssh " + " ".join(SSHOPT) + " " + h["name"] + " '" + sudo + inner_q + "'"]


def push(h, local_path, remote_path):
    """Copy a file to a host, hopping through the jump box when needed."""
    if h["kind"] == "direct":
        run(["scp"] + SSHOPT + [local_path, addr(h) + ":" + remote_path],
        timeout=300)
        return
    jump = h["jump"]
    run(["scp"] + SSHOPT + [local_path, jump + ":" + remote_path],
        timeout=300)
    run(["ssh"] + SSHOPT + [jump, "sudo -n scp " + " ".join(SSHOPT) + " " +
                   remote_path + " " + h["name"] + ":" + remote_path],
                   capture_output=True, timeout=600)


def local_inventory():
    sys.path.insert(0, HERE)
    import scan as S
    mid = S.machine_id()
    root = os.path.expanduser("~/.claude/projects")
    out = []
    for dp, _d, ns in os.walk(root):
        for n in ns:
            if n.endswith(".jsonl") and not n.startswith("._"):
                p = os.path.join(dp, n)
                out.append((mid, root, os.path.relpath(p, root), os.path.getsize(p)))
    return out


def parse_inventory(text):
    out = []
    for line in text.splitlines():
        parts = line.strip().split("|")
        if len(parts) == 4 and parts[3].isdigit():
            out.append((parts[0], parts[1], parts[2], int(parts[3])))
    return out


def archive_state():
    """(machine_id, root_slug, relpath) -> bytes already held."""
    have = {}
    if not os.path.isdir(ARCHIVE):
        return have
    for mid in os.listdir(ARCHIVE):
        mdir = os.path.join(ARCHIVE, mid)
        if not os.path.isdir(mdir):
            continue
        for rs in os.listdir(mdir):
            rdir = os.path.join(mdir, rs)
            if not os.path.isdir(rdir):
                continue
            for dp, _d, ns in os.walk(rdir):
                for n in ns:
                    if n.endswith(".jsonl"):
                        p = os.path.join(dp, n)
                        have[(mid, rs, os.path.relpath(p, rdir))] = os.path.getsize(p)
    return have


def discover(cfg, only=None):
    """Slow path: walk each filesystem for transcript roots not in hosts.json.

    The fast path inventories the recorded roots. This finds stores added since
    -- which is how .claude-cio.pre-symlink-backup (344 files) stayed invisible
    for a full day while discovery hardcoded the directory name ".claude".
    """
    dpath = os.path.join(P.ROOT, ".discover.sh")
    with open(dpath, "w") as fh:
        fh.write("#!/bin/sh\n" + DISCOVER)
    for h in cfg["hosts"]:
        if only and h["name"] != only:
            continue
        if h["kind"] == "local":
            continue
        push(h, dpath, "/tmp/discover.sh")
        r = run(remote_cmd(h, "sh /tmp/discover.sh"), timeout=1800)
        found = [x.strip() for x in (r.stdout if r else "").splitlines() if x.strip()]
        known = set(h.get("roots", []))
        new = [f for f in found if f.rsplit("/.claude", 1)[0] + "/.claude" not in ""
               and f not in known]
        print("  %-10s %d root(s) on disk" % (h["name"], len(found)))
        for f in found:
            print("     %s%s" % (f, "   <-- NOT in hosts.json" if f not in known else ""))


def main():
    dry = "--dry-run" in sys.argv
    only = None
    if "--host" in sys.argv:
        only = sys.argv[sys.argv.index("--host") + 1]

    if "--discover" in sys.argv:
        discover(json.load(open(P.HOSTS)), only)
        return
    cfg = json.load(open(P.HOSTS))
    have = archive_state()
    print("archive currently holds %d files" % len(have))

    grand_new = grand_grown = grand_bytes = 0
    seen_keys = set()
    failed_hosts = set()
    ok_machines = set()
    verify_failed = {}

    for h in cfg["hosts"]:
        if only and h["name"] != only:
            continue
        name = h["name"]
        try:
            if h["kind"] == "local":
                inv = local_inventory()
            else:
                subprocess.run(["scp"] + SSHOPT + [os.path.join(HERE, "scan.py"),
                                                   (h.get("jump") or addr(h)) + ":/tmp/"],
                               capture_output=True, timeout=120)
                if h["kind"] == "via":
                    subprocess.run(["ssh"] + SSHOPT + [h["jump"],
                        "sudo -n scp " + " ".join(SSHOPT) + " /tmp/scan.py " + name + ":/tmp/"],
                        capture_output=True, timeout=180)
                inv_path = os.path.join(P.ROOT, ".inventory.sh")
                with open(inv_path, "w") as fh:
                    fh.write("#!/bin/sh\n" + inventory_script(h.get("roots", [])))
                push(h, inv_path, "/tmp/inventory.sh")
                r = subprocess.run(remote_cmd(h, "sh /tmp/inventory.sh"),
                                   capture_output=True, text=True, timeout=900)
                inv = parse_inventory(r.stdout)
                if not inv and r.stderr.strip():
                    print("  %-10s inventory stderr: %s" % (name, r.stderr.strip()[:90]))
        except Exception as exc:
            print("  %-10s ERROR %s  (archive left untouched)" % (name, exc))
            failed_hosts.add(name)
            continue

        if not inv:
            # Critical: an unreachable host must never be mistaken for retention
            # deleting everything. Mark it failed so its files stay accounted for.
            print("  %-10s NO INVENTORY -- treating as failure, not deletion" % name)
            failed_hosts.add(name)
            continue
        ok_machines.update(mid for mid, _r, _p, _s in inv)

        fetch = []
        for mid, root, rel, size in inv:
            key = (mid, slug(root), rel)
            seen_keys.add(key)
            old = have.get(key)
            if old is None:
                fetch.append((mid, root, rel, size, "new"))
            elif size > old:
                fetch.append((mid, root, rel, size, "grown"))
        nnew = sum(1 for x in fetch if x[4] == "new")
        ngrown = len(fetch) - nnew
        nbytes = sum(x[3] for x in fetch)
        grand_new += nnew; grand_grown += ngrown; grand_bytes += nbytes
        print("  %-10s %5d upstream | %4d new  %4d grown  (%.1f MB to fetch)"
              % (name, len(inv), nnew, ngrown, nbytes / 1e6))

        if fetch and not dry:
            fetch_files(h, fetch)
            missing = []
            for mid, root, rel, size, _kind in fetch:
                p = os.path.join(ARCHIVE, mid, slug(root), rel)
                got = os.path.getsize(p) if os.path.exists(p) else -1
                if got < size:
                    missing.append((rel.split("/")[-1], size, got))
            if missing:
                verify_failed[name] = missing
                print("  %-10s VERIFY FAILED on %d file(s)" % (name, len(missing)))
                for fn, want, got in missing[:3]:
                    print("     %s  expected>=%d got %s" % (fn, want, got if got >= 0 else "ABSENT"))

    gone = [k for k in have if k not in seen_keys and k[0] in ok_machines]
    print()
    print("TOTAL: %d new, %d grown, %.1f MB" % (grand_new, grand_grown, grand_bytes / 1e6))
    if failed_hosts:
        print("hosts not reached: %s  (their archived files are NOT reported as gone)"
              % ", ".join(sorted(failed_hosts)))
    if gone:
        print("%d file(s) in archive no longer upstream (retention) -- KEPT:" % len(gone))
        for k in gone[:10]:
            print("   %s/%s/%s" % k)
    if FAILURES:
        print()
        print("COMMAND FAILURES (%d) -- these used to be silent:" % len(FAILURES))
        for cmd, rc, err in FAILURES[:8]:
            print("   rc=%s  %s" % (rc, cmd))
            if err:
                print("            %s" % err)
    if verify_failed:
        print()
        print("SYNC INCOMPLETE -- %d host(s) failed verification: %s"
              % (len(verify_failed), ", ".join(verify_failed)))
    elif not dry:
        print()
        print("all fetched files verified on disk")
    if dry:
        print("(dry run -- nothing fetched)")
    else:
        print("now run:  python3 scripts/build_db.py")


def fetch_files(h, fetch):
    """Tar exactly the wanted files on the source, pull, extract."""
    byroot = {}
    for mid, root, rel, _s, _k in fetch:
        byroot.setdefault((mid, root), []).append(rel)
    for (mid, root), rels in byroot.items():
        dst = os.path.join(ARCHIVE, mid, slug(root))
        os.makedirs(dst, exist_ok=True)
        if h["kind"] == "local":
            for rel in rels:
                src = os.path.join(root, rel)
                out = os.path.join(dst, rel)
                os.makedirs(os.path.dirname(out), exist_ok=True)
                run(["cp", "-p", src, out])
            continue
        # Ship the file list as a FILE. The previous version built a heredoc and
        # passed it through json.dumps + two levels of shell quoting, which
        # flattened the newlines and made `cat` eat the tar flags
        # ("cat: invalid option -- 'C'"). Failed silently for a full day.
        sudo = "sudo -n " if h.get("sudo") else ""
        # Clear stale scratch first: a previous failed run left these owned by
        # root, which then blocked the fixed run from writing them.
        run(remote_cmd(h, sudo + "rm -f /tmp/synclist /tmp/sync.tgz"), timeout=300)
        listfile = os.path.join(P.ROOT, ".synclist")
        with open(listfile, "w") as fh:
            # "./" prefix required: Claude project dirs begin with "-"
            # (-projects-AIF), and tar -T reads a leading dash as an option.
            fh.write("\n".join("./" + r for r in rels) + "\n")
        push(h, listfile, "/tmp/synclist")
        run(remote_cmd(h, sudo + "tar czf /tmp/sync.tgz -C " + root + " -T /tmp/synclist"),
            timeout=1800)
        jump = h.get("jump")
        if jump:
            run(["ssh"] + SSHOPT + [jump,
                "sudo -n rm -f /tmp/sync.tgz; sudo -n scp " + " ".join(SSHOPT) + " " +
                h["name"] + ":/tmp/sync.tgz /tmp/sync.tgz && sudo -n chown $(whoami) /tmp/sync.tgz"],
                timeout=1800)
            src = jump + ":/tmp/sync.tgz"
        else:
            run(["ssh"] + SSHOPT + [addr(h),
                "sudo -n cp /tmp/sync.tgz /tmp/s2.tgz && sudo -n chown $(whoami) /tmp/s2.tgz"],
                capture_output=True, timeout=600)
            src = addr(h) + ":/tmp/s2.tgz"
        local_tgz = "/tmp/sync-%s.tgz" % h["name"]
        run(["scp"] + SSHOPT + [src, local_tgz], timeout=1800)
        if os.path.exists(local_tgz):
            run(["tar", "xzf", local_tgz, "-C", dst])
            os.remove(local_tgz)


if __name__ == "__main__":
    main()
