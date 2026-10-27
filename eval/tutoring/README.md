# Tutoring evaluation (TutorMoments replays)

Measures the Tutor page's tutoring guidance the way the TutorMoments paper measures LLM tutors
([Zhang et al., 2026](https://tutormoments.allen.ai/static/paper/tutormoments-preview.pdf)), using the
paper's own harness ([allenai/tutormoments](https://github.com/allenai/tutormoments), Apache-2.0) and
data ([allenai/tutormoments-preview](https://huggingface.co/datasets/allenai/tutormoments-preview),
CC-BY-4.0).

Each **moment** is a point in a real tutoring session where expert math teachers agreed the tutor
should scaffold (make the task approachable) or push for rigor (raise the demand). The harness cuts
the session there, lets the tutor under test take over for five turns against a simulated student
(Claude Opus 4.6 playing that student, with the persona the paper used), and has a scorer (Opus 4.6)
judge what the tutor did. Three numbers come out, all higher-is-better:

- **Appropriate scaffolding**: in scaffolding moments, the tutor scaffolded without over-scaffolding.
- **Appropriate rigor**: in rigor moments, the tutor pushed for rigor without over-scaffolding.
- **Avoids over-scaffolding**: across all moments, the tutor didn't do the student's thinking.

It compares three tutor prompts on the same model and moments: the paper's `plain` baseline, its
`scaffolding_rigor` prompt, and `dashboard`, which is the "dial" section of our agent's AGENTS.md
(`backend/app/agent/runner.py`), as deployed, in the paper's tutor framing. The tutor is Opus 5.5 at
medium effort, as the dashboard runs it.

## Running it

```bash
python eval/tutoring/prepare.py          # free: harness, data, a balanced 20+20 sample, config
python eval/tutoring/estimate.py data/eval-tutoring/data/sample-20x2-seed451   # free: expected cost
python eval/tutoring/run.py data/eval-tutoring/data/sample-20x2-seed451        # spends money
```

`run.py --first 2` replays just the first two moments, to check everything works and see real costs
before the full run. To re-check particular moments after a prompt change, `prepare.py --only ID ...`
builds a sample of just those (in `data/eval-tutoring/data/only-<n>-<hash>/`) to pass to `run.py`. Results land in `data/eval-tutoring/results/`, and `data/eval-tutoring/leaderboard.md`
sums them up. Rerunning skips finished moments. Run `prepare.py` again after changing the
tutoring guidance: it rewrites the `dashboard` prompt from the current AGENTS.md, and move the old
`dashboard` results out of `results/` first or they'll be skipped as finished.

Run `estimate.py` with the same `--modes` before each run: the simulated student's cached context is
the same for every prompt and is written by whichever prompt runs first, so re-running one prompt
alone costs more than its share of a three-prompt run.

## Reading the results

Past runs and what they showed are in [RESULTS.md](RESULTS.md).


- The moments are K-12 math. The dashboard tutors college CSE and math, so this measures whether
  the scaffold/rigor guidance works, not the whole tutoring experience. Two lines of our prompt
  (about `notes/` and citing course documents) don't apply in a replay and are left in only so the
  prompt is exactly what's deployed.
- 20 moments per kind is small. A score's sampling error is about ±0.1, so differences under about
  0.2 between prompts aren't meaningful; the paper used 260 per kind.
- The paper's own numbers for comparison (Claude Opus 4.8, Table 8): plain 0.615 / 0.208 / 0.462,
  scaffolding_rigor 0.858 / 0.831 / 0.896 (scaffolding / rigor / avoids over-scaffolding).
