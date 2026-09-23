---
name: evaluation
description: Evaluation of the baseline and graph screeners, including synthetic test-set generation, ground truth, tuning/test splits, precision-recall, equal-recall comparison, bootstrap intervals, stratified metrics, freshness, and the A7 case study. Use whenever reading, writing, testing, or reviewing src/evaluate/, generating test data, or writing results in docs/results/.
---

# Evaluation

**The spec is the single source of truth:** `docs/specs/evaluation.md`. Read it in full before making any change. If this skill and the spec disagree, the spec wins; point out the disagreement. Never substitute an easier metric for one the spec defines; if a metric can't be computed as specified, stop and say so.

## Tripwires

- **No accuracy,** under any name, anywhere.
- **Ground truth is assigned by construction.** The generator (`src/evaluate/generate.py`) must never import from `src/screen/`. Never overwrite an intended label with screener output.
- **Nothing is tuned on the test split.** Thresholds are chosen on tuning and applied unchanged to test.
- **Source entities are disjoint** between tuning and test, for direct hits, indirect roots, and decoy sources.
- **The test set is frozen.** The evaluation refuses to run if the test-set hash doesn't match config. Never regenerate the test set to change results.
- **Per-stratum metrics use only what's defined for that stratum:** recall for positive strata, false-positive rate for negative strata, never precision within one stratum.
- **AMBIGUOUS counts as flagged for workload,** but only BLOCKED counts as caught for indirect-hit recall.
- **The A7 case study is descriptive only.** No precision, recall, or false-positive metrics, and all findings are framed as exposure to already-designated parties.
- **Headline numbers always have intervals,** and comparisons use paired bootstrap differences.

## Before writing up any results

Run the `/leakage-review` workflow and include its outcome in the write-up. Results are not publishable without it.

## Working rules

- Report counts alongside rates.
- Every write-up states the snapshot dates, seeds, test-set hash, and code commit.
- Show the command run and its output for every reported number.