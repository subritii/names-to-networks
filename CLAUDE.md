# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

"From Names to Networks" is a prototype risk-intelligence platform for sanctions/financial-crime screening. The core thesis: name-matching screening misses risk that arrives through ownership relationships (a clean-named company owned or controlled by a sanctioned person), while also generating heavy false-positive rates. The project builds a time-aware graph of people, companies, and ownership from open data, and compares a name-matching baseline screener against a graph-aware screener on the same test sets, plus a real-world case study (the A7 sanctions-evasion network). See `README.md` for full background, motivation, and the current enforcement cases that motivate it.

**Current state:** structural skeleton only. `src/` subpackages exist with empty `__init__.py` files; no pipeline logic, screeners, or tests have been implemented yet.

## The specs are binding — read before writing code

`docs/specs/` contains four specs that are the single source of truth for their respective areas. They are tracked and public in the GitHub repo by design (portfolio evidence and version history — see "Docs and decisions" below) and must be read in full before touching the corresponding code — do not re-derive this logic from intuition or from the README's simplified description, which the specs supersede with precise, edge-case-tested rules.

| Area | Spec | Governs |
|---|---|---|
| Ownership/blocking | `docs/specs/ownership_rules.md` | `src/screen/ownership.py`, `propagate_blocked()` |
| Entity linking | `docs/specs/entity_linking.md` | `src/resolve/` |
| Evaluation | `docs/specs/evaluation.md` | `src/evaluate/` |
| L1 review agent | `docs/specs/l1_agent.md` | `src/agent/` |

Each spec has a **Status** line (`complete`, `draft`, or `stub`) and, where relevant, an explicit **TBD/open** section. Settled sections must be implemented exactly as written; TBD/open items must never be filled in by inference — stop and propose options instead, grounded in the actual ingested data or actual screener output once it exists.

`.claude/skills/` has one auto-loading skill per spec (`ownership-rules`, `entity-linking`, `evaluation`, `l1-agent`) that surfaces the highest-stakes rules whenever the corresponding code is touched, plus two manually-invoked review workflows: `/leakage-review` (required before publishing any evaluation results — checks for train/test leakage, wrong-split metrics, forbidden accuracy usage) and `/spec-check <spec-name>` (fresh-context subagent diff review against a given spec).

## Non-obvious rules most likely to be gotten wrong

- **Ownership blocking is status propagation, not percentage multiplication.** If A owns 60% of B and B owns 50% of C, C is BLOCKED — B's stake counts in full once B is blocked; it is never multiplied down the chain. Multiplied ("effective") ownership is a risk feature only and must never gate a blocked/clear decision. (`ownership_rules.md` §3–§5)
- **UK PSC ownership bands are intervals, not point percentages**, producing a three-valued BLOCKED/AMBIGUOUS/CLEAR status via lower/upper-bound sums, with specific, non-obvious rules for exactly-50% and exactly-75% boundaries. (`ownership_rules.md` §4.2, §7)
- **`IdentityLinkEdge` (identity-uncertainty propagation) is populated only from entity-linking tasks 1–2** (linking within the graph itself). Task 3 (matching a live screened customer to the graph) never feeds it — that routes through the L1 agent's routing table instead. This boundary is stated independently in all three of `ownership_rules.md`, `entity_linking.md`, and `l1_agent.md`.
- **No plain accuracy metric, anywhere, under any name**, in evaluation code, logs, or write-ups — the positive class is rare enough that it's meaningless. Use precision/recall curves, equal-recall comparison, and bootstrapped intervals instead. (`evaluation.md` §5)
- **Thresholds for any headline evaluation number are chosen on the tuning split only**, then applied unchanged to the test split. Ground truth for the synthetic benchmark is assigned at generation time and must never be derived from running `src/screen/` itself.
- **The L1 agent's guardrails are enforced in code, not in the prompt.** Routing (customer-identity confidence × matched-entity status), the structured-output schema, and the grounding check (every cited fact ID must exist in the evidence bundle) are all deterministic Python, with their own unit tests independent of any LLM call. The agent only ever recommends on one case type: uncertain customer identity against a confirmed-BLOCKED entity. AMBIGUOUS-status cases go to a human summary queue, never to an agent recommendation.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # then add ANTHROPIC_API_KEY / COMPANIES_HOUSE_API_KEY
```

## Commands

Planned pipeline entry points (per README; not yet implemented):

```bash
python -m src.ingest.run          # download and normalize sources to FollowTheMoney schema
python -m src.resolve.run         # link entities across sources
python -m src.graph.build         # build the time-aware risk graph
python -m src.evaluate.run        # run both screeners, report metrics
```

Tests (once written, per each spec's required-tests section):

```bash
pytest                                                          # full suite
pytest tests/test_ownership_examples.py tests/test_ownership_properties.py -q   # ownership rules: worked examples + hypothesis property tests
```

`tests/test_ownership_properties.py` must implement the property tests from `ownership_rules.md` §11 using `hypothesis` (monotonicity, interval validity, order independence, cycle safety, time consistency) — these check the rules hold for arbitrary generated graphs, not just fixed examples. Never weaken a property test to make code pass.

## Architecture

Pipeline, matching the README's flow (`src/ingest` → `src/resolve` → `src/graph` → `src/screen` → `src/agent` → `src/evaluate`):

1. **Ingest** (`src/ingest/`) — raw sources (OpenSanctions, UK Companies House, GLEIF) converted to the FollowTheMoney (FtM) schema; every ingest records a source snapshot date.
2. **Resolve** (`src/resolve/`) — cross-source entity linking per `entity_linking.md`: exact identifiers first (Tier 1), then Splink probabilistic linkage (Tier 2), with an accept/flag/reject banding where flagged links become graph-internal ambiguity, never silently accepted or dropped.
3. **Graph** (`src/graph/`) — resolved entities and ownership/directorship/sanction relationships become a time-aware graph; every edge and designation has start/end dates so the graph is queryable as-of any date using half-open `[start, end)` semantics.
4. **Screen** (`src/screen/`) — two screeners implementing the same callable interface (`screen(entity, as_of_date) → hits + explanation`): a fuzzy name-matching baseline, and a graph screener that additionally runs `propagate_blocked()` per `ownership_rules.md`.
5. **Agent** (`src/agent/`) — the L1 review agent per `l1_agent.md`, with prompts under `src/agent/prompts/` (version-controlled; any prompt or model change requires a full re-run of the evaluation set).
6. **Evaluate** (`src/evaluate/`) — synthetic benchmark generation (tagged, quarantined from real data until test time) plus the A7 case study, scored per `evaluation.md`.

`case_studies/a7/` holds the A7 network case-study data/mappings. `modules/evasion_arena/` and `modules/sanctions_contagion/` are stubs for future roadmap modules (not yet started). `config/settings.yaml` holds paths, thresholds, snapshot dates, and seeds referenced by name throughout the specs (e.g. `eval.equal_recall_target`, `eval.test_set_hash`).

## Docs and decisions

- `docs/specs/` — the four binding specs above (tracked, public by design — see 2026-09-23 in `docs/decisions.md`).
- `docs/decisions.md` — running log of interpretation/implementation decisions (tracked, public by design — same reasoning as `docs/specs/`).
- `docs/context_log.md` and `docs/results/` — dated log of relevant news/regulatory events, and results write-ups/charts (tracked, public).

## Data and privacy

- `data/raw/`, `data/processed/`, `data/snapshots/` are git-ignored (downloaded/derived data); `data/synthetic/` is tracked (small, deterministically seeded, tagged test-only overlay).
- The L1 agent runs on synthetic customers only; for real listed entities, only the minimum fields a case needs are sent to the external LLM API. Never send real customer data to the API.
- `.env` holds API keys and is git-ignored; use `.env.example` as the template.
