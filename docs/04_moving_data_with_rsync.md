# 4. Moving data with rsync

A DFT campaign produces thousands of files, some of them gigabytes large, on a
machine behind a gateway. `scp -r` restarts from zero when the connection drops
and copies everything every time. `rsync` copies only what changed, resumes
interrupted files, and filters what you do not want. This page explains the
flags and filters behind [`tools/fetch_runs.sh`](../tools/fetch_runs.sh).

## The command, flag by flag

```bash
rsync -avzhP --stats -e "ssh -J user@gateway.example.org" \
      user@login.cluster.example.org:/path/to/runs/campaign1/  ./campaign1/
```

| Flag | Meaning | Why |
| --- | --- | --- |
| `-a` | archive = `-rlptgoD`: recursive, keep symlinks, permissions, times, group, owner, devices | modification times let the next run skip unchanged files |
| `-v` | verbose: list the files | |
| `-z` | compress during transfer | OUTCAR, vasprun.xml, LOCPOT are text and shrink 3-4x |
| `-h` | human-readable sizes | |
| `-P` | `--partial --progress` | keep partially transferred files and continue them next time; show progress |
| `--stats` | summary at the end | how much was actually sent |
| `-n`, `--dry-run` | show what would happen, copy nothing | always try a new filter with `-n` first |
| `-e "ssh -J gw"` | the remote shell, here through a jump host | see [01_ssh_keys_and_jump_hosts.md](01_ssh_keys_and_jump_hosts.md) |
| `--no-perms --no-owner --no-group` | do not try to reproduce Unix permissions | needed when writing to Windows drives (NTFS) |

Flags to know but use with care:

| Flag | Effect | Caution |
| --- | --- | --- |
| `--delete` | delete local files that no longer exist remotely | one wrong source path empties your copy; always with `-n` first |
| `--remove-source-files` | delete the remote files after a successful transfer | only for data you will never need on the cluster again |
| `-c`, `--checksum` | compare files by checksum instead of size + time | slow (reads everything on both sides), but the strongest check |
| `--info=progress2` | one overall progress line instead of one per file | nicer for many small files |
| `--bwlimit=20M` | limit the bandwidth | polite on shared links |
| `-m`, `--prune-empty-dirs` | do not create directories that end up empty after filtering | |

### The trailing slash

```bash
rsync -a host:/path/runs/  local/runs/   # copies the CONTENT of runs into local/runs
rsync -a host:/path/runs   local/        # copies the directory runs itself into local/
```

Both end up in `local/runs/`, but mixing the two forms is the classic way to
get `local/runs/runs/`. `fetch_runs.sh` always uses the first form.

## Filters: include and exclude

rsync tests every path against the filter rules **in order** and applies the
**first rule that matches**; a path matched by no rule is included.

* a pattern ending in `/` matches directories only;
* a pattern starting with `/` is **anchored** at the root of the transfer;
  without it, it matches at any depth;
* `*` matches within one path component, `**` across components.

Taking only some run folders, e.g. those whose name starts with `Si_`:

```bash
--include='/Si_*/' --include='/Si_*/**' --exclude='/*'
```

1. `/Si_*/` -- include the top-level directories `Si_*` (so rsync enters them);
2. `/Si_*/**` -- include everything inside them;
3. `/*` -- exclude every other top-level entry.

Why anchored? Without the leading `/`, `Si_*` would also be tested against
every file *inside* the folders, and `--exclude='*'` would exclude the files
themselves.

**Order matters when you combine filters.** To skip heavy files inside the
selected folders, the excludes must come *first*:

```bash
# correct: WAVECAR is excluded before the include rule can match it
--exclude=WAVECAR --exclude=CHGCAR --include='/Si_*/' --include='/Si_*/**' --exclude='/*'

# wrong: '/Si_*/**' matches Si_1/WAVECAR first, so WAVECAR IS transferred
--include='/Si_*/' --include='/Si_*/**' --exclude=WAVECAR --exclude='/*'
```

`fetch_runs.sh` builds the rules in the correct order (its tests check it).
When in doubt: `rsync -n -v ...` and read the list.

## What not to transfer

| File | Size | Keep? |
| --- | --- | --- |
| `WAVECAR` | huge (wavefunctions) | only to restart a calculation; usually leave it on the cluster or do not write it (`LWAVE = .FALSE.`) |
| `WAVEDER` | large | only for optical follow-up runs |
| `CHGCAR`, `CHG` | large | `CHGCAR` is needed for non-self-consistent bands (`ICHARG = 11`), Bader/DDEC charges; `CHG` is rarely needed (`LCHARG = .FALSE.` if not) |
| `core`, `core.*` | huge | crash dumps: never |
| `LOCPOT` | medium | needed for work functions and vacuum level alignment of slabs |
| `vasprun.xml` | medium | needed by many post-processing tools (pymatgen, py4vasp) |
| `OUTCAR`, `OSZICAR`, `CONTCAR`, `EIGENVAL`, `DOSCAR`, `PROCAR` | small to medium | yes |

`fetch_runs.sh -s` (`--small`) skips `WAVECAR`, `WAVEDER`, `CHGCAR`, `CHG` and
core dumps; `-x PATTERN` adds more (e.g. `-x vasprun.xml`). The best transfer
is the one you avoid: set `LWAVE = .FALSE.` and `LCHARG = .FALSE.` in the
INCAR when the files are not needed.

## Checking integrity

rsync already verifies every transferred file with a whole-file checksum. For
extra peace of mind:

```bash
# a second pass that compares checksums and lists what differs (nothing = identical)
rsync -avzc -n -e "ssh -J user@gateway.example.org" user@login.cluster.example.org:/path/to/runs/campaign1/ ./campaign1/

# or a checksum manifest made on the cluster and verified locally
ssh mycluster 'cd /path/to/runs/campaign1 && find . -name OUTCAR -exec sha256sum {} +' > SHA256SUMS
(cd campaign1 && sha256sum -c ../SHA256SUMS)
```

And the scientific check: did the runs you copied actually finish?

```bash
grep -L "General timing" campaign1/*/OUTCAR      # OUTCARs WITHOUT the timing block
python tools/run_status.py campaign1/             # finished, SCF, ionic convergence per run
```

Exit codes 23 and 24 of rsync mean a partial transfer; 24 ("some files
vanished") is typical when a job is still writing -- rerun later.

## Windows and NTFS caveats

* **Which rsync.** Git Bash has none. Use MobaXterm's local terminal, WSL
  (`sudo apt install rsync`) or MSYS2 (`pacman -S rsync`). Use the `ssh` of the
  *same* environment: a Cygwin/MSYS rsync with the Windows OpenSSH client (or the
  reverse) fails with confusing protocol errors.
* **Drive paths.** `E:\data` is `/drives/e/data` (MobaXterm), `/mnt/e/data`
  (WSL), `/cygdrive/e/data` (Cygwin), `/e/data` (Git Bash/MSYS2).
  `fetch_runs.sh` translates `E:/data` style paths automatically.
* **Permissions.** NTFS cannot store Unix permissions; rsync then fails with
  `chmod` errors. `--no-perms --no-owner --no-group` avoids it (`fetch_runs.sh`
  adds them automatically under Windows-like shells and on `/mnt/<drive>`).
* **Case-insensitive names.** `CHG` and `chg` are the same file on Windows: two
  files differing only by case overwrite each other.
* **Forbidden characters.** `: * ? " < > |` are not allowed in Windows file
  names; folders named like `run:1` cannot be copied as they are.
* **Timestamps on FAT/exFAT** (many USB drives) have 2-second resolution:
  add `--modify-window=1`, or every file looks modified at every run.
* **Long paths.** Windows tools may choke on paths longer than 260 characters;
  keep campaign folder names short.
* **Line endings.** rsync copies bytes, so files keep their LF endings. If you
  edit an INCAR or a job script with a Windows editor and send it back, check
  for CRLF (`file job.sh`; fix with `dos2unix job.sh`) -- bash on the cluster
  fails on `\r` with errors like `$'\r': command not found`. The tools of this
  repository always write LF.

## Putting it together

```bash
cp tools/fetch_runs.conf.example tools/fetch_runs.conf   # once: fill in hosts and folders
eval "$(ssh-agent -s)" && ssh-add ~/.ssh/id_ed25519       # once per session

bash tools/fetch_runs.sh -n -s campaign1 campaign1 'Si_*' # look first
bash tools/fetch_runs.sh -s campaign1 campaign1 'Si_*'    # copy; rerun the same line to resume
python tools/run_status.py campaign1/                     # what came back, and is it usable?
```
