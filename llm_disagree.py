"""Find a <=100-char prompt where gpt-5.6-luna says Yes and gpt-5.4-mini says No (case-sensitive)."""
import os
import re
import sys
import concurrent.futures as cf

import requests

TOKEN = os.environ["AIPIPE_TOKEN"]
URL = "https://aipipe.org/openai/v1/chat/completions"
MODELS = {"gpt-5.6-luna": "Yes", "gpt-5.4-mini": "No"}
RUNS = 10


def ask(model, prompt):
    r = requests.post(URL, headers={"Authorization": f"Bearer {TOKEN}"},
                      json={"model": model, "messages": [{"role": "user", "content": prompt}]}, timeout=90)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"] or ""


def passes(reply, want):
    other = "No" if want == "Yes" else "Yes"
    has = lambda w: re.search(rf"\b{w}\b", reply) is not None
    return has(want) and not has(other)


def check(prompt):
    assert len(prompt) <= 100, f"{len(prompt)} chars"
    jobs = [(m, want) for m, want in MODELS.items() for _ in range(RUNS)]
    with cf.ThreadPoolExecutor(len(jobs)) as ex:
        replies = list(ex.map(lambda j: ask(j[0], prompt), jobs))
    ok = all(passes(rep, want) for (m, want), rep in zip(jobs, replies))
    print(f"{'PASS' if ok else 'fail'} [{len(prompt)}] {prompt}")
    for (m, want), rep in zip(jobs, replies):
        print(f"   {m:13s} want {want:3s} -> {'ok ' if passes(rep, want) else 'BAD'} {rep[:80]!r}")
    return ok


if __name__ == "__main__":
    for p in sys.argv[1:]:
        check(p)
