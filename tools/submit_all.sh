#!/usr/bin/env bash
# =============================================================================
#  submit_all.sh -- submit every job script below a directory, safely
# -----------------------------------------------------------------------------
#  Finds every job script (default name: job.sh) below ROOT and runs 'sbatch'
#  in its folder, EXCEPT when the folder
#    * is already done: its OUTCAR has VASP's timing block and, for a
#      relaxation (IBRION = 1, 2, 3 with NSW > 0), "reached required accuracy"
#      (a relaxation that stopped at NSW is resubmitted -- with
#      vasp_relax_autorestart.sh it then continues from CONTCAR);
#    * already has a job in the queue (squeue working directory), so running
#      the script twice never submits the same folder twice.
#
#  Usage
#    bash submit_all.sh [options] [ROOT] [-- extra sbatch options]
#
#  Options
#    -n, --dry-run     list what would be submitted, submit nothing
#    -j, --job NAME    job-script file name to look for (default: job.sh)
#    -m, --max N       submit at most N jobs (stay below the queue limit)
#    -f, --force       also submit folders that are already done
#    -h, --help        show this help
#
#  Examples
#    bash submit_all.sh -n runs/                    # what would happen?
#    bash submit_all.sh runs/
#    bash submit_all.sh -m 50 runs/                 # 50 now, rerun later for the rest
#    bash submit_all.sh runs/ -- --partition=debug --time=00:30:00
#
#  Exit code: 0 if every sbatch call succeeded, 1 otherwise, 2 on a usage error.
# =============================================================================
set -euo pipefail

usage() { awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"; }

DRY=0; FORCE=0; MAX=0; JOB="job.sh"; ROOT="."
SBATCH_ARGS=()
while [ "$#" -gt 0 ]; do
  case "$1" in
    -n|--dry-run) DRY=1 ;;
    -f|--force)   FORCE=1 ;;
    -j|--job)     JOB="${2:?--job needs a file name}"; shift ;;
    -m|--max)     MAX="${2:?--max needs a number}"; shift ;;
    -h|--help)    usage; exit 0 ;;
    --)           shift; SBATCH_ARGS=("$@"); break ;;
    -*)           echo "unknown option $1 (see --help)" >&2; exit 2 ;;
    *)            ROOT="$1" ;;
  esac
  shift
done
case "$MAX" in ''|*[!0-9]*) echo "--max needs a whole number" >&2; exit 2 ;; esac
if [ ! -d "$ROOT" ]; then echo "no such directory: $ROOT" >&2; exit 2; fi
if [ "$DRY" -eq 0 ] && ! command -v sbatch >/dev/null 2>&1; then
  echo "sbatch not found: run this on the cluster (or use --dry-run)" >&2; exit 2
fi

# ---- what is in the queue already (working directories of my jobs) -----------
QUEUED=""
if command -v squeue >/dev/null 2>&1; then
  QUEUED=$(squeue -h -u "${USER:-$(id -un)}" -o "%Z" 2>/dev/null || true)
else
  echo "[note] squeue not found: queued jobs cannot be detected" >&2
fi

in_queue() {                    # in_queue <dir>: a job of mine runs/waits in <dir>
  local d1 d2
  [ -n "$QUEUED" ] || return 1
  d1=$(cd "$1" && pwd)
  d2=$(cd "$1" && pwd -P)
  grep -qxF -e "$d1" -e "$d2" <<< "$QUEUED"
}

outcar_param() {                # outcar_param <OUTCAR> <TAG>: first value echoed by VASP
  awk -v tag="$2" '$1 == tag && $2 == "=" { print $3; exit }
                   $1 ~ "^"tag"=" { sub("^"tag"=", "", $1); print $1; exit }' "$1" | tr -d ';'
}

is_done() {                     # is_done <dir>: finished, and converged if a relaxation
  local oc="$1/OUTCAR" ibrion nsw
  [ -f "$oc" ] || return 1
  grep -q "General timing and accounting" "$oc" || return 1
  if grep -q "reached required accuracy" "$oc"; then return 0; fi
  ibrion=$(outcar_param "$oc" IBRION)
  nsw=$(outcar_param "$oc" NSW)
  case "$ibrion" in
    1|2|3) case "$nsw" in ''|*[!0-9]*) ;; *) if [ "$nsw" -gt 0 ]; then return 1; fi ;; esac ;;
  esac
  return 0
}

# ---- walk the tree -----------------------------------------------------------
n_sub=0; n_done=0; n_queue=0; n_fail=0; n_limit=0
while IFS= read -r script; do
  dir=$(dirname "$script")
  if in_queue "$dir"; then
    echo "queued    $dir"; n_queue=$((n_queue + 1)); continue
  fi
  if [ "$FORCE" -eq 0 ] && is_done "$dir"; then
    echo "done      $dir"; n_done=$((n_done + 1)); continue
  fi
  if [ "$MAX" -gt 0 ] && [ "$n_sub" -ge "$MAX" ]; then
    n_limit=$((n_limit + 1)); continue
  fi
  if [ "$DRY" -eq 1 ]; then
    echo "would run $dir: sbatch ${SBATCH_ARGS[*]+${SBATCH_ARGS[*]} }$JOB"
    n_sub=$((n_sub + 1)); continue
  fi
  if out=$(cd "$dir" && sbatch ${SBATCH_ARGS[@]+"${SBATCH_ARGS[@]}"} "$JOB" 2>&1); then
    echo "submitted $dir: $out"; n_sub=$((n_sub + 1))
  else
    echo "FAILED    $dir: $out" >&2; n_fail=$((n_fail + 1))
  fi
done < <(find "$ROOT" -type f -name "$JOB" | LC_ALL=C sort)

verb="submitted"
if [ "$DRY" -eq 1 ]; then verb="would submit"; fi
extra=""
if [ "$n_limit" -gt 0 ]; then extra=" (+$n_limit left out by --max)"; fi
echo
echo "$verb $n_sub, done $n_done, already queued $n_queue, failed $n_fail$extra"
[ "$n_fail" -eq 0 ]
