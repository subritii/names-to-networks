# Decisions log

Running log of interpretation/implementation decisions not settled verbatim by a spec. Dated, with reasoning.

## 2026-09-23 — `propagate_blocked()` signature and `ControlEdge` type (ownership_rules.md)

`ownership_rules.md` §2 defines `Entity`, `OwnershipEdge`, and `IdentityLinkEdge` but does not specify
`propagate_blocked()`'s exact call signature, nor a data type for the "control relationship" mentioned
in §4.5 (a PSC entry recorded as "significant influence or control" with no ownership share) that §9
requires as evidence output (`control_links`).

Decision, for `src/screen/ownership.py`:

- Added a `ControlEdge` dataclass (`owner_id`, `owned_id`, `source`, `start_date`, `end_date`) mirroring
  `OwnershipEdge` minus the stake fields, since §4.5 explicitly says these are "stored" separately from
  ownership edges but doesn't name a type.
- `propagate_blocked(entities, ownership_edges, identity_link_edges=(), control_edges=(), as_of_date=None)`
  returns `dict[entity_id, OwnershipResult]`, where `OwnershipResult` carries the §9 output fields
  (`status`, `blocked_owner_sum`, `possible_owner_sum`, `evidence_paths`, `control_links`,
  `effective_ownership`, `reason_codes`, `as_of_date`).

This is scaffolding only (call signature / data shape), not a rule interpretation — none of the settled
blocking logic in §3–§8 was inferred or guessed. `propagate_blocked()` itself is currently a stub that
raises `NotImplementedError` (see `tests/test_ownership_examples.py`, `tests/test_ownership_properties.py`
still to be written).

## 2026-09-23 — Row 18 (PSC <=25% has no edge) is a documentation-only test

`ownership_rules.md` §6.2 row 18 ("D's UK PSC record for X reports a stake of exactly 25% or below
(no band assigned) -> no edge; X: CLEAR") is really a statement about the PSC *ingester* (`src/ingest/`
and/or `src/resolve/`): whether it assigns a band/edge for a <=25% stake at all. `propagate_blocked()`
itself has no PSC-percentage-to-band logic and never sees a raw percentage — it only ever sees edges
that already exist. So `test_row_18_...` in `tests/test_ownership_examples.py` can only assert the
trivial half of the rule (no edge -> CLEAR), which `propagate_blocked()` does need to guarantee; the
substantive half (that the ingester correctly omits the edge below the threshold) belongs in a future
`tests/test_ingest_psc.py` once ingestion is implemented. Marked as documentation-only in the test's
comment so this isn't mistaken for full coverage of row 18.

## 2026-09-23 — `IdentityLinkEdge` made symmetric

Spec change (v0.4) to `ownership_rules.md` §2 and §5.1, at the user's direction:

`IdentityLinkEdge(entity_id, same_as_id)` previously propagated ambiguity in one direction only —
`IdentityLinkEdge(E, d)` pushed `E` into `possible` if `d in blocked ∪ possible`, but not the reverse.
This was a latent asymmetry: entity linking has no way to know, when it records a Flag-band candidate
match, which of the two ids will end up in `entity_id` vs. `same_as_id`, so the direction of the edge
carried no real meaning, yet the algorithm was treating it as if it did.

Decision: the edge is now explicitly symmetric. If either end is in `blocked ∪ possible`, the other end
is added to `possible`. §2 states that `entity_id`/`same_as_id` name an unordered pair; §5.1 states the
symmetry and its consequence (an entity can be pushed into `possible` by an unconfirmed identity link
regardless of which field names it). §5's algorithm pseudocode was also updated for consistency — the
user's instruction named only §2 and §5.1, but leaving §5's pseudocode with the old one-directional
check would have made the spec self-contradictory, since §5 is where the check is actually executed.

## 2026-09-23 — §11 item 1 (monotonicity) scoped to the combined edge, at the user's direction

While designing `tests/test_ownership_properties.py`'s monotonicity property, found a concrete
counterexample to §11 item 1 as originally worded ("increasing any edge's `stake_lower` or
`stake_upper` never moves any entity ... toward CLEAR"): if source A reports 40% and source B reports
`[45, 100]` for the same `owner_id`→`owned_id` pair, §4.4 sees non-overlapping ranges and takes the hull
`[40, 100]` (straddles 50 → AMBIGUOUS if the owner is blocked). Raising source A's bound from 40 to 45
makes the ranges overlap, so §4.4 switches to the *intersection* `[45, 45]`, whose upper bound (45) is
below the 50% threshold — so the owned entity goes AMBIGUOUS → CLEAR even though a bound was raised. The
property was
never false about a *single* combined edge; it silently assumed raising a raw per-source bound can't
change which §4.4 combination rule fires, which is wrong whenever a pair has more than one active
source.

Decision (user-directed): amended §11 item 1 to state the monotonicity guarantee over the **combined**
edge after §4.4 resolution, and excluded two constructions from the property test: adding a new edge to
a pair that already has an active edge, and raising a bound on a pair with more than one active source.
`tests/test_ownership_properties.py`'s monotonicity test only adds edges to previously-edge-free pairs
and only raises bounds on single-source pairs. This is a spec correction with a concrete counterexample,
not an unguided inference — see `ownership_rules.md` §11 item 1 (v0.5).

## 2026-09-23 — Coverage evidence for the property-test strategy uses `hypothesis.event()`, not a script

The property-test strategy (`graphs()` in `tests/test_ownership_properties.py`) needs to guarantee that
every feature named in the task (cycles, all three PSC bands, unknown stakes, duplicate-source edges,
control edges, identity links, varied dates) actually appears across a run, not just in theory. Original
plan was to verify this once with a throwaway sampling script and discard it. At the user's direction,
replaced that with `_record_structural_events()`/`_record_outcome_events()` helpers inside the test file
itself, calling `hypothesis.event()` for each structural feature and each notable outcome (AMBIGUOUS
present, a non-designated entity going BLOCKED via ownership, `STAKE_CONFLICT`, an identity link
actually firing `LOW_CONFIDENCE_LINK`). This keeps the coverage evidence live and re-checkable via
`HYPOTHESIS_PROFILE=ci pytest tests/test_ownership_properties.py --hypothesis-show-statistics` instead
of a one-off artifact that could go stale.

## 2026-09-23 — Property 12 (no-effect identity links) uses a constructed case, not `assume()`

To test "removing an `IdentityLinkEdge` whose both ends are CLEAR never changes any result," the case
needs at least one identity link between two entities that are actually CLEAR — which, for a link drawn
from the general graph strategy, is only known after running `propagate_blocked`, tempting an
`assume()` filter on the computed result. At the user's direction, instead the test explicitly
constructs two fresh, isolated, undesignated entities with no ownership edges to or from anyone, linked
by one `IdentityLinkEdge` — CLEAR on both ends by construction, independent of whatever the rest of the
drawn graph looks like. No `assume()` needed for this property.

## 2026-09-23 — Two extra property tests added; hypothesis dev/ci profiles

At the user's direction, added two properties beyond `ownership_rules.md` §11's ten plus the two
identity-link properties already planned (symmetry, no-effect links):
- **General time consistency:** removing every fact (designation, ownership edge, identity link,
  control edge) whose earliest-valid date is after the query date `d` changes nothing — a broader check
  of the §7 filtering step across all fact types, complementing §11 item 5's single-designation case.
- **Range validity, extended:** `blocked_lower <= blocked_upper <= possible_upper <= 100` for every
  entity, folded into the existing range-validity property (§11 item 2) rather than a new one.

Also registered two hypothesis profiles in `tests/test_ownership_properties.py` — `dev`
(`max_examples=200`) and `ci` (`max_examples=1000`), both `derandomize=True` for reproducibility,
selected via `HYPOTHESIS_PROFILE` env var, defaulting to `dev`. Chosen so local runs stay fast while CI
can opt into deeper search.

## 2026-09-23 — §5/§9 reported sums capped at 100; new `OWNERSHIP_OVER_100` reason code

Writing the range-validity property test (`0 <= blocked_lower <= blocked_upper <= possible_upper <=
100`) surfaced a real spec inconsistency: `blocked_owner_sum`'s upper component (`upper_sum_blocked`,
§5) was explicitly *uncapped* ("never used in the decision"), while `possible_owner_sum` was capped via
`min(100, ...)`. Two independently-blocked owners each reporting a large stake in the same target (e.g.
70% and 80% — not realistic for a real cap table, but not forbidden by the data model) makes
`blocked_upper = 150` while `possible_upper = min(100, >=150) = 100`, breaking the chain the user wanted
tested.

Decision (user-directed): amended `ownership_rules.md` with a new §5.2 ("Reported sums are capped at
100"):
- The internal decision variables (`lower_sum`, `upper_sum_blocked`) stay uncapped in the §5 pseudocode
  — capping them there is a no-op for the `>= 50` comparisons, since `min(100, x) >= 50` iff `x >= 50`.
- The *reported* `blocked_owner_sum` (§9) is now capped in both components: `min(100, lower_sum)`,
  `min(100, upper_sum_blocked)`. `possible_owner_sum` was already capped.
- New reason code `OWNERSHIP_OVER_100`: set when the *uncapped* sum of `stake_lower` across all active
  owners of an entity (regardless of each owner's own status) exceeds 100 — a data-quality flag only,
  never affecting `status`.
- §11 item 2 (range validity) updated to state the full capped chain unconditionally.
- Added worked-example row 26 (§6.2): two designated owners at 70%/80% of the same target → BLOCKED,
  `OWNERSHIP_OVER_100`, `blocked_owner_sum=(100,100)`, `possible_owner_sum=100`.

`tests/test_ownership_properties.py`'s property 2 (range validity) now tests the full chain
unconditionally, with no special-casing for over-100 sums, since the spec now guarantees it holds by
capping the reported values rather than by constraining the test strategy.

## 2026-09-23 — `docs/specs/` and `docs/decisions.md` are tracked and public by design

Before pushing the ownership property-test work, found that `CLAUDE.md` claimed `docs/specs/` and this
file are git-ignored and local-only ("deliberately excluded from the public GitHub repo"), but neither
was ever actually in `.gitignore`, and both were already committed and pushed to the public
`subritii/names-to-networks` GitHub repo in an earlier commit (`74800d5`).

Decision, at the user's direction: this is not a leak to fix — tracking `docs/specs/` and
`docs/decisions.md` publicly is intentional, kept for version history and as portfolio evidence of the
project's reasoning. `CLAUDE.md` was out of date, not the repo. Updated `CLAUDE.md`'s "The specs are
binding" section and "Docs and decisions" section to state both are tracked and public by design, and
removed the incorrect git-ignored/local-only claims. No change to `.gitignore` or repo history.

## 2026-09-23 — `propagate_blocked()` implemented: reason-code attribution not pinned down by §6/§9

Implemented the algorithm itself (statuses, §4.4 combination, the §5 fixed-point loop, §5.2 capped
sums), leaving `evidence_paths` and `effective_ownership` as empty placeholders per scope. All 40 tests
in `tests/test_ownership_examples.py` and `tests/test_ownership_properties.py` pass unmodified.

§9 lists eleven reason codes but only five are pinned down by a worked example
(`UNKNOWN_STAKE`, `STAKE_CONFLICT`, `CONTROL_ONLY_LINK`, `LOW_CONFIDENCE_LINK`, `OWNERSHIP_OVER_100`).
For the rest, made the following defensible-but-unverified choices:

- **`OWNED_BY_BLOCKED`**: added whenever an entity's blocked-owner `lower_sum >= 50` (i.e. the ownership
  cascade path fired), independent of whether the entity is also independently `DESIGNATED`.
- **`AGGREGATE_OWNERSHIP`**: added alongside `OWNED_BY_BLOCKED` only when *more than one* blocked owner's
  combined edge contributed to `lower_sum` (row 5's "blocked owners' stakes are summed" pattern) — a
  single blocked owner reaching 50% alone does not get this code.
- **`BAND_UNCERTAINTY`** vs. **`AMBIGUOUS_OWNER`**: both only ever added when the entity's final status is
  AMBIGUOUS, attributed per contributing edge — `BAND_UNCERTAINTY` when a *blocked* owner's combined edge
  is a known band (`lower < upper`); `AMBIGUOUS_OWNER` when the contributing owner is itself only
  AMBIGUOUS (not BLOCKED). This distinguishes row 6-style banded-stake ambiguity from row 10-style
  ambiguity-propagated-through-an-ambiguous-owner, since both produce AMBIGUOUS but for different reasons.
- **`START_DATE_INFERRED`**: added whenever any active combined edge into the entity has
  `start_date_inferred=True` on any contributing source edge, regardless of status.
- **Same-source duplicate edges**: §4.4 is worded as "two or more active edges ... from different
  sources," but doesn't say what happens if the same source reports two active edges for one pair. Applied
  the same intersection/hull combination regardless of whether sources differ, since it's the natural
  generalization and doesn't conflict with anything the spec states.

None of this changes any settled §3–§8 rule or any worked-example outcome — it only fills in reason-code
detail the spec's worked-examples table doesn't exercise. Flagging here per the ownership-rules skill's
"record decisions" rule rather than treating it as settled.

## 2026-09-23 — §9.1 reason-code trigger conditions promoted from decisions.md into the spec, with two corrections

The reason-code attribution choices logged in the previous entry ("`propagate_blocked()` implemented:
reason-code attribution not pinned down by §6/§9") were promoted into `ownership_rules.md` as explicit,
settled rules (v0.7, new §9.1 and a §4.4 addition), at the user's direction, with two corrections to what
`src/screen/ownership.py` actually did:

1. **`AGGREGATE_OWNERSHIP`** was previously set whenever *more than one* blocked owner had a contributing
   edge, regardless of whether one of them already reached 50% alone. Corrected: it now requires that
   *no single* blocked owner's combined `stake_lower` reaches 50 alone — a dominant blocked owner plus an
   incidental smaller one no longer gets flagged as an aggregate case.
2. **`BAND_UNCERTAINTY`** was previously set whenever a blocked owner's combined edge had
   `stake_lower < stake_upper`, known — which also fires on a hull-combined (`STAKE_CONFLICT`) edge, since
   a hull is a `[min, max]` range and thus almost always has `lower < upper` too. Corrected: added an
   explicit `uncertainty kind` classification per combined edge (§4.4: `conflict` > `unknown` > `band` >
   `none`, mutually exclusive), and `BAND_UNCERTAINTY` now requires kind `band` specifically, never
   `conflict` or `unknown`.

Also specified in the spec (previously implementation-only): `DESIGNATED` fires exactly when the entity
itself is designated and active at `as_of_date`; the same-source duplicate-edge combination rule (§4.4
applies to any 2+ active edges on a pair, not only cross-source ones).

`src/screen/ownership.py` updated to match §9.1 exactly (the `AGGREGATE_OWNERSHIP` and `BAND_UNCERTAINTY`
fixes above); `tests/test_ownership_reason_codes.py` added — one positive test per reason code, three
negative tests (single dominant blocked owner → no `AGGREGATE_OWNERSHIP`; unknown stake → no
`BAND_UNCERTAINTY`; hull conflict → no `BAND_UNCERTAINTY`), and a property test that `BAND_UNCERTAINTY`/
`AMBIGUOUS_OWNER` only ever appear on AMBIGUOUS entities and `OWNED_BY_BLOCKED`/`AGGREGATE_OWNERSHIP` only
on BLOCKED ones. All of `tests/test_ownership_examples.py`, `tests/test_ownership_properties.py`, and the
new file pass together; no existing test file was modified.

## 2026-09-23 — §4.4 `unknown` uncertainty-kind condition fixed; worked-example rows 27-28 added

The v0.7 uncertainty-kind classification (previous entry) still had a bug in `unknown`'s condition, both
in the spec text and in `_uncertainty_kind()`: it fired whenever the *combined* edge's `stake_known` was
`False`, which — via the old `known = all(e.stake_known for e in edges)` combination logic — is true
whenever *any* contributing source is unknown, even if another source is a known band or exact stake that
fully determines the (narrower) intersection. Concretely: D→X reported as unknown `[0, 100]` by one source
and Band A `[25, 50]` by another intersects to `[25, 50]` — a real band, not an unconstrained unknown — but
the old code labeled it `unknown` and emitted `UNKNOWN_STAKE` instead of `BAND_UNCERTAINTY`.

Decision, at the user's direction: `unknown` now requires that **every** contributing source edge is
itself unknown — an unknown source contributes nothing once intersected with any known source, since
`[0, 100]` cannot narrow anything. Restated in `ownership_rules.md` §4.4 (v0.8) as kind reflecting the
*resulting range*, not which sources fed it: `none` (point value) is checked first — so intersecting
unknown with an exact stake is `none`, not `unknown`, even though it involved an unknown source — then
`unknown` (only if all sources unknown), then `band` (the residual case, which for this spec's finite
exact/PSC-band/unknown stake model always exactly equals one known contributing source's own range).

`_uncertainty_kind()` in `src/screen/ownership.py` now takes the contributing `edges` list directly
(rather than a pre-reduced `known` boolean) so it can check `all(not e.stake_known for e in edges)`
itself. Added worked-example rows 27 (`unknown` + Band A → `[25, 50]`, `band`, not `unknown`) and 28
(`unknown` + exact 40% → `[40, 40]`, `none`) to §6.2, and corresponding tests
`test_row_27_unknown_and_band_overlap_is_band_not_unknown` /
`test_row_28_unknown_and_exact_overlap_is_none` in `tests/test_ownership_reason_codes.py`. Row 28 can't
expose a reason-code symptom of the old bug directly (the entity ends up CLEAR under either the old or new
kind, since `BAND_UNCERTAINTY`/`UNKNOWN_STAKE` only ever apply to AMBIGUOUS entities) — it's kept as a
documented spec row for the combination result itself, consistent with the row-18 precedent
(documentation-only rows are allowed when the algorithm's public output can't distinguish the two cases).
Verified the fix actually mattered by replaying the old `_uncertainty_kind()` logic standalone against
row 27's inputs (`unknown` vs. the correct `band`) rather than by reverting the real file. All 58 tests
in `tests/test_ownership_examples.py`, `tests/test_ownership_properties.py`, and
`tests/test_ownership_reason_codes.py` pass; no existing test file was modified.

## 2026-09-23 — Three test-infrastructure fixes to `tests/test_ownership_properties.py`, at the user's direction

Unlike other entries in this log, these are changes to the property-test file itself, not to
`ownership.py` or the spec. The ownership-rules skill's "never edit the ownership tests to make code
pass" rule is about the assistant unilaterally weakening tests to get an implementation to pass; it
doesn't bar the user from directing test-infrastructure improvements, which is what these are — no
property's assertions changed, and no implementation behavior changed.

1. **`_record_outcome_events()` now called in every test that computes a result**, not just five of
   thirteen. Changed its signature to `_record_outcome_events(entities, edges, result)` (added `edges`,
   needed for item 2 below) and added a call at every `_run(...)` call site, including properties 11, 12,
   13 (previously missing) and 1, 3, 4, 5, 7, 8 (also previously missing). Property 5 uses the separate
   `graphs_single_controlled_designation()` strategy, so its calls pass `graph.entities`/
   `graph.ownership_edges` from that strategy's own `Graph`, not the general `graphs()` one.

2. **New outcome event `outcome:blocked_via_cascade`**: a non-designated entity is BLOCKED and at least
   one owner with an active edge into it is itself BLOCKED and non-designated — the multi-hop cascade
   pattern from `ownership_rules.md` §6.1 (the §3 trap example), as opposed to blocking that traces
   directly back to a single designated owner. Implemented as `_has_cascade()`, checking active
   `(owner_id, owned_id)` pairs for this owner/owned-both-BLOCKED-and-non-designated pattern.

   Biased `graphs()` to construct this pattern explicitly and often: with `len(ids) >= 3`, a 60% chance
   picks three distinct ids `D`/`A`/`B`, forces `D` designated-and-active, forces `A` and `B`
   non-designated (overriding the per-entity `_entity()` draw's independent ~50%-designated coin flip,
   which was otherwise defeating the pattern most of the time — see below), and adds `D`→`A` and `A`→`B`
   exact 60% edges, each independently guaranteed active via `_active_now()`.

   First attempt used a plain 50%-probability boolean gate and only forced `D`'s designation, leaving `A`
   and `B` to whatever the earlier per-entity draw assigned — checked via
   `--hypothesis-show-statistics` and found only 3.70% of examples firing the event, far under the 20%
   target. Root cause: `_entity()` independently designates every drawn id ~50% of the time, so `A` or
   `B` ended up designated (and thus excluded from `_has_cascade()`'s non-designated check) about 75% of
   the time the chain was constructed at all. Fixing that (forcing `A`/`B` non-designated) alone brought
   most tests into the 15–37% range; bumping the construction probability from 50% to 60% brought every
   `graphs()`-based property test to 27–50%. `test_property_5` (2.44%) and `test_property_7` (0%, not
   shown by `--hypothesis-show-statistics`) are expected exceptions: property 5 doesn't use `graphs()` at
   all, and property 7's own strategy (`graphs_no_designations()`) strips every designation by
   construction, so the pattern can never fire there — that's the property being tested, not a gap.

3. **`_distinct_pair()` no longer uses `.filter(lambda t: t[0] != t[1])`.** Rewrote it to draw the first
   id, remove it from the candidate list, then draw the second id from what's left — functionally
   identical (still a uniformly-chosen ordered pair of distinct ids from `ids`, which always has at least
   2 elements) but avoids Hypothesis's filter-and-retry machinery. Left the one other `.filter(...)`
   pair-draw in `graphs_single_controlled_designation()` (inside `_risk_increase`'s "extra edges" loop)
   unchanged — its filter also excludes a specific `(d_id, owned)` pair, not just self-pairs, so it isn't
   the same pattern the user asked to remove.

All 61 tests in `tests/test_ownership_examples.py`, `tests/test_ownership_properties.py`, and
`tests/test_ownership_reason_codes.py` pass after these changes.

## 2026-09-23 — Monotonicity branch events; property 5's actual invalid-example source fixed (not the date)

Two more fixes to `tests/test_ownership_properties.py`, at the user's direction:

1. **`_risk_increase()` now records `event(f"mono:{kind}")`** for each of its four branches
   (`designate`/`edge_from_blocked`/`identity_link_to_blocked`/`raise_bounds`), plus `event("mono:noop")`
   on the no-op fallback (`if not kinds: return graph`) — gives per-branch coverage evidence for
   `test_property_1_monotonicity` the same way `_record_structural_events`/`_record_outcome_events`
   already do elsewhere.

2. **Property 5's invalid-example count investigated and fixed — but not where the instruction pointed.**
   The user described it as "generating and rejecting ... for the date condition," asking for the query
   date to be generated as `designation_start` minus a positive offset. Checked first: the test already
   does exactly that (`d = s - timedelta(days=data.draw(st.integers(min_value=1, max_value=1000)))`,
   unconditional, no `assume`/`filter` involved) — this was already correct and needed no change. The
   actual, sole source of `test_property_5_time_consistency_designation`'s 128 invalid examples (checked
   via `--hypothesis-show-statistics`, which named the exact filter in every "invalid" line) was a
   different filter entirely: `graphs_single_controlled_designation()`'s "extra edges" loop excluded both
   self-pairs and the specific `(d_id, owned)` pair via
   `.filter(lambda t: t[0] != t[1] and (t[0], t[1]) != (d_id, owned))`, which rejects most of the sample
   space when `ids` is short (e.g. only 1 of 4 possible tuples is valid when `len(ids) == 2`) — this is
   the same `.filter(lambda t: t[0] != t[1])`-style pair draw fixed elsewhere in this file in an earlier
   entry, left alone at the time because of its extra exclusion condition.

   Fixed by drawing `owner` first, then computing the excluded set (`{owner}`, or `{owner, owned}` when
   `owner == d_id`) and drawing `target` from `ids` with that set removed, skipping the edge entirely on
   the rare occasion (`len(ids) == 2` and `owner == d_id`) that leaves no valid target — never raising and
   never retrying. This took the invalid count from 128 to 34 (out of 200, `dev` profile).

   The remaining 34 are not filter-related: `--hypothesis-show-statistics` shows no named reason for them
   (unlike the removed filter, which named itself in every "invalid" line), and a from-scratch, filter-free
   `st.data()`-based property test in the same file (`test_property_3_order_independence`, which never
   touches this strategy) shows a comparable 21 invalid examples of its own. This is baseline overhead
   inherent to `@given(data=st.data())`'s interactive `.draw()` style, not something under this strategy's
   control — restructuring away from `st.data()` would be a larger change than asked for here.

All 61 tests pass, including a full run on the `ci` Hypothesis profile
(`HYPOTHESIS_PROFILE=ci pytest tests/ -q`, 1000 examples per property test).

## 2026-09-24 — Four spec-check findings addressed (spec first, then tests, then code)

Follow-up to the `/spec-check ownership_rules` review (fresh-context subagent against the diff and the
spec). Spec updated to v0.9. All four items below: spec change → new test (shown failing against the
pre-fix code) → code fix. `tests/test_ownership_examples.py` and `tests/test_ownership_properties.py`
were not touched; new tests went into `tests/test_ownership_reason_codes.py`, which this session owns.

1. **`control_links` was never populated (real gap, not a documented scope decision).**
   `propagate_blocked()` computed `active_control` and used it for the `CONTROL_ONLY_LINK` reason code,
   but always returned `control_links=[]`. Unlike `evidence_paths`/`effective_ownership`, this was never
   scoped as deferred anywhere. §9's `control_links` row and §9.1's `CONTROL_ONLY_LINK` row now state the
   same explicit filter: an active `ControlEdge` counts only when its controller (`owner_id`) is BLOCKED
   or AMBIGUOUS in the final `blocked`/`possible` sets — a control edge from a CLEAR controller produces
   neither the reason code nor a `control_links` entry. This is a real behavior change to
   `CONTROL_ONLY_LINK` (previously fired for *any* active control edge regardless of controller status);
   row 17 was updated to note it now also asserts `control_links` content, and a new row 32 covers the
   CLEAR-controller case. Code: `control_links` is now built from the same `controlling_links` list that
   sets the reason code, instead of a hardcoded `[]`.

2. **§4.4 boundary-touching ranges: numeric touch is not always a true overlap.** Two source ranges whose
   *stored* plain-number bounds meet at exactly one point (e.g. Band A's stored upper `50` and Band B's
   stored lower `50`) were always treated as overlapping (intersection = that point), because combination
   only ever compared stored numbers, never each source's true open/closed bound from §4.2's band table.
   Concretely: Band A `(25, 50]` + Band B `(50, 75)` touch at `50` in stored form, but `50` is *excluded*
   from Band B's true range (open lower bound) — genuinely no shared stake percentage exists there, so
   this should be a conflict (hull `[25, 75]`, `STAKE_CONFLICT`), not a pinned `[50, 50]`. Added a new
   `_true_bounds()`/`_point_truly_shared()` pair to `src/screen/ownership.py`: for the degenerate case
   where the numeric intersection collapses to a single point, that point counts as a true overlap only
   if it lies inside *every* contributing source's own range under that source's true (not stored) bound.
   A genuine sub-range intersection (`inter_lower < inter_upper`) is untouched — always a true overlap,
   since interior points don't depend on endpoint openness. Spec gained the §4.2-band-table-based
   boundary rule in §4.4 and worked-example rows 29–31 (Band A + Band B → conflict; exact 25% + Band A →
   conflict, since 25 is Band A's open lower bound; exact 50% + Band A → true overlap, since 50 is Band
   A's closed upper bound).

3. **§9.1 `AGGREGATE_OWNERSHIP` now states explicitly that it presupposes `OWNED_BY_BLOCKED`.** The prior
   wording ("no single blocked owner reaches 50 alone") was, read in isolation, vacuously satisfiable for
   an entity BLOCKED solely via its own designation with no qualifying incoming edge. The code already
   only sets `AGGREGATE_OWNERSHIP` nested inside the `lower_sum >= 50` branch that also sets
   `OWNED_BY_BLOCKED` (`src/screen/ownership.py`), so no code change was needed here — just closing the
   spec-wording gap the review found.

4. **§7: a null start date is now a validation error, not silent inactivity.** `_active()` previously
   returned `False` for a `None` start (treating the fact as simply not-yet-active). §7 already required
   ingestion to backfill any missing start date before propagation ever runs; reaching `propagate_blocked()`
   with a null start is a data-integrity bug, not a legitimate "inactive" edge, and silently dropping it
   risked masking real defects as a quiet CLEAR result. `_active()` now raises `ValueError` on a null
   start, and this applies uniformly to `OwnershipEdge`/`ControlEdge` start dates and to an
   active-designation check (`designated=True` with no `designation_start`), since all three route through
   the same `_active()` call. Added `test_null_start_date_raises_validation_error`.

All 70 tests pass, including a full run on the `ci` Hypothesis profile
(`HYPOTHESIS_PROFILE=ci pytest tests/ -q`, 1000 examples per property test).

## 2026-09-24 — §9.2 (evidence) added to `ownership_rules.md`; `evidence_paths` replaced by `evidence`

At the user's direction, folded `evidence_section.md` (drafted separately) into `ownership_rules.md` as new
§9.2, and updated §9's output table so its `evidence` field now points to §9.2 instead of the old
`evidence_paths` placeholder description. Bumped the spec to v0.10 and added `tests/test_ownership_evidence.py`
to the spec's `Governs` line (new file, not yet written at the time of this entry).

This is a source-of-truth promotion, not a new rule invented here: §9.2's content (fact IDs, synchronous
justification rank, evidence-content schema, per-status selection rules, worked examples EV1–EV11, and
seven required properties) was supplied verbatim by the user via `evidence_section.md`; no interpretation
was added at this step. `src/screen/ownership.py`'s `OwnershipResult.evidence_paths` field, and its doc
comment listing `evidence_paths`/`effective_ownership` as deferred placeholders, still need renaming to
`evidence` as part of implementing §9.2 — tracked as the next step (tests first, then a plan-mode design
note on rank/fact-ID computation, then implementation), not done in this spec-and-decisions-only step.

## 2026-09-24 — §9.2 evidence implemented: fact-ID hash recipe, synchronous ranks

Implemented `docs/specs/ownership_rules.md` §9.2 in `src/screen/ownership.py`, following the
plan-mode design presented and approved before any code was written (per the ownership-rules
skill's "present a plan" rule and the user's explicit instruction to explain fact-ID/rank
computation in plan mode first). All 18 new tests (`tests/test_ownership_evidence.py`'s
EV1–EV11, plus 7 property tests for §9.2.6 appended to `tests/test_ownership_properties.py`)
were written and confirmed failing before this implementation; all 88 tests
(70 pre-existing + 18 new) pass afterward, on both the `dev` and `ci` Hypothesis profiles.
Neither `tests/test_ownership_examples.py` nor `tests/test_ownership_reason_codes.py` was
touched; no existing assertion in any file was modified.

Two scaffolding decisions the spec leaves to the implementation (not rule interpretations —
same category as the `ControlEdge` dataclass decision above):

1. **Fact-ID hash recipe.** Each fact type's ID is `<prefix>:<visible fields>:<h8>`, where
   `h8` is the first 8 hex chars of `sha256` over *all* the fact's fields (not just the
   visible ones) joined by `|`. Field order per type: designation —
   `(entity_id, designation_start, designation_end)`; ownership edge — `(owner_id, owned_id,
   stake_lower, stake_upper, stake_known, source, start_date, end_date, start_date_inferred)`;
   identity link — `(id_a, id_b, first_seen_date)` with `id_a`/`id_b` sorted first (the edge
   is unordered, §2); control edge — `(owner_id, owned_id, source, start_date, end_date)`.
   Hashing every field (not just the ones shown in the visible prefix) is what makes two
   same-source, same-pair, same-start-date edges that differ only in stake or end date get
   different IDs, per §9.2.1's "including same-source duplicates" requirement.

2. **Synchronous ranks computed by a separate pass, not the existing async loop.** The
   existing fixed-point loop in `propagate_blocked()` is asynchronous (Gauss-Seidel: within
   one pass, an update to entity A is visible to entity B examined later in the *same* pass)
   and is already correct and tested — left untouched. Added `_synchronous_ranks()`, a
   self-contained Jacobi-style pass that only ever reads the *previous* round's frozen
   `blocked`/`possible` sets, seeded from a copy of the designation set taken before the
   existing loop mutates it in place (`designated_seed = set(blocked)`, one added line).
   Relies on the standard fixed-point-theory fact that a monotone update function's least
   fixed point above a given seed doesn't depend on synchronous-vs-asynchronous scheduling as
   long as every entity is re-examined every round (true of both loops here) — so
   `_synchronous_ranks()`'s `blocked_rank`/`possible_rank` key sets always agree with the
   main loop's `blocked`/`possible`, without the two being reconciled in code. Verified by
   the full property-test suite (in particular `test_evidence_property_1_sufficiency` and
   `test_evidence_property_4_acyclic`, which would fail immediately on any disagreement).

One test-writing bug found and fixed while turning the new tests green (not a code bug):
`test_evidence_property_4_acyclic`'s first version treated *any* second visit to an entity
during the `depends_on` walk as a cycle, which incorrectly flags a legitimate diamond
dependency (two different entities both citing the same lower-ranked owner — e.g. two
BLOCKED entities both citing the same designated root) as acyclic-property violation. Fixed
by tracking the current DFS path separately from the set of everything ever visited (a real
cycle is a back-edge to an ancestor still on the current path; revisiting a node via a
different, non-overlapping path is fine and expected). This is a fix to a test authored this
session, not a change to any of the four pre-existing test files.

`_combine_ownership_edges()` gained one additive key, `source_edges` (the raw list of
contributing edges per pair, already grouped internally as `by_pair`) — used to build each
`EvidenceStep.source_fact_ids`; the four keys existing code already reads are untouched.
`EvidenceStep.source_fact_ids` is sorted before being stored, since the spec assigns no
meaning to the list's order and leaving it in raw input order made
`test_evidence_property_6_order_independence` fail (shuffling duplicate-pair source edges
changed the list's order without changing its contents).

## 2026-09-24 — §9.2.6 item 4 (acyclic) reworded: diamonds explicitly allowed

At the user's direction, corrected `ownership_rules.md` §9.2.6 item 4's wording from "Following
`depends_on` never revisits an entity" to "The `depends_on` graph contains no cycle (shared
dependencies, i.e. diamonds, are allowed)."

The original wording was ambiguous in a way that had already caused a real bug: the first
version of `test_evidence_property_4_acyclic` (see the "§9.2 evidence implemented" entry
above) read "never revisits an entity" literally and flagged any second visit to an entity
during the `depends_on` walk as a violation — including a legitimate diamond, where two
different entities both cite the same lower-ranked owner (e.g. two BLOCKED entities both
citing the same designated root). That is not a cycle and must be allowed; the actual
required property is that the graph has no back-edge to an ancestor still on the current
path. The test was already fixed to check that correctly (tracking the current DFS path
separately from the set of everything ever visited); this entry corrects the spec prose itself
to match the property actually being enforced, rather than the case that produced the earlier
false positive.

## 2026-09-23 — `IdentityLinkEdge` gets a `first_seen_date`, filtered like any other edge

Spec change (v0.4) to `ownership_rules.md` §2, at the user's direction: `IdentityLinkEdge` now carries a
`first_seen_date` and is filtered to `as_of_date` before propagation, using §7's open-ended half-open
case (`first_seen_date <= as_of_date`, no end date — an unconfirmed identity link is resolved and
removed from the data by entity linking rather than end-dated in place). Previously §5.1 stated the
opposite explicitly ("this path is not date-scoped by the link itself"); that paragraph was rewritten to
describe the new two-sided time-scoping (the link's own `first_seen_date`, plus the other end's status
already being computed at the same `as_of_date`).
