"""Build data/trials.csv from the Assignment 2 study dataset + saved model outputs.

Run once from the sarcasm-study folder:
    python scripts/build_trials.py [path/to/study_dataset__openai__gpt-6-luna.csv]

The saved AI outputs are kept as a fallback for when a live model call fails.
"""

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SRC = ROOT.parent.parent / "Assignment 2" / "outputs" / "study_dataset__openai__gpt-6-luna.csv"
OUT = ROOT / "data" / "trials.csv"
COLUMNS = ["id", "tweet", "sarcastic", "ai_label", "ai_confidence", "ai_explanation"]

src = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SRC
with open(src, newline="") as f:
    rows = list(csv.DictReader(f))

with open(OUT, "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=COLUMNS)
    writer.writeheader()
    for r in rows:
        writer.writerow({
            "id": r["id"],
            "tweet": r["tweet"],
            "sarcastic": r["sarcastic"],
            "ai_label": r["label"],
            "ai_confidence": r["confidence"],
            "ai_explanation": r["explanation"],
        })

print(f"wrote {len(rows)} trials to {OUT}")
