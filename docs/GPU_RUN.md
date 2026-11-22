# Running the bot on your GPU (instructions for the person running it)

> For the baseline, the preferred setup runs the games on the real game on another computer and only the
> model here: see [REMOTE_GPU.md](REMOTE_GPU.md). This page runs everything here on the jackdaw
> simulator, whose prompts lack the card text.

Thanks for lending your GPU. One script does everything: it installs what it needs, runs an AI model
on your GPU, has it play Balatro in a simulator, and packs the results into one file for you to send
back. You don't need Balatro or any account. Nothing is installed system-wide except `uv` (a Python
tool, in `~/.local/bin`); everything else stays inside the project folder.

## What you need
- Linux with an NVIDIA GPU and its driver (`nvidia-smi` should work). On Windows, use WSL2 with
  Ubuntu; the NVIDIA driver for WSL comes with the normal Windows driver.
- About 40 GB of free disk space and an internet connection (it downloads the model, ~15 GB).
- `git` and `curl`.

## Steps
1. Get the project:
   ```bash
   git clone https://github.com/RealMyth21/balatro-bot.git
   ```
   ```bash
   cd balatro-bot
   ```
2. Run it (the first time takes a while: installs, then downloads the model):
   ```bash
   bash scripts/gpu/run_on_gpu.sh
   ```
3. When it finishes it prints `DONE. Send this file back: results/balatro-results-....tar.gz`.
   Send that file. If something fails, it still writes that file with the error logs inside, so send
   it anyway.

Leave the terminal open while it runs. With the defaults (2 × 20 games, 4 at a time, the model
thinking before each move) expect several hours; `THINKING=0` is much faster. The GPU is busy the whole
time.

## Settings (optional)
Put them before the command, e.g. `N=50 PARALLEL=6 bash scripts/gpu/run_on_gpu.sh`.

| Setting | Default | Meaning |
|---|---|---|
| `N` | 20 | games per configuration |
| `PARALLEL` | 4 | games at once (lower it if you run out of GPU memory) |
| `MODEL` | `Qwen/Qwen3-8B` | model from Hugging Face; it must support tool calling |
| `TOOL_PARSER` | `hermes` | vLLM's tool-call parser for that model family |
| `REASONING_PARSER` | `qwen3` | separates the model's thinking from its answer; empty for models that don't think |
| `THINKING` | 1 | `THINKING=0`: Qwen3 answers without thinking first (faster, usually weaker) |
| `VLLM_ARGS` | (none) | extra flags for the model server, e.g. `VLLM_ARGS="--enforce-eager"` |
| `VLLM_SPEC` | `vllm` | which vLLM to install; pin a version that worked, e.g. `VLLM_SPEC="vllm==0.11.0"` |
| `ARMS` | `notools tools` | configurations to play: game actions only, and with the score preview tool |
| `SPLIT` | `dev` | which fixed list of game seeds to use |
| `GPU_MEM` | `0.90` | share of GPU memory the model server may use |
| `MOCK` | 0 | `MOCK=1`: test the whole flow with a fake model, no GPU needed |

## If something goes wrong
- **"No NVIDIA GPU found":** the driver isn't installed or visible (`nvidia-smi` must work).
- **vLLM fails at startup with a CUDA or driver error:** recent vLLM needs a recent NVIDIA driver; update
  the driver, or pin an older vLLM with `VLLM_SPEC`.
- **Out of memory:** run again with `PARALLEL=2` or `GPU_MEM=0.85`.
- **vLLM did not come up:** the script shows the last lines of `runtime/gpu_logs/<time>/vllm.log`.
  Send the results file; those logs are in it. If the log complains about `--reasoning-parser`, your
  vLLM version names it differently: run with `REASONING_PARSER=` and send the log.
- **Stopped midway:** run the same command again. Games already finished are skipped.

## What's in the results file
Per-game results (ante reached, won or not), the model's prompts, replies and tool calls for every
decision, a fine-tuning dataset built from them, the model server's log, and the GPU name. No personal
information is collected.
