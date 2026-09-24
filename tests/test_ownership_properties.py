"""Property tests for propagate_blocked() per docs/specs/ownership_rules.md
Section 11, plus three tests added at the user's direction (docs/decisions.md,
2026-09-23): symmetry and no-effect-link properties for the symmetric
IdentityLinkEdge (v0.4), and a general time-consistency property covering all
fact types (designations, ownership edges, identity links, control edges),
not just the single-designation case in Section 11 item 5.

propagate_blocked() is currently a stub (raises NotImplementedError), so
every test in this file is expected to fail until it is implemented. Do not
weaken these tests to make code pass -- see the ownership-rules skill and
CLAUDE.md.
"""

import os
from dataclasses import dataclass, replace
from datetime import date, timedelta

import pytest
from hypothesis import event, given, settings
from hypothesis import strategies as st

from src.screen.ownership import (
    ControlEdge,
    Entity,
    IdentityLinkEdge,
    OwnershipEdge,
    designation_fact_id,
    identity_link_fact_id,
    ownership_fact_id,
    propagate_blocked,
)

settings.register_profile("dev", derandomize=True, max_examples=200, deadline=None)
settings.register_profile("ci", derandomize=True, max_examples=1000, deadline=None)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "dev"))

AS_OF = date(2026, 1, 1)
ENTITY_POOL = [f"E{i}" for i in range(8)]
SOURCES = ["source_a", "source_b", "source_c"]
STAKE_KINDS = ["exact", "band_a", "band_b", "band_c", "unknown"]
STATUS_ORDER = {"CLEAR": 0, "AMBIGUOUS": 1, "BLOCKED": 2}


@dataclass(frozen=True)
class Graph:
    entities: list
    ownership_edges: list
    identity_link_edges: list
    control_edges: list
    as_of_date: date


# --- shared primitives -------------------------------------------------


def _date_near(draw, as_of, low=-1500, high=500):
    offset = draw(st.one_of(st.just(0), st.integers(min_value=low, max_value=high)))
    return as_of + timedelta(days=offset)


def _active_interval(draw, as_of):
    start = _date_near(draw, as_of)
    end = None
    if draw(st.booleans()):
        end = start + timedelta(days=draw(st.integers(min_value=1, max_value=800)))
    return start, end


def _active_now(draw, as_of):
    """(start, end) guaranteed active at as_of: start <= as_of < end (or None)."""
    start = as_of - timedelta(days=draw(st.integers(min_value=0, max_value=1500)))
    end = None
    if draw(st.booleans()):
        end = as_of + timedelta(days=draw(st.integers(min_value=1, max_value=800)))
    return start, end


def _stake_bounds(kind, draw=None):
    if kind == "exact":
        p = draw(st.integers(min_value=0, max_value=100))
        return p, p, True
    if kind == "band_a":
        return 25, 50, True
    if kind == "band_b":
        return 50, 75, True
    if kind == "band_c":
        return 75, 100, True
    return 0, 100, False  # unknown


def _stake(draw):
    return _stake_bounds(draw(st.sampled_from(STAKE_KINDS)), draw=draw)


def _stake_kind(edge):
    if not edge.stake_known:
        return "unknown"
    bounds = (edge.stake_lower, edge.stake_upper)
    if bounds == (25, 50):
        return "band_a"
    if bounds == (50, 75):
        return "band_b"
    if bounds == (75, 100):
        return "band_c"
    if edge.stake_lower == edge.stake_upper:
        return "exact"
    return "other"


def _distinct_pair(draw, ids):
    first = draw(st.sampled_from(ids))
    remaining = [i for i in ids if i != first]
    second = draw(st.sampled_from(remaining))
    return first, second


def _ownership_edge(draw, owner, owned, as_of, source=None, stake=None):
    lower, upper, known = stake if stake is not None else _stake(draw)
    start, end = _active_interval(draw, as_of)
    return OwnershipEdge(
        owner_id=owner,
        owned_id=owned,
        stake_lower=lower,
        stake_upper=upper,
        stake_known=known,
        source=source if source is not None else draw(st.sampled_from(SOURCES)),
        start_date=start,
        end_date=end,
    )


def _entity(draw, eid, as_of):
    kind = draw(st.sampled_from(["Person", "Company"]))
    designated = draw(st.booleans())
    start = end = None
    if designated:
        start, end = _active_interval(draw, as_of)
    return Entity(
        id=eid, kind=kind, designated=designated,
        designation_start=start, designation_end=end,
    )


def _entity_by_id(entities, eid):
    return next(e for e in entities if e.id == eid)


# --- the reusable graph strategy ----------------------------------------


@st.composite
def graphs(draw, as_of=AS_OF, force=frozenset()):
    ids = draw(st.lists(st.sampled_from(ENTITY_POOL), min_size=2, max_size=8, unique=True))
    entities = [_entity(draw, eid, as_of) for eid in ids]

    edges = []
    for _ in range(draw(st.integers(min_value=0, max_value=6))):
        owner, owned = _distinct_pair(draw, ids)
        edges.append(_ownership_edge(draw, owner, owned, as_of))

    # Cycle: guaranteed for force={"cycle"}, otherwise ~50% of examples.
    if "cycle" in force or draw(st.booleans()):
        cyc_ids = draw(
            st.lists(st.sampled_from(ids), min_size=2, max_size=min(4, len(ids)), unique=True)
        )
        for i in range(len(cyc_ids)):
            owner = cyc_ids[i]
            owned = cyc_ids[(i + 1) % len(cyc_ids)]
            edges.append(_ownership_edge(draw, owner, owned, as_of))

    # Occasional explicit cascade chain: D (forced designated) -> A -> B,
    # both stakes >= 50, so a non-designated owner (A) itself becomes
    # BLOCKED via D and then blocks a further non-designated entity (B) --
    # the multi-hop cascade pattern from ownership_rules.md §6.1. Makes
    # "outcome:blocked_via_cascade" common across a run, not merely
    # incidental to the other random edge draws (docs/decisions.md, 2026-09-23).
    if len(ids) >= 3 and draw(st.integers(min_value=0, max_value=9)) < 6:
        d_id, a_id, b_id = draw(
            st.lists(st.sampled_from(ids), min_size=3, max_size=3, unique=True)
        )
        d_start, d_end = _active_now(draw, as_of)

        def _cascade_entity(e):
            # A and B must stay non-designated -- the earlier per-entity
            # `_entity()` draw randomly designates every id (~50% each),
            # which would otherwise defeat _has_cascade()'s non-designated
            # requirement on the owner/owned pair most of the time.
            if e.id == d_id:
                return replace(e, designated=True, designation_start=d_start, designation_end=d_end)
            if e.id in (a_id, b_id):
                return replace(e, designated=False, designation_start=None, designation_end=None)
            return e

        entities = [_cascade_entity(e) for e in entities]
        for owner, owned in ((d_id, a_id), (a_id, b_id)):
            start, end = _active_now(draw, as_of)
            edges.append(
                OwnershipEdge(
                    owner_id=owner, owned_id=owned, stake_lower=60, stake_upper=60,
                    stake_known=True, source=draw(st.sampled_from(SOURCES)),
                    start_date=start, end_date=end,
                )
            )

    # One edge per stake kind, each independently ~50%, so all three PSC
    # bands, exact, and unknown stakes show up densely across a run rather
    # than at 1-in-5 odds on ordinary edges alone.
    for kind in STAKE_KINDS:
        if draw(st.booleans()):
            owner, owned = _distinct_pair(draw, ids)
            edges.append(_ownership_edge(draw, owner, owned, as_of, stake=_stake_bounds(kind, draw=draw)))

    # Duplicate edges from different sources on the same pair (~50%):
    # independently-drawn stakes give both overlapping (intersection) and
    # disjoint (hull + STAKE_CONFLICT) cases across a run.
    if draw(st.booleans()):
        owner, owned = _distinct_pair(draw, ids)
        edges.append(_ownership_edge(draw, owner, owned, as_of, source="source_a"))
        edges.append(_ownership_edge(draw, owner, owned, as_of, source="source_b"))

    control_edges = []
    if draw(st.booleans()):
        owner, owned = _distinct_pair(draw, ids)
        start, end = _active_interval(draw, as_of)
        control_edges.append(
            ControlEdge(
                owner_id=owner, owned_id=owned,
                source=draw(st.sampled_from(SOURCES)),
                start_date=start, end_date=end,
            )
        )

    identity_links = []
    if "identity_link" in force or draw(st.booleans()):
        a, b = _distinct_pair(draw, ids)
        if draw(st.booleans()):
            a, b = b, a
        identity_links.append(
            IdentityLinkEdge(entity_id=a, same_as_id=b, first_seen_date=_date_near(draw, as_of))
        )

    return Graph(entities, edges, identity_links, control_edges, as_of)


@st.composite
def graphs_no_designations(draw, as_of=AS_OF):
    g = draw(graphs(as_of=as_of))
    entities = [
        replace(e, designated=False, designation_start=None, designation_end=None)
        for e in g.entities
    ]
    return replace(g, entities=entities)


@st.composite
def graphs_single_controlled_designation(draw, as_of=AS_OF):
    """Exactly one entity D is designated, with a controlled start s and no
    other entity ever designated -- isolates property 5's claim to D's own
    designation. A single downstream edge D->owned (dated far in the past,
    so it is active at any as_of used by the test) makes "anything
    downstream" meaningful; other random edges never touch the (D, owned)
    pair, so they cannot change the guaranteed downstream result via a
    Section 4.4 multi-source combination.
    """
    ids = draw(st.lists(st.sampled_from(ENTITY_POOL), min_size=2, max_size=6, unique=True))
    d_id = ids[0]
    owned = draw(st.sampled_from(ids[1:]))
    s = _date_near(draw, as_of, low=-1000, high=1000)
    end = None
    if draw(st.booleans()):
        end = s + timedelta(days=draw(st.integers(min_value=1, max_value=800)))

    entities = [Entity(id=d_id, kind="Company", designated=True, designation_start=s, designation_end=end)]
    for eid in ids[1:]:
        kind = draw(st.sampled_from(["Person", "Company"]))
        entities.append(Entity(id=eid, kind=kind, designated=False))

    edges = [
        OwnershipEdge(
            owner_id=d_id, owned_id=owned, stake_lower=60, stake_upper=60,
            stake_known=True, source="test", start_date=date(2000, 1, 1), end_date=None,
        )
    ]
    for _ in range(draw(st.integers(min_value=0, max_value=4))):
        owner = draw(st.sampled_from(ids))
        # Exclude self-pairs, and specifically (d_id, owned) -- other random
        # edges must never touch that pair, or they could change its
        # combined range via a §4.4 multi-source combination and break the
        # guaranteed D->owned result this strategy exists to isolate.
        excluded = {owner, owned} if owner == d_id else {owner}
        candidates = [i for i in ids if i not in excluded]
        if not candidates:
            continue
        target = draw(st.sampled_from(candidates))
        edges.append(_ownership_edge(draw, owner, target, as_of))

    return Graph(entities, edges, [], [], as_of), s, owned


# --- monotonicity's risk-increasing change --------------------------------


def _active_ownership_edges(edges, as_of):
    return [e for e in edges if e.start_date <= as_of and (e.end_date is None or as_of < e.end_date)]


def _single_source_edge_indices(edges, as_of):
    by_pair = {}
    for i, e in enumerate(edges):
        if e.start_date <= as_of and (e.end_date is None or as_of < e.end_date):
            by_pair.setdefault((e.owner_id, e.owned_id), []).append(i)
    return [idxs[0] for idxs in by_pair.values() if len(idxs) == 1]


def _edge_free_pairs(ids, edges, as_of):
    active_pairs = {(e.owner_id, e.owned_id) for e in _active_ownership_edges(edges, as_of)}
    return [(a, b) for a in ids for b in ids if a != b and (a, b) not in active_pairs]


def _designated_active_ids(entities, as_of):
    return [
        e.id for e in entities
        if e.designated and e.designation_start is not None
        and e.designation_start <= as_of
        and (e.designation_end is None or as_of < e.designation_end)
    ]


@st.composite
def _risk_increase(draw, graph):
    """One risk-increasing change per Section 11 item 1, restricted (per
    docs/decisions.md, 2026-09-23) to constructions that are monotone even
    across Section 4.4 conflict resolution: never add an edge to a pair
    that already has an active edge, and only raise bounds on single-source
    pairs.
    """
    as_of = graph.as_of_date
    ids = [e.id for e in graph.entities]
    entities = list(graph.entities)
    edges = list(graph.ownership_edges)
    links = list(graph.identity_link_edges)

    designated_active = set(_designated_active_ids(entities, as_of))
    undesignated = [e for e in entities if e.id not in designated_active]
    free_pairs = _edge_free_pairs(ids, edges, as_of)
    blocked_free_pairs = [p for p in free_pairs if p[0] in designated_active]
    raise_candidates = [i for i in _single_source_edge_indices(edges, as_of) if edges[i].stake_upper < 100]

    kinds = []
    if undesignated:
        kinds.append("designate")
    if blocked_free_pairs:
        kinds.append("edge_from_blocked")
    if designated_active:
        kinds.append("identity_link_to_blocked")
    if raise_candidates:
        kinds.append("raise_bounds")

    if not kinds:
        event("mono:noop")
        return graph

    kind = draw(st.sampled_from(kinds))
    event(f"mono:{kind}")

    if kind == "designate":
        target = draw(st.sampled_from(undesignated))
        start, end = _active_now(draw, as_of)
        entities = [
            replace(e, designated=True, designation_start=start, designation_end=end)
            if e.id == target.id else e
            for e in entities
        ]
    elif kind == "edge_from_blocked":
        owner, owned = draw(st.sampled_from(blocked_free_pairs))
        start, end = _active_now(draw, as_of)
        lower, upper, known = _stake_bounds(draw(st.sampled_from(STAKE_KINDS)), draw=draw)
        edges.append(
            OwnershipEdge(
                owner_id=owner, owned_id=owned, stake_lower=lower, stake_upper=upper,
                stake_known=known, source=draw(st.sampled_from(SOURCES)),
                start_date=start, end_date=end,
            )
        )
    elif kind == "identity_link_to_blocked":
        d_id = draw(st.sampled_from(sorted(designated_active)))
        target = draw(st.sampled_from([i for i in ids if i != d_id]))
        first_seen = as_of - timedelta(days=draw(st.integers(min_value=0, max_value=500)))
        links.append(IdentityLinkEdge(entity_id=target, same_as_id=d_id, first_seen_date=first_seen))
    else:  # raise_bounds
        idx = draw(st.sampled_from(raise_candidates))
        old = edges[idx]
        new_upper = old.stake_upper + draw(st.integers(min_value=1, max_value=100 - old.stake_upper))
        new_lower = old.stake_lower
        if old.stake_lower < new_upper and draw(st.booleans()):
            new_lower = old.stake_lower + draw(st.integers(min_value=0, max_value=new_upper - old.stake_lower))
        edges[idx] = replace(old, stake_lower=new_lower, stake_upper=new_upper)

    return replace(graph, entities=entities, ownership_edges=edges, identity_link_edges=links)


# --- general time consistency: strip facts not yet valid at d -------------


def _strip_future_facts(graph, d):
    entities = [
        replace(e, designated=False, designation_start=None, designation_end=None)
        if e.designated and e.designation_start > d else e
        for e in graph.entities
    ]
    edges = [e for e in graph.ownership_edges if e.start_date <= d]
    links = [l for l in graph.identity_link_edges if l.first_seen_date <= d]
    controls = [c for c in graph.control_edges if c.start_date <= d]
    return replace(graph, entities=entities, ownership_edges=edges, identity_link_edges=links, control_edges=controls)


# --- coverage evidence via hypothesis.event() -----------------------------


def _has_cycle(entities, edges):
    adjacency = {}
    for e in edges:
        adjacency.setdefault(e.owner_id, []).append(e.owned_id)
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {e.id: WHITE for e in entities}

    def visit(node):
        color[node] = GRAY
        for nxt in adjacency.get(node, []):
            if color.get(nxt, WHITE) == GRAY:
                return True
            if color.get(nxt, WHITE) == WHITE and visit(nxt):
                return True
        color[node] = BLACK
        return False

    return any(color[e.id] == WHITE and visit(e.id) for e in entities)


def _has_duplicate_source_pair(edges):
    pairs = {}
    for e in edges:
        pairs.setdefault((e.owner_id, e.owned_id), set()).add(e.source)
    return any(len(sources) > 1 for sources in pairs.values())


def _has_boundary_date(graph):
    dates = []
    for e in graph.entities:
        if e.designation_start is not None:
            dates.append(e.designation_start)
        if e.designation_end is not None:
            dates.append(e.designation_end)
    for e in graph.ownership_edges:
        dates.append(e.start_date)
        if e.end_date is not None:
            dates.append(e.end_date)
    for link in graph.identity_link_edges:
        dates.append(link.first_seen_date)
    for c in graph.control_edges:
        dates.append(c.start_date)
        if c.end_date is not None:
            dates.append(c.end_date)
    return graph.as_of_date in dates


def _record_structural_events(graph):
    for kind in {_stake_kind(e) for e in graph.ownership_edges}:
        event(f"stake:{kind}")
    if _has_cycle(graph.entities, graph.ownership_edges):
        event("cycle")
    if _has_duplicate_source_pair(graph.ownership_edges):
        event("duplicate_source_pair")
    if graph.control_edges:
        event("control_edge")
    if graph.identity_link_edges:
        event("identity_link")
    if _has_boundary_date(graph):
        event("boundary_date")


def _has_cascade(entities, edges, result):
    """A non-designated entity is BLOCKED and at least one owner with an
    active edge into it is itself BLOCKED and non-designated -- i.e. its
    blocked status was reached through at least one intermediate owner,
    not directly from a designation (ownership_rules.md §6.1, the §3 trap
    example).
    """
    designated_ids = {e.id for e in entities if e.designated}
    pairs = {(e.owner_id, e.owned_id) for e in edges}
    for owner_id, owned_id in pairs:
        owned_result = result.get(owned_id)
        owner_result = result.get(owner_id)
        if owned_result is None or owner_result is None:
            continue
        if (
            owned_result.status == "BLOCKED"
            and owned_id not in designated_ids
            and owner_result.status == "BLOCKED"
            and owner_id not in designated_ids
        ):
            return True
    return False


def _record_outcome_events(entities, edges, result):
    statuses = {r.status for r in result.values()}
    if "AMBIGUOUS" in statuses:
        event("outcome:ambiguous_present")
    if any(r.status == "BLOCKED" and not _entity_by_id(entities, eid).designated for eid, r in result.items()):
        event("outcome:blocked_via_ownership")
    if any("STAKE_CONFLICT" in r.reason_codes for r in result.values()):
        event("outcome:stake_conflict")
    if any("LOW_CONFIDENCE_LINK" in r.reason_codes for r in result.values()):
        event("outcome:identity_link_fires")
    if any("OWNERSHIP_OVER_100" in r.reason_codes for r in result.values()):
        event("outcome:ownership_over_100")
    if _has_cascade(entities, edges, result):
        event("outcome:blocked_via_cascade")


def _run(graph):
    return propagate_blocked(
        graph.entities,
        graph.ownership_edges,
        identity_link_edges=graph.identity_link_edges,
        control_edges=graph.control_edges,
        as_of_date=graph.as_of_date,
    )


# --- the property tests ----------------------------------------------------


@given(data=st.data())
def test_property_1_monotonicity(data):
    graph = data.draw(graphs())
    _record_structural_events(graph)
    modified = data.draw(_risk_increase(graph))

    before = _run(graph)
    after = _run(modified)
    _record_outcome_events(graph.entities, graph.ownership_edges, before)
    _record_outcome_events(modified.entities, modified.ownership_edges, after)

    for eid in {e.id for e in graph.entities}:
        assert STATUS_ORDER[after[eid].status] >= STATUS_ORDER[before[eid].status]


@given(graph=graphs())
def test_property_2_range_validity(graph):
    _record_structural_events(graph)
    result = _run(graph)
    _record_outcome_events(graph.entities, graph.ownership_edges, result)

    for r in result.values():
        blocked_lower, blocked_upper = r.blocked_owner_sum
        assert 0 <= blocked_lower <= blocked_upper <= r.possible_owner_sum <= 100


@given(data=st.data())
def test_property_3_order_independence(data):
    graph = data.draw(graphs())
    _record_structural_events(graph)

    shuffled = Graph(
        list(data.draw(st.permutations(graph.entities))),
        list(data.draw(st.permutations(graph.ownership_edges))),
        list(data.draw(st.permutations(graph.identity_link_edges))),
        list(data.draw(st.permutations(graph.control_edges))),
        graph.as_of_date,
    )

    result_a = _run(graph)
    result_b = _run(shuffled)
    _record_outcome_events(graph.entities, graph.ownership_edges, result_a)

    for eid in {e.id for e in graph.entities}:
        assert result_a[eid].status == result_b[eid].status


@pytest.mark.timeout(10)
@given(graph=graphs(force=frozenset({"cycle"})))
def test_property_4_cycle_safety(graph):
    _record_structural_events(graph)
    result = _run(graph)
    _record_outcome_events(graph.entities, graph.ownership_edges, result)
    assert set(result.keys()) == {e.id for e in graph.entities}


@given(data=st.data())
def test_property_5_time_consistency_designation(data):
    graph, s, owned = data.draw(graphs_single_controlled_designation())
    d = s - timedelta(days=data.draw(st.integers(min_value=1, max_value=1000)))

    result_before = _run(replace(graph, as_of_date=d))
    _record_outcome_events(graph.entities, graph.ownership_edges, result_before)
    for r in result_before.values():
        assert r.status == "CLEAR"

    result_active = _run(replace(graph, as_of_date=s))
    _record_outcome_events(graph.entities, graph.ownership_edges, result_active)
    d_id = graph.entities[0].id
    assert result_active[d_id].status == "BLOCKED"
    assert result_active[owned].status == "BLOCKED"


@given(graph=graphs())
def test_property_6_designated_are_blocked(graph):
    _record_structural_events(graph)
    result = _run(graph)
    _record_outcome_events(graph.entities, graph.ownership_edges, result)

    for e in graph.entities:
        if e.designated and e.designation_start <= graph.as_of_date and (
            e.designation_end is None or graph.as_of_date < e.designation_end
        ):
            assert result[e.id].status == "BLOCKED"


@given(graph=graphs_no_designations())
def test_property_7_no_designations_no_risk(graph):
    result = _run(graph)
    _record_outcome_events(graph.entities, graph.ownership_edges, result)
    for r in result.values():
        assert r.status == "CLEAR"


@given(graph=graphs())
def test_property_8_idempotence(graph):
    result_a = _run(graph)
    result_b = _run(graph)
    _record_outcome_events(graph.entities, graph.ownership_edges, result_a)
    for eid in {e.id for e in graph.entities}:
        assert result_a[eid].status == result_b[eid].status


@given(graph=graphs())
def test_property_9_only_blocked_owners_confirm(graph):
    _record_structural_events(graph)
    result_before = _run(graph)
    _record_outcome_events(graph.entities, graph.ownership_edges, result_before)

    ambiguous_ids = {eid for eid, r in result_before.items() if r.status == "AMBIGUOUS"}
    filtered_edges = [e for e in graph.ownership_edges if e.owner_id not in ambiguous_ids]
    result_after = _run(replace(graph, ownership_edges=filtered_edges))
    _record_outcome_events(graph.entities, filtered_edges, result_after)

    for eid, r in result_before.items():
        if r.status == "BLOCKED":
            assert result_after[eid].status == "BLOCKED"


@given(graph=graphs(force=frozenset({"identity_link"})))
def test_property_10_identity_linked_ambiguity(graph):
    _record_structural_events(graph)
    result = _run(graph)
    _record_outcome_events(graph.entities, graph.ownership_edges, result)

    for link in graph.identity_link_edges:
        if link.first_seen_date > graph.as_of_date:
            continue
        a_status = result[link.entity_id].status
        b_status = result[link.same_as_id].status
        if a_status in ("BLOCKED", "AMBIGUOUS"):
            assert b_status != "CLEAR"
        if b_status in ("BLOCKED", "AMBIGUOUS"):
            assert a_status != "CLEAR"


@given(graph=graphs(force=frozenset({"identity_link"})))
def test_property_11_identity_link_symmetry(graph):
    _record_structural_events(graph)
    result_before = _run(graph)

    swapped_links = [
        IdentityLinkEdge(entity_id=link.same_as_id, same_as_id=link.entity_id, first_seen_date=link.first_seen_date)
        for link in graph.identity_link_edges
    ]
    result_after = _run(replace(graph, identity_link_edges=swapped_links))
    _record_outcome_events(graph.entities, graph.ownership_edges, result_before)
    _record_outcome_events(graph.entities, graph.ownership_edges, result_after)

    for eid in {e.id for e in graph.entities}:
        assert result_before[eid].status == result_after[eid].status


@given(graph=graphs())
def test_property_12_no_effect_identity_links(graph):
    _record_structural_events(graph)

    # Construct two fresh, isolated, undesignated entities with no ownership
    # edges to or from anyone else, linked by one IdentityLinkEdge -- CLEAR
    # on both ends by construction, regardless of what the rest of the
    # drawn graph looks like (docs/decisions.md, 2026-09-23).
    iso_a, iso_b = "ISO0", "ISO1"
    entities = graph.entities + [
        Entity(id=iso_a, kind="Company", designated=False),
        Entity(id=iso_b, kind="Person", designated=False),
    ]
    no_effect_link = IdentityLinkEdge(entity_id=iso_a, same_as_id=iso_b, first_seen_date=graph.as_of_date)
    with_link = replace(graph, entities=entities, identity_link_edges=graph.identity_link_edges + [no_effect_link])

    result_with = _run(with_link)
    _record_outcome_events(entities, graph.ownership_edges, result_with)
    assert result_with[iso_a].status == "CLEAR"
    assert result_with[iso_b].status == "CLEAR"

    without_link = replace(with_link, identity_link_edges=graph.identity_link_edges)
    result_without = _run(without_link)
    _record_outcome_events(entities, graph.ownership_edges, result_without)

    for eid in {e.id for e in entities}:
        assert result_with[eid].status == result_without[eid].status


@given(graph=graphs())
def test_property_13_general_time_consistency(graph):
    _record_structural_events(graph)
    d = graph.as_of_date
    result_before = _run(graph)
    _record_outcome_events(graph.entities, graph.ownership_edges, result_before)

    stripped = _strip_future_facts(graph, d)
    result_after = _run(stripped)
    _record_outcome_events(stripped.entities, stripped.ownership_edges, result_after)

    for eid in {e.id for e in graph.entities}:
        assert result_before[eid].status == result_after[eid].status


# --- §9.2.6 evidence property tests -----------------------------------------
#
# propagate_blocked() does not compute real evidence yet (evidence is always
# None, and the fact-ID helpers raise NotImplementedError), so every test
# below is expected to fail until §9.2 is implemented. Added at the user's
# direction, per docs/decisions.md (2026-09-24); reuses the graphs() strategy
# above. Do not weaken these to make code pass.


def _collect_cited(evidence_by_id, entity_id, seen=None):
    """Recursively collect the fact IDs and entity IDs cited by entity_id's
    evidence and, transitively, by every entity in its depends_on closure
    (§9.2.6 item 1, sufficiency)."""
    if seen is None:
        seen = set()
    if entity_id in seen:
        return set(), set()
    seen.add(entity_id)
    ev = evidence_by_id[entity_id]
    fact_ids = set(ev.fact_ids)
    for step in ev.steps:
        fact_ids.update(step.source_fact_ids)
    entity_ids = {entity_id}
    for dep in ev.depends_on:
        sub_facts, sub_entities = _collect_cited(evidence_by_id, dep, seen)
        fact_ids |= sub_facts
        entity_ids |= sub_entities
    return fact_ids, entity_ids


def _minimal_graph(graph, fact_ids, entity_ids):
    """The graph reduced to only entity_ids, with each entity's designation
    kept only if its own designation fact is in fact_ids, and only the
    ownership edges / identity links whose fact ID is in fact_ids (§9.2.6
    item 1). Control edges are omitted -- they never affect status (§10)."""
    entities = []
    for e in graph.entities:
        if e.id not in entity_ids:
            continue
        if e.designated and designation_fact_id(e) in fact_ids:
            entities.append(e)
        else:
            entities.append(
                replace(e, designated=False, designation_start=None, designation_end=None)
            )
    edges = [e for e in graph.ownership_edges if ownership_fact_id(e) in fact_ids]
    links = [l for l in graph.identity_link_edges if identity_link_fact_id(l) in fact_ids]
    return Graph(entities, edges, links, [], graph.as_of_date)


@given(graph=graphs())
def test_evidence_property_1_sufficiency(graph):
    result = _run(graph)
    evidence_by_id = {eid: r.evidence for eid, r in result.items()}

    for eid, r in result.items():
        if r.status == "CLEAR":
            continue
        fact_ids, entity_ids = _collect_cited(evidence_by_id, eid)
        minimal = _minimal_graph(graph, fact_ids, entity_ids)
        minimal_result = _run(minimal)
        assert minimal_result[eid].status == r.status


@given(graph=graphs())
def test_evidence_property_2_necessity_blocked(graph):
    result = _run(graph)
    for eid, r in result.items():
        ev = r.evidence
        if r.status != "BLOCKED" or ev.kind != "OWNERSHIP":
            continue
        lowers = [s.stake_range[0] for s in ev.steps]
        total = sum(lowers)
        assert total >= 50
        for i in range(len(lowers)):
            assert total - lowers[i] < 50


@given(graph=graphs())
def test_evidence_property_3_valid_citations(graph):
    active_ownership_ids = {
        ownership_fact_id(e)
        for e in graph.ownership_edges
        if e.start_date <= graph.as_of_date
        and (e.end_date is None or graph.as_of_date < e.end_date)
    }
    active_link_ids = {
        identity_link_fact_id(link)
        for link in graph.identity_link_edges
        if link.first_seen_date <= graph.as_of_date
    }
    active_designation_ids = {
        designation_fact_id(e)
        for e in graph.entities
        if e.designated
        and e.designation_start <= graph.as_of_date
        and (e.designation_end is None or graph.as_of_date < e.designation_end)
    }
    all_active_ids = active_ownership_ids | active_link_ids | active_designation_ids

    result = _run(graph)
    for r in result.values():
        ev = r.evidence
        cited = set(ev.fact_ids)
        for step in ev.steps:
            cited.update(step.source_fact_ids)
        assert cited <= all_active_ids


def _walk_acyclic(evidence_by_id, result, entity_id, on_path, visited):
    """DFS from entity_id through depends_on, asserting no cycle (a real
    cycle is a back-edge to an ancestor still `on_path` -- a diamond, where
    two different entities both cite the same lower-ranked owner, is fine
    and must not be flagged: that's why this tracks the current path
    separately from the set of everything visited so far, rather than
    forbidding a second visit outright)."""
    assert entity_id not in on_path
    if entity_id in visited:
        return
    visited.add(entity_id)
    on_path.add(entity_id)
    ev = evidence_by_id[entity_id]
    if ev.kind != "DESIGNATED":
        for dep in ev.depends_on:
            dep_status = result[dep].status
            assert dep_status in ("BLOCKED", "AMBIGUOUS")
            if ev.status == "BLOCKED":
                assert dep_status == "BLOCKED"
                assert evidence_by_id[dep].rank < ev.rank
            elif dep_status == "AMBIGUOUS":
                assert evidence_by_id[dep].rank < ev.rank
            _walk_acyclic(evidence_by_id, result, dep, on_path, visited)
    on_path.discard(entity_id)


@given(graph=graphs())
def test_evidence_property_4_acyclic(graph):
    result = _run(graph)
    evidence_by_id = {eid: r.evidence for eid, r in result.items()}

    for eid, r in result.items():
        if r.status == "CLEAR":
            continue
        visited = set()
        _walk_acyclic(evidence_by_id, result, eid, set(), visited)
        assert any(evidence_by_id[x].kind == "DESIGNATED" for x in visited)


@given(graph=graphs())
def test_evidence_property_5_designated_and_clear(graph):
    result = _run(graph)
    for e in graph.entities:
        r = result[e.id]
        ev = r.evidence
        if r.status == "BLOCKED" and ev.kind == "DESIGNATED":
            assert ev.fact_ids == [designation_fact_id(e)]
            assert ev.steps == []
        if r.status == "CLEAR":
            assert ev.kind == "NONE"
            assert ev.rank is None
            assert ev.fact_ids == []
            assert ev.steps == []
            assert ev.depends_on == []


@given(data=st.data())
def test_evidence_property_6_order_independence(data):
    graph = data.draw(graphs())
    shuffled = Graph(
        list(data.draw(st.permutations(graph.entities))),
        list(data.draw(st.permutations(graph.ownership_edges))),
        list(data.draw(st.permutations(graph.identity_link_edges))),
        list(data.draw(st.permutations(graph.control_edges))),
        graph.as_of_date,
    )
    result_a = _run(graph)
    result_b = _run(shuffled)

    for eid in {e.id for e in graph.entities}:
        ev_a, ev_b = result_a[eid].evidence, result_b[eid].evidence
        assert ev_a.rank == ev_b.rank
        assert ev_a.kind == ev_b.kind
        assert ev_a.fact_ids == ev_b.fact_ids
        assert ev_a.depends_on == ev_b.depends_on
        steps_a = [(s.owner_id, s.stake_range, tuple(s.source_fact_ids)) for s in ev_a.steps]
        steps_b = [(s.owner_id, s.stake_range, tuple(s.source_fact_ids)) for s in ev_b.steps]
        assert steps_a == steps_b


@given(graph=graphs())
def test_evidence_property_7_fact_id_stability(graph):
    for e in graph.entities:
        if e.designated:
            assert designation_fact_id(e) == designation_fact_id(e)
    for e in graph.ownership_edges:
        assert ownership_fact_id(e) == ownership_fact_id(e)
    for link in graph.identity_link_edges:
        assert identity_link_fact_id(link) == identity_link_fact_id(link)

    # Distinct facts get distinct IDs: any two ownership edges that differ in
    # any field must get different fact IDs.
    edge_keys = [
        (
            e.owner_id, e.owned_id, e.stake_lower, e.stake_upper, e.stake_known,
            e.source, e.start_date, e.end_date, e.start_date_inferred,
        )
        for e in graph.ownership_edges
    ]
    edge_ids = [ownership_fact_id(e) for e in graph.ownership_edges]
    for i in range(len(edge_keys)):
        for j in range(i + 1, len(edge_keys)):
            if edge_keys[i] != edge_keys[j]:
                assert edge_ids[i] != edge_ids[j]
