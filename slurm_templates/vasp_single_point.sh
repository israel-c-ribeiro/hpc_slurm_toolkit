#!/usr/bin/env bash
# =============================================================================
#  vasp_single_point.sh -- one VASP run per job (SCF, DOS, bands, MD, ...)
# -----------------------------------------------------------------------------
#  Usage
#    Put this file in the run folder (INCAR, POSCAR, KPOINTS, POTCAR) and
#        sbatch vasp_single_point.sh
#    or keep one copy and submit it from each folder:
#        cd /path/to/runs/Si && sbatch /path/to/vasp_single_point.sh
#    Every variable below can be overridden at submission, without editing:
#        sbatch --export=ALL,VASP_EXE=vasp_ncl vasp_single_point.sh
#
#  Edit the lines marked  >>> EDIT
#    #SBATCH lines  account, partition, wall time and size for your cluster
#                   (one option per line, never a comment after the option)
#    VASP_MODULE    module providing the VASP binaries ('module avail vasp')
#    VASP_EXE       vasp_std  standard build (default)
#                   vasp_gam  Gamma-point-only build: ~2x faster and half the
#                             memory when KPOINTS is the single Gamma point
#                   vasp_ncl  non-collinear build: spin-orbit coupling
#                             (LSORBIT) or non-collinear magnetism
#    LAUNCHER       srun (default; knows the allocation) or "mpirun -np N"
#
#  Exit code: VASP's own; a run that returns 0 without the timing block at the
#  end of OUTCAR (it did not really finish) is turned into exit code 1.
# =============================================================================
# >>> EDIT: resources
#SBATCH --job-name=vasp_sp
#SBATCH --account=<your_account>
#SBATCH --partition=<partition>
#SBATCH --time=24:00:00
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=128
#SBATCH --cpus-per-task=1
#SBATCH --mem=0
#SBATCH --output=%x_%j.out
#SBATCH --error=%x_%j.err

# >>> EDIT: software environment (modules are loaded before 'set -u': many
#           module systems reference unset variables internally)
VASP_MODULE="${VASP_MODULE:-VASP/6.4.2}"
if command -v module >/dev/null 2>&1; then
  module purge
  module load "$VASP_MODULE"
else
  echo "[warn] no 'module' command: assuming VASP is already on PATH" >&2
fi

set -euo pipefail

# >>> EDIT: binary and launcher
VASP_EXE="${VASP_EXE:-vasp_std}"
LAUNCHER="${LAUNCHER:-srun}"
VASP_RUN="${VASP_RUN:-$LAUNCHER $VASP_EXE}"     # full command (override to test the script)

# Pure MPI: one MPI rank per core, no threads (threads would oversubscribe the cores)
export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1

cd "${SLURM_SUBMIT_DIR:-.}"

# ---- checks: fail in seconds rather than after hours in the queue -----------
for f in INCAR POSCAR POTCAR; do
  if [ ! -s "$f" ]; then echo "[error] $f missing or empty in $(pwd)" >&2; exit 1; fi
done
if [ ! -s KPOINTS ] && ! grep -qiE '^[[:space:]]*KSPACING' INCAR; then
  echo "[error] no KPOINTS and no KSPACING in INCAR" >&2; exit 1
fi
if [ "$VASP_RUN" = "$LAUNCHER $VASP_EXE" ] && ! command -v "$VASP_EXE" >/dev/null 2>&1; then
  echo "[error] $VASP_EXE not found on PATH (check VASP_MODULE)" >&2; exit 1
fi

echo "=== job ${SLURM_JOB_ID:-local} (${SLURM_JOB_NAME:-vasp}) ==="
echo "start   : $(date '+%F %T')"
echo "host    : $(hostname)"
echo "folder  : $(pwd)"
echo "tasks   : ${SLURM_NTASKS:-?} on ${SLURM_JOB_NUM_NODES:-?} node(s)"
echo "command : $VASP_RUN"
echo

SECONDS=0
rc=0
$VASP_RUN || rc=$?

t=$SECONDS
echo
printf 'end     : %s  (elapsed %dh %02dm %02ds, exit code %d)\n' \
  "$(date '+%F %T')" $((t / 3600)) $((t % 3600 / 60)) $((t % 60)) "$rc"

if [ "$rc" -eq 0 ] && ! grep -q "General timing and accounting" OUTCAR 2>/dev/null; then
  echo "[error] VASP returned 0 but OUTCAR has no timing block: the run did not finish" >&2
  rc=1
fi
if grep -q "EDIFF was not reached" OUTCAR 2>/dev/null; then
  echo "[warn] an electronic loop did not reach EDIFF -- check NELM / ALGO (run_status.py)" >&2
fi
exit "$rc"
