#!/usr/bin/env bash
# =============================================================================
#  fetch_runs.sh -- resumable rsync of run folders from a cluster
# -----------------------------------------------------------------------------
#  Copies <REMOTE_ROOT>/<remote_subdir>/ from the cluster into
#  <LOCAL_ROOT>/<local_subdir>/, through an SSH jump host if needed.
#  Interrupted? Run the exact same command again: rsync skips complete files
#  and continues partial ones (--partial).
#
#  Usage
#    bash fetch_runs.sh [options] <remote_subdir> <local_subdir> [pattern]
#
#    <remote_subdir>  relative to REMOTE_ROOT, or an absolute remote path
#    <local_subdir>   relative to LOCAL_ROOT, or an absolute local path
#    [pattern]        take only the top-level folders matching it, e.g. 'Si_*'
#                     or '*_done' (shell-style wildcards; quote it!)
#
#  Options
#    -s, --small          skip WAVECAR, WAVEDER, CHGCAR, CHG and core dumps
#                         (alias: --include-only-small)
#    -x, --exclude PAT    one more exclude pattern (repeatable), e.g. -x vasprun.xml
#    -n, --dry-run        list what would be transferred, copy nothing
#    -c, --config FILE    configuration file (default: see below)
#    -h, --help           show this help
#
#  Configuration (environment variables win over the configuration file;
#  file = --config, else $FETCH_RUNS_CONF, else ./fetch_runs.conf, else
#  fetch_runs.conf next to this script -- see fetch_runs.conf.example)
#    REMOTE       user@login.cluster.example.org, or a Host alias of ~/.ssh/config
#    GATEWAY      user@gateway.example.org (jump host); empty = direct connection
#    REMOTE_ROOT  base directory on the cluster, e.g. /path/to/runs
#    LOCAL_ROOT   base directory here; E:/data style paths are translated for
#                 MobaXterm (/drives/e), WSL (/mnt/e), Cygwin and Git Bash (/e)
#    SSH_OPTS     extra ssh options, e.g. "-i $HOME/.ssh/id_ed25519" (optional;
#                 write $HOME, not ~: rsync does not expand ~ inside -e)
#    NTFS_SAFE    auto | 1 | 0: do not copy permissions/owner/group (needed on
#                 Windows drives; auto = on under Windows-like shells and /mnt/<x>)
#
#  Examples
#    bash fetch_runs.sh campaign1 campaign1               # everything
#    bash fetch_runs.sh -s campaign1 campaign1 'Si_*'     # only Si_* folders, no WAVECAR...
#    REMOTE=mycluster GATEWAY= bash fetch_runs.sh -n /path/to/runs/test test
#
#  Tutorial: docs/04_moving_data_with_rsync.md (filters, what not to transfer).
#  Exit code: the one of rsync (0 = complete; 23/24 = partial, rerun later).
# =============================================================================
set -euo pipefail

usage() { awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"; }

# ---- options -------------------------------------------------------------------
SMALL=0; DRY=0; CONF=""
EXTRA_EXCLUDES=()
ARGS=()
while [ "$#" -gt 0 ]; do
  case "$1" in
    -s|--small|--include-only-small) SMALL=1 ;;
    -n|--dry-run) DRY=1 ;;
    -x|--exclude) EXTRA_EXCLUDES+=("--exclude=${2:?--exclude needs a pattern}"); shift ;;
    -c|--config)  CONF="${2:?--config needs a file}"; shift ;;
    -h|--help)    usage; exit 0 ;;
    --)           shift; ARGS+=("$@"); break ;;
    -*)           echo "unknown option $1 (see --help)" >&2; exit 2 ;;
    *)            ARGS+=("$1") ;;
  esac
  shift
done
if [ "${#ARGS[@]}" -lt 2 ] || [ "${#ARGS[@]}" -gt 3 ]; then
  usage >&2
  exit 2
fi
REMOTE_SUB="${ARGS[0]%/}"
LOCAL_SUB="${ARGS[1]%/}"
PATTERN="${ARGS[2]:-}"

# ---- configuration: file first, then the environment on top ----------------------
CONF_VARS="REMOTE GATEWAY REMOTE_ROOT LOCAL_ROOT SSH_OPTS NTFS_SAFE"
if [ -z "$CONF" ]; then
  for c in "${FETCH_RUNS_CONF:-}" ./fetch_runs.conf "$(dirname "$0")/fetch_runs.conf"; do
    if [ -n "$c" ] && [ -f "$c" ]; then CONF="$c"; break; fi
  done
elif [ ! -f "$CONF" ]; then
  echo "configuration file $CONF not found" >&2; exit 2
fi
if [ -n "$CONF" ]; then
  saved=""
  for v in $CONF_VARS; do
    if eval "[ -n \"\${$v+x}\" ]"; then saved="$saved $v"; eval "_env_$v=\"\${$v}\""; fi
  done
  # shellcheck disable=SC1090
  . "$CONF"
  for v in $saved; do eval "$v=\"\${_env_$v}\""; done
fi
REMOTE="${REMOTE:-}"; GATEWAY="${GATEWAY:-}"; REMOTE_ROOT="${REMOTE_ROOT:-}"
LOCAL_ROOT="${LOCAL_ROOT:-.}"; SSH_OPTS="${SSH_OPTS:-}"; NTFS_SAFE="${NTFS_SAFE:-auto}"
if [ -z "$REMOTE" ]; then
  echo "REMOTE is not set: export REMOTE=user@login.cluster.example.org or write a fetch_runs.conf" >&2
  exit 2
fi

# ---- paths ---------------------------------------------------------------------
to_posix() {                    # E:/data or E:\data -> /drives/e/data, /mnt/e/data, /cygdrive/e/data or /e/data
  local p="$1" d rest pre
  case "$p" in
    [A-Za-z]:[\\/]*|[A-Za-z]:)
      d=$(printf '%s' "${p:0:1}" | tr 'A-Z' 'a-z')
      rest="${p:2}"; rest="${rest//\\//}"
      for pre in /drives /mnt /cygdrive ""; do
        if [ -d "$pre/$d" ]; then printf '%s\n' "$pre/$d$rest"; return; fi
      done ;;
  esac
  printf '%s\n' "$p"
}
case "$REMOTE_SUB" in
  /*) SRC_PATH="$REMOTE_SUB" ;;
  *)  if [ -z "$REMOTE_ROOT" ]; then echo "REMOTE_ROOT is not set and $REMOTE_SUB is relative" >&2; exit 2; fi
      SRC_PATH="${REMOTE_ROOT%/}/$REMOTE_SUB" ;;
esac
LOCAL_SUB=$(to_posix "$LOCAL_SUB")
case "$LOCAL_SUB" in
  /*) DEST="$LOCAL_SUB" ;;
  *)  DEST="$(to_posix "$LOCAL_ROOT")/$LOCAL_SUB" ;;
esac
SRC="$REMOTE:$SRC_PATH/"
DEST="${DEST%/}/"

# ---- environment checks ----------------------------------------------------------
if ! command -v rsync >/dev/null 2>&1; then
  echo "[error] rsync not found. On Windows use MobaXterm's local terminal, WSL or MSYS2" >&2
  echo "        (Git Bash has no rsync). See docs/04_moving_data_with_rsync.md" >&2
  exit 1
fi
agent_rc=0
ssh-add -l >/dev/null 2>&1 || agent_rc=$?
if [ "$agent_rc" -ne 0 ]; then
  echo "[note] no key loaded in an ssh-agent: you may be asked for your passphrase" >&2
  echo "       (once per connection, twice with a jump host). To avoid it:" >&2
  echo "         eval \"\$(ssh-agent -s)\" && ssh-add ~/.ssh/<your_key>" >&2
fi

if [ "$NTFS_SAFE" = "auto" ]; then
  NTFS_SAFE=0
  case "$(uname -s 2>/dev/null)" in MINGW*|MSYS*|CYGWIN*) NTFS_SAFE=1 ;; esac
  case "$DEST" in /mnt/[a-z]/*|/drives/*|/cygdrive/*) NTFS_SAFE=1 ;; esac
fi

# ---- rsync options -----------------------------------------------------------------
# Order matters: rsync applies the FIRST rule that matches a path, so the
# excludes of heavy files must come before the includes of the pattern
# (otherwise '/<pattern>/**' would include WAVECAR as well).
OPTS=(-avzhP --stats)
if [ "$NTFS_SAFE" = "1" ]; then OPTS+=(--no-perms --no-owner --no-group); fi
if [ "$DRY" -eq 1 ]; then OPTS+=(--dry-run); fi
FILTERS=()
if [ "$SMALL" -eq 1 ]; then
  FILTERS+=(--exclude=WAVECAR --exclude=WAVEDER --exclude=CHGCAR --exclude=CHG --exclude='core' --exclude='core.*')
fi
FILTERS+=(${EXTRA_EXCLUDES[@]+"${EXTRA_EXCLUDES[@]}"})
if [ -n "$PATTERN" ]; then
  # anchored with '/': tested on the top-level folder names only, never on files inside
  FILTERS+=(--include="/$PATTERN/" --include="/$PATTERN/**" --exclude='/*')
fi
SSH_CMD="ssh${SSH_OPTS:+ $SSH_OPTS}${GATEWAY:+ -J $GATEWAY}"

echo "[*] from : $SRC${PATTERN:+   (only folders matching '$PATTERN')}"
echo "[*] to   : $DEST"
echo "[*] via  : ${GATEWAY:-direct connection}"
if [ "$SMALL" -eq 1 ]; then echo "[*] small: WAVECAR, WAVEDER, CHGCAR, CHG and core dumps are skipped"; fi
if [ "$DRY" -eq 1 ]; then echo "[*] DRY RUN: nothing is copied"; fi
echo

if [ "$DRY" -eq 0 ]; then mkdir -p "$DEST"; fi
rc=0
rsync "${OPTS[@]}" ${FILTERS[@]+"${FILTERS[@]}"} -e "$SSH_CMD" "$SRC" "$DEST" || rc=$?

echo
case "$rc" in
  0)
    if [ "$DRY" -eq 0 ]; then
      n=0; done_n=0
      for d in "$DEST"*/; do
        [ -d "$d" ] || continue
        n=$((n + 1))
        if grep -q "General timing and accounting" "$d/OUTCAR" 2>/dev/null; then done_n=$((done_n + 1)); fi
      done
      echo "=== done: $n folder(s) in $DEST ($done_n with a finished OUTCAR)"
    fi ;;
  23|24)
    echo "=== rsync exited $rc: partial transfer (24 = files vanished, e.g. a job is still writing)."
    echo "=== Re-run the same command later to complete it." ;;
  *)
    echo "=== rsync exited $rc -- re-run the same command to resume." ;;
esac
exit "$rc"
