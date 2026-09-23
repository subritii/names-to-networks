# Entity Linking

**Status:** draft. Scope, fixed decisions, and known source properties (§1–§5) are fixed. Blocking rules, comparison levels, clustering method, and match thresholds (§6) are **TBD after data exploration**. Do not fill in §6 by inference from this document alone; those decisions depend on what the ingested data actually looks like, not on the shape of the problem.

## 1. Purpose and scope

The goal is for each real-world person or company to resolve to one profile in the graph. OpenSanctions already deduplicates entities across the lists it consolidates, but that is not the only linking needed. This spec covers three linking tasks:

1. **Within Companies House (persons).** Companies House officer records are generally per appointment, not per person, so the same individual often appears under several officer records across companies. These must be merged, or one person's ownership stakes get split across several "people" in the graph.
2. **Cross-source.** OpenSanctions ↔ Companies House (officers and PSCs); OpenSanctions ↔ GLEIF; Companies House ↔ GLEIF (corporate entities covered by both).
3. **Customer-to-graph matching (screening).** Matching a screened customer to entities in the graph. This produces the entity-linking match score used by the graph screener in `evaluation.md` §4. It reuses the comparison design from tasks 1–2, but its thresholds are tuned under `evaluation.md` (tuning split), not here: `t_g` (the alert threshold, lower edge of the flag band) and an accept threshold above it.

**Where each task's output goes:**

| Task | Accept band | Flag band |
|---|---|---|
| 1–2 (links inside the graph) | Records merge into one entity | `possible_same_as` edge → feeds ownership propagation (§2.4) |
| 3 (customer to graph) | Customer identity **confirmed** → routing in `l1_agent.md` §2.1 | Customer identity **uncertain** → routing in `l1_agent.md` §2.1. **Never** feeds ownership propagation. |

## 2. Fixed decisions

### 2.1 Two tiers: exact identifiers first, probabilistic second

- **Tier 1: deterministic.** Where records share an exact, normalized identifier (Companies House company number together with its jurisdiction, or LEI), they are linked with `link_method: identifier` and `link_confidence: 1.0`. No Splink involvement.
  - If a Tier 1 link joins records whose names clearly disagree, keep the link but flag it with `IDENTIFIER_NAME_CONFLICT` for review.
- **Tier 2: probabilistic.** Records not resolved by Tier 1 go through probabilistic record linkage via `splink` (Fellegi–Sunter model). Each candidate pair receives a match probability from summed per-field match weights (`log2(m/u)` per comparison).

### 2.2 Fields compared

- **Persons:** name, date of birth, nationality, address.
- **Companies:** name, registration number, jurisdiction, incorporation date, address.

Date of birth is compared at **year-month granularity** wherever a Companies House record is involved (§5).

### 2.3 Link outcomes

Every Tier 2 candidate pair lands in one of three bands, by match probability:

| Band | Effect |
|---|---|
| **Accept** | Records merge into one entity (§2.5) |
| **Flag** | Records stay separate entities, joined by a `possible_same_as` edge with `link_confidence` and `flagged_low_confidence: true`. Routed to review. |
| **Reject** | No link |

Low-confidence links are **never discarded** and **never silently accepted.** The accept/flag/reject thresholds are TBD (§6).

### 2.4 Effect of flagged links on ownership

**Applies to tasks 1–2 only** (links inside the graph). Task 3 flag-band results never enter ownership propagation; they are routed by `l1_agent.md` §2.1.

A flagged link must not be treated as a confirmed identity, and must not be ignored either.

- If an entity is joined to a **designated** entity only by a `possible_same_as` edge, that entity is treated as **possibly designated**: it is seeded into the `possible` set in `ownership_rules.md` §5, never into `blocked`.
- Its downstream effects are therefore AMBIGUOUS, never BLOCKED and never CLEAR.
- Reason code: `LOW_CONFIDENCE_LINK`.

Ignoring flagged links would risk false CLEAR results; treating them as confirmed would risk false BLOCKED results. Routing them to AMBIGUOUS is consistent with how `ownership_rules.md` treats every other kind of uncertainty.

### 2.5 Clustering

- Accepted links (Tier 1 and Tier 2 accept band) merge records into clusters; each cluster becomes one entity in the graph.
- A cluster is flagged `CLUSTER_CONFLICT` for review if it contains any pair of records with a **hard conflict** (different full dates of birth, different company numbers in the same jurisdiction) or any pair whose own match probability falls in the reject band. This prevents chaining: A matches B and B matches C must not silently merge A and C when A and C clearly differ.
- The specific clustering method is TBD (§6).

### 2.6 Human-labeled ground truth

Ground truth is **human-made, always**. Labels are entered by the project owner and must never be generated or labeled by Claude or any model.

Claude **may** build the tooling: the scripts that sample pairs, display them side by side, and save labels. The label values themselves are human-entered only.

Two labeled sets serve two different purposes:

**Set A: threshold set (about 100–200 pairs).**
- Sampled from Tier 2 candidate pairs, **stratified across match-probability bands**, because a random sample of pairs is almost entirely non-matches.
- Split with a fixed seed into **tune (60%)** and **holdout (40%)**, stratified by band.
- Thresholds (§2.3) are chosen on **tune only**. Linking precision and recall are reported on **holdout only**.

**Set B: blocking-recall set.**
Pairs sampled from candidate pairs always survive blocking, so Set A cannot measure blocking recall. Set B contains true matches found **independently of blocking**:
- **Identifier-linked pairs** from Tier 1, with identifiers removed before blocking is applied. (Caveat: records that carry identifiers may be easier to match than those that don't; report this subset separately.)
- **Manual search:** a random sample of at least 50 OpenSanctions entities with a UK connection. For each, a human searches Companies House and records the counterpart found, or "none found."

**Blocking recall** = share of Set B true pairs that survive the blocking rules. Report it separately for each Set B source. No blocking rule is accepted without this measurement.

**`unsure` labels** are excluded from threshold selection and all metrics, but counted and reported.

**Storage:** `data/labeled/entity_pairs_labeled.csv`, with columns: pair id, source record ids, label ∈ `{match, non_match, unsure}`, set (`A` | `B`), split (`tune` | `holdout` | `n/a`), sampling method, labeler, date. Treat it as immutable once created; extending it is a manual, human act, not an automation target.

### 2.7 Required plan-mode explanation

Before accepting any Splink code, require a plan-mode explanation (not just working code) covering:

1. Which blocking rule(s) were chosen, and the measured blocking recall on Set B.
2. Which `m` and `u` parameters were estimated (e.g. `u` from random pairs, `m` via EM training) versus fixed manually, and why.
3. How name comparisons handle transliteration (e.g. "Mohammed" vs. "Muhammad"). A plain string-distance measure alone is not sufficient, and the explanation must state what actually handles it.
4. How missing values (e.g. no date of birth) are handled in the comparison vector.

### 2.8 Interface

Linking output feeds the graph-build step as:
- resolved entity clusters, each with its member source records;
- `possible_same_as` edges for flagged links, with `link_confidence`;
- per-link `link_method` (`identifier` | `probabilistic`) and reason codes (`IDENTIFIER_NAME_CONFLICT`, `CLUSTER_CONFLICT`, `LOW_CONFIDENCE_LINK`).

Linking does not decide blocked, ambiguous, or clear status. That is entirely `ownership_rules.md`'s concern, using the inputs defined in §2.4.

## 3. Constraints carried over from the rest of the project

- Every link and its confidence carries a source-snapshot date (README "Reproducibility").
- Linking thresholds are never selected on data used for final evaluation. Set A's holdout is for reporting linking quality only. The screening threshold `t_g` follows `evaluation.md` §3.
- Source entities used to build Set A or Set B are recorded, and are subject to the source-entity disjointness rule in `evaluation.md` §3.2.

## 4. Non-goals

- Real-time or streaming entity resolution (batch only, for this prototype).
- Linking to sources beyond OpenSanctions, Companies House, and GLEIF.

## 5. Known source properties (fixed; they come from the sources, not from exploration)

- **Companies House dates of birth** are public only as **month and year**. Date-of-birth comparisons involving Companies House records use year-month granularity; a full date from another source is truncated to year-month for that comparison.
- **Companies House officer records** are generally per appointment, so one person can appear under several records (§1, task 1).
- **GLEIF records** generally include the registration authority and local registration number for the entity, which enables Tier 1 links to Companies House for UK companies.
- **OpenSanctions entities** sometimes carry registration numbers or LEIs, where the source lists provide them. Coverage is uneven and must be measured, not assumed.

Verify each property against the ingested data and note any exceptions in `docs/decisions.md`.

## 6. TBD: depends on data exploration, do not guess

- [ ] Blocking rule(s) (e.g. surname + birth year, or postcode or registration-number prefix), depending on field completeness and cardinality once sources are ingested. Must be accepted using Set B blocking recall (§2.6).
- [ ] Comparison levels and distance functions per field (which string-distance or phonetic measure for names; exact vs. fuzzy for registration numbers; handling of missing values).
- [ ] Transliteration-handling approach for names. Candidates to evaluate empirically once real name data is available: phonetic algorithms, transliteration-normalizing libraries, a dedicated Splink comparison level. Do not pick one in the abstract.
- [ ] Accept / flag / reject thresholds, chosen on Set A tune (§2.6), possibly different per linking task.
- [ ] Whether `m` and `u` are trained per source pair or globally.
- [ ] Clustering method, subject to the conflict rule in §2.5.
- [ ] Coverage of identifiers in OpenSanctions records (share with a registration number or LEI), which determines how much Tier 1 can resolve.

Update the Status line as each item is resolved against real ingested data (e.g. to `draft — thresholds pending`, then `complete`). Do not mark this document complete while any box above is unchecked.