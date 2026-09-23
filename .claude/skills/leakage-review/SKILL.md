---
name: leakage-review
description: The required leakage review gate before any evaluation results are written up. Run manually with /leakage-review.
disable-model-invocation: true
---

# Leakage review

This is the required gate from `docs/specs/evaluation.md`. Results are not publishable until it has run and its findings are resolved.

Use a **subagent with a fresh context**. Give it `src/evaluate/`, `config/settings.yaml`, and `docs/specs/evaluation.md`. It checks specifically for:

1. **Train/test leakage:** any threshold, parameter, or prompt selected using the test split; source entities shared between tuning and test.
2. **Wrong split:** any reported metric computed on the tuning split, or any tuning step that reads the test split.
3. **Ground-truth independence:** any import from `src/screen/` in the generator, or any label derived from screener output.
4. **Frozen test set:** the hash check exists, runs before evaluation, and matches config.
5. **Forbidden metrics:** accuracy under any name, or precision computed within a single stratum.
6. **A7:** any precision, recall, or false-positive metric computed for the A7 case study.

Report only correctness issues, each with file and line. Do not report style.

Save the report to `docs/results/leakage_review_<YYYY-MM-DD>.md`, with the code commit it reviewed, and state clearly at the top: **PASS** (no issues) or **FAIL** (issues listed).