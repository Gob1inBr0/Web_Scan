#!/usr/bin/env bash
set -euo pipefail

ENV_NAME="${HAC_ENV_NAME:-HAC_env}"
CUDA_HOME="${CUDA_HOME:-/usr/local/cuda-12.1}"
TORCH_CUDA_ARCH_LIST="${TORCH_CUDA_ARCH_LIST:-8.9}"
MAX_JOBS="${MAX_JOBS:-4}"
OMP_NUM_THREADS="${OMP_NUM_THREADS:-1}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if command -v mamba >/dev/null 2>&1; then
  CONDA_FRONTEND=mamba
else
  CONDA_FRONTEND=conda
fi

if ! command -v conda >/dev/null 2>&1; then
  echo "conda is required. Source conda.sh first, for example:" >&2
  echo "source /root/autodl-tmp/miniforge3/etc/profile.d/conda.sh" >&2
  exit 1
fi

eval "$(conda shell.bash hook)"

if conda env list | awk '{print $1}' | grep -qx "${ENV_NAME}"; then
  echo "Reusing conda environment: ${ENV_NAME} (shared with HAC++ if already installed)"
else
  "${CONDA_FRONTEND}" create -y -n "${ENV_NAME}" python=3.10 pip
fi

conda activate "${ENV_NAME}"

"${CONDA_FRONTEND}" install -y -c pytorch -c nvidia -c conda-forge \
  pytorch=2.2.2 torchvision=0.17.2 torchaudio=2.2.2 pytorch-cuda=12.1 \
  numpy=1.26.* scipy pillow=10.* plyfile=1.1.* tqdm

python -m pip install --upgrade pip setuptools wheel ninja packaging
python -m pip install \
  einops \
  wandb==0.16.4 \
  lpips \
  colorama \
  opencv-python \
  imageio \
  matplotlib
python -m pip install torch-scatter -f https://data.pyg.org/whl/torch-2.2.0+cu121.html

export CUDA_HOME
export TORCH_CUDA_ARCH_LIST
export FORCE_CUDA=1
export MAX_JOBS
export OMP_NUM_THREADS
export PATH="${CUDA_HOME}/bin:${PATH}"
export LD_LIBRARY_PATH="${CONDA_PREFIX}/lib:${CUDA_HOME}/lib64:${LD_LIBRARY_PATH:-}"

prepare_submodule_source() {
  local name="$1"
  local dir="${ROOT_DIR}/submodules/${name}"
  local zip="${ROOT_DIR}/submodules/${name}.zip"

  if [ -f "${dir}/setup.py" ]; then
    return 0
  fi
  if [ -f "${zip}" ]; then
    echo "Extracting ${zip}"
    unzip -oq "${zip}" -d "${ROOT_DIR}/submodules" -x "__MACOSX/*" "*/__MACOSX/*"
  fi
  if [ ! -f "${dir}/setup.py" ]; then
    echo "Missing HAC CUDA submodule source: ${dir}" >&2
    echo "Restore ${name}/ or ${name}.zip before running this script." >&2
    exit 1
  fi
}

for module in arithmetic diff-gaussian-rasterization simple-knn gridencoder; do
  prepare_submodule_source "${module}"
  python -m pip install --no-build-isolation -v "${ROOT_DIR}/submodules/${module}"
done

python - <<'PY'
import torch

print("torch:", torch.__version__)
print("cuda:", torch.version.cuda)
print("cuda available:", torch.cuda.is_available())
if torch.cuda.is_available():
    print("gpu:", torch.cuda.get_device_name(0))
    print("capability:", torch.cuda.get_device_capability(0))
PY
