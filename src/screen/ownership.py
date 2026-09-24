"""Ownership-based blocking (BLOCKED/AMBIGUOUS/CLEAR) per docs/specs/ownership_rules.md.

Implements statuses, reason codes, and capped reported sums (spec §5, §5.1,
§5.2, §7, §9). `evidence_paths` and `effective_ownership` (§8) are left as
empty placeholders -- they get their own implementation and tests later.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Optional


@dataclass(frozen=True)
class Entity:
    id: str
    kind: str  # "Person" | "Company"
    designated: bool = False
    designation_start: Optional[date] = None
    designation_end: Optional[date] = None  # None = ongoing


@dataclass(frozen=True)
class OwnershipEdge:
    owner_id: str
    owned_id: str
    stake_lower: float  # 0-100
    stake_upper: float  # 0-100, >= stake_lower
    stake_known: bool = True
    source: str = ""
    start_date: Optional[date] = None
    end_date: Optional[date] = None  # None = ongoing
    start_date_inferred: bool = False


@dataclass(frozen=True)
class IdentityLinkEdge:
    """A possible_same_as link entity linking could not confirm or reject.

    Symmetric: entity_id/same_as_id name an unordered pair (spec §2, §5.1,
    v0.4). first_seen_date gives it the same half-open time validity as
    any other edge, using the open-ended case (first_seen_date <= as_of_date,
    no end date) -- see spec §7.

    Populated only from entity_linking.md tasks 1-2. Never from task 3
    (customer-to-graph matching) -- see spec §2 and §5.1.
    """
    entity_id: str
    same_as_id: str
    first_seen_date: date  # required: an undated link is an error at creation time


@dataclass(frozen=True)
class ControlEdge:
    """A PSC 'significant influence or control' entry with no ownership
    share (spec §4.5). Evidence only; never changes status (spec §10,
    reason code CONTROL_ONLY_LINK).
    """
    owner_id: str
    owned_id: str
    source: str = ""
    start_date: Optional[date] = None
    end_date: Optional[date] = None


@dataclass
class OwnershipResult:
    status: str  # "BLOCKED" | "AMBIGUOUS" | "CLEAR"
    blocked_owner_sum: tuple  # (lower_sum, upper_sum) from blocked owners only
    possible_owner_sum: float  # upper_sum from blocked and ambiguous owners
    evidence_paths: list = field(default_factory=list)
    control_links: list = field(default_factory=list)
    effective_ownership: dict = field(default_factory=dict)
    reason_codes: list = field(default_factory=list)
    as_of_date: Optional[date] = None


def _active(start, end, as_of_date):
    """Half-open [start, end) validity (spec §7).

    A null start is a data-integrity error, not an inactive fact -- §7
    requires ingestion to backfill a missing start date (START_DATE_INFERRED)
    before it ever reaches propagate_blocked().
    """
    if start is None:
        raise ValueError(
            "propagate_blocked(): edge, control edge, or active designation "
            "is missing a start date (spec §7) -- ingestion must backfill "
            "this before propagation, it is not treated as inactive"
        )
    if start > as_of_date:
        return False
    if end is not None and as_of_date >= end:
        return False
    return True


def _uncertainty_kind(edges, lower, upper, conflict):
    """Per-pair uncertainty kind (spec §4.4): conflict > unknown > band > none,
    mutually exclusive. For an overlap-combined edge, the kind reflects the
    resulting range, not which sources fed it: a single value is `none` even
    if one contributing source was unknown or a band; the range is `unknown`
    only if *every* contributing source was itself unknown; otherwise it is
    `band` (an overlap that narrows to something other than a point, for this
    finite exact/PSC-band/unknown stake model, always exactly equals one
    contributing known source's own range). Used by §9.1's reason-code
    conditions.
    """
    if conflict:
        return "conflict"
    if lower == upper:
        return "none"
    if all(not e.stake_known for e in edges):
        return "unknown"
    return "band"


def _true_bounds(edge):
    """Per-source (lower_open, upper_open): whether §4.2's true band/exact/
    unknown bound at that end is open (excludes the boundary value) or
    closed (includes it) -- §4.4's boundary-touching rule. Only the finite
    exact / Band A / Band B / Band C / unknown stake shapes are recognized;
    anything else falls back to closed-closed (not expected to occur for
    genuine PSC-band/exact/unknown-sourced data -- see docs/decisions.md,
    2026-09-24).
    """
    lower, upper = edge.stake_lower, edge.stake_upper
    if lower == upper or not edge.stake_known:
        return False, False
    if (lower, upper) == (25, 50):   # Band A: (25, 50]
        return True, False
    if (lower, upper) == (50, 75):   # Band B: (50, 75)
        return True, True
    if (lower, upper) == (75, 100):  # Band C: [75, 100]
        return False, False
    return False, False


def _point_truly_shared(edges, point):
    """Whether `point` -- the numeric intersection's lower==upper value --
    lies inside every edge's *true* range once each source's own open/closed
    bound (§4.2, via _true_bounds) is accounted for, not just its stored
    plain-number bound (§4.4, "boundary-touching ranges").
    """
    for e in edges:
        lower_open, upper_open = _true_bounds(e)
        if e.stake_lower == point and lower_open:
            return False
        if e.stake_upper == point and upper_open:
            return False
    return True


def _combine_ownership_edges(active_edges):
    """Combine same-pair active edges per spec §4.4.

    Overlapping ranges -> intersection. Non-overlapping ranges -> hull,
    flagged STAKE_CONFLICT. Applied to any active edges sharing an
    (owner_id, owned_id) pair, not only ones from different sources --
    the spec's combination rule is stated in terms of "two or more active
    edges", and duplicate same-source edges for one pair aren't otherwise
    addressed (docs/decisions.md, 2026-09-23).
    """
    by_pair = defaultdict(list)
    for e in active_edges:
        by_pair[(e.owner_id, e.owned_id)].append(e)

    combined = {}
    for pair, edges in by_pair.items():
        if len(edges) == 1:
            e = edges[0]
            lower, upper, conflict = e.stake_lower, e.stake_upper, False
            start_date_inferred = e.start_date_inferred
        else:
            inter_lower = max(e.stake_lower for e in edges)
            inter_upper = min(e.stake_upper for e in edges)
            if inter_lower < inter_upper or (
                inter_lower == inter_upper and _point_truly_shared(edges, inter_lower)
            ):
                lower, upper, conflict = inter_lower, inter_upper, False
            else:
                lower = min(e.stake_lower for e in edges)
                upper = max(e.stake_upper for e in edges)
                conflict = True
            start_date_inferred = any(e.start_date_inferred for e in edges)

        kind = _uncertainty_kind(edges, lower, upper, conflict)
        combined[pair] = {
            "lower": lower,
            "upper": upper,
            "known": kind != "unknown",
            "conflict": conflict,
            "kind": kind,
            "start_date_inferred": start_date_inferred,
        }
    return combined


def propagate_blocked(
    entities,
    ownership_edges,
    identity_link_edges=(),
    control_edges=(),
    as_of_date=None,
):
    """Compute BLOCKED/AMBIGUOUS/CLEAR status for every entity as of as_of_date.

    See docs/specs/ownership_rules.md §5 (algorithm), §7 (time awareness),
    §9 (output) for the full specification.
    """
    entities_by_id = {e.id: e for e in entities}
    ids = list(entities_by_id.keys())

    # --- §7 time filtering, §4.5 ignored self-ownership edges ---
    active_ownership = [
        e for e in ownership_edges
        if e.owner_id != e.owned_id and _active(e.start_date, e.end_date, as_of_date)
    ]
    active_identity = [
        link for link in identity_link_edges if link.first_seen_date <= as_of_date
    ]
    active_control = [
        c for c in control_edges if _active(c.start_date, c.end_date, as_of_date)
    ]

    # --- §4.4 combine duplicate-pair edges ---
    combined = _combine_ownership_edges(active_ownership)
    incoming = defaultdict(list)  # owned_id -> [(owner_id, combined_info), ...]
    for (owner_id, owned_id), info in combined.items():
        incoming[owned_id].append((owner_id, info))

    # Symmetric identity-link neighbors (§5.1).
    identity_neighbors = defaultdict(set)
    for link in active_identity:
        identity_neighbors[link.entity_id].add(link.same_as_id)
        identity_neighbors[link.same_as_id].add(link.entity_id)

    # --- §5 seed ---
    blocked = {
        eid for eid, e in entities_by_id.items()
        if e.designated and _active(e.designation_start, e.designation_end, as_of_date)
    }
    possible = set()

    # --- §5 fixed-point loop ---
    changed = True
    while changed:
        changed = False
        for eid in ids:
            if eid in blocked:
                continue

            edges_in = incoming.get(eid, [])
            lower_sum = sum(info["lower"] for owner, info in edges_in if owner in blocked)
            if lower_sum >= 50:
                blocked.add(eid)
                possible.discard(eid)
                changed = True
                continue

            if eid not in possible:
                upper_sum = min(
                    100,
                    sum(
                        info["upper"] for owner, info in edges_in
                        if owner in blocked or owner in possible
                    ),
                )
                if upper_sum >= 50:
                    possible.add(eid)
                    changed = True
                    continue

                if any(
                    d in blocked or d in possible
                    for d in identity_neighbors.get(eid, ())
                ):
                    possible.add(eid)
                    changed = True

    # --- §9 output, computed from the final blocked/possible sets ---
    results = {}
    for eid in ids:
        e = entities_by_id[eid]
        status = "BLOCKED" if eid in blocked else ("AMBIGUOUS" if eid in possible else "CLEAR")

        edges_in = incoming.get(eid, [])
        blocked_edges = [(owner, info) for owner, info in edges_in if owner in blocked]
        possible_edges = [
            (owner, info) for owner, info in edges_in
            if owner in blocked or owner in possible
        ]

        lower_sum = sum(info["lower"] for _, info in blocked_edges)
        upper_sum_blocked = sum(info["upper"] for _, info in blocked_edges)
        upper_sum_possible = sum(info["upper"] for _, info in possible_edges)

        reason_codes = set()

        designated_active = e.designated and _active(
            e.designation_start, e.designation_end, as_of_date
        )
        if designated_active:
            reason_codes.add("DESIGNATED")

        if lower_sum >= 50:
            reason_codes.add("OWNED_BY_BLOCKED")
            if not any(info["lower"] >= 50 for _, info in blocked_edges):
                reason_codes.add("AGGREGATE_OWNERSHIP")

        if status == "AMBIGUOUS":
            for owner, info in edges_in:
                if owner in blocked:
                    if info["kind"] == "unknown":
                        reason_codes.add("UNKNOWN_STAKE")
                    elif info["kind"] == "band":
                        reason_codes.add("BAND_UNCERTAINTY")
                elif owner in possible:
                    reason_codes.add("AMBIGUOUS_OWNER")

            if any(
                d in blocked or d in possible
                for d in identity_neighbors.get(eid, ())
            ):
                reason_codes.add("LOW_CONFIDENCE_LINK")

        if any(info["kind"] == "conflict" for _, info in edges_in):
            reason_codes.add("STAKE_CONFLICT")

        if any(info["start_date_inferred"] for _, info in edges_in):
            reason_codes.add("START_DATE_INFERRED")

        controlling_links = [
            c for c in active_control
            if c.owned_id == eid and (c.owner_id in blocked or c.owner_id in possible)
        ]
        if controlling_links:
            reason_codes.add("CONTROL_ONLY_LINK")

        total_lower_all = sum(info["lower"] for _, info in edges_in)
        if total_lower_all > 100:
            reason_codes.add("OWNERSHIP_OVER_100")

        results[eid] = OwnershipResult(
            status=status,
            blocked_owner_sum=(min(100, lower_sum), min(100, upper_sum_blocked)),
            possible_owner_sum=min(100, upper_sum_possible),
            evidence_paths=[],
            control_links=controlling_links,
            effective_ownership={},
            reason_codes=sorted(reason_codes),
            as_of_date=as_of_date,
        )

    return results
