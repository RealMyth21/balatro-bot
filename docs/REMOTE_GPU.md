# Baseline with a remote GPU (model there, real game here)

The model (Qwen3-8B) runs with vLLM on a Linux machine with an RTX 4090. The games run on this Mac on
the exact engine: the real Balatro code, with the real card and blind text in the prompt. The Mac sends
each decision to the GPU machine over a private connection. Nothing from the game is installed on the
GPU machine, and the model server is never open to the internet.

```
  Mac: exact engine games ── scripts/run_baseline.sh ──(SSH tunnel or Tailscale)──> GPU: vLLM + Qwen3-8B
```

The baseline uses BalatroLLM's default prompt, the game actions, one analysis tool while playing a
hand (`score_plays`, the score preview: minimum and maximum score and average money of the best plays),
Qwen3 with thinking on, Qwen's recommended sampling (temperature 0.6, top_p 0.95, top_k 20), and at most 8192
output tokens per decision (thinking included).

## Before you start
- **Permission.** If the GPU machine belongs to a lab, make sure you're allowed to run multi-hour GPU
  jobs on it and to connect to it remotely. The server script refuses to start if the GPU is already in
  use by others (`FORCE=1` overrides that).
- **Disk.** ~30 GB free in your home folder on the GPU machine (vLLM ~6 GB, the model ~16 GB).

## 1. Start the model server (on the GPU machine, through TeamViewer)
1. Connect with TeamViewer (type the password yourself; refresh it afterwards on the GPU machine's
   TeamViewer window with ↻).
2. Open a terminal there and get the script (one file; nothing else from the project is needed):
   ```bash
   curl -fsSLO https://raw.githubusercontent.com/RealMyth21/balatro-bot/main/scripts/gpu/serve_model.sh
   ```
   (or copy `scripts/gpu/serve_model.sh` with TeamViewer's file transfer).
3. Start it. For an SSH tunnel (step 3a):
   ```bash
   bash serve_model.sh
   ```
   For Tailscale (step 3b) instead:
   ```bash
   BIND=tailscale bash serve_model.sh
   ```
   The first start installs vLLM and downloads the model (a while). It's ready when the log shows
   `Application startup complete`. Leave the window open; `Ctrl+C` stops it.

## 2. Copy the key to the Mac
The server only answers requests carrying a random key it created on its first run. On the GPU machine:
```bash
cat ~/.config/balatro-bot/vllm_key
```
Copy it (TeamViewer shares the clipboard), then on the Mac:
```bash
mkdir -p ~/.config/balatro-bot && pbpaste | tr -d ' \n' > ~/.config/balatro-bot/gpu_key && chmod 600 ~/.config/balatro-bot/gpu_key
```
The key stays out of the project folder, so it can't end up in git.

## 3. Connect the Mac to the server (pick one)
**a. SSH tunnel** — if the Mac can SSH into the GPU machine (e.g. on the lab's network or VPN; test
with `ssh <user>@<host>`). Keep this running in its own terminal on the Mac:
```bash
ssh -N -L 8000:127.0.0.1:8000 <user>@<host>
```
The server is then at `http://127.0.0.1:8000/v1` on the Mac, the script's default.

**b. Tailscale** — if SSH isn't reachable. Install Tailscale on both machines (on Linux it needs sudo:
check that's allowed on a lab machine), log both into your own Tailscale account, start the server with
`BIND=tailscale` (step 1), and find the GPU machine's address with `tailscale ip -4` there. Then on the
Mac add `BASE_URL=http://<that address>:8000/v1` in front of the commands in step 4. Traffic between
your devices is encrypted; nobody else can reach the server.

## 4. Play (on the Mac)
Check the connection and the model (sends one game-sized request; prints the speed):
```bash
uv run python scripts/check_model_server.py
```
One baseline game:
```bash
bash scripts/run_baseline.sh
```
More games, several at once (the check above tells you the speed; 4 at a time suits a 4090):
```bash
N=20 PARALLEL=4 bash scripts/run_baseline.sh
```
Watch any game, live or finished:
```bash
uv run python scripts/view_record.py
```
Rerunning skips finished games and retries games stopped by a connection or server problem, so a
dropped tunnel only costs the games in progress. Keep the Mac awake while it plays.

Use `SPLIT=heldout` only for final numbers, once nothing about the setup will change any more.

## 5. When you're done
Stop the server on the GPU machine with `Ctrl+C`, close the tunnel, and refresh the TeamViewer password.

## If something goes wrong
| Problem | What to do |
|---|---|
| `cannot reach it` | Server not ready yet, tunnel not running, or wrong `BASE_URL`. |
| `401` / unauthorized | The key on the Mac differs from `~/.config/balatro-bot/vllm_key` on the GPU machine. |
| `prompt is too long` | Start the server with `MAX_LEN=32768 bash serve_model.sh`. |
| `no tool call came back` | The server needs `--tool-call-parser hermes` (the script's default). |
| thinking mixed into the answer | The server needs `--reasoning-parser qwen3` (the default); an old vLLM may name it differently: pin `VLLM_SPEC="vllm==0.11.0"`. |
| out of GPU memory at start | Something else uses the GPU, or try `GPU_MEM=0.85`. |
| slow decisions / timeouts | Lower `PARALLEL`; BalatroLLM gives each model call 240 s. |

## Settings
GPU machine (`serve_model.sh`): `MODEL` (Qwen/Qwen3-8B), `MAX_LEN` (24576), `GPU_MEM` (0.90), `PORT`
(8000), `BIND` (127.0.0.1 or tailscale), `VLLM_SPEC`, `VLLM_ARGS`, `FORCE`.

Mac (`run_baseline.sh`): `MODEL`, `BASE_URL`, `THINKING` (1), `MAX_TOKENS` (8192), `N` (1), `PARALLEL`
(1), `SPLIT` (dev), `LABEL`, `MOCK=1` for a dry run with a fake model, and `TOOLS`:

| `TOOLS` | The model is offered |
|---|---|
| `baseline` (default) | `score_plays`: minimum and maximum score, average money of the best plays |
| `none` | game actions only |
| a comma list, e.g. `score_plays,optimize_jokers,optimize_hand` | exactly those tools |
| `all` | all four, including `estimate_clear_chance` (a search: report it as LLM + search) |

Each setting gets its own label (e.g. `qwen3-8b-baseline`, `qwen3-8b-notools`), so results never mix.
Game records keep every tool's full answer for the viewer whatever the model was offered.

If running everything on the GPU machine instead (simulator games, no Balatro needed), see
[GPU_RUN.md](GPU_RUN.md); those games use jackdaw, whose prompts lack the card text.
