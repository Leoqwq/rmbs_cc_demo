"""Safe .env editing for the automation scripts.

Central guarantee (spec §2): writes never drop or silently overwrite existing keys.
- set_keys(..., force=False) appends a key only if absent; with force=True it replaces
  the last occurrence in place. Either way it backs up first and replaces atomically.
- merge_file pulls KEY=VALUE pairs from a source file into the target via set_keys.

CLI:
  python config_env.py set   --into .env KEY=VALUE [KEY2=V2 ...] [--force]
  python config_env.py merge --from members.env --into .env [--force]
"""
import argparse
import os
import re
import shutil
import sys
import tempfile
import time

_LINE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


def _read_lines(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return f.read().splitlines()


def parse_env(path):
    """Return {key: value} with last-wins semantics (matches python-dotenv).

    Values are whitespace-stripped, so merge_file normalizes (strips) values it writes.
    """
    result = {}
    for line in _read_lines(path):
        if line.strip().startswith("#"):
            continue
        m = _LINE.match(line)
        if m:
            result[m.group(1)] = m.group(2).strip()
    return result


def _key_line_indexes(lines, key):
    idxs = []
    for i, line in enumerate(lines):
        if line.strip().startswith("#"):
            continue
        m = _LINE.match(line)
        if m and m.group(1) == key:
            idxs.append(i)
    return idxs


def _atomic_write(path, lines):
    backup = None
    if os.path.exists(path):
        backup = f"{path}.bak.{time.time_ns()}"
        shutil.copy2(path, backup)
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".env.tmp.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        os.replace(tmp, path)
    except Exception:
        os.unlink(tmp)
        raise
    return backup


def set_keys(path, updates, force=False):
    """Apply {key: value} to the .env at `path`.

    Returns {"changed": [...], "skipped": [...], "backup": path|None}. A key already
    present is skipped unless force=True (then its last occurrence is replaced). Backs up
    and replaces atomically only when something actually changes.
    """
    lines = _read_lines(path)
    changed, skipped = [], []
    for key, value in updates.items():
        idxs = _key_line_indexes(lines, key)
        if idxs and not force:
            skipped.append(key)
        elif idxs and force:
            lines[idxs[-1]] = f"{key}={value}"
            changed.append(key)
        else:
            lines.append(f"{key}={value}")
            changed.append(key)
    backup = _atomic_write(path, lines) if changed else None
    return {"changed": changed, "skipped": skipped, "backup": backup}


def merge_file(src, dst, force=False):
    return set_keys(dst, parse_env(src), force=force)


def main(argv=None):
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("set")
    s.add_argument("--into", required=True)
    s.add_argument("--force", action="store_true")
    s.add_argument("pairs", nargs="+", help="KEY=VALUE")
    m = sub.add_parser("merge")
    m.add_argument("--from", dest="src", required=True)
    m.add_argument("--into", required=True)
    m.add_argument("--force", action="store_true")
    a = p.parse_args(argv)
    if a.cmd == "set":
        updates = {}
        for pair in a.pairs:
            k, _, v = pair.partition("=")
            if not k:
                p.error(f"bad KEY=VALUE pair: {pair!r}")
            updates[k] = v
        res = set_keys(a.into, updates, force=a.force)
    else:
        res = merge_file(a.src, a.into, force=a.force)
    print(f"changed={res['changed']} skipped={res['skipped']} backup={res['backup']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
