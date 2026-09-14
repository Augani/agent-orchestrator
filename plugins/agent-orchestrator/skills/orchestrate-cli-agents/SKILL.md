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

## Choose the worker

Treat the CLI, its model, and its internal agent/persona as separate choices. The user may specify
any subset in natural language; preserve every explicit choice exactly. Before the first launch,
ask the user for the bounded executor pool they authorize for this plan. Never add a model to that
pool merely because another worker is slow, unavailable, or failed.

1. Run `choices --json` to present installed harnesses alongside the routing flows. If the user
   selects a harness, run `catalog --cli <name>` for its live or configured model and internal-agent
   choices. Use `catalog --cli all --query <model-or-agent>` when the user names a model or agent but
   not the CLI. Use `choices --include-unavailable` only for installation help.
2. If the user chose only a CLI, show that CLI's available models and internal agents when
   discoverable. Require an explicit model whenever the harness supports selection. Offer a
   configured default only when the adapter cannot select a model, label it clearly, and get the
   user's approval.
3. If the user chose only a model or internal agent, resolve which installed CLIs offer it. If one
   match is clear, use it; if several materially different matches remain, ask one compact choice.
4. If the user chose neither, recommend a short pool of installed options based on task fit, price
   information in the live catalog, maturity, required context, and supported budget controls.
   Prefer stable profiles over preview profiles when both fit. Show every CLI/model pairing and get
   the user's choice before persisting it. A CLI that exposes model selection must use an explicit
   model; never rely on its configured default.
5. Pass the chosen values independently as `--cli`, `--model`, and `--cli-agent`. Omit a dimension
   only when that CLI cannot select it and the user knowingly approved the configured default.
   Never invent a model ID or internal-agent name.

Persist normal entries with repeated `--executor CLI=MODEL`. Astra, Fable, Opus, and any model the
catalog or user identifies as expensive/frontier must be recorded with
`--expensive-executor CLI=MODEL`, and only after the user explicitly accepts that it may execute
code. Planning or reviewing with a model is not consent to use it for implementation.

When model roles matter, read
[routing and durable plans](references/routing-and-plans.md). Present the resolved flow before
launch and let the user choose:

- `quality-first`: recommends a capable bounded executor, but requires an explicit executor and
  separate expensive-executor approval when that recommendation is Astra.
- `economy-first`: recommends a strong planner/orchestrator and a user-approved lower-cost executor.
- custom: pass `--coordinator-model`, `--cli`, `--model`, and `--reasoning-effort` explicitly.

The model selected for the current Codex task is always the orchestrator. The runner cannot infer
that selection, so it records `current-codex-task` unless the user explicitly supplies
`--coordinator-model`. Routes recommend task models but never overwrite coordinator identity,
change the current model, or fill executor fields. Every launch requires an explicit executor that
matches the user's allowlist. Never silently substitute the opposite flow or a frontier model.
Either route can be combined with any installed harness by passing explicit `--cli`, `--model`,
`--cli-agent`, and `--reasoning-effort` values.

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

For work likely to span several bounded jobs, outlive the current context, or approach the route's
context budget, offer to create a durable plan. If the user already requested a plan or authorized
the complete end-to-end workflow, create it without another confirmation. The plan keeps decisions
and checklist state outside model context; bind every executor job to one checklist item.

Create the plan with its approved pool, or set the pool after model discovery:

```bash
python3 <runner> create-plan \
  --title '<title>' \
  --workspace <workspace> \
  --plan-file <plan.md> \
  --executor 'devin=swe-2' \
  --executor 'grok=grok-code-fast-1' \
  --terra-fallback-after-seconds 900 \
  --json

python3 <runner> set-executors <plan-id> \
  --executor 'devin=swe-2' \
  --executor 'opencode' \
  --executor 'grok=grok-code-fast-1' \
  --terra-fallback-after-seconds 900 \
  --json
```

The Terra option must be disclosed when asking for the pool. Omit
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
  --model gpt-6-astra \
  --approved-expensive-executor 'codex-cli=gpt-6-astra' \
  --plan-id <plan-id> \
  --checklist-item item-001 \
  --workspace <workspace> \
  --task-file <task-packet> \
  --timeout-seconds 7200 \
  --json
```

The example above is intentionally costly and is valid only when the user specifically chose
Astra for execution after seeing the warning. Selecting `quality-first`, using Astra to plan, or
using Astra to review does not provide that approval.

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

Do not poll faster than every ten seconds. Relay meaningful progress, failures, or required user
input, not repetitive unchanged status. Cancel only when requested, when a declared timeout is
reached, or when continuing would violate scope or safety.

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

## Review gate

Completion by the worker is not completion of the task.

1. Inspect the complete diff against the recorded baseline and identify scope drift first.
2. Re-read security-sensitive and boundary code directly. Reject secrets, permission expansion,
   destructive behavior, suppressed errors, and unrequested dependency or configuration changes.
3. Run the focused tests yourself, then the repository's required broader checks. Treat the
   worker's test report only as a lead.
4. Compare behavior with every acceptance criterion and repository definition of done.
5. If repair is bounded, write a new packet naming exact defects and failing evidence, then launch
   one repair job from the approved pool. Prefer at most two repair passes. After that, use the
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
