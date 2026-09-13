# Required task contract

Cheaper agents need explicit local context. Codex must inspect the relevant code first and write the
contract with concrete repository facts. Avoid vague instructions such as “follow best practices,”
“fix related issues,” or “make it production-ready.” Do not make the worker choose product policy.

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
repository instruction files. Explain why each item matters.

# Implementation guidance

Give an ordered approach. State data flow, interfaces, invariants, error behavior, retry and
idempotency rules, security boundaries, observability, compatibility, and rollout constraints.
Include concise examples or expected shapes when they remove ambiguity.

# Constraints

Restate repository rules relevant to this task. Ban commits, pushes, releases, production changes,
credential changes, destructive cleanup, unrelated refactors, and dependency changes unless each
was explicitly authorized.

# Acceptance criteria

Write independently checkable behavior, including failure paths and things that must not happen.

# Validation

Give exact focused test, lint, typecheck, and required broader-check commands. Explain any command
the worker must not run locally.

# Final report

Require changed files, behavior implemented, tests with exit results, assumptions, deviations,
failures, and remaining risks. The report is evidence for Codex review, not acceptance by itself.
```

Each section must contain task-specific content. Prefer exact symbol and file names over large pasted
source dumps. Include only context that changes implementation decisions. If essential information
is unknown, Codex should resolve it before delegation or explicitly tell the worker to use the
question channel before choosing.
