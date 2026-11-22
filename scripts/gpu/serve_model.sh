#!/usr/bin/env bash
# Serve an open-weights model with vLLM on this machine's GPU, for the Balatro bot running on another
# computer (the games run there, on the real game; only the model runs here).
#
#   bash serve_model.sh                    # Qwen3-8B, reachable only from this machine (use an SSH tunnel)
#   BIND=tailscale bash serve_model.sh     # reachable only from your own Tailscale devices
#   MODEL=Qwen/Qwen3-14B-AWQ bash serve_model.sh
#
# Standalone: copy just this file to the GPU machine. Needs Linux, an NVIDIA GPU with a working
# `nvidia-smi`, curl, ~30 GB free disk and internet for the first run. Installs uv (in ~/.local/bin) and
# vLLM in its own folder (VENV below); nothing system-wide, no sudo.
#
# Safety: the server listens on 127.0.0.1 (or the Tailscale address), never on all interfaces, and needs
# a random key, created on the first run and kept in ~/.config/balatro-bot/vllm_key (only you can read it).
# Stop the server with Ctrl+C.
set -euo pipefail

MODEL="${MODEL:-Qwen/Qwen3-8B}"
TOOL_PARSER="${TOOL_PARSER:-hermes}"            # vLLM tool-call parser for Qwen3 / Qwen2.5
REASONING_PARSER="${REASONING_PARSER:-qwen3}"   # puts Qwen3's thinking in its own field; "" for other models
MAX_LEN="${MAX_LEN:-24576}"                     # prompt (~13k tokens) + answer (max_tokens 8192) + margin
GPU_MEM="${GPU_MEM:-0.90}"                      # share of GPU memory vLLM may take
PORT="${PORT:-8000}"
BIND="${BIND:-127.0.0.1}"                       # 127.0.0.1 (SSH tunnel) or "tailscale"
VLLM_SPEC="${VLLM_SPEC:-vllm}"                  # e.g. "vllm==0.11.0" to pin a version that worked
VLLM_ARGS="${VLLM_ARGS:-}"                      # extra flags for `vllm serve`
VENV="${VENV:-$HOME/.cache/balatro-bot/vllm-venv}"
FORCE="${FORCE:-0}"                             # 1: start even if other programs are using the GPU
KEY_FILE="$HOME/.config/balatro-bot/vllm_key"
LOG_DIR="$HOME/.cache/balatro-bot/logs"
say() { printf '\n== %s\n' "$*"; }

say "checking the machine"
[ "$(uname -s)" = "Linux" ] || { echo "This script is for Linux."; exit 1; }
for tool in curl nvidia-smi; do
  command -v "$tool" >/dev/null || { echo "$tool not found."; exit 1; }
done
nvidia-smi --query-gpu=name,memory.total,memory.used,driver_version --format=csv,noheader
used_mib="$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits | head -1 | tr -d ' ')"
if [ "${used_mib:-0}" -gt 3000 ] && [ "$FORCE" != "1" ]; then
  echo "The GPU already has ${used_mib} MiB in use by other programs:"
  nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader || true
  echo "If this is a shared machine, someone may be using it. Start anyway with FORCE=1."
  exit 1
fi
free_gb="$(df -Pk "$HOME" | awk 'NR==2 {print int($4/1024/1024)}')"
[ "$free_gb" -ge 30 ] || echo "warning: only ${free_gb} GB free in $HOME (the model and vLLM need ~30 GB)"

case "$BIND" in
  127.0.0.1|localhost) HOST=127.0.0.1 ;;
  tailscale)
    command -v tailscale >/dev/null || { echo "tailscale is not installed."; exit 1; }
    HOST="$(tailscale ip -4 | head -1)"
    case "$HOST" in 100.*) ;; *) echo "no Tailscale address (is it logged in? tailscale status)"; exit 1 ;; esac ;;
  *) echo "BIND must be 127.0.0.1 or tailscale (never all interfaces)."; exit 1 ;;
esac

mkdir -p "$(dirname "$KEY_FILE")" "$LOG_DIR"
if [ ! -s "$KEY_FILE" ]; then
  (umask 077; head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' > "$KEY_FILE")
  echo "created a new key in $KEY_FILE"
fi
chmod 600 "$KEY_FILE"

if ! command -v uv >/dev/null && [ ! -x "$HOME/.local/bin/uv" ]; then
  say "installing uv (Python package manager, into ~/.local/bin)"
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"
if [ ! -x "$VENV/bin/vllm" ]; then
  say "installing vLLM into $VENV (a few GB, once)"
  uv venv "$VENV" --python 3.12
  uv pip install --python "$VENV/bin/python" "$VLLM_SPEC" 2>&1 | tail -3
fi
"$VENV/bin/python" -c "import vllm; print('vLLM', vllm.__version__)"

# the key goes in the environment, not the command line, so other users of the machine can't see it in `ps`
VLLM_API_KEY="$(cat "$KEY_FILE")"
export VLLM_API_KEY
args=(--host "$HOST" --port "$PORT" --served-model-name "$MODEL"
      --enable-auto-tool-choice --tool-call-parser "$TOOL_PARSER"
      --max-model-len "$MAX_LEN" --gpu-memory-utilization "$GPU_MEM" --enable-prefix-caching)
[ -n "$REASONING_PARSER" ] && args+=(--reasoning-parser "$REASONING_PARSER")
# shellcheck disable=SC2206  # VLLM_ARGS is meant to split into separate flags
[ -n "$VLLM_ARGS" ] && args+=($VLLM_ARGS)

log="$LOG_DIR/vllm-$(date +%Y%m%d-%H%M%S).log"
say "starting $MODEL on http://$HOST:$PORT/v1 (the first start downloads the model, ~16 GB)"
echo "log: $log"
echo "Ready when the log says 'Application startup complete'. Leave this window open; Ctrl+C stops it."
echo "On your Mac, the key is: cat $KEY_FILE   (copy it to ~/.config/balatro-bot/gpu_key there)"
"$VENV/bin/vllm" serve "$MODEL" "${args[@]}" 2>&1 | tee "$log"
