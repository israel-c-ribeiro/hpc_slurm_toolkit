"""Read and edit VASP INCAR files without destroying them.

Why
---
A DFT campaign usually starts from one good INCAR and then needs small,
systematic changes: turn relaxations into single points (``NSW = 0``),
tighten ``EDIFF`` everywhere, remove ``NCORE`` before moving to another
cluster... Doing that by hand on fifty folders is how typos and silent
duplicate tags creep in; doing it with ``sed`` breaks lines that carry several
tags (``ISTART = 0; ICHARG = 2``) or a trailing comment.

This module edits INCARs *statement by statement*:

* every other tag, comment, blank line and the order of the file are kept;
* several ``TAG = value`` statements on one line (separated by ``;``) are
  handled one by one;
* ``#`` and ``!`` comments are respected, also after a value;
* lines continued with a trailing backslash and multi-line quoted strings
  count as one statement;
* tag names are case-insensitive (as in VASP) and written in upper case;
* line endings are normalised to LF, the only ones the cluster expects.

A tag that appears twice in an INCAR is ambiguous: ``set`` rewrites every
occurrence and the command line warns about duplicates.

Use it as a library
-------------------
    import sys; sys.path.insert(0, "path/to/hpc_slurm_toolkit/tools")
    from incar_tags import set_tag, drop_tag, get_tag
    text = open("INCAR").read()
    text = set_tag(text, "NSW", "0")
    text = drop_tag(text, "EDIFFG")
    get_tag(text, "ENCUT")            # '520' -- a string, exactly as written

or from the command line
------------------------
    # look at a few tags in every run of a campaign (globs are expanded by the tool)
    python incar_tags.py "runs/*/INCAR" --get ENCUT --get ISMEAR

    # turn relaxations into single points and record the change in each file
    python incar_tags.py "runs/*/INCAR" --set NSW=0 --set IBRION=-1 --drop EDIFFG --provenance

    # preview the change as a diff, write nothing
    python incar_tags.py runs/Si --set EDIFF=1E-7 --dry-run

A directory argument means ``<directory>/INCAR``. Only the Python standard
library is needed, so the file can be copied to a cluster login node.
"""
from __future__ import annotations

import argparse
import datetime as dt
import difflib
import glob
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping

__all__ = ["parse_incar", "get_tag", "set_tag", "drop_tag", "apply_changes",
           "find_duplicates", "parse_assignment"]

_TAG_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_STATEMENT = re.compile(r"^(\s*)([A-Za-z][A-Za-z0-9_]*)(\s*)=(.*)$", re.S)
_CONTINUATION = re.compile(r"[ \t]*\\[ \t]*\n[ \t]*")
_COMMENT_CHARS = "#!"


# ----------------------------------------------------------------------------- parsing
@dataclass
class _LogicalLine:
    """One INCAR statement line (possibly continued over several physical lines)."""

    raw: str                      # original text, newline included
    indent: str                   # leading white space of the line
    statements: list[str]         # code split at ';' (white space kept)
    comment: str                  # '# ...' or '! ...' ('' if none)
    eol: str                      # '\n' or '' (last line without newline)
    changed: bool = field(default=False)

    def render(self) -> str:
        if not self.changed:
            return self.raw
        parts = [s for s in self.statements if s.strip()]
        if not parts:              # last statement dropped: the line (and its comment) goes
            return ""
        code = self.indent + ";".join([parts[0].lstrip()] + parts[1:])
        if self.comment:
            if not code[-1:].isspace():
                code += "   "
            code += self.comment
        return code + self.eol


def _scan(text: str, in_quote: bool = False) -> tuple[int | None, bool]:
    """Index where a comment starts in *text* (None if none) and the quote state at its end."""
    for i, ch in enumerate(text):
        if ch == '"':
            in_quote = not in_quote
        elif not in_quote and ch in _COMMENT_CHARS:
            return i, in_quote
    return None, in_quote


def _split_statements(code: str) -> list[str]:
    """Split the code part of a line at ';' outside double quotes."""
    parts, cur, in_quote = [], [], False
    for ch in code:
        if ch == '"':
            in_quote = not in_quote
        if ch == ";" and not in_quote:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return parts


def _make_logical(group: list[str]) -> _LogicalLine:
    raw = "".join(group)
    eol = "\n" if raw.endswith("\n") else ""
    body = raw[:-1] if eol else raw
    cpos, _ = _scan(body)
    code, comment = (body, "") if cpos is None else (body[:cpos], body[cpos:])
    indent = re.match(r"[ \t]*", code).group(0)
    return _LogicalLine(raw=raw, indent=indent, statements=_split_statements(code),
                        comment=comment.rstrip(), eol=eol)


def _logical_lines(text: str) -> list[_LogicalLine]:
    """Group physical lines into logical ones (backslash continuation, open quotes)."""
    out: list[_LogicalLine] = []
    group: list[str] = []
    in_quote = False
    for phys in text.splitlines(keepends=True):
        group.append(phys)
        body = phys.rstrip("\n")
        cpos, in_quote = _scan(body, in_quote)
        code = body if cpos is None else body[:cpos]
        if in_quote or (cpos is None and code.rstrip().endswith("\\")):
            continue
        out.append(_make_logical(group))
        group = []
    if group:
        out.append(_make_logical(group))
    return out


def _normalise(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _clean_value(raw: str) -> str:
    """Value of a statement as VASP sees it: continuations joined, outer blanks removed."""
    return _CONTINUATION.sub(" ", raw).strip()


def _check_tag(tag: str) -> str:
    tag = tag.strip()
    if not _TAG_NAME.match(tag):
        raise ValueError(f"not a valid INCAR tag name: {tag!r}")
    return tag.upper()


# ----------------------------------------------------------------------------- public API
def parse_incar(text: str) -> dict[str, str]:
    """All tags of an INCAR as ``{TAG: value}`` (first occurrence wins, values are strings)."""
    tags: dict[str, str] = {}
    for line in _logical_lines(_normalise(text)):
        for st in line.statements:
            m = _STATEMENT.match(st)
            if m:
                tags.setdefault(m.group(2).upper(), _clean_value(m.group(4)))
    return tags


def get_tag(text: str, tag: str, default: str | None = None) -> str | None:
    """Value of *tag* (case-insensitive) as written in the INCAR, or *default*."""
    return parse_incar(text).get(_check_tag(tag), default)


def find_duplicates(text: str) -> list[str]:
    """Tags that appear more than once (an ambiguous INCAR)."""
    seen: dict[str, int] = {}
    for line in _logical_lines(_normalise(text)):
        for st in line.statements:
            m = _STATEMENT.match(st)
            if m:
                seen[m.group(2).upper()] = seen.get(m.group(2).upper(), 0) + 1
    return [t for t, n in seen.items() if n > 1]


def set_tag(text: str, tag: str, value: object, comment: str = "") -> str:
    """Set *tag* = *value*; every occurrence is rewritten, a missing tag is appended.

    The spacing before ``=`` and the comment of the line are kept; *comment*
    (if given) replaces the line comment.
    """
    tag = _check_tag(tag)
    value = str(value).strip()
    if "\n" in value or not value:
        raise ValueError(f"{tag}: the value must be a non-empty single line")
    lines = _logical_lines(_normalise(text))
    found = False
    for line in lines:
        for i, st in enumerate(line.statements):
            m = _STATEMENT.match(st)
            if m and m.group(2).upper() == tag:
                lead, space, old = m.group(1), m.group(3) or " ", m.group(4)
                trailing = re.search(r"[ \t]*$", old).group(0)
                line.statements[i] = f"{lead}{tag}{space}= {value}{trailing}"
                if comment:
                    line.comment = f"# {comment}"
                line.changed = found = True
    out = "".join(line.render() for line in lines)
    if not found:
        if out and not out.endswith("\n"):
            out += "\n"
        out += f"{tag} = {value}" + (f"   # {comment}" if comment else "") + "\n"
    return out


def drop_tag(text: str, tag: str) -> str:
    """Remove every ``TAG = value`` statement; a line left without statements is removed."""
    tag = _check_tag(tag)
    lines = _logical_lines(_normalise(text))
    for line in lines:
        kept = [st for st in line.statements
                if not ((m := _STATEMENT.match(st)) and m.group(2).upper() == tag)]
        if len(kept) != len(line.statements):
            line.statements = kept
            line.changed = True
    return "".join(line.render() for line in lines)


def apply_changes(text: str, set_tags: Mapping[str, object] | None = None,
                  drop_tags: Iterable[str] | None = None, header: str | None = None) -> str:
    """Apply several ``set`` and ``drop`` operations, then optionally prepend a ``# header`` line."""
    for tag in drop_tags or ():
        text = drop_tag(text, tag)
    for tag, value in (set_tags or {}).items():
        text = set_tag(text, tag, value)
    if header:
        text = f"# {header.strip()}\n" + _normalise(text)
    return text


def parse_assignment(item: str) -> tuple[str, str]:
    """``'ENCUT=520'`` -> ``('ENCUT', '520')`` (for command-line options)."""
    if "=" not in item:
        raise ValueError(f"expected TAG=VALUE, got {item!r}")
    tag, value = item.split("=", 1)
    tag = _check_tag(tag)
    if not value.strip():
        raise ValueError(f"{tag}: empty value in {item!r}")
    return tag, value.strip()


def describe_changes(set_tags: Mapping[str, object], drop_tags: Iterable[str]) -> str:
    """Short human-readable summary, e.g. ``'set NSW=0, IBRION=-1; drop EDIFFG'``."""
    parts = []
    if set_tags:
        parts.append("set " + ", ".join(f"{k}={v}" for k, v in set_tags.items()))
    drop_tags = list(drop_tags)
    if drop_tags:
        parts.append("drop " + ", ".join(t.upper() for t in drop_tags))
    return "; ".join(parts)


# ----------------------------------------------------------------------------- command line
def _natural_key(s: str) -> list:
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def expand_inputs(items: Iterable[str]) -> list[Path]:
    """Expand globs (also on Windows shells) and map directories to ``<dir>/INCAR``."""
    paths: list[Path] = []
    for item in items:
        matches = sorted(glob.glob(item, recursive=True), key=_natural_key) \
            if any(c in item for c in "*?[") else [item]
        if not matches:
            raise FileNotFoundError(f"no file matches {item!r}")
        for m in matches:
            p = Path(m)
            paths.append(p / "INCAR" if p.is_dir() else p)
    return paths


def _read(path: Path) -> str:
    with open(path, encoding="utf-8", errors="surrogateescape") as fh:
        return _normalise(fh.read())


def _write(path: Path, text: str) -> None:
    with open(path, "w", encoding="utf-8", errors="surrogateescape", newline="\n") as fh:
        fh.write(text)


def _print_table(rows: list[list[str]], header: list[str]) -> None:
    widths = [max(len(str(r[i])) for r in rows + [header]) for i in range(len(header))]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*header))
    print(fmt.format(*("-" * w for w in widths)))
    for r in rows:
        print(fmt.format(*r))


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="incar_tags.py",
        description="Set, drop or read tags in one or many VASP INCAR files, keeping everything else.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  python incar_tags.py "runs/*/INCAR" --get ENCUT --get EDIFF
  python incar_tags.py "runs/*/INCAR" --set NSW=0 --set IBRION=-1 --drop EDIFFG --provenance
  python incar_tags.py runs/Si --set "MAGMOM=2*0.0" --dry-run      # show the diff only
  python incar_tags.py INCAR --list

notes:
  quote globs so that the tool expands them (works the same in bash and PowerShell);
  a directory means <directory>/INCAR; files are written with LF line endings.""")
    ap.add_argument("files", nargs="+", help="INCAR files, directories or glob patterns")
    ap.add_argument("--set", dest="set_", action="append", default=[], metavar="TAG=VALUE",
                    help="set a tag (repeatable); a missing tag is appended")
    ap.add_argument("--drop", action="append", default=[], metavar="TAG", help="remove a tag (repeatable)")
    ap.add_argument("--get", action="append", default=[], metavar="TAG",
                    help="print the value of a tag in every file (repeatable)")
    ap.add_argument("--list", action="store_true", help="print every tag of every file")
    ap.add_argument("--provenance", action="store_true",
                    help="prepend '# <date> incar_tags.py: <changes>' to each modified file")
    ap.add_argument("--note", default="", help="free text added to the provenance line")
    ap.add_argument("--backup", action="store_true", help="keep the previous version as INCAR.bak")
    ap.add_argument("--dry-run", action="store_true", help="show a diff of the changes, write nothing")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        sets = dict(parse_assignment(s) for s in args.set_)
        drops = [_check_tag(t) for t in args.drop]
        gets = [_check_tag(t) for t in args.get]
        files = expand_inputs(args.files)
    except (ValueError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not (sets or drops or gets or args.list):
        print("error: nothing to do -- use --set, --drop, --get or --list", file=sys.stderr)
        return 2

    missing = [f for f in files if not f.is_file()]
    for f in missing:
        print(f"error: {f} not found", file=sys.stderr)
    files = [f for f in files if f.is_file()]

    header = None
    if args.provenance:
        header = f"{dt.date.today().isoformat()} incar_tags.py: {describe_changes(sets, drops)}"
        if args.note:
            header += f" ({args.note})"

    table: list[list[str]] = []
    n_changed = 0
    for path in files:
        text = _read(path)
        for dup in find_duplicates(text):
            print(f"warning: {path}: {dup} appears more than once", file=sys.stderr)
        if sets or drops:
            new = apply_changes(text, sets, drops, header)
            if new != text:
                n_changed += 1
                if args.dry_run:
                    sys.stdout.writelines(difflib.unified_diff(
                        text.splitlines(keepends=True), new.splitlines(keepends=True),
                        fromfile=f"{path} (before)", tofile=f"{path} (after)"))
                else:
                    if args.backup:
                        _write(path.with_name(path.name + ".bak"), text)
                    _write(path, new)
            text = new
        if gets:
            values = parse_incar(text)
            table.append([path.as_posix()] + [values.get(t, "-") for t in gets])
        if args.list:
            print(f"== {path.as_posix()}")
            for tag, value in parse_incar(text).items():
                print(f"  {tag:<10} = {value}")
    if table:
        _print_table(table, ["file"] + gets)
    if sets or drops:
        verb = "would change" if args.dry_run else "changed"
        print(f"{verb} {n_changed} of {len(files)} file(s)", file=sys.stderr)
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
