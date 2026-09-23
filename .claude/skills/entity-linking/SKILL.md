---
name: entity-linking
description: Entity linking and resolution across OpenSanctions, Companies House, and GLEIF, plus customer-to-graph matching, using Splink, blocking rules, match thresholds, clustering, and the human-labeled pair sets. Use whenever reading, writing, testing, or reviewing src/resolve/, Splink configuration, matching code, or labeling tools.
---

# Entity linking

**The spec is the single source of truth:** `docs/specs/entity_linking.md`. Read it in full before making any change. If this skill and the spec disagree, the spec wins; point out the disagreement.

## The spec is a draft: respect the TBD section

The spec separates fixed decisions from items marked TBD. **Never fill in a TBD item by inference or by choosing a "reasonable default."** When a task depends on one:

1. Stop.
2. Propose 2–3 options, each with the evidence from the ingested data that would favor it (field completeness, cardinality, example records).
3. Wait for a decision. Once it's made, update the spec and move the item out of TBD.

## Before accepting any Splink code

Give a plan-mode explanation, not just working code, covering every point the spec requires: blocking rules with measured blocking recall on Set B, which `m` and `u` parameters are trained vs. fixed and why, how transliteration is actually handled, and how missing values are handled.

## Tripwires

- Exact identifiers (company number with jurisdiction, LEI) link first, without Splink.
- Companies House person records need linking within Companies House, not only across sources.
- Companies House dates of birth are month and year only; compare at that granularity.
- Flag-band links are never discarded and never treated as confirmed.
- Task 1–2 flag-band links become `IdentityLinkEdge` edges that feed ownership. Task 3 (customer matching) results **never** feed ownership; they go to L1 routing.
- Blocking recall is measured on Set B, never on pairs sampled from blocking output.
- Thresholds are chosen on Set A tune only; linking quality is reported on Set A holdout only.

## Labeled data: human-only labels

- **Never create, fill in, modify, or infer labels** in `data/labeled/`. Label values are entered by a human only.
- You **may** build the tooling: pair sampling, side-by-side display, and saving the labels a human enters.
- Treat existing labeled files as read-only.

## Working rules

- Show evidence for every claim about the data (counts, samples, measured recall) rather than asserting it.
- Record decisions in `docs/decisions.md`.