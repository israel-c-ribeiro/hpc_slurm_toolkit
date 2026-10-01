#!/usr/bin/env bash
# =============================================================================
#  fhi_aims.sh -- one FHI-aims calculation per job
# -----------------------------------------------------------------------------
#  FHI-aims reads control.in (settings + one 'species' block per element, copied
#  from species_defaults/) and geometry.in (lattice vectors and atoms), and
#  writes everything to standard output -- here redirected to aims.out.
#  A run that finished normally ends with the line "Have a nice day.".
#
#  Usage
#    cd /path/to/run && sbatch fhi_aims.sh
#    sbatch --export=ALL,AIMS_BIN=/path/to/aims.x fhi_aims.sh
#
#  Edit the lines marked  >>> EDIT
#    #SBATCH lines  account, partition, wall time and size
#    AIMS_MODULES   compiler / MPI / math libraries the binary was built with
#    AIMS_BIN       path to your FHI-aims executable (e.g. aims.<version>.scalapack.mpi.x)
#    LAUNCHER       srun (default) or "mpirun -np N"
#
#  Exit codes: 0 "Have a nice day." found; 1 setup problem; 2 the run did not
#  finish normally (read the end of aims.out and the .err file).
# =============================================================================
# >>> EDIT: resources
#SBATCH --job-name=aims
#SBATCH --account=<your_account>
#SBATCH --partition=<partition>
#SBATCH --time=24:00:00
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=128
#SBATCH --cpus-per-task=1
#SBATCH --mem=0
#SBATCH --output=%x_%j.out
#SBATCH --error=%x_%j.err

# >>> EDIT: software environment
AIMS_MODULES="${AIMS_MODULES:-intel/2023a}"
if command -v module >/dev/null 2>&1; then
  module purge
  for m in $AIMS_MODULES; do module load "$m"; done
else
  echo "[warn] no 'module' command: assuming the libraries are already available" >&2
fi

set -euo pipefail

# >>> EDIT: binary and launcher
AIMS_BIN="${AIMS_BIN:-/path/to/aims.x}"
LAUNCHER="${LAUNCHER:-srun}"
AIMS_OUT="${AIMS_OUT:-aims.out}"

export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1
# FHI-aims keeps large arrays on the stack: the default 8 MB stack makes it crash
# with a segmentation fault on anything but small systems.
ulimit -s unlimited 2>/dev/null || echo "[warn] could not set 'ulimit -s unlimited' (limit: $(ulimit -s))" >&2

cd "${SLURM_SUBMIT_DIR:-.}"

# ---- checks -------------------------------------------------------------------
for f in control.in geometry.in; do
  if [ ! -s "$f" ]; then echo "[error] $f missing or empty in $(pwd)" >&2; exit 1; fi
done
if [ ! -x "$AIMS_BIN" ]; then
  echo "[error] AIMS_BIN=$AIMS_BIN is not an executable file (edit AIMS_BIN)" >&2; exit 1
fi
# every element of geometry.in needs a 'species <El>' block in control.in
for el in $(awk '$1 == "atom" || $1 == "atom_frac" {print $5}' geometry.in | sort -u); do
  if ! grep -qE "^[[:space:]]*species[[:space:]]+${el}([[:space:]]|$)" control.in; then
    echo "[error] no 'species $el' block in control.in: append species_defaults/<level>/*_${el}_default" >&2
    exit 1
  fi
done
if [ -s "$AIMS_OUT" ]; then
  mv "$AIMS_OUT" "$AIMS_OUT.$(date '+%Y%m%d_%H%M%S')"     # never overwrite a previous output
fi

echo "=== job ${SLURM_JOB_ID:-local} (${SLURM_JOB_NAME:-aims}) ==="
echo "start   : $(date '+%F %T')"
echo "host    : $(hostname)"
echo "folder  : $(pwd)"
echo "tasks   : ${SLURM_NTASKS:-?} on ${SLURM_JOB_NUM_NODES:-?} node(s)"
echo "command : $LAUNCHER $AIMS_BIN > $AIMS_OUT"

SECONDS=0
rc=0
$LAUNCHER "$AIMS_BIN" > "$AIMS_OUT" 2>&1 || rc=$?
t=$SECONDS
printf 'end     : %s  (elapsed %dh %02dm %02ds, exit code %d)\n' \
  "$(date '+%F %T')" $((t / 3600)) $((t % 3600 / 60)) $((t % 60)) "$rc"

if grep -q "Have a nice day" "$AIMS_OUT"; then
  echo "FHI-aims finished normally"
  exit 0
fi
echo "[error] $AIMS_OUT has no 'Have a nice day.': the run did not finish normally" >&2
tail -n 20 "$AIMS_OUT" >&2 || true
exit 2
