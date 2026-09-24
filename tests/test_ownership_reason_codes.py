"""Reason-code trigger-condition tests for propagate_blocked() per
docs/specs/ownership_rules.md §9.1.

One positive test per reason code, plus negative tests for the two cases
corrected in §9.1 (AGGREGATE_OWNERSHIP requires no single blocked owner
alone reaching 50%; BAND_UNCERTAINTY requires uncertainty kind `band`,
never `unknown` or `conflict`), plus a property test tying BAND_UNCERTAINTY/
AMBIGUOUS_OWNER to AMBIGUOUS status and OWNED_BY_BLOCKED/AGGREGATE_OWNERSHIP
to BLOCKED status. Do not weaken these tests to make code pass -- see the
ownership-rules skill and CLAUDE.md.
"""

from datetime import date

import pytest
from hypothesis import given

from src.screen.ownership import (
    ControlEdge,
    Entity,
    IdentityLinkEdge,
    OwnershipEdge,
    _combine_ownership_edges,
    propagate_blocked,
)
from tests.test_ownership_properties import graphs

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


# --- one positive test per reason code -------------------------------------


def test_designated():
    entities = [entity("D", designated=True, start=EARLY)]
    result = propagate_blocked(entities, [], as_of_date=AS_OF)

    assert result["D"].status == "BLOCKED"
    assert "DESIGNATED" in result["D"].reason_codes


def test_owned_by_blocked():
    entities = [entity("D", designated=True, start=EARLY), entity("A")]
    edges = [edge("D", "A", 60, 60)]
    result = propagate_blocked(entities, edges, as_of_date=AS_OF)

    assert result["A"].status == "BLOCKED"
    assert "OWNED_BY_BLOCKED" in result["A"].reason_codes


def test_aggregate_ownership():
    # D1 owns 25%, D2 owns 25% of A: neither alone reaches 50, sum does.
    entities = [
        entity("D1", designated=True, start=EARLY),
        entity("D2", designated=True, start=EARLY),
        entity("A"),
    ]
    edges = [edge("D1", "A", 25, 25), edge("D2", "A", 25, 25)]
    result = propagate_blocked(entities, edges, as_of_date=AS_OF)

    assert result["A"].status == "BLOCKED"
    assert "AGGREGATE_OWNERSHIP" in result["A"].reason_codes


def test_band_uncertainty():
    entities = [entity("D", designated=True, start=EARLY), entity("X")]
    edges = [edge("D", "X", 25, 50)]  # Band A: known, lower < upper, no conflict
    result = propagate_blocked(entities, edges, as_of_date=AS_OF)

    assert result["X"].status == "AMBIGUOUS"
    assert "BAND_UNCERTAINTY" in result["X"].reason_codes


def test_unknown_stake():
    entities = [entity("D", designated=True, start=EARLY), entity("X")]
    edges = [edge("D", "X", 0, 100, known=False)]
    result = propagate_blocked(entities, edges, as_of_date=AS_OF)

    assert result["X"].status == "AMBIGUOUS"
    assert "UNKNOWN_STAKE" in result["X"].reason_codes


def test_ambiguous_owner():
    # D owns Band A of X (X: AMBIGUOUS); X owns 100% of Y.
    # Y's only incoming edge is exact (100, 100) -- kind `none`, not `band` --
    # so Y's ambiguity is attributable only to its owner (X) being AMBIGUOUS.
    entities = [
        entity("D", designated=True, start=EARLY),
        entity("X"),
        entity("Y"),
    ]
    edges = [edge("D", "X", 25, 50), edge("X", "Y", 100, 100)]
    result = propagate_blocked(entities, edges, as_of_date=AS_OF)

    assert result["Y"].status == "AMBIGUOUS"
    assert "AMBIGUOUS_OWNER" in result["Y"].reason_codes
    assert "BAND_UNCERTAINTY" not in result["Y"].reason_codes


def test_stake_conflict():
    entities = [entity("D", designated=True, start=EARLY), entity("X")]
    edges = [
        edge("D", "X", 60, 60, source="source_1"),
        edge("D", "X", 30, 30, source="source_2"),
    ]
    result = propagate_blocked(entities, edges, as_of_date=AS_OF)

    assert result["X"].status == "AMBIGUOUS"
    assert "STAKE_CONFLICT" in result["X"].reason_codes


def test_start_date_inferred():
    entities = [entity("D", designated=True, start=EARLY), entity("A")]
    edges = [edge("D", "A", 60, 60, start_inferred=True)]
    result = propagate_blocked(entities, edges, as_of_date=AS_OF)

    assert result["A"].status == "BLOCKED"
    assert "START_DATE_INFERRED" in result["A"].reason_codes


def test_control_only_link():
    entities = [entity("D", designated=True, start=EARLY), entity("X")]
    control_edges = [ControlEdge(owner_id="D", owned_id="X", source="test", start_date=EARLY)]
    result = propagate_blocked(entities, [], control_edges=control_edges, as_of_date=AS_OF)

    assert result["X"].status == "CLEAR"
    assert "CONTROL_ONLY_LINK" in result["X"].reason_codes


def test_low_confidence_link():
    entities = [entity("D", designated=True, start=EARLY), entity("E", kind="Person")]
    identity_links = [IdentityLinkEdge(entity_id="E", same_as_id="D", first_seen_date=EARLY)]
    result = propagate_blocked(entities, [], identity_link_edges=identity_links, as_of_date=AS_OF)

    assert result["E"].status == "AMBIGUOUS"
    assert "LOW_CONFIDENCE_LINK" in result["E"].reason_codes


def test_ownership_over_100():
    entities = [
        entity("D1", designated=True, start=EARLY),
        entity("D2", designated=True, start=EARLY),
        entity("X"),
    ]
    edges = [edge("D1", "X", 70, 70), edge("D2", "X", 80, 80)]
    result = propagate_blocked(entities, edges, as_of_date=AS_OF)

    assert result["X"].status == "BLOCKED"
    assert "OWNERSHIP_OVER_100" in result["X"].reason_codes


# --- spec rows 27-28: overlap-combined kind reflects the resulting range ---


def test_row_27_unknown_and_band_overlap_is_band_not_unknown():
    # D designated; sources for D->X: unknown [0,100] and Band A [25,50]
    # -> intersection [25,50]; X: AMBIGUOUS, BAND_UNCERTAINTY, not UNKNOWN_STAKE.
    entities = [entity("D", designated=True, start=EARLY), entity("X")]
    edges = [
        edge("D", "X", 0, 100, known=False, source="source_1"),
        edge("D", "X", 25, 50, source="source_2"),
    ]
    result = propagate_blocked(entities, edges, as_of_date=AS_OF)

    assert result["X"].status == "AMBIGUOUS"
    assert "BAND_UNCERTAINTY" in result["X"].reason_codes
    assert "UNKNOWN_STAKE" not in result["X"].reason_codes


def test_row_28_unknown_and_exact_overlap_is_none():
    # D designated; sources for D->X: unknown [0,100] and exact 40
    # -> intersection [40,40]; X: CLEAR.
    entities = [entity("D", designated=True, start=EARLY), entity("X")]
    edges = [
        edge("D", "X", 0, 100, known=False, source="source_1"),
        edge("D", "X", 40, 40, source="source_2"),
    ]
    result = propagate_blocked(entities, edges, as_of_date=AS_OF)

    assert result["X"].status == "CLEAR"


# --- direct _combine_ownership_edges() checks (rows 27-28, plus conflict) --
#
# Rows 27-28's combined kind isn't always distinguishable through
# propagate_blocked()'s public output -- row 28's entity ends up CLEAR
# whether its combined edge's kind is `none` or (incorrectly) `unknown`,
# since BAND_UNCERTAINTY/UNKNOWN_STAKE only ever apply to AMBIGUOUS
# entities. These call _combine_ownership_edges() directly so the combined
# range and kind are pinned down regardless of what status they happen to
# produce downstream.


def test_combine_unknown_and_exact_overlap_gives_none():
    # Row 28: unknown [0,100] and exact 40 on the same pair -> [40, 40], kind none.
    edges = [
        OwnershipEdge(owner_id="D", owned_id="X", stake_lower=0, stake_upper=100, stake_known=False),
        OwnershipEdge(owner_id="D", owned_id="X", stake_lower=40, stake_upper=40, stake_known=True),
    ]
    combined = _combine_ownership_edges(edges)
    info = combined[("D", "X")]

    assert (info["lower"], info["upper"]) == (40, 40)
    assert info["kind"] == "none"


def test_combine_unknown_and_band_overlap_gives_band():
    # Row 27: unknown [0,100] and Band A [25,50] on the same pair -> [25, 50], kind band.
    edges = [
        OwnershipEdge(owner_id="D", owned_id="X", stake_lower=0, stake_upper=100, stake_known=False),
        OwnershipEdge(owner_id="D", owned_id="X", stake_lower=25, stake_upper=50, stake_known=True),
    ]
    combined = _combine_ownership_edges(edges)
    info = combined[("D", "X")]

    assert (info["lower"], info["upper"]) == (25, 50)
    assert info["kind"] == "band"


def test_combine_non_overlapping_gives_conflict():
    # Non-overlapping exact stakes on the same pair -> hull, kind conflict.
    edges = [
        OwnershipEdge(owner_id="D", owned_id="X", stake_lower=30, stake_upper=30, stake_known=True),
        OwnershipEdge(owner_id="D", owned_id="X", stake_lower=60, stake_upper=60, stake_known=True),
    ]
    combined = _combine_ownership_edges(edges)
    info = combined[("D", "X")]

    assert (info["lower"], info["upper"]) == (30, 60)
    assert info["kind"] == "conflict"


# --- negative tests ----------------------------------------------------


def test_single_dominant_blocked_owner_no_aggregate_ownership():
    # D owns 60% of A alone: reaches 50 without summing -- not an aggregate case.
    entities = [entity("D", designated=True, start=EARLY), entity("A")]
    edges = [edge("D", "A", 60, 60)]
    result = propagate_blocked(entities, edges, as_of_date=AS_OF)

    assert result["A"].status == "BLOCKED"
    assert "AGGREGATE_OWNERSHIP" not in result["A"].reason_codes


def test_dominant_blocked_owner_plus_minor_owner_no_aggregate_ownership():
    # D1 owns 60% alone (already >= 50); D2's extra 10% is incidental --
    # summing was not what crossed the threshold, so no AGGREGATE_OWNERSHIP.
    entities = [
        entity("D1", designated=True, start=EARLY),
        entity("D2", designated=True, start=EARLY),
        entity("A"),
    ]
    edges = [edge("D1", "A", 60, 60), edge("D2", "A", 10, 10)]
    result = propagate_blocked(entities, edges, as_of_date=AS_OF)

    assert result["A"].status == "BLOCKED"
    assert "AGGREGATE_OWNERSHIP" not in result["A"].reason_codes


def test_unknown_stake_no_band_uncertainty():
    entities = [entity("D", designated=True, start=EARLY), entity("X")]
    edges = [edge("D", "X", 0, 100, known=False)]
    result = propagate_blocked(entities, edges, as_of_date=AS_OF)

    assert result["X"].status == "AMBIGUOUS"
    assert "BAND_UNCERTAINTY" not in result["X"].reason_codes


def test_hull_conflict_no_band_uncertainty():
    entities = [entity("D", designated=True, start=EARLY), entity("X")]
    edges = [
        edge("D", "X", 60, 60, source="source_1"),
        edge("D", "X", 30, 30, source="source_2"),
    ]
    result = propagate_blocked(entities, edges, as_of_date=AS_OF)

    assert result["X"].status == "AMBIGUOUS"
    assert "STAKE_CONFLICT" in result["X"].reason_codes
    assert "BAND_UNCERTAINTY" not in result["X"].reason_codes


# --- control_links (§9, §9.1): same controller-status filter as CONTROL_ONLY_LINK ---


def test_control_links_contains_edge_from_blocked_controller():
    # Row 17: D designated, has a control-only PSC entry for X.
    entities = [entity("D", designated=True, start=EARLY), entity("X")]
    control_edge = ControlEdge(owner_id="D", owned_id="X", source="test", start_date=EARLY)
    result = propagate_blocked(entities, [], control_edges=[control_edge], as_of_date=AS_OF)

    assert result["X"].status == "CLEAR"
    assert "CONTROL_ONLY_LINK" in result["X"].reason_codes
    assert result["X"].control_links == [control_edge]


def test_control_links_empty_for_clear_controller():
    # Row 32: C is not designated (CLEAR) and has a control-only PSC entry
    # for X -- neither the reason code nor a control_links entry appears.
    entities = [entity("C"), entity("X")]
    control_edge = ControlEdge(owner_id="C", owned_id="X", source="test", start_date=EARLY)
    result = propagate_blocked(entities, [], control_edges=[control_edge], as_of_date=AS_OF)

    assert result["X"].status == "CLEAR"
    assert "CONTROL_ONLY_LINK" not in result["X"].reason_codes
    assert result["X"].control_links == []


# --- §4.4 boundary-touching ranges: true open/closed bounds (rows 29-31) ---


def test_row_29_band_a_band_b_touch_is_conflict():
    # Stored ranges [25,50] and [50,75] touch at 50, but 50 is open in
    # Band B's true range (50,75) -- not a true overlap -- hull [25,75].
    entities = [entity("D", designated=True, start=EARLY), entity("X")]
    edges = [
        edge("D", "X", 25, 50, source="source_1"),
        edge("D", "X", 50, 75, source="source_2"),
    ]
    result = propagate_blocked(entities, edges, as_of_date=AS_OF)

    assert result["X"].status == "AMBIGUOUS"
    assert "STAKE_CONFLICT" in result["X"].reason_codes


def test_row_30_exact_25_band_a_touch_is_conflict():
    # Stored ranges [25,25] and [25,50] touch at 25, but 25 is open in
    # Band A's true range (25,50] -- not a true overlap -- hull [25,50].
    entities = [entity("D", designated=True, start=EARLY), entity("X")]
    edges = [
        edge("D", "X", 25, 25, source="source_1"),
        edge("D", "X", 25, 50, source="source_2"),
    ]
    result = propagate_blocked(entities, edges, as_of_date=AS_OF)

    assert result["X"].status == "AMBIGUOUS"
    assert "STAKE_CONFLICT" in result["X"].reason_codes


def test_row_31_exact_50_band_a_touch_is_overlap():
    # Stored ranges [50,50] and [25,50] touch at 50, and 50 is closed
    # (included) in Band A's true range (25,50] -- a true overlap -- [50,50].
    entities = [entity("D", designated=True, start=EARLY), entity("X")]
    edges = [
        edge("D", "X", 50, 50, source="source_1"),
        edge("D", "X", 25, 50, source="source_2"),
    ]
    result = propagate_blocked(entities, edges, as_of_date=AS_OF)

    assert result["X"].status == "BLOCKED"


def test_combine_band_a_band_b_touch_gives_conflict():
    edges = [
        OwnershipEdge(owner_id="D", owned_id="X", stake_lower=25, stake_upper=50, stake_known=True),
        OwnershipEdge(owner_id="D", owned_id="X", stake_lower=50, stake_upper=75, stake_known=True),
    ]
    combined = _combine_ownership_edges(edges)
    info = combined[("D", "X")]

    assert (info["lower"], info["upper"]) == (25, 75)
    assert info["kind"] == "conflict"


def test_combine_exact_25_band_a_touch_gives_conflict():
    edges = [
        OwnershipEdge(owner_id="D", owned_id="X", stake_lower=25, stake_upper=25, stake_known=True),
        OwnershipEdge(owner_id="D", owned_id="X", stake_lower=25, stake_upper=50, stake_known=True),
    ]
    combined = _combine_ownership_edges(edges)
    info = combined[("D", "X")]

    assert (info["lower"], info["upper"]) == (25, 50)
    assert info["kind"] == "conflict"


def test_combine_exact_50_band_a_touch_gives_overlap():
    edges = [
        OwnershipEdge(owner_id="D", owned_id="X", stake_lower=50, stake_upper=50, stake_known=True),
        OwnershipEdge(owner_id="D", owned_id="X", stake_lower=25, stake_upper=50, stake_known=True),
    ]
    combined = _combine_ownership_edges(edges)
    info = combined[("D", "X")]

    assert (info["lower"], info["upper"]) == (50, 50)
    assert info["kind"] == "none"


# --- §7: a null start_date is a validation error, not an inactive fact ---


def test_null_start_date_raises_validation_error():
    entities = [entity("D", designated=True, start=EARLY), entity("A")]
    bad_edge = edge("D", "A", 60, 60, start=None)

    with pytest.raises(ValueError):
        propagate_blocked(entities, [bad_edge], as_of_date=AS_OF)


# --- property test -------------------------------------------------------


@given(graph=graphs())
def test_reason_codes_imply_expected_status(graph):
    result = propagate_blocked(
        graph.entities,
        graph.ownership_edges,
        identity_link_edges=graph.identity_link_edges,
        control_edges=graph.control_edges,
        as_of_date=graph.as_of_date,
    )

    for r in result.values():
        if "BAND_UNCERTAINTY" in r.reason_codes or "AMBIGUOUS_OWNER" in r.reason_codes:
            assert r.status == "AMBIGUOUS"
        if "OWNED_BY_BLOCKED" in r.reason_codes or "AGGREGATE_OWNERSHIP" in r.reason_codes:
            assert r.status == "BLOCKED"
