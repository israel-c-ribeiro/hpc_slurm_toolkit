#!/usr/bin/env bash
# =============================================================================
#  vasp_relax_autorestart.sh -- a geometry optimisation that restarts itself
# -----------------------------------------------------------------------------
#  A relaxation often needs more ionic steps than NSW, or more time than one
#  job, and a cell relaxation (ISIF = 3) should be restarted anyway to remove
#  the Pulay stress of the initial basis set. This script continues the run
#  from its last geometry (CONTCAR -> POSCAR), up to MAX_CYCLES times inside
#  one job -- and across jobs if you resubmit it in the same folder.
#
#  Each cycle
#    1. runs VASP;
#    2. archives OUTCAR, OSZICAR and CONTCAR as OUTCAR.cycleN, ... (N = 1, 2, ...);
#    3. stops with exit code 0 if OUTCAR says "reached required accuracy";
#    4. otherwise copies CONTCAR -> POSCAR (only if CONTCAR is a complete file)
#       and starts the next cycle.
#  The very first POSCAR is kept as POSCAR.orig.
#
#  Wall time: the '--signal=B:USR1@600' line asks Slurm to send SIGUSR1 to this
#  script 600 s before the time limit. The script then writes a STOPCAR
#  (LSTOP = .TRUE.): VASP finishes the current ionic step, writes CONTCAR and
#  exits cleanly. The 600 s must be longer than one ionic step.
#  Resubmit the same script to continue (or set AUTO_RESUBMIT=1).
#
#  Usage
#    cd /path/to/run && sbatch job.sh            (this file copied as job.sh)
#    sbatch --export=ALL,MAX_CYCLES=5 job.sh     (override a variable)
#  The INCAR must describe a relaxation: IBRION = 1, 2 or 3, NSW > 0, EDIFFG.
#
#  Exit codes (sacct shows them as ExitCode N:0)
#    0  converged ("reached required accuracy"), also if it already was
#    1  setup problem: missing input, VASP not found, INCAR is not a relaxation
#    2  VASP failed (non-zero exit) or left no usable CONTCAR: read .err/OUTCAR
#    3  not converged after MAX_CYCLES cycles: resubmit to continue
#    4  stopped by the wall-time signal before converging: resubmit to continue
#
#  Edit the lines marked  >>> EDIT  (resources, VASP_MODULE, VASP_EXE, LAUNCHER,
#  MAX_CYCLES). Tutorial: docs/03_restartable_relaxations.md
# =============================================================================
# >>> EDIT: resources
#SBATCH --job-name=vasp_relax
#SBATCH --account=<your_account>
#SBATCH --partition=<partition>
#SBATCH --time=48:00:00
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=128
#SBATCH --cpus-per-task=1
#SBATCH --mem=0
#SBATCH --signal=B:USR1@600
#SBATCH --output=%x_%j.out
#SBATCH --error=%x_%j.err

# >>> EDIT: software environment (before 'set -u', see vasp_single_point.sh)
VASP_MODULE="${VASP_MODULE:-VASP/6.4.2}"
if command -v module >/dev/null 2>&1; then
  module purge
  module load "$VASP_MODULE"
else
  echo "[warn] no 'module' command: assuming VASP is already on PATH" >&2
fi

set -euo pipefail

# >>> EDIT: binary, launcher and restart policy
VASP_EXE="${VASP_EXE:-vasp_std}"
LAUNCHER="${LAUNCHER:-srun}"
VASP_RUN="${VASP_RUN:-$LAUNCHER $VASP_EXE}"     # full command (override to test the script)
MAX_CYCLES="${MAX_CYCLES:-3}"                   # VASP runs inside this job
AUTO_RESUBMIT="${AUTO_RESUBMIT:-0}"             # 1 = resubmit itself after exit 3 or 4
MAX_TOTAL_CYCLES="${MAX_TOTAL_CYCLES:-12}"      # safety net for AUTO_RESUBMIT
JOB_SCRIPT="${JOB_SCRIPT:-job.sh}"              # name of this file in the run folder (for AUTO_RESUBMIT)
ARCHIVE="${ARCHIVE:-OUTCAR OSZICAR CONTCAR}"    # kept as <file>.cycleN (add vasprun.xml if needed)

export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1

cd "${SLURM_SUBMIT_DIR:-.}"
SECONDS=0

log() { echo "[$(date '+%F %T')] $*"; }

# ---- helpers -----------------------------------------------------------------
STOP_REQUESTED=0
on_usr1() {
  STOP_REQUESTED=1
  echo "LSTOP = .TRUE." > STOPCAR
  log "wall-time signal received: STOPCAR written, VASP stops after the current ionic step"
}
trap on_usr1 USR1

converged() { [ -f OUTCAR ] && grep -q "reached required accuracy" OUTCAR; }

valid_contcar() {               # non-empty, at least a header + one atom line
  [ -s CONTCAR ] || return 1
  [ "$(wc -l < CONTCAR)" -ge 8 ]
}

last_cycle() {                  # highest N among OUTCAR.cycleN (0 if none)
  local f k max=0
  for f in OUTCAR.cycle*; do
    [ -e "$f" ] || continue
    k="${f##*cycle}"
    case "$k" in ''|*[!0-9]*) continue ;; esac
    if [ "$k" -gt "$max" ]; then max=$k; fi
  done
  echo "$max"
}

archive() {                     # archive <N>: copy the ARCHIVE files to <file>.cycleN
  local f
  for f in $ARCHIVE; do
    if [ -f "$f" ]; then cp -p "$f" "$f.cycle$1"; fi
  done
}

run_vasp() {                    # VASP in the background, so the USR1 trap can run
  local pid rc rc2
  $VASP_RUN &
  pid=$!
  while :; do
    rc=0
    wait "$pid" || rc=$?
    if [ "$rc" -gt 128 ]; then  # 'wait' interrupted by the trap, or VASP killed by a signal
      if kill -0 "$pid" 2>/dev/null; then continue; fi
      rc2=0
      wait "$pid" 2>/dev/null || rc2=$?
      if [ "$rc2" -ne 127 ]; then rc=$rc2; fi
    fi
    break
  done
  return "$rc"
}

finish() {                      # finish <exit code>
  local code=$1 t=$SECONDS
  rm -f STOPCAR
  log "exit code $code after $((t / 3600))h $((t % 3600 / 60))m $((t % 60))s"
  if [ "$AUTO_RESUBMIT" = "1" ] && { [ "$code" -eq 3 ] || [ "$code" -eq 4 ]; }; then
    if [ "$cycle" -lt "$MAX_TOTAL_CYCLES" ] && command -v sbatch >/dev/null 2>&1 && [ -f "$JOB_SCRIPT" ]; then
      log "AUTO_RESUBMIT: $(sbatch "$JOB_SCRIPT")"
    else
      log "AUTO_RESUBMIT: not resubmitted (cycle $cycle of MAX_TOTAL_CYCLES=$MAX_TOTAL_CYCLES, or no sbatch / $JOB_SCRIPT)"
    fi
  fi
  exit "$code"
}

# ---- checks -------------------------------------------------------------------
for f in INCAR POSCAR POTCAR; do
  if [ ! -s "$f" ]; then echo "[error] $f missing or empty in $(pwd)" >&2; exit 1; fi
done
if [ ! -s KPOINTS ] && ! grep -qiE '^[[:space:]]*KSPACING' INCAR; then
  echo "[error] no KPOINTS and no KSPACING in INCAR" >&2; exit 1
fi
if ! grep -qiE '(^|;)[[:space:]]*NSW[[:space:]]*=[[:space:]]*[1-9]' INCAR; then
  echo "[error] INCAR is not a relaxation (NSW must be > 0): use vasp_single_point.sh" >&2; exit 1
fi
if [ "$VASP_RUN" = "$LAUNCHER $VASP_EXE" ] && ! command -v "$VASP_EXE" >/dev/null 2>&1; then
  echo "[error] $VASP_EXE not found on PATH (check VASP_MODULE)" >&2; exit 1
fi

echo "=== job ${SLURM_JOB_ID:-local} (${SLURM_JOB_NAME:-vasp_relax}) ==="
echo "start   : $(date '+%F %T')"
echo "host    : $(hostname)"
echo "folder  : $(pwd)"
echo "tasks   : ${SLURM_NTASKS:-?} on ${SLURM_JOB_NUM_NODES:-?} node(s)"
echo "command : $VASP_RUN   (up to $MAX_CYCLES cycles)"
echo

rm -f STOPCAR                   # a leftover STOPCAR would stop VASP at once
if [ ! -f POSCAR.orig ]; then cp -p POSCAR POSCAR.orig; fi

# ---- resume what a previous job left behind ------------------------------------
cycle=$(last_cycle)
if converged; then
  log "OUTCAR already says 'reached required accuracy': nothing to do"
  finish 0
fi
if [ -f OUTCAR ]; then
  if [ "$cycle" -eq 0 ] || ! cmp -s OUTCAR "OUTCAR.cycle$cycle"; then
    cycle=$((cycle + 1))        # killed before it could archive: archive it now
    archive "$cycle"
    log "previous run archived as cycle $cycle"
  fi
  if valid_contcar; then
    cp CONTCAR POSCAR
    log "continuing from the CONTCAR of cycle $cycle"
  fi
fi

# ---- the restart loop ----------------------------------------------------------
for (( i = 1; i <= MAX_CYCLES; i++ )); do
  cycle=$((cycle + 1))
  log "cycle $cycle ($i of $MAX_CYCLES in this job)"
  vrc=0
  run_vasp || vrc=$?
  rm -f STOPCAR
  archive "$cycle"
  if converged; then
    log "converged in cycle $cycle"
    finish 0
  fi
  if [ "$vrc" -ne 0 ]; then
    log "VASP exited with code $vrc in cycle $cycle"
    finish 2
  fi
  if ! valid_contcar; then
    log "no usable CONTCAR after cycle $cycle: cannot restart"
    finish 2
  fi
  cp CONTCAR POSCAR
  log "not converged yet: CONTCAR -> POSCAR"
  if [ "$STOP_REQUESTED" -eq 1 ]; then
    log "stopped by the wall-time signal: resubmit to continue"
    finish 4
  fi
done
log "not converged after $MAX_CYCLES cycles in this job: resubmit to continue"
finish 3
