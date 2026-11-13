# Compute plan

What each stage needs, with estimates to replace with pilot measurements. Prices are third-party
reports for Kimi K3.1 Pro (Nov 2026): $2 per 1M input tokens, $10 per 1M output, $0.20 per 1M cached
input. Check Moonshot's page first.

## Needs at a glance
| Stage | Needs | Status |
|---|---|---|
| Engine, jackdaw, validation, baselines, search bot | laptop CPU | done |
| LLM pipeline (mock, with and without tools) | laptop | done: 4 parallel games end to end, logs, export |
| RL pipeline (jackdaw's MaskablePPO example) | laptop CPU | done: smoke test on Black/Gold, ~140k steps/min on 2+2 threads |
| Kimi pilot and baseline | Moonshot API key + budget | waiting on key |
| Distillation data | API budget (or the search bot as a free teacher for play/discard) | after pilot |
| Fine-tuning (SFT/LoRA) | GPU | waiting on GPU |
| RL on the student model | GPU for generation; jackdaw on CPU for the game | later |

## Kimi cost per game (estimate; the pilot replaces this)
- **Decisions per game.** BalatroLLM asks the model about hands, shops and packs; blind select and
  cash-out are automatic. At Gold Stake most games end at ante 1–3, around 30–100 decisions. A full
  8-ante win is around 200–250.
- **Tokens per decision (measured).** BalatroLLM's default prompt is about 12.8k input tokens
  (chars/4 estimate from the mock run):
  - about 10.5k (84%) is strategy text, identical on every call, so a provider's prefix cache can
    serve it;
  - about 1.2k is game state;
  - about 0.7k is tool schemas.

  Output is about 1–3k tokens, mostly reasoning (estimate).
- **Cost per decision.** About $0.006 input if the strategy text is cached (about $0.026 if not), plus
  about $0.01–0.03 output, so roughly $0.016–0.056. The pilot must check whether Moonshot caches.
- **Cost per game:**
  - **no tools**: about $1–4 for a typical loss, $4–10 for a full-length game;
  - **with tools**: roughly 1.5–2× that, since each tool call is another request carrying the full
    context.
- **Pilot budget.** 10 games per arm is roughly $20–80 in total.

**Precision vs cost:** at ~$3 per game, 400 games per arm (±2 pts on win rate) costs about $1.2k per
arm. Use mean ante as the primary signal while win rates are near 0.

## Distillation data
- Target: 50k–200k decisions for a first SFT run. That's about 500–2,000 Kimi games, or $2k–8k.
- **Cheaper mix:** label play/discard decisions with the search bot (free, CPU). Spend Kimi tokens
  only on shop, pack and joker decisions, where the search bot has no edge.
- Export: `scripts/export_sft.py`, filtered by final ante or wins and weighted by ante.

## GPU (rough)
- **Student model:** an 8B-class open instruct model with tool calling first; 32B if 8B plateaus.
- **SFT tokens:** 100k decisions × ~5k tokens is about 500M tokens per epoch.
- **LoRA SFT on 8B:**
  - 1× H100 80GB at ~3k tokens/s is about 45 h per epoch;
  - 8× H100 is about 6 h.
- **32B:** roughly 4× the GPU-hours.
- **Suggested start:**
  - 1–2× A100/H100 80GB for LoRA on 8B with a 20–50k-decision subset (about 10–25 GPU-h);
  - then scale with what the first fine-tune shows.

## Order of operations once resources arrive
1. `scripts/run_llm.py --mock` (already passing) as a final check, then set `MOONSHOT_API_KEY`.
2. Kimi pilot, 10 dev seeds × 2 arms, `--max-cost` set; fill in real cost per game here.
3. Baseline on dev (n from cost and EVAL_PROTOCOL.md), then heldout.
4. Generate distillation data (Kimi plus search-bot labels), export, LoRA SFT on GPU, evaluate the
   student on the same seeds.
