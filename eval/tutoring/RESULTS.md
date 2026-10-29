# Results

## 2026-10-28: `dashboard` v4, confirming answers and stopping once they've got it

Two rules added to the dial section since v3, both from using the tutor: say plainly whether an answer is
right before raising the bar ("Say so when they get it"), and once the student has shown what they came for,
sum up and stop asking ("Stop when they've got it"). Same harness, data, sample, tutor and scorer as below;
only `dashboard` was re-run.

| Prompt | Appropriate scaffolding | Appropriate rigor | Avoids over-scaffolding | KL(S‖R) / KL(R‖S) |
|---|---|---|---|---|
| `dashboard` v1 | 0.95 (19/20) | 0.95 (19/20) | 0.975 (39/40) | 0.141 / 0.136 |
| `dashboard` v2 | 0.95 (19/20) | 0.90 (18/20) | 0.95 (38/40) | 0.116 / 0.119 |
| `dashboard` v4 | 1.00 (20/20) | 0.90 (18/20) | 0.95 (38/40) | 0.116 / 0.115 |

**Paired**: v4 was right on 2 moments v1 wasn't and v1 on 2 v4 wasn't; against v2, 2 vs 1. The stopping rule
didn't cost rigor: both v4 misses are rigor moments where the tutor did push for rigor and was marked for
over-scaffolding. One (the even/odd moment) was also missed by v2. The other is the new rules' doing: the
student finished 5/3 + 7/4, and the tutor's "Yes, 41/12 is right" went on to replay every step (5 × 4 = 20,
3 × 7 = 21, …), which the scorer counted as doing the student's reflecting for them. Tutor turns without a
question rose from 10 of 120 (v2) to 19 of 120, so the rules do change how often it stops asking.

Follow-up, deployed but not re-run: both rules now say to name the takeaway in a phrase, not replay the steps.

## 2026-10-25 (later still): `dashboard` v3, spot-check of the two moments the rule touched

The rigor-gap rule reworded and deployed: first ask what they know and build from that; if they can't
say, point to where to look (the figure, the definition, their own earlier work), not at what they
missed, and don't break it into steps for them. Re-run on just the congruence and tally-marks moments
(`prepare.py --only`, sample `only-2-d41f0468`), both rigor moments.

| Moment | v1 (no rule) | v2 (first wording) | v3 (this wording) |
|---|---|---|---|
| Congruence | over-scaffolded | right | right |
| Tally marks | right | over-scaffolded | right |

- Congruence: the tutor asked "when two triangles are congruent, what does that tell you about their
  sides?", then pointed back to the parallelogram to match sides itself. That's the intended order.
- Tally marks: no hint about the slanted line this time. It asked for a slow recount, and once the
  student had it, raised the bar (how could the bundle of five get you to 6 without counting?).

One replay of each, so this shows the new wording doesn't repeat v2's giveaway on these two, not that it
raises the 40-moment score; v1 got the tally moment right without the rule.

## 2026-10-25 (later): `dashboard` v2, with the rigor-gap rule

One rule added to AGENTS.md's dial section and deployed: when a push for rigor turns up a gap, don't
re-teach; ask what they already know, or point them at where it lives in the problem, and explain only
the piece they turn out not to have. Same harness, data, sample, tutor and scorer as the run below;
only `dashboard` was re-run.

| Prompt | Appropriate scaffolding | Appropriate rigor | Avoids over-scaffolding | KL(S‖R) / KL(R‖S) |
|---|---|---|---|---|
| `dashboard` v1 | 0.95 (19/20) | 0.95 (19/20) | 0.975 (39/40) | 0.141 / 0.136 |
| `dashboard` v2 | 0.95 (19/20) | 0.90 (18/20) | 0.95 (38/40) | 0.116 / 0.119 |

**Paired** (exact McNemar): v2 was right on 2 moments v1 wasn't, and v1 on 3 that v2 wasn't (p = 1.0);
over-scaffolding 2 vs 1. Against `scaffolding_rigor`, 3 vs 1 (p = 0.63); against `plain`, 19 vs 1
(p < 0.001). No measurable change from v1. Five moments flipped between the two runs; the tutor and the
simulated student both sample, so some would flip with an unchanged prompt, and 40 moments can't tell
the two apart.

**What changed:**
1. The target moment (congruence) is fixed. When the student said they'd guessed, the tutor asked
   smaller questions about the diagram instead of re-explaining congruence; no over-scaffolding.
2. Two scaffolding moments traded places, both the label/replay mismatch from before: the simulated
   student succeeded on its own, the tutor pushed for rigor, and the scorer called it effective.
3. New miss, probably from the new wording (tally marks). The student counted six, then seven. The tutor
   said "don't forget the slanted line" and then had them count in steps (the straight lines, then add
   the slanted one). The rule's example "point them at… the line they skipped" reads as permission to
   name the very thing they missed. Tighter wording would ask first, and point to where to look rather
   than at what's missing.
4. New miss, unrelated to the rule (even/odd). The student was right, and the tutor said "so that first
   one isn't our answer" for them before asking about the rest. v1 didn't.


## 2026-10-25: TutorMoments replays, 40 moments (20 scaffolding, 20 rigor)

Tutor: Claude Opus 5.5 at medium effort, as the dashboard runs it. Harness `allenai/tutormoments@f62fce9`,
data `allenai/tutormoments-preview@3bb0c10`, sample `sample-20x2-seed451` (one moment per session).
`dashboard` is AGENTS.md's dial section as deployed on 2026-10-25.

| Prompt | Appropriate scaffolding | Appropriate rigor | Avoids over-scaffolding | KL(S‖R) / KL(R‖S) |
|---|---|---|---|---|
| `plain` (paper's baseline) | 0.80 (16/20) | 0.15 (3/20) | 0.50 (20/40) | 0.125 / 0.112 |
| `scaffolding_rigor` (paper's best) | 0.90 (18/20) | 0.85 (17/20) | 0.925 (37/40) | 0.087 / 0.081 |
| **`dashboard` (ours)** | **0.95 (19/20)** | **0.95 (19/20)** | **0.975 (39/40)** | 0.141 / 0.136 |

For comparison, the paper's Claude Opus 4.8 (Table 8): plain 0.615 / 0.208 / 0.462, scaffolding_rigor
0.858 / 0.831 / 0.896.

**Paired, moment by moment** (exact McNemar test):
- `dashboard` vs `plain`: the right move on 19 moments where plain wasn't, never the reverse (p < 0.001);
  over-scaffolding avoided on 19 where plain didn't, never the reverse (p < 0.001).
- `dashboard` vs `scaffolding_rigor`: right on 5 moments it wasn't, and it on 2 we weren't (p = 0.45);
  over-scaffolding 1 vs 3 (p = 0.63). At this sample size, ours matches the paper's best prompt; the
  edge isn't significant.

**What the tutor did** (scorer's label per moment): plain 14 scaffolding-only, 24 both, 2 rigor-only;
scaffolding_rigor 2 / 27 / 11; dashboard 3 / 25 / 12. KL is how differently the tutor acts in the two
kinds of moment (higher = more situation-aware); at n = 40 it's smoothed toward 0, so compare it only
between these runs, not with the harness's human reference (computed on 518 moments).

**Where `dashboard` missed:**
1. A scaffolding moment where the simulated student answered correctly on its own, so the tutor pushed
   for rigor (asked why a wrong answer is wrong, then a reversed problem). The scorer called it
   effective; the miss is the teachers' label not matching how the replay went (the paper notes its
   simulated students succeed more than real ones).
2. A rigor moment: the tutor rightly asked how the student got a step; when the student said they'd
   guessed, it re-explained what congruence means, which the student had already shown they knew. That's
   the over-scaffolding the instructions warn about. A lighter move would have been to ask what they
   know about congruent sides and point them at the figure.
