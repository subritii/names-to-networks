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

## 2026-09-23 — `IdentityLinkEdge` gets a `first_seen_date`, filtered like any other edge

Spec change (v0.4) to `ownership_rules.md` §2, at the user's direction: `IdentityLinkEdge` now carries a
`first_seen_date` and is filtered to `as_of_date` before propagation, using §7's open-ended half-open
case (`first_seen_date <= as_of_date`, no end date — an unconfirmed identity link is resolved and
removed from the data by entity linking rather than end-dated in place). Previously §5.1 stated the
opposite explicitly ("this path is not date-scoped by the link itself"); that paragraph was rewritten to
describe the new two-sided time-scoping (the link's own `first_seen_date`, plus the other end's status
already being computed at the same `as_of_date`).
