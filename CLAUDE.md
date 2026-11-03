# GPU Quote Parser

## Purpose
A small portfolio project for an application to Stoa's Compute Markets desk (an AI compute/GPU trading desk). It turns messy, free-text GPU rental offers into a clean comparable table, flags unusual prices, and measures how accurately the LLM parsed them. I need to understand and explain every part of this code in an interview, so explain your plan before writing code and keep things simple.

## Setup
- Python 3.14, managed with uv. Run code with `uv run python parser.py`. Add packages with `uv add`.
- Anthropic API key is in the `ANTHROPIC_API_KEY` environment variable. Never write the key into any file.
- Use a cheap, fast model (e.g. `claude-haiku-5-5`) for parsing.

## Inputs
- `quotes.txt`: one GPU offer per line (synthetic, realistic prices).
- `answers.csv`: correct values per line — `line,gpu_model,gpu_count,price_per_gpu_hr,term`.

## What to build (in this order)
1. **Parse:** send each offer to the Anthropic API and get structured JSON back: gpu_model (family: A100/H100/H200/B200), gpu_count, price, price_unit (per GPU-hour or per node-hour), term, interconnect, availability/lead time, location. Handle missing fields as null.
2. **Normalize:** convert every price to $/GPU-hour. Assume 8 GPUs per node unless stated. Compute total GPU count correctly (e.g. "16 nodes of 8xH200" = 128).
3. **Accuracy check:** compare parsed gpu_model, gpu_count, normalized price and term against `answers.csv`; print a score (e.g. 15/16 prices correct) and list each miss with what went wrong.
4. **Outlier flag:** per GPU model, compute the median $/GPU-hour and flag offers more than 25% above or below it.
5. **Output:** print a clean table to the terminal and save `results.csv`.
6. **README:** what it does, how to run it, sample output, accuracy results, limitations (synthetic data; flag ignores contract length — short/on-demand terms naturally price higher), and next steps.

## Style
- One main script (`parser.py`), small readable functions, short comments.
- Don't over-engineer: no web frameworks, databases or extra abstractions.
- Time budget is tight (~6 hours total), so prioritize a working core over extras.
