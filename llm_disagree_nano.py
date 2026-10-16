"""Find a prompt where gpt-5-nano answers YES and gpt-5-mini answers NO (same trial, >=2 of 3)."""
import os
import re
import sys
import concurrent.futures as cf
from pathlib import Path

import requests


def load_token():
    if os.environ.get("AIPIPE_TOKEN"):
        return os.environ["AIPIPE_TOKEN"]
    env = Path(__file__).with_name(".env")
    for line in env.read_text().splitlines():
        line = line.strip()
        if line.startswith("AIPIPE_TOKEN="):
            return line.split("=", 1)[1].strip().strip("'\"")
        if line.startswith("eyJ"):
            return line
    raise SystemExit("No AI Pipe token found")


TOKEN = load_token()
URL = "https://aipipe.org/openai/v1/chat/completions"
NANO, MINI = "gpt-5-nano-2025-08-07", "gpt-5-mini-2025-08-07"
TRIALS = int(os.environ.get("TRIALS", 5))


def ask(model, prompt):
    r = requests.post(URL, headers={"Authorization": f"Bearer {TOKEN}"},
                      json={"model": model, "messages": [{"role": "user", "content": prompt}],
                            "reasoning_effort": "minimal", "max_completion_tokens": 2048}, timeout=90)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"] or ""


def norm(reply):
    s = reply.strip().upper()
    return s[:-1] if s[-1:] in ".!" else s


def check(prompt):
    jobs = [m for _ in range(TRIALS) for m in (NANO, MINI)]
    with cf.ThreadPoolExecutor(len(jobs)) as ex:
        replies = list(ex.map(lambda m: ask(m, prompt), jobs))
    trials = [(norm(replies[i]), norm(replies[i + 1])) for i in range(0, len(replies), 2)]
    wins = sum(n == "YES" and m == "NO" for n, m in trials)
    print(f"{wins}/{TRIALS} {'PASS' if wins * 3 >= TRIALS * 2 else 'fail'} | {prompt[:110]!r}")
    print("   " + "  ".join(f"{n[:6]}/{m[:6]}" for n, m in trials))
    return wins


if __name__ == "__main__":
    prompts = sys.argv[1:] or [p for p in Path(os.environ["PROMPTS"]).read_text().split("\n---\n") if p.strip()]
    for p in prompts:
        check(p.strip())
