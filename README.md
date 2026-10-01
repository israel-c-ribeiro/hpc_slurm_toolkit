<h1 align="center">HPC Slurm Toolkit <sub><code>v1.0</code></sub></h1>
<p align="center">
  <b>From a folder of structures to converged, verified DFT results on a Slurm cluster.</b><br>
  Job templates, batch setup, run monitoring and data transfer for DFT campaigns on Slurm clusters.
</p>

<p align="center">
  <a href="https://github.com/israel-c-ribeiro/hpc_slurm_toolkit/actions/workflows/tests.yml"><img src="https://github.com/israel-c-ribeiro/hpc_slurm_toolkit/actions/workflows/tests.yml/badge.svg" alt="tests"/></a>
  <img src="https://img.shields.io/badge/Python-3.9%2B-3776AB?logo=python&logoColor=white" alt="Python 3.9+"/>
  <img src="https://img.shields.io/badge/Slurm-workload%20manager-0B5394" alt="Slurm"/>
  <img src="https://img.shields.io/badge/VASP-5.4%20%7C%206.x-E8710A" alt="VASP"/>
  <img src="https://img.shields.io/badge/Bash-scripts-4EAA25?logo=gnubash&logoColor=white" alt="Bash"/>
  <img src="https://img.shields.io/badge/license-MIT-lightgrey" alt="MIT license"/>
</p>

---

## Why

Every DFT campaign rewrites the same job scripts. A new project, a new
cluster, a new student -- and once more someone copies a `job.sh` from an old
folder, edits the account by hand in forty places and forgets one.

Then the runs come back, and "the job finished" turns out not to mean "the
result is usable": a relaxation that stopped at `NSW` without converging, an
electronic loop that ran out of `NELM` iterations, a job killed by the wall
time halfway through an ionic step. Nothing in the queue says so; only the
OUTCAR does.

And the data has to come home through an SSH gateway, over a connection that
drops at 80 % of a 40 GB transfer, onto a Windows drive that refuses Unix
permissions.

This repository collects the small, boring, reliable pieces that remove
those problems: Slurm templates that check their inputs and restart
relaxations by themselves, a batch tool that builds job folders and refuses
mismatched POTCARs, a status scanner that tells converged from merely
finished, and a resumable transfer script -- with tutorials that explain the
*why* behind each choice.

## What's inside

| Component | What it does |
| --- | --- |
| [`slurm_templates/`](slurm_templates/) | Ready-to-edit Slurm scripts: VASP single point (`vasp_std`/`vasp_gam`/`vasp_ncl`), **self-restarting relaxation** (CONTCAR -> POSCAR cycles, clean stop before the wall time, resume across jobs), VASP job array, FHI-aims, Python/ML on CPU or GPU |
| [`tools/make_job_folders.py`](tools/make_job_folders.py) | One folder per structure with POSCAR, INCAR (template + `--set`/`--drop`), KPOINTS, POTCAR (from a library, **species order checked**), `job.sh`; plus `manifest.csv` and `submit_all.sh` |
| [`tools/incar_tags.py`](tools/incar_tags.py) | Set, drop or read INCAR tags in many files at once, keeping comments, order and multi-tag lines; diff preview; provenance line |
| [`tools/run_status.py`](tools/run_status.py) | One line per run: finished? last SCF converged? relaxation converged? ionic steps, restart cycles, energy, Slurm state; CSV export; exit code for scripts |
| [`tools/submit_all.sh`](tools/submit_all.sh) | Submit every `job.sh` below a folder, skipping runs that are done or already queued; `--max` for queue limits |
| [`tools/fetch_runs.sh`](tools/fetch_runs.sh) | Resumable `rsync` through a jump host, folder patterns, skip WAVECAR/CHGCAR, NTFS-safe, Windows drive paths |
| [`docs/`](docs/) | Four tutorials: SSH keys and jump hosts, Slurm for DFT users, restartable relaxations, rsync |
| [`examples/`](examples/) | A demo campaign (Si, NaCl, bcc Fe) and six synthetic run folders covering the usual failure modes |

The Python tools need **only the Python standard library** (3.9 or newer), so
they run on a bare cluster login node; [ASE](https://wiki.fysik.dtu.dk/ase/) is
optional (to read CIF and other formats in `make_job_folders.py`). The shell
scripts need bash, and `rsync` for the transfers.

## Installation

```bash
git clone https://github.com/israel-c-ribeiro/hpc_slurm_toolkit.git
cd hpc_slurm_toolkit
```

That is all: nothing to install. Copy or clone the repository both on your
workstation and on the cluster (the tools are small). Optionally, in a virtual
environment:

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install ase                                       # optional: CIF/xyz input
pip install -r requirements-dev.txt                   # only to run the tests
```

## Quick start

A five-step walkthrough with the demo campaign. Replace the paths by yours;
the fake POTCAR library of the demo is a placeholder (see
[examples/demo_campaign](examples/demo_campaign/)).

**1. Make the job folders** (once with `--dry-run` to check, then for real):

```bash
python tools/make_job_folders.py examples/demo_campaign/structures --out runs \
    --incar examples/demo_campaign/INCAR --kpoints examples/demo_campaign/KPOINTS \
    --potcar-dir examples/demo_campaign/fake_potcar_library --potcar-map Na=Na_pv \
    --job-template slurm_templates/vasp_relax_autorestart.sh --set ENCUT=520
```

Before that, edit the `>>> EDIT` lines of the template (account, partition,
modules) once for your cluster.

**2. Copy to the cluster and submit:**

```bash
rsync -avzhP runs/ mycluster:/path/to/runs/                 # "mycluster": see docs/01
ssh mycluster
bash hpc_slurm_toolkit/tools/submit_all.sh -n /path/to/runs # what would be submitted
bash hpc_slurm_toolkit/tools/submit_all.sh /path/to/runs
```

**3. Monitor:**

```bash
python hpc_slurm_toolkit/tools/run_status.py /path/to/runs --squeue
```

**4. Fetch the results** (back on your workstation; configure once with
`tools/fetch_runs.conf.example`):

```bash
bash tools/fetch_runs.sh --small runs runs     # resumable; skips WAVECAR/CHGCAR/CHG
```

**5. Check that they are usable:**

```bash
python tools/run_status.py runs --problems && echo "all converged"
```

Relaxations that stopped at `NSW` show up as `ionic-unconverged`: run
`submit_all.sh` again and the self-restarting template continues them from
their last geometry; converged folders are skipped.

## Tutorials

1. [SSH keys and jump hosts](docs/01_ssh_keys_and_jump_hosts.md) -- keys, agent, `~/.ssh/config` with ProxyJump, Windows (OpenSSH, MobaXterm, WSL), common errors
2. [Slurm for DFT users](docs/02_slurm_for_dft_users.md) -- sbatch/squeue/sacct/scancel, choosing nodes and time, MPI vs OpenMP for VASP, reading `.out`/`.err`, job arrays, dependencies
3. [Restartable relaxations](docs/03_restartable_relaxations.md) -- why relaxations need restarts, the autorestart pattern, surviving the wall time, a worked example
4. [Moving data with rsync](docs/04_moving_data_with_rsync.md) -- flags, filter order and anchored patterns, what not to transfer, integrity, Windows/NTFS caveats

## Repository layout

```text
hpc_slurm_toolkit/
├── slurm_templates/
│   ├── README.md                    template · when to use · variables to edit · exit codes
│   ├── vasp_single_point.sh         one VASP run; vasp_std / vasp_gam / vasp_ncl
│   ├── vasp_relax_autorestart.sh    relaxation with CONTCAR -> POSCAR cycles and wall-time stop
│   ├── vasp_job_array.sh            one array task per folder listed in folders.txt
│   ├── fhi_aims.sh                  FHI-aims with input and species checks
│   └── python_gpu_or_cpu.sh         Python/ML job (venv or conda), CPU or GPU
├── tools/
│   ├── make_job_folders.py          batch creation of VASP job folders
│   ├── incar_tags.py                INCAR editing library + command line
│   ├── run_status.py                campaign overview: finished / converged / Slurm state
│   ├── submit_all.sh                submit what is neither done nor queued
│   ├── fetch_runs.sh                resumable rsync through a jump host
│   └── fetch_runs.conf.example      configuration template for fetch_runs.sh
├── docs/                            four tutorials (see above)
├── examples/
│   ├── demo_campaign/               Si, NaCl, bcc Fe + INCAR, KPOINTS, placeholder POTCARs
│   └── status_demo/                 six synthetic run folders, one per typical outcome
├── tests/                           pytest suite (Python tools and shell scripts)
├── .github/workflows/tests.yml      CI: Ubuntu + Windows, Python 3.10-3.12
├── requirements.txt                 no required dependency (ASE optional)
├── requirements-dev.txt             pytest
├── CHANGELOG.md · CITATION.cff · LICENSE
```

## Testing

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

The suite checks the INCAR editor, the folder builder (including POTCAR order
mismatches and the dry run), the OUTCAR parser on synthetic outputs (converged,
SCF unconverged with VASP 5 and 6, killed, NSW reached), and runs the shell
scripts with fake `vasp`, `sbatch` and `rsync` programs: restart cycles,
resume after a killed job, every exit code, the wall-time signal (Linux), the
filter order of `fetch_runs.sh`. Every `.sh` file is also checked with
`bash -n` and for LF line endings. On Windows the shell tests use Git Bash and
are skipped if no bash is found.

## Related repositories

* [VASP_tools](https://github.com/israel-c-ribeiro/VASP_tools) -- post-processing
  of VASP results, in-depth diagnosis of a single problematic run
  (`Run_Diagnostics`) and the two-step hybrid-functional SCF rescue
  (`SCF_Rescue_Hybrid`)
* [materials_data_toolkit](https://github.com/israel-c-ribeiro/materials_data_toolkit) --
  statistics and publication figures for materials data

## How to cite

If this toolkit helps your work, please cite it using the metadata in
[`CITATION.cff`](CITATION.cff) (GitHub shows a "Cite this repository" button
on the repository page).

## License

[MIT](LICENSE) © 2026 Israel C. Ribeiro

## Author

**Israel C. Ribeiro** -- Postdoctoral researcher, University of Mons ·
[portfolio](https://israel-c-ribeiro.github.io/)

Contributions, bug reports and suggestions are welcome: please open an
[issue](https://github.com/israel-c-ribeiro/hpc_slurm_toolkit/issues) or a pull request.
