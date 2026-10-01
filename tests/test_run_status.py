"""run_status: OUTCAR markers -> finished / SCF / ionic convergence -> one-word status."""
from __future__ import annotations

import csv
import io
from contextlib import redirect_stdout
from pathlib import Path

import pytest

import run_status as rs
from helpers import STATUS_DEMO
from outcar_factory import fake_outcar


def _run(tmp_path: Path, name: str, outcar: str | None, incar: str = "NSW = 0\n", cycles: int = 0) -> Path:
    d = tmp_path / name
    d.mkdir(parents=True)
    (d / "INCAR").write_text(incar)
    if outcar is not None:
        (d / "OUTCAR").write_text(outcar)
    for k in range(1, cycles + 1):
        (d / f"OUTCAR.cycle{k}").write_text("x")
    return d


# ----------------------------------------------------------------------------- parse_outcar
def test_finished_and_converged_relaxation(tmp_path):
    d = _run(tmp_path, "ok", fake_outcar([-10.1, -10.2, -10.25], nsw=50, ibrion=2, ionic_converged=True))
    info = rs.parse_outcar(d / "OUTCAR")
    assert info.finished and info.scf_converged and info.ionic_converged
    assert info.is_relaxation and info.ionic_steps == 3
    assert info.energy == pytest.approx(-10.25)
    assert (info.nelm, info.nsw, info.ibrion) == (60, 50, 2)
    assert rs.classify(info, None) == "converged"


def test_finished_but_scf_unconverged_vasp6(tmp_path):
    d = _run(tmp_path, "bad", fake_outcar([-5.0], last_scf="bad"))
    info = rs.parse_outcar(d / "OUTCAR")
    assert info.finished and info.scf_converged is False and not info.is_relaxation
    assert rs.classify(info, None) == "scf-unconverged"


def test_scf_unconverged_vasp5_without_message(tmp_path):
    d = _run(tmp_path, "nelm", fake_outcar([-5.0], nelm=30, last_scf="silent"))
    assert rs.parse_outcar(d / "OUTCAR").scf_converged is False


def test_only_the_last_loop_counts(tmp_path):
    # an unconverged loop in an early ionic step does not matter if the last loop converged
    text = fake_outcar([-1.0, -1.1], nsw=5, ibrion=2, ionic_converged=True)
    text = text.replace("aborting loop because EDIFF is reached",
                        "aborting loop EDIFF was not reached (unconverged)", 1)
    d = _run(tmp_path, "mixed", text)
    info = rs.parse_outcar(d / "OUTCAR")
    assert "EDIFF was not reached" in text and info.ionic_steps == 2
    assert info.scf_converged is True


def test_still_running(tmp_path):
    d = _run(tmp_path, "running", fake_outcar([-3.0, -3.1], nsw=20, ibrion=2, last_scf="running", finished=False))
    info = rs.parse_outcar(d / "OUTCAR")
    assert not info.finished and info.scf_converged is None and info.ionic_steps == 1
    assert rs.classify(info, None) == "incomplete"                 # no Slurm information
    assert rs.classify(info, "RUNNING (123)") == "running"
    assert rs.classify(info, "") == "stopped"                     # squeue checked: not in the queue


def test_ionic_not_converged(tmp_path):
    d = _run(tmp_path, "nsw", fake_outcar([-2.0, -2.1, -2.15], nsw=3, ibrion=2))
    info = rs.parse_outcar(d / "OUTCAR")
    assert info.finished and info.scf_converged and not info.ionic_converged
    assert rs.classify(info, None) == "ionic-unconverged"


def test_md_is_not_a_relaxation(tmp_path):
    d = _run(tmp_path, "md", fake_outcar([-2.0, -2.1], nsw=2, ibrion=0))
    assert rs.classify(rs.parse_outcar(d / "OUTCAR"), None) == "converged"


def test_overflowed_energy(tmp_path):
    text = fake_outcar([-1.5]).replace("TOTEN  =        -1.50000000 eV", "TOTEN  = ************** eV")
    d = _run(tmp_path, "overflow", text)
    assert rs.parse_outcar(d / "OUTCAR").energy is None


def test_queued_without_outcar():
    assert rs.classify(None, "PENDING (7)") == "queued"
    assert rs.classify(None, None) == "not-started"


# ----------------------------------------------------------------------------- folders + Slurm
def test_find_run_folders_prunes_inside_runs(tmp_path):
    a = _run(tmp_path / "campaign", "a2", None)
    b = _run(tmp_path / "campaign", "a10", None)
    (a / "step1").mkdir()
    (a / "step1" / "INCAR").write_text("NSW = 0\n")              # nested archive: not a separate run
    (tmp_path / "campaign" / ".hidden").mkdir()
    (tmp_path / "campaign" / ".hidden" / "INCAR").write_text("")
    found = rs.find_run_folders([str(tmp_path / "campaign")])
    assert found == [a, b]                                       # natural order: a2 before a10
    assert rs.find_run_folders([str(tmp_path / "campaign" / "*" / "INCAR")]) == [a, b]


def test_slurm_states_by_workdir_and_name(tmp_path):
    a = _run(tmp_path, "Si", None)
    b = _run(tmp_path, "NaCl", None)
    c = _run(tmp_path, "Fe", None)
    jobs = [{"id": "11", "state": "RUNNING", "name": "x", "workdir": str(a)},
            {"id": "12", "state": "PENDING", "name": "NaCl", "workdir": "/elsewhere"}]
    states = rs.slurm_states([a, b, c], jobs)
    assert states == {a: "RUNNING (11)", b: "PENDING (12)", c: ""}


def test_count_cycles(tmp_path):
    d = _run(tmp_path, "c", None, cycles=3)
    (d / "OUTCAR.cycle2.bak").write_text("")
    assert rs.count_cycles(d) == 3


# ----------------------------------------------------------------------------- main / exit codes
def _main(args):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = rs.main(args)
    return rc, buf.getvalue()


def test_main_all_converged_exit_0(tmp_path):
    _run(tmp_path, "a", fake_outcar([-1.0]))
    _run(tmp_path, "b", fake_outcar([-1.0, -1.1], nsw=9, ibrion=1, ionic_converged=True), cycles=2)
    rc, out = _main([str(tmp_path)])
    assert rc == 0 and "2 run(s): 2 converged" in out


def test_main_problems_and_csv(tmp_path):
    _run(tmp_path, "good", fake_outcar([-1.0]))
    _run(tmp_path, "bad", fake_outcar([-1.0], last_scf="bad"))
    _run(tmp_path, "new", None)
    target = tmp_path / "status.csv"
    rc, out = _main([str(tmp_path), "--problems", "--csv", str(target)])
    assert rc == 1
    assert "good" not in out.split("\n\n")[0] and "scf-unconverged" in out
    rows = list(csv.DictReader(target.open(encoding="utf-8")))
    assert {r["status"] for r in rows} == {"scf-unconverged", "not-started"}


def test_main_no_folder_exit_2(tmp_path):
    assert _main([str(tmp_path)])[0] == 2


def test_status_demo_examples():
    expected = {"Si_relax_converged": "converged", "Si_relax_nsw_reached": "ionic-unconverged",
                "NaCl_scf_unconverged": "scf-unconverged", "GaAs_vasp5_nelm_exhausted": "scf-unconverged",
                "Fe_bcc_killed_by_walltime": "incomplete", "MAPbI3_not_started": "not-started"}
    got = {f.name: rs.status_of(f, None, f.name).status for f in rs.find_run_folders([str(STATUS_DEMO)])}
    assert got == expected
