# Slurm job templates

Ready-to-edit Slurm scripts for the jobs a DFT campaign runs most often. Each
file is self-contained: copy it into a run folder (or point `make_job_folders.py
--job-template` to it), edit the lines marked `>>> EDIT`, submit.

| Template | When to use | Key variables to edit |
| --- | --- | --- |
| [`vasp_single_point.sh`](vasp_single_point.sh) | Any VASP run that should simply run once: SCF, DOS, band structure, a short MD | `#SBATCH` resources, `VASP_MODULE`, `VASP_EXE` (`vasp_std` / `vasp_gam` / `vasp_ncl`), `LAUNCHER` |
| [`vasp_relax_autorestart.sh`](vasp_relax_autorestart.sh) | Geometry or cell relaxations that may need more ionic steps or more time than one run | the above + `MAX_CYCLES`, `--signal=B:USR1@600` margin, optional `AUTO_RESUBMIT`, `ARCHIVE` |
| [`vasp_job_array.sh`](vasp_job_array.sh) | Many small independent runs (snapshots, convergence tests): one array task per folder | per-task resources, `--array=1-N%M`, `FOLDER_LIST`, `VASP_LOG` |
| [`fhi_aims.sh`](fhi_aims.sh) | One FHI-aims calculation (`control.in` + `geometry.in`) | `#SBATCH` resources, `AIMS_MODULES`, `AIMS_BIN`, `LAUNCHER` |
| [`python_gpu_or_cpu.sh`](python_gpu_or_cpu.sh) | A Python job on CPU cores or a GPU: ASE/MACE molecular dynamics, ML training, heavy post-processing | `--cpus-per-task`, `--gres=gpu:1`, `ENV_KIND` + `VENV_PATH` / `CONDA_ENV`, `PY_MODULES`, `SCRIPT`, `SCRIPT_ARGS` |

A two-step hybrid-functional rescue job (PBE pre-convergence, then HSE/PBE0 from
its WAVECAR) is not part of this toolkit: see
[`SCF_Rescue_Hybrid`](https://github.com/israel-c-ribeiro/VASP_tools) in
VASP_tools.

## Conventions shared by all templates

**Placeholders, never real values.** `--account=<your_account>` and
`--partition=<partition>` make `sbatch` refuse the script until you replace
them: better a clear error at submission than a job charged to the wrong
project. Ask your cluster documentation for the module names (`module avail vasp`,
`module spider vasp`).

**One `#SBATCH` option per line, no trailing comment.** Explanations are on the
lines *above* the block. A `#SBATCH` line that starts with `##` is disabled
(used for the optional GPU request).

**Every variable can be overridden at submission**, so one copy of a template
serves a whole campaign:

```bash
sbatch --export=ALL,VASP_EXE=vasp_ncl vasp_single_point.sh
sbatch --export=ALL,MAX_CYCLES=5 job.sh
sbatch --time=02:00:00 --partition=debug job.sh       # options override #SBATCH lines
```

**Modules before strict mode.** Modules (and conda) are loaded *before*
`set -euo pipefail`: many module systems reference unset variables internally
and would abort a `set -u` script.

**Pure MPI for VASP.** `OMP_NUM_THREADS=1` and `MKL_NUM_THREADS=1`: one MPI
rank per core, no hidden threads that would oversubscribe the cores
(see [docs/02_slurm_for_dft_users.md](../docs/02_slurm_for_dft_users.md) for
hybrid MPI + OpenMP).

**Fail early.** Every script checks its inputs (INCAR, POSCAR, POTCAR,
KPOINTS or `KSPACING`; `control.in`/`geometry.in` and the species blocks for
FHI-aims) and the executable before starting the expensive part.

**Meaningful exit codes**, visible afterwards in `sacct -j <jobid> -o JobID,State,ExitCode`
(a non-zero code marks the job `FAILED`, which is what you want to notice):

- `vasp_single_point.sh`, `vasp_job_array.sh`: `0` finished (timing block in
  OUTCAR; for the array also "already done, skipped"), `1` setup problem or
  VASP returned 0 without finishing, otherwise VASP's own exit code.
- `vasp_relax_autorestart.sh`: `0` converged, `1` setup problem or the INCAR is
  not a relaxation, `2` VASP failed or left no usable CONTCAR, `3` not converged
  after `MAX_CYCLES` cycles (resubmit), `4` stopped by the wall-time signal
  (resubmit).
- `fhi_aims.sh`: `0` "Have a nice day." found, `1` setup problem, `2` the run
  did not finish normally.
- `python_gpu_or_cpu.sh`: the exit code of your script.

**Testing a template without VASP.** `VASP_RUN` replaces the whole VASP command,
which is how the test suite drives `vasp_relax_autorestart.sh` with a fake VASP:

```bash
VASP_RUN="bash fake_vasp.sh" bash vasp_relax_autorestart.sh
```
