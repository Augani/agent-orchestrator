# Agent Orchestrator

Agent Orchestrator is a Codex plugin for separating long-lived coordination from bounded code
execution. You choose which model plans and holds the project thread, which CLI/model executes each
task, and which model reviews the result.

It is designed for long-running implementation work where you want to choose a less expensive CLI
or model, give it a precise task contract, see what it is doing, answer clarification questions,
and independently review the resulting diff and tests before accepting it. Large jobs can start
from one prompt: Codex creates a durable plan, maintains its checklist, dispatches each item, handles
questions, applies gates, and keeps the evidence needed to resume later.

## Why separate coordination from execution?

Long-running agent work mixes two different workloads:

- Coordination repeatedly revisits user intent, decisions, dependencies, questions, and progress.
- Execution needs a precise local assignment, the relevant repository files, strong coding ability,
  and a clear exit condition.

Sending the entire growing conversation to every executor wastes context. Agent Orchestrator keeps
plans, job state, logs, usage, questions, and review evidence in a durable local control plane. The
coordinator receives compact state changes; every executor gets one bounded context capsule and
exits after reporting its work.

This makes two useful flows possible. A cost-efficient coordinator can preserve the long thread
while the strongest model handles short implementation bursts, or a strong coordinator can do the
hard planning and review while a cheaper model performs carefully specified edits. OpenAI describes
[GPT-5.6 Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna) as a cost-sensitive,
high-volume model and [GPT-6 Astra](https://developers.openai.com/api/docs/models/gpt-6-astra) as its
most capable model for complex reasoning and coding. Stronger execution can also reduce failed
attempts and output volume, so the useful metric is cost per accepted task—not price per token
alone. [OpenAI model guidance](https://developers.openai.com/api/docs/guides/latest-model)

## What it provides

- Independent selection of CLI, model, and a CLI's internal agent/persona when supported.
- Built-in profiles for DeepSeek Harness, Kimi Code, Codex CLI, Claude Code, Devin CLI, Grok CLI,
  Gemini CLI, and OpenCode.
- Detached jobs with durable status, stdout/stderr logs, heartbeats, progress events, and git diff
  summaries.
- A job-local question channel that lets a worker pause for a concrete answer without restarting.
- Desktop notifications for questions, completion, failure, timeout, and scope violations.
- One-writer-per-workspace protection, dependency ordering, path allow/deny rules, and changed-file
  limits.
- Durable plans with decision records, checklist-bound jobs, resumable progress, and completion
  evidence.
- User-selectable model-role routes, task-capsule budgets, reasoning-effort selection, and
  provider-reported usage capture.
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
| `codex-cli` | Yes | No | Provider config | Uses `codex exec`, stdin, `workspace-write`, and ephemeral sessions. |
| `claude-code` | Yes | Yes | `--max-budget-usd` | Uses non-interactive `acceptEdits`; `claude` is a compatibility alias. |
| `kimi-code` | Yes | Yes | Provider config | Accepts either `kimi` or `kimi-cli`; uses streaming JSON print mode. |
| `deepseek-harness` | Profile config | No | Profile config | Developer preview; its documented headless interface exposes the prompt as a process argument. |
| `devin` | Yes | No | Provider config | Uses a prompt file and accepted-edit sandbox profile. |
| `grok` | Yes | Yes | Max turns | Uses a prompt file and disables subagents by default. |
| `gemini` | Yes | No | Provider config | Uses stdin and auto-edit mode. |
| `opencode` | Provider config | No | Provider config | Uses a process argument, so avoid sensitive task packets. |

Run the catalog before choosing:

```bash
python3 plugins/agent-orchestrator/scripts/cli_agent_job.py choices --json
python3 plugins/agent-orchestrator/scripts/cli_agent_job.py profiles
python3 plugins/agent-orchestrator/scripts/cli_agent_job.py doctor --cli all
python3 plugins/agent-orchestrator/scripts/cli_agent_job.py catalog --cli codex-cli
python3 plugins/agent-orchestrator/scripts/cli_agent_job.py catalog --cli claude-code
python3 plugins/agent-orchestrator/scripts/cli_agent_job.py catalog --cli kimi-code
```

`choices` shows only installed harnesses by default. Once the user chooses one, `catalog` shows its
live model list where the CLI exposes one, configured aliases otherwise, available internal agents,
and supported controls. Codex model discovery is compacted to model IDs, descriptions, defaults,
and reasoning-effort choices instead of loading the CLI's full catalog payload into coordinator
context. Claude Code currently exposes the configured aliases `sonnet`, `opus`, and `fable`; omit
`--model` to use the user's Claude default.

`doctor --cli all` exits non-zero when any built-in CLI is missing; that is expected when you only
install the agents you use.

## Choose a model-role flow

Run `routes` to inspect the built-in policies:

| Route | Coordinator | Executor | Best when |
| --- | --- | --- | --- |
| `quality-first` | GPT-5.6 Luna | Defaults to Codex CLI + GPT-6 Astra, high effort | You want the best bounded execution while keeping the long coordination thread inexpensive. |
| `economy-first` | GPT-6 Astra | Defaults to Codex CLI + GPT-5.6 Luna, high effort | Architecture and review are hard, but implementation items can be specified precisely. |
| Custom | Your choice | Any supported CLI/model/agent/effort | You want another provider or complete control over the pairing. |

Routes fill only missing values. Explicit selections always win:

```bash
python3 plugins/agent-orchestrator/scripts/cli_agent_job.py routes

# Luna coordinates; Astra executes.
python3 plugins/agent-orchestrator/scripts/cli_agent_job.py launch \
  --route quality-first \
  --workspace /path/to/worktree \
  --task-file /path/to/task.md \
  --json

# Fully custom: Astra coordinates; Claude Code executes with Sonnet.
python3 plugins/agent-orchestrator/scripts/cli_agent_job.py launch \
  --coordinator-model gpt-6-astra \
  --cli claude-code \
  --model sonnet \
  --reasoning-effort high \
  --workspace /path/to/worktree \
  --task-file /path/to/task.md \
  --json
```

The route does not lock execution to Codex CLI. For example, Luna can coordinate while Claude Code,
Kimi Code, Grok, Devin, Gemini, DeepSeek Harness, OpenCode, or a custom adapter executes. Codex first
presents what is actually installed, then the selected harness's available model and agent choices.

The coordinator model is recorded as workflow metadata; select that model for the Codex task where
you invoke the plugin. Model availability and billing depend on your account and provider.

## One prompt to a durable plan

For multi-stage or long-running work, Codex offers to turn the original request into a plan with
decisions, guardrails, validation strategy, completion criteria, and bounded checklist items. Users
do not need to create a plan in another harness or manually transfer context.

```bash
python3 plugins/agent-orchestrator/scripts/cli_agent_job.py create-plan \
  --title 'Authentication refresh' \
  --workspace /path/to/project \
  --plan-file /path/to/plan.md \
  --json

python3 plugins/agent-orchestrator/scripts/cli_agent_job.py launch \
  --route quality-first \
  --plan-id <plan-id> \
  --checklist-item item-001 \
  --workspace /path/to/worktree \
  --task-file /path/to/item-001.md \
  --json

python3 plugins/agent-orchestrator/scripts/cli_agent_job.py plan-status <plan-id> --json
```

Built-in routes cap a task capsule at 64 KiB. Oversized packets are stopped with guidance to create
a plan and split the work. This keeps the executor focused and preserves the complete workflow in
durable state instead of an ever-growing model transcript.

## Typical delegated flow

Ask Codex naturally, for example:

> Use Kimi Code with the default agent for this implementation. Give it exact file-level
> instructions, keep me notified, answer its questions, and review the final diff and tests.

Codex will inspect the repository first, produce a task packet with the required objective, why,
scope, file, implementation, constraint, acceptance, validation, and reporting sections, then run:

```bash
python3 plugins/agent-orchestrator/scripts/cli_agent_job.py launch \
  --cli kimi-code \
  --cli-agent default \
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

Successful execution is deliberately not accepted work. Status remains `awaiting_review` until
Codex inspects the diff, reruns tests, and records evidence:

```bash
python3 plugins/agent-orchestrator/scripts/cli_agent_job.py record-review <job-id> \
  --verdict accepted \
  --reviewer gpt-6-astra \
  --test 'pnpm test: passed' \
  --notes-file /path/to/review.md \
  --json
```

An accepted review completes the linked checklist item. `repair_required` keeps it in progress;
`rejected` blocks it. Dependent jobs cannot start until prerequisite jobs are accepted. When a CLI
emits JSONL token or cost fields, the latest provider-reported snapshot appears in status and
observation output; missing usage means unavailable, not zero.

Runtime data is private to the local user and defaults to `~/.codex/agent-orchestrator/`. It is not
part of this repository.

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
python3 -m py_compile plugins/agent-orchestrator/scripts/cli_agent_job.py
python3 -m json.tool .agents/plugins/marketplace.json >/dev/null
python3 -m json.tool plugins/agent-orchestrator/.codex-plugin/plugin.json >/dev/null
```

Contributions for new CLI adapters, better structured event parsing, and cross-platform
notifications are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md)
before opening a pull request.

## License

MIT. See [LICENSE](LICENSE).
