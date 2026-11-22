# Compute plan

Everything runs on **one NVIDIA RTX 4090 (24 GB)** and the CPU of the machine it's in. No paid APIs:
every model is an open-weights model served on that GPU. Numbers below are estimates until the first
GPU run measures them.

## What runs where
| Stage | Runs on | Status |
|---|---|---|
| Engine, jackdaw, validation, baselines, search bot | CPU | done |
| LLM pipeline (mock, with and without tools), game records, viewer | CPU | done |
| LLM baseline with an open model | GPU (vLLM) + CPU (games, tools) | ready: `scripts/gpu/run_on_gpu.sh`, untested on the GPU |
| Judgment score (position value network) | CPU (game data) + GPU (training) | next |
| Search + PPO bot (non-LLM) | CPU (jackdaw envs) + GPU (network) | after the judgment score |
| LLM fine-tuning (SFT / DPO, later per-decision GRPO) | GPU (LoRA) | after the baseline |

The CPU matters as much as the GPU: the simulator, the search player and the analysis tools all run on
CPU cores. More cores means more games in parallel.

## Which LLMs fit
- **7–8B models at full precision** (about 16 GB of weights, the rest for the context cache): the
  default is `Qwen/Qwen2.5-7B-Instruct` (vLLM tool parser `hermes`); Qwen3-8B is the next to try.
- **~14B models compressed to 4 bits** (AWQ/GPTQ) also fit, with less room for parallel games.
- Larger models don't fit on one 24 GB GPU.
- Small models will be weak at Black Deck / Gold Stake; they're the starting point for fine-tuning,
  not expected to win on their own.

## LLM baseline
- **Prompt:** BalatroLLM's prompt is about 12.8k tokens per decision, ~84% of it the same strategy
  text every time, which vLLM's prefix cache reuses. Games are configured for a 32k context.
- **Decisions per game:** ~30–100 for a typical Gold Stake loss, ~200–250 for a full 8-ante game.
- **Throughput (estimate):** roughly 1,000–2,500 games in 12 hours with a 7–8B model and 4–8 games in
  parallel. The pilot measures the real number.
- **Description text:** on a machine without Balatro, games run on jackdaw, whose game state has card
  and joker names but not their effect text. That makes the task harder for a small model. Options:
  run on a machine with Balatro installed (the exact engine gives the real text), or add effect text
  generated locally from your own copy of the game (it can't be committed to the repo).

## Teachers for fine-tuning (no API model)
- **Play and discard decisions:** labeled by the search player (free, CPU).
- **Shop, pack and skip decisions:** labeled by search guided by the judgment score, once it exists.
- **Reasoning text:** comes from the model itself; keep its attempts whose actions match what the
  teachers chose or that led to good outcomes.
- Export with `scripts/export_sft.py`; games are filtered by ante reached and wins.

## Fine-tuning on one 24 GB GPU (rough)
- **LoRA SFT on a 7–8B model:** fits comfortably; QLoRA (4-bit base) for ~14B.
- **Data size:** start with 20k–50k decisions. At ~5k tokens each that's 100M–250M tokens per epoch,
  on the order of a day or two of training on a 4090 (to be measured).
- **DPO** on "better vs worse action in the same spot" pairs: similar cost to SFT.
- **Per-decision GRPO:** possible with a 7–8B model and LoRA, but generation and training share the
  24 GB, so groups stay small and runs take days.

## Order of operations
1. Dry run: `MOCK=1 bash scripts/gpu/run_on_gpu.sh` (no GPU), already passing.
2. GPU pilot: `N=10 bash scripts/gpu/run_on_gpu.sh`. Measure games per hour, tokens and seconds per
   decision, and how often tool calls are valid.
3. Baseline on dev seeds, both arms, with n chosen from the measured games per hour; held-out seeds once
   the setup is frozen.
4. Judgment score, then the search + PPO bot.
5. Fine-tune the LLM on teacher labels (SFT, then DPO), evaluate on the same seeds; GRPO if it helps.
