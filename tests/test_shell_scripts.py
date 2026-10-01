"""Behaviour of the shell scripts, with fake vasp / sbatch / rsync programs.

* vasp_relax_autorestart.sh: cycles, archives, CONTCAR -> POSCAR, resume after a
  killed job, exit codes 0/1/2/3, and the wall-time signal (POSIX only: exit 4);
* submit_all.sh: skips finished runs, resubmits relaxations that stopped at NSW;
* fetch_runs.sh: rsync options, filter order, configuration file.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from helpers import DEMO, TEMPLATES, TOOLS, clean_env, posix
from outcar_factory import fake_outcar

RELAX = TEMPLATES / "vasp_relax_autorestart.sh"

FAKE_VASP = r"""#!/usr/bin/env bash
# fake VASP: counts its calls; converges on call CONVERGE_ON; FAKE_CRASH=1 exits 7;
# FAKE_WAIT_STOPCAR=1 waits (max 30 s) for a STOPCAR, like VASP with LSTOP.
n=$(( $(cat .fake_calls 2>/dev/null || echo 0) + 1 )); echo "$n" > .fake_calls
if [ "${FAKE_CRASH:-0}" = 1 ]; then echo "fake crash" >&2; exit 7; fi
if [ -n "${FAKE_WAIT_STOPCAR:-}" ]; then
  touch .fake_started
  for i in $(seq 1 300); do [ -f STOPCAR ] && break; sleep 0.1; done
fi
{ echo " fake OUTCAR, call $n"
  if [ "$n" = "${CONVERGE_ON:-0}" ]; then echo " reached required accuracy - stopping structural energy minimisation"; fi
  echo " General timing and accounting informations for this job:"; } > OUTCAR
echo "   1 F= -$n" > OSZICAR
sed "1s/.*/geometry after call $n/" POSCAR > CONTCAR
"""


def _write(path: Path, text: str) -> Path:
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    path.chmod(0o755)
    return path


def _relax_folder(tmp_path: Path, incar: str = "IBRION = 2\nNSW = 5\n") -> Path:
    d = tmp_path / "run"
    d.mkdir()
    _write(d / "POSCAR", (DEMO / "structures" / "Si.vasp").read_text())
    _write(d / "INCAR", incar)
    _write(d / "KPOINTS", "k\n")
    _write(d / "POTCAR", "p\n")
    _write(d / "fake_vasp.sh", FAKE_VASP)
    return d


def _relax(bash: str, folder: Path, **env: str) -> subprocess.CompletedProcess:
    return subprocess.run([bash, posix(RELAX)], cwd=folder, capture_output=True, text=True, timeout=120,
                          env=clean_env(VASP_RUN="bash fake_vasp.sh", **env))


def _first_line(path: Path) -> str:
    return path.read_text().splitlines()[0]


# ----------------------------------------------------------------------------- autorestart
def test_relax_converges_in_second_cycle(bash, tmp_path):
    d = _relax_folder(tmp_path)
    res = _relax(bash, d, CONVERGE_ON="2")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "converged in cycle 2" in res.stdout
    assert {p.name for p in d.glob("*.cycle*")} == {f"{f}.cycle{k}" for f in ("OUTCAR", "OSZICAR", "CONTCAR")
                                                    for k in (1, 2)}
    assert _first_line(d / "POSCAR") == "geometry after call 1"          # restarted from CONTCAR
    assert _first_line(d / "POSCAR.orig").startswith("Si diamond")        # original kept


def test_relax_not_converged_exit_3(bash, tmp_path):
    d = _relax_folder(tmp_path)
    res = _relax(bash, d, MAX_CYCLES="2")
    assert res.returncode == 3
    assert (d / ".fake_calls").read_text().strip() == "2"
    assert _first_line(d / "POSCAR") == "geometry after call 2"


def test_relax_resumes_after_killed_job(bash, tmp_path):
    d = _relax_folder(tmp_path)
    _write(d / "OUTCAR", " killed by the wall time, no timing block\n")
    _write(d / "CONTCAR", (d / "POSCAR").read_text().replace("Si diamond", "geometry from killed job", 1))
    res = _relax(bash, d, CONVERGE_ON="1")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "previous run archived as cycle 1" in res.stdout
    assert (d / "OUTCAR.cycle1").read_text().startswith(" killed")
    assert (d / "OUTCAR.cycle2").exists()
    assert _first_line(d / "POSCAR").startswith("geometry from killed job")


def test_relax_already_converged_does_nothing(bash, tmp_path):
    d = _relax_folder(tmp_path)
    _write(d / "OUTCAR", " reached required accuracy - stopping structural energy minimisation\n")
    res = _relax(bash, d)
    assert res.returncode == 0 and "nothing to do" in res.stdout
    assert not (d / ".fake_calls").exists()


def test_relax_crash_exit_2(bash, tmp_path):
    res = _relax(bash, _relax_folder(tmp_path), FAKE_CRASH="1")
    assert res.returncode == 2 and "VASP exited with code 7" in res.stdout


def test_relax_refuses_single_point_incar(bash, tmp_path):
    res = _relax(bash, _relax_folder(tmp_path, incar="NSW = 0\n"))
    assert res.returncode == 1 and "not a relaxation" in res.stderr


def test_relax_missing_input_exit_1(bash, tmp_path):
    d = _relax_folder(tmp_path)
    (d / "POTCAR").unlink()
    res = _relax(bash, d)
    assert res.returncode == 1 and "POTCAR missing" in res.stderr


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_relax_walltime_signal_exit_4(bash, tmp_path):
    d = _relax_folder(tmp_path)
    proc = subprocess.Popen([bash, posix(RELAX)], cwd=d, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, env=clean_env(VASP_RUN="bash fake_vasp.sh", FAKE_WAIT_STOPCAR="1"))
    for _ in range(300):
        if (d / ".fake_started").exists():
            break
        time.sleep(0.1)
    proc.send_signal(signal.SIGUSR1)                 # what Slurm does with --signal=B:USR1@600
    out, _ = proc.communicate(timeout=60)
    assert proc.returncode == 4, out
    assert "wall-time signal received" in out
    assert not (d / "STOPCAR").exists()
    assert _first_line(d / "POSCAR") == "geometry after call 1"


# ----------------------------------------------------------------------------- submit_all.sh
def _fake_bin(tmp_path: Path) -> Path:
    b = tmp_path / "bin"
    b.mkdir()
    _write(b / "sbatch", '#!/usr/bin/env bash\necho "Submitted batch job 4242"\necho "$*" > submitted.marker\n')
    _write(b / "rsync", '#!/usr/bin/env bash\nfor a in "$@"; do echo "ARG:$a"; done\n')
    return b


def _path_env(bin_dir: Path, **extra: str) -> dict[str, str]:
    return clean_env(PATH=str(bin_dir) + os.pathsep + os.environ.get("PATH", ""), **extra)


def _tree(tmp_path: Path) -> Path:
    root = tmp_path / "tree"
    outcars = {"fresh": None,
               "single_point_done": fake_outcar([-1.0]),
               "relax_stopped_at_nsw": fake_outcar([-1.0, -1.1], nsw=2, ibrion=2),
               "relax_converged": fake_outcar([-1.0, -1.1], nsw=9, ibrion=2, ionic_converged=True)}
    for name, outcar in outcars.items():
        (root / name).mkdir(parents=True)
        _write(root / name / "job.sh", "#!/usr/bin/env bash\n")
        if outcar:
            _write(root / name / "OUTCAR", outcar)
    return root


def test_submit_all_dry_run(bash, tmp_path):
    root = _tree(tmp_path)
    res = subprocess.run([bash, posix(TOOLS / "submit_all.sh"), "-n", posix(root)],
                         capture_output=True, text=True, timeout=60, env=clean_env())
    assert res.returncode == 0, res.stderr
    out = res.stdout
    lines = out.splitlines()
    assert sum(line.startswith("would run ") for line in lines) == 2
    assert "relax_stopped_at_nsw: sbatch job.sh" in out and "fresh: sbatch job.sh" in out
    assert sorted(line.split("/")[-1] for line in lines if line.startswith("done ")) == \
        ["relax_converged", "single_point_done"]
    assert not list(root.rglob("submitted.marker"))


def test_submit_all_submits_with_extra_options(bash, tmp_path):
    root = _tree(tmp_path)
    res = subprocess.run([bash, posix(TOOLS / "submit_all.sh"), posix(root), "--", "--partition=debug"],
                         capture_output=True, text=True, timeout=60, env=_path_env(_fake_bin(tmp_path)))
    assert res.returncode == 0, res.stderr
    markers = sorted(p.parent.name for p in root.rglob("submitted.marker"))
    assert markers == ["fresh", "relax_stopped_at_nsw"]
    assert (root / "fresh" / "submitted.marker").read_text().strip() == "--partition=debug job.sh"
    assert "submitted 2, done 2" in res.stdout


def test_submit_all_max(bash, tmp_path):
    root = _tree(tmp_path)
    res = subprocess.run([bash, posix(TOOLS / "submit_all.sh"), "--max", "1", "--force", posix(root)],
                         capture_output=True, text=True, timeout=60, env=_path_env(_fake_bin(tmp_path)))
    assert res.returncode == 0
    assert len(list(root.rglob("submitted.marker"))) == 1 and "(+3 left out by --max)" in res.stdout


# ----------------------------------------------------------------------------- fetch_runs.sh
def _fetch(bash, tmp_path, *args, **env):
    return subprocess.run([bash, posix(TOOLS / "fetch_runs.sh"), *args], cwd=tmp_path, capture_output=True,
                          text=True, timeout=60, env=_path_env(tmp_path / "bin", **env))


def test_fetch_runs_options_and_filter_order(bash, tmp_path):
    _fake_bin(tmp_path)
    res = _fetch(bash, tmp_path, "-s", "-n", "-x", "vasprun.xml", "campaign1", posix(tmp_path / "local"), "Si_*",
                 REMOTE="user@login.cluster.example.org", GATEWAY="user@gateway.example.org",
                 REMOTE_ROOT="/path/to/runs")
    assert res.returncode == 0, res.stderr
    args = [line[4:] for line in res.stdout.splitlines() if line.startswith("ARG:")]
    assert args[0] == "-avzhP" and "--dry-run" in args
    assert args.index("--exclude=WAVECAR") < args.index("--include=/Si_*/") < args.index("--exclude=/*")
    assert args.index("--exclude=vasprun.xml") < args.index("--include=/Si_*/**")
    assert args[args.index("-e") + 1] == "ssh -J user@gateway.example.org"
    assert args[-2] == "user@login.cluster.example.org:/path/to/runs/campaign1/"
    assert args[-1].endswith("/local/")


def test_fetch_runs_config_file_and_env_override(bash, tmp_path):
    _fake_bin(tmp_path)
    _write(tmp_path / "fetch_runs.conf", 'REMOTE="user@login.cluster.example.org"\nGATEWAY=""\n'
                                         'REMOTE_ROOT="/path/to/runs"\nLOCAL_ROOT="/tmp/x"\n')
    res = _fetch(bash, tmp_path, "-n", "/abs/remote/dir", "out", REMOTE_ROOT="/ignored")
    assert res.returncode == 0, res.stderr
    args = [line[4:] for line in res.stdout.splitlines() if line.startswith("ARG:")]
    assert args[args.index("-e") + 1] == "ssh"                      # no gateway: direct connection
    assert args[-2] == "user@login.cluster.example.org:/abs/remote/dir/"
    res = _fetch(bash, tmp_path, "-n", "sub", "out", REMOTE="other@login.cluster.example.org")
    args = [line[4:] for line in res.stdout.splitlines() if line.startswith("ARG:")]
    assert args[-2] == "other@login.cluster.example.org:/path/to/runs/sub/"   # environment wins


def test_fetch_runs_usage_errors(bash, tmp_path):
    _fake_bin(tmp_path)
    assert _fetch(bash, tmp_path, "only_one_arg").returncode == 2
    res = _fetch(bash, tmp_path, "a", "b", REMOTE="")
    assert res.returncode == 2 and "REMOTE is not set" in res.stderr
    assert _fetch(bash, tmp_path, "--help").returncode == 0
