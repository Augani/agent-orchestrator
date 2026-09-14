# Routing and durable plans

Use this mode when the user wants to choose which model keeps context and which model executes.

## Model-role flows

`cost-first` is the default when the user gives no preference:

- The current Codex task remains the coordinator.
- Installed Codex CLI Terra and Luna models form a bounded automatic pool.
- Risk selects reasoning effort. Review, tests, scope controls, and acceptance gates are unchanged.
- Optimize attempts and provider usage per accepted result, not nominal price per token.

`quality-first` is selected by an explicit “quality over cost” or equivalent intent:

- The current Codex task remains the coordinator regardless of its model.
- Installed Codex CLI Sol and Claude Code Opus may execute short, bounded jobs, with Terra as a
  balanced fallback in the same allowlist.
- The phrase authorizes Sol/Opus execution cost, but never Astra.

`maximum-quality` requires an explicit maximum/frontier-quality request or explicit Astra executor
selection:

- Installed Astra, Sol, and Opus may form the automatic pool.
- Astra's inclusion records the user's intent as executor authorization; no failure may invent it.

`economy-first` spends capability on strategy and recommends lower-cost routine execution:

- Recommended task selection: GPT-6 Astra for planning, decomposition, difficult questions, and review.
- Codex CLI with GPT-5.6 Luna at high reasoning effort is a recommendation only; the user must still
  approve it in the executor pool.

For any other pairing, omit `--route` and pass the coordinator metadata, CLI, execution model, and
effort independently. The current Codex task model is always the orchestrator. The runner cannot
infer it and defaults coordinator metadata to `current-codex-task`. Recommendations do not change
the current task model; only an explicit `--coordinator-model` replaces the default metadata.
Every launch names a CLI/model and matches the durable pool resolved during planning or an explicit
one-off approval. Planning or review model selection never grants implementation authority.

These are routing policies, not universal cost claims. Capture usage and compare cost per accepted
task, including retries and review.

## Evidence-aware model choice

Classify each item by task type and run `recommend-executors` before fixing its order. The router
uses the versioned `model-evidence.json` catalog as a qualitative prior, then incorporates durable
local outcomes for the exact CLI/model pair. Public benchmark results are harness-dependent and do
not prove that a model will perform equally in another CLI. Prefer task-specific reviewed history
after three samples and overall reviewed history after five; below those thresholds, report low
confidence and retain the policy order.

For CLIs with multiple models, discover exact current IDs first and pass them as repeated
`--candidate CLI=MODEL` values. Never infer current availability from an old successful job. The
recommendation can reorder eligible candidates, but cannot widen the user's pool, select an
uninstalled CLI, or admit Astra outside `maximum-quality`.

## Durable plan and goal

Create a plan for every Agent Orchestrator run. The top-level goal and checklist are its external
memory, so the user never needs to transfer a plan between harnesses. Use the native Codex goal
mechanism only when the user explicitly asks for persistent end-to-end pursuit.

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
  --strategy cost-first \
  --risk medium \
  --task-type backend \
  --json
python3 <runner> plan-status <plan-id> --json
python3 <runner> plan-checkpoint <plan-id> --json
```

Create one detailed task contract per checklist item. Launch with `--plan-id` and
`--checklist-item`. Use `--depends-on` only for accepted prerequisites; the runner rejects an
unreviewed dependency.

## Executor pool and escalation

When the user does not name executors, `create-plan --strategy ... --risk ...` resolves only
installed built-in candidates into a durable allowlist. The default is `cost-first`. A
`quality-first` intent authorizes Sol/Opus but excludes Astra; `maximum-quality` is the only
automatic policy that admits Astra. When the user names exact `CLI=MODEL` combinations, persist
those instead with `--executor` or `--expensive-executor`. An unlisted executor still cannot launch.

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

The coordinator resumes from `plan-checkpoint`, which contains the goal, next item, completed
items, active/unreviewed jobs, pending feedback, and aggregate usage. Read raw output only to
diagnose a specific failure. Wait once for up to 45 seconds after launch, then rely on dashboard
events and notifications instead of repeatedly waking the coordinator on unchanged state.
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
