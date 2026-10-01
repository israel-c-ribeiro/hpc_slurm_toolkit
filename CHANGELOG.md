# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] - 2026-10-01

### Added

- Slurm templates: `vasp_single_point.sh` (vasp_std / vasp_gam / vasp_ncl),
  `vasp_relax_autorestart.sh` (CONTCAR -> POSCAR restart cycles, per-cycle
  archives, clean stop before the wall time through `--signal` + STOPCAR,
  resume across jobs, optional self-resubmission, documented exit codes),
  `vasp_job_array.sh`, `fhi_aims.sh`, `python_gpu_or_cpu.sh`.
- `tools/make_job_folders.py`: one VASP job folder per structure, INCAR
  template with `--set`/`--drop`, POTCAR from a file or a library with a
  species-order check, `manifest.csv`, `submit_all.sh`, dry run.
- `tools/incar_tags.py`: statement-level INCAR editing (library and command
  line) that keeps comments, order, multi-tag lines and continuations.
- `tools/run_status.py`: per-folder finished / SCF / ionic convergence report,
  restart cycles, last energy, optional Slurm state, CSV, exit code.
- `tools/submit_all.sh`: submit every job script below a folder, skipping done
  and queued runs.
- `tools/fetch_runs.sh`: resumable rsync through a jump host with anchored
  folder patterns, optional exclusion of heavy files, NTFS-safe options and
  Windows drive-path translation.
- Tutorials: SSH keys and jump hosts, Slurm for DFT users, restartable
  relaxations, moving data with rsync.
- Examples (demo campaign, synthetic run folders) and a pytest suite run on
  Ubuntu and Windows by GitHub Actions.

[Unreleased]: https://github.com/israel-c-ribeiro/hpc_slurm_toolkit/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/israel-c-ribeiro/hpc_slurm_toolkit/releases/tag/v1.0.0
