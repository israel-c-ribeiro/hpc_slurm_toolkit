#!/usr/bin/env bash
# =============================================================================
#  python_gpu_or_cpu.sh -- a Python job (ASE, MACE or any ML/analysis script)
# -----------------------------------------------------------------------------
#  One Python process, on CPU cores or on a GPU. Typical uses: molecular
#  dynamics or relaxations with a machine-learning potential (an ASE script
#  with a MACE calculator), training a model, heavy post-processing.
#
#  Usage
#    sbatch python_gpu_or_cpu.sh
#    sbatch --export=ALL,SCRIPT=run_md.py,SCRIPT_ARGS="--steps 50000" python_gpu_or_cpu.sh
#
#  CPU or GPU
#    CPU: leave the '##SBATCH --gres' line as it is ('##' = disabled) and set
#         --cpus-per-task to the number of threads you want.
#    GPU: remove one '#' from '##SBATCH --gres=gpu:1' and choose a GPU
#         partition. Check the GPU type names with: sinfo -o "%P %G"
#
#  Edit the lines marked  >>> EDIT
#    ENV_KIND     venv | conda | module | none -- how your Python is provided
#    VENV_PATH    path of the virtual environment (ENV_KIND=venv)
#    CONDA_ENV    name of the conda environment   (ENV_KIND=conda)
#    PY_MODULES   modules to load (always loaded first; e.g. Python, CUDA)
#    SCRIPT, SCRIPT_ARGS   what to run
#
#  Exit code: the one of the Python script.
# =============================================================================
# >>> EDIT: resources
#SBATCH --job-name=py_job
#SBATCH --account=<your_account>
#SBATCH --partition=<partition>
#SBATCH --time=12:00:00
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
##SBATCH --gres=gpu:1
#SBATCH --output=%x_%j.out
#SBATCH --error=%x_%j.err

# >>> EDIT: Python environment (activated before 'set -u': activation scripts of
#           conda and of some modules reference unset variables)
ENV_KIND="${ENV_KIND:-venv}"
VENV_PATH="${VENV_PATH:-$HOME/venvs/ml}"
CONDA_ENV="${CONDA_ENV:-ml}"
PY_MODULES="${PY_MODULES:-}"

if [ -n "$PY_MODULES" ] && command -v module >/dev/null 2>&1; then
  module purge
  for m in $PY_MODULES; do module load "$m"; done
fi
case "$ENV_KIND" in
  venv)
    if [ ! -f "$VENV_PATH/bin/activate" ]; then
      echo "[error] no virtual environment at VENV_PATH=$VENV_PATH (python -m venv $VENV_PATH)" >&2; exit 1
    fi
    # shellcheck disable=SC1091
    source "$VENV_PATH/bin/activate" ;;
  conda)
    eval "$(conda shell.bash hook)"
    conda activate "$CONDA_ENV" ;;
  module|none) ;;
  *) echo "[error] ENV_KIND must be venv, conda, module or none (got '$ENV_KIND')" >&2; exit 1 ;;
esac

set -euo pipefail

# >>> EDIT: what to run
SCRIPT="${SCRIPT:-run.py}"
SCRIPT_ARGS="${SCRIPT_ARGS:-}"

# Threads = the cores Slurm gave this task (numpy/torch would otherwise use the whole node)
export OMP_NUM_THREADS="${SLURM_CPUS_PER_TASK:-1}"
export MKL_NUM_THREADS="$OMP_NUM_THREADS"
export OPENBLAS_NUM_THREADS="$OMP_NUM_THREADS"
export PYTHONUNBUFFERED=1        # print() reaches the .out file immediately

cd "${SLURM_SUBMIT_DIR:-.}"
if [ ! -f "$SCRIPT" ]; then echo "[error] $SCRIPT not found in $(pwd)" >&2; exit 1; fi

echo "=== job ${SLURM_JOB_ID:-local} (${SLURM_JOB_NAME:-py_job}) ==="
echo "start   : $(date '+%F %T')"
echo "host    : $(hostname)"
echo "folder  : $(pwd)"
echo "python  : $(command -v python) ($(python -c 'import sys; print(sys.version.split()[0])'))"
echo "threads : $OMP_NUM_THREADS"
if [ -n "${CUDA_VISIBLE_DEVICES:-}" ] && command -v nvidia-smi >/dev/null 2>&1; then
  echo "gpu     : $(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader | paste -sd ';' -)"
else
  echo "gpu     : none"
fi
echo "command : python -u $SCRIPT $SCRIPT_ARGS"
echo

SECONDS=0
rc=0
# SCRIPT_ARGS is split into words on purpose
# shellcheck disable=SC2086
python -u "$SCRIPT" $SCRIPT_ARGS || rc=$?
t=$SECONDS
printf '\nend     : %s  (elapsed %dh %02dm %02ds, exit code %d)\n' \
  "$(date '+%F %T')" $((t / 3600)) $((t % 3600 / 60)) $((t % 60)) "$rc"
exit "$rc"
