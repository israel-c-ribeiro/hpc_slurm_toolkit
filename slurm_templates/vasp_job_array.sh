#!/usr/bin/env bash
# =============================================================================
#  vasp_job_array.sh -- one Slurm job array, one VASP run per array task
# -----------------------------------------------------------------------------
#  For many small, independent runs (single points on 200 snapshots, a k-point
#  convergence series...) one job array is kinder to the scheduler than 200
#  separate jobs: a single submission, one line in squeue, and a throttle
#  ('%20') on how many tasks run at the same time.
#
#  Task i runs VASP in the i-th folder listed in FOLDER_LIST (blank lines and
#  lines starting with '#' are ignored; paths are relative to the submission
#  folder, or absolute). A folder whose OUTCAR already has the timing block is
#  skipped, so the same array can be resubmitted after a partial failure.
#  VASP's output goes to <folder>/vasp.out.
#
#  Usage
#    ls -d runs/*/ > folders.txt
#    n=$(grep -cvE '^[[:space:]]*(#|$)' folders.txt)
#    sbatch --array=1-${n}%20 vasp_job_array.sh        # at most 20 tasks at once
#  Rerun only tasks 7 and 12:  sbatch --array=7,12 vasp_job_array.sh
#  Cancel one task:            scancel <jobid>_7
#
#  Edit the lines marked  >>> EDIT  (resources are PER TASK: one task = one run).
#  Exit code of a task: 0 finished (or skipped), 1 setup problem, else VASP's.
# =============================================================================
# >>> EDIT: resources of ONE task (the --array line is usually given on the command line)
#SBATCH --job-name=vasp_array
#SBATCH --account=<your_account>
#SBATCH --partition=<partition>
#SBATCH --time=04:00:00
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=32
#SBATCH --cpus-per-task=1
#SBATCH --mem-per-cpu=2000M
#SBATCH --array=1-10%5
#SBATCH --output=%x_%A_%a.out
#SBATCH --error=%x_%A_%a.err

# >>> EDIT: software environment
VASP_MODULE="${VASP_MODULE:-VASP/6.4.2}"
if command -v module >/dev/null 2>&1; then
  module purge
  module load "$VASP_MODULE"
else
  echo "[warn] no 'module' command: assuming VASP is already on PATH" >&2
fi

set -euo pipefail

# >>> EDIT: binary, launcher, list of folders
VASP_EXE="${VASP_EXE:-vasp_std}"
LAUNCHER="${LAUNCHER:-srun}"
VASP_RUN="${VASP_RUN:-$LAUNCHER $VASP_EXE}"
FOLDER_LIST="${FOLDER_LIST:-folders.txt}"
VASP_LOG="${VASP_LOG:-vasp.out}"
FORCE="${FORCE:-0}"                             # 1 = rerun folders that already finished

export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1

cd "${SLURM_SUBMIT_DIR:-.}"
task="${SLURM_ARRAY_TASK_ID:?not an array task: submit with sbatch --array=1-N}"

if [ ! -f "$FOLDER_LIST" ]; then echo "[error] $FOLDER_LIST not found in $(pwd)" >&2; exit 1; fi
dir=$(awk 'NF && $1 !~ /^#/' "$FOLDER_LIST" | sed -n "${task}p")
dir="${dir%/}"
if [ -z "$dir" ]; then echo "[error] no entry number $task in $FOLDER_LIST" >&2; exit 1; fi
if [ ! -d "$dir" ]; then echo "[error] folder $dir does not exist" >&2; exit 1; fi
cd "$dir"

echo "=== array ${SLURM_ARRAY_JOB_ID:-?} task $task: $dir ==="
echo "start   : $(date '+%F %T') on $(hostname)"

if [ "$FORCE" != "1" ] && grep -q "General timing and accounting" OUTCAR 2>/dev/null; then
  echo "already finished (OUTCAR has the timing block): skipped"
  exit 0
fi
for f in INCAR POSCAR POTCAR; do
  if [ ! -s "$f" ]; then echo "[error] $f missing or empty in $dir" >&2; exit 1; fi
done
if [ ! -s KPOINTS ] && ! grep -qiE '^[[:space:]]*KSPACING' INCAR; then
  echo "[error] no KPOINTS and no KSPACING in $dir/INCAR" >&2; exit 1
fi

SECONDS=0
rc=0
$VASP_RUN > "$VASP_LOG" 2>&1 || rc=$?
t=$SECONDS
printf 'end     : %s  (elapsed %dh %02dm %02ds, exit code %d)\n' \
  "$(date '+%F %T')" $((t / 3600)) $((t % 3600 / 60)) $((t % 60)) "$rc"
if [ "$rc" -eq 0 ] && ! grep -q "General timing and accounting" OUTCAR 2>/dev/null; then
  echo "[error] VASP returned 0 but OUTCAR has no timing block (see $dir/$VASP_LOG)" >&2
  rc=1
fi
exit "$rc"
