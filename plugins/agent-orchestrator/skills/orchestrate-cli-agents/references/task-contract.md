# Required task contract

Cheaper agents need explicit local context. Codex must inspect the relevant code first and write the
contract with concrete repository facts. Avoid vague instructions such as “follow best practices,”
“fix related issues,” or “make it production-ready.” Do not make the worker choose product policy.
Treat the task contract as a compiled execution unit: the worker should implement decisions, not
rediscover or reinterpret them.

Use these exact sections so the runner can validate the packet:

```markdown
# Objective

State one observable outcome and what must remain unchanged.

# Why

Explain the user need, current failure or limitation, and architectural reason for this approach.

# Scope

List work that is in scope, explicit non-goals, allowed paths, and forbidden paths or operations.

# Files to inspect

Name exact files, symbols, relevant line areas when stable, callers, tests, public exports, and
repository instruction files. Explain why each item matters and state what fact the worker should
learn from it. Do not say “inspect related files”; enumerate the bounded search path.

# Implementation guidance

Give an ordered approach. State data flow, interfaces, invariants, error behavior, retry and
idempotency rules, security boundaries, observability, compatibility, and rollout constraints.
Include concise examples or expected shapes when they remove ambiguity. Name the exact existing
pattern to copy, the signatures or schemas that must remain compatible, and the sequence in which
edits should be made. Mark confirmed plan decisions as fixed. For each unknown that could alter
behavior or scope, say “stop and ask” and write the exact question category rather than allowing a
guess.

# Constraints

Restate repository rules relevant to this task. Ban commits, pushes, releases, production changes,
credential changes, destructive cleanup, unrelated refactors, and dependency changes unless each
was explicitly authorized. State that the worker must not spawn native subagents: Agent
Orchestrator owns all delegation, model selection, retry, and escalation for the job.

# Acceptance criteria

Write independently checkable behavior, including failure paths and things that must not happen.
Use concrete inputs and outputs where possible. Every criterion should be decidable from a diff,
test, or command result; avoid subjective words such as “clean,” “robust,” or “production-ready.”

# Validation

Give exact focused test, lint, typecheck, and required broader-check commands. Explain any command
the worker must not run locally.

# Final report

Require changed files, behavior implemented, tests with exit results, assumptions, deviations,
failures, and remaining risks. The report is evidence for Codex review, not acceptance by itself.
Require the worker to identify any instruction it could not follow instead of silently substituting
another approach.
```

Each section must contain task-specific content. Prefer exact symbol and file names over large pasted
source dumps. Include only context that changes implementation decisions. If essential information
is unknown, Codex should resolve it before delegation or explicitly tell the worker to use the
question channel before choosing.

Before launch, read the packet once from the viewpoint of a model with no prior conversation. If it
cannot answer all of the following from the packet alone, it is not ready:

- What single result must exist?
- Why is this the chosen design?
- Which exact files and symbols may change?
- Which decisions are fixed and which ambiguity requires a question?
- What must remain unchanged?
- What is the ordered edit sequence?
- Which failure and retry cases must be proven?
- Which exact commands provide completion evidence?
- What must the worker report before exiting?
