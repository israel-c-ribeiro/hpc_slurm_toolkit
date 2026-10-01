"""Helpers shared by the tests: repository paths, a working bash, a clean environment."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TOOLS = REPO / "tools"
TEMPLATES = REPO / "slurm_templates"
DEMO = REPO / "examples" / "demo_campaign"
STATUS_DEMO = REPO / "examples" / "status_demo"


def _bash_candidates() -> list[Path]:
    cands: list[Path] = []
    if os.name == "nt":
        git = shutil.which("git")
        if git:
            for root in list(Path(git).resolve().parents)[:3]:
                cands += [root / "bin" / "bash.exe", root / "usr" / "bin" / "bash.exe"]
        cands.append(Path(r"C:\Program Files\Git\bin\bash.exe"))
    found = shutil.which("bash")
    if found:
        cands.append(Path(found))
    if os.name == "nt":                       # the WSL launcher cannot read Windows paths
        cands = [c for c in cands if "system32" not in str(c).lower()]
    return cands


def find_bash() -> str | None:
    """A bash that runs (Git Bash on Windows), or None."""
    for cand in _bash_candidates():
        if not cand.is_file():
            continue
        try:
            res = subprocess.run([str(cand), "-c", "echo ok"], capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if res.returncode == 0 and res.stdout.strip() == "ok":
            return str(cand)
    return None


def clean_env(**extra: str) -> dict[str, str]:
    """The current environment without Slurm variables, plus *extra*."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("SLURM_")}
    env.update(extra)
    return env


def posix(path: Path) -> str:
    """A path in the form bash accepts on every platform."""
    return path.as_posix()
