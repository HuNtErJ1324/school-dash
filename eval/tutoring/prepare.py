"""Set up the TutorMoments replay evaluation of the dashboard tutor's instructions. See README.md.

Everything it fetches and builds goes in data/eval-tutoring/ (git- and Docker-ignored, like the rest
of data/). It spends nothing: no model is called until you run run.py.

    python eval/tutoring/prepare.py [--per-dimension 20] [--seed 451]
    python eval/tutoring/prepare.py --only MOMENT_ID [MOMENT_ID ...]   # re-check a few moments
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import subprocess
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
WORK = ROOT / "data" / "eval-tutoring"
HARNESS = WORK / "harness"  # not "tutormoments": run from WORK, that name would shadow the package
HARNESS_URL = "https://github.com/allenai/tutormoments.git"
HARNESS_COMMIT = "f62fce988bd1b36c8ef9d8a022582d5e98bf3188"  # 2026-10-23
DATASET = "allenai/tutormoments-preview"
DATASET_REVISION = "3bb0c104d65e29facffca7ba864ef10fa22d0e78"  # 2026-10-01
DATA_FILES = ["moments.jsonl", "moments.manifest.json", "moments.schema.json"]
MODE = "dashboard"  # our prompt, beside the paper's plain and scaffolding_rigor
# The tutor as the dashboard runs it: Opus 5.5 at medium effort (prime-agent's --thinking medium).
ARM = "claude-opus-5-5-medium"
ARM_SPEC = "{ model: claude-opus-5-5, thinking: { type: adaptive }, effort: medium, condition: medium }"


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    print("$", " ".join(cmd))
    return subprocess.run(cmd, check=True, **kw)


def dial_section() -> str:
    """The deployed tutoring guidance: AGENTS.md's dial subsection, from the runner the agent container uses."""
    sys.path.insert(0, str(ROOT / "backend"))
    from app.agent.runner import AGENTS_MD

    start = AGENTS_MD.index("### The dial: scaffold or push for rigor")
    end = AGENTS_MD.index("\n- Practice material", start)
    return AGENTS_MD[start:end].strip()


def dashboard_prompt() -> str:
    # Same framing and harness plumbing as the paper's prompts (prompts/tutor/plain.txt), so the only
    # difference between the modes is the tutoring guidance.
    return "\n\n".join([
        "You are an online tutor in a live tutoring session with a K-12 student.",
        dial_section(),
        "## Context\nYou have the following metadata about the student:\n{student_context}",
        "## Sending multiple messages\nTo break up your response over multiple messages, use [NEW_MESSAGE].\n"
        "This will separate your response into multiple lines / messages to the student.",
    ]) + "\n"


def install_harness() -> None:
    if not (HARNESS / ".git").exists():
        WORK.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", "-q", HARNESS_URL, str(HARNESS)])
    run(["git", "-C", str(HARNESS), "checkout", "-q", HARNESS_COMMIT])
    run(["uv", "sync", "--frozen", "--project", str(HARNESS)])
    prompt = HARNESS / "src" / "tutormoments" / "prompts" / "tutor" / f"{MODE}.txt"
    prompt.write_text(dashboard_prompt(), encoding="utf-8", newline="\n")
    print(f"wrote {prompt.relative_to(ROOT)} ({len(prompt.read_text(encoding='utf-8'))} chars)")
    tutor = HARNESS / "src" / "tutormoments" / "tutor.py"
    src = tutor.read_text(encoding="utf-8")
    old = 'SUPPORTED_MODES = {"plain", "scaffolding_rigor", "oracle"}'
    if old in src:  # documentation only: the harness loads prompts/tutor/<mode>.txt for any mode
        tutor.write_text(src.replace(old, f'SUPPORTED_MODES = {{"plain", "scaffolding_rigor", "oracle", "{MODE}"}}'),
                         encoding="utf-8", newline="\n")  # fmt: skip


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def download() -> Path:
    full = WORK / "data" / "full"
    full.mkdir(parents=True, exist_ok=True)
    for name in DATA_FILES:
        dest = full / name
        if not dest.exists():
            url = f"https://huggingface.co/datasets/{DATASET}/resolve/{DATASET_REVISION}/{name}"
            print("downloading", url)
            urllib.request.urlretrieve(url, dest)
    manifest = json.loads((full / "moments.manifest.json").read_text(encoding="utf-8"))
    if sha256(full / "moments.jsonl") != manifest["file_sha256"]:
        raise SystemExit("moments.jsonl doesn't match its manifest; delete data/eval-tutoring/data/full and rerun")
    return full


def sample(full: Path, per_dimension: int, seed: int) -> Path:
    """A balanced sample, one moment per tutoring session so no session counts twice."""
    lines = (full / "moments.jsonl").read_text(encoding="utf-8").splitlines()
    records = [(line, json.loads(line)) for line in lines if line.strip()]
    random.Random(seed).shuffle(records)
    picked, sessions = {"scaffolding": [], "rigor": []}, set()
    for line, rec in records:
        dim, conv = rec["dimension"], rec["provenance"]["conv_id"]
        if dim in picked and len(picked[dim]) < per_dimension and conv not in sessions:
            picked[dim].append((line, rec))
            sessions.add(conv)
    chosen = sorted(picked["scaffolding"] + picked["rigor"], key=lambda lr: lr[1]["id"])
    out = write_sample(
        WORK / "data" / f"sample-{per_dimension}x2-seed{seed}", [line for line, _ in chosen], full,
        f"balanced_520_sample_{per_dimension}x2_seed{seed}",
        {"per_dimension": per_dimension, "seed": seed, "one_moment_per_session": True},
    )  # fmt: skip
    counts = {d: len(v) for d, v in picked.items()}
    print(f"sample: {counts} from {len(sessions)} sessions -> {out.relative_to(ROOT)}")
    return out


def only(full: Path, ids: list[str]) -> Path:
    """Just the named moments, to re-check a few after a prompt change."""
    lines = (full / "moments.jsonl").read_text(encoding="utf-8").splitlines()
    by_id = {json.loads(line)["id"]: line for line in lines if line.strip()}
    if missing := [i for i in ids if i not in by_id]:
        raise SystemExit(f"not in the dataset: {missing}")
    ids = sorted(set(ids))
    key = hashlib.sha256("\n".join(ids).encode()).hexdigest()[:8]
    out = write_sample(WORK / "data" / f"only-{len(ids)}-{key}", [by_id[i] for i in ids], full,
                       f"balanced_520_only_{len(ids)}_{key}", {"ids": ids})  # fmt: skip
    print(f"sample: {len(ids)} named moments -> {out.relative_to(ROOT)}")
    return out


def write_sample(out: Path, lines: list[str], full: Path, name: str, provenance: dict) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    (out / "moments.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    (out / "moments.schema.json").write_bytes((full / "moments.schema.json").read_bytes())
    # The manifest's content hash is over records as the harness loads them, so let it compute that.
    hashes = subprocess.run(
        ["uv", "run", "--project", str(HARNESS), "python", "-c",
         "import json, sys; from tutormoments import moments as m; "
         "recs = m._read_moments_jsonl(sys.argv[1]); "
         "print(json.dumps({'content_hash': m.records_content_hash([r.to_dict() for r in recs]), "
         "'file_sha256': m.file_sha256(sys.argv[1]), 'n': len(recs)}))",
         str(out / "moments.jsonl")],
        check=True, capture_output=True, text=True,
    )  # fmt: skip
    h = json.loads(hashes.stdout.strip().splitlines()[-1])
    base = json.loads((full / "moments.manifest.json").read_text(encoding="utf-8"))
    manifest = {
        "name": name,
        "version": base.get("version"),
        "schema_version": base["schema_version"],
        "record_count": h["n"],
        "content_hash": h["content_hash"],
        "file_sha256": h["file_sha256"],
        "provenance": {"subset_of": DATASET, "revision": DATASET_REVISION, **provenance},
    }
    (out / "moments.manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return out


def write_config() -> Path:
    default = (HARNESS / "src" / "tutormoments" / "default_config.yaml").read_text(encoding="utf-8")
    anchor = "benchmark_models:\n"
    config = default.replace(anchor, f"{anchor}  {ARM}: {ARM_SPEC}\n", 1)
    out = WORK / "config.yaml"
    out.write_text(config, encoding="utf-8", newline="\n")
    print("wrote", out.relative_to(ROOT))
    return out


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--per-dimension", type=int, default=20, help="moments of each kind (scaffolding, rigor)")
    p.add_argument("--seed", type=int, default=451)
    p.add_argument("--only", nargs="+", metavar="MOMENT_ID", help="just these moments, instead of a balanced sample")
    args = p.parse_args()
    install_harness()
    full = download()
    data = only(full, args.only) if args.only else sample(full, args.per_dimension, args.seed)
    write_config()
    print(f"\nReady. Estimate the cost with:  python eval/tutoring/estimate.py {data.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
