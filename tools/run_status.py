"""One line per run folder: did the VASP run finish, and did it converge?

Why
---
"The job finished" and "the result is usable" are two different statements.
A VASP job can end without error while

* the last electronic loop hit NELM without reaching EDIFF (the energy and
  forces of that step are not converged);
* the relaxation stopped at NSW ionic steps without reaching EDIFFG (the
  geometry is not relaxed);
* the job was killed by the wall time or crashed (no timing block at the end
  of OUTCAR).

``run_status.py`` scans a campaign and reports, for each run folder:

* ``outcar``   -- an OUTCAR exists;
* ``finished`` -- the OUTCAR ends with VASP's timing block
  ("General timing and accounting informations for this job");
* ``scf``      -- the *last* electronic loop converged
  ("aborting loop because EDIFF is reached") or not
  ("aborting loop EDIFF was not reached (unconverged)", VASP 6; for VASP 5,
  which prints no message, a loop that used all NELM iterations counts as
  unconverged);
* ``ionic``    -- "reached required accuracy" (relaxations only: IBRION = 1, 2, 3
  and NSW > 0);
* ``steps``    -- number of completed ionic steps in the current OUTCAR;
* ``cycles``   -- number of restart cycles archived as ``OUTCAR.cycle*``
  (see ``slurm_templates/vasp_relax_autorestart.sh``);
* ``energy``   -- the last ionic ``free energy TOTEN`` (eV);
* ``slurm``    -- the Slurm state of the job running in that folder
  (``--squeue``; matched by the job's working directory).

and a one-word ``status``: converged, running, queued, incomplete (no timing
block, Slurm state unknown), stopped (no timing block and no job in the queue),
scf-unconverged, ionic-unconverged, not-started.

The OUTCAR is read line by line, so multi-GB files are fine. This is the
campaign-level overview; for an in-depth diagnosis of one problematic run
(SCF history per ionic step, residual forces vs EDIFFG, selective dynamics,
error messages in the logs) use ``Run_Diagnostics`` in
https://github.com/israel-c-ribeiro/VASP_tools.

A run folder is a directory with an INCAR or an OUTCAR. Arguments may be run
folders, parent folders (searched recursively; the search does not descend
into a run folder) or quoted glob patterns.

Examples
--------
    python run_status.py runs/                       # every run below runs/
    python run_status.py "runs/*" --squeue           # add the Slurm state (on the cluster)
    python run_status.py runs/ --problems            # only what needs attention
    python run_status.py runs/ --csv status.csv      # also save the table

Exit code: 0 if every run is converged, 1 otherwise, 2 if no run folder was
found -- so ``python run_status.py runs/ && echo all done`` works in scripts.
Only the Python standard library is needed.
"""
from __future__ import annotations

import argparse
import csv
import getpass
import glob
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

MARK_TIMING = "General timing and accounting informations for this job"
MARK_SCF_OK = "aborting loop because EDIFF is reached"
MARK_SCF_BAD = "EDIFF was not reached"
MARK_IONIC_OK = "reached required accuracy"
MARK_FREE_ENERGY = "FREE ENERGIE OF THE ION-ELECTRON SYSTEM"
ITERATION = re.compile(r"Iteration\s+(\d+)\(\s*(\d+)\)")
TOTEN = re.compile(r"TOTEN\s*=\s*([-+]?\d+\.\d*(?:[EeDd][-+]?\d+)?)")
PARAMS = {"NELM": re.compile(r"\bNELM\s*=\s*(-?\d+)"),
          "NSW": re.compile(r"\bNSW\s*=\s*(-?\d+)"),
          "IBRION": re.compile(r"\bIBRION\s*=\s*(-?\d+)")}
RUN_MARKERS = ("INCAR", "OUTCAR")
ACTIVE = {"RUNNING", "COMPLETING", "CONFIGURING", "STAGE_OUT", "SIGNALING"}
WAITING = {"PENDING", "REQUEUED", "REQUEUE_HOLD", "REQUEUE_FED", "SUSPENDED", "RESV_DEL_HOLD"}


# ----------------------------------------------------------------------------- OUTCAR
@dataclass
class OutcarInfo:
    finished: bool = False
    scf_converged: bool | None = None
    ionic_converged: bool = False
    ionic_steps: int = 0
    energy: float | None = None
    nelm: int | None = None
    nsw: int | None = None
    ibrion: int | None = None

    @property
    def is_relaxation(self) -> bool:
        return (self.nsw or 0) > 0 and self.ibrion in (1, 2, 3)


def parse_outcar(path: Path) -> OutcarInfo:
    """Read the convergence markers of an OUTCAR in one streaming pass."""
    info = OutcarInfo()
    params: dict[str, int] = {}
    cur_ionic: int | None = None
    loop_state: bool | None = None       # state of the last electronic loop
    max_elec = 0
    loop_closed = False                  # an ionic-step energy followed the last loop
    want_toten = False
    with open(path, "rb") as fh:
        for raw in fh:
            line = raw.decode("latin-1")
            if "Iteration" in line:
                m = ITERATION.search(line)
                if m:
                    ionic, elec = int(m.group(1)), int(m.group(2))
                    if ionic != cur_ionic:
                        cur_ionic, loop_state, max_elec, loop_closed = ionic, None, 0, False
                    max_elec = max(max_elec, elec)
                    continue
            if "aborting loop" in line:
                if MARK_SCF_OK in line:
                    loop_state = True
                elif MARK_SCF_BAD in line:
                    loop_state = False
            elif MARK_FREE_ENERGY in line:
                info.ionic_steps += 1
                want_toten = loop_closed = True
            elif want_toten and "TOTEN" in line:
                m = TOTEN.search(line)
                info.energy = float(m.group(1).replace("D", "E").replace("d", "e")) if m else None
                want_toten = False
            elif MARK_IONIC_OK in line:
                info.ionic_converged = True
            elif MARK_TIMING in line:
                info.finished = True
            elif len(params) < len(PARAMS) and "=" in line:
                for key, rx in PARAMS.items():
                    if key not in params:
                        m = rx.search(line)
                        if m:
                            params[key] = int(m.group(1))
    info.nelm, info.nsw, info.ibrion = params.get("NELM"), params.get("NSW"), params.get("IBRION")
    info.scf_converged = loop_state
    if (loop_state is None and cur_ionic is not None and (loop_closed or info.finished)
            and info.nelm and max_elec >= info.nelm):
        info.scf_converged = False       # VASP 5: NELM exhausted without a message
    return info


# ----------------------------------------------------------------------------- folders
def natural_key(s: str) -> list:
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def is_run_folder(path: Path) -> bool:
    return any((path / m).is_file() for m in RUN_MARKERS)


def find_run_folders(items: list[str]) -> list[Path]:
    """Run folders from folders, parent folders and glob patterns (in natural order)."""
    found: list[Path] = []
    for item in items:
        matches = glob.glob(item, recursive=True) if any(c in item for c in "*?[") else [item]
        for m in matches:
            p = Path(m)
            if p.is_file():
                p = p.parent                       # e.g. "runs/*/OUTCAR"
            if not p.is_dir():
                continue
            if is_run_folder(p):
                found.append(p)
                continue
            for dirpath, dirnames, filenames in os.walk(p):
                if any(mk in filenames for mk in RUN_MARKERS):
                    found.append(Path(dirpath))
                    dirnames[:] = []               # do not descend into a run folder
                else:
                    dirnames[:] = [d for d in dirnames if not d.startswith(".")]
    unique = {f.resolve(): f for f in found}
    return sorted(unique.values(), key=lambda f: natural_key(f.as_posix()))


def count_cycles(folder: Path) -> int:
    return sum(1 for f in folder.glob("OUTCAR.cycle*") if re.fullmatch(r"OUTCAR\.cycle\d+", f.name))


# ----------------------------------------------------------------------------- Slurm
def query_squeue(user: str | None = None) -> list[dict[str, str]] | None:
    """Jobs of *user* from ``squeue`` (None if squeue is unavailable or fails)."""
    exe = shutil.which("squeue")
    if exe is None:
        return None
    cmd = [exe, "-h", "-o", "%i|%T|%j|%Z", "-u", user or getpass.getuser()]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"warning: squeue failed: {exc}", file=sys.stderr)
        return None
    if res.returncode != 0:
        print(f"warning: squeue failed: {res.stderr.strip()}", file=sys.stderr)
        return None
    jobs = []
    for line in res.stdout.splitlines():
        parts = line.strip().split("|", 3)
        if len(parts) == 4:
            jobs.append(dict(zip(("id", "state", "name", "workdir"), parts)))
    return jobs


def slurm_states(folders: list[Path], jobs: list[dict[str, str]]) -> dict[Path, str]:
    """Map run folders to 'STATE (jobid)' by working directory, else by a unique job name."""
    by_dir: dict[str, str] = {}
    by_name: dict[str, list[str]] = {}
    for j in jobs:
        label = f"{j['state']} ({j['id']})"
        by_dir[os.path.realpath(j["workdir"])] = label
        by_name.setdefault(j["name"], []).append(label)
    states = {}
    for f in folders:
        label = by_dir.get(os.path.realpath(f))
        if label is None and len(by_name.get(f.name, [])) == 1:
            label = by_name[f.name][0]
        states[f] = label or ""
    return states


# ----------------------------------------------------------------------------- status
@dataclass
class RunStatus:
    run: str
    status: str
    outcar: bool
    finished: bool
    scf: str
    ionic: str
    steps: int
    cycles: int
    energy: str
    slurm: str


def classify(info: OutcarInfo | None, slurm: str | None) -> str:
    """One-word status from the OUTCAR markers and (optionally) the Slurm state."""
    state = (slurm or "").split(" ")[0]
    if state in ACTIVE:
        return "running"
    if state in WAITING:
        return "queued"
    if info is None:
        return "not-started"
    if not info.finished:
        return "stopped" if slurm == "" else "incomplete"
    if info.scf_converged is False:
        return "scf-unconverged"
    if info.is_relaxation and not info.ionic_converged:
        return "ionic-unconverged"
    return "converged"


def _yes_no(value: bool | None) -> str:
    return "-" if value is None else ("yes" if value else "no")


def status_of(folder: Path, slurm: str | None, label: str) -> RunStatus:
    outcar = folder / "OUTCAR"
    info = parse_outcar(outcar) if outcar.is_file() else None
    ionic = "-" if info is None or not info.is_relaxation else _yes_no(info.ionic_converged)
    energy = f"{info.energy:.6f}" if info and info.energy is not None else "-"
    return RunStatus(run=label, status=classify(info, slurm), outcar=info is not None,
                     finished=bool(info and info.finished),
                     scf=_yes_no(info.scf_converged) if info else "-", ionic=ionic,
                     steps=info.ionic_steps if info else 0, cycles=count_cycles(folder),
                     energy=energy, slurm=slurm if slurm is not None else "")


def display_name(folder: Path) -> str:
    try:
        return Path(os.path.relpath(folder)).as_posix()
    except ValueError:                             # other drive on Windows
        return folder.as_posix()


def print_table(rows: list[RunStatus], with_slurm: bool) -> None:
    head = ["run", "status", "outcar", "finished", "scf", "ionic", "steps", "cycles", "energy_eV"]
    if with_slurm:
        head.append("slurm")
    table = []
    for r in rows:
        line = [r.run, r.status, _yes_no(r.outcar), _yes_no(r.finished), r.scf, r.ionic,
                str(r.steps), str(r.cycles), r.energy]
        if with_slurm:
            line.append(r.slurm or "-")
        table.append(line)
    widths = [max(len(x[i]) for x in table + [head]) for i in range(len(head))]
    right = {"steps", "cycles", "energy_eV"}
    fmt = "  ".join(f"{{:{'>' if h in right else '<'}{w}}}" for h, w in zip(head, widths))
    print(fmt.format(*head))
    print(fmt.format(*("-" * w for w in widths)))
    for line in table:
        print(fmt.format(*line))


def write_csv(rows: list[RunStatus], target: str) -> None:
    fields = list(asdict(rows[0]).keys()) if rows else list(RunStatus.__dataclass_fields__)
    fh = sys.stdout if target == "-" else open(target, "w", encoding="utf-8", newline="")
    try:
        w = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow(asdict(r))
    finally:
        if fh is not sys.stdout:
            fh.close()


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="run_status.py",
        description="Report, for every VASP run folder, whether it finished and converged.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  python run_status.py runs/
  python run_status.py "runs/*" --squeue           # on the cluster: add the Slurm state
  python run_status.py runs/ --problems --csv status.csv

statuses:
  converged          finished, last SCF converged, relaxation reached EDIFFG
  running / queued   Slurm says so (--squeue)
  incomplete         OUTCAR without the final timing block, Slurm state unknown
  stopped            OUTCAR without the timing block and no job in the queue
  scf-unconverged    the last electronic loop did not reach EDIFF
  ionic-unconverged  relaxation ended (NSW) without 'reached required accuracy'
  not-started        no OUTCAR yet

exit code: 0 all converged, 1 otherwise, 2 no run folder found.
In-depth diagnosis of a single run: Run_Diagnostics in
https://github.com/israel-c-ribeiro/VASP_tools""")
    ap.add_argument("paths", nargs="*", default=["."], help="run folders, parent folders or quoted globs")
    ap.add_argument("--squeue", action="store_true", help="add the Slurm state of each folder (needs squeue)")
    ap.add_argument("--user", help="Slurm user for --squeue (default: you)")
    ap.add_argument("--problems", action="store_true", help="show only runs that are not converged")
    ap.add_argument("--csv", metavar="FILE", help="also write the table as CSV ('-' = standard output)")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    folders = find_run_folders(args.paths)
    if not folders:
        print("no run folder (directory with INCAR or OUTCAR) found", file=sys.stderr)
        return 2

    states: dict[Path, str] | None = None
    if args.squeue:
        jobs = query_squeue(args.user)
        if jobs is None:
            print("warning: squeue not available -- Slurm state not shown", file=sys.stderr)
        else:
            states = slurm_states(folders, jobs)

    rows = [status_of(f, states.get(f) if states is not None else None, display_name(f)) for f in folders]
    shown = [r for r in rows if r.status != "converged"] if args.problems else rows
    to_stdout = args.csv == "-"                    # CSV on stdout: keep the table out of it
    if shown and not to_stdout:
        print_table(shown, with_slurm=states is not None)
    counts: dict[str, int] = {}
    for r in rows:
        counts[r.status] = counts.get(r.status, 0) + 1
    summary = ", ".join(f"{n} {s}" for s, n in sorted(counts.items(), key=lambda kv: -kv[1]))
    print(f"\n{len(rows)} run(s): {summary}", file=sys.stderr if to_stdout else sys.stdout)
    if args.csv:
        write_csv(shown, args.csv)
    return 0 if all(r.status == "converged" for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
