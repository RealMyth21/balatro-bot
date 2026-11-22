#!/usr/bin/env bash
# Play Balatro bot games with an open-weights LLM on this machine's GPU, then pack every result into
# one file to send back. Meant to be run by someone else: no API key, no Balatro install needed.
#
#   bash scripts/gpu/run_on_gpu.sh                 # defaults below
#   N=50 PARALLEL=6 bash scripts/gpu/run_on_gpu.sh # more games
#   MOCK=1 bash scripts/gpu/run_on_gpu.sh          # dry run without a GPU (fake model)
#
# What it does: installs the project (uv) and vLLM in its own environment, starts vLLM serving MODEL with
# tool calling on localhost, plays N games per arm on the jackdaw simulator (no Balatro install needed),
# exports fine-tuning data, and writes results/balatro-results-<host>-<time>.tar.gz. Needs Linux (or
# WSL2 on Windows) with an NVIDIA GPU, ~40 GB of free disk, and internet access for the downloads.
set -euo pipefail

MODEL="${MODEL:-Qwen/Qwen2.5-7B-Instruct}"     # any vLLM model with tool calling
TOOL_PARSER="${TOOL_PARSER:-hermes}"            # vLLM tool-call parser for MODEL's family (hermes: Qwen 2.5)
MAX_LEN="${MAX_LEN:-32768}"                     # the game prompt is ~13k tokens per decision
GPU_MEM="${GPU_MEM:-0.90}"                      # share of GPU memory vLLM may use
N="${N:-20}"                                    # games per arm
PARALLEL="${PARALLEL:-4}"                       # games at once (24 GB GPU + 7-8B model: 4-8)
SPLIT="${SPLIT:-dev}"                           # seed list: dev (tuning) or heldout (final numbers)
ARMS="${ARMS:-notools tools}"                   # notools: game actions only; tools: + analysis tools
PORT="${PORT:-8000}"
MOCK="${MOCK:-0}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
STAMP="$(date +%Y%m%d-%H%M%S)"
LOGS="$ROOT/runtime/gpu_logs/$STAMP"
mkdir -p "$LOGS" "$ROOT/results"
say() { printf '\n== %s\n' "$*"; }
VLLM_PID=""

pack() {
  status=$?
  [ -n "$VLLM_PID" ] && kill "$VLLM_PID" 2>/dev/null || true
  say "packing results (exit status $status)"
  {
    echo "date: $STAMP"; echo "model: $MODEL"; echo "tool_parser: $TOOL_PARSER"; echo "n: $N parallel: $PARALLEL"
    echo "split: $SPLIT arms: $ARMS mock: $MOCK exit: $status"
    git -C "$ROOT" rev-parse HEAD 2>/dev/null | sed 's/^/commit: /' || true
    nvidia-smi 2>/dev/null || echo "no nvidia-smi"
  } > "$LOGS/run_info.txt"
  out="$ROOT/results/balatro-results-$(hostname -s 2>/dev/null || echo host)-$STAMP.tar.gz"
  tar czf "$out" -C "$ROOT" runtime/eval runtime/llm_runs runtime/sft "runtime/gpu_logs/$STAMP" 2>/dev/null || \
    tar czf "$out" -C "$ROOT" "runtime/gpu_logs/$STAMP"
  say "DONE. Send this file back: $out"
}
trap pack EXIT

say "checking the machine"
if [ "$MOCK" != "1" ]; then
  command -v nvidia-smi >/dev/null || { echo "No NVIDIA GPU found (nvidia-smi missing)."; exit 1; }
  nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
fi
if ! command -v uv >/dev/null; then
  say "installing uv (Python package manager)"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

say "installing the project (simulator + LLM harness)"
uv sync --extra sim --extra llm 2>&1 | tail -3

BASE_URL="http://127.0.0.1:$PORT/v1"
if [ "$MOCK" != "1" ]; then
  if [ ! -x "$ROOT/.venv-vllm/bin/vllm" ]; then
    say "installing vLLM in its own environment (a few GB)"
    uv venv "$ROOT/.venv-vllm" --python 3.12
    uv pip install --python "$ROOT/.venv-vllm/bin/python" vllm 2>&1 | tail -3
  fi
  say "starting vLLM with $MODEL (the first start downloads the model)"
  "$ROOT/.venv-vllm/bin/vllm" serve "$MODEL" --port "$PORT" --host 127.0.0.1 \
    --enable-auto-tool-choice --tool-call-parser "$TOOL_PARSER" \
    --max-model-len "$MAX_LEN" --gpu-memory-utilization "$GPU_MEM" > "$LOGS/vllm.log" 2>&1 &
  VLLM_PID=$!
  for i in $(seq 1 180); do  # up to 30 minutes for download + load
    curl -sf "$BASE_URL/models" >/dev/null && break
    kill -0 "$VLLM_PID" 2>/dev/null || { echo "vLLM exited; last lines:"; tail -30 "$LOGS/vllm.log"; exit 1; }
    sleep 10
  done
  curl -sf "$BASE_URL/models" >/dev/null || { echo "vLLM did not come up"; tail -30 "$LOGS/vllm.log"; exit 1; }
  echo "vLLM ready"
fi

export LOCAL_LLM_KEY="unused"  # the local server needs no key; the runner just reads one
SHORT="$(basename "$MODEL" | tr '[:upper:]' '[:lower:]')"
for arm in $ARMS; do
  label="$SHORT-$arm"
  extra=()
  [ "$arm" = "tools" ] && extra+=(--tools --option-rollout smart)  # smart: fast shop evaluation on CPU
  say "playing $N games: $label"
  if [ "$MOCK" = "1" ]; then
    uv run python scripts/run_llm.py --mock --label "mock-$arm" --backend jackdaw --n "$N" \
      --parallel "$PARALLEL" --split "$SPLIT" ${extra[@]+"${extra[@]}"} 2>&1 | tee "$LOGS/games-$arm.log"
  else
    uv run python scripts/run_llm.py --label "$label" --model "$MODEL" --base-url "$BASE_URL" \
      --api-key-env LOCAL_LLM_KEY --price-in 0 --price-out 0 --price-cached 0 --backend jackdaw \
      --n "$N" --parallel "$PARALLEL" --split "$SPLIT" ${extra[@]+"${extra[@]}"} 2>&1 | tee "$LOGS/games-$arm.log"
  fi
done

say "exporting fine-tuning data"
uv run python scripts/export_sft.py 2>&1 | tail -3 | tee "$LOGS/export.log"
