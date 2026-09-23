---
name: ownership-rules
description: Ownership and blocking logic for the risk graph, including propagate_blocked(), stake ranges, UK PSC bands, the 50% rule, and BLOCKED/AMBIGUOUS/CLEAR status. Use whenever reading, writing, testing, or reviewing src/screen/ownership.py, or any code that computes or consumes ownership status.
---

# Ownership rules

**The spec is the single source of truth:** `docs/specs/ownership_rules.md`. Read it in full before making any change. This skill only tells you how to work with it. If this skill and the spec ever disagree, the spec wins; point out the disagreement.

## Before writing code

1. Read `docs/specs/ownership_rules.md` in full, not just the section that seems relevant.
2. Present a plan that names which parts of the spec the change touches, and which worked examples and property tests cover it.
3. If the task needs a behavior the spec doesn't define, or lists as a non-goal, stop. Propose a spec change instead of choosing a behavior in code.

## Tripwires

These are the mistakes most likely to appear in generated code. The spec explains each one.

- Never multiply percentages along a chain to decide status. Blocked status cascades; multiplied ownership is a risk feature only.
- Only BLOCKED owners can make an entity BLOCKED. AMBIGUOUS owners can only make an entity AMBIGUOUS.
- The threshold is inclusive: 50% or more.
- Stakes are ranges with plain-number bounds. Do not write epsilon or open-interval arithmetic.
- Filter every edge and designation to the `as_of_date` first. Never reuse results computed for a different date.
- `effective_ownership()` must never be read anywhere in the status decision.
- `IdentityLinkEdge` comes only from entity-linking tasks 1–2 (links inside the graph), never from customer matching.

## Working rules

- **Tests first.** Every row of the spec's worked-examples table is a unit test in `tests/test_ownership_examples.py`. Every required property is a `hypothesis` test in `tests/test_ownership_properties.py`.
- **Never edit the ownership tests to make code pass.** If a test looks wrong, stop and explain why, with reference to the spec.
- **Show evidence.** After changes, run:
  `pytest tests/test_ownership_examples.py tests/test_ownership_properties.py -q`
  and show the output. Do not report success without it.
- **Record decisions.** Any interpretation choice goes in `docs/decisions.md` with the date and reason.