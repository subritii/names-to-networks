"""Worked-example unit tests from docs/specs/ownership_rules.md §6.

Every row of §6.1 (the cascade) and the §6.2 table is one test here.
propagate_blocked() is currently a stub (raises NotImplementedError), so
every test in this file is expected to fail until it is implemented.
Do not weaken these tests to make code pass -- see the ownership-rules
skill and CLAUDE.md.
"""

from datetime import date

import pytest

from src.screen.ownership import (
    ControlEdge,
    Entity,
    IdentityLinkEdge,
    OwnershipEdge,
    propagate_blocked,
)

# Dates chosen so that, unless a test says otherwise, every designation and
# edge is active "as of" AS_OF (spec §6: "All edges active unless stated").
EARLY = date(2020, 1, 1)
AS_OF = date(2026, 1, 1)


def entity(id, kind="Company", designated=False, start=None, end=None):
    return Entity(
        id=id,
        kind=kind,
        designated=designated,
        designation_start=start if designated else None,
        designation_end=end,
    )


def edge(owner, owned, lower, upper, known=True, source="test", start=EARLY, end=None, start_inferred=False):
    return OwnershipEdge(
        owner_id=owner,
        owned_id=owned,
        stake_lower=lower,
        stake_upper=upper,
        stake_known=known,
        source=source,
        start_date=start,
        end_date=end,
        start_date_inferred=start_inferred,
    )


# --- §6.1 The cascade (the §3 trap) ---------------------------------------


def test_6_1_cascade():
    # A is designated. A owns 60% of B. B owns 50% of C.
    entities = [
        entity("A", designated=True, start=EARLY),
        entity("B"),
        entity("C"),
    ]
    edges = [
        edge("A", "B", 60, 60),
        edge("B", "C", 50, 50),
    ]

    result = propagate_blocked(entities, edges, as_of_date=AS_OF)

    # Multiplying (0.6 * 0.5 = 30%) would wrongly clear C; status must cascade.
    assert result["A"].status == "BLOCKED"
    assert result["B"].status == "BLOCKED"
    assert result["C"].status == "BLOCKED"


# --- §6.2 Full example table -----------------------------------------------


def test_row_1_single_owner_60_percent():
    # D owns 60% of A -> A: BLOCKED (single owner >= 50%)
    entities = [entity("D", designated=True, start=EARLY), entity("A")]
    edges = [edge("D", "A", 60, 60)]

    result = propagate_blocked(entities, edges, as_of_date=AS_OF)

    assert result["A"].status == "BLOCKED"


def test_row_2_exactly_50_percent_is_blocked():
    # D owns 50% of A -> A: BLOCKED (threshold is inclusive)
    entities = [entity("D", designated=True, start=EARLY), entity("A")]
    edges = [edge("D", "A", 50, 50)]

    result = propagate_blocked(entities, edges, as_of_date=AS_OF)

    assert result["A"].status == "BLOCKED"


def test_row_3_49_percent_is_clear():
    # D owns 49% of A -> A: CLEAR (upper sum 49 < 50)
    entities = [entity("D", designated=True, start=EARLY), entity("A")]
    edges = [edge("D", "A", 49, 49)]

    result = propagate_blocked(entities, edges, as_of_date=AS_OF)

    assert result["A"].status == "CLEAR"


def test_row_4_cascade_two_hops():
    # D owns 60% of A; A owns 50% of B -> B: BLOCKED (cascade, §6.1)
    entities = [
        entity("D", designated=True, start=EARLY),
        entity("A"),
        entity("B"),
    ]
    edges = [
        edge("D", "A", 60, 60),
        edge("A", "B", 50, 50),
    ]

    result = propagate_blocked(entities, edges, as_of_date=AS_OF)

    assert result["B"].status == "BLOCKED"


def test_row_5_two_blocked_owners_sum():
    # D1 owns 25%, D2 owns 25% of A -> A: BLOCKED (blocked owners' stakes summed)
    entities = [
        entity("D1", designated=True, start=EARLY),
        entity("D2", designated=True, start=EARLY),
        entity("A"),
    ]
    edges = [
        edge("D1", "A", 25, 25),
        edge("D2", "A", 25, 25),
    ]

    result = propagate_blocked(entities, edges, as_of_date=AS_OF)

    assert result["A"].status == "BLOCKED"


def test_row_6_band_a_is_ambiguous():
    # D owns Band A of X -> X: AMBIGUOUS (lower 25, upper 50: true stake could be 26% or 50%)
    entities = [entity("D", designated=True, start=EARLY), entity("X")]
    edges = [edge("D", "X", 25, 50)]

    result = propagate_blocked(entities, edges, as_of_date=AS_OF)

    assert result["X"].status == "AMBIGUOUS"


def test_row_7_two_band_a_owners_is_blocked():
    # D1 and D2 each own Band A of X -> X: BLOCKED (lower sum 50 >= 50)
    entities = [
        entity("D1", designated=True, start=EARLY),
        entity("D2", designated=True, start=EARLY),
        entity("X"),
    ]
    edges = [
        edge("D1", "X", 25, 50),
        edge("D2", "X", 25, 50),
    ]

    result = propagate_blocked(entities, edges, as_of_date=AS_OF)

    assert result["X"].status == "BLOCKED"


def test_row_8_band_b_is_blocked():
    # D owns Band B of X -> X: BLOCKED (lower 50 >= 50)
    entities = [entity("D", designated=True, start=EARLY), entity("X")]
    edges = [edge("D", "X", 50, 75)]

    result = propagate_blocked(entities, edges, as_of_date=AS_OF)

    assert result["X"].status == "BLOCKED"


def test_row_9_band_c_is_blocked():
    # D owns Band C of X -> X: BLOCKED (lower 75 >= 50)
    entities = [entity("D", designated=True, start=EARLY), entity("X")]
    edges = [edge("D", "X", 75, 100)]

    result = propagate_blocked(entities, edges, as_of_date=AS_OF)

    assert result["X"].status == "BLOCKED"


def test_row_10_ambiguity_propagates_downstream():
    # D owns Band A of X; X owns 100% of Y -> X: AMBIGUOUS, Y: AMBIGUOUS
    entities = [
        entity("D", designated=True, start=EARLY),
        entity("X"),
        entity("Y"),
    ]
    edges = [
        edge("D", "X", 25, 50),
        edge("X", "Y", 100, 100),
    ]

    result = propagate_blocked(entities, edges, as_of_date=AS_OF)

    assert result["X"].status == "AMBIGUOUS"
    assert result["Y"].status == "AMBIGUOUS"


def test_row_11_unknown_stake_is_ambiguous():
    # D owns X, stake unknown -> X: AMBIGUOUS, UNKNOWN_STAKE (range [0, 100])
    entities = [entity("D", designated=True, start=EARLY), entity("X")]
    edges = [edge("D", "X", 0, 100, known=False)]

    result = propagate_blocked(entities, edges, as_of_date=AS_OF)

    assert result["X"].status == "AMBIGUOUS"
    assert "UNKNOWN_STAKE" in result["X"].reason_codes


def test_row_12_conflicting_non_overlapping_sources_use_hull():
    # Source 1: D owns 60% of X; Source 2: D owns 30% of X
    # -> X: AMBIGUOUS, STAKE_CONFLICT (hull [30, 60] straddles 50)
    entities = [entity("D", designated=True, start=EARLY), entity("X")]
    edges = [
        edge("D", "X", 60, 60, source="source_1"),
        edge("D", "X", 30, 30, source="source_2"),
    ]

    result = propagate_blocked(entities, edges, as_of_date=AS_OF)

    assert result["X"].status == "AMBIGUOUS"
    assert "STAKE_CONFLICT" in result["X"].reason_codes


def test_row_13_conflicting_overlapping_sources_use_intersection():
    # Source 1: D owns 40% of X; Source 2: D owns Band A of X
    # -> X: CLEAR (intersection [40, 40]; 40 < 50)
    entities = [entity("D", designated=True, start=EARLY), entity("X")]
    edges = [
        edge("D", "X", 40, 40, source="source_1"),
        edge("D", "X", 25, 50, source="source_2"),
    ]

    result = propagate_blocked(entities, edges, as_of_date=AS_OF)

    assert result["X"].status == "CLEAR"


@pytest.mark.timeout(5)
def test_row_14_cycle_safety():
    # D owns 60% of A; A owns 60% of B; B owns 60% of A -> A, B: BLOCKED; terminates
    entities = [
        entity("D", designated=True, start=EARLY),
        entity("A"),
        entity("B"),
    ]
    edges = [
        edge("D", "A", 60, 60),
        edge("A", "B", 60, 60),
        edge("B", "A", 60, 60),
    ]

    result = propagate_blocked(entities, edges, as_of_date=AS_OF)

    assert result["A"].status == "BLOCKED"
    assert result["B"].status == "BLOCKED"


def test_row_15_edge_not_yet_active():
    # D owns 60% of A, edge starts 2025-01-01; as_of 2024-12-31 -> A: CLEAR
    entities = [entity("D", designated=True, start=EARLY), entity("A")]
    edges = [edge("D", "A", 60, 60, start=date(2025, 1, 1))]

    result = propagate_blocked(entities, edges, as_of_date=date(2024, 12, 31))

    assert result["A"].status == "CLEAR"

    # Positive control: the same edge, queried on/after its start date, must
    # actually block -- otherwise the CLEAR result above could just as well
    # mean the edge is never read at all.
    result_active = propagate_blocked(entities, edges, as_of_date=date(2025, 1, 1))

    assert result_active["A"].status == "BLOCKED"


def test_row_16_designation_ended_half_open():
    # D designated 2026-03-01, delisted 2026-06-01; D owns 60% of A;
    # as_of 2026-06-01 -> A: CLEAR (half-open end, §7)
    entities = [
        entity(
            "D",
            designated=True,
            start=date(2026, 3, 1),
            end=date(2026, 6, 1),
        ),
        entity("A"),
    ]
    edges = [edge("D", "A", 60, 60)]

    result = propagate_blocked(entities, edges, as_of_date=date(2026, 6, 1))

    assert result["A"].status == "CLEAR"
    # D's own designation is likewise not active on its own end date.
    assert result["D"].status == "CLEAR"

    # Positive control: the day before delisting, the designation is still
    # active, so A must be BLOCKED.
    result_active = propagate_blocked(entities, edges, as_of_date=date(2026, 5, 31))

    assert result_active["A"].status == "BLOCKED"


def test_row_17_control_only_link_never_changes_status():
    # D has a control-only PSC entry for X, no shares -> X: CLEAR, CONTROL_ONLY_LINK
    entities = [entity("D", designated=True, start=EARLY), entity("X")]
    control_edges = [ControlEdge(owner_id="D", owned_id="X", source="test", start_date=EARLY)]

    result = propagate_blocked(entities, [], control_edges=control_edges, as_of_date=AS_OF)

    assert result["D"].status == "BLOCKED"
    assert result["X"].status == "CLEAR"
    assert "CONTROL_ONLY_LINK" in result["X"].reason_codes


def test_row_18_psc_stake_at_or_below_25_percent_has_no_edge():
    # Documentation-only: this row is about ingestion (whether the PSC
    # ingester assigns a band or an edge at all for <=25%), not about
    # propagate_blocked() itself, which never sees an edge either way.
    # The real rule belongs in a future tests/test_ingest_psc.py; this test
    # only pins down that "no edge" implies CLEAR, which propagate_blocked()
    # does need to guarantee. See docs/decisions.md (2026-09-23).
    #
    # D's UK PSC record for X reports a stake of exactly 25% or below
    # (no band assigned) -> no edge; X: CLEAR (§4.2)
    entities = [entity("D", designated=True, start=EARLY), entity("X")]
    edges = []  # below the PSC reporting threshold: absence of an edge, not (0, 25]

    result = propagate_blocked(entities, edges, as_of_date=AS_OF)

    assert result["X"].status == "CLEAR"


def test_row_19_identity_link_to_blocked_entity_with_no_ownership_edges():
    # E has an IdentityLinkEdge to D, first seen before as_of_date (designated,
    # so D is in blocked); E has no ownership edges at all
    # -> E: AMBIGUOUS, LOW_CONFIDENCE_LINK
    entities = [entity("D", designated=True, start=EARLY), entity("E", kind="Person")]
    identity_links = [IdentityLinkEdge(entity_id="E", same_as_id="D", first_seen_date=EARLY)]

    result = propagate_blocked(
        entities, [], identity_link_edges=identity_links, as_of_date=AS_OF
    )

    assert result["E"].status == "AMBIGUOUS"
    assert "LOW_CONFIDENCE_LINK" in result["E"].reason_codes


def test_row_20_identity_link_to_ambiguous_entity():
    # E has an IdentityLinkEdge to X, first seen before as_of_date, where X is
    # AMBIGUOUS (not BLOCKED) via a Band A ownership edge from a blocked owner
    # -> E: AMBIGUOUS
    entities = [
        entity("D", designated=True, start=EARLY),
        entity("X"),
        entity("E", kind="Person"),
    ]
    edges = [edge("D", "X", 25, 50)]  # Band A -> X is AMBIGUOUS, not BLOCKED
    identity_links = [IdentityLinkEdge(entity_id="E", same_as_id="X", first_seen_date=EARLY)]

    result = propagate_blocked(
        entities, edges, identity_link_edges=identity_links, as_of_date=AS_OF
    )

    assert result["X"].status == "AMBIGUOUS"
    assert result["E"].status == "AMBIGUOUS"


def test_row_21_identity_link_to_clear_entity_has_no_effect():
    # E has an IdentityLinkEdge to C; C is not designated and has no owners
    # -> E: CLEAR, C: CLEAR (neither end is in blocked ∪ possible)
    entities = [entity("C"), entity("E", kind="Person")]
    identity_links = [IdentityLinkEdge(entity_id="E", same_as_id="C", first_seen_date=EARLY)]

    result = propagate_blocked(
        entities, [], identity_link_edges=identity_links, as_of_date=AS_OF
    )

    assert result["E"].status == "CLEAR"
    assert result["C"].status == "CLEAR"


def test_row_22_identity_link_cannot_compound_into_blocked():
    # D is designated and owns 60% of B; E has an IdentityLinkEdge to B
    # -> B: BLOCKED, E: AMBIGUOUS (the link can only push E into possible,
    # never into blocked, even though B itself is BLOCKED)
    entities = [
        entity("D", designated=True, start=EARLY),
        entity("B"),
        entity("E", kind="Person"),
    ]
    edges = [edge("D", "B", 60, 60)]
    identity_links = [IdentityLinkEdge(entity_id="E", same_as_id="B", first_seen_date=EARLY)]

    result = propagate_blocked(
        entities, edges, identity_link_edges=identity_links, as_of_date=AS_OF
    )

    assert result["B"].status == "BLOCKED"
    assert result["E"].status == "AMBIGUOUS"


def test_row_23_identity_linked_ambiguity_propagates_through_ownership():
    # As row 19 (E linked to D, designated), plus E owns 100% of X
    # -> E: AMBIGUOUS, X: AMBIGUOUS
    entities = [
        entity("D", designated=True, start=EARLY),
        entity("E", kind="Person"),
        entity("X"),
    ]
    edges = [edge("E", "X", 100, 100)]
    identity_links = [IdentityLinkEdge(entity_id="E", same_as_id="D", first_seen_date=EARLY)]

    result = propagate_blocked(
        entities, edges, identity_link_edges=identity_links, as_of_date=AS_OF
    )

    assert result["E"].status == "AMBIGUOUS"
    assert result["X"].status == "AMBIGUOUS"


def test_row_24_identity_link_is_symmetric_regardless_of_field_order():
    # As row 19, but the IdentityLinkEdge is stored with entity_id=D,
    # same_as_id=E (fields reversed) -> same result as row 19
    entities = [entity("D", designated=True, start=EARLY), entity("E", kind="Person")]
    identity_links = [IdentityLinkEdge(entity_id="D", same_as_id="E", first_seen_date=EARLY)]

    result = propagate_blocked(
        entities, [], identity_link_edges=identity_links, as_of_date=AS_OF
    )

    assert result["E"].status == "AMBIGUOUS"
    assert "LOW_CONFIDENCE_LINK" in result["E"].reason_codes


def test_row_25_identity_link_not_yet_active():
    # As row 19, but the IdentityLinkEdge's first_seen_date is after
    # as_of_date -> E: CLEAR (§7 half-open filtering applies to
    # IdentityLinkEdge too)
    entities = [entity("D", designated=True, start=EARLY), entity("E", kind="Person")]
    identity_links = [
        IdentityLinkEdge(entity_id="E", same_as_id="D", first_seen_date=date(2027, 1, 1))
    ]

    result = propagate_blocked(
        entities, [], identity_link_edges=identity_links, as_of_date=AS_OF
    )

    assert result["E"].status == "CLEAR"


def test_row_26_reported_sums_capped_at_100():
    # D1 and D2 are both designated; D1 owns 70% of X, D2 owns 80% of X
    # -> X: BLOCKED, OWNERSHIP_OVER_100; reported sums capped at 100 even
    # though the raw total (150) exceeds it (spec §5.2, v0.6)
    entities = [
        entity("D1", designated=True, start=EARLY),
        entity("D2", designated=True, start=EARLY),
        entity("X"),
    ]
    edges = [
        edge("D1", "X", 70, 70),
        edge("D2", "X", 80, 80),
    ]

    result = propagate_blocked(entities, edges, as_of_date=AS_OF)

    assert result["X"].status == "BLOCKED"
    assert "OWNERSHIP_OVER_100" in result["X"].reason_codes
    assert result["X"].blocked_owner_sum == (100, 100)
    assert result["X"].possible_owner_sum == 100
