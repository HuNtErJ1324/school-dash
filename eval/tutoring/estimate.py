"""Estimate what a TutorMoments run on a sample will cost, without calling any model.

    python eval/tutoring/estimate.py data/eval-tutoring/data/sample-20x2-seed451 [--modes plain scaffolding_rigor dashboard]

Token counts come from the sample's own text (about 4 characters a token); output lengths and the
thinking the tutor does are the uncertain part, so it prints a low/typical/high range. Prices are the
harness's own table (harness/src/tutormoments/models.yaml), with prompt caching as the harness does
it (the shared start of each tutor and student prompt is written once and then read) and the scorer's
passes at the Batch API's half price.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROMPTS = ROOT / "data" / "eval-tutoring" / "harness" / "src" / "tutormoments" / "prompts"

# $ per million tokens: input, output, cache read, cache write (models.yaml at the pinned commit)
PRICE = {
    "claude-opus-5-5": (4.00, 20.00, 0.20, 5.00),  # the tutor
    "claude-opus-4-6": (5.00, 25.00, 0.50, 6.25),  # simulated student and scorer
    "claude-opus-4-8": (5.00, 25.00, 0.50, 6.25),  # action-taxonomy classifier
}
TUTOR_TURNS, STUDENT_TURNS = 3, 2  # the paper's 5-turn replay, tutor first
TUTOR_TEXT, STUDENT_TEXT = 150, 60  # tokens per generated turn
# Tutor output per call (reply plus adaptive thinking at medium effort): low / typical / high
TUTOR_OUT = (350, 800, 1600)
SCORER_IN, SCORER_OUT = 3400, (1200, 2000, 3500)  # three batched passes per conversation, plus the excerpt
TAXONOMY_IN, TAXONOMY_OUT = 1200, 150


def tokens(text: str) -> int:
    return len(text) // 4


def usd(model: str, inp: float = 0, out: float = 0, read: float = 0, write: float = 0, batch: bool = False) -> float:
    i, o, r, w = PRICE[model]
    cost = (inp * i + out * o + read * r + write * w) / 1e6
    return cost / 2 if batch else cost


def conversation(m: dict, mode: str, band: int) -> dict[str, float]:
    system = tokens((PROMPTS / "tutor" / f"{mode}.txt").read_text(encoding="utf-8"))
    context = int(sum(tokens(t["text"]) + 4 for t in m["context"]) * 1.05)  # role labels and turn numbers
    # Tutor: the system prompt and pre-cut transcript are written to the cache once and read after;
    # each later call adds the turns generated since.
    tutor = usd("claude-opus-5-5", write=system + context, out=TUTOR_OUT[band])
    generated = 0
    for _ in range(TUTOR_TURNS - 1):
        generated += TUTOR_TEXT + STUDENT_TEXT
        tutor += usd("claude-opus-5-5", read=system + context, write=TUTOR_TEXT + STUDENT_TEXT, out=TUTOR_OUT[band])
    # Student: instructions, the full original session and the persona are the cached head; the
    # conversation so far follows uncached.
    head = tokens((PROMPTS / "student" / "oracle.txt").read_text(encoding="utf-8")) + tokens(m["student"]["reference"])
    head += tokens(json.dumps(m["student"]["trait"]))
    student = usd("claude-opus-4-6", write=head, inp=context + TUTOR_TEXT, out=STUDENT_TEXT)
    student += usd("claude-opus-4-6", read=head, inp=context + 2 * TUTOR_TEXT + STUDENT_TEXT, out=STUDENT_TEXT)
    excerpt = TUTOR_TURNS * TUTOR_TEXT + STUDENT_TURNS * STUDENT_TEXT
    scorer = usd("claude-opus-4-6", inp=SCORER_IN + excerpt, out=SCORER_OUT[band], batch=True)
    taxonomy = usd("claude-opus-4-8", inp=TAXONOMY_IN, out=TAXONOMY_OUT)
    return {"tutor": tutor, "student": student, "scorer": scorer, "taxonomy": taxonomy}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("sample", type=Path, help="a sample folder from prepare.py")
    p.add_argument("--modes", nargs="+", default=["plain", "scaffolding_rigor", "dashboard"])
    args = p.parse_args()
    moments = [json.loads(line) for line in (args.sample / "moments.jsonl").read_text(encoding="utf-8").splitlines() if line]
    n = len(moments)
    print(f"{n} moments x {len(args.modes)} prompts = {n * len(args.modes)} replayed conversations\n")
    totals = []
    for band, label in enumerate(("low", "typical", "high")):
        roles = {"tutor": 0.0, "student": 0.0, "scorer": 0.0, "taxonomy": 0.0}
        for mode in args.modes:
            for m in moments:
                for role, cost in conversation(m, mode, band).items():
                    roles[role] += cost
        total = sum(roles.values())
        totals.append(total)
        parts = ", ".join(f"{role} ${cost:.2f}" for role, cost in roles.items())
        print(f"{label:>8}: ${total:6.2f}  ({parts})")
    per = totals[1] / (n * len(args.modes))
    print(f"\ntypical cost per replayed conversation: ${per:.3f}")


if __name__ == "__main__":
    main()
