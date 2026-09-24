# Ownership & Blocking Rules

**Status:** complete (v0.10: added §9.2 — evidence content, computed from fact IDs (§9.2.1) and a synchronous justification rank (§9.2.2), with per-status selection rules (§9.2.4), worked examples EV1–EV11 (§9.2.5), and required properties (§9.2.6); §9's `evidence_paths` placeholder replaced by the `evidence` field, now specified by §9.2; v0.9: `control_links`/`CONTROL_ONLY_LINK` now filtered to controllers that are BLOCKED or AMBIGUOUS (§9, §9.1); §4.4 gained the boundary-touching true-open/closed-bounds rule (§4.2's band table) for ranges whose stored numbers meet at exactly one point, with worked-example rows 29–31; row 32 added for a CLEAR-controller control edge; §9.1's `AGGREGATE_OWNERSHIP` now states explicitly that it presupposes `OWNED_BY_BLOCKED`; §7 now requires `propagate_blocked()` to raise a validation error on a null start date rather than treat it as inactive; v0.8: fixed §4.4's `unknown` uncertainty-kind condition — an overlap-combined edge is `unknown` only if *every* contributing source is unknown, not merely one; added worked-example rows 27–28; v0.7: added §9.1 explicit reason-code trigger conditions, §4.4 uncertainty-kind classification (`band`/`unknown`/`conflict`/`none`) and the same-source duplicate-edge rule; v0.6: added §5.2 — reported sums capped at 100, new `OWNERSHIP_OVER_100` reason code; v0.5: §11 item 1 scoped monotonicity to the combined edge after §4.4 conflict resolution; v0.4: `IdentityLinkEdge` made symmetric and given a `first_seen_date` with half-open time validity; v0.3: identity-link ambiguity path added, per `entity_linking.md` §2.4)
**Governs:** `src/screen/ownership.py` (`propagate_blocked()`), `tests/test_ownership_examples.py`, `tests/test_ownership_properties.py`, `tests/test_ownership_reason_codes.py`, `tests/test_ownership_evidence.py`

## 1. Purpose

This spec defines the ownership-based blocking test used by the graph screener: a **simplified OFAC-style 50 Percent Rule**, applied to both exact ownership percentages and UK PSC ownership bands. It is a demonstration rule for this prototype, not a legal determination under any regime (see README "Limitations and Ethics").

There is **one rule**. Exact and banded data differ only in how precisely the stake is known, so both are represented the same way: as a range with a lower and upper bound (§4). An exact stake is simply a range whose bounds are equal.

This document is the single source of truth for the blocking logic. Do not infer or "improve" the algorithm from intuition about how ownership percentages should combine. The intuitive approach (multiplying percentages along a chain) is wrong for blocking purposes and is explicitly rejected in §3.

## 2. Data model

**Entity**: `id`, `kind` (`Person` | `Company`), `designated` (bool), `designation_start` (date), `designation_end` (date | null).

**OwnershipEdge**: `owner_id`, `owned_id`, `stake_lower` (0–100), `stake_upper` (0–100, `>= stake_lower`), `stake_known` (bool), `source` (string), `start_date`, `end_date` (null = ongoing), `start_date_inferred` (bool).

- `stake_lower == stake_upper` for exact stakes (e.g. exact-percentage ownership data).
- `stake_lower < stake_upper` for banded stakes (UK PSC bands) and unknown stakes (§4).

**IdentityLinkEdge**: `entity_id`, `same_as_id`, `first_seen_date`. Produced by entity linking (`entity_linking.md` §2.3–§2.4) for a `possible_same_as` link it could not confirm or reject — i.e., a Flag-band Tier 2 result. It is consumed here only for the ambiguity path in §5.1; it is not an `OwnershipEdge` and carries no stake. A confirmed identity (Tier 1, or Tier 2 accept band) is not represented this way — entity linking merges those records into one entity before this algorithm ever runs, so this edge type only ever exists between two still-distinct entities. **Populated only from `entity_linking.md` tasks 1–2 (links inside the graph). Task 3 customer-matching results never create an `IdentityLinkEdge`** — those are routed by `l1_agent.md` §2.1 instead, against the matched entity's status computed here.

`entity_id` and `same_as_id` name an unordered pair: the edge is symmetric (§5.1), so which field holds which id is not meaningful and entity linking may record either order. `first_seen_date` gives the link the same half-open time validity as any other edge (§7), using the open-ended case (`first_seen_date <= as_of_date`, no end date) — an unconfirmed identity link doesn't get end-dated in place, it is either resolved (merged or rejected, and removed from the data by entity linking) or remains open-ended. It is filtered to `as_of_date` before propagation exactly like an `OwnershipEdge`.

Blocked status per entity is one of **BLOCKED**, **AMBIGUOUS**, **CLEAR**. Status is always computed **as of a date** (§7). It is never a permanent attribute stored once and reused.

## 3. Trap: do not multiply ownership percentages to decide blocking

The intuitive approach computes *effective* ownership along a chain: if A owns 60% of B, and B owns 50% of C, then A's effective stake in C is `0.6 × 0.5 = 30%`, which is under 50%, so C looks clear.

**This is wrong for blocking purposes.** Under OFAC's guidance, blocked status *cascades*: A's stake makes B blocked; B's stake (as a now-blocked owner) makes C blocked. At each step, the stakes of *blocked owners* are summed. Percentages are never multiplied along the whole chain.

Blocking is **status propagation**, not percentage multiplication. The multiplied ("effective") ownership figure still has a use, as a risk feature computed and reported separately (§8), but it must never gate the blocking decision.

## 4. Stake representation

### 4.1 Exact stakes

An exact percentage `p` becomes `stake_lower = stake_upper = p`, `stake_known = True`.

### 4.2 UK PSC bands

| Band | Meaning | Range | `stake_lower` | `stake_upper` |
|---|---|---|---|---|
| A | more than 25% but not more than 50% | `(25, 50]` | 25 | 50 |
| B | more than 50% but less than 75% | `(50, 75)` | 50 | 75 |
| C | 75% or more | `[75, 100]` | 75 | 100 |

Edge cases, stated explicitly because this is exactly where generated code goes wrong:

- **Exactly 50%** falls in Band A (upper-closed at 50). When summed, it counts as **crossing** the 50% threshold (§5). Do not treat 50% as "not yet at" the threshold.
- **Exactly 75%** falls in Band C, not Band B. Band B is open at both ends.
- **Exactly 25% or below** is below the PSC reporting threshold, so there is no reportable interest. Represent this as the **absence of an edge**, not as a range like `(0, 25]`. Do not invent a stake for an unreported relationship.
- **Numeric bound convention.** Open endpoints are stored as plain numbers (`stake_lower = 25` for Band A). Openness only matters when assigning a *raw* percentage to a band. Once stored as bounds, sums are ordinary arithmetic. This is safe: an open lower bound means the true stake is strictly greater than the stored number, so a lower-bound sum can only understate the truth and can never cause a false BLOCKED. Do not implement epsilon-adjusted open-interval arithmetic; it is unnecessary and a common source of bugs.

### 4.3 Unknown stakes

An ownership edge with no percentage or band (common in OpenSanctions data) becomes `stake_lower = 0`, `stake_upper = 100`, `stake_known = False`. The link is real; only its size is unknown. A blocked owner with an unknown stake therefore makes the owned company AMBIGUOUS (unless other owners confirm BLOCKED).

### 4.4 Conflicting sources

If two or more active edges exist for the same `owner_id` → `owned_id` pair from different sources, combine them into one edge before propagation:

- If their ranges **overlap**, use the **intersection** (the range both sources allow). Example: exact 40% and Band A `(25, 50]` → `[40, 40]`.
- If their ranges **do not overlap**, use the **hull** (the smallest range containing both) and add reason code `STAKE_CONFLICT`. Example: 30% and 60% → `[30, 60]`, which produces AMBIGUOUS if the owner is blocked.

This combination rule applies to any two or more active edges sharing an `owner_id` → `owned_id` pair, not only edges from different sources. If a single source itself reports two active edges for the same pair, combine them the same way.

**Boundary-touching ranges.** Two ranges whose *stored* numbers meet at exactly one point (e.g. Band A's stored upper bound `50` and Band B's stored lower bound `50`) do not automatically count as overlapping. §4.2's bound convention stores only plain numbers — openness is not tracked in `stake_lower`/`stake_upper` themselves — so combination must look up each contributing source's **true** openness at that boundary from §4.2's band table before deciding overlap:

| Source shape | True lower bound | True upper bound |
|---|---|---|
| Exact (`stake_lower == stake_upper`) | closed | closed |
| Unknown (`stake_known = False`) | closed | closed |
| Band A `(25, 50]` | open | closed |
| Band B `(50, 75)` | open | open |
| Band C `[75, 100]` | closed | closed |

The shared point is a true overlap only if it falls inside **every** contributing source's own range under that source's true bounds. If any contributing source's own bound at that point is open, the sources are treated as **not** overlapping — hull, `STAKE_CONFLICT` — even though their stored numbers touch. (This check only matters when the numeric intersection collapses to a single point; a genuine sub-range intersection, where `inter_lower < inter_upper`, is always a true overlap regardless of any endpoint's openness, since every interior point is included no matter how the endpoints are bounded.)

Examples (§6.2 rows 29–31):
- Band A `[25, 50]` + Band B `[50, 75]`: stored ranges touch at `50`, but `50` is open in Band B's true range `(50, 75)` → not a true overlap → hull `[25, 75]`, `STAKE_CONFLICT`.
- Exact `25` + Band A `[25, 50]`: stored ranges touch at `25`, but `25` is open in Band A's true range `(25, 50]` → not a true overlap → hull `[25, 50]`, `STAKE_CONFLICT`.
- Exact `50` + Band A `[25, 50]`: stored ranges touch at `50`, and `50` is closed (included) in Band A's true range `(25, 50]` → a true overlap → pinned `[50, 50]`.

**Uncertainty kind.** After combination, each `owner_id` → `owned_id` pair's single combined edge carries exactly one uncertainty kind, used by §9.1's reason-code conditions. The kind reflects the **resulting range**, not which sources fed it:

| Kind | Condition |
|---|---|
| `conflict` | The hull was used (the source ranges did not overlap) |
| `none` | Not `conflict`, and the combined `stake_lower == stake_upper` (a pinned single value) — including when this point value was only reached by intersecting an unknown source (`[0, 100]`) with an exact source, e.g. row 28 |
| `unknown` | Not `conflict` or `none`, and **every** contributing source edge is unknown (`stake_known = False`). An unknown source intersected with any known source (band or exact) is *not* `unknown` — the known source's own range applies, since intersecting with `[0, 100]` cannot narrow it further (row 27) |
| `band` | Not `conflict`, `none`, or `unknown` — i.e. at least one contributing source is known, and the combined range is not a single point. For this spec's finite stake model (exact / Band A / Band B / Band C / unknown), this case's combined range always exactly equals one contributing known source's own range |

Kinds are mutually exclusive and checked in the order above, so a hull-combined edge is always `conflict`, never also `band` — even though a hull's range is necessarily at least as wide as either source's own range. Likewise, an edge combined from an unknown source and a band source is `band`, never `unknown` — unknown only describes a combined edge where *no* source narrowed the range at all.

### 4.5 Ignored edges

- **Self-ownership edges** (`owner_id == owned_id`) are ignored.
- A PSC entry recorded only as "significant influence or control" **without** an ownership share is not an ownership edge. It is stored as a `control` relationship, reported as evidence (§9), and never changes status (§10).

## 5. The algorithm (three-valued, with ambiguity propagation)

Two sets are built up together, over **every entity** — persons as well as companies. Persons and companies without ownership edges simply contribute zero to every sum below; the loop does not need to branch on `kind`.

- `blocked`: entities confirmed BLOCKED
- `possible`: entities that might be blocked but cannot be confirmed (AMBIGUOUS), via an ownership-stake path (below) or a low-confidence identity link (§5.1)

```
blocked  = { e : e.designated and designation active at as_of_date }   # seed (§7)
possible = { }

repeat:
    changed = False
    for each entity E not in blocked:
        edges = combined active ownership edges into E at as_of_date (§4.4, §7)   # empty for entities nobody owns

        # Confirmed: blocked owners alone definitely reach 50%
        lower_sum = Σ stake_lower  for edges whose owner is in blocked
        upper_sum_blocked = Σ stake_upper  for edges whose owner is in blocked   # for blocked_owner_sum output (§9) only; never used in the decision below
        if lower_sum >= 50:
            blocked.add(E)
            possible.discard(E)
            changed = True
            continue

        if E not in possible:
            # Possible, ownership path: blocked or ambiguous owners could reach 50%
            upper_sum = min(100, Σ stake_upper  for edges whose owner is in blocked ∪ possible)
            if upper_sum >= 50:
                possible.add(E)
                changed = True
                continue

            # Possible, identity path (§5.1): a low-confidence link to an entity that is currently BLOCKED or itself AMBIGUOUS.
            # Symmetric: it does not matter which end is entity_id and which is same_as_id.
            # Time-filtered like any other edge: only links active at as_of_date (§2, §7) count.
            if exists IdentityLinkEdge active at as_of_date between E and some d, with d in blocked ∪ possible:
                possible.add(E)
                changed = True
until not changed

status(e) = BLOCKED    if e in blocked
          = AMBIGUOUS  if e in possible
          = CLEAR      otherwise
```

### 5.1 Identity-linked ambiguity

An `IdentityLinkEdge` between two entities `E` and `d` (§2) means entity linking found a candidate match between them but could not confirm or reject it. The relationship is **symmetric**: it says nothing about which of the two is more likely to be the "real" entity underlying the other, only that they might be the same one. Propagation is symmetric too — if either `E` or `d` is currently BLOCKED or AMBIGUOUS, the other cannot be cleared on the strength of a non-match that was never actually confirmed, so it becomes AMBIGUOUS too, until entity linking resolves the identity one way or the other. It does not matter which one is stored as `entity_id` and which as `same_as_id`. Reason code: `LOW_CONFIDENCE_LINK`.

This path is binary, not arithmetic: an unconfirmed identity link isn't a stake, so there is no lower/upper sum for it, and it can only ever push the other entity into `possible`, never into `blocked`. A confirmed identity doesn't go through this path at all — entity linking merges `E` and `d` into one entity before this algorithm runs, so a real designation match is caught by the ordinary `blocked` seed, not by §5.1.

This path is date-scoped in two ways: the link itself is filtered to `as_of_date` via `first_seen_date` (§2, §7) before propagation runs, exactly like an `OwnershipEdge`, and additionally the status of the other end (`blocked ∪ possible`) is already computed against the same `as_of_date`, so time-consistency holds on both sides.

**Scope:** `IdentityLinkEdge` only ever comes from `entity_linking.md` tasks 1–2 — identity uncertainty *within the graph itself* (e.g. two Companies House officer records that might be the same person, or an OpenSanctions entity that might be a GLEIF entity). It is never populated from task 3 (matching a live screened customer to the graph); that uncertainty is handled entirely by `l1_agent.md` §2.1's routing table, which reads this algorithm's output (the matched entity's status) but never feeds back into it. This keeps the two kinds of "we're not sure who this is" cleanly separated: uncertainty about the graph's own structure propagates through ownership; uncertainty about who a customer is does not.

Rules:

1. **Threshold is inclusive:** "50 percent or more", so `>= 50`.
2. **Only BLOCKED owners can make something BLOCKED.** AMBIGUOUS owners, and unconfirmed identity links, count only toward the *possible* test. Neither can compound into a false BLOCKED.
3. **Ambiguity propagates downstream as ambiguity**, through both paths. If an AMBIGUOUS company owns 50% or more of another company, or an entity has an unresolved identity link to a BLOCKED or AMBIGUOUS entity, the result is AMBIGUOUS too. Otherwise a possibly-blocked chain would silently produce CLEAR results, which is a false-negative risk.
4. **Monotonic within a run.** Both sets only ever grow, so status only moves CLEAR → AMBIGUOUS → BLOCKED within one as-of-date run, never backward. A different `as_of_date` is a fresh run (§7), not a reversal.
5. **Termination.** Both sets only grow and the entity set is finite, so the loop reaches a fixed point within `2N` passes (`N` = number of entities), even when the ownership graph contains cycles or the identity-link graph contains loops.

### 5.2 Reported sums are capped at 100

The `lower_sum` / `upper_sum_blocked` values used internally in §5's pseudocode to decide BLOCKED/AMBIGUOUS status are used **uncapped**, exactly as computed — capping them there would never change a `>= 50` comparison, since `min(100, x) >= 50` iff `x >= 50` for any non-negative `x`. But the *reported* `blocked_owner_sum` in §9 is capped: both components, `min(100, lower_sum)` and `min(100, upper_sum_blocked)`, so a caller never sees a percentage figure above 100. `possible_owner_sum` was already capped via `min(100, ...)` in the §5 pseudocode.

**`OWNERSHIP_OVER_100`:** when the *uncapped* sum of `stake_lower` across **all** active edges into an entity — regardless of whether each owner is BLOCKED, AMBIGUOUS, or CLEAR — exceeds 100, add reason code `OWNERSHIP_OVER_100` to that entity's output. This is a data-quality flag only, for conflicting or unreconciled ownership claims (e.g. two independently-reported owners each claiming a large stake in the same entity); it never changes `status`.

## 6. Worked examples

D = designated. All edges active unless stated. Every row is a required unit test.

### 6.1 The cascade (the §3 trap)

A is designated. A owns 60% of B. B owns 50% of C.

| Pass | blocked | Reasoning |
|---|---|---|
| 0 | {A} | seed |
| 1 | {A, B} | B: blocked-owner lower sum = 60 ≥ 50 |
| 2 | {A, B, C} | C: blocked-owner lower sum = 50 ≥ 50 (B is now blocked) |
| 3 | {A, B, C} | no change → fixed point |

**Result: C is BLOCKED.** The multiplied approach (`0.6 × 0.5 = 30%`) would wrongly clear C.

### 6.2 Full example table

| # | Setup | Expected | Why |
|---|---|---|---|
| 1 | D owns 60% of A | A: BLOCKED | Single owner ≥ 50% |
| 2 | D owns 50% of A | A: BLOCKED | Threshold is inclusive |
| 3 | D owns 49% of A | A: CLEAR | Upper sum 49 < 50 |
| 4 | D owns 60% of A; A owns 50% of B | B: BLOCKED | Cascade (§6.1) |
| 5 | D1 owns 25%, D2 owns 25% of A | A: BLOCKED | Blocked owners' stakes are summed |
| 6 | D owns Band A of X | X: AMBIGUOUS | Lower 25, upper 50: true stake could be 26% or 50% |
| 7 | D1 and D2 each own Band A of X | X: BLOCKED | Lower sum 50 ≥ 50 (true minimum is above 50) |
| 8 | D owns Band B of X | X: BLOCKED | Lower 50 ≥ 50 |
| 9 | D owns Band C of X | X: BLOCKED | Lower 75 ≥ 50 |
| 10 | D owns Band A of X; X owns 100% of Y | X: AMBIGUOUS, Y: AMBIGUOUS | Ambiguity propagates; Y is never CLEAR or BLOCKED |
| 11 | D owns X, stake unknown | X: AMBIGUOUS, `UNKNOWN_STAKE` | Range [0, 100] |
| 12 | Source 1: D owns 60% of X; Source 2: D owns 30% of X | X: AMBIGUOUS, `STAKE_CONFLICT` | Hull [30, 60] straddles 50 |
| 13 | Source 1: D owns 40% of X; Source 2: D owns Band A of X | X: CLEAR | Intersection [40, 40]; 40 < 50 |
| 14 | D owns 60% of A; A owns 60% of B; B owns 60% of A | A, B: BLOCKED; terminates | Cycle safety |
| 15 | D owns 60% of A, edge starts 2025-01-01; as_of 2024-12-31 | A: CLEAR | Edge not yet active |
| 16 | D designated 2026-03-01, delisted 2026-06-01; D owns 60% of A; as_of 2026-06-01 | A: CLEAR | Half-open end (§7) |
| 17 | D is designated and has a control-only PSC entry for X, no shares | X: CLEAR, `CONTROL_ONLY_LINK`; `control_links` contains the D→X control edge | Control is out of scope for status (§10), but D (the controller) is BLOCKED, so this control edge is reportable evidence (§9, §9.1) |
| 18 | D's UK PSC record for X reports a stake of exactly 25% or below (no band assigned) | No edge; X: CLEAR | Below PSC reporting threshold (§4.2) — this rule applies only to PSC-band-sourced data, not to exact-stake data from other sources (contrast row 5, where an exact 25% stake from a non-PSC source does get an edge) |
| 19 | E has an `IdentityLinkEdge` to D, first seen before `as_of_date` (designated, so D is in `blocked`); E has no ownership edges at all | E: AMBIGUOUS, `LOW_CONFIDENCE_LINK` | Identity path (§5.1); works for persons with no ownership edges, not just companies |
| 20 | E has an `IdentityLinkEdge` to X, first seen before `as_of_date`, where X is AMBIGUOUS (not BLOCKED) via a Band A ownership edge from a blocked owner | E: AMBIGUOUS | Identity path checks `blocked ∪ possible`, consistent with the ownership path's own `blocked ∪ possible` reachability test |
| 21 | E has an `IdentityLinkEdge` to C; C is not designated and has no owners | E: CLEAR, C: CLEAR | Neither end is in `blocked ∪ possible`, so the link has no effect |
| 22 | D is designated and owns 60% of B; E has an `IdentityLinkEdge` to B | B: BLOCKED, E: AMBIGUOUS | B is BLOCKED via ordinary ownership; the identity link can only push the *other* end (E) into `possible`, never into `blocked` — symmetry does not let AMBIGUOUS-via-identity compound into a false BLOCKED |
| 23 | As row 19 (E linked to D, designated), plus E owns 100% of X | E: AMBIGUOUS, X: AMBIGUOUS | E's AMBIGUOUS status from the identity path propagates downstream through ordinary ownership like any AMBIGUOUS owner (§5.1 rule 3) |
| 24 | As row 19, but the `IdentityLinkEdge` is stored with `entity_id = D`, `same_as_id = E` (fields reversed) | Same as row 19: E: AMBIGUOUS, `LOW_CONFIDENCE_LINK` | The edge is symmetric (§2, §5.1); which field holds which id is not meaningful |
| 25 | As row 19, but the `IdentityLinkEdge`'s `first_seen_date` is after `as_of_date` | E: CLEAR | The link is not yet active at `as_of_date` (§7 half-open filtering applies to `IdentityLinkEdge` too) |
| 26 | D1 and D2 are both designated; D1 owns 70% of X, D2 owns 80% of X | X: BLOCKED, `OWNERSHIP_OVER_100`; `blocked_owner_sum = (100, 100)`, `possible_owner_sum = 100` | Uncapped `lower_sum = 150 >= 50` decides BLOCKED (§5); the raw total across both owners (150) exceeds 100, so `OWNERSHIP_OVER_100` is flagged (§5.2), but every *reported* sum is capped at 100 |
| 27 | D designated; two sources for D→X: one unknown (`[0, 100]`), one Band A (`[25, 50]`) | X: AMBIGUOUS, `BAND_UNCERTAINTY`, not `UNKNOWN_STAKE` | Overlapping ranges → intersection `[25, 50]` (§4.4) — the unknown source's `[0, 100]` contributes nothing once intersected with the known band, so the combined edge's uncertainty kind is `band`, not `unknown`, even though one contributing source was itself unknown |
| 28 | D designated; two sources for D→X: one unknown (`[0, 100]`), one exact 40% | X: CLEAR | Overlapping ranges → intersection `[40, 40]` (§4.4); the combined edge's uncertainty kind is `none` (a pinned single value), not `unknown` — 40 < 50 so X is CLEAR, and neither `UNKNOWN_STAKE` nor `BAND_UNCERTAINTY` would apply if it were AMBIGUOUS |
| 29 | D designated; two sources for D→X: Band A (stored `[25, 50]`) and Band B (stored `[50, 75]`) | X: AMBIGUOUS, `STAKE_CONFLICT` | Stored ranges touch at 50, but 50 is open in Band B's true range `(50, 75)` — not a true overlap; hull `[25, 75]` (§4.4) |
| 30 | D designated; two sources for D→X: exact 25% and Band A (stored `[25, 50]`) | X: AMBIGUOUS, `STAKE_CONFLICT` | Stored ranges touch at 25, but 25 is open in Band A's true range `(25, 50]` — not a true overlap; hull `[25, 50]` |
| 31 | D designated; two sources for D→X: exact 50% and Band A (stored `[25, 50]`) | X: BLOCKED | Stored ranges touch at 50, and 50 is closed (included) in Band A's true range `(25, 50]` — a true overlap; pinned `[50, 50]` |
| 32 | C is not designated (CLEAR) and has a control-only PSC entry for X, no shares | X: CLEAR; no `CONTROL_ONLY_LINK`; `control_links` is empty | The controller (C) is CLEAR, not BLOCKED or AMBIGUOUS, so the control edge produces neither the reason code nor a `control_links` entry (§9, §9.1) — contrast row 17, where the controller is BLOCKED |

## 7. Time awareness: half-open validity intervals

An `OwnershipEdge` or designation with `start = s` and `end = e` is **active at date `d`** iff:

```
s <= d < e          # if e is not null
s <= d              # if e is null (open-ended / ongoing)
```

Use half-open `[start, end)` semantics uniformly. An edge that ends on `e` is *not* active on `e` itself, which avoids double-counting at transition dates.

**Missing start dates.** If a source gives no start date, use the record's **first-seen date in the source snapshot** and set `start_date_inferred = True` (reason code `START_DATE_INFERRED`). This backfill is ingestion's responsibility, applied before a fact ever reaches `propagate_blocked()` — the algorithm itself never receives a null start date for a valid fact. If `propagate_blocked()` is called with any `OwnershipEdge`, `ControlEdge`, or active (`designated = True`) `Entity` whose start date is `None`, that is a data-integrity error, not an inactive fact: `propagate_blocked()` must raise a validation error rather than silently treating it as inactive.

**Time consistency.** `propagate_blocked(as_of_date=d)` must first filter edges and designations to those active at `d`, then run propagation fresh on that filtered snapshot. For any designation with `designation_start = s`, a query with `d < s` must never return BLOCKED or AMBIGUOUS because of that designation, for it or anything downstream. Never cache or reuse `blocked` or `possible` sets computed at one date for a query at a different date.

## 8. Effective (multiplied) ownership: risk feature only

Define `effective_ownership(owner, target)` as the sum, over all **simple** directed ownership paths from `owner` to `target` (no entity visited twice) of **length ≤ 5**, of the product of stakes along each path, capped at 100%. Use the range midpoint for banded stakes; skip edges with unknown stakes and report them as `UNKNOWN_STAKE` on the feature.

The simple-path and length limits are required: with ownership cycles, "all paths" is infinite.

This number is a **risk feature and explanation field only** (e.g. "high effective ownership, no blocked-owner path"). It **must never appear in the status decision** (§5). Any code that reads `effective_ownership()` to set `status` is a spec violation.

## 9. Output

`propagate_blocked(as_of_date)` returns, for every entity:

| Field | Content |
|---|---|
| `status` | BLOCKED, AMBIGUOUS, or CLEAR |
| `blocked_owner_sum` | `(lower_sum, upper_sum_blocked)` from blocked owners only (§5), each component capped at 100 for reporting (§5.2) |
| `possible_owner_sum` | `upper_sum` from blocked and ambiguous owners, capped at 100 (§5, §5.2) |
| `evidence` | Why the entity has its status, using the fewest facts that decide it. See §9.2. |
| `control_links` | Active `ControlEdge`s into the entity whose controller (`owner_id`) is BLOCKED or AMBIGUOUS in the final `blocked`/`possible` sets (evidence only) — same filter as reason code `CONTROL_ONLY_LINK` (§9.1); a control edge from a CLEAR controller produces no entry here |
| `effective_ownership` | Feature from §8, per designated root |
| `reason_codes` | See below |
| `as_of_date` | The query date |

**Reason codes:** `DESIGNATED`, `OWNED_BY_BLOCKED`, `AGGREGATE_OWNERSHIP`, `BAND_UNCERTAINTY`, `UNKNOWN_STAKE`, `AMBIGUOUS_OWNER`, `STAKE_CONFLICT`, `START_DATE_INFERRED`, `CONTROL_ONLY_LINK`, `LOW_CONFIDENCE_LINK`, `OWNERSHIP_OVER_100`. Trigger conditions for each are given in §9.1.

### 9.1 Reason code trigger conditions

Each entity's `reason_codes` is the union of every condition below that holds for it, evaluated against the **final** `blocked`/`possible` sets (after §5's fixed point) and the combined edges into that entity (§4.4). More than one code can apply to the same entity.

| Code | Trigger |
|---|---|
| `DESIGNATED` | The entity itself is designated and its designation is active at `as_of_date` (§7). |
| `OWNED_BY_BLOCKED` | The entity's blocked-owner `lower_sum >= 50` (§5) — i.e. it is BLOCKED via the ownership cascade, independent of whether it is also independently `DESIGNATED`. |
| `AGGREGATE_OWNERSHIP` | `OWNED_BY_BLOCKED` also applies (this code presupposes it — the entity is BLOCKED via `lower_sum >= 50` from ownership, not solely via its own designation), **and** no single blocked owner's own combined `stake_lower` reaches 50 alone — i.e. summing multiple blocked owners' stakes was necessary to cross the threshold (§6.2 row 5). Never set for an entity BLOCKED solely via its own designation with no incoming ownership edge reaching the threshold, and never set when one blocked owner's stake alone is `>= 50`, even if other, smaller blocked owners also hold edges into the same entity. |
| `BAND_UNCERTAINTY` | The entity is AMBIGUOUS, and at least one combined edge from a **blocked** owner has uncertainty kind `band` (§4.4). Never set from an `unknown` or `conflict` edge, even though both can also have `stake_lower < stake_upper`. |
| `UNKNOWN_STAKE` | The entity is AMBIGUOUS, and at least one combined edge from a **blocked** owner has uncertainty kind `unknown` (§4.4). |
| `AMBIGUOUS_OWNER` | The entity is AMBIGUOUS, and at least one contributing owner is itself AMBIGUOUS (not BLOCKED) — ambiguity propagated downstream via an already-ambiguous owner (§5.1 rule 3, row 10). |
| `STAKE_CONFLICT` | At least one combined edge into the entity has uncertainty kind `conflict` (§4.4), regardless of status or of the owner's own status. |
| `START_DATE_INFERRED` | At least one active edge into the entity has `start_date_inferred = True` (§7), regardless of status. |
| `CONTROL_ONLY_LINK` | At least one active `ControlEdge` into the entity exists (§4.5, §10) whose controller (`owner_id`) is BLOCKED or AMBIGUOUS in the final `blocked`/`possible` sets — the same filter that populates `control_links` (§9). A control edge from a CLEAR controller triggers neither this code nor a `control_links` entry. |
| `LOW_CONFIDENCE_LINK` | The entity is AMBIGUOUS, and it has an active `IdentityLinkEdge` (§5.1) to an entity in `blocked ∪ possible`. |
| `OWNERSHIP_OVER_100` | The uncapped sum of `stake_lower` across **all** active combined edges into the entity, regardless of owner status, exceeds 100 (§5.2). |

`BAND_UNCERTAINTY`, `UNKNOWN_STAKE`, and `STAKE_CONFLICT` are mutually exclusive **per edge** (an edge's uncertainty kind is exactly one of `band`/`unknown`/`conflict`/`none`, §4.4), but a single entity can still carry more than one of these codes if it has multiple incoming edges of different kinds.

### 9.2 Evidence

**Governs:** the `evidence` field of each `propagate_blocked()` result (replaces the `evidence_paths` placeholder in §9). Effective ownership (§8) remains separate and out of scope for this section.

Evidence explains **why an entity has its status**, using the fewest facts that decide it. It does not list every ownership path: in dense or cyclic graphs that set can grow without bound, and most paths play no part in the decision.

#### 9.2.1 Fact IDs

Every input fact has a stable, content-derived ID, computed from the fact's fields alone:

| Fact type | ID format |
|---|---|
| Designation | `des:<entity_id>:<designation_start>:<h8>` |
| Ownership edge | `own:<source>:<owner_id>:<owned_id>:<start_date>:<h8>` |
| Identity link | `idl:<id_a>:<id_b>:<first_seen_date>:<h8>`, with `id_a`, `id_b` sorted (the link is unordered, §2) |
| Control edge | `ctl:<source>:<owner_id>:<owned_id>:<start_date>:<h8>` |

`<h8>` is the first 8 hex characters of the SHA-256 of all the fact's fields in a fixed order. It guarantees distinct facts get distinct IDs, including same-source duplicates (§4.4). The same fact always gets the same ID, on every run and in any input order.

#### 9.2.2 Justification rank

Each BLOCKED or AMBIGUOUS entity has a **rank**: the step at which it first entered its final status, computed with **synchronous** passes (every entity's status in step *k* is computed from the statuses at the end of step *k − 1*). Ranks are therefore independent of processing order.

- Designated entities: blocked-rank 0.
- BLOCKED entities: blocked-rank = the step at which they entered `blocked`.
- AMBIGUOUS entities: ambiguous-rank = the step at which they entered `possible`.

Ranks exist to make evidence acyclic by construction (§9.2.4), including when ownership forms a cycle. The implementation may compute statuses however it likes, but ranks must match the synchronous definition.

#### 9.2.3 Evidence content

```
Evidence:
  entity_id
  status            BLOCKED | AMBIGUOUS | CLEAR
  kind              DESIGNATED | OWNERSHIP | IDENTITY_LINK | NONE
  rank              int, or null for CLEAR
  fact_ids          list of fact IDs cited directly by this evidence
  steps             list of EvidenceStep
  depends_on        list of entity IDs whose evidence this evidence references

EvidenceStep:
  owner_id          the owner (or identity-link partner) cited
  owner_status      BLOCKED | AMBIGUOUS (final status)
  stake_range       (lower, upper) of the combined edge (§4.4); null for identity links
  uncertainty_kind  none | band | unknown | conflict (§4.4); null for identity links
  source_fact_ids   ALL source fact IDs combined into this edge (§4.4), or the link's fact ID
```

Evidence **references** each owner's own evidence through `depends_on`; it never copies the owner's chain. A helper `explain(entity_id)` may expand references into a full chain for display; that expansion is derived, not stored.

#### 9.2.4 Selection rules

**Designated.** `kind = DESIGNATED`, `rank = 0`, `fact_ids` = the designation fact only, `steps` empty. This applies even if the entity is also owned by blocked owners.

**BLOCKED (not designated).** `kind = OWNERSHIP`. Eligible owners: BLOCKED owners with a **smaller blocked-rank** than this entity. Sort eligible steps by `stake_range.lower` descending, then `owner_id` ascending, then smallest source fact ID. Take steps in that order until the running sum of lower bounds reaches 50. This cites the fewest owners that decide the outcome.

**AMBIGUOUS.** Eligible contributors: BLOCKED owners (any rank), and AMBIGUOUS owners or identity-link partners with a **smaller ambiguous-rank** than this entity.
- If eligible ownership steps can reach an upper-bound sum of 50: `kind = OWNERSHIP`. Sort by `stake_range.upper` descending, then `owner_id`, then smallest source fact ID, and take steps until the upper-bound sum reaches 50.
- Otherwise: `kind = IDENTITY_LINK`. Cite the eligible identity link with the smallest fact ID, with the partner as the step's `owner_id`.

**CLEAR.** `kind = NONE`, `rank = null`, `fact_ids`, `steps`, and `depends_on` empty.

**Every step cites all source facts of its combined edge** (§4.4), never a subset. Citing one source of a conflict would change the combined range, and with it the status.

#### 9.2.5 Worked examples

D, D1, D2 are designated. All facts active unless stated. Each row is a required unit test.

| # | Setup | Expected evidence |
|---|---|---|
| EV1 | D designated | D: `DESIGNATED`, rank 0, fact_ids = [D's designation], no steps |
| EV2 | D owns 60% of A; A owns 50% of B (the §3 cascade) | A: rank 1, one step (D, [60,60]). B: rank 2, one step (A, [50,50]), depends_on [A]. B does **not** cite D directly. |
| EV3 | D1 owns 25%, D2 owns 25% of A | A: two steps (D1 and D2), since neither alone reaches 50 |
| EV4 | D1 owns 60%, D2 owns 10% of A | A: one step (D1) only; D2 is not needed |
| EV5 | D1 owns 60%, D2 owns 60% of A | A: one step, D1 (tie broken by `owner_id`) |
| EV6 | D owns Band A of X | X: AMBIGUOUS, `OWNERSHIP`, one step (D, [25,50], kind band) |
| EV7 | D owns Band A of X; X owns 100% of Y | Y: AMBIGUOUS, one step (X, owner_status AMBIGUOUS), depends_on [X]; X's rank < Y's rank |
| EV8 | D owns 60% of A; A owns 60% of B; B owns 60% of A (cycle) | A: rank 1, cites D. B: rank 2, cites A. A's evidence never cites B. |
| EV9 | Source 1: D owns 60% of X; source 2: D owns 30% of X | X: AMBIGUOUS, one step, range [30,60], kind conflict, source_fact_ids = **both** source facts |
| EV10 | E linked to designated D, no ownership edges | E: AMBIGUOUS, `IDENTITY_LINK`, one step (owner D), source_fact_ids = [the link's fact ID], depends_on [D] |
| EV11 | Entity with no risk | `NONE`, rank null, everything empty |

#### 9.2.6 Required properties

Property tests, over the same generated graphs as §11:

1. **Sufficiency.** For every BLOCKED or AMBIGUOUS entity *x*, collect the facts cited by *x*'s evidence and, recursively, by every entity in its `depends_on` closure. Running `propagate_blocked()` on only those facts, at the same `as_of_date`, gives *x* the **same status**.
2. **Necessity (BLOCKED).** For every non-designated BLOCKED entity, removing any single step drops the sum of cited lower bounds below 50.
3. **Valid citations.** Every cited fact ID exists in the input and is active at `as_of_date`.
4. **Acyclic.** The `depends_on` graph contains no cycle (shared dependencies, i.e. diamonds, are allowed). BLOCKED evidence cites only BLOCKED owners of smaller blocked-rank; AMBIGUOUS evidence cites BLOCKED owners, or AMBIGUOUS contributors of smaller ambiguous-rank. Every chain ends at a designation.
5. **Designated and CLEAR.** Designated entities have exactly their designation fact; CLEAR entities have empty evidence.
6. **Order independence.** Shuffling input order never changes any evidence, including fact IDs, step order, and ranks.
7. **Fact ID stability.** The same fact always gets the same ID; distinct facts always get distinct IDs.

## 10. Non-goals (out of scope for this version)

- **The UK's own ownership-or-control test.** The UK regime uses its own test, which differs from the OFAC rule (including in its threshold wording and its treatment of control). Verify against OFSI guidance before making any claim about it. This project applies only the simplified OFAC-style rule, including to UK data.
- Non-ownership "significant control" (voting rights, right to appoint or remove directors) as a blocking condition.
- Weighting blocked owners by entity-linking confidence. All blocked owners are treated as equally certain here; linking confidence is handled in `entity_linking.md`.
- Licenses, exemptions, and wind-down periods.
- Other jurisdictions' rules.

Do not implement anything on this list without updating this spec first.

## 11. Required property tests (`tests/test_ownership_properties.py`)

Using `hypothesis`, the following must hold for arbitrary generated graphs, including graphs with cycles, unknown stakes, conflicting edges, and randomized node/edge insertion order:

1. **Monotonicity:** adding a designation, adding an edge from a blocked owner, adding an `IdentityLinkEdge` to an entity that is BLOCKED or AMBIGUOUS, or increasing any edge's `stake_lower` or `stake_upper` never moves any entity from BLOCKED to AMBIGUOUS or CLEAR, nor from AMBIGUOUS to CLEAR. This is stated over the combined edge after §4.4 conflict resolution. Raising one source's raw bound when multiple active sources already cover the same `owner_id`→`owned_id` pair can change which §4.4 rule applies (intersection vs. hull) and is not required to be monotone in isolation; adding a *new* edge to a pair that already has an active edge has the same issue, since it also changes the combination inputs. Both are excluded from this property; monotonicity is tested on single-source pairs and on pairs with no pre-existing active edge.
2. **Range validity:** for every entity, `0 <= blocked_lower <= blocked_upper <= possible_upper <= 100` (all reported sums are capped at 100, §5.2).
3. **Order independence:** shuffling node and edge insertion order never changes any entity's final status.
4. **Cycle safety:** propagation terminates within `2N` passes on graphs with ownership cycles.
5. **Time consistency:** for any designation with `designation_start = s`, `propagate_blocked(as_of_date=d)` with `d < s` never returns BLOCKED or AMBIGUOUS because of that designation, for it or anything downstream.
6. **Designated are blocked:** every entity designated and active at `as_of_date` is BLOCKED.
7. **No designations, no risk:** with zero active designations, every entity is CLEAR.
8. **Idempotence:** running `propagate_blocked` twice on the same snapshot gives identical results.
9. **Only blocked owners confirm:** deleting every edge whose owner is AMBIGUOUS never changes any BLOCKED result.
10. **Identity-linked ambiguity:** an entity with an `IdentityLinkEdge` to another entity that is BLOCKED or AMBIGUOUS is never CLEAR, regardless of whether it has any ownership edges at all.

These must be genuine property tests over randomly generated graphs (via `hypothesis` strategies), not example tests relabeled. Example tests (§6) check specific cases; property tests check that the rules hold for arbitrary inputs.