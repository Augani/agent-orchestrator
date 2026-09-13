# Agent Orchestrator

Agent Orchestrator is a Codex plugin for delegating bounded coding work to local CLI agents while
Codex remains the planner, authority boundary, and reviewer.

It is designed for long-running implementation work where you want to choose a less expensive CLI
or model, give it a precise task contract, see what it is doing, answer clarification questions,
and independently review the resulting diff and tests before accepting it.

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
python3 plugins/agent-orchestrator/scripts/cli_agent_job.py profiles
python3 plugins/agent-orchestrator/scripts/cli_agent_job.py doctor --cli all
python3 plugins/agent-orchestrator/scripts/cli_agent_job.py catalog --cli kimi-code
```

`doctor --cli all` exits non-zero when any built-in CLI is missing; that is expected when you only
install the agents you use.

## Typical flow

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
