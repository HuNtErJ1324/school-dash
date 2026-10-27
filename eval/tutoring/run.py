"""Run the TutorMoments replay evaluation set up by prepare.py, then print the leaderboard. Spends money:
see estimate.py first.

    python eval/tutoring/run.py data/eval-tutoring/data/sample-20x2-seed451 [--modes ...] [--first N]

Uses ANTHROPIC_API_KEY from the project's .env. Results go to data/eval-tutoring/results/<run id>/
(transcripts, scores, summary.json); a rerun of the same command skips moments already done.
"""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

from prepare import ARM, HARNESS, ROOT, WORK


def api_key() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8-sig").splitlines():
        key, _, value = line.strip().partition("=")
        if key == "ANTHROPIC_API_KEY" and value.strip():
            return value.strip().strip("'\"")
    raise SystemExit("ANTHROPIC_API_KEY isn't set in .env")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("sample", type=Path, help="a sample folder from prepare.py")
    p.add_argument("--modes", nargs="+", default=["plain", "scaffolding_rigor", "dashboard"])
    p.add_argument("--first", type=int, help="only the first N moments (a cheap calibration run)")
    p.add_argument("--concurrency", type=int, default=4)
    args = p.parse_args()

    env = {**os.environ, "ANTHROPIC_API_KEY": api_key()}
    tm = ["uv", "run", "--project", str(HARNESS), "tutormoments"]
    cmd = tm + ["run", "--config", str(WORK / "config.yaml"), "--tutors", ARM, "--modes", *args.modes,
                "--data_path", str(args.sample.resolve()), "--concurrency", str(args.concurrency)]  # fmt: skip
    if args.first:
        cmd += ["--sample", str(args.first)]
    print("$", " ".join(cmd))
    # From WORK, so results/ lands there; the harness lives in WORK/harness (see prepare.py's HARNESS).
    subprocess.run(cmd, cwd=WORK, env=env, check=True)
    subprocess.run(tm + ["report", "--results-root", "results", "--out", "leaderboard"], cwd=WORK, env=env, check=True)
    print((WORK / "leaderboard.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
