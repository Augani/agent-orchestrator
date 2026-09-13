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

## Choose the worker

Treat the CLI, its model, and its internal agent/persona as separate choices. The user may specify
any subset in natural language; preserve every explicit choice exactly.

1. Run `choices --json` to present installed harnesses alongside the routing flows. If the user
   selects a harness, run `catalog --cli <name>` for its live or configured model and internal-agent
   choices. Use `catalog --cli all --query <model-or-agent>` when the user names a model or agent but
   not the CLI. Use `choices --include-unavailable` only for installation help.
2. If the user chose only a CLI, show that CLI's available models and internal agents when
   discoverable. Offer its configured default as a no-extra-choice option.
3. If the user chose only a model or internal agent, resolve which installed CLIs offer it. If one
   match is clear, use it; if several materially different matches remain, ask one compact choice.
4. If the user chose neither, recommend an installed option based on task fit, price information in
   the live catalog, maturity, required context, and supported budget controls. Prefer a stable
   profile over a preview profile when both fit. Make the CLI/model combination visible before
   launch; do not silently select an expensive model.
5. Pass the chosen values independently as `--cli`, `--model`, and `--cli-agent`. Omit a dimension
   to use that CLI's configured default. Never invent a model ID or internal-agent name.

When model roles matter, read
[routing and durable plans](references/routing-and-plans.md). Present the resolved flow before
launch and let the user choose:

- `quality-first`: GPT-5.6 Luna coordinates; GPT-6 Astra executes at high effort.
- `economy-first`: GPT-6 Astra plans and reviews; GPT-5.6 Luna executes at high effort.
- custom: pass `--coordinator-model`, `--cli`, `--model`, and `--reasoning-effort` explicitly.

Explicit user choices override route defaults. Never silently substitute the opposite flow.
The built-in routes use Codex CLI defaults, but either route can be combined with any installed
harness by passing explicit `--cli`, `--model`, `--cli-agent`, and `--reasoning-effort` values.

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

For a model-routing flow:

```bash
python3 <runner> routes
python3 <runner> launch \
  --route quality-first \
  --plan-id <plan-id> \
  --checklist-item item-001 \
  --workspace <workspace> \
  --task-file <task-packet> \
  --timeout-seconds 7200 \
  --json
```

Both built-in routes reject task packets above 64 KiB by default. Prefer a durable plan and smaller
jobs over `--allow-large-context`; use the override only when decomposition would lose correctness
and the user accepts the context cost.

Built-in CLI profiles are `deepseek-harness`, `kimi-code`, `codex-cli`, `claude-code`, `devin`,
`grok`, `gemini`, and `opencode`. `claude` remains as a compatibility alias for `claude-code`.
Use `profiles` to inspect maturity, installation guidance, prompt transport, and supported selection
or budget controls. For another CLI, read
[adapter configuration](references/adapter-config.md) and add a local argv-based adapter; never
interpolate a shell command.

DeepSeek Harness is a developer-preview adapter and currently passes the task packet as a process
argument because that is the documented headless interface. Warn the user that the prompt may be
visible to other local processes and do not use it for sensitive repositories unless the local
threat model permits that exposure. Kimi Code accepts both the newer `kimi` executable name and the
`kimi-cli` compatibility name. Its print mode is non-interactive, so use a trusted worktree, narrow
path limits, and the review gate. Codex CLI always uses `workspace-write`; never replace it with the
dangerous bypass option. Claude Code uses `acceptEdits`; never add a permission-bypass flag.

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

Each job receives a job-local `channel.py` path in its task packet. The worker uses it to publish
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
When Codex can open a terminal panel, show the dashboard there after launch. Local notifications are
enabled by default for completion, failure, scope violations, and questions; use `--no-notify` only
when the user asks for quiet operation. Do not expose private log content outside the task, and do
not mistake lack of streamed prose for a stalled process when heartbeats continue.

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
   one repair job. Prefer at most two worker repair passes; after that, take over directly or ask
   the user whether to spend more agent budget.
6. Report what was accepted, changed, tested, and still uncertain. Never imply that a worker's
   output was reviewed when only its prose response was read.

Record the verdict after review:

```bash
python3 <runner> record-review <job-id> \
  --verdict accepted \
  --reviewer gpt-6-astra \
  --test 'pnpm test: passed' \
  --notes-file <review-notes> \
  --json
```

Execution success remains `awaiting_review` until this gate is recorded. Acceptance requires at
least one independently run test result. Dependencies cannot start until their prerequisite is
accepted. For a plan-bound job, acceptance marks its checklist item done, a repair request keeps it
in progress, and rejection blocks it.

Use `cancel <job-id>` for a graceful stop. Logs and task packets can contain private repository
content and are created with user-only permissions; do not paste them elsewhere without the user's
authorization.
