"""Ownership-based blocking (BLOCKED/AMBIGUOUS/CLEAR) per docs/specs/ownership_rules.md.

Not yet implemented. This module currently defines only the data model
(spec §2, §4.5) and the propagate_blocked() signature; the algorithm
itself (spec §5) is a stub. See tests/test_ownership_examples.py and
tests/test_ownership_properties.py for the required behavior.
"""

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


def propagate_blocked(
    entities,
    ownership_edges,
    identity_link_edges=(),
    control_edges=(),
    as_of_date=None,
):
    """Compute BLOCKED/AMBIGUOUS/CLEAR status for every entity as of as_of_date.

    See docs/specs/ownership_rules.md §5 (algorithm), §7 (time awareness),
    §9 (output) for the full specification. Not yet implemented.
    """
    raise NotImplementedError
