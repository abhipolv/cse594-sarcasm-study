"""Live AI assistance: ask the model whether a tweet is sarcastic (via OpenRouter).

Same prompt, model, and settings as Assignment 2 (scripts/model.py there), so live
outputs are comparable to the saved ones. Standard library only.
"""

import json
import os
import re
import time
import urllib.request

API_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = os.environ.get("AI_MODEL", "openai/gpt-6-luna")
TIMEOUT_S = float(os.environ.get("AI_TIMEOUT_S", "8"))  # per attempt; main.py retries once, then uses the saved output

SYSTEM_PROMPT = """You help people judge whether a tweet is sarcastic.

A tweet is sarcastic if its author means something different from (often the
opposite of) what the words literally say, e.g. to mock, criticize, or joke.
Irony, satire, understatement, overstatement, and rhetorical questions count
as sarcasm when the author does not mean the literal statement.

Respond with ONLY a JSON object, no other text:
{"label": "sarcastic" or "not_sarcastic",
 "confidence": integer 50-100 (how sure you are of the label),
 "explanation": one or two short sentences citing the cues you used}"""


def classify(tweet: str) -> dict:
    """Return {label, confidence, explanation, latency_s, raw}. Raises on any failure."""
    api_key = os.environ["OPENROUTER_API_KEY"]
    body = json.dumps({
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Tweet: {tweet}"},
        ],
        "temperature": 0,
        "max_tokens": 4000,
    }).encode()
    req = urllib.request.Request(API_URL, data=body, headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-Title": "CSE594 A3 sarcasm study",
    })
    start = time.time()
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        payload = json.load(resp)
    latency = time.time() - start

    raw = payload["choices"][0]["message"]["content"] or ""
    match = re.search(r"\{.*\}", raw, re.DOTALL)  # tolerate ```json fences
    data = json.loads(match.group(0))
    label = str(data["label"]).strip().lower()
    if label not in ("sarcastic", "not_sarcastic"):
        raise ValueError(f"unexpected label {label!r}")
    return {
        "label": label,
        "confidence": int(data["confidence"]),
        "explanation": str(data["explanation"]).strip(),
        "latency_s": round(latency, 2),
        "raw": raw,
    }
