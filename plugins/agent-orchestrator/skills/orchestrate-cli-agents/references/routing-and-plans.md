# Routing and durable plans

Use this mode when the user wants to choose which model keeps context and which model executes.

## Model-role flows

`quality-first` keeps long-lived coordination inexpensive and recommends spending capability on
bounded work:

- Recommended task selection: GPT-5.6 Luna for decisions, dependencies, questions, and progress summaries.
- Codex CLI with GPT-6 Astra at high reasoning effort is a recommendation only. It cannot execute
  unless the user explicitly adds it as an expensive executor.
- Astra is recommended for high-risk review, but review model cost must also be visible to the user;
  review selection never grants Astra permission to execute implementation work.

`economy-first` spends capability on strategy and recommends lower-cost routine execution:

- Recommended task selection: GPT-6 Astra for planning, decomposition, difficult questions, and review.
- Codex CLI with GPT-5.6 Luna at high reasoning effort is a recommendation only; the user must still
  approve it in the executor pool.

For any other pairing, omit `--route` and pass the coordinator metadata, CLI, execution model, and
effort independently. The current Codex task model is always the orchestrator. The runner cannot
infer it and defaults coordinator metadata to `current-codex-task`. Recommendations do not change
the current task model; only an explicit `--coordinator-model` replaces the default metadata.
Routes never fill missing executor values. Every launch must name a CLI/model and match a durable
user-approved pool or an explicit one-off approval. Planning or review model selection never grants
implementation authority.

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

## Executor pool and escalation

After catalog discovery, ask the user which exact `CLI=MODEL` combinations may implement plan
items. Persist them with `create-plan --executor ...` or `set-executors`. Record Astra, Fable, Opus,
or another known expensive/frontier executor with `--expensive-executor` only after a clear cost
warning and explicit user approval. The default is fail-closed: an empty pool cannot launch, an
unlisted executor cannot launch, and routes cannot silently populate the pool.

When a worker fails or reaches its declared timeout, prefer an untried approved executor. Use
`executor-options` to see attempts and remaining choices. Once the pool is exhausted, ask in the
active Codex chat and create a linked dashboard feedback record. If the user answers, their answer
wins. If they remain silent and the plan was created with `--terra-fallback-after-seconds`, Terra
may run one sub-32-KiB capsule after that grace period by linking the still-pending feedback record.
No other automatic model escalation is permitted, and Astra is never an automatic fallback.

## Context discipline

An executor capsule contains exact file and symbol pointers, contracts, invariants, ordered
guidance, acceptance criteria, tests, authority limits, and the injected question channel. Refer to
repository files instead of pasting large source blocks.

The coordinator retains compact decisions, item states, job IDs, changed files, test outcomes,
review verdicts, and unresolved questions. Read raw output only to diagnose a specific failure.
Observability data belongs in the dashboard, not permanently in model context. Open `dashboard-web`
after long-running launches when helpful; it groups every workspace in the configured state home
and refreshes every two seconds without discarding answer drafts.

Use `request-feedback --workspace <workspace> --question-file <file> --json` to preserve a project
decision outside any worker. Link `--plan-id` and optionally `--checklist-item` when appropriate.
The user can respond in the Project orchestrator inspector, or Codex can save their actual answer
with `answer-feedback <id> --file <file> --json`. `feedback --all --json` includes answered history.
Worker questions remain separately scoped to their exact job. Both answer paths are durable and
audited; never substitute an assumed answer for user input.

## Review routing

Use deterministic checks for every job. Use a fresh high-capability review for security-sensitive,
architecture-sensitive, cross-module, concurrency, migration, or otherwise high-risk changes. A
coordinator may review a trivial diff only when the acceptance criteria and checks make correctness
straightforward.

Record `accepted`, `repair_required`, or `rejected` with concise notes and independent test evidence.
If two repair passes fail, exhaust any untried approved executors and use the chat/dashboard
escalation flow. The orchestrator must not take over implementation or silently select a frontier
executor. If a pre-approved Terra fallback also fails, stop and ask the user.
