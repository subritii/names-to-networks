# Evaluation Specification

**Status:** complete (v0.2: merged)
**Governs:** `src/evaluate/`
**Depends on:** `ownership_rules.md` (status definitions, §5)

## 1. Purpose

This spec defines what "done" means for comparing the baseline (name-matching) screener against the graph screener. It exists *before* the evaluation code does, so metrics are specified on their own merits rather than shaped around whatever the code happens to produce.

`src/evaluate/` must implement exactly what is described here. If a metric can't be computed as specified once real data is in hand, that is a bug to fix or a reason to update this spec, never a silent substitution for an easier metric.

**Unit of evaluation:** one decision per customer (flagged or not flagged). All counts below are customer counts.

## 2. Test sets

### 2.1 Synthetic benchmark

A customer list screened against the **real graph plus a synthetic overlay** (`data/synthetic/`). The overlay is combined with the real graph only at test time and always tagged `synthetic: true`, so it can be excluded from anything that must run on real-only data.

| Stratum | Default count (per split) | Ground truth | Construction |
|---|---|---|---|
| Direct hit | 150 | Positive | Designated entity with name perturbed: transliteration, typo, token reorder, alias |
| Indirect hit | 150 | Positive | Clean-named synthetic company; overlay ownership edges make it BLOCKED under `ownership_rules.md` |
| Indirect ambiguous | 50 | Ambiguous | Overlay edges make it AMBIGUOUS under `ownership_rules.md` (band uncertainty, unknown stake, conflict, or ambiguous upstream owner) |
| Decoy | 700 | Negative | Innocent entity with a name similar to a designated one, and a different DOB, nationality, or identifiers |
| Clean control | 8,950 | Negative | Ordinary synthetic customer, no risk |
| **Total** | **10,000** | | |

Counts are defaults in `config/settings.yaml: eval.strata`.

### 2.2 Ground truth by construction

Ground truth must **not** come from running the code being evaluated. Otherwise the ownership code is being tested against itself.

1. The generator assigns each customer's label **at generation time**, from the structure it deliberately builds (e.g. "build a Band A edge from one designated owner → intended AMBIGUOUS").
2. The generator (`src/evaluate/generate.py`) **must not import** anything from `src/screen/`. It encodes intended outcomes independently.
3. **Cross-check:** after generation, run `propagate_blocked()` on the overlay and compare with the intended labels. Every disagreement is investigated and resolved as either a generator bug or an ownership-code bug, and recorded in `docs/decisions.md`. Never resolve a disagreement by overwriting the intended label with the code's output without a written reason.
4. **Hand check:** before freezing, a human reviews a random sample of **30** indirect cases (mixing indirect hits and indirect ambiguous) against `ownership_rules.md`.

### 2.3 A7 real-world case study

`case_studies/a7/`: entities already designated in connection with A7, mapped into UK and international company data. This is **descriptive only**. Ground truth for undesignated A7-linked companies is unknown, so **no precision, recall, or false-positive metrics are computed for A7**. See §7.

## 3. Splits and leakage prevention

1. **Two splits,** generated independently with different seeds: a **tuning split** (for choosing the entity-linking match threshold, screener thresholds, and any other parameter) and a **test split** (final reported metrics only). No customer appears in both.
2. **Source-entity disjointness.** Every designated source entity used in the tuning split is excluded from the test split, whether it is used as a direct hit, as the designated root of an indirect structure, or as the source name for a decoy. The same applies to any entities used to tune entity linking. Record the tuning and test source-entity lists in `config/settings.yaml`.
3. **Fixed seeds** for generation and split assignment, recorded in config (README "Reproducibility").
4. **Freeze the test set.** After generation, write the SHA-256 hash of the test file to `config/settings.yaml: eval.test_set_hash`. The evaluation refuses to run if the hash doesn't match. If the test set must be regenerated, use a new seed and record why in `docs/decisions.md`.
5. **Required leakage review** before any results are written up. Run a fresh-context subagent against the evaluation code:

   > Use a subagent to review src/evaluate/ against @docs/specs/evaluation.md. Check specifically for train/test leakage, metrics computed on the wrong split, ground truth derived from src/screen/, and any use of accuracy. Report only correctness issues.

   This review is a required gate, not optional polish. Do not publish results without it having run.

## 4. Screeners under comparison

**Baseline (name matching).** Name score = maximum over all name and alias pairs of `rapidfuzz.fuzz.WRatio`, after normalization: Unicode NFKD, transliteration to ASCII (`unidecode`), lowercase, strip punctuation, collapse whitespace. Flag if score ≥ threshold `t_b`. The baseline gets transliteration handling so that it is a reasonable matcher, not a strawman.

**Graph screener.** Flag if either:
- the entity-linking match score (Splink `match_probability × 100`, so it is on the same 0–100 scale as `t_b`) ≥ threshold `t_g`, or
- ownership status (per `ownership_rules.md` §5) is BLOCKED or AMBIGUOUS.

**How AMBIGUOUS counts:**
- For **workload metrics** (false positives, alerts per 1,000), an AMBIGUOUS result counts as **flagged**, because it creates analyst work.
- For **recall on the indirect-hit stratum**, only **BLOCKED** counts as caught.
- The indirect-ambiguous stratum is excluded from overall precision and recall, and reported separately (§5d).

## 5. Metrics (precise definitions, no substitutes)

**Forbidden anywhere in code, logs, or write-ups: plain accuracy,** under any name. With about 3% positives, a screener that flags nothing scores roughly 97% accuracy. The number is meaningless here.

Let TP, FP, FN, TN be customer counts. Recall = TP / (TP + FN). Precision = TP / (TP + FP). False-positive rate (FPR) = FP / (FP + TN). FP/1,000 = 1,000 × FP / N.

**a. Precision–recall curves.** Sweep thresholds `t_b` and `t_g` from 50 to 100 in steps of 1 — both are on a 0–100 scale (§4: `t_g` is `match_probability × 100`, never the raw Splink probability). Plot precision–recall curves for both screeners on the test split, and report **average precision (AP)** (`sklearn.metrics.average_precision_score`).

**b. Headline equal-recall comparison.** Target recall = `config/settings.yaml: eval.equal_recall_target` (default `0.95`; change it in config, not code).

1. On the **tuning split**, choose each screener's threshold as the highest value reaching the target recall.
2. Apply that threshold **unchanged** to the test split.
3. Report FP/1,000 for each screener on test, plus the **recall actually achieved on test** (it will differ slightly from the target).
4. **If a screener cannot reach the target recall** on the tuning split at any threshold (expected for the baseline, since it cannot see indirect hits), use the threshold giving its maximum recall, report that maximum, and state plainly that the target was unreachable.

**c. Name-only comparison.** Repeat (a) and (b) on the direct-hit, decoy, and clean-control strata only. This isolates name-matching quality from network reach, so the graph screener's advantage is not credited solely to indirect hits.

**d. Stratified results.** Report the metric that is defined for each stratum:

| Stratum | Report |
|---|---|
| Direct hit | Recall (count caught / count in stratum) |
| Indirect hit | **Indirect-risk recall** (BLOCKED only) |
| Indirect ambiguous | Share flagged (should be flagged; reported as a count and share) |
| Decoy | FPR and FP count |
| Clean control | FPR and FP count |

Precision is computed only on the full set (§5b) and name-only set (§5c), never per stratum. Indirect-risk recall is always reported on its own, never folded into an average. The baseline's expected value is about 0, and that gap is the project's central claim.

**e. Bootstrap confidence intervals.** 1,000 resamples of the test split with replacement, **stratified** (resampling within each stratum, preserving stratum sizes), fixed seed. Report the 95% percentile interval (2.5th / 97.5th percentile) for every headline number: precision, recall, AP, FP/1,000 at the equal-recall threshold, and indirect-risk recall.

**f. Paired differences.** For every comparison between screeners (FP/1,000, AP, indirect-risk recall), compute the difference **on the same resample** for both screeners, and report the 95% interval of the difference. If that interval includes 0, the write-up must say the difference is not established.

**g. Ambiguous-case rate.** AMBIGUOUS count / number of customers with at least one active ownership link to a BLOCKED or AMBIGUOUS entity. Report overall and broken down by reason code (`ownership_rules.md` §9). This measures data gaps.

**h. Freshness.** Measured per **designation–customer pair**: time from the designation's publication date (earliest of the official publication date and the OpenSanctions first-seen date) to the first scheduled graph refresh in which that customer is flagged.

- Replay designations from the last 12 months against the refresh schedule in `config/settings.yaml: eval.refresh_schedule` (default: daily), using `as_of` queries.
- Also report the same measure under a weekly schedule, for comparison.
- Report the **median, 90th percentile, and maximum**, in days. Never the mean alone.

**i. Alerts per 1,000 customers (deployment view).** Operational workload at the threshold recommended for deployment, which is separate from the equal-recall threshold in (b). The equal-recall threshold exists only for a fair head-to-head comparison. The deployment threshold is chosen on the tuning split by a criterion stated in the write-up (e.g. "maximum recall with FP/1,000 ≤ 20").

**Reporting format:** always give counts alongside rates (e.g. "146 / 150 (97.3%)").

## 6. Reporting requirements

Every results write-up in `docs/results/` states:

- Source snapshot date(s)
- Random seed(s)
- Test-set hash
- Code commit
- Split sizes per stratum, tuning and test
- The equal-recall target, and whether each screener reached it
- Whether bootstrap intervals were stratified (default) or unstratified
- That the leakage review (§3.5) ran, and its outcome

Required outputs:

1. Precision–recall curves: full set and name-only
2. Headline table at the equal-recall threshold, with intervals
3. Stratified table (§5d)
4. Paired-difference table (§5f)
5. Ambiguous-case rate with reason-code breakdown
6. Freshness distribution chart and summary
7. Deployment-view workload (§5i)
8. A7 case-study write-up (§7)

## 7. A7 case study: descriptive reporting

Report, without precision or recall:

- Designated A7-related entities in the graph: count, source lists, jurisdictions
- Linked entities each screener surfaces, by relationship type and hop distance
- Entities surfaced **only** by the graph screener
- AMBIGUOUS cases and the data gaps causing them (by reason code)
- Which customer-level red flags from the NCA Flash Alert could be checked with available data, and which could not (red flags taken from the alert's own text)

All findings are framed as **exposure to already-designated parties**, never as accusations against unlisted companies or people.

## 8. Non-goals (for this phase)

- Production latency and throughput benchmarking
- Cross-validation across multiple generation seeds (a single fixed seed is sufficient for this prototype; revisit if results turn out to be seed-sensitive)
- Evaluation of the L1 agent (see `l1_agent.md`)

## 9. Correctness checks for `src/evaluate/`

1. No function computes `correct / total` over the full imbalanced set and surfaces it as a headline number, under any name.
2. No threshold used for a headline number (§5b, §5c, §5i) was selected using the test split.
3. Ground truth is never read from `src/screen/` output (§2.2).
4. The evaluation refuses to run on a test set whose hash doesn't match config (§3.4).
5. Per-stratum reporting never computes precision within a single stratum, or recall within a negative-only stratum (§5d).
6. No precision or recall is computed for the A7 case study (§2.3).

## 10. Definition of done

- [ ] Ground truth generated by construction; cross-check disagreements resolved and recorded
- [ ] Hand check of 30 indirect cases passed
- [ ] Test set frozen and hash recorded before any final run
- [ ] Headline equal-recall comparison and name-only comparison complete
- [ ] All headline numbers reported with intervals; paired differences reported
- [ ] Stratified, ambiguous-case, freshness, and deployment-view results complete
- [ ] Leakage review run and passed
- [ ] A7 case study written with exposure-only framing
- [ ] Every result reproducible from one command with the recorded seeds, snapshots, and hash