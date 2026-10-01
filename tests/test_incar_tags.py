"""incar_tags: statement-level INCAR editing that keeps the rest of the file."""
from __future__ import annotations

import pytest

import incar_tags as it

INCAR = """# relaxation settings
SYSTEM  = "bulk Si # test"
ENCUT   = 400        # plane-wave cutoff
ISMEAR  = 0 ; SIGMA = 0.05   ! smearing
IBRION  = 2
MAGMOM  = 1 1 \\
          2 2
NSW     = 50
"""


def test_parse_and_get():
    tags = it.parse_incar(INCAR)
    assert tags["ENCUT"] == "400"
    assert tags["SIGMA"] == "0.05"
    assert tags["SYSTEM"] == '"bulk Si # test"'          # '#' inside quotes is not a comment
    assert tags["MAGMOM"] == "1 1 2 2"                   # continuation joined
    assert it.get_tag(INCAR, "encut") == "400"           # case-insensitive
    assert it.get_tag(INCAR, "LORBIT") is None
    assert it.get_tag(INCAR, "LORBIT", "11") == "11"


def test_set_existing_keeps_comment_and_rest():
    new = it.set_tag(INCAR, "ENCUT", 520)
    assert "ENCUT   = 520        # plane-wave cutoff\n" in new
    # every other line untouched
    assert new.replace("ENCUT   = 520", "ENCUT   = 400") == INCAR


def test_set_in_multi_statement_line():
    new = it.set_tag(INCAR, "SIGMA", "0.01")
    assert "ISMEAR  = 0 ; SIGMA = 0.01   ! smearing" in new
    assert it.get_tag(new, "ISMEAR") == "0"


def test_set_missing_appends():
    new = it.set_tag(INCAR, "lorbit", "11", comment="projected DOS")
    assert new.endswith("LORBIT = 11   # projected DOS\n")
    assert it.set_tag("ENCUT = 400", "NSW", 0) == "ENCUT = 400\nNSW = 0\n"


def test_set_continued_value():
    new = it.set_tag(INCAR, "MAGMOM", "4*0.0")
    assert it.get_tag(new, "MAGMOM") == "4*0.0"
    assert "2 2" not in new
    assert it.get_tag(new, "NSW") == "50"


def test_drop_single_line_removes_its_comment():
    new = it.drop_tag(INCAR, "ENCUT")
    assert "ENCUT" not in new and "plane-wave cutoff" not in new
    assert new.count("\n") == INCAR.count("\n") - 1


def test_drop_from_multi_statement_line_keeps_others():
    new = it.drop_tag(INCAR, "ISMEAR")
    assert "SIGMA = 0.05   ! smearing\n" in new
    assert it.get_tag(new, "ISMEAR") is None
    new = it.drop_tag(INCAR, "sigma")
    assert "ISMEAR  = 0 ! smearing\n" in new


def test_drop_absent_tag_is_noop():
    assert it.drop_tag(INCAR, "LORBIT") == INCAR


def test_duplicates_are_reported_and_all_set():
    text = "ENCUT = 400\nNSW = 1\nENCUT = 450\n"
    assert it.find_duplicates(text) == ["ENCUT"]
    new = it.set_tag(text, "ENCUT", "520")
    assert new.count("ENCUT = 520") == 2


def test_crlf_is_normalised():
    new = it.set_tag("ENCUT = 400\r\nNSW = 0\r\n", "NSW", "5")
    assert "\r" not in new and new == "ENCUT = 400\nNSW = 5\n"


def test_apply_changes_with_header():
    new = it.apply_changes(INCAR, {"NSW": "0", "IBRION": "-1"}, ["MAGMOM"], header="single point")
    assert new.startswith("# single point\n")
    tags = it.parse_incar(new)
    assert tags["NSW"] == "0" and tags["IBRION"] == "-1" and "MAGMOM" not in tags


@pytest.mark.parametrize("bad", ["ENCUT", "=5", "1TAG=3", "ENCUT="])
def test_parse_assignment_rejects(bad):
    with pytest.raises(ValueError):
        it.parse_assignment(bad)


def test_parse_assignment_ok():
    assert it.parse_assignment("magmom = 2*0.6") == ("MAGMOM", "2*0.6")
    with pytest.raises(ValueError):
        it.set_tag(INCAR, "BAD TAG", "1")


# ----------------------------------------------------------------------------- command line
def _make_runs(tmp_path, n=3):
    files = []
    for i in range(n):
        d = tmp_path / f"run{i}"
        d.mkdir()
        (d / "INCAR").write_text(INCAR, encoding="utf-8")
        files.append(d / "INCAR")
    return files


def test_cli_set_drop_with_provenance_and_backup(tmp_path, capsys):
    files = _make_runs(tmp_path)
    rc = it.main([str(tmp_path / "run*" / "INCAR"), "--set", "NSW=0", "--drop", "EDIFFG",
                  "--drop", "ibrion", "--provenance", "--note", "static", "--backup"])
    assert rc == 0
    for f in files:
        text = f.read_bytes().decode()
        assert text.startswith("# ") and "incar_tags.py: set NSW=0; drop EDIFFG, IBRION (static)" in text
        assert it.get_tag(text, "NSW") == "0" and it.get_tag(text, "IBRION") is None
        assert "\r" not in text
        assert f.with_name("INCAR.bak").read_text(encoding="utf-8") == INCAR
    assert "changed 3 of 3" in capsys.readouterr().err


def test_cli_dry_run_writes_nothing(tmp_path, capsys):
    (f,) = _make_runs(tmp_path, 1)
    rc = it.main([str(f.parent), "--set", "ENCUT=600", "--dry-run"])
    assert rc == 0
    assert f.read_text(encoding="utf-8") == INCAR
    out = capsys.readouterr().out
    assert "-ENCUT   = 400" in out and "+ENCUT   = 600" in out


def test_cli_get_table(tmp_path, capsys):
    _make_runs(tmp_path, 2)
    rc = it.main([str(tmp_path / "run*"), "--get", "ENCUT", "--get", "LORBIT"])
    assert rc == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].split() == ["file", "ENCUT", "LORBIT"]
    assert out[2].split()[1:] == ["400", "-"]


def test_cli_errors(tmp_path, capsys):
    assert it.main([str(tmp_path / "nothing*")]) == 2               # nothing to do / no match
    assert it.main([str(tmp_path / "nothing*"), "--get", "ENCUT"]) == 2
    assert it.main([str(tmp_path / "INCAR"), "--get", "ENCUT"]) == 1  # missing file
