# Status demo

Six run folders with **synthetic** OUTCAR files (they reproduce only the lines
that `tools/run_status.py` reads; they are not real calculations). Each one is a
situation you meet in every campaign:

| Folder | Situation | Status reported |
| --- | --- | --- |
| `Si_relax_converged` | relaxation converged in its 2nd restart cycle (`OUTCAR.cycle1`, `OUTCAR.cycle2`) | `converged` |
| `Si_relax_nsw_reached` | relaxation stopped at `NSW` without reaching `EDIFFG` | `ionic-unconverged` |
| `NaCl_scf_unconverged` | single point whose electronic loop hit `NELM` (VASP 6 message) | `scf-unconverged` |
| `GaAs_vasp5_nelm_exhausted` | the same with VASP 5, which prints no message: all `NELM` iterations used | `scf-unconverged` |
| `Fe_bcc_killed_by_walltime` | OUTCAR stops in the middle of an electronic loop, no timing block | `incomplete` (`stopped` with `--squeue` if no job is in the queue) |
| `MAPbI3_not_started` | inputs only | `not-started` |

Try it from the repository root:

```bash
python tools/run_status.py examples/status_demo
python tools/run_status.py examples/status_demo --problems --csv -
echo "exit code: $?"          # 1: not everything is converged
```

```text
run                                             status             outcar  finished  scf  ionic  steps  cycles   energy_eV
----------------------------------------------  -----------------  ------  --------  ---  -----  -----  ------  ----------
examples/status_demo/Fe_bcc_killed_by_walltime  incomplete         yes     no        -    no         2       0  -16.452346
examples/status_demo/GaAs_vasp5_nelm_exhausted  scf-unconverged    yes     yes       no   -          1       0   -8.912346
examples/status_demo/MAPbI3_not_started         not-started        no      no        -    -          0       0           -
examples/status_demo/NaCl_scf_unconverged       scf-unconverged    yes     yes       no   -          1       0   -6.712346
examples/status_demo/Si_relax_converged         converged          yes     yes       yes  yes        2       2  -10.822901
examples/status_demo/Si_relax_nsw_reached       ionic-unconverged  yes     yes       yes  no         3       0  -10.822346

6 run(s): 2 scf-unconverged, 1 incomplete, 1 not-started, 1 converged, 1 ionic-unconverged
```

The same files are used by `tests/test_run_status.py`; they were generated
with `tests/outcar_factory.py`.
