# Adapter configuration

Built-in adapters cover DeepSeek Harness, Kimi Code, Codex CLI, Claude Code, Devin, Grok, Gemini,
and OpenCode. Add other headless coding CLIs in `~/.config/agent-orchestrator/agents.json`, or pass
another file with `--config`.

```json
{
  "agents": {
    "team-cli": {
      "argv": ["my-agent-cli", "run", "--prompt-file", "{prompt_file}"],
      "display_name": "Team CLI",
      "description": "Internal implementation worker",
      "maturity": "stable",
      "docs_url": "https://example.com/team-cli/docs",
      "install_hint": "Install team-cli and authenticate it before launch.",
      "prompt_transport": "file",
      "model_args": ["--model", "{model}"],
      "models": ["openrouter/deepseek/deepseek-v3.2"],
      "cli_agent_args": ["--agent", "{cli_agent}"],
      "cli_agents": ["builder", "reviewer"]
    }
  }
}
```

Each adapter accepts these fields:

- `argv` (required): an array of executable arguments. It is executed directly, never through a
  shell. Supported placeholders are `{workspace}`, `{prompt_file}`, and `{prompt_text}`.
- `executable_candidates` (optional): ordered executable names for compatible distributions. The
  first installed candidate replaces the first `argv` token. Do not use this for behaviorally
  different CLIs.
- `prompt_transport` (required): `file`, `stdin`, or `arg`. File mode requires `{prompt_file}` in
  `argv`; arg mode requires `{prompt_text}`. Prefer file or stdin because arg prompts may be visible
  to other local processes.
- `model_args` (optional): argv appended when `--model` is supplied. Use `{model}`.
- `cli_agent_args` (optional): argv appended when `--cli-agent` is supplied. Use `{cli_agent}`.
- `models` and `cli_agents` (optional): configured choices surfaced by `catalog`.
- `discover_models_argv` and `discover_cli_agents_argv` (optional): safe argv arrays that print live
  choices. Discovery commands are read-only, time-limited, and never run through a shell.
- `max_turns_args` (optional): argv appended when `--max-turns` is supplied. Use `{max_turns}`.
- `cost_args` (optional): argv appended when `--max-cost-usd` is supplied. Use `{max_cost_usd}`.
- `display_name`, `description`, `docs_url`, and `install_hint` (optional): user-facing catalog and
  diagnostic metadata. Do not include tokens, private URLs, or machine-specific paths.
- `maturity` (optional): `stable`, `preview`, or `legacy`. Codex should prefer stable adapters unless
  the user explicitly chooses another maturity level.

The runner rejects unknown placeholders, shell strings, unsupported requested controls, malformed
agent names, oversized task packets, and missing executables. Authentication stays with the CLI's
own credential store; do not put tokens in this file or in task packets.

Validate an adapter before delegating work:

```bash
python3 <runner> profiles --config ~/.config/agent-orchestrator/agents.json
python3 <runner> catalog --cli team-cli --config ~/.config/agent-orchestrator/agents.json
python3 <runner> doctor --cli team-cli --config ~/.config/agent-orchestrator/agents.json
```
