# Ownership & Blocking Rules

**Status:** complete (v0.6: added §5.2 — reported sums capped at 100, new `OWNERSHIP_OVER_100` reason code; v0.5: §11 item 1 scoped monotonicity to the combined edge after §4.4 conflict resolution; v0.4: `IdentityLinkEdge` made symmetric and given a `first_seen_date` with half-open time validity; v0.3: identity-link ambiguity path added, per `entity_linking.md` §2.4)
**Governs:** `src/screen/ownership.py` (`propagate_blocked()`), `tests/test_ownership_examples.py`, `tests/test_ownership_properties.py`

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
| 17 | D has a control-only PSC entry for X, no shares | X: CLEAR, `CONTROL_ONLY_LINK` | Control is out of scope (§10) |
| 18 | D's UK PSC record for X reports a stake of exactly 25% or below (no band assigned) | No edge; X: CLEAR | Below PSC reporting threshold (§4.2) — this rule applies only to PSC-band-sourced data, not to exact-stake data from other sources (contrast row 5, where an exact 25% stake from a non-PSC source does get an edge) |
| 19 | E has an `IdentityLinkEdge` to D, first seen before `as_of_date` (designated, so D is in `blocked`); E has no ownership edges at all | E: AMBIGUOUS, `LOW_CONFIDENCE_LINK` | Identity path (§5.1); works for persons with no ownership edges, not just companies |
| 20 | E has an `IdentityLinkEdge` to X, first seen before `as_of_date`, where X is AMBIGUOUS (not BLOCKED) via a Band A ownership edge from a blocked owner | E: AMBIGUOUS | Identity path checks `blocked ∪ possible`, consistent with the ownership path's own `blocked ∪ possible` reachability test |
| 21 | E has an `IdentityLinkEdge` to C; C is not designated and has no owners | E: CLEAR, C: CLEAR | Neither end is in `blocked ∪ possible`, so the link has no effect |
| 22 | D is designated and owns 60% of B; E has an `IdentityLinkEdge` to B | B: BLOCKED, E: AMBIGUOUS | B is BLOCKED via ordinary ownership; the identity link can only push the *other* end (E) into `possible`, never into `blocked` — symmetry does not let AMBIGUOUS-via-identity compound into a false BLOCKED |
| 23 | As row 19 (E linked to D, designated), plus E owns 100% of X | E: AMBIGUOUS, X: AMBIGUOUS | E's AMBIGUOUS status from the identity path propagates downstream through ordinary ownership like any AMBIGUOUS owner (§5.1 rule 3) |
| 24 | As row 19, but the `IdentityLinkEdge` is stored with `entity_id = D`, `same_as_id = E` (fields reversed) | Same as row 19: E: AMBIGUOUS, `LOW_CONFIDENCE_LINK` | The edge is symmetric (§2, §5.1); which field holds which id is not meaningful |
| 25 | As row 19, but the `IdentityLinkEdge`'s `first_seen_date` is after `as_of_date` | E: CLEAR | The link is not yet active at `as_of_date` (§7 half-open filtering applies to `IdentityLinkEdge` too) |
| 26 | D1 and D2 are both designated; D1 owns 70% of X, D2 owns 80% of X | X: BLOCKED, `OWNERSHIP_OVER_100`; `blocked_owner_sum = (100, 100)`, `possible_owner_sum = 100` | Uncapped `lower_sum = 150 >= 50` decides BLOCKED (§5); the raw total across both owners (150) exceeds 100, so `OWNERSHIP_OVER_100` is flagged (§5.2), but every *reported* sum is capped at 100 |

## 7. Time awareness: half-open validity intervals

An `OwnershipEdge` or designation with `start = s` and `end = e` is **active at date `d`** iff:

```
s <= d < e          # if e is not null
s <= d              # if e is null (open-ended / ongoing)
```

Use half-open `[start, end)` semantics uniformly. An edge that ends on `e` is *not* active on `e` itself, which avoids double-counting at transition dates.

**Missing start dates.** If a source gives no start date, use the record's **first-seen date in the source snapshot** and set `start_date_inferred = True` (reason code `START_DATE_INFERRED`).

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
| `evidence_paths` | For BLOCKED: each chain of blocked owners back to a designated entity, with stake range, source, and dates on every edge. For AMBIGUOUS: the chains that make the threshold reachable, plus any `IdentityLinkEdge` and the status of its target, for identity-linked ambiguity (§5.1). |
| `control_links` | Control-only relationships to blocked or ambiguous entities (evidence only) |
| `effective_ownership` | Feature from §8, per designated root |
| `reason_codes` | See below |
| `as_of_date` | The query date |

**Reason codes:** `DESIGNATED`, `OWNED_BY_BLOCKED`, `AGGREGATE_OWNERSHIP`, `BAND_UNCERTAINTY`, `UNKNOWN_STAKE`, `AMBIGUOUS_OWNER`, `STAKE_CONFLICT`, `START_DATE_INFERRED`, `CONTROL_ONLY_LINK`, `LOW_CONFIDENCE_LINK`, `OWNERSHIP_OVER_100`.

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