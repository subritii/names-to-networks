# L1 Review Agent

**Status:** stub (v0.2). The non-negotiables in §2 are locked in and must not be reinterpreted or "improved." Prompt design, evidence format, and the evaluation fixture set (§4) are genuinely open and must not be invented ahead of the screener that would inform them.

## 1. Purpose

The L1 review agent recommends **clear** or **escalate** on one kind of alert: a screened customer who **might** be a BLOCKED graph entity, where evidence can settle whether they are the same (§2.1). It is the AI-engineering core of the project. The governing principle: **anything that must never happen is enforced by code, not by asking the model nicely.**

## 2. Non-negotiables (settled; do not change without updating this spec first)

### 2.1 Deterministic routing, in plain Python, before the model

Every alert is routed by code before any LLM call, using two questions in order:

1. **Customer identity:** how sure are we that the screened customer is the matched graph entity? This is the result of customer-to-graph matching (`entity_linking.md` §1, task 3): **confirmed** (identifier match or accept band) or **uncertain** (flag band, score ≥ `t_g` but below the accept threshold).
2. **Entity status:** the matched entity's status under `ownership_rules.md` §5.

| Customer identity | Matched entity status | Route | Agent role |
|---|---|---|---|
| Confirmed | BLOCKED | Escalated deterministically | **None.** Never reaches the LLM. |
| Confirmed | AMBIGUOUS | Human review queue | **Summary only** (§2.3). No recommendation. |
| Uncertain | BLOCKED | Agent reviews | **Identity question:** is this customer the same as this entity? Recommends clear (not the same) or escalate (same or can't rule out) |
| Uncertain | AMBIGUOUS | Human review queue | **Summary only** (§2.3). No recommendation. |
| Any | CLEAR | No alert | None |

**Why AMBIGUOUS entities bypass the agent:** their status comes from data gaps (a stake known only as a band, an unknown stake, conflicting sources, an ambiguous upstream owner, or a flagged link inside the graph). The agent has no information that resolves them, so any "clear" recommendation would be a guess.

**What the agent does:** it answers identity questions only, meaning whether a screened customer is the same as a BLOCKED graph entity, where evidence such as dates of birth, nationality, and identifiers can confirm or rule out a match.

**Flagged links inside the graph** (`entity_linking.md` tasks 1–2) never reach the agent as alerts. They make entities AMBIGUOUS through ownership propagation, and those entities route to humans under the table above.

The routing logic has its own unit tests, independent of any LLM test.

### 2.2 Structured output only

Recommendation mode returns JSON validated against a Pydantic schema with at minimum: `decision` (`clear` | `escalate`), `confidence`, `cited_fact_ids` (list of evidence IDs), `explanation` (string).

Output that fails schema validation is retried. If retries are exhausted, the case is **escalated**, never silently accepted or treated as `clear`.

### 2.3 Summary mode (AMBIGUOUS ownership cases)

For cases routed to humans under §2.1, the agent may produce a reviewer summary using a separate schema with **no `decision` field**: `summary`, `cited_fact_ids`, `open_questions` (what data would resolve the ambiguity). The grounding check (§2.4) applies. A summary can never change the case's route.

### 2.4 Grounding check, in code

- Every ID in `cited_fact_ids` must exist in that case's evidence bundle. This is a deterministic check after every response. A response citing a nonexistent fact ID fails automatically and the case is escalated, whatever `decision` says.
- **A `clear` recommendation must cite at least one discriminating fact**, meaning a fact of a type that can rule out a match (e.g. a date-of-birth mismatch or an identifier mismatch). A `clear` citing no discriminating fact is converted to `escalate`. The list of discriminating fact types is defined with the evidence format (§4).
- **Limit, stated plainly:** these checks confirm that cited facts exist and are of the right type, not that they support the conclusion. A human spot-checks a sample of explanations in every evaluation run (§3).

### 2.5 Evidence is data, not instructions

Evidence bundles contain text from external sources (entity names, aliases, addresses, possibly news snippets), and that text may contain instruction-like content. In the prompt, evidence is clearly delimited and labeled as untrusted data to be analyzed, never followed. The code-level controls (§2.1 routing, §2.2 schema, §2.4 grounding, §2.6 human decision) bound what any model output can do, regardless of what the text says.

### 2.6 The agent recommends; it never decides

This holds in the workflow and interface, not just in documentation. There is no code path where an agent output becomes a final disposition without a human action.

### 2.7 Disagreement in operation

If the agent is ever run more than once on the same case in operation, **any disagreement between runs → escalate.**

### 2.8 Data minimization

The agent runs on **synthetic customers only**. For listed (real) entities, only the fields needed for the case are sent to the external API. No real customer data is ever sent.

### 2.9 Prompt and model versioning

- Prompts live in `src/agent/prompts/`, under version control.
- The model is pinned to an exact model ID.
- Every logged decision records the prompt version and model ID that produced it.
- **Any prompt change or model change requires a full rerun of the evaluation set before merge.** A prompt edit is a code change, not a copy edit.

### 2.10 Cache and cap

- API responses are cached by a key built from: **evidence bundle hash + prompt version + model ID + run index.**
  - Prompt version and model ID in the key ensure a prompt or model change never returns stale cached answers.
  - The run index ensures each of the 5 consistency runs (§3) gets a real response, while identical reruns of the evaluation remain free and reproducible.
- A spending limit is set on the API key before running any evaluation.

### 2.11 Audit log

Every agent call logs: case ID, route, evidence bundle hash, prompt version, model ID, run index, raw response, schema result, grounding result, final recommendation, and timestamp.

## 3. Evaluation (non-negotiable metrics)

### 3.1 Sets

- The evaluation fixtures are split into a **dev set** (for iterating on prompts) and a **test set** (reported results only). The prompt is never tuned on the test set.
- The test set is frozen with a SHA-256 hash recorded in `config/settings.yaml: agent_eval.test_set_hash`, as in `evaluation.md` §3.4.
- Ground truth labels are human-made or assigned by construction, never produced by the agent or by `src/screen/` output.

### 3.2 Metrics, reported together on every run

| Metric | Definition | Role |
|---|---|---|
| **Escalation recall on true hits** | Of all true hits in the set, the share escalated | **Primary.** Target: 100% on the test set |
| **Clear rate on true negatives** | Of all true negatives, the share correctly cleared | Required pair to the primary metric; shows the workload the agent saves |
| Confusion matrix | Counts of all four outcomes | Always shown in full |
| **Flip rate** | Share of cases whose decision changes across **5 runs** | Reliability |
| Schema failure rate | Share of responses failing validation | Reliability |
| Grounding failure rate | Share of responses failing §2.4 checks | Reliability |
| Agreement rate | Share of decisions matching ground truth | Reported, never sufficient alone |

- **Escalation recall and clear rate must always be reported together.** An agent that escalates everything scores 100% escalation recall and saves no work.
- **Reporting "100%":** with 0 misses out of *n* true hits, report the approximate 95% upper bound on the true miss rate as **3 / n** (the "rule of three"). Example: 0 misses in 50 true hits means the true miss rate could still be up to about 6%.
- **Human spot check:** each evaluation run includes a human review of a random sample of explanations (default 20) for whether cited facts actually support the conclusion. Record the result.
- Always give counts alongside rates.

## 4. Open: do not invent ahead of the screener

- **Evidence bundle format:** what fields exist in a case, the `fact_id` scheme (§2.4 depends on it), and what the graph screener actually emits as evidence.
- **Discriminating fact types** for the §2.4 `clear` rule, defined with the evidence format.
- **Prompt content and structure,** depending on which case types actually reach the agent once routing (§2.1) runs on real and synthetic data. Do not write prompt logic against imagined case types.
- **Evaluation fixtures** (`tests/fixtures/l1_eval_dev.jsonl`, `tests/fixtures/l1_eval_test.jsonl`), built from real cases surfaced by the screener plus the synthetic benchmark.
- **Retry count** before escalating on schema failure (§2.2).
- **Operational run count:** whether operation uses a single run or several runs per case (§2.7).
- **Spot-check sample size,** if 20 proves too small or too large.
- **What `confidence` is used for,** if anything, beyond being logged and reported. It is not part of any decision rule.

Move items from this section into §2 or §3 only as each is actually decided against real data or real screener output, not in the abstract. Update the Status line as sections firm up.