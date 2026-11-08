# GPU Quote Parser

GPU rental offers arrive as messy free text: one quotes per GPU-hour, another per node-hour, and terms are written a dozen ways. This tool turns those offers into a clean table priced in $/GPU-hour, flags unusual prices, and measures how accurately the LLM reads them.

## How it works

`parser.py` runs five steps:

1. **Parse:** sends each offer to `claude-haiku-5-5` and gets back fields that match a fixed Pydantic schema. Anything not stated comes back null.
2. **Normalize:** converts every price to $/GPU-hour and works out total GPUs (for example, "16 nodes of 8xH200" = 128). It assumes 8 GPUs per node unless the offer says otherwise.
3. **Outlier flag:** computes the median $/GPU-hour for each GPU model and flags offers more than 25% above or below it.
4. **Output:** prints a table and saves `results.csv`.
5. **Accuracy check:** compares GPU model, GPU count, price and term against `answers.csv`, prints a score, and lists each miss.

The model only reads the offers; all arithmetic happens in Python. A wrong number is therefore either a misread, visible in the parsed fields, or a code bug. If the price unit is unknown, the normalized price stays blank rather than being guessed.

## How to run

Requires Python 3.14, [uv](https://docs.astral.sh/uv/) and an Anthropic API key.

```bash
export ANTHROPIC_API_KEY=your-key
uv run python parser.py
```

Inputs:
- `quotes.txt`: one offer per line
- `answers.csv`: the correct values for each line

## Sample output

```text
Parsing 16 offers with claude-haiku-5-5...

Line  Model  GPUs  $/GPU-hr  Term            vs median  Flag
-------------------------------------------------------------
   1  H100      8      2.15  6 months              -5%  
   2  H100     64      2.15  month-to-month        -5%  
   3  A100     16      1.10  12 months             +0%  
   4  H200    256      3.60  12 months            -27%  LOW
   5  H100      1      2.49  on-demand             +9%  
   6  H100     32      4.20  3 months             +85%  HIGH
   7  B200      8      8.00  12 months             +4%  
   8  A100      8      0.95  spot                 -14%  
   9  H100    128      0.89  24 months            -61%  LOW
  10  H100      8      9.80  on-demand           +331%  HIGH
  11  H200    128      5.20  6 months              +5%  
  12  A100     64      1.65  month-to-month       +50%  HIGH
  13  H100      8      2.40  12 months             +5%  
  14  H100    512      2.05  18 months            -10%  
  15  H200      8      4.95  on-demand             +0%  
  16  B200     64      7.40  6 months              -4%  

Accuracy
  gpu_model         16/16
  gpu_count         16/16
  price_per_gpu_hr  16/16
  term              16/16
  No misses.

Saved 16 rows to results.csv
```

## Accuracy results

| Run | GPU model | GPU count | Price | Term | Total |
|---|---|---|---|---|---|
| First run, before any fixes | 16/16 | 15/16 | 16/16 | 16/16 | 63/64 |
| After the fix (same result on 3 runs) | 16/16 | 16/16 | 16/16 | 16/16 | 64/64 |

**The one miss:** line 7 says `B200 8-GPU node`. The model correctly read 8 GPUs per node and left the node count blank, but `normalize` had no rule for a quote with neither a node count nor a GPU count. I added one general rule in Python: treat that as a single node. The prompt did not change.

**How to read the 64/64:** the fix was made after seeing a miss on these same 16 lines, so the final score is not an independent test. The held-out result below is the better guide to how it handles new formats.

## Held-out result

I wrote 5 new offers in formats `quotes.txt` doesn't contain, with their answers fixed before any run: a broker email, a monthly price, key=value fields, trader shorthand and a whole-cluster price. They ran with the prompt unchanged, and nothing was fixed afterwards.

| GPU model | GPU count | Price | Term | Total fields | Offers fully correct |
|---|---|---|---|---|---|
| 5/5 | 5/5 | 3/5 | 3/5 | **16/20** | **1/5** |

The score was the same on all 3 runs.

| Line | Format | Miss | Cause |
|---|---|---|---|
| 1 | Broker email | Term stayed `six-month contract` | `normalize_term` only understands digits, not number words |
| 2 | `$1,752/GPU/month` | Price blank | The schema has no per-month unit |
| 4 | Trader shorthand | Term stayed `9m` | `normalize_term` doesn't recognise `m` for months |
| 5 | `$512/hr` for a whole 128-GPU pod | Price blank | The schema has no whole-cluster unit |

All four misses are gaps in the code or schema rather than misreads: the model extracted the right number or phrase each time. The two price misses came out blank, not wrong, so no offer got a bad price. They would still need a person to resolve them.

**How to read the 16/20:** this is 5 synthetic offers that I wrote knowing how the parser works, and they use deliberately new formats. It shows where the parser breaks, but it is not a general accuracy estimate.

To reproduce it:

```bash
uv run python parser.py heldout_quotes.txt heldout_answers.csv heldout_results.csv
```

## Limitations

- **Synthetic data:** the offers and prices are made up, though realistic, and 16 lines is a small sample.
- **The flag ignores contract length:** short and on-demand terms naturally price higher. Line 10 (on-demand, +331%) and line 9 (2-year commit, -61%) may be fair prices for their terms rather than anomalies.
- **The flag ignores GPU variants:** A100 40GB and 80GB, or H100 SXM and PCIe, share one median because offers are grouped by family.
- **The flag is weak with few offers:** with two offers, as for B200, both sit the same distance from the median, so either both are flagged or neither is. The median also includes the offer being tested.
- **The 8-GPU default is an assumption:** a per-node price on a differently sized node would be wrong if the offer doesn't state the size.
- **Only two price units are supported:** per-month and whole-cluster prices come out blank (see the held-out result).
- **Fixed GPU list:** the schema only allows A100, H100, H200 and B200, so newer or AMD GPUs (B300, MI300X) can't be labeled correctly.
- **Assumes USD:** prices in other currencies would be read as dollars without a warning.
- **Ignores what's included:** some offers are all-in and others exclude storage, networking or support, so equal $/GPU-hour prices aren't always equal deals.

## Next steps

- Fix the held-out gaps: add monthly and whole-cluster price units, and handle terms like `six-month` and `9m`. Then write a fresh held-out set, since these 5 would no longer be unseen.
- Test on real offers to get an independent accuracy number.
- Compare each offer against others with a similar term, not just the same GPU model.
- Parse GPU variants (memory size, SXM or PCIe) and compare within them.
- Send offers the model can't read confidently, or with missing prices, to a person for review.
- Add a currency field and an "unknown model" option, so unfamiliar offers get flagged for review instead of mislabeled.
