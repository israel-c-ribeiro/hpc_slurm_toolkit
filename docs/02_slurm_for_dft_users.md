# 2. Slurm for DFT users

Slurm is the queueing system of most HPC clusters. You describe what your job
needs (cores, memory, time), Slurm finds the nodes, starts the script there and
records what happened. This page covers what a DFT user needs day to day; the
templates in [`slurm_templates/`](../slurm_templates/) put it into practice.

## Vocabulary

| Term | Meaning |
| --- | --- |
| login node | where you land with `ssh`; for editing and submitting, **never** for running VASP |
| compute node | where jobs run; you do not log in there directly |
| partition | a queue: a group of nodes with limits (max time, size), e.g. `debug`, `batch`, `gpu` |
| account | the project your CPU hours are charged to (`--account`) |
| job / job step | one submission / one `srun` (or `mpirun`) inside it |
| task | one process, i.e. one MPI rank (`--ntasks`, `--ntasks-per-node`) |
| CPU | one core (or hardware thread) given to a task (`--cpus-per-task`) |

## The six commands you use every day

```bash
sbatch job.sh                        # submit; prints "Submitted batch job 123456"
squeue --me                          # my jobs (older Slurm: squeue -u $USER)
squeue --me --start                  # estimated start times of pending jobs
scontrol show job 123456             # everything about one job (nodes, workdir, reason)
scancel 123456                       # cancel one job; scancel -n relax_Si (by name); scancel -u $USER (all!)
sacct -j 123456 -o JobID,JobName,State,Elapsed,MaxRSS,ExitCode    # after the fact
```

Also handy: `seff 123456` (efficiency summary: did the job use the cores and
memory it asked for?), `sinfo -s` (partitions and their state), `sshare` /
`sprio` (why am I waiting: fair-share and priority).

Useful `squeue` format: `squeue --me -o "%.10i %.25j %.8T %.10M %.6D %R"`
(id, name, state, elapsed time, nodes, reason or node list). The pending
*reason* tells you why a job waits: `Priority` (others first), `Resources`
(nodes busy), `QOSMaxJobsPerUserLimit` (your own limit), `DependencyNeverSatisfied`
(see below).

## Choosing the resources of a VASP job

**Fill whole nodes.** VASP scales well inside a node and the cluster's nodes
have a fixed number of cores; ask for `--nodes=N --ntasks-per-node=<cores per node>`
(`sinfo -o "%P %c %m"` shows cores and memory per node). Half-empty nodes waste
your allocation and are often charged in full anyway.

**Memory.** `--mem=0` requests all the memory of the node (natural when you take
all its cores). For partial nodes use `--mem-per-cpu`. Out-of-memory kills
show up as `oom-kill` / `OUT_OF_MEMORY` in the `.err` file or in `sacct`.

**Time.** Ask for what the job needs plus a margin, not the partition maximum:
short jobs are back-filled into gaps of the schedule and start earlier. Measure
with one representative run (`sacct ... Elapsed`), then scale.

**Parallelisation tags in the INCAR** (they do not change results, only speed):

| Tag | Rule of thumb |
| --- | --- |
| `NCORE` | cores working on one band; start with a divisor of the cores per node close to its square root, or the cores per socket (e.g. 8-16 on 128-core nodes) |
| `KPAR` | number of k-point groups; must divide the number of MPI ranks (a divisor of the number of nodes is a good start) and should not exceed the number of irreducible k-points. Uses more memory. |
| `NPAR` | the older alternative to `NCORE`; set one of them, not both |

Benchmark a few combinations on one structure before launching a campaign.
`make_job_folders.py --set NCORE=16` or `incar_tags.py --set` changes them
everywhere at once.

### Which binary

| Binary | Use it for |
| --- | --- |
| `vasp_std` | the general case |
| `vasp_gam` | a single Gamma k-point (large supercells, molecules, AIMD): about twice as fast, half the memory |
| `vasp_ncl` | spin-orbit coupling (`LSORBIT = .TRUE.`) or non-collinear magnetism |

### MPI or MPI + OpenMP

The default and safest choice for VASP is **pure MPI**: one rank per core and
`OMP_NUM_THREADS=1`, which every template sets. Without it, a threaded math
library may start one thread per core *in every rank* and oversubscribe the
node -- the job runs, but several times slower.

VASP 6 built with OpenMP can run hybrid MPI + OpenMP, which pays off mainly for
large hybrid-functional or memory-bound runs:

```bash
#SBATCH --ntasks-per-node=32
#SBATCH --cpus-per-task=4
export OMP_NUM_THREADS=$SLURM_CPUS_PER_TASK
export OMP_PLACES=cores
export OMP_PROC_BIND=close
srun vasp_std
```

The VASP wiki page "Combining MPI and OpenMP" lists the restrictions of your
version (`NCORE`, for instance, behaves differently); benchmark before using it
in production.

### `srun` or `mpirun`

Both work on most clusters. `srun` reads the allocation directly (task count,
binding, accounting per step) and is the default in the templates
(`LAUNCHER=srun`). Some MPI installations require `mpirun -np $SLURM_NTASKS`
instead: set `LAUNCHER` accordingly. If `srun` complains about PMI/PMIx, try
`srun --mpi=pmix` or ask the centre which one their VASP module expects.

## Reading the `.out` and `.err` files

The templates name them `%x_%j.out` / `%x_%j.err` (job name, job id), so
they sort by job name and never overwrite each other. What to look for:

| You see | It means |
| --- | --- |
| `CANCELLED AT ... DUE TO TIME LIMIT` | wall time reached; VASP was killed mid-step (use `vasp_relax_autorestart.sh` for relaxations) |
| `oom-kill`, `OUT_OF_MEMORY`, `Killed` | not enough memory: more nodes, fewer ranks per node, `KPAR` smaller, or `vasp_gam` |
| `BAD TERMINATION OF ONE OF YOUR APPLICATION PROCESSES` | some rank died: read the lines above it and the end of OUTCAR |
| `srun: error: ... task N: Exited with exit code ...` | the application failed; the reason is usually in the `.out` |
| `module: command not found` / `vasp_std: command not found` | module name wrong or not loaded |
| VASP messages like `ZBRENT: fatal error`, `Error EDDDAV`, `VERY BAD NEWS! internal error in subroutine SGRCON` | physics/numerics: often geometry, symmetry (`SYMPREC`, `ISYM`) or SCF settings; diagnose the run in detail with [VASP_tools/Run_Diagnostics](https://github.com/israel-c-ribeiro/VASP_tools) |

The **exit code** (`sacct ... ExitCode`, shown as `code:signal`) is the
quickest summary: `0:0` success; `N:0` the script exited with `N` (see the
exit-code list of each template); `0:9` / `0:15` killed by a signal.

A job that ended with `0:0` is not necessarily a usable result: the SCF or the
relaxation may not have converged. That is what `tools/run_status.py` checks.

## Job arrays

One submission, many similar tasks -- each gets a different
`$SLURM_ARRAY_TASK_ID`:

```bash
sbatch --array=1-200%20 vasp_job_array.sh    # 200 tasks, at most 20 running at once
squeue --me                                  # 123456_[21-200] pending, 123456_1 ... running
scancel 123456_17                            # cancel one task
scancel 123456                               # cancel the whole array
sacct -j 123456 -o JobID%15,State,ExitCode   # one line per task
```

In the output file name, `%A` is the array id and `%a` the task index.
[`vasp_job_array.sh`](../slurm_templates/vasp_job_array.sh) maps task *i* to
the *i*-th folder of `folders.txt`. Arrays are ideal for independent single
points; avoid them for runs with very different sizes (every task gets the
same resources).

## Dependencies: chaining jobs

```bash
jid=$(sbatch --parsable relax/job.sh)                       # --parsable prints only the id
sbatch --dependency=afterok:$jid static/job.sh              # starts only if relax exited 0
sbatch --dependency=afterany:$jid cleanup.sh                # starts whatever happened
sbatch --dependency=afternotok:$jid notify_me.sh            # only if it failed
sbatch --dependency=singleton --job-name=Si_chain next.sh   # one job of this name at a time
```

A typical chain is *relaxation -> static SCF (writes CHGCAR) -> band structure
(`ICHARG = 11`)*. Note how `afterok` interacts with exit codes: when
`vasp_relax_autorestart.sh` ends with `3` (not converged yet) the dependent job
never starts (`DependencyNeverSatisfied`). That is the point -- do not compute
bands on an unconverged geometry -- but remember to `scancel` it or to resubmit
the relaxation and re-create the chain.

## Environment and reproducibility

* Load modules **inside** the job script after `module purge`; do not rely on
  what your login shell happened to have loaded.
* `sbatch` copies your current environment by default (`--export=ALL`). Use
  `--export=ALL,VAR=value` to pass settings to a template, or `--export=NONE`
  for a clean start.
* Keep the job script in the run folder (`make_job_folders.py` writes it as
  `job.sh`): months later you will know exactly how the result was produced.

## Etiquette

* Test on a `debug`/short partition with a small system before a campaign.
* Never run heavy work on the login node (`python analysis.py` on 2000 OUTCARs
  is fine; VASP is not).
* Do not submit thousands of tiny jobs: use arrays, and `submit_all.sh --max`
  to stay below the per-user limits.
* Delete `WAVECAR`/`CHGCAR` you do not need (`LWAVE = .FALSE.`,
  `LCHARG = .FALSE.` from the start): scratch quotas are shared.
