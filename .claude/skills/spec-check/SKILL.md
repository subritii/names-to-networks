---
name: spec-check
description: Review the current changes against a project spec in a fresh-context subagent. Run manually with /spec-check <spec-name>.
disable-model-invocation: true
---

# Spec check

Review the current changes against `docs/specs/$ARGUMENTS.md`. If `$ARGUMENTS` is empty, ask which spec to check against: `ownership_rules`, `entity_linking`, `evaluation`, or `l1_agent`.

Use a **subagent with a fresh context**, so the review isn't biased by the reasoning that produced the changes. Give it only the diff (`git diff` against the last commit, plus any untracked files in scope) and the spec.

The subagent checks:

1. Every behavior in the diff is defined by the spec. Anything the spec doesn't define, or lists as a non-goal or TBD, is reported as **UNSPECIFIED**.
2. Nothing in the diff contradicts the spec. Each contradiction is reported as **VIOLATION**, citing the spec section and the file and line.
3. No test files were modified to make code pass. Any test change is reported as **TEST CHANGED**, with an explanation of whether the spec justifies it.
4. The spec's required tests cover the changed behavior. Missing coverage is reported as **UNTESTED**.

Report only these four categories. Do not report style preferences or suggest extra abstraction. For any UNSPECIFIED item, propose a spec change rather than a code change.