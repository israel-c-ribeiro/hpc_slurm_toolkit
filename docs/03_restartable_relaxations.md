# 3. Restartable relaxations

A geometry optimisation is the most common VASP job and the one most often
left half-done: it stops at `NSW` ionic steps, or the wall time kills it in the
middle of a step, and the "result" is simply the last geometry it reached.
This page explains why relaxations need restarts, what
[`vasp_relax_autorestart.sh`](../slurm_templates/vasp_relax_autorestart.sh)
does about it, and how to check that a relaxation really converged.

## Why a relaxation needs restarts

1. **NSW is a cap, not a target.** VASP stops after `NSW` ionic steps whether or
   not the forces are converged. Large or soft systems (molecular crystals,
   hybrid perovskites, surfaces) easily need more steps than you guessed.
2. **The wall time is finite.** A job killed by Slurm loses the ionic step in
   progress; without care it can also leave a truncated `CONTCAR`.
3. **Cell relaxations carry the Pulay stress of the starting cell.** VASP keeps
   the plane-wave basis of the *initial* volume during an `ISIF = 3` run; when the
   cell changes, the effective cutoff drifts and the stress is biased. The cure
   is to restart from the relaxed cell (new basis) until the volume no longer
   changes -- in addition to a generous `ENCUT` (about 1.3 x the largest `ENMAX`
   of the POTCARs).

The standard remedy is the same in all three cases: copy `CONTCAR` (the last
geometry) to `POSCAR` and run again. Doing it by hand for fifty structures is
tedious and error-prone; the template does it for you.

## What "converged" means

VASP prints `reached required accuracy - stopping structural energy minimisation`
in the OUTCAR when the `EDIFFG` criterion is met:

* `EDIFFG < 0` (recommended): every force component is below `|EDIFFG|` eV/Å,
  e.g. `EDIFFG = -0.01`;
* `EDIFFG > 0`: the energy change between two ionic steps is below `EDIFFG` eV.

Two caveats:

* the force criterion does **not** include the stress: after a cell relaxation
  also look at `external pressure` in the last OUTCAR (a few kB at most);
* every ionic step relies on a converged electronic loop. If the last loop hit
  `NELM` without reaching `EDIFF` (`aborting loop EDIFF was not reached`), the
  forces of that step are not trustworthy. `tools/run_status.py` reports both.

## The autorestart pattern

```text
            +-------------------------------+
            |  job starts                   |
            |  already converged? -> exit 0 |
            |  OUTCAR left by a killed job? |
            |    archive it, CONTCAR->POSCAR|
            +---------------+---------------+
                            |
        +-------------------v--------------------+
        |  cycle N:  run VASP                    |<-------------+
        |  archive OUTCAR/OSZICAR/CONTCAR.cycleN |              |
        +-------------------+--------------------+              |
                            |                                   |
          "reached required accuracy"? --yes--> exit 0          |
                            | no                                |
          VASP failed / CONTCAR unusable? --yes--> exit 2       |
                            | no                                |
                    CONTCAR -> POSCAR                           |
                            |                                   |
          wall-time signal received? --yes--> exit 4            |
                            | no                                |
          N < MAX_CYCLES? --yes---------------------------------+
                            | no
                         exit 3
```

### What is archived, and why

| File | Why keep one per cycle |
| --- | --- |
| `OUTCAR.cycleN` | the full record: forces, stress, SCF history of that cycle |
| `OSZICAR.cycleN` | compact energy per ionic step -- the quickest way to see whether the energy still goes down |
| `CONTCAR.cycleN` | the geometry at the end of the cycle, so you can go back if a later cycle goes astray |
| `POSCAR.orig` | the very first input structure (kept once) |

Add `vasprun.xml` to `ARCHIVE` if your post-processing needs it (it is large).
The `CONTCAR -> POSCAR` copy only happens when `CONTCAR` exists and has at
least a header and one atom line: an empty CONTCAR (crash before the first
ionic step) never overwrites a good POSCAR. Selective-dynamics flags travel
with the geometry, because VASP writes them into CONTCAR.

### Surviving the wall time

```bash
#SBATCH --signal=B:USR1@600
```

asks Slurm to send `SIGUSR1` to the job script (`B:` = the batch shell only)
600 seconds before the time limit. The script traps it and writes

```text
LSTOP = .TRUE.
```

into a `STOPCAR`. VASP checks for that file and stops cleanly at the end of the
current ionic step, writing a complete CONTCAR. (`LABORT = .TRUE.` would stop
after the current *electronic* step instead.) Two details make this work:

* bash only runs a trap between commands, so VASP is started in the background
  and the script `wait`s for it -- `wait` is interrupted by the signal, the trap
  runs, and the script waits again for VASP to finish its step;
* the margin (600 s) must be longer than one ionic step, or Slurm kills VASP
  before it gets there. For expensive steps (hybrid functionals) use 1800 or
  more.

A leftover `STOPCAR` would stop the next run immediately, so the script
deletes it at start-up and after each run.

### Continuing across jobs

Resubmitting the same script in the same folder continues where the last job
stopped: the cycle numbering goes on (`OUTCAR.cycle4`, ...), an OUTCAR that the
previous job could not archive (killed hard) is archived first, and its
`CONTCAR` becomes the new `POSCAR`. A folder that already converged exits
immediately with code 0, so `tools/submit_all.sh` can be rerun safely: it only
resubmits relaxations that stopped at `NSW` and never touches converged ones.

With `AUTO_RESUBMIT=1` the job resubmits itself after exit codes 3 and 4,
until `MAX_TOTAL_CYCLES` cycles have been done (a safety net against a
relaxation that never converges).

### Exit codes and what to do

| Code | Meaning | Action |
| --- | --- | --- |
| 0 | converged | check the pressure (cell relaxations) and move on |
| 1 | setup problem (missing input, VASP not found, `NSW = 0`) | read the `.err` file; fix and resubmit |
| 2 | VASP failed or left no usable CONTCAR | read the `.err` file and the end of OUTCAR; [VASP_tools/Run_Diagnostics](https://github.com/israel-c-ribeiro/VASP_tools) helps |
| 3 | not converged after `MAX_CYCLES` cycles | resubmit; if the energy hardly changes any more, look at the forces and the settings |
| 4 | stopped by the wall-time signal | resubmit (or ask for more time) |

## Worked example: relaxing bulk Si

1. Build the folder from the demo campaign, with a deliberately small `NSW`
   so that restarts happen:

   ```bash
   python tools/make_job_folders.py examples/demo_campaign/structures/Si.vasp --out runs \
       --incar examples/demo_campaign/INCAR --kpoints examples/demo_campaign/KPOINTS \
       --potcar-dir /path/to/potpaw_PBE \
       --job-template slurm_templates/vasp_relax_autorestart.sh \
       --set NSW=10 --set ENCUT=520
   ```

2. Copy `runs/` to the cluster, edit the `>>> EDIT` lines of `runs/Si/job.sh`
   (or of the template before step 1), and submit:

   ```bash
   cd /path/to/runs/Si && sbatch job.sh
   ```

3. The job log (`Si_<jobid>.out`: job name + job id; illustrative timings)
   shows the cycles:

   ```text
   === job 123456 (Si) ===
   command : srun vasp_std   (up to 3 cycles)

   [2026-10-01 10:00:02] cycle 1 (1 of 3 in this job)
   ...VASP output...
   [2026-10-01 10:04:40] not converged yet: CONTCAR -> POSCAR
   [2026-10-01 10:04:40] cycle 2 (2 of 3 in this job)
   ...VASP output...
   [2026-10-01 10:07:15] converged in cycle 2
   [2026-10-01 10:07:15] exit code 0 after 0h 7m 13s
   ```

4. Check the result:

   ```bash
   cd /path/to/runs/Si
   grep -l "reached required accuracy" OUTCAR.cycle*      # which cycle converged
   for f in OSZICAR.cycle*; do                            # energy at the end of each cycle
     echo "$f: $(grep -c 'F=' "$f") steps, F = $(grep 'F=' "$f" | tail -1 | awk '{print $3}') eV"
   done
   grep "volume of cell" OUTCAR.cycle1 | tail -1           # volume drift between cycles
   grep "volume of cell" OUTCAR.cycle2 | tail -1
   grep "external pressure" OUTCAR | tail -1              # should be close to 0 kB
   python /path/to/hpc_slurm_toolkit/tools/run_status.py /path/to/runs
   ```

   For a cell relaxation, a last cycle that converges in very few ionic steps
   with an unchanged volume is the sign that the Pulay stress is gone.

## When restarts do not help

Restarting continues a relaxation; it does not fix a bad one. If the energy
oscillates or rises over cycles:

* make sure each SCF converged (`run_status.py`; `scf-unconverged` runs need
  better electronic settings first);
* `IBRION = 2` (conjugate gradient) is robust far from the minimum;
  `IBRION = 1` (quasi-Newton) is efficient close to it but loses its history at
  every restart; reduce `POTIM` if steps overshoot;
* very flat energy surfaces (rotating molecules, soft modes) may need a looser
  `EDIFFG` or a better starting geometry (e.g. a pre-relaxation with a
  machine-learning potential);
* molecular dynamics (`IBRION = 0`), NEB and phonon runs need other restart
  logic: the template refuses `NSW = 0` but does not otherwise recognise them,
  so use [`vasp_single_point.sh`](../slurm_templates/vasp_single_point.sh) for
  those.
