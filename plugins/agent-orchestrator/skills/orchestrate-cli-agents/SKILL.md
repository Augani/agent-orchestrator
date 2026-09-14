---
name: orchestrate-cli-agents
description: Route durable coding plans between user-selected coordinator and executor models through local CLIs such as DeepSeek Harness, Kimi Code, Codex CLI, Claude Code, Devin, Grok, Gemini, or OpenCode, then monitor and review their diffs. Use for long-running external-agent orchestration; do not use for ordinary in-process subagent delegation.
---

# Orchestrate CLI Agents

Use Codex as the coordinator and durable job state as external memory. Give each implementation
worker a bounded context capsule, then let the selected execution model implement and exit. Treat
every worker as untrusted until its diff and tests are independently reviewed.

Resolve `../../scripts/cli_agent_job.py` relative to this file and invoke it with `python3`. Job
state is stored outside the repository under `~/.codex/agent-orchestrator/` by default. Set
`AGENT_ORCHESTRATOR_HOME` only when the user wants a different state location.

At the beginning of every orchestration session, start or reuse the all-project dashboard and make
its URL visible to the user:

```bash
python3 <runner> ensure-dashboard --json
```

This command is idempotent: it reuses the healthy local server recorded for the configured state
home instead of opening competing dashboard processes.

## Infer the orchestration policy

The model selected for the current Codex task is always the orchestrator. Treat the CLI, executor
model, internal agent/persona, and reasoning effort as separate choices. Preserve every explicit
user choice exactly; explicit choices override automatic routing.

Infer one strategy from the user's words:

- `cost-first` is the default when the user gives no cost/quality preference. It optimizes expected
  cost per accepted change, not the lowest token price. It prefers installed Terra and Luna
  workers with risk-based effort while keeping the same review and test gates.
- `quality-first` applies when the user says “quality over cost”, “prioritize quality”, or an
  equivalent. That phrase is sufficient authorization to automatically execute bounded jobs with
  installed Sol and Opus models; Terra remains a balanced fallback in the pool. It does **not**
  authorize Astra.
- `maximum-quality` applies only when the user explicitly asks for maximum/frontier quality, names
  Astra as an executor, or otherwise explicitly authorizes Astra implementation cost. It may place
  Astra, Sol, and Opus in the installed executor pool.

Never infer `maximum-quality` merely because a worker is slow, failed, or unavailable. Planning or
reviewing with Astra is not consent to use Astra for implementation. Fable and other expensive
models not in the automatic policy still require an explicit user-selected pool.

After inspecting the repository, classify each plan as `low`, `medium`, or `high` risk. Low covers
documentation, tests, and mechanical local edits. Medium covers a bounded feature following an
existing pattern. High includes authentication, authorization, security, migrations, concurrency,
public APIs, cross-module invariants, or irreversible behavior. Pass the classification to
`create-plan`; the runner records the exact model, rank, reasoning effort, rationale, and source of
authorization. The automatic pool only includes installed built-in harnesses.

Run `choices --json` once to learn what is installed. Use `catalog --cli <name>` when the user names
a harness, or `catalog --cli all --query <name>` when they name a model/agent without a harness.
Ask a compact question only when an explicit choice is ambiguous, no automatic candidate is
installed, or a product/security decision changes the result. Otherwise continue automatically.
Never invent a model ID or internal-agent name, and never rely on a configurable model default when
the harness supports explicit model selection.

For an explicit custom pool, persist ordinary entries with repeated `--executor CLI=MODEL` and
explicitly approved Astra, Fable, Opus, or other frontier entries with
`--expensive-executor CLI=MODEL`. For an inferred policy, omit both executor flags and use
`--strategy` plus `--risk`; `quality-first` itself records authorization for Sol/Opus, while only
`maximum-quality` records authorization for Astra.

When model roles matter, read
[routing and durable plans](references/routing-and-plans.md). The runner records
`current-codex-task` unless the user explicitly supplies `--coordinator-model`; this is metadata and
never changes the current Codex model. Every launch still names one exact executor and must match
the plan's resolved allowlist. Either automatic strategy can be replaced by a custom pool using
explicit `--cli`, `--model`, `--cli-agent`, and `--reasoning-effort` values.

```bash
python3 <runner> catalog --cli grok --json
python3 <runner> catalog --cli all --query fast --json
```

## Before delegation

1. Read the repository instructions and the actual files needed to define a bounded task. Inspect
   relevant symbols, callers, tests, and public contracts before writing the worker prompt. Do not
   ask a weaker worker to rediscover context Codex can state directly.
2. Inspect `git status`. Prefer a clean dedicated worktree for isolated work. If using a dirty
   checkout is intentional, record the baseline and pass `--allow-dirty`; never attribute the
   user's pre-existing changes to the worker.
3. Write one detailed task contract using [the required task contract](references/task-contract.md).
   Explain what to change, how to approach it, and why each constraint exists. Name exact files,
   symbols, callers, tests, and public exports where known. Include:
   - observable objective, motivation, and user-visible acceptance criteria;
   - exact scope, non-goals, allowed paths, and files or module boundaries;
   - ordered implementation guidance suitable for a less capable agent;
   - repository instructions and architectural context the worker must read;
   - contracts, invariants, edge cases, authority, idempotency, security, and observability rules;
   - focused tests, failure/retry tests, and broader validation commands;
   - a ban on commits, pushes, releases, production changes, credential changes, and destructive
     cleanup unless the user separately authorized them;
   - the expected final report: changed files, tests run, failures, assumptions, and remaining risk.
4. Run `validate-task --task-file <packet>` before launch. Keep each packet independently
   reviewable. Split unrelated work into separate jobs.

Every Agent Orchestrator run starts with a durable plan and goal ledger before the first executor.
Write the plan from the user's prompt and repository evidence, including decisions, guardrails,
validation, completion criteria, and bounded checklist items. Do not make the user transfer a plan
from another harness. Bind every executor job to one checklist item.

The plan's `goal` object is the default durable goal mechanism. Use Codex's native goal tool only
when the user explicitly asks to set a goal, finish end-to-end, keep working until completion, or
otherwise requests persistent autonomous pursuit. On every native-goal continuation, read
`plan-checkpoint` first and resume from that compact record; do not reconstruct state by replaying
the full chat, raw logs, or every historical job.

Create the plan with its approved pool, or set the pool after model discovery:

```bash
python3 <runner> create-plan \
  --title '<title>' \
  --workspace <workspace> \
  --plan-file <plan.md> \
  --strategy quality-first \
  --risk high \
  --json

python3 <runner> plan-checkpoint <plan-id> --json

python3 <runner> set-executors <plan-id> \
  --executor 'devin=swe-2' \
  --executor 'opencode' \
  --executor 'grok=grok-code-fast-1' \
  --terra-fallback-after-seconds 900 \
  --json
```

For an explicit custom pool, the Terra option must be disclosed when asking for the pool. Omit
`--terra-fallback-after-seconds` if the user does not pre-approve it. `opencode` without `=MODEL`
means the user explicitly accepted that harness's configured default because the adapter cannot
select a model itself.

For multiple workers, assign a short `--group` and distinct `--role` to every job. Launch independent
roles in separate worktrees. Use `--depends-on` for ordered work; never let two jobs write the same
workspace unless their file scopes are provably disjoint and the user accepts the risk. Pass
`--allow-path`, `--deny-path`, and `--max-changed-files` so the runner can stop obvious scope drift.

## Launch and monitor

First run `doctor --agent <name>`. Then launch with an explicit timeout and any provider-supported
budget controls:

```bash
python3 <runner> launch \
  --cli grok \
  --model grok-4.6 \
  --cli-agent builder \
  --group auth-refresh \
  --role token-store \
  --allow-path 'packages/auth/**' \
  --deny-path '.env*' \
  --max-changed-files 12 \
  --workspace <workspace> \
  --task-file <task-packet> \
  --max-turns 30 \
  --timeout-seconds 7200 \
  --json
```

For a one-off job without a plan, add a matching
`--approved-executor 'grok=grok-4.6'`. Use
`--approved-expensive-executor 'codex-cli=gpt-6-astra'` only after explicit cost approval. These
flags are approval evidence, not switches Codex may invent for convenience.

For a model-routing flow:

```bash
python3 <runner> routes
python3 <runner> launch \
  --route quality-first \
  --cli codex-cli \
  --model gpt-5.6-sol \
  --plan-id <plan-id> \
  --checklist-item item-001 \
  --workspace <workspace> \
  --task-file <task-packet> \
  --timeout-seconds 7200 \
  --json
```

The example uses the plan's automatically resolved quality-first pool and inherits the recorded
risk-based reasoning effort when `--reasoning-effort` is omitted. To use Astra, the plan must use
`maximum-quality` or contain an explicit expensive executor approval. Selecting `quality-first`,
using Astra to plan, or using Astra to review does not provide that approval.

Both built-in routes reject task packets above 64 KiB by default. Prefer a durable plan and smaller
jobs over `--allow-large-context`; use the override only when decomposition would lose correctness
and the user accepts the context cost.

Built-in CLI profiles are `antigravity`, `deepseek-harness`, `kimi-code`, `codex-cli`,
`claude-code`, `devin`, `grok`, `gemini`, and `opencode`. `claude` remains as a compatibility alias
for `claude-code`.
Use `profiles` to inspect maturity, installation guidance, prompt transport, and supported selection
or budget controls. For another CLI, read
[adapter configuration](references/adapter-config.md) and add a local argv-based adapter; never
interpolate a shell command.

Antigravity uses the official `agy` sandboxed headless stream protocol. Always discover and pin an
explicit model with `catalog --cli antigravity`; optionally pin its internal agent and effort. The
runner sends one JSON user event over private stdin and captures streaming events/usage. Never add
`--dangerously-skip-permissions`. Antigravity soft-denies unapproved tools in headless mode, so tell
the user which narrow workspace-write and test-command permission rules the selected task needs.

DeepSeek Harness is a developer-preview adapter and currently passes the task packet as a process
argument because that is the documented headless interface. Warn the user that the prompt may be
visible to other local processes and do not use it for sensitive repositories unless the local
threat model permits that exposure. Kimi Code accepts both the newer `kimi` executable name and the
`kimi-cli` compatibility name. Its print mode is non-interactive, so use a trusted worktree, narrow
path limits, and the review gate. Codex CLI always uses `workspace-write`; never replace it with the
dangerous bypass option. New Codex jobs grant only their per-job `channel/` subtree through
`--add-dir {channel_dir}`. Do not grant the state or job root, edit trust configuration, or ask the
user to trust the private state directory. Authoritative metadata, results, reviews, and lifecycle
events stay outside that worker-writable subtree. Claude Code uses `acceptEdits`; never add a permission-bypass flag.

The launch command detaches and returns a job ID. Use `status`, `logs`, or a wait capped below one
minute so the conversation stays responsive:

```bash
python3 <runner> wait <job-id> --timeout-seconds 45 --json
python3 <runner> logs <job-id> --tail 160
python3 <runner> observe <job-id> --json
```

Wait once for up to 45 seconds after launch. If nothing meaningful changes, yield control and let
the dashboard and local notifications carry passive progress; do not create a tight status loop or
repeatedly wake a long-lived coordinator. Read `observe` or logs only for a phase change, question,
failure, completion, or specific diagnostic need. Relay deltas, not repetitive unchanged status.
Cancel only when requested, when a declared timeout is reached, or when continuing would violate
scope or safety.

Each new job receives a `channel/channel.py` path in its task packet. The worker uses it to publish
phase events and can ask a blocking question when ambiguity affects behavior, scope, or safety.
`wait` returns early with `state: needs_input`; inspect and answer without restarting the agent:

```bash
python3 <runner> questions <job-id> --json
python3 <runner> answer <job-id> <question-id> --file <answer-file>
```

The waiting `channel.py ask` call prints the answer directly into the worker's tool session. Never
answer beyond the user's authority; if the question requires a product or security decision, bring
it to the user. `channel.py event` updates are useful evidence, but they do not replace diff review.

For live observability, `observe` returns process health, heartbeat age, elapsed time, pending
questions, recent events, log tails, and current git status/diff statistics. A user can run
`watch <job-id>` for one worker or `dashboard --watch --group <group>` for a live multi-worker view.
The dashboard should already be running from the session-start gate. Use `ensure-dashboard` again
whenever its health is uncertain; use `dashboard-web` only for deliberate foreground operation:

```bash
python3 <runner> dashboard-web
# Use --no-open when opening its printed URL in an available browser tool.
python3 <runner> dashboard-web --no-open --port 0
```

Keep the server running and open its printed loopback URL, including the private token fragment.
It aggregates every workspace in the configured state home. Project and agent selection, inspector
tabs, and filters help focus the view; the two-second browser refresh preserves selections and
focused answer drafts. Pause refresh only stops polling. Worker questions can be answered in the
selected agent inspector; select Project orchestrator for project decisions and answered history.
Use the existing terminal dashboard when a browser is not helpful. Local notifications are
enabled by default for completion, failure, scope violations, and questions; use `--no-notify` only
when the user asks for quiet operation. Do not expose private log content outside the task, and do
not mistake lack of streamed prose for a stalled process when heartbeats continue.

## Executor failures, delays, and escalation

Do not respond to a slow or failed worker by spawning Astra or another unapproved model. Use the
declared job timeout and heartbeat evidence instead of deciding that quiet output means failure.
After a genuine failure, timeout, cancellation, or rejected result:

1. Run `executor-options <plan-id> --checklist-item <item> --json`.
2. Try an untried entry from the approved pool with a fresh bounded packet. Never leave the pool.
   Allow at most three execution attempts for one checklist item, including repairs; then stop for
   user direction even if another automatic candidate remains.
3. When the pool is exhausted, ask the user in the active Codex chat **and** create the same durable
   dashboard request with `request-feedback`. State which models were tried, why each failed or was
   stopped, the cost/quality tradeoff, and the proposed next action.
4. If the user answers either channel, follow that answer. Never start the timeout fallback after an
   answer has been recorded.
5. If the user does not answer within the plan's pre-approved grace period, Terra may execute only
   when the plan policy enables it. Use a new task capsule below 32 KiB that focuses on one outcome,
   exact files, known failure evidence, and deterministic tests. Launch with the linked pending
   feedback record:

```bash
python3 <runner> launch \
  --cli codex-cli \
  --model gpt-5.6-terra \
  --use-terra-fallback \
  --feedback-id <pending-feedback-id> \
  --plan-id <plan-id> \
  --checklist-item <item> \
  --workspace <workspace> \
  --task-file <narrow-repair-packet> \
  --json
```

The runner rejects this fallback unless every approved primary executor has already been attempted,
the feedback is still unanswered and belongs to the same plan item, the grace period elapsed, and
the packet is narrowly bounded. Terra is the only timeout fallback. Astra never is. If Terra also
fails, stop and ask the user; do not escalate again automatically.

When the project orchestrator needs a user decision, create a durable feedback record instead of
putting a question in an unrelated worker job:

```bash
python3 <runner> request-feedback --workspace <workspace> --question-file <question-file> --json
python3 <runner> feedback --workspace <workspace> --all --json
python3 <runner> answer-feedback <feedback-id> --file <answer-file> --json
```

Optionally link feedback with `--plan-id` and `--checklist-item` for the same workspace. Requests
notify locally by default; `--no-notify` disables that notification. Both CLI and dashboard answers
use the same locked, audited transition and reject second answers. Never invent a user's answer.
The server binds only to loopback, requires its per-server token, serves no external assets, and
shows short private logs only for a selected agent. Do not share or proxy its URL.

When a CLI emits JSONL usage fields, status and observation include the latest provider-reported
token and cost snapshot. Treat missing usage as unavailable, not zero. Keep raw logs in durable
state and bring only phase changes, questions, failures, and concise usage deltas into coordinator
context.

Use `metrics --since-hours 24 --json` when comparing local routing outcomes. Prefer cost per
accepted result: attempts, acceptance rate, repair/scope failures, and provider-reported usage.
Never promote a model from a tiny or mostly-unreviewed sample, and do not treat missing provider
usage as zero cost.

## Review gate

Completion by the worker is not completion of the task.

1. Inspect the complete diff against the recorded baseline and identify scope drift first.
2. Re-read security-sensitive and boundary code directly. Reject secrets, permission expansion,
   destructive behavior, suppressed errors, and unrequested dependency or configuration changes.
3. Run the focused tests yourself, then the repository's required broader checks. Treat the
   worker's test report only as a lead.
4. Compare behavior with every acceptance criterion and repository definition of done.
5. If repair is bounded, write a new packet naming exact defects and failing evidence, then launch
   one repair job from the approved pool. Batch related findings into one review/repair cycle and
   prefer at most two repair passes. Do not create a separate model review for every tiny mechanical
   edit when the coordinator can inspect the bounded diff and deterministic test evidence. After that, use the
   failure/delay escalation flow above. Never take over implementation with the orchestrator or
   spawn a frontier executor unless the user explicitly approved that executor role.
6. Report what was accepted, changed, tested, and still uncertain. Never imply that a worker's
   output was reviewed when only its prose response was read.

Record the verdict after review:

```bash
python3 <runner> record-review <job-id> \
  --verdict accepted \
  --reviewer current-codex-task \
  --test 'pnpm test: passed' \
  --notes-file <review-notes> \
  --json
```

Execution success remains `awaiting_review` until this gate is recorded. Acceptance requires at
least one independently run test result. Dependencies cannot start until their prerequisite is
accepted. For a plan-bound job, acceptance marks its checklist item done, a repair request keeps it
in progress, and rejection blocks it.

After the task is accepted, clean up resources created only for orchestration: stop temporary
preview servers, remove disposable fixtures and prompt/answer scratch files, and remove temporary
worktrees or checkouts once their reviewed changes are safely preserved. Never delete user-created
files, the user's working tree, or durable plans, job history, review evidence, questions, and audit
events by default. If ownership or recoverability is uncertain, preserve the resource and report it
instead of guessing. State what was cleaned up in the final handoff.

Use `cancel <job-id>` for a graceful stop. Logs and task packets can contain private repository
content and are created with user-only permissions; do not paste them elsewhere without the user's
authorization.
