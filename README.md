# Agent Orchestrator

Agent Orchestrator is a Codex plugin for separating long-lived coordination from bounded code
execution. You choose which model plans and holds the project thread, which CLI/model executes each
task, and which model reviews the result. It can form a small team of independent top-level workers
across Cursor, Devin, OpenCode, Grok, Codex, Claude, and other installed harnesses.

It is designed for long-running implementation work where you want to choose a less expensive CLI
or model, give it a precise task contract, see what it is doing, answer clarification questions,
and independently review the resulting diff and tests before accepting it. Large jobs can start
from one prompt: Codex creates a durable plan, maintains its checklist, dispatches each item, handles
questions, applies gates, and keeps the evidence needed to resume later.

Requesting Agent Orchestrator also asks Codex to create a native persistent goal when the task does
not already have one, unless you opt out. If an aligned goal already exists it is reused; an
unrelated active goal is never overwritten. The native goal keeps Codex pursuing the outcome while
the durable plan holds detailed checklist, executor, question, and review state.

## Why separate coordination from execution?

Long-running agent work mixes two different workloads:

- Coordination repeatedly revisits user intent, decisions, dependencies, questions, and progress.
- Execution needs a precise local assignment, the relevant repository files, strong coding ability,
  and a clear exit condition.

Sending the entire growing conversation to every executor wastes context. Agent Orchestrator keeps
plans, job state, logs, usage, questions, and review evidence in a durable local control plane. The
coordinator receives compact state changes; every executor gets one bounded context capsule and
exits after reporting its work.

## This is not just another subagent feature

Cursor, Claude Code, Codex, Devin, and other harnesses can already create subagents inside their own
runtime. Those native subagents are useful, but they are controlled and metered behind one parent
harness. Agent Orchestrator owns a different layer: it creates accountable top-level workers across
different CLIs and subscriptions, gives each an exact role and model, and keeps their questions,
usage, changes, and review state in one local control plane.

The design is inspired by [Devin Fusion](https://cognition.com/blog/devin-fusion), which pairs a
frontier lead with a lower-cost sidekick. Agent Orchestrator applies that separation across
harnesses: a Codex coordinator can assign one bounded item to Cursor, another to Devin, and another
to OpenCode or Grok. It prefers two to four useful roles over a large swarm, parallelizes only
independent scopes, and keeps one writer per workspace.

One orchestrator job means one visible agent. Native nested subagents are denied by default where a
harness exposes a reliable control (`--no-subagents` for Grok and a bundled `subagentStart` deny
hook for Cursor) and prohibited by the worker contract everywhere else. This prevents hidden model
switches, surprise parallel token use, and work that cannot be attributed in the dashboard.

This also lets existing personal allowances become an execution portfolio. A user may coordinate
from Codex while routing suitable tasks through an authenticated Devin, OpenCode, Cursor, Claude,
or other CLI account. Availability, quotas, and overage billing remain provider-specific; the
plugin does not turn a subscription into unlimited or interchangeable API credit.

This makes two useful flows possible. A cost-efficient coordinator can preserve the long thread
while the strongest model handles short implementation bursts, or a strong coordinator can do the
hard planning and review while a cheaper model performs carefully specified edits. OpenAI describes
[GPT-5.6 Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna) as a cost-sensitive,
high-volume model and [GPT-6 Astra](https://developers.openai.com/api/docs/models/gpt-6-astra) as its
most capable model for complex reasoning and coding. Stronger execution can also reduce failed
attempts and output volume, so the useful metric is cost per accepted task—not price per token
alone. [OpenAI model guidance](https://developers.openai.com/api/docs/guides/latest-model)

The other major cost is coordinator replay. In one anonymized 24-hour project trace, a single
long-lived coordinator accumulated 528.4 million input tokens, 519.5 million of them cached, across
48 task turns and 29 context compactions. The executor jobs were bounded; repeatedly waking the
near-full coordinator was the dominant amplification. Agent Orchestrator therefore resumes from a
small durable `plan-checkpoint`, waits once after dispatch, and relies on phase changes, questions,
completion events, the dashboard, and notifications instead of tight polling.

## What it provides

- Independent selection of CLI, model, and a CLI's internal agent/persona when supported.
- Built-in profiles for Google Antigravity CLI, DeepSeek Harness, Kimi Code, Codex CLI, Claude
  Code, Cursor CLI, Devin CLI, Grok CLI, Gemini CLI, and OpenCode.
- Durable `auto`, `single`, and `cross-harness` execution topologies with a bounded parallel-team
  size and Agent Orchestrator ownership of all spawning.
- Detached jobs with durable status, stdout/stderr logs, heartbeats, progress events, and git diff
  summaries.
- A job-local question channel that lets a worker pause for a concrete answer without restarting.
- A local web dashboard spanning every project, with worker questions and durable orchestrator
  feedback answered directly in the inspector.
- Desktop notifications for questions, completion, failure, timeout, and scope violations.
- One-writer-per-workspace protection, dependency ordering, path allow/deny rules, and changed-file
  limits.
- A persistent workspace preference: isolated worktrees by default, or the existing project
  checkout when the user opts out of worktrees for space or workflow reasons.
- Durable plans with decision records, checklist-bound jobs, resumable progress, and completion
  evidence.
- Automatic intent routing with a cost-first default, quality-first Sol/Opus execution, explicit
  maximum-quality Astra authorization, risk-based reasoning effort, and exact durable allowlists.
- Evidence-aware candidate ranking by task type, using versioned public benchmark/model sources as
  priors and reviewed local acceptance, repair, execution-failure, and scope-failure history as the
  stronger signal once enough samples exist.
- Compact goal checkpoints and 24-hour outcome/usage metrics for evidence-based routing without
  replaying the full coordinator transcript.
- Fail-closed executor pools: every launch must match the plan's resolved or user-selected exact
  CLI/model pairing, and Astra never appears from a failure fallback.
- Controlled escalation that exhausts the approved pool, asks in chat and the dashboard, and can
  use only a pre-approved Terra fallback after an unanswered grace period.
- A concise installed-harness chooser followed by harness-specific model and internal-agent
  discovery.
- A required detailed task contract and a Codex review/repair gate.
- JSON adapter configuration for additional headless coding CLIs without shell interpolation.

Agent Orchestrator does not make an external CLI safe or inexpensive by itself. Permissions, token
usage, and cost still depend on the selected CLI, model, account, and repository. Treat every worker
as untrusted until Codex has inspected its diff and rerun the relevant checks.

## Install in Codex

Add this repository as a plugin marketplace, then install the plugin:

```bash
codex plugin marketplace add Augani/agent-orchestrator
codex plugin add agent-orchestrator@agent-orchestrator
```

Restart Codex if the skill is not immediately available. Each external CLI must be installed and
authenticated separately; Agent Orchestrator never stores provider credentials.

## Supported profiles

| Profile | Model choice | Internal agent | Budget control | Notes |
| --- | --- | --- | --- | --- |
| `antigravity` | Yes | Yes | Reasoning effort | Uses `agy` sandboxed headless mode, pinned models, JSONL stdin, and streaming usage. |
| `codex-cli` | Yes | No | Provider config | Uses `codex exec`, stdin, `workspace-write`, and ephemeral sessions. |
| `claude-code` | Yes | Yes | `--max-budget-usd` | Uses non-interactive `acceptEdits`; `claude` is a compatibility alias. |
| `kimi-code` | Yes | Yes | Provider config | Accepts either `kimi` or `kimi-cli`; uses streaming JSON print mode. |
| `deepseek-harness` | Profile config | No | Profile config | Developer preview; its documented headless interface exposes the prompt as a process argument. |
| `devin` | Yes | No | Provider config | Uses a prompt file and accepted-edit sandbox profile. |
| `grok` | Yes | Yes | Max turns | Uses a prompt file and disables subagents by default. |
| `gemini` | Yes | No | Provider config | Uses stdin and auto-edit mode. |
| `opencode` | Provider config | No | Provider config | Uses a process argument, so avoid sensitive task packets. |
| `cursor` | Yes | Denied | Provider config | Uses sandboxed headless streaming, live model discovery, and a bundled hook that blocks nested subagents. |

Run the catalog before choosing:

```bash
python3 plugins/agent-orchestrator/scripts/cli_agent_job.py choices --json
python3 plugins/agent-orchestrator/scripts/cli_agent_job.py profiles
python3 plugins/agent-orchestrator/scripts/cli_agent_job.py doctor --cli all
python3 plugins/agent-orchestrator/scripts/cli_agent_job.py catalog --cli codex-cli
python3 plugins/agent-orchestrator/scripts/cli_agent_job.py catalog --cli claude-code
python3 plugins/agent-orchestrator/scripts/cli_agent_job.py catalog --cli kimi-code
python3 plugins/agent-orchestrator/scripts/cli_agent_job.py catalog --cli antigravity
python3 plugins/agent-orchestrator/scripts/cli_agent_job.py catalog --cli cursor
```

`choices` shows only installed harnesses by default. Once the user chooses one, `catalog` shows its
live model list where the CLI exposes one, configured aliases otherwise, available internal agents,
and supported controls. Codex model discovery is compacted to model IDs, descriptions, defaults,
and reasoning-effort choices instead of loading the CLI's full catalog payload into coordinator
context. Claude Code currently exposes the configured aliases `sonnet`, `opus`, and `fable`.
Whenever a harness supports model selection, Agent Orchestrator requires an explicit `--model` so
an unknown configured default cannot silently become an expensive executor.

`doctor --cli all` exits non-zero when any built-in CLI is missing; that is expected when you only
install the agents you use.

Antigravity uses its official `agy` headless stream protocol, pins `--model`, accepts an optional
`--agent` and `--effort`, and enables `--sandbox`. The runner sends the complete private task capsule
as one JSON user event on stdin, so it is not exposed in the process list. It never uses
`--dangerously-skip-permissions`; configure narrow Antigravity permission rules for the exact write
paths and validation commands required by the task.

Cursor uses `cursor-agent` in sandboxed headless streaming mode and loads the bundled guard that
denies nested subagents. Cursor may require a one-time interactive trust decision for each project
workspace. Open Cursor Agent in that project and make the decision yourself; Agent Orchestrator
never passes `--trust`, `--force`, `--yolo`, or `--approve-mcps`, and never redirects a worker into
the private orchestrator state directory merely to avoid the prompt.

## Choose a model-role flow

Run `routes` to inspect the built-in policies:

| Route | Recommended Codex task model | Executor | Best when |
| --- | --- | --- | --- |
| `cost-first` (default) | Current task model | Installed Terra, then Luna, with risk-based effort | You want the lowest expected cost per accepted change without weakening quality gates. |
| `quality-first` | Current task model | Installed Sol, Opus, then Terra, with risk-based effort | You say “quality over cost”; this authorizes Sol/Opus but not Astra. |
| `maximum-quality` | Current task model | Installed Astra, Sol, and Opus | You explicitly authorize maximum/frontier execution quality and cost. |
| `economy-first` | GPT-6 Astra | Recommends Codex CLI + GPT-5.6 Luna, but never selects it | Architecture and review are hard, but implementation can use an approved lower-cost pool. |
| Custom | Your choice | Any supported CLI/model/agent/effort | You want another provider or complete control over the pairing. |

The current Codex task model is always the orchestrator. The runner never changes it; coordinator
metadata defaults to `current-codex-task`, and an explicit `--coordinator-model` only changes that
metadata. `create-plan` resolves an automatic installed-model allowlist from natural-language intent
and risk, while every individual launch still names and verifies the exact executor:

```bash
python3 plugins/agent-orchestrator/scripts/cli_agent_job.py routes

# Quality over cost: Sol is selected from the plan's automatic pool; Astra remains excluded.
python3 plugins/agent-orchestrator/scripts/cli_agent_job.py launch \
  --route quality-first \
  --cli codex-cli \
  --model gpt-5.6-sol \
  --plan-id <plan-id> \
  --checklist-item item-001 \
  --workspace /path/to/worktree \
  --task-file /path/to/task.md \
  --json

# Fully custom: Astra coordinates; Claude Code executes with Sonnet.
python3 plugins/agent-orchestrator/scripts/cli_agent_job.py launch \
  --coordinator-model gpt-6-astra \
  --cli claude-code \
  --model sonnet \
  --approved-executor 'claude-code=sonnet' \
  --reasoning-effort high \
  --workspace /path/to/worktree \
  --task-file /path/to/task.md \
  --json
```

The route does not lock execution to Codex CLI. For example, Luna can coordinate while Claude Code,
Kimi Code, Grok, Cursor, Devin, Gemini, DeepSeek Harness, OpenCode, or a custom adapter executes. Codex first
presents what is actually installed, then the selected harness's available model and agent choices.

The coordinator model override is workflow metadata, not a model switch. Select the desired model
for the Codex task itself. Model availability and billing depend on your account and provider.
Using Astra for planning or review does not approve it for implementation. Only an explicit
`maximum-quality` plan or explicit expensive executor selection does.

Workspace isolation is also user-controlled and remembered locally:

```bash
# Opt out of extra worktrees for this and future orchestrations.
python3 plugins/agent-orchestrator/scripts/cli_agent_job.py preferences \
  --workspace-mode project

# Restore isolated worktrees later.
python3 plugins/agent-orchestrator/scripts/cli_agent_job.py preferences \
  --workspace-mode worktree
```

Project mode does not weaken writer locks, dirty-worktree baselines, scope limits, or review gates.

## Resolve and lock the executor pool

When the user invokes Agent Orchestrator without naming an executor, the default is automatic
`cost-first`. Saying “quality over cost” selects `quality-first`. Only an explicit maximum/frontier
request enables `maximum-quality`. Risk controls reasoning effort; it does not relax review gates:

```bash
python3 plugins/agent-orchestrator/scripts/cli_agent_job.py create-plan \
  --title 'Authentication refresh' \
  --workspace /path/to/project \
  --plan-file /path/to/plan.md \
  --strategy quality-first \
  --risk high \
  --task-type security \
  --topology auto \
  --max-parallel 3 \
  --json
```

The plan records every installed CLI/model choice, rank, effort, rationale, and authorization
source. `auto` uses one worker for tightly coupled or judgment-heavy work and a cross-harness team
only when the checklist has independent, safely isolated roles. Use `--topology single` or
`--topology cross-harness` to make that decision explicit. To rank a discovered cross-harness
candidate set before locking it:

```bash
python3 plugins/agent-orchestrator/scripts/cli_agent_job.py recommend-executors \
  --strategy quality-first \
  --risk high \
  --task-type debugging \
  --candidate 'codex-cli=gpt-5.6-sol' \
  --candidate 'devin=swe-2' \
  --candidate 'grok=grok-4.6' \
  --json
```

The bundled evidence catalog links to SWE-bench, Terminal-Bench, LiveCodeBench, Aider, and official
provider model catalogs. Its capability tiers are transparent routing heuristics—not fabricated
benchmark scores—and every output names the evidence date and sources. Task-specific local history
changes ordering only after three independently reviewed jobs; overall history needs five. Explicit
user selections and the no-silent-Astra rule always win.

If the user names a custom set, persist exactly that set instead:

```bash
python3 plugins/agent-orchestrator/scripts/cli_agent_job.py create-plan \
  --title 'Authentication refresh' \
  --workspace /path/to/project \
  --plan-file /path/to/plan.md \
  --executor 'devin=swe-2' \
  --executor 'grok=grok-4.6' \
  --executor 'opencode' \
  --terra-fallback-after-seconds 900 \
  --json

python3 plugins/agent-orchestrator/scripts/cli_agent_job.py set-executors <plan-id> \
  --executor 'devin=swe-2' \
  --executor 'grok=grok-4.6' \
  --terra-fallback-after-seconds 900 \
  --json
```

`CLI=MODEL` is exact. A bare CLI is permitted only for adapters such as OpenCode that cannot select
a model themselves, and means the user knowingly approved that configured default. Astra, Fable,
Opus, and other known expensive/frontier choices in a custom pool belong under
`--expensive-executor`, which records that the user accepted execution cost rather than merely
using that model to plan or review. Automatic `quality-first` is the narrow exception for Sol/Opus
because the user's quality-over-cost wording is itself recorded authorization; it never adds Astra.

The optional Terra fallback must also be disclosed up front. It does not run merely because a
worker is quiet: every primary entry must have been attempted, a linked question must be visible in
the active Codex chat and dashboard, the question must remain unanswered for the configured grace
period, and the fallback task packet must be under 32 KiB. Astra is never an automatic fallback.

## One prompt to a durable plan

For every Agent Orchestrator run, Codex turns the original request into a plan with
decisions, guardrails, validation strategy, completion criteria, and bounded checklist items. Users
do not need to create a plan in another harness or manually transfer context.

```bash
python3 plugins/agent-orchestrator/scripts/cli_agent_job.py create-plan \
  --title 'Authentication refresh' \
  --workspace /path/to/project \
  --plan-file /path/to/plan.md \
  --strategy cost-first \
  --risk medium \
  --json

python3 plugins/agent-orchestrator/scripts/cli_agent_job.py launch \
  --route cost-first \
  --cli codex-cli \
  --model gpt-5.6-terra \
  --plan-id <plan-id> \
  --checklist-item item-001 \
  --workspace /path/to/worktree \
  --task-file /path/to/item-001.md \
  --json

python3 plugins/agent-orchestrator/scripts/cli_agent_job.py plan-status <plan-id> --json
python3 plugins/agent-orchestrator/scripts/cli_agent_job.py plan-checkpoint <plan-id> --json
python3 plugins/agent-orchestrator/scripts/cli_agent_job.py executor-options <plan-id> \
  --checklist-item item-001 --json
```

Built-in routes cap a task capsule at 64 KiB. Oversized packets are stopped with guidance to create
a plan and split the work. This keeps the executor focused and preserves the complete workflow in
durable state instead of an ever-growing model transcript.

Use `plan-checkpoint` for every automatic continuation. It returns only the goal, next item,
completed items, active/unreviewed jobs, pending feedback, and aggregate provider usage. Use
`metrics --since-hours 24 --json` to compare recent attempts, reviewed acceptance, failures, and
reported usage by CLI/model. Missing usage is unavailable, never zero.

## Typical delegated flow

Ask Codex naturally, for example:

> Use Kimi Code with the default agent for this implementation. Give it exact file-level
> instructions, keep me notified, answer its questions, and review the final diff and tests. Do
> not use any other executor unless I approve it.

Codex will inspect the repository first, produce a task packet with the required objective, why,
scope, file, implementation, constraint, acceptance, validation, and reporting sections, then run:

```bash
python3 plugins/agent-orchestrator/scripts/cli_agent_job.py launch \
  --cli kimi-code \
  --model <approved-kimi-model> \
  --cli-agent default \
  --approved-executor 'kimi-code=<approved-kimi-model>' \
  --workspace /path/to/worktree \
  --task-file /path/to/task.md \
  --allow-path 'packages/target/**' \
  --deny-path '.env*' \
  --max-changed-files 12 \
  --timeout-seconds 7200 \
  --json
```

Useful observability and communication commands:

```bash
python3 plugins/agent-orchestrator/scripts/cli_agent_job.py observe <job-id> --json
python3 plugins/agent-orchestrator/scripts/cli_agent_job.py watch <job-id>
python3 plugins/agent-orchestrator/scripts/cli_agent_job.py dashboard --watch --group <group>
python3 plugins/agent-orchestrator/scripts/cli_agent_job.py questions <job-id> --json
python3 plugins/agent-orchestrator/scripts/cli_agent_job.py answer <job-id> <question-id> --file answer.md
```

When a worker genuinely fails or reaches its declared timeout, inspect `executor-options` and try
an untried approved entry. Once the pool is exhausted, the orchestrator asks in the active Codex
chat and creates the same linked dashboard question:

```bash
python3 plugins/agent-orchestrator/scripts/cli_agent_job.py request-feedback \
  --workspace /path/to/project \
  --plan-id <plan-id> \
  --checklist-item item-001 \
  --question-file /path/to/escalation-question.md \
  --json
```

If the user answers, that answer wins. If the question remains pending past a pre-approved grace
period, the runner can admit only the configured Terra fallback:

```bash
python3 plugins/agent-orchestrator/scripts/cli_agent_job.py launch \
  --cli codex-cli \
  --model gpt-5.6-terra \
  --use-terra-fallback \
  --feedback-id <pending-feedback-id> \
  --plan-id <plan-id> \
  --checklist-item item-001 \
  --workspace /path/to/worktree \
  --task-file /path/to/narrow-repair-task.md \
  --json
```

The runner verifies pool exhaustion, feedback ownership/state/age, exact Terra selection, and the
32 KiB fallback context limit. If Terra fails, execution stops for user direction; there is no
second automatic escalation.

Successful execution is deliberately not accepted work. Status remains `awaiting_review` until
Codex inspects the diff, reruns tests, and records evidence:

```bash
python3 plugins/agent-orchestrator/scripts/cli_agent_job.py record-review <job-id> \
  --verdict accepted \
  --reviewer current-codex-task \
  --test 'pnpm test: passed' \
  --notes-file /path/to/review.md \
  --json
```

An accepted review completes the linked checklist item. `repair_required` keeps it in progress;
`rejected` blocks it. Dependent jobs cannot start until prerequisite jobs are accepted. When a CLI
emits JSONL token or cost fields, the latest provider-reported snapshot appears in status and
observation output; missing usage means unavailable, not zero.

After acceptance, the orchestrator cleans up resources it created only for the run, such as
temporary worktrees, disposable preview fixtures, scratch prompt/answer files, and stopped preview
server state. It preserves user files and durable plans, job history, questions, review evidence,
and audit events unless the user explicitly requests a purge. If ownership is uncertain, it leaves
the resource in place and reports it.

Runtime data is private to the local user and defaults to `~/.codex/agent-orchestrator/`. It is not
part of this repository.

## Local web dashboard

```bash
python3 plugins/agent-orchestrator/scripts/cli_agent_job.py ensure-dashboard --json
python3 plugins/agent-orchestrator/scripts/cli_agent_job.py dashboard-web
# Keep the browser closed and choose a port (0 asks the OS for a free port).
python3 plugins/agent-orchestrator/scripts/cli_agent_job.py dashboard-web --no-open --port 0
```

The orchestration skill runs `ensure-dashboard` at the beginning of every session. It starts the
private local server when needed, opens it, and otherwise reuses the healthy server registered for
the current state home. This avoids duplicate dashboards while keeping all projects visible.
`dashboard-web` remains available for foreground operation; Ctrl-C stops that server.

The fixed project rail aggregates **all** repositories represented by jobs, feedback, or durable plans in
`AGENT_ORCHESTRATOR_HOME` (default `~/.codex/agent-orchestrator/`), regardless of current directory or
job group. A main checkout and all of its linked Git worktrees collapse into one project entry;
the selected project's workers appear in a separate inner Agents rail. Only unanswered user
questions create a project-level attention alert, so historical failures and unreviewed runs do not
turn an old project into a permanent alarm. Select an inner agent and the center switches to that
agent's work alone; select **Project overview** to return to the full project history.

Select a project and an agent to see current work, phase, elapsed time, review state, recent
messages, changed files, test evidence, and available provider usage. The Files tab reports changes
relative to the recorded baseline; Tests shows independent review evidence. Missing measurements
are labeled unavailable rather than estimated.

The dashboard refreshes every two seconds. Filtering and inspector tabs preserve the current
selection; polling preserves focused answers and unsent drafts. **Pause refresh** pauses only the
browser refresh, not agent execution. Select **Project overview** for pending project decisions
and answered feedback history. Worker questions appear in the selected worker's inspector. Type
an answer and choose **Send answer** to save it durably; a waiting worker receives it without
restarting. Concurrent or duplicate answers cannot overwrite an answered record.

Coordinators can create reusable feedback separately from worker questions, even before any worker
exists:

```bash
python3 plugins/agent-orchestrator/scripts/cli_agent_job.py request-feedback \
  --workspace /path/to/project --question 'Which scope should the next item cover?' \
  --context 'The current item is ready for review.' --json
python3 plugins/agent-orchestrator/scripts/cli_agent_job.py feedback --json
python3 plugins/agent-orchestrator/scripts/cli_agent_job.py feedback --workspace /path/to/project --all --json
python3 plugins/agent-orchestrator/scripts/cli_agent_job.py answer-feedback <feedback-id> --file answer.md --json
```

`request-feedback` also accepts `--question-file`, optional `--plan-id` / `--checklist-item` linkage,
and `--no-notify`. Linked plans must belong to the same workspace. Feedback records and their audit
events live under `feedback/<id>/`, with private directories and atomic user-only record writes.
Worker and orchestrator answers share the same validation, locking, and audited state transition.

### Loopback and privacy

The dependency-free server binds only to `127.0.0.1`. Every API request requires a random per-server
capability token. The launch URL carries that token in its fragment; the browser removes it from
the visible URL and retains it in tab-scoped session storage so reloading works. Treat the printed
URL as private local access. A restart generates a new token. There are no external assets,
telemetry, remote network requests, or CORS access. Host and Origin checks, a restrictive CSP,
no-store responses, frame blocking, a static-file allowlist, ID validation, and a 64 KiB JSON-body
limit protect the local interface. HTTP mutations accept only an answer and never a filesystem path.

Only the selected job can expose short, explicitly **Local / private** log tails (up to 8 KiB per
stream). The overview excludes task packets, commands, raw logs, and absolute workspace fields.
Progress and question text may still contain private repository content supplied by an agent.
This is a local user interface, not a remote service or an isolation boundary against other
processes already running with your user account. Do not proxy or share the capability URL.

New jobs put the worker wrapper and worker-authored events/questions in `jobs/<id>/channel/`.
Codex CLI retains `--sandbox workspace-write` and receives only `--add-dir {channel_dir}` as its
additional writable directory. Metadata, results, reviews, task packets, and runner lifecycle
and answer-audit events stay outside that subtree. No user trust configuration is changed and no
permission-bypass flag is used. The `{channel_dir}` placeholder is available to argv adapters;
legacy jobs with root-level `events/` and `questions/` remain readable and answerable.

## Add another CLI

Create `~/.config/agent-orchestrator/agents.json`:

```json
{
  "agents": {
    "team-cli": {
      "argv": ["team-agent", "run", "--prompt-file", "{prompt_file}"],
      "prompt_transport": "file",
      "model_args": ["--model", "{model}"],
      "models": ["fast", "balanced"],
      "cli_agent_args": ["--agent", "{cli_agent}"],
      "cli_agents": ["builder", "reviewer"],
      "reasoning_effort_args": ["--effort", "{reasoning_effort}"],
      "maturity": "stable"
    }
  }
}
```

Adapters are argv arrays executed directly, never shell command strings. See the plugin's
`adapter-config.md` reference for the complete schema and safety rules.

## Development

```bash
python3 -m unittest discover -s plugins/agent-orchestrator/tests -v
python3 -m compileall -q plugins/agent-orchestrator/scripts
python3 -m json.tool .agents/plugins/marketplace.json >/dev/null
python3 -m json.tool plugins/agent-orchestrator/.codex-plugin/plugin.json >/dev/null
```

Contributions for new CLI adapters, better structured event parsing, and cross-platform
notifications are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md)
before opening a pull request.

## License

MIT. See [LICENSE](LICENSE).
