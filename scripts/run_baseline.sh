#!/usr/bin/env bash
# The LLM baseline: Qwen3 plays Black Deck / Gold Stake on the exact engine (the real game, headless,
# on this Mac), with the model served by vLLM on a GPU machine (scripts/gpu/serve_model.sh there).
# Analysis tools are switches (TOOLS): the baseline offers only the score preview (score_plays: minimum
# and maximum score, average money per play). Records keep every tool's answer for the viewer either way.
# See docs/REMOTE_GPU.md for the whole setup.
#
#   bash scripts/run_baseline.sh                      # one game (the first dev seed)
#   N=20 PARALLEL=4 bash scripts/run_baseline.sh      # 20 games, 4 at a time
#   SPLIT=heldout N=100 bash scripts/run_baseline.sh  # final numbers (only once the setup is frozen)
#   MOCK=1 bash scripts/run_baseline.sh               # dry run with a fake model, no GPU
#   TOOLS=none bash scripts/run_baseline.sh           # game actions only
#   TOOLS=score_plays,optimize_jokers,optimize_hand bash scripts/run_baseline.sh   # pick any tools
#   TOOLS=all bash scripts/run_baseline.sh            # + estimate_clear_chance (a search, not a calculator)
#
# The key for the model server is read from BALATRO_GPU_KEY or ~/.config/balatro-bot/gpu_key.
# Rerunning skips games already finished under the same label; games stopped by a connection or server
# problem are retried. Results: runtime/eval/llm.jsonl, full logs and records under runtime/llm_runs/.
set -euo pipefail

MODEL="${MODEL:-Qwen/Qwen3-8B}"
BASE_URL="${BASE_URL:-http://127.0.0.1:8000/v1}"   # through the SSH tunnel; or http://<tailscale ip>:8000/v1
THINKING="${THINKING:-1}"                          # Qwen3 thinking mode: 1 on (the baseline), 0 off
MAX_TOKENS="${MAX_TOKENS:-8192}"                   # longest answer (thinking included) per decision
N="${N:-1}"
PARALLEL="${PARALLEL:-1}"
SPLIT="${SPLIT:-dev}"
MOCK="${MOCK:-0}"
TOOLS="${TOOLS:-baseline}"                          # baseline (= score_plays), none, all, or a comma list

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
KEY_FILE="$HOME/.config/balatro-bot/gpu_key"
if [ -z "${BALATRO_GPU_KEY:-}" ] && [ -s "$KEY_FILE" ]; then
  BALATRO_GPU_KEY="$(tr -d ' \n' < "$KEY_FILE")"
fi
export BALATRO_GPU_KEY="${BALATRO_GPU_KEY:-}"

# Sampling as Qwen recommends for each mode (greedy decoding makes Qwen3's thinking loop). Sent with
# every request, so the result doesn't depend on the server's defaults.
short="$(basename "$MODEL" | tr '[:upper:]' '[:lower:]')"
if [ "$THINKING" = "1" ]; then
  sampling='"temperature": 0.6, "top_p": 0.95'; extra='"top_k": 20, "min_p": 0, "chat_template_kwargs": {"enable_thinking": true}'
else
  sampling='"temperature": 0.7, "top_p": 0.8'; extra='"top_k": 20, "min_p": 0, "chat_template_kwargs": {"enable_thinking": false}'
  short="$short-nothink"
fi
MODEL_CONFIG="{$sampling, \"max_tokens\": $MAX_TOKENS, \"extra_body\": {$extra}}"
tool_args=(); [ "$TOOLS" != "none" ] && tool_args=(--tools "$TOOLS")
case "$TOOLS" in
  baseline) arm="baseline" ;;
  none) arm="notools" ;;
  *) arm="tools-${TOOLS//,/+}" ;;
esac
LABEL="${LABEL:-$short-$arm}"

if [ "$MOCK" = "1" ]; then
  echo "dry run: fake model, real game"
  exec uv run python scripts/run_llm.py --mock --label "mock-$arm-$(date +%s)" --backend engine \
    --n "$N" --parallel "$PARALLEL" --split "$SPLIT" ${tool_args[@]+"${tool_args[@]}"}
fi

[ -n "$BALATRO_GPU_KEY" ] || { echo "No key: put the model server's key in $KEY_FILE (see docs/REMOTE_GPU.md)."; exit 1; }
echo "== checking the model server"
uv run python scripts/check_model_server.py --base-url "$BASE_URL" --model "$MODEL" \
  --api-key-env BALATRO_GPU_KEY --model-config "$MODEL_CONFIG"

echo
echo "== playing $N $SPLIT game(s) as $LABEL, $PARALLEL at a time"
uv run python scripts/run_llm.py --label "$LABEL" --model "$MODEL" --base-url "$BASE_URL" \
  --api-key-env BALATRO_GPU_KEY --backend engine --model-config "$MODEL_CONFIG" \
  --price-in 0 --price-out 0 --price-cached 0 --n "$N" --parallel "$PARALLEL" --split "$SPLIT" ${tool_args[@]+"${tool_args[@]}"}

echo
echo "Watch a game: uv run python scripts/view_record.py   (the newest record is at the top)"
