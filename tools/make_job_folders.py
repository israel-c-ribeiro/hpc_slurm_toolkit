"""Create one VASP job folder per structure: POSCAR, INCAR, KPOINTS, POTCAR and job.sh.

Why
---
Every campaign (a set of structures computed with the same settings) starts
the same way: one folder per structure, the same INCAR with a few changes,
the same KPOINTS, the right POTCAR and a Slurm script with a recognisable job
name. Doing it by hand is slow and error-prone -- the classic silent error is
a POTCAR whose species order does not match the POSCAR, which VASP happily
runs (with the wrong atoms). This tool builds all folders in one go and
**checks the POTCAR against the POSCAR before writing anything**.

For every structure file it writes ``<out>/<name>/``:

* ``POSCAR``  -- the structure (POSCAR/CONTCAR/*.vasp files are copied as they
  are; other formats such as CIF are converted with ASE if it is installed);
* ``INCAR``   -- the template INCAR plus ``--set TAG=VALUE`` / ``--drop TAG``
  changes and a provenance comment on the first line;
* ``KPOINTS`` -- copied (or omitted when the INCAR uses ``KSPACING``);
* ``POTCAR``  -- copied from ``--potcar`` or concatenated, in the POSCAR
  species order, from a POTCAR library (``--potcar-dir``: ``<dir>/<label>/POTCAR``,
  the layout of the VASP distribution); the ``TITEL`` lines are checked
  against the POSCAR species in both cases;
* ``job.sh``  -- the job template with ``#SBATCH --job-name`` set to the folder name.

It also writes ``<out>/manifest.csv`` (what was made from what) and
``<out>/submit_all.sh`` (submits the folders just created).

Names: ``Si.vasp`` -> ``Si``; ``POSCAR_Si`` -> ``Si``; ``some/dir/POSCAR`` or
``CONTCAR`` -> ``dir`` (the parent folder name).

Existing folders are skipped unless ``--overwrite``; with it, the five input
files are rewritten and everything else in the folder (outputs) is left alone.
All files are written with LF line endings.

Examples
--------
    python make_job_folders.py structures/*.vasp --out runs \\
        --incar INCAR --kpoints KPOINTS \\
        --potcar-dir /path/to/potpaw_PBE --potcar-map Na=Na_pv \\
        --job-template ../slurm_templates/vasp_relax_autorestart.sh \\
        --set ENCUT=520 --drop NCORE --dry-run

    # one POTCAR for every structure (all must share the same species order)
    python make_job_folders.py "relaxed/*/CONTCAR" --out static --incar INCAR.scf \\
        --kpoints KPOINTS --potcar POTCAR --job-template vasp_single_point.sh

Only the Python standard library is needed (ASE is optional, for non-POSCAR
formats). ``incar_tags.py`` must sit in the same directory.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import glob
import io
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from incar_tags import apply_changes, describe_changes, get_tag, parse_assignment  # noqa: E402

STRUCTURE_PATTERNS = ("POSCAR*", "CONTCAR*", "*.vasp", "*.poscar", "*.cif")
POSCAR_LIKE = re.compile(r"^(POSCAR|CONTCAR)|\.(vasp|poscar)$", re.I)
TITEL = re.compile(r"^\s*TITEL\s*=\s*(\S+)\s+(\S+)", re.M)
ELEMENT = re.compile(r"^[A-Z][a-z]?")
JOB_NAME_LINE = re.compile(r"^(#SBATCH[ \t]+(?:--job-name[= ]|-J[ \t]*))(\S+)", re.M)
INPUT_FILES = ("POSCAR", "INCAR", "KPOINTS", "POTCAR", "job.sh")


class SetupError(Exception):
    """A problem with one structure or input file (reported, nothing is written)."""


# ----------------------------------------------------------------------------- small helpers
def natural_key(s: str) -> list:
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def read_lf(path: Path) -> str:
    """Text of a file with LF line endings (inputs go to Linux machines)."""
    return path.read_bytes().decode("utf-8", errors="surrogateescape").replace("\r\n", "\n")


def write_lf(path: Path, text: str) -> None:
    with open(path, "w", encoding="utf-8", errors="surrogateescape", newline="\n") as fh:
        fh.write(text)


def safe_name(name: str) -> str:
    """Folder/job name with only letters, digits and ``. _ + -``."""
    return re.sub(r"[^A-Za-z0-9._+-]", "_", name).strip("._") or "structure"


def structure_name(path: Path) -> str:
    """``Si.vasp`` -> ``Si``, ``POSCAR_Si`` -> ``Si``, ``.../Si/CONTCAR`` -> ``Si``."""
    stem = path.stem if path.suffix.lower() in (".vasp", ".poscar", ".cif") else path.name
    if stem.upper() in ("POSCAR", "CONTCAR"):
        return safe_name(path.resolve().parent.name)
    stem = re.sub(r"^(POSCAR|CONTCAR)[_.-]", "", stem, flags=re.I)
    return safe_name(stem)


def parse_mapping(items: list[str]) -> dict[str, str]:
    """``['Na=Na_pv,Fe=Fe_pv']`` -> ``{'Na': 'Na_pv', 'Fe': 'Fe_pv'}``."""
    mapping: dict[str, str] = {}
    for item in items:
        for pair in filter(None, (p.strip() for p in item.split(","))):
            if "=" not in pair:
                raise SetupError(f"--potcar-map expects Element=label, got {pair!r}")
            el, label = (s.strip() for s in pair.split("=", 1))
            mapping[el] = label
    return mapping


# ----------------------------------------------------------------------------- structures
@dataclass
class PoscarHeader:
    species: list[str]
    counts: list[int]

    @property
    def n_atoms(self) -> int:
        return sum(self.counts)

    @property
    def formula(self) -> str:
        return "".join(f"{s}{n if n > 1 else ''}" for s, n in zip(self.species, self.counts))


def read_poscar_header(text: str, source: str) -> PoscarHeader:
    """Species and counts of a VASP 5/6 POSCAR (line 6 = element symbols, line 7 = counts)."""
    lines = text.splitlines()
    if len(lines) < 8:
        raise SetupError(f"{source}: too short to be a POSCAR")
    tokens, counts = lines[5].split(), lines[6].split()
    if not tokens or not all(t[0].isalpha() for t in tokens):
        raise SetupError(f"{source}: line 6 has no element symbols (VASP 4 format?). "
                         "Add a line with the species (e.g. 'Na Cl') above the atom counts.")
    species = []
    for t in tokens:
        m = ELEMENT.match(t)                    # 'Na_pv/1a2b3c' (VASP 6) -> 'Na'
        if not m:
            raise SetupError(f"{source}: {t!r} on line 6 is not an element symbol")
        species.append(m.group(0))
    try:
        numbers = [int(c) for c in counts[:len(species)]]
    except ValueError:
        raise SetupError(f"{source}: line 7 should hold the atom counts, got {lines[6]!r}") from None
    if len(numbers) != len(species):
        raise SetupError(f"{source}: {len(species)} species but {len(numbers)} counts")
    return PoscarHeader(species, numbers)


def poscar_via_ase(path: Path, comment: str) -> str:
    """Convert any ASE-readable file to POSCAR text, atoms grouped by species."""
    try:
        from ase.io import read, write
    except ImportError:
        raise SetupError(f"{path}: reading '{path.suffix or path.name}' needs ASE (pip install ase); "
                         "POSCAR, CONTCAR and *.vasp files do not") from None
    atoms = read(str(path))
    symbols = atoms.get_chemical_symbols()
    first = {s: symbols.index(s) for s in symbols}
    atoms = atoms[sorted(range(len(atoms)), key=lambda i: first[symbols[i]])]
    buf = io.StringIO()
    write(buf, atoms, format="vasp", direct=True, sort=False, vasp5=True)
    lines = buf.getvalue().splitlines(keepends=True)
    return comment + "\n" + "".join(lines[1:])


def load_structure(path: Path, name: str) -> tuple[str, PoscarHeader]:
    text = read_lf(path) if POSCAR_LIKE.search(path.name) else poscar_via_ase(path, name)
    return text, read_poscar_header(text, str(path))


def collect_structures(items: list[str]) -> list[Path]:
    """Files, glob patterns or directories (searched for POSCAR*, CONTCAR*, *.vasp, *.poscar, *.cif)."""
    found: list[Path] = []
    for item in items:
        p = Path(item)
        if p.is_dir():
            hits = {h for pat in STRUCTURE_PATTERNS for h in p.glob(pat) if h.is_file()}
            found += sorted(hits, key=lambda h: natural_key(h.name))
        elif any(c in item for c in "*?["):
            found += [Path(h) for h in sorted(glob.glob(item, recursive=True), key=natural_key)
                      if Path(h).is_file()]
        elif p.is_file():
            found.append(p)
        else:
            raise SetupError(f"structure {item!r} not found")
    seen, unique = set(), []
    for f in found:
        key = f.resolve()
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return unique


# ----------------------------------------------------------------------------- POTCAR
def potcar_titels(text: str) -> list[str]:
    """POTCAR labels in file order, from the ``TITEL = PAW_PBE Na_pv 05Jan2001`` lines."""
    return [m.group(2) for m in TITEL.finditer(text)]


def element_of(label: str) -> str:
    """``'Na_pv'`` -> ``'Na'``, ``'Si_GW'`` -> ``'Si'``, ``'H.75'`` -> ``'H'``."""
    m = ELEMENT.match(label)
    return m.group(0) if m else label


def check_potcar(text: str, species: list[str], source: str) -> list[str]:
    """Labels of the POTCAR; raise if their elements differ from the POSCAR species order."""
    labels = potcar_titels(text)
    elements = [element_of(lab) for lab in labels]
    if elements != species:
        raise SetupError(f"{source}: POTCAR species {elements or '(no TITEL line)'} do not match "
                         f"the POSCAR species {species} (same order required)")
    return labels


def potcar_from_library(species: list[str], library: Path, mapping: dict[str, str]) -> str:
    """Concatenate ``<library>/<label>/POTCAR`` for every species, in POSCAR order."""
    parts = []
    for el in species:
        label = mapping.get(el, el)
        f = library / label / "POTCAR"
        if not f.is_file():
            raise SetupError(f"no POTCAR for {el}: {f} not found "
                             f"(choose a variant with --potcar-map {el}=<label>, e.g. {el}_pv)")
        part = read_lf(f)
        parts.append(part if part.endswith("\n") else part + "\n")
    return "".join(parts)


# ----------------------------------------------------------------------------- job script
def render_job(template: str, job_name: str) -> str:
    """Put *job_name* in the ``#SBATCH --job-name`` (or ``-J``) line, adding one if absent."""
    if JOB_NAME_LINE.search(template):
        return JOB_NAME_LINE.sub(lambda m: m.group(1) + job_name, template, count=1)
    lines = template.splitlines(keepends=True)
    at = 1 if lines and lines[0].startswith("#!") else 0
    lines.insert(at, f"#SBATCH --job-name={job_name}\n")
    return "".join(lines)


# ----------------------------------------------------------------------------- planning
@dataclass
class Job:
    name: str
    source: Path
    folder: Path
    poscar: str
    header: PoscarHeader
    potcar: str | None
    potcar_labels: list[str] = field(default_factory=list)
    action: str = "create"


def plan_jobs(args: argparse.Namespace, out: Path) -> tuple[list[Job], list[str]]:
    """Read and check every structure; return the jobs and the list of errors."""
    jobs, errors = [], []
    mapping = parse_mapping(args.potcar_map)
    shared_potcar = read_lf(Path(args.potcar)) if args.potcar else None
    library = Path(args.potcar_dir) if args.potcar_dir else None
    for src in collect_structures(args.structures):
        name = structure_name(src)
        try:
            poscar, header = load_structure(src, name)
            potcar, labels = None, []
            if shared_potcar is not None:
                potcar = shared_potcar
                labels = check_potcar(potcar, header.species, f"{src} + {args.potcar}")
            elif library is not None:
                potcar = potcar_from_library(header.species, library, mapping)
                labels = check_potcar(potcar, header.species, f"{src} + {library}")
        except SetupError as exc:
            errors.append(str(exc))
            continue
        except Exception as exc:                     # e.g. an unreadable CIF
            errors.append(f"{src}: {type(exc).__name__}: {exc}")
            continue
        folder = out / name
        action = "create"
        if folder.exists():
            action = "overwrite" if args.overwrite else "skip (exists)"
        jobs.append(Job(name, src, folder, poscar, header, potcar, labels, action))
    names = [j.name for j in jobs]
    for dup in sorted({n for n in names if names.count(n) > 1}):
        srcs = ", ".join(str(j.source) for j in jobs if j.name == dup)
        errors.append(f"two structures would share the folder name {dup!r}: {srcs} (rename one)")
    return jobs, errors


def print_plan(jobs: list[Job]) -> None:
    rows = [[j.name, j.header.formula, str(j.header.n_atoms), " ".join(j.potcar_labels) or "-",
             j.action] for j in jobs]
    head = ["folder", "formula", "atoms", "POTCAR", "action"]
    widths = [max(len(r[i]) for r in rows + [head]) for i in range(len(head))]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*head))
    print(fmt.format(*("-" * w for w in widths)))
    for r in rows:
        print(fmt.format(*r))


# ----------------------------------------------------------------------------- writing
def write_job(job: Job, incar: str, kpoints: str | None, job_text: str) -> None:
    job.folder.mkdir(parents=True, exist_ok=True)
    write_lf(job.folder / "POSCAR", job.poscar)
    write_lf(job.folder / "INCAR", incar)
    if kpoints is not None:
        write_lf(job.folder / "KPOINTS", kpoints)
    if job.potcar is not None:
        write_lf(job.folder / "POTCAR", job.potcar)
    script = job.folder / "job.sh"
    write_lf(script, job_text)
    try:
        os.chmod(script, 0o755)
    except OSError:
        pass


def write_manifest(out: Path, jobs: list[Job], args: argparse.Namespace, changes: str) -> Path:
    path = out / "manifest.csv"
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["folder", "source", "formula", "n_atoms", "species", "potcar", "incar_template",
                    "incar_changes", "kpoints", "job_template", "action"])
        for j in jobs:
            w.writerow([j.name, j.source.as_posix(), j.header.formula, j.header.n_atoms,
                        " ".join(j.header.species), " ".join(j.potcar_labels),
                        Path(args.incar).name, changes,
                        Path(args.kpoints).name if args.kpoints else "KSPACING",
                        Path(args.job_template).name, j.action])
    return path


def write_submit_script(out: Path, names: list[str]) -> Path:
    path = out / "submit_all.sh"
    folders = " ".join(f'"{n}"' for n in names)
    write_lf(path, f"""#!/usr/bin/env bash
# Submit the {len(names)} job folder(s) written by make_job_folders.py on {dt.date.today().isoformat()}.
# Run from anywhere:  bash {out.name}/submit_all.sh
# (tools/submit_all.sh does the same for any folder tree and skips finished or queued runs.)
set -euo pipefail
cd "$(dirname "$0")"
n=0
for d in {folders}; do
  echo "$d: $(cd "$d" && sbatch job.sh)"
  n=$((n + 1))
done
echo "submitted $n job(s)"
""")
    try:
        os.chmod(path, 0o755)
    except OSError:
        pass
    return path


# ----------------------------------------------------------------------------- main
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="make_job_folders.py",
        description="Create one VASP job folder (POSCAR, INCAR, KPOINTS, POTCAR, job.sh) per structure.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  python make_job_folders.py structures/ --out runs --incar INCAR --kpoints KPOINTS \\
      --potcar-dir /path/to/potpaw_PBE --potcar-map Na=Na_pv \\
      --job-template slurm_templates/vasp_relax_autorestart.sh --set ENCUT=520 --dry-run

  python make_job_folders.py "relaxed/*/CONTCAR" --out static --incar INCAR.scf \\
      --kpoints KPOINTS --potcar POTCAR --job-template slurm_templates/vasp_single_point.sh

POTCAR check: the elements of the POTCAR TITEL lines must equal the POSCAR species
(line 6), in the same order -- otherwise nothing is written and the tool exits with 1.""")
    ap.add_argument("structures", nargs="+",
                    help="structure files, glob patterns (quoted) or directories")
    ap.add_argument("--out", required=True, help="directory that receives one folder per structure")
    ap.add_argument("--incar", required=True, help="template INCAR")
    ap.add_argument("--kpoints", help="KPOINTS file (omit only if the INCAR sets KSPACING)")
    pot = ap.add_mutually_exclusive_group(required=True)
    pot.add_argument("--potcar", help="one POTCAR used for every structure")
    pot.add_argument("--potcar-dir", help="POTCAR library: <dir>/<label>/POTCAR (e.g. potpaw_PBE)")
    pot.add_argument("--no-potcar", action="store_true",
                     help="write no POTCAR (add it later, e.g. on the cluster)")
    ap.add_argument("--potcar-map", action="append", default=[], metavar="EL=LABEL",
                    help="POTCAR variant per element, e.g. Na=Na_pv,Fe=Fe_pv (default: the element name)")
    ap.add_argument("--job-template", required=True, help="Slurm script copied as job.sh")
    ap.add_argument("--job-prefix", default="", help="prefix for the Slurm job names (e.g. rlx_)")
    ap.add_argument("--set", dest="set_", action="append", default=[], metavar="TAG=VALUE",
                    help="INCAR tag to set in every folder (repeatable)")
    ap.add_argument("--drop", action="append", default=[], metavar="TAG",
                    help="INCAR tag to remove in every folder (repeatable)")
    ap.add_argument("--system-tag", action="store_true", help="also set SYSTEM = <folder name>")
    ap.add_argument("--overwrite", action="store_true",
                    help="rewrite the input files of existing folders (outputs are never touched)")
    ap.add_argument("--dry-run", action="store_true", help="check everything and print the plan, write nothing")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    out = Path(args.out)
    try:
        for opt in ("incar", "kpoints", "potcar", "job_template"):
            value = getattr(args, opt)
            if value and not Path(value).is_file():
                raise SetupError(f"--{opt.replace('_', '-')} {value}: file not found")
        if args.potcar_dir and not Path(args.potcar_dir).is_dir():
            raise SetupError(f"--potcar-dir {args.potcar_dir}: directory not found")
        sets = dict(parse_assignment(s) for s in args.set_)
        drops = [d.strip().upper() for d in args.drop]
        changes = describe_changes(sets, drops)
        base_incar = apply_changes(read_lf(Path(args.incar)), sets, drops)
        kpoints = read_lf(Path(args.kpoints)) if args.kpoints else None
        if kpoints is None and get_tag(base_incar, "KSPACING") is None:
            raise SetupError("no --kpoints given and the INCAR has no KSPACING: VASP would stop")
        job_template = read_lf(Path(args.job_template))
        jobs, errors = plan_jobs(args, out)
    except (SetupError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if errors:
        for e in errors:
            print(f"error: {e}", file=sys.stderr)
        print(f"nothing written: {len(errors)} problem(s) found", file=sys.stderr)
        return 1
    if not jobs:
        print("error: no structure file found", file=sys.stderr)
        return 1
    if args.no_potcar:
        print("warning: --no-potcar: add a POTCAR to every folder before submitting", file=sys.stderr)

    print_plan(jobs)
    if args.dry_run:
        print(f"\ndry run: {len(jobs)} folder(s) planned in {out}, nothing written")
        return 0

    today = dt.date.today().isoformat()
    written = []
    for job in jobs:
        if job.action.startswith("skip"):
            continue
        header = f"{job.name} -- generated by make_job_folders.py on {today} from {Path(args.incar).name}"
        if changes:
            header += f" ({changes})"
        incar = apply_changes(base_incar, {"SYSTEM": job.name} if args.system_tag else None, None, header)
        write_job(job, incar, kpoints, render_job(job_template, args.job_prefix + job.name))
        written.append(job.name)

    out.mkdir(parents=True, exist_ok=True)
    manifest = write_manifest(out, jobs, args, changes)
    print(f"\nwrote {len(written)} folder(s) in {out}"
          + (f", skipped {len(jobs) - len(written)} existing" if len(written) < len(jobs) else ""))
    print(f"manifest : {manifest}")
    if written:
        print(f"submit   : bash {write_submit_script(out, written)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
