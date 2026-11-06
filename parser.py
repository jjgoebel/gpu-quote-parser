"""Turn free-text GPU rental offers into a clean, comparable table.

The LLM only reads each offer into fields. All maths happens in Python, so a
wrong number is either a misread (visible in the parsed fields) or a code bug.
"""

import csv
import re
import statistics
import sys
from typing import Literal

import anthropic
from pydantic import BaseModel

MODEL = "claude-haiku-5-5"
QUOTES_FILE = "quotes.txt"
ANSWERS_FILE = "answers.csv"
RESULTS_FILE = "results.csv"
DEFAULT_GPUS_PER_NODE = 8
OUTLIER_THRESHOLD = 0.25  # flag offers more than 25% from the model's median
PRICE_TOLERANCE = 0.005  # half a cent, to absorb rounding

SYSTEM_PROMPT = """You extract fields from one GPU rental offer.
Copy what the offer says. Do not calculate anything. Use null for anything not stated.

- gpu_model: the GPU family only (A100, H100, H200 or B200)
- gpu_count: the number of GPUs stated
- node_count: the number of nodes, only if the offer states it
- gpus_per_node: GPUs in each node, only if the offer states it
- price: the number only
- price_unit: gpu_hour if the price is per GPU, node_hour if the price is per node
- term: the contract length or type, as written
- interconnect: for example InfiniBand or NVLink
- availability: when the GPUs are available
- location: where the GPUs are"""


class Quote(BaseModel):
    """The fields we ask the model to fill in. The API enforces this shape."""

    gpu_model: Literal["A100", "H100", "H200", "B200"] | None
    gpu_count: int | None
    node_count: int | None
    gpus_per_node: int | None
    price: float | None
    price_unit: Literal["gpu_hour", "node_hour"] | None
    term: str | None
    interconnect: str | None
    availability: str | None
    location: str | None


# --- Step 1: parse ---


def parse_quote(client, text):
    """Send one offer to the API. Returns a Quote, with all fields null on failure."""
    try:
        response = client.messages.parse(
            model=MODEL,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": text}],
            output_format=Quote,
        )
        if response.parsed_output is not None:
            return response.parsed_output
        print(f"  No result for: {text} (stop reason: {response.stop_reason})")
    except (anthropic.APIError, ValueError) as error:
        # One bad line shouldn't lose the whole run; it shows up as a miss.
        print(f"  Failed on: {text} ({error})")
    return Quote(**dict.fromkeys(Quote.model_fields))


# --- Step 2: normalize ---


def normalize_term(term):
    """Convert a term to the answer key's format, e.g. '1yr' -> '12 months'."""
    if term is None:
        return None
    text = term.lower()
    if "spot" in text:
        return "spot"
    if "month-to-month" in text or "month to month" in text:
        return "month-to-month"
    if "on-demand" in text or "on demand" in text or "no commit" in text:
        return "on-demand"
    match = re.search(r"(\d+)\s*-?\s*(yr|year|mo)", text)
    if match:
        length = int(match.group(1))
        months = length * 12 if match.group(2) in ("yr", "year") else length
        return f"{months} months"
    return term  # keep unrecognised terms as written so they show up as misses


def normalize(line_number, text, quote):
    """Build one clean row: total GPUs, $/GPU-hour and a standard term."""
    gpus_per_node = quote.gpus_per_node or DEFAULT_GPUS_PER_NODE

    # "16 nodes of 8xH200" gives a node count; otherwise use the stated GPU count.
    # "8-GPU node" gives neither, which means one node.
    if quote.node_count:
        gpu_count = quote.node_count * gpus_per_node
    elif quote.gpu_count is not None:
        gpu_count = quote.gpu_count
    else:
        gpu_count = quote.gpus_per_node

    # An unknown unit stays blank rather than being guessed.
    if quote.price is None or quote.price_unit is None:
        price = None
    elif quote.price_unit == "node_hour":
        price = quote.price / gpus_per_node
    else:
        price = quote.price

    return {
        "line": line_number,
        "gpu_model": quote.gpu_model,
        "gpu_count": gpu_count,
        "price_per_gpu_hr": price,
        "term": normalize_term(quote.term),
        "interconnect": quote.interconnect,
        "availability": quote.availability,
        "location": quote.location,
        "offer": text,
        "parsed": quote,  # kept to explain misses; not written to the CSV
    }


# --- Step 3: accuracy check ---


def load_answers(answers_file):
    """Read an answers CSV into {line number: expected values}."""
    with open(answers_file, newline="") as file:
        return {
            int(row["line"]): {
                "gpu_model": row["gpu_model"],
                "gpu_count": int(row["gpu_count"]),
                "price_per_gpu_hr": float(row["price_per_gpu_hr"]),
                "term": row["term"],
            }
            for row in csv.DictReader(file)
        }


def is_correct(field, got, expected):
    """Exact match, except prices, which allow for rounding."""
    if field == "price_per_gpu_hr" and got is not None:
        return abs(got - expected) <= PRICE_TOLERANCE
    return got == expected


def check_accuracy(rows, answers):
    """Print a score per field, then list every miss with what the model read."""
    fields = ["gpu_model", "gpu_count", "price_per_gpu_hr", "term"]
    misses = []
    print("\nAccuracy")
    for field in fields:
        correct = 0
        for row in rows:
            expected = answers[row["line"]][field]
            if is_correct(field, row[field], expected):
                correct += 1
            else:
                misses.append((row, field, expected))
        print(f"  {field:<17} {correct}/{len(rows)}")

    if not misses:
        print("  No misses.")
        return
    print(f"\nMisses ({len(misses)})")
    for row, field, expected in misses:
        print(f"  Line {row['line']}: {field} expected {expected}, got {row[field]}")
        print(f"    Offer:  {row['offer']}")
        print(f"    Parsed: {row['parsed'].model_dump(exclude_none=True)}")


# --- Step 4: outlier flag ---


def flag_outliers(rows):
    """Add each row's median, % difference and HIGH/LOW flag, per GPU model."""
    prices_by_model = {}
    for row in rows:
        if row["gpu_model"] and row["price_per_gpu_hr"] is not None:
            prices_by_model.setdefault(row["gpu_model"], []).append(row["price_per_gpu_hr"])
    medians = {model: statistics.median(prices) for model, prices in prices_by_model.items()}

    for row in rows:
        row["model_median"] = None
        row["pct_from_median"] = None
        row["flag"] = ""
        median = medians.get(row["gpu_model"])
        if median is None or row["price_per_gpu_hr"] is None:
            continue
        difference = (row["price_per_gpu_hr"] - median) / median
        row["model_median"] = median
        row["pct_from_median"] = difference * 100
        if difference > OUTLIER_THRESHOLD:
            row["flag"] = "HIGH"
        elif difference < -OUTLIER_THRESHOLD:
            row["flag"] = "LOW"


# --- Step 5: output ---


def blank_if_none(value):
    return "" if value is None else value


def print_table(rows):
    print(
        f"\n{'Line':>4}  {'Model':<5}  {'GPUs':>4}  {'$/GPU-hr':>8}  "
        f"{'Term':<14}  {'vs median':>9}  Flag"
    )
    print("-" * 61)
    for row in rows:
        price = row["price_per_gpu_hr"]
        price_text = "" if price is None else f"{price:.2f}"
        pct = row["pct_from_median"]
        pct_text = "" if pct is None else f"{pct:+.0f}%"
        print(
            f"{row['line']:>4}  {blank_if_none(row['gpu_model']):<5}  "
            f"{blank_if_none(row['gpu_count']):>4}  {price_text:>8}  "
            f"{blank_if_none(row['term']):<14}  {pct_text:>9}  {row['flag']}"
        )


def save_csv(rows, results_file):
    columns = [
        "line", "gpu_model", "gpu_count", "price_per_gpu_hr", "term",
        "interconnect", "availability", "location",
        "model_median", "pct_from_median", "flag", "offer",
    ]
    with open(results_file, "w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nSaved {len(rows)} rows to {results_file}")


def main():
    # Optional arguments: quotes file, answers file, results file.
    files = sys.argv[1:] or [QUOTES_FILE, ANSWERS_FILE, RESULTS_FILE]
    quotes_file, answers_file, results_file = files

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from the environment
    with open(quotes_file) as file:
        offers = [line.strip() for line in file]

    print(f"Parsing {len(offers)} offers with {MODEL}...")
    rows = [
        normalize(number, text, parse_quote(client, text))
        for number, text in enumerate(offers, start=1)
    ]

    flag_outliers(rows)
    print_table(rows)
    check_accuracy(rows, load_answers(answers_file))
    save_csv(rows, results_file)


if __name__ == "__main__":
    main()
