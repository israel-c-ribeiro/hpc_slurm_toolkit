"""Fixtures: the tools on sys.path and a working bash (tests needing it are skipped otherwise)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from helpers import TOOLS, find_bash  # noqa: E402

sys.path.insert(0, str(TOOLS))


@pytest.fixture(scope="session")
def bash() -> str:
    exe = find_bash()
    if exe is None:
        pytest.skip("bash is not available")
    return exe
