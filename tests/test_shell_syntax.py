"""Every shell script: LF line endings, a bash shebang, and valid syntax (bash -n)."""
from __future__ import annotations

import subprocess

import pytest

from helpers import REPO, posix

SCRIPTS = sorted(p for p in REPO.rglob("*.sh") if ".git" not in p.parts)


def test_scripts_found():
    names = {p.name for p in SCRIPTS}
    assert {"vasp_single_point.sh", "vasp_relax_autorestart.sh", "vasp_job_array.sh", "fhi_aims.sh",
            "python_gpu_or_cpu.sh", "fetch_runs.sh", "submit_all.sh"} <= names


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.relative_to(REPO).as_posix())
def test_lf_and_shebang(script):
    data = script.read_bytes()
    assert b"\r" not in data, "CRLF line endings: bash on the cluster would fail"
    assert data.startswith(b"#!/usr/bin/env bash\n")


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.relative_to(REPO).as_posix())
def test_bash_syntax(script, bash):
    res = subprocess.run([bash, "-n", posix(script)], capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr


@pytest.mark.parametrize("script", sorted((REPO / "slurm_templates").glob("*.sh")), ids=lambda p: p.name)
def test_templates_have_placeholders_not_real_values(script):
    text = script.read_text(encoding="utf-8")
    assert "#SBATCH --account=<your_account>" in text
    assert ">>> EDIT" in text
    for line in text.splitlines():               # sbatch would read a trailing comment as part of the value
        if line.startswith("#SBATCH"):
            assert " #" not in line, line
