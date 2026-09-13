# Routing and durable plans

Use this mode when the user wants to choose which model keeps context and which model executes.

## Model-role flows

`quality-first` keeps long-lived coordination inexpensive and spends capability on bounded work:

- GPT-5.6 Luna coordinates decisions, dependencies, questions, and progress summaries.
- Codex CLI with GPT-6 Astra at high reasoning effort executes one detailed capsule and exits.
- High-risk review uses a fresh Astra context.

`economy-first` spends capability on strategy and lowers routine execution cost:

- GPT-6 Astra plans, decomposes, answers difficult questions, and reviews.
- Codex CLI with GPT-5.6 Luna at high reasoning effort executes one detailed capsule and exits.

For any other pairing, omit `--route` and pass the coordinator metadata, CLI, execution model, and
effort independently. A route fills only missing values, so explicit user choices always win.

These are routing policies, not universal cost claims. Capture usage and compare cost per accepted
task, including retries and review.

## When to offer a plan

Offer one compact planning choice when any of these is true:

- the request contains multiple independently reviewable outcomes;
- implementation needs several workers, worktrees, or ordered dependencies;
- the work may outlive the current conversation context;
- the task packet approaches the 64 KiB route budget;
- product, security, or architecture decisions must be preserved before execution.

Do not ask again if the user already requested a plan or authorized the complete workflow. Create
the plan from their original prompt and repository inspection so they never need to transfer a plan
between harnesses.

Use these exact sections:

```markdown
# Goal

Describe the final observable result.

# Decisions and assumptions

Record confirmed decisions, open assumptions, and their authority.

# Constraints and guardrails

Record repository rules, security boundaries, non-goals, and forbidden operations.

# Checklist

- [ ] One bounded, independently reviewable outcome.
- [ ] Another bounded outcome with an explicit dependency if needed.

# Validation strategy

Describe focused tests, integration checks, and final broad validation.

# Completion criteria

State what evidence makes the entire plan complete.
```

Create and inspect the ledger:

```bash
python3 <runner> create-plan \
  --title '<short title>' \
  --workspace <workspace> \
  --plan-file <plan.md> \
  --json
python3 <runner> plan-status <plan-id> --json
```

Create one detailed task contract per checklist item. Launch with `--plan-id` and
`--checklist-item`. Use `--depends-on` only for accepted prerequisites; the runner rejects an
unreviewed dependency.

## Context discipline

An executor capsule contains exact file and symbol pointers, contracts, invariants, ordered
guidance, acceptance criteria, tests, authority limits, and the injected question channel. Refer to
repository files instead of pasting large source blocks.

The coordinator retains compact decisions, item states, job IDs, changed files, test outcomes,
review verdicts, and unresolved questions. Read raw output only to diagnose a specific failure.
Observability data belongs in the dashboard, not permanently in model context.

## Review routing

Use deterministic checks for every job. Use a fresh high-capability review for security-sensitive,
architecture-sensitive, cross-module, concurrency, migration, or otherwise high-risk changes. A
coordinator may review a trivial diff only when the acceptance criteria and checks make correctness
straightforward.

Record `accepted`, `repair_required`, or `rejected` with concise notes and independent test evidence.
If two repair passes fail, stop delegating and either take over directly or ask whether to spend
more execution budget.
