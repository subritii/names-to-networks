"""Unit tests for propagate_blocked()'s evidence field, one per worked
example EV1-EV11 in docs/specs/ownership_rules.md §9.2.5.

propagate_blocked() does not compute real evidence yet: OwnershipResult.evidence
is currently always None, and the fact-ID helpers (designation_fact_id() etc.)
raise NotImplementedError. Every test in this file is expected to fail until
§9.2 is implemented. Do not weaken these tests to make code pass -- see the
ownership-rules skill and CLAUDE.md.
"""

from datetime import date

from src.screen.ownership import (
    Entity,
    IdentityLinkEdge,
    OwnershipEdge,
    designation_fact_id,
    identity_link_fact_id,
    ownership_fact_id,
    propagate_blocked,
)

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


def edge(owner, owned, lower, upper, known=True, source="test", start=EARLY, end=None):
    return OwnershipEdge(
        owner_id=owner,
        owned_id=owned,
        stake_lower=lower,
        stake_upper=upper,
        stake_known=known,
        source=source,
        start_date=start,
        end_date=end,
    )


def test_ev1_designated():
    # D designated -> D: DESIGNATED, rank 0, fact_ids = [D's designation], no steps.
    d = entity("D", designated=True, start=EARLY)
    result = propagate_blocked([d], [], as_of_date=AS_OF)
    ev = result["D"].evidence

    assert ev.status == "BLOCKED"
    assert ev.kind == "DESIGNATED"
    assert ev.rank == 0
    assert ev.fact_ids == [designation_fact_id(d)]
    assert ev.steps == []
    assert ev.depends_on == []


def test_ev2_cascade_b_does_not_cite_d_directly():
    # D owns 60% of A; A owns 50% of B (the §3 cascade).
    # A: rank 1, one step (D, [60,60]). B: rank 2, one step (A, [50,50]),
    # depends_on [A]. B does not cite D directly.
    d = entity("D", designated=True, start=EARLY)
    a = entity("A")
    b = entity("B")
    edge_da = edge("D", "A", 60, 60)
    edge_ab = edge("A", "B", 50, 50)
    result = propagate_blocked([d, a, b], [edge_da, edge_ab], as_of_date=AS_OF)

    ev_a = result["A"].evidence
    assert ev_a.rank == 1
    assert len(ev_a.steps) == 1
    assert ev_a.steps[0].owner_id == "D"
    assert ev_a.steps[0].stake_range == (60, 60)

    ev_b = result["B"].evidence
    assert ev_b.rank == 2
    assert len(ev_b.steps) == 1
    assert ev_b.steps[0].owner_id == "A"
    assert ev_b.steps[0].stake_range == (50, 50)
    assert ev_b.depends_on == ["A"]
    assert "D" not in ev_b.depends_on
    assert designation_fact_id(d) not in ev_b.fact_ids


def test_ev3_two_owners_needed():
    # D1 owns 25%, D2 owns 25% of A -> A: two steps, neither alone reaches 50.
    d1 = entity("D1", designated=True, start=EARLY)
    d2 = entity("D2", designated=True, start=EARLY)
    a = entity("A")
    edges = [edge("D1", "A", 25, 25), edge("D2", "A", 25, 25)]
    result = propagate_blocked([d1, d2, a], edges, as_of_date=AS_OF)

    ev = result["A"].evidence
    assert len(ev.steps) == 2
    assert {s.owner_id for s in ev.steps} == {"D1", "D2"}


def test_ev4_dominant_owner_only_d2_not_needed():
    # D1 owns 60%, D2 owns 10% of A -> A: one step (D1) only.
    d1 = entity("D1", designated=True, start=EARLY)
    d2 = entity("D2", designated=True, start=EARLY)
    a = entity("A")
    edges = [edge("D1", "A", 60, 60), edge("D2", "A", 10, 10)]
    result = propagate_blocked([d1, d2, a], edges, as_of_date=AS_OF)

    ev = result["A"].evidence
    assert len(ev.steps) == 1
    assert ev.steps[0].owner_id == "D1"


def test_ev5_tie_broken_by_owner_id():
    # D1 owns 60%, D2 owns 60% of A -> A: one step, D1 (tie broken by owner_id).
    d1 = entity("D1", designated=True, start=EARLY)
    d2 = entity("D2", designated=True, start=EARLY)
    a = entity("A")
    edges = [edge("D1", "A", 60, 60), edge("D2", "A", 60, 60)]
    result = propagate_blocked([d1, d2, a], edges, as_of_date=AS_OF)

    ev = result["A"].evidence
    assert len(ev.steps) == 1
    assert ev.steps[0].owner_id == "D1"


def test_ev6_band_a_ambiguous_ownership_step():
    # D owns Band A of X -> X: AMBIGUOUS, OWNERSHIP, one step (D, [25,50], band).
    d = entity("D", designated=True, start=EARLY)
    x = entity("X")
    edges = [edge("D", "X", 25, 50)]
    result = propagate_blocked([d, x], edges, as_of_date=AS_OF)

    assert result["X"].status == "AMBIGUOUS"
    ev = result["X"].evidence
    assert ev.kind == "OWNERSHIP"
    assert len(ev.steps) == 1
    assert ev.steps[0].owner_id == "D"
    assert ev.steps[0].stake_range == (25, 50)
    assert ev.steps[0].uncertainty_kind == "band"


def test_ev7_ambiguous_owner_cited_via_depends_on():
    # D owns Band A of X; X owns 100% of Y -> Y: AMBIGUOUS, one step
    # (X, owner_status AMBIGUOUS), depends_on [X]; X's rank < Y's rank.
    d = entity("D", designated=True, start=EARLY)
    x = entity("X")
    y = entity("Y")
    edges = [edge("D", "X", 25, 50), edge("X", "Y", 100, 100)]
    result = propagate_blocked([d, x, y], edges, as_of_date=AS_OF)

    ev_x = result["X"].evidence
    ev_y = result["Y"].evidence
    assert result["Y"].status == "AMBIGUOUS"
    assert len(ev_y.steps) == 1
    assert ev_y.steps[0].owner_id == "X"
    assert ev_y.steps[0].owner_status == "AMBIGUOUS"
    assert ev_y.depends_on == ["X"]
    assert ev_x.rank < ev_y.rank


def test_ev8_cycle_a_never_cites_b():
    # D owns 60% of A; A owns 60% of B; B owns 60% of A (cycle).
    # A: rank 1, cites D. B: rank 2, cites A. A's evidence never cites B.
    d = entity("D", designated=True, start=EARLY)
    a = entity("A")
    b = entity("B")
    edges = [edge("D", "A", 60, 60), edge("A", "B", 60, 60), edge("B", "A", 60, 60)]
    result = propagate_blocked([d, a, b], edges, as_of_date=AS_OF)

    ev_a = result["A"].evidence
    ev_b = result["B"].evidence
    assert ev_a.rank == 1
    assert [s.owner_id for s in ev_a.steps] == ["D"]
    assert ev_b.rank == 2
    assert [s.owner_id for s in ev_b.steps] == ["A"]
    assert "B" not in ev_a.depends_on


def test_ev9_conflict_cites_both_source_facts():
    # Source 1: D owns 60% of X; source 2: D owns 30% of X -> X: AMBIGUOUS,
    # one step, range [30,60], kind conflict, source_fact_ids = both facts.
    d = entity("D", designated=True, start=EARLY)
    x = entity("X")
    e1 = edge("D", "X", 60, 60, source="source_1")
    e2 = edge("D", "X", 30, 30, source="source_2")
    result = propagate_blocked([d, x], [e1, e2], as_of_date=AS_OF)

    ev = result["X"].evidence
    assert len(ev.steps) == 1
    step = ev.steps[0]
    assert step.stake_range == (30, 60)
    assert step.uncertainty_kind == "conflict"
    assert set(step.source_fact_ids) == {ownership_fact_id(e1), ownership_fact_id(e2)}


def test_ev10_identity_link_no_ownership_edges():
    # E linked to designated D, no ownership edges -> E: AMBIGUOUS,
    # IDENTITY_LINK, one step (owner D), source_fact_ids = [the link's fact
    # ID], depends_on [D].
    d = entity("D", designated=True, start=EARLY)
    e = entity("E", kind="Person")
    link = IdentityLinkEdge(entity_id="E", same_as_id="D", first_seen_date=EARLY)
    result = propagate_blocked([d, e], [], identity_link_edges=[link], as_of_date=AS_OF)

    ev = result["E"].evidence
    assert result["E"].status == "AMBIGUOUS"
    assert ev.kind == "IDENTITY_LINK"
    assert len(ev.steps) == 1
    assert ev.steps[0].owner_id == "D"
    assert ev.steps[0].source_fact_ids == [identity_link_fact_id(link)]
    assert ev.depends_on == ["D"]


def test_ev11_no_risk_entity_has_empty_evidence():
    # Entity with no risk -> NONE, rank null, everything empty.
    x = entity("X")
    result = propagate_blocked([x], [], as_of_date=AS_OF)

    ev = result["X"].evidence
    assert result["X"].status == "CLEAR"
    assert ev.kind == "NONE"
    assert ev.rank is None
    assert ev.fact_ids == []
    assert ev.steps == []
    assert ev.depends_on == []
