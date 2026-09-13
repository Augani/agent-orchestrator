#!/usr/bin/env python3
"""Detached, auditable job runner for local coding-agent CLIs."""

from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


MAX_TASK_BYTES = 1_000_000
QUALITY_FIRST_CONTEXT_BYTES = 65_536
REQUIRED_TASK_SECTIONS = (
    "Objective",
    "Why",
    "Scope",
    "Files to inspect",
    "Implementation guidance",
    "Constraints",
    "Acceptance criteria",
    "Validation",
    "Final report",
)
REQUIRED_PLAN_SECTIONS = (
    "Goal",
    "Decisions and assumptions",
    "Constraints and guardrails",
    "Checklist",
    "Validation strategy",
    "Completion criteria",
)
JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
AGENT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
CHECKLIST_RE = re.compile(r"(?m)^\s*-\s+\[([ xX])\]\s+(.+?)\s*$")
PLACEHOLDER_RE = re.compile(r"\{([a-z_]+)\}")
ALLOWED_PLACEHOLDERS = {
    "workspace",
    "prompt_file",
    "prompt_text",
    "model",
    "cli_agent",
    "max_turns",
    "max_cost_usd",
    "reasoning_effort",
}

BUILTIN_ROUTES: dict[str, dict[str, Any]] = {
    "quality-first": {
        "display_name": "Quality first",
        "description": (
            "Keep durable orchestration with a cost-efficient coordinator and use a fresh, "
            "high-capability model for each bounded implementation job."
        ),
        "coordinator": {
            "model": "gpt-5.6-luna",
            "responsibility": "Task decomposition, durable decisions, questions, and progress deltas.",
        },
        "executor": {
            "cli": "codex-cli",
            "model": "gpt-6-astra",
            "reasoning_effort": "high",
            "session": "ephemeral",
        },
        "review": {
            "required": True,
            "high_risk_model": "gpt-6-astra",
            "policy": "Independent diff review and rerun tests before acceptance.",
        },
        "context_budget_bytes": QUALITY_FIRST_CONTEXT_BYTES,
    },
    "economy-first": {
        "display_name": "Economy first",
        "description": (
            "Use the strongest model for planning and review while delegating bounded execution "
            "to a lower-cost model."
        ),
        "coordinator": {
            "model": "gpt-6-astra",
            "responsibility": "Architecture, decomposition, questions, progress, and review.",
        },
        "executor": {
            "cli": "codex-cli",
            "model": "gpt-5.6-luna",
            "reasoning_effort": "high",
            "session": "ephemeral",
        },
        "review": {
            "required": True,
            "high_risk_model": "gpt-6-astra",
            "policy": "Coordinator reviews the diff and reruns tests before acceptance.",
        },
        "context_budget_bytes": QUALITY_FIRST_CONTEXT_BYTES,
    },
}

BUILTIN_PROFILES: dict[str, dict[str, Any]] = {
    "devin": {
        "display_name": "Devin CLI",
        "description": "Delegate a bounded implementation task to Devin CLI.",
        "maturity": "stable",
        "docs_url": "https://docs.devin.ai/work-with-devin/devin-cli",
        "install_hint": "Install and authenticate Devin CLI, then ensure `devin` is on PATH.",
        "argv": [
            "devin",
            "--permission-mode",
            "accept-edits",
            "--sandbox",
            "--prompt-file",
            "{prompt_file}",
            "-p",
        ],
        "prompt_transport": "file",
        "model_args": ["--model", "{model}"],
        "discover_models_argv": ["devin", "models", "list"],
    },
    "grok": {
        "display_name": "Grok CLI",
        "description": "Run a Grok coding worker with optional model and internal-agent selection.",
        "maturity": "stable",
        "install_hint": "Install and authenticate Grok CLI, then ensure `grok` is on PATH.",
        "argv": [
            "grok",
            "--cwd",
            "{workspace}",
            "--permission-mode",
            "acceptEdits",
            "--prompt-file",
            "{prompt_file}",
            "--output-format",
            "plain",
            "--no-subagents",
        ],
        "prompt_transport": "file",
        "model_args": ["--model", "{model}"],
        "cli_agent_args": ["--agent", "{cli_agent}"],
        "max_turns_args": ["--max-turns", "{max_turns}"],
        "discover_models_argv": ["grok", "models"],
    },
    "claude-code": {
        "display_name": "Claude Code",
        "description": "Run Claude Code headlessly with edit approvals and optional budget limits.",
        "maturity": "stable",
        "docs_url": "https://docs.anthropic.com/en/docs/claude-code/cli-reference",
        "install_hint": "Install and authenticate Claude Code, then ensure `claude` is on PATH.",
        "argv": [
            "claude",
            "-p",
            "--permission-mode",
            "acceptEdits",
            "--output-format",
            "stream-json",
            "--verbose",
        ],
        "prompt_transport": "stdin",
        "model_args": ["--model", "{model}"],
        "cli_agent_args": ["--agent", "{cli_agent}"],
        "cost_args": ["--max-budget-usd", "{max_cost_usd}"],
        "reasoning_effort_args": ["--effort", "{reasoning_effort}"],
    },
    "claude": {
        "display_name": "Claude Code (compatibility alias)",
        "description": "Compatibility alias for the `claude-code` profile.",
        "maturity": "legacy",
        "docs_url": "https://docs.anthropic.com/en/docs/claude-code/cli-reference",
        "install_hint": "Prefer `--cli claude-code`; both profiles invoke the `claude` executable.",
        "argv": [
            "claude",
            "-p",
            "--permission-mode",
            "acceptEdits",
            "--output-format",
            "stream-json",
            "--verbose",
        ],
        "prompt_transport": "stdin",
        "model_args": ["--model", "{model}"],
        "cli_agent_args": ["--agent", "{cli_agent}"],
        "cost_args": ["--max-budget-usd", "{max_cost_usd}"],
        "reasoning_effort_args": ["--effort", "{reasoning_effort}"],
    },
    "codex-cli": {
        "display_name": "Codex CLI",
        "description": "Run the selected Codex model as a sandboxed implementation worker.",
        "maturity": "stable",
        "docs_url": "https://developers.openai.com/codex/cli/reference",
        "install_hint": "Install and authenticate Codex CLI, then ensure `codex` is on PATH.",
        "argv": [
            "codex",
            "exec",
            "--cd",
            "{workspace}",
            "--sandbox",
            "workspace-write",
            "--color",
            "never",
            "--ephemeral",
            "--json",
            "-",
        ],
        "prompt_transport": "stdin",
        "model_args": ["--model", "{model}"],
        "reasoning_effort_args": [
            "--config",
            "model_reasoning_effort=\"{reasoning_effort}\"",
        ],
    },
    "kimi-code": {
        "display_name": "Kimi Code",
        "description": "Run Kimi Code in non-interactive print mode with live JSON events.",
        "maturity": "stable",
        "docs_url": "https://www.kimi.com/code/docs/en/kimi-code-cli/reference/kimi-command",
        "install_hint": "Install Kimi Code CLI; the runner accepts either `kimi` or `kimi-cli` on PATH.",
        "argv": [
            "kimi",
            "--work-dir",
            "{workspace}",
            "--print",
            "--input-format",
            "text",
            "--output-format",
            "stream-json",
        ],
        "executable_candidates": ["kimi", "kimi-cli"],
        "prompt_transport": "stdin",
        "model_args": ["--model", "{model}"],
        "cli_agent_args": ["--agent", "{cli_agent}"],
        "cli_agents": ["default", "okabe"],
    },
    "deepseek-harness": {
        "display_name": "DeepSeek Harness",
        "description": "Run DeepSeek Harness's developer-preview headless profile.",
        "maturity": "preview",
        "docs_url": "https://github.com/deepseek-ai/deepseek-harness/tree/master/apps/cli",
        "install_hint": "Install `@deepseek-ai/dsh` and ensure `dsh` is on PATH. The headless profile is a developer preview.",
        "argv": ["dsh", "--profile", "headless", "{prompt_text}"],
        "prompt_transport": "arg",
    },
    "gemini": {
        "display_name": "Gemini CLI",
        "description": "Run Gemini CLI as an auto-edit implementation worker.",
        "maturity": "stable",
        "install_hint": "Install and authenticate Gemini CLI, then ensure `gemini` is on PATH.",
        "argv": [
            "gemini",
            "--approval-mode",
            "auto_edit",
            "--output-format",
            "text",
            "--prompt",
            "",
        ],
        "prompt_transport": "stdin",
        "model_args": ["--model", "{model}"],
    },
    "opencode": {
        "display_name": "OpenCode",
        "description": "Run an OpenCode implementation worker in the selected workspace.",
        "maturity": "stable",
        "install_hint": "Install and configure OpenCode, then ensure `opencode` is on PATH.",
        "argv": [
            "opencode",
            "--cwd",
            "{workspace}",
            "--quiet",
            "--output-format",
            "text",
            "--prompt",
            "{prompt_text}",
        ],
        "prompt_transport": "arg",
    },
}


class RunnerError(Exception):
    pass


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def state_home() -> Path:
    override = os.environ.get("AGENT_ORCHESTRATOR_HOME") or os.environ.get("CLI_AGENT_ORCHESTRATOR_HOME")
    return Path(override).expanduser().resolve() if override else Path.home() / ".codex" / "agent-orchestrator"


def config_path(value: str | None) -> Path:
    if value:
        return Path(value).expanduser().resolve()
    return Path.home() / ".config" / "agent-orchestrator" / "agents.json"


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RunnerError(f"Cannot read valid JSON from {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RunnerError(f"Expected a JSON object in {path}")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    temp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temp, 0o600)
    os.replace(temp, path)


def add_event(path: Path, event_type: str, **details: Any) -> dict[str, Any]:
    event = {
        "id": f"{time.time_ns()}-{secrets.token_hex(3)}",
        "at": utc_now(),
        "type": event_type,
        **details,
    }
    write_json(path / "events" / f"{event['id']}.json", event)
    return event


def read_events(path: Path, limit: int = 20) -> list[dict[str, Any]]:
    root = path / "events"
    if not root.exists():
        return []
    events: list[dict[str, Any]] = []
    for event_path in sorted(root.glob("*.json"))[-limit:]:
        try:
            events.append(read_json(event_path))
        except RunnerError:
            continue
    return events


def pending_questions(path: Path) -> list[dict[str, Any]]:
    root = path / "questions"
    if not root.exists():
        return []
    questions: list[dict[str, Any]] = []
    for question_path in sorted(root.glob("*.json")):
        try:
            question = read_json(question_path)
        except RunnerError:
            continue
        if question.get("state") == "pending":
            questions.append(question)
    return questions


def notify_user(title: str, message: str) -> None:
    """Best-effort local notification without including task or repository content."""
    try:
        if sys.platform == "darwin" and shutil.which("osascript"):
            script = (
                "on run argv\n"
                "display notification (item 1 of argv) with title (item 2 of argv)\n"
                "end run"
            )
            subprocess.run(
                ["osascript", "-e", script, message, title],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
        elif shutil.which("notify-send"):
            subprocess.run(
                ["notify-send", title, message],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
    except (OSError, subprocess.TimeoutExpired):
        pass


def write_channel_wrapper(path: Path, job_id: str) -> Path:
    wrapper = path / "channel.py"
    runner = str(Path(__file__).resolve())
    content = f'''#!/usr/bin/env python3
import os
import sys

RUNNER = {runner!r}
JOB_ID = {job_id!r}

if len(sys.argv) < 2 or sys.argv[1] not in {{"ask", "event"}}:
    raise SystemExit("usage: channel.py <ask|event> [arguments]")
os.execv(sys.executable, [sys.executable, RUNNER, sys.argv[1], JOB_ID, *sys.argv[2:]])
'''
    wrapper.write_text(content, encoding="utf-8")
    os.chmod(wrapper, 0o700)
    return wrapper


def coordination_preamble(channel_path: Path) -> str:
    return f"""# Codex coordination channel

You are an implementation worker directed by Codex. Do not commit, push, release, modify
credentials, touch production, or broaden scope. Codex will review your diff and rerun tests.

Publish a short phase update before edits, before tests, and whenever the phase changes:

```bash
python3 {channel_path} event --phase <phase> --message '<short factual update>'
```

If an ambiguity would change public behavior, scope, data handling, permissions, or security, ask
Codex instead of guessing. This call waits and prints Codex's answer back to you:

```bash
python3 {channel_path} ask --question '<one concrete question>' --wait-seconds 1800
```

For private or multiline text, write it to a file and use `--question-file <path>` instead of
`--question`. Continue only after receiving the answer. If the wait expires, stop safely and report
the blocker.

# Assigned task

"""


def validate_task_packet(text: str) -> list[str]:
    headings: list[tuple[str, int, int]] = []
    matches = list(re.finditer(r"(?m)^#{1,6}\s+(.+?)\s*$", text))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        headings.append((match.group(1).strip().casefold(), match.end(), end))
    problems: list[str] = []
    for required in REQUIRED_TASK_SECTIONS:
        matching = [entry for entry in headings if entry[0] == required.casefold()]
        if not matching:
            problems.append(f"missing section: {required}")
            continue
        _, start, end = matching[0]
        body = text[start:end].strip()
        if not body:
            problems.append(f"empty section: {required}")
    return problems


def validate_named_sections(text: str, required_sections: tuple[str, ...]) -> list[str]:
    matches = list(re.finditer(r"(?m)^#{1,6}\s+(.+?)\s*$", text))
    headings: list[tuple[str, int, int]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        headings.append((match.group(1).strip().casefold(), match.end(), end))
    problems: list[str] = []
    for required in required_sections:
        matching = [entry for entry in headings if entry[0] == required.casefold()]
        if not matching:
            problems.append(f"missing section: {required}")
        elif not text[matching[0][1] : matching[0][2]].strip():
            problems.append(f"empty section: {required}")
    return problems


def task_packet_stats(text: str) -> dict[str, Any]:
    path_mentions = sorted(
        set(re.findall(r"(?m)(?:^|[`\s])([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.*/-]+)+)", text))
    )
    return {
        "bytes": len(text.encode("utf-8")),
        "lines": len(text.splitlines()),
        "path_mentions": path_mentions[:50],
        "path_mention_count": len(path_mentions),
        "checklist_items": len(CHECKLIST_RE.findall(text)),
        "code_fences": text.count("```") // 2,
    }


def command_validate_task(args: argparse.Namespace) -> int:
    source = Path(args.task_file).expanduser().resolve()
    if not source.is_file():
        raise RunnerError(f"Task file does not exist: {source}")
    if source.stat().st_size > MAX_TASK_BYTES:
        raise RunnerError(f"Task file exceeds {MAX_TASK_BYTES} bytes")
    text = source.read_text(encoding="utf-8")
    problems = validate_task_packet(text)
    result = {
        "valid": not problems,
        "problems": problems,
        "task_file": str(source),
        "stats": task_packet_stats(text),
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if not problems else 1


def validate_string_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not value or not all(isinstance(item, str) for item in value):
        raise RunnerError(f"{label} must be a non-empty array of strings")
    return list(value)


def validate_profile(name: str, raw: Any) -> dict[str, Any]:
    if not AGENT_NAME_RE.fullmatch(name):
        raise RunnerError(f"Invalid agent name: {name!r}")
    if not isinstance(raw, dict):
        raise RunnerError(f"Profile {name!r} must be a JSON object")
    allowed_keys = {
        "argv",
        "prompt_transport",
        "model_args",
        "cli_agent_args",
        "max_turns_args",
        "cost_args",
        "reasoning_effort_args",
        "models",
        "cli_agents",
        "discover_models_argv",
        "discover_cli_agents_argv",
        "display_name",
        "description",
        "maturity",
        "docs_url",
        "install_hint",
        "executable_candidates",
    }
    unknown_keys = sorted(set(raw) - allowed_keys)
    if unknown_keys:
        raise RunnerError(f"Profile {name!r} has unsupported fields: {', '.join(unknown_keys)}")
    profile: dict[str, Any] = {
        "argv": validate_string_list(raw.get("argv"), f"{name}.argv"),
        "prompt_transport": raw.get("prompt_transport"),
    }
    if profile["prompt_transport"] not in {"file", "stdin", "arg"}:
        raise RunnerError(f"{name}.prompt_transport must be file, stdin, or arg")
    for key in ("display_name", "description", "docs_url", "install_hint"):
        if key in raw:
            if not isinstance(raw[key], str) or not raw[key].strip():
                raise RunnerError(f"{name}.{key} must be a non-empty string")
            profile[key] = raw[key]
    if "maturity" in raw:
        if raw["maturity"] not in {"stable", "preview", "legacy"}:
            raise RunnerError(f"{name}.maturity must be stable, preview, or legacy")
        profile["maturity"] = raw["maturity"]
    for key in (
        "model_args",
        "cli_agent_args",
        "max_turns_args",
        "cost_args",
        "reasoning_effort_args",
        "models",
        "cli_agents",
        "discover_models_argv",
        "discover_cli_agents_argv",
        "executable_candidates",
    ):
        if key in raw:
            profile[key] = validate_string_list(raw[key], f"{name}.{key}")
    command_keys = {
        "argv",
        "model_args",
        "cli_agent_args",
        "max_turns_args",
        "cost_args",
        "reasoning_effort_args",
        "discover_models_argv",
        "discover_cli_agents_argv",
    }
    tokens = [token for key, values in profile.items() if key in command_keys for token in values]
    placeholders = {match for token in tokens for match in PLACEHOLDER_RE.findall(token)}
    unknown_placeholders = sorted(placeholders - ALLOWED_PLACEHOLDERS)
    if unknown_placeholders:
        raise RunnerError(f"Profile {name!r} has unknown placeholders: {', '.join(unknown_placeholders)}")
    if profile["prompt_transport"] == "file" and "prompt_file" not in placeholders:
        raise RunnerError(f"File profile {name!r} must use {{prompt_file}}")
    if profile["prompt_transport"] == "arg" and "prompt_text" not in placeholders:
        raise RunnerError(f"Arg profile {name!r} must use {{prompt_text}}")
    return profile


def resolve_profile_executable(profile: dict[str, Any]) -> tuple[str, str | None]:
    """Return the requested executable name and first installed candidate, if any."""
    candidates = profile.get("executable_candidates", [profile["argv"][0]])
    for candidate in candidates:
        found = shutil.which(candidate)
        if found:
            return candidate, found
    return candidates[0], None


def load_profiles(custom_path: Path | None) -> dict[str, dict[str, Any]]:
    profiles = {name: validate_profile(name, raw) for name, raw in BUILTIN_PROFILES.items()}
    if custom_path and custom_path.exists():
        document = read_json(custom_path)
        agents = document.get("agents")
        if not isinstance(agents, dict):
            raise RunnerError(f"{custom_path} must contain an 'agents' object")
        for name, raw in agents.items():
            profiles[name] = validate_profile(name, raw)
    return profiles


def resolve_route(name: str | None) -> dict[str, Any] | None:
    if name is None:
        return None
    route = BUILTIN_ROUTES.get(name)
    if not route:
        raise RunnerError(f"Unknown route: {name}")
    return route


def apply_launch_route(args: argparse.Namespace) -> dict[str, Any] | None:
    route = resolve_route(args.route)
    if route:
        executor = route["executor"]
        args.cli = args.cli or executor["cli"]
        args.model = args.model or executor["model"]
        args.reasoning_effort = args.reasoning_effort or executor.get("reasoning_effort")
        args.coordinator_model = args.coordinator_model or route["coordinator"]["model"]
    if not args.cli:
        raise RunnerError("Choose --cli or select a --route that supplies one")
    return route


def replace_placeholders(tokens: list[str], values: dict[str, str]) -> list[str]:
    result: list[str] = []
    for token in tokens:
        expanded = token
        for key, value in values.items():
            expanded = expanded.replace("{" + key + "}", value)
        unresolved = PLACEHOLDER_RE.findall(expanded)
        if unresolved:
            raise RunnerError(f"Missing values for placeholders: {', '.join(sorted(set(unresolved)))}")
        result.append(expanded)
    return result


def build_command(
    profile: dict[str, Any],
    workspace: Path,
    prompt_file: Path,
    model: str | None,
    cli_agent: str | None,
    max_turns: int | None,
    max_cost_usd: float | None,
    reasoning_effort: str | None,
) -> tuple[list[str], str]:
    prompt_text = prompt_file.read_text(encoding="utf-8")
    values = {
        "workspace": str(workspace),
        "prompt_file": str(prompt_file),
        "prompt_text": prompt_text,
    }
    argv = list(profile["argv"])
    executable, found = resolve_profile_executable(profile)
    argv[0] = found or executable
    controls = (
        (model, "model_args", "model", str(model) if model is not None else ""),
        (cli_agent, "cli_agent_args", "cli_agent", str(cli_agent) if cli_agent is not None else ""),
        (max_turns, "max_turns_args", "max_turns", str(max_turns) if max_turns is not None else ""),
        (max_cost_usd, "cost_args", "max_cost_usd", str(max_cost_usd) if max_cost_usd is not None else ""),
        (
            reasoning_effort,
            "reasoning_effort_args",
            "reasoning_effort",
            str(reasoning_effort) if reasoning_effort is not None else "",
        ),
    )
    for requested, profile_key, value_key, string_value in controls:
        if requested is None:
            continue
        if profile_key not in profile:
            raise RunnerError(f"Selected CLI does not support requested control: {value_key}")
        values[value_key] = string_value
        argv.extend(profile[profile_key])
    command = replace_placeholders(argv, values)
    return command, profile["prompt_transport"]


def git_snapshot(workspace: Path) -> dict[str, Any]:
    check = subprocess.run(
        ["git", "-C", str(workspace), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if check.returncode != 0 or check.stdout.strip() != "true":
        return {"is_git": False, "dirty": False}
    head = subprocess.run(
        ["git", "-C", str(workspace), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    status = subprocess.run(
        ["git", "-C", str(workspace), "status", "--porcelain=v1"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if status.returncode != 0:
        raise RunnerError(f"Could not inspect git status: {status.stderr.strip()}")
    lines = status.stdout.splitlines()
    return {
        "is_git": True,
        "head": head.stdout.strip() if head.returncode == 0 else None,
        "dirty": bool(lines),
        "status": lines,
    }


def status_paths(status_lines: list[str]) -> set[str]:
    paths: set[str] = set()
    for line in status_lines:
        value = line[3:] if len(line) > 3 else line
        if " -> " in value:
            value = value.split(" -> ", 1)[1]
        paths.add(value.strip('"'))
    return paths


def scope_violations(workspace: Path, baseline: dict[str, Any], scope: dict[str, Any]) -> list[str]:
    if not scope.get("allowed_paths") and not scope.get("denied_paths") and scope.get("max_changed_files") is None:
        return []
    if not baseline.get("is_git"):
        return []
    current = git_snapshot(workspace)
    changed = status_paths(current.get("status", [])) - status_paths(baseline.get("status", []))
    allowed = scope.get("allowed_paths", [])
    denied = scope.get("denied_paths", [])
    violations: list[str] = []
    for path in sorted(changed):
        if denied and any(fnmatch.fnmatch(path, pattern) for pattern in denied):
            violations.append(f"denied path changed: {path}")
        elif allowed and not any(fnmatch.fnmatch(path, pattern) for pattern in allowed):
            violations.append(f"out-of-scope path changed: {path}")
    limit = scope.get("max_changed_files")
    if limit is not None and len(changed) > limit:
        violations.append(f"changed file count {len(changed)} exceeds limit {limit}")
    return violations


def make_job_id(agent: str) -> str:
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{agent}-{timestamp}-{secrets.token_hex(3)}"


def jobs_dir() -> Path:
    return state_home() / "jobs"


def plans_dir() -> Path:
    return state_home() / "plans"


def plan_dir(plan_id: str, must_exist: bool = True) -> Path:
    if not JOB_ID_RE.fullmatch(plan_id):
        raise RunnerError(f"Invalid plan ID: {plan_id!r}")
    path = plans_dir() / plan_id
    if must_exist and not path.is_dir():
        raise RunnerError(f"Unknown plan ID: {plan_id}")
    return path


def plan_items(text: str) -> list[dict[str, Any]]:
    return [
        {
            "id": f"item-{index:03d}",
            "title": title.strip(),
            "state": "done" if marker.casefold() == "x" else "pending",
            "job_ids": [],
            "evidence": [],
        }
        for index, (marker, title) in enumerate(CHECKLIST_RE.findall(text), start=1)
    ]


def read_plan(plan_id: str) -> tuple[Path, dict[str, Any]]:
    path = plan_dir(plan_id)
    return path, read_json(path / "plan.json")


def save_plan(path: Path, plan: dict[str, Any]) -> None:
    plan["updated_at"] = utc_now()
    counts: dict[str, int] = {}
    for item in plan.get("items", []):
        state = str(item.get("state", "unknown"))
        counts[state] = counts.get(state, 0) + 1
    plan["counts"] = counts
    plan["complete"] = bool(plan.get("items")) and all(
        item.get("state") == "done" for item in plan["items"]
    )
    write_json(path / "plan.json", plan)


def command_create_plan(args: argparse.Namespace) -> int:
    source = Path(args.plan_file).expanduser().resolve()
    workspace = Path(args.workspace).expanduser().resolve()
    if not source.is_file():
        raise RunnerError(f"Plan file does not exist: {source}")
    if not workspace.is_dir():
        raise RunnerError(f"Workspace is not a directory: {workspace}")
    if source.stat().st_size > MAX_TASK_BYTES:
        raise RunnerError(f"Plan file exceeds {MAX_TASK_BYTES} bytes")
    text = source.read_text(encoding="utf-8")
    problems = validate_named_sections(text, REQUIRED_PLAN_SECTIONS)
    items = plan_items(text)
    if not items:
        problems.append("Checklist must contain at least one '- [ ]' item")
    if problems:
        raise RunnerError("Plan is incomplete: " + "; ".join(problems))
    selected_plan_id = args.plan_id or f"plan-{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"
    path = plan_dir(selected_plan_id, must_exist=False)
    if path.exists():
        raise RunnerError(f"Plan already exists: {selected_plan_id}")
    path.mkdir(parents=True, mode=0o700)
    plan_copy = path / "plan.md"
    shutil.copyfile(source, plan_copy)
    os.chmod(plan_copy, 0o600)
    plan = {
        "plan_id": selected_plan_id,
        "title": args.title,
        "workspace": str(workspace),
        "created_at": utc_now(),
        "plan_file": str(plan_copy),
        "plan_sha256": hashlib.sha256(plan_copy.read_bytes()).hexdigest(),
        "items": items,
    }
    save_plan(path, plan)
    output = read_json(path / "plan.json")
    print(json.dumps(output, indent=2, sort_keys=True) if args.json else selected_plan_id)
    return 0


def command_plan_status(args: argparse.Namespace) -> int:
    _, plan = read_plan(args.plan_id)
    if args.json:
        print(json.dumps(plan, indent=2, sort_keys=True))
    else:
        print(f"plan_id: {plan['plan_id']}")
        print(f"title: {plan['title']}")
        print(f"complete: {plan.get('complete', False)}")
        for item in plan.get("items", []):
            print(f"{item['id']}\t{item['state']}\t{item['title']}")
    return 0


def bind_plan_item(plan_id: str, item_id: str, job_id: str) -> None:
    path, plan = read_plan(plan_id)
    matching = [item for item in plan.get("items", []) if item.get("id") == item_id]
    if not matching:
        raise RunnerError(f"Unknown checklist item {item_id!r} in plan {plan_id}")
    item = matching[0]
    if item.get("state") == "done":
        raise RunnerError(f"Checklist item {item_id} is already done")
    item["state"] = "in_progress"
    if job_id not in item["job_ids"]:
        item["job_ids"].append(job_id)
    save_plan(path, plan)


def job_dir(job_id: str, must_exist: bool = True) -> Path:
    if not JOB_ID_RE.fullmatch(job_id):
        raise RunnerError(f"Invalid job ID: {job_id!r}")
    path = jobs_dir() / job_id
    if must_exist and not path.is_dir():
        raise RunnerError(f"Unknown job ID: {job_id}")
    return path


def pid_alive(pid: Any) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def usage_summary(log_path: Path) -> dict[str, Any] | None:
    """Extract the last provider-reported token/cost snapshot from JSONL output."""
    if not log_path.exists():
        return None
    latest: dict[str, Any] | None = None
    try:
        with log_path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                fields: dict[str, Any] = {}

                def visit(node: Any) -> None:
                    if isinstance(node, dict):
                        for key, child in node.items():
                            normalized = key.casefold()
                            if isinstance(child, (int, float)) and not isinstance(child, bool) and (
                                "token" in normalized
                                or normalized in {"total_cost_usd", "cost_usd", "duration_ms"}
                            ):
                                fields[key] = child
                            elif isinstance(child, (dict, list)):
                                visit(child)
                    elif isinstance(node, list):
                        for child in node:
                            visit(child)

                visit(value)
                if fields:
                    latest = {"provider_reported": True, **fields}
    except OSError:
        return None
    return latest


def get_status(path: Path) -> dict[str, Any]:
    meta = read_json(path / "meta.json")
    result_path = path / "result.json"
    if result_path.exists():
        result = read_json(result_path)
        status = {**meta, **result}
    else:
        worker_alive = pid_alive(meta.get("worker_pid"))
        state = "cancelling" if (path / "cancel-requested").exists() and worker_alive else "running" if worker_alive else "lost"
        status = {**meta, "state": state}
    review_path = path / "review.json"
    review = read_json(review_path) if review_path.exists() else None
    status["review"] = review
    if status["state"] == "succeeded":
        status["review_state"] = review.get("verdict") if review else "required"
        status["acceptance_state"] = review.get("verdict") if review else "awaiting_review"
    else:
        status["review_state"] = review.get("verdict") if review else "not_ready"
        status["acceptance_state"] = "not_accepted"
    if not status.get("usage_summary"):
        status["usage_summary"] = usage_summary(path / "stdout.log")
    questions = pending_questions(path)
    status["execution_state"] = status["state"]
    status["pending_questions"] = questions
    status["attention_required"] = bool(questions)
    if questions and status["state"] in {"starting", "running"}:
        status["state"] = "needs_input"
    heartbeat_path = path / "heartbeat.json"
    if heartbeat_path.exists():
        status["heartbeat"] = read_json(heartbeat_path)
    events = read_events(path, 30)
    progress = [event for event in events if event.get("type") == "worker_progress"]
    status["current_phase"] = progress[-1].get("phase") if progress else None
    status["last_event"] = events[-1] if events else None
    return status


def workspace_observation(workspace: Path) -> dict[str, Any]:
    snapshot = git_snapshot(workspace)
    if not snapshot["is_git"]:
        return snapshot
    diff = subprocess.run(
        ["git", "-C", str(workspace), "diff", "--stat", "--", "."],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    snapshot["diff_stat"] = diff.stdout.splitlines() if diff.returncode == 0 else []
    return snapshot


def print_value(value: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(value, indent=2, sort_keys=True))
        return
    for key in (
        "job_id",
        "cli",
        "model",
        "cli_agent",
        "route",
        "coordinator_model",
        "reasoning_effort",
        "group",
        "role",
        "state",
        "review_state",
        "acceptance_state",
        "workspace",
        "started_at",
        "finished_at",
        "exit_code",
    ):
        if key in value and value[key] is not None:
            print(f"{key}: {value[key]}")


def command_profiles(args: argparse.Namespace) -> int:
    profiles = load_profiles(config_path(args.config))
    output = {
        name: {
            "display_name": profile.get("display_name", name),
            "description": profile.get("description"),
            "maturity": profile.get("maturity", "stable"),
            "docs_url": profile.get("docs_url"),
            "install_hint": profile.get("install_hint"),
            "executable": profile["argv"][0],
            "executable_candidates": profile.get("executable_candidates", [profile["argv"][0]]),
            "prompt_transport": profile["prompt_transport"],
            "supports_model": "model_args" in profile,
            "supports_cli_agent": "cli_agent_args" in profile,
            "supports_max_turns": "max_turns_args" in profile,
            "supports_max_cost_usd": "cost_args" in profile,
            "supports_reasoning_effort": "reasoning_effort_args" in profile,
            "can_discover_models": "discover_models_argv" in profile,
            "can_discover_cli_agents": "discover_cli_agents_argv" in profile,
        }
        for name, profile in sorted(profiles.items())
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


def command_routes(args: argparse.Namespace) -> int:
    print(json.dumps(BUILTIN_ROUTES, indent=2, sort_keys=True))
    return 0


def command_doctor(args: argparse.Namespace) -> int:
    profiles = load_profiles(config_path(args.config))
    names = sorted(profiles) if args.cli == "all" else [args.cli]
    report: dict[str, Any] = {}
    failed = False
    for name in names:
        if name not in profiles:
            raise RunnerError(f"Unknown CLI profile: {name}")
        profile = profiles[name]
        executable, found = resolve_profile_executable(profile)
        item: dict[str, Any] = {
            "display_name": profile.get("display_name", name),
            "maturity": profile.get("maturity", "stable"),
            "executable": executable,
            "executable_candidates": profile.get("executable_candidates", [profile["argv"][0]]),
            "path": found,
            "available": bool(found),
            "install_hint": profile.get("install_hint"),
            "docs_url": profile.get("docs_url"),
        }
        if found:
            version = subprocess.run(
                [found, "--version"], capture_output=True, text=True, timeout=10, check=False
            )
            combined = (version.stdout or version.stderr).strip().splitlines()
            item["version"] = combined[0] if combined else None
            item["version_exit_code"] = version.returncode
        else:
            failed = True
        report[name] = item
    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if failed else 0


ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def discovery_result(
    profile: dict[str, Any],
    key: str,
    workspace: Path,
    query: str | None,
    timeout_seconds: float,
    full: bool,
) -> dict[str, Any]:
    configured_key = "models" if key == "discover_models_argv" else "cli_agents"
    result: dict[str, Any] = {"configured": profile.get(configured_key, [])}
    command_template = profile.get(key)
    if not command_template:
        result["discovery"] = "not_supported"
        return result
    command = replace_placeholders(command_template, {"workspace": str(workspace)})
    executable = shutil.which(command[0])
    if not executable:
        result.update({"discovery": "unavailable", "error": f"Executable not found: {command[0]}"})
        return result
    try:
        completed = subprocess.run(
            command,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        result.update({"discovery": "timed_out", "command": command})
        return result
    output = ANSI_RE.sub("", (completed.stdout or completed.stderr).strip())
    if query:
        lowered = query.casefold()
        output = "\n".join(line for line in output.splitlines() if lowered in line.casefold())
    elif not full and len(output.splitlines()) > 120:
        compact_lines = [
            line
            for line in output.splitlines()
            if line and (not line.startswith((" ", "\t")) or line.lstrip().startswith("aliases:"))
        ]
        output = "\n".join(compact_lines[:120])
        result["compact"] = True
        result["hint"] = "Use --query <text> for exact matches or --full for unabridged output."
    result.update(
        {
            "discovery": "succeeded" if completed.returncode == 0 else "failed",
            "command": command,
            "exit_code": completed.returncode,
            "output": output,
        }
    )
    return result


def command_catalog(args: argparse.Namespace) -> int:
    profiles = load_profiles(config_path(args.config))
    names = sorted(profiles) if args.cli == "all" else [args.cli]
    workspace = Path(args.workspace).expanduser().resolve()
    if not workspace.is_dir():
        raise RunnerError(f"Workspace is not a directory: {workspace}")
    catalog: dict[str, Any] = {}
    for name in names:
        if name not in profiles:
            raise RunnerError(f"Unknown CLI profile: {name}")
        profile = profiles[name]
        executable, found = resolve_profile_executable(profile)
        item: dict[str, Any] = {
            "display_name": profile.get("display_name", name),
            "description": profile.get("description"),
            "maturity": profile.get("maturity", "stable"),
            "docs_url": profile.get("docs_url"),
            "install_hint": profile.get("install_hint"),
            "available": bool(found),
            "executable": executable,
            "executable_candidates": profile.get("executable_candidates", [profile["argv"][0]]),
            "supports": {
                "model": "model_args" in profile,
                "cli_agent": "cli_agent_args" in profile,
                "max_turns": "max_turns_args" in profile,
                "max_cost_usd": "cost_args" in profile,
                "reasoning_effort": "reasoning_effort_args" in profile,
            },
        }
        if not args.no_discovery and item["available"]:
            item["models"] = discovery_result(
                profile, "discover_models_argv", workspace, args.query, args.timeout_seconds, args.full
            )
            item["cli_agents"] = discovery_result(
                profile, "discover_cli_agents_argv", workspace, args.query, args.timeout_seconds, args.full
            )
        else:
            item["models"] = {"configured": profile.get("models", []), "discovery": "skipped"}
            item["cli_agents"] = {"configured": profile.get("cli_agents", []), "discovery": "skipped"}
        catalog[name] = item
    print(json.dumps(catalog, indent=2, sort_keys=True))
    return 0


def active_jobs_for_workspace(workspace: Path) -> list[str]:
    active: list[str] = []
    root = jobs_dir()
    if not root.exists():
        return active
    for path in root.iterdir():
        if not path.is_dir() or not (path / "meta.json").exists():
            continue
        try:
            status = get_status(path)
        except RunnerError:
            continue
        if status.get("workspace") == str(workspace) and status.get("execution_state") in {
            "starting",
            "running",
            "cancelling",
        }:
            active.append(status["job_id"])
    return active


def validate_dependencies(job_ids: list[str]) -> None:
    for dependency in job_ids:
        status = get_status(job_dir(dependency))
        if status.get("acceptance_state") != "accepted":
            raise RunnerError(
                f"Dependency {dependency} is {status.get('state')} with review "
                f"{status.get('review_state')}; launch only after it is independently accepted"
            )


def command_launch(args: argparse.Namespace) -> int:
    route = apply_launch_route(args)
    workspace = Path(args.workspace).expanduser().resolve()
    task_source = Path(args.task_file).expanduser().resolve()
    if not workspace.is_dir():
        raise RunnerError(f"Workspace is not a directory: {workspace}")
    if not task_source.is_file():
        raise RunnerError(f"Task file does not exist: {task_source}")
    if task_source.stat().st_size > MAX_TASK_BYTES:
        raise RunnerError(f"Task file exceeds {MAX_TASK_BYTES} bytes")
    task_text = task_source.read_text(encoding="utf-8")
    task_stats = task_packet_stats(task_text)
    if route and task_stats["bytes"] > route["context_budget_bytes"] and not args.allow_large_context:
        raise RunnerError(
            f"Task packet is {task_stats['bytes']} bytes, above the {route['context_budget_bytes']}-byte "
            "quality-first context budget. Create a durable plan and split it into checklist-bound "
            "jobs, or explicitly pass --allow-large-context."
        )
    task_problems = validate_task_packet(task_text)
    if task_problems and not args.allow_unstructured_task:
        raise RunnerError(
            "Task contract is incomplete: "
            + "; ".join(task_problems)
            + ". Use validate-task or pass --allow-unstructured-task for intentional legacy packets."
        )
    profiles = load_profiles(config_path(args.config))
    if args.cli not in profiles:
        raise RunnerError(f"Unknown CLI profile: {args.cli}")
    profile = profiles[args.cli]
    executable, found = resolve_profile_executable(profile)
    if not found:
        candidates = ", ".join(profile.get("executable_candidates", [executable]))
        hint = f" {profile['install_hint']}" if profile.get("install_hint") else ""
        raise RunnerError(f"Executable not found on PATH (tried: {candidates}).{hint}")
    if bool(args.plan_id) != bool(args.checklist_item):
        raise RunnerError("--plan-id and --checklist-item must be supplied together")
    if args.plan_id:
        _, plan = read_plan(args.plan_id)
        matching = [item for item in plan.get("items", []) if item.get("id") == args.checklist_item]
        if not matching:
            raise RunnerError(f"Unknown checklist item {args.checklist_item!r} in plan {args.plan_id}")
        if matching[0].get("state") == "done":
            raise RunnerError(f"Checklist item {args.checklist_item} is already done")
    validate_dependencies(args.depends_on)
    active = active_jobs_for_workspace(workspace)
    if active and not args.allow_concurrent_workspace:
        raise RunnerError(
            "Another job is already writing this workspace: "
            + ", ".join(active)
            + ". Use a separate worktree or explicitly pass --allow-concurrent-workspace."
        )
    snapshot = git_snapshot(workspace)
    if snapshot["dirty"] and not args.allow_dirty:
        raise RunnerError("Workspace has uncommitted changes; use a clean worktree or pass --allow-dirty after recording the baseline")
    selected_job_id = args.job_id or make_job_id(args.cli)
    path = job_dir(selected_job_id, must_exist=False)
    if path.exists():
        raise RunnerError(f"Job already exists: {selected_job_id}")
    path.mkdir(parents=True, mode=0o700)
    original_task = path / "task-original.md"
    shutil.copyfile(task_source, original_task)
    os.chmod(original_task, 0o600)
    channel_path = write_channel_wrapper(path, selected_job_id)
    task_copy = path / "task.md"
    task_copy.write_text(
        coordination_preamble(channel_path) + original_task.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    os.chmod(task_copy, 0o600)
    task_sha256 = hashlib.sha256(task_copy.read_bytes()).hexdigest()
    command, transport = build_command(
        profile,
        workspace,
        task_copy,
        args.model,
        args.cli_agent,
        args.max_turns,
        args.max_cost_usd,
        args.reasoning_effort,
    )
    redacted_command = ["<prompt_text>" if token == task_copy.read_text(encoding="utf-8") else token for token in command]
    meta = {
        "job_id": selected_job_id,
        "agent": args.cli,
        "cli": args.cli,
        "workspace": str(workspace),
        "created_at": utc_now(),
        "state": "starting",
        "timeout_seconds": args.timeout_seconds,
        "notify": args.notify,
        "group": args.group,
        "role": args.role,
        "depends_on": args.depends_on,
        "profile": profile,
        "prompt_transport": transport,
        "model": args.model,
        "route": args.route,
        "coordinator_model": args.coordinator_model,
        "reasoning_effort": args.reasoning_effort,
        "cli_agent": args.cli_agent,
        "max_turns": args.max_turns,
        "max_cost_usd": args.max_cost_usd,
        "task_sha256": task_sha256,
        "task_stats": task_stats,
        "context_budget_bytes": route.get("context_budget_bytes") if route else None,
        "review_required": route.get("review", {}).get("required", True) if route else True,
        "plan_id": args.plan_id,
        "checklist_item": args.checklist_item,
        "scope": {
            "allowed_paths": args.allow_path,
            "denied_paths": args.deny_path,
            "max_changed_files": args.max_changed_files,
        },
        "git_baseline": snapshot,
        "command": redacted_command,
        "channel": str(channel_path),
    }
    write_json(path / "meta.json", meta)
    add_event(
        path,
        "job_created",
        cli=args.cli,
        model=args.model,
        cli_agent=args.cli_agent,
        group=args.group,
        role=args.role,
        depends_on=args.depends_on,
        task_sha256=task_sha256,
        workspace=str(workspace),
        route=args.route,
        plan_id=args.plan_id,
        checklist_item=args.checklist_item,
    )
    if args.plan_id:
        bind_plan_item(args.plan_id, args.checklist_item, selected_job_id)
    runner_log = (path / "runner.log").open("ab", buffering=0)
    worker = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "_worker", "--job-dir", str(path)],
        stdin=subprocess.DEVNULL,
        stdout=runner_log,
        stderr=runner_log,
        start_new_session=True,
        close_fds=True,
    )
    runner_log.close()
    meta["worker_pid"] = worker.pid
    write_json(path / "meta.json", meta)
    output = {
        "job_id": selected_job_id,
        "agent": args.cli,
        "cli": args.cli,
        "state": "starting",
        "group": args.group,
        "role": args.role,
        "model": args.model,
        "route": args.route,
        "coordinator_model": args.coordinator_model,
        "reasoning_effort": args.reasoning_effort,
        "cli_agent": args.cli_agent,
        "review_state": "required",
        "plan_id": args.plan_id,
        "checklist_item": args.checklist_item,
        "workspace": str(workspace),
        "job_dir": str(path),
    }
    print_value(output, args.json)
    return 0


def terminate_process_group(pid: int, force: bool = False) -> None:
    try:
        os.killpg(pid, signal.SIGKILL if force else signal.SIGTERM)
    except ProcessLookupError:
        pass


def command_worker(args: argparse.Namespace) -> int:
    path = Path(args.job_dir).resolve()
    meta_path = path / "meta.json"
    meta = read_json(meta_path)
    prompt_file = path / "task.md"
    profile = validate_profile(meta["agent"], meta["profile"])
    workspace = Path(meta["workspace"])
    stdout_path = path / "stdout.log"
    stderr_path = path / "stderr.log"
    started = time.monotonic()
    started_at = utc_now()
    state = "failed"
    exit_code = 127
    timed_out = False
    violated_scope: list[str] = []
    try:
        command, transport = build_command(
            profile,
            workspace,
            prompt_file,
            meta.get("model"),
            meta.get("cli_agent"),
            meta.get("max_turns"),
            meta.get("max_cost_usd"),
            meta.get("reasoning_effort"),
        )
        with stdout_path.open("wb") as stdout_handle, stderr_path.open("wb") as stderr_handle:
            os.chmod(stdout_path, 0o600)
            os.chmod(stderr_path, 0o600)
            stdin_handle = prompt_file.open("rb") if transport == "stdin" else subprocess.DEVNULL
            try:
                child = subprocess.Popen(
                    command,
                    cwd=workspace,
                    stdin=stdin_handle,
                    stdout=stdout_handle,
                    stderr=stderr_handle,
                    start_new_session=True,
                    close_fds=True,
                )
                meta.update({"state": "running", "started_at": started_at, "child_pid": child.pid})
                write_json(meta_path, meta)
                add_event(path, "agent_started", child_pid=child.pid)
                deadline = time.monotonic() + meta["timeout_seconds"]
                while child.poll() is None and time.monotonic() < deadline:
                    write_json(
                        path / "heartbeat.json",
                        {"at": utc_now(), "worker_pid": os.getpid(), "child_pid": child.pid},
                    )
                    violated_scope = scope_violations(
                        workspace, meta.get("git_baseline", {}), meta.get("scope", {})
                    )
                    if violated_scope:
                        add_event(path, "scope_violation", violations=violated_scope)
                        terminate_process_group(child.pid)
                        break
                    time.sleep(2)
                if violated_scope:
                    try:
                        exit_code = child.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        terminate_process_group(child.pid, force=True)
                        exit_code = child.wait()
                elif child.poll() is None:
                    timed_out = True
                    add_event(path, "timeout_reached", timeout_seconds=meta["timeout_seconds"])
                    terminate_process_group(child.pid)
                    try:
                        exit_code = child.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        terminate_process_group(child.pid, force=True)
                        exit_code = child.wait()
                else:
                    exit_code = child.returncode
            finally:
                if hasattr(stdin_handle, "close"):
                    stdin_handle.close()
        if violated_scope:
            state = "scope_violated"
        elif timed_out:
            state = "timed_out"
        elif (path / "cancel-requested").exists():
            state = "cancelled"
        else:
            state = "succeeded" if exit_code == 0 else "failed"
    except Exception as exc:  # Worker must leave durable failure evidence.
        with stderr_path.open("ab") as stderr_handle:
            stderr_handle.write(f"runner error: {type(exc).__name__}: {exc}\n".encode("utf-8", errors="replace"))
        os.chmod(stderr_path, 0o600)
    finished_at = utc_now()
    reported_usage = usage_summary(stdout_path)
    result = {
        "state": state,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_seconds": round(time.monotonic() - started, 3),
        "exit_code": exit_code,
        "timed_out": timed_out,
        "scope_violations": violated_scope,
        "usage_summary": reported_usage,
    }
    write_json(path / "result.json", result)
    meta.update(result)
    write_json(meta_path, meta)
    add_event(path, "agent_finished", state=state, exit_code=exit_code)
    if meta.get("notify"):
        suffix = "; review required" if state == "succeeded" else ""
        notify_user("Agent Orchestrator", f"Job {meta['job_id']} finished: {state}{suffix}")
    return 0 if state == "succeeded" else 1


def command_status(args: argparse.Namespace) -> int:
    status = get_status(job_dir(args.job_id))
    print_value(status, args.json)
    return 0


def command_wait(args: argparse.Namespace) -> int:
    path = job_dir(args.job_id)
    deadline = time.monotonic() + args.timeout_seconds
    while True:
        status = get_status(path)
        if status["state"] == "needs_input":
            print_value(status, args.json)
            return 3
        if status["state"] not in {"starting", "running", "cancelling"}:
            print_value(status, args.json)
            return 0 if status["state"] == "succeeded" else 1
        if time.monotonic() >= deadline:
            print_value(status, args.json)
            return 2
        time.sleep(min(args.poll_seconds, max(0.1, deadline - time.monotonic())))


def tail_lines(path: Path, count: int) -> str:
    if not path.exists():
        return ""
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        lines = handle.readlines()
    return "".join(lines[-count:])


def command_logs(args: argparse.Namespace) -> int:
    path = job_dir(args.job_id)
    streams = ("stdout", "stderr") if args.stream == "both" else (args.stream,)
    for stream in streams:
        content = tail_lines(path / f"{stream}.log", args.tail)
        if len(streams) > 1:
            print(f"[{stream}]")
        if content:
            print(content, end="" if content.endswith("\n") else "\n")
    return 0


def command_event(args: argparse.Namespace) -> int:
    path = job_dir(args.job_id)
    event = add_event(path, "worker_progress", phase=args.phase, message=args.message)
    print(json.dumps(event, sort_keys=True))
    return 0


def question_text(args: argparse.Namespace) -> str:
    if bool(args.question) == bool(args.question_file):
        raise RunnerError("Provide exactly one of --question or --question-file")
    if args.question_file:
        source = Path(args.question_file).expanduser().resolve()
        if not source.is_file():
            raise RunnerError(f"Question file does not exist: {source}")
        if source.stat().st_size > MAX_TASK_BYTES:
            raise RunnerError(f"Question file exceeds {MAX_TASK_BYTES} bytes")
        value = source.read_text(encoding="utf-8")
    else:
        value = args.question
    if not value or not value.strip():
        raise RunnerError("Question cannot be empty")
    return value.strip()


def command_ask(args: argparse.Namespace) -> int:
    path = job_dir(args.job_id)
    value = question_text(args)
    question_id = f"q-{time.time_ns()}-{secrets.token_hex(3)}"
    question_path = path / "questions" / f"{question_id}.json"
    question = {
        "id": question_id,
        "state": "pending",
        "question": value,
        "created_at": utc_now(),
    }
    write_json(question_path, question)
    add_event(path, "question_asked", question_id=question_id, question=value)
    meta = read_json(path / "meta.json")
    if meta.get("notify"):
        notify_user("Agent Orchestrator", f"Job {args.job_id} needs input")
    deadline = time.monotonic() + args.wait_seconds
    while time.monotonic() < deadline:
        current = read_json(question_path)
        if current.get("state") == "answered":
            print(current["answer"])
            return 0
        if current.get("state") == "cancelled":
            print("Question was cancelled before an answer was provided.", file=sys.stderr)
            return 2
        time.sleep(min(1, max(0.1, deadline - time.monotonic())))
    current = read_json(question_path)
    if current.get("state") == "pending":
        current.update({"state": "expired", "expired_at": utc_now()})
        write_json(question_path, current)
        add_event(path, "question_expired", question_id=question_id)
    print(f"Timed out waiting for answer to {question_id}.", file=sys.stderr)
    return 2


def command_questions(args: argparse.Namespace) -> int:
    path = job_dir(args.job_id)
    root = path / "questions"
    questions: list[dict[str, Any]] = []
    if root.exists():
        for question_path in sorted(root.glob("*.json")):
            question = read_json(question_path)
            if args.all or question.get("state") == "pending":
                questions.append(question)
    if args.json:
        print(json.dumps(questions, indent=2, sort_keys=True))
    else:
        for question in questions:
            print(f"{question['id']}\t{question['state']}\t{question['question']}")
    return 0


def answer_text(args: argparse.Namespace) -> str:
    if bool(args.text) == bool(args.file):
        raise RunnerError("Provide exactly one of --text or --file")
    if args.file:
        source = Path(args.file).expanduser().resolve()
        if not source.is_file():
            raise RunnerError(f"Answer file does not exist: {source}")
        value = source.read_text(encoding="utf-8")
    else:
        value = args.text
    if not value or not value.strip():
        raise RunnerError("Answer cannot be empty")
    return value.strip()


def command_answer(args: argparse.Namespace) -> int:
    path = job_dir(args.job_id)
    if not JOB_ID_RE.fullmatch(args.question_id):
        raise RunnerError(f"Invalid question ID: {args.question_id!r}")
    question_path = path / "questions" / f"{args.question_id}.json"
    if not question_path.is_file():
        raise RunnerError(f"Unknown question ID: {args.question_id}")
    question = read_json(question_path)
    if question.get("state") != "pending":
        raise RunnerError(f"Question is not pending; current state is {question.get('state')}")
    value = answer_text(args)
    question.update({"state": "answered", "answer": value, "answered_at": utc_now()})
    write_json(question_path, question)
    add_event(path, "question_answered", question_id=args.question_id)
    print_value({"job_id": args.job_id, "state": "answered", "question_id": args.question_id}, args.json)
    return 0


def review_notes(args: argparse.Namespace) -> str:
    if bool(args.notes) == bool(args.notes_file):
        raise RunnerError("Provide exactly one of --notes or --notes-file")
    if args.notes_file:
        source = Path(args.notes_file).expanduser().resolve()
        if not source.is_file():
            raise RunnerError(f"Review notes file does not exist: {source}")
        if source.stat().st_size > MAX_TASK_BYTES:
            raise RunnerError(f"Review notes file exceeds {MAX_TASK_BYTES} bytes")
        return source.read_text(encoding="utf-8")
    return args.notes


def update_plan_after_review(meta: dict[str, Any], review: dict[str, Any]) -> None:
    plan_id = meta.get("plan_id")
    item_id = meta.get("checklist_item")
    if not plan_id or not item_id:
        return
    path, plan = read_plan(plan_id)
    matching = [item for item in plan.get("items", []) if item.get("id") == item_id]
    if not matching:
        raise RunnerError(f"Plan {plan_id} no longer contains checklist item {item_id}")
    item = matching[0]
    verdict = review["verdict"]
    if verdict == "accepted":
        item["state"] = "done"
    elif verdict == "rejected":
        item["state"] = "blocked"
    else:
        item["state"] = "in_progress"
    item.setdefault("evidence", []).append(
        {
            "at": review["reviewed_at"],
            "job_id": meta["job_id"],
            "verdict": verdict,
            "reviewer": review["reviewer"],
            "tests": review["tests"],
        }
    )
    was_complete = bool(plan.get("complete"))
    save_plan(path, plan)
    updated = read_json(path / "plan.json")
    if updated.get("complete") and not was_complete and meta.get("notify"):
        notify_user("Agent Orchestrator", f"Plan {plan_id} is complete")


def command_record_review(args: argparse.Namespace) -> int:
    path = job_dir(args.job_id)
    meta = read_json(path / "meta.json")
    status = get_status(path)
    if status.get("execution_state") != "succeeded":
        raise RunnerError(
            f"Review can only be recorded after successful execution; current state is {status.get('execution_state')}"
        )
    if status.get("acceptance_state") == "accepted":
        raise RunnerError("This job is already accepted; review evidence is immutable")
    notes = review_notes(args)
    if args.verdict == "accepted" and not args.test:
        raise RunnerError("An accepted review requires at least one independently run --test result")
    review = {
        "review_id": f"review-{time.time_ns()}-{secrets.token_hex(3)}",
        "job_id": args.job_id,
        "verdict": args.verdict,
        "reviewer": args.reviewer,
        "reviewed_at": utc_now(),
        "tests": args.test,
        "notes": notes,
    }
    write_json(path / "reviews" / f"{review['review_id']}.json", review)
    write_json(path / "review.json", review)
    add_event(
        path,
        "review_recorded",
        verdict=args.verdict,
        reviewer=args.reviewer,
        test_count=len(args.test),
    )
    update_plan_after_review(meta, review)
    if meta.get("notify"):
        notify_user("Agent Orchestrator", f"Job {args.job_id} review: {args.verdict}")
    print(json.dumps(review, indent=2, sort_keys=True) if args.json else args.verdict)
    return 0


def command_observe(args: argparse.Namespace) -> int:
    path = job_dir(args.job_id)
    status = get_status(path)
    created_at = status.get("created_at")
    try:
        created = dt.datetime.fromisoformat(created_at) if created_at else None
        elapsed = (dt.datetime.now(dt.timezone.utc) - created).total_seconds() if created else None
    except ValueError:
        elapsed = None
    observation = {
        **status,
        "elapsed_seconds": round(elapsed, 3) if elapsed is not None else None,
        "logs": {
            "stdout_bytes": (path / "stdout.log").stat().st_size if (path / "stdout.log").exists() else 0,
            "stderr_bytes": (path / "stderr.log").stat().st_size if (path / "stderr.log").exists() else 0,
            "stdout_tail": tail_lines(path / "stdout.log", args.tail),
            "stderr_tail": tail_lines(path / "stderr.log", args.tail),
        },
        "recent_events": read_events(path, args.event_limit),
        "workspace_observation": workspace_observation(Path(status["workspace"])),
    }
    print(json.dumps(observation, indent=2, sort_keys=True))
    return 0


def command_watch(args: argparse.Namespace) -> int:
    path = job_dir(args.job_id)
    offsets = {"stdout": 0, "stderr": 0}
    previous_state: str | None = None
    try:
        while True:
            status = get_status(path)
            if status["state"] != previous_state:
                print(f"[{utc_now()}] state={status['state']} attention_required={status['attention_required']}", flush=True)
                previous_state = status["state"]
            for stream in ("stdout", "stderr"):
                log_path = path / f"{stream}.log"
                if not log_path.exists():
                    continue
                with log_path.open("rb") as handle:
                    handle.seek(offsets[stream])
                    data = handle.read()
                    offsets[stream] = handle.tell()
                if data:
                    print(f"[{stream}] {data.decode('utf-8', errors='replace')}", end="", flush=True)
            if status["state"] == "needs_input":
                for question in status["pending_questions"]:
                    print(f"[question {question['id']}] {question['question']}", flush=True)
                return 3
            if status["state"] not in {"starting", "running", "cancelling"}:
                return 0 if status["state"] == "succeeded" else 1
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 130


def command_cancel(args: argparse.Namespace) -> int:
    path = job_dir(args.job_id)
    status = get_status(path)
    if status["state"] not in {"starting", "running", "needs_input", "cancelling"}:
        print_value(status, args.json)
        return 0
    marker = path / "cancel-requested"
    marker.write_text(utc_now() + "\n", encoding="utf-8")
    os.chmod(marker, 0o600)
    child_pid = status.get("child_pid")
    if pid_alive(child_pid):
        terminate_process_group(child_pid, force=args.force)
    for question in pending_questions(path):
        question["state"] = "cancelled"
        question["cancelled_at"] = utc_now()
        write_json(path / "questions" / f"{question['id']}.json", question)
    add_event(path, "cancellation_requested", force=args.force)
    output = {"job_id": args.job_id, "state": "cancelling", "force": args.force}
    print_value(output, args.json)
    return 0


def command_list(args: argparse.Namespace) -> int:
    root = jobs_dir()
    jobs = []
    if root.exists():
        for path in sorted(root.iterdir(), reverse=True):
            if path.is_dir() and (path / "meta.json").exists():
                try:
                    status = get_status(path)
                    jobs.append(
                        {
                            key: status.get(key)
                            for key in (
                                "job_id",
                                "cli",
                                "model",
                                "reasoning_effort",
                                "route",
                                "cli_agent",
                                "group",
                                "role",
                                "state",
                                "review_state",
                                "acceptance_state",
                                "plan_id",
                                "checklist_item",
                                "current_phase",
                                "workspace",
                                "created_at",
                            )
                        }
                    )
                except RunnerError:
                    continue
    if args.json:
        print(json.dumps(jobs, indent=2, sort_keys=True))
    else:
        for item in jobs:
            print(
                "\t".join(
                    str(item.get(key) or "")
                    for key in ("job_id", "cli", "model", "cli_agent", "state", "workspace")
                )
            )
    return 0


def elapsed_seconds(status: dict[str, Any]) -> float | None:
    started_at = status.get("started_at") or status.get("created_at")
    if not started_at:
        return None
    try:
        start = dt.datetime.fromisoformat(started_at)
        end_value = status.get("finished_at")
        end = dt.datetime.fromisoformat(end_value) if end_value else dt.datetime.now(dt.timezone.utc)
        return max(0.0, (end - start).total_seconds())
    except (TypeError, ValueError):
        return None


def dashboard_rows(group: str | None, limit: int) -> list[dict[str, Any]]:
    root = jobs_dir()
    rows: list[dict[str, Any]] = []
    if not root.exists():
        return rows
    for path in sorted(root.iterdir(), reverse=True):
        if not path.is_dir() or not (path / "meta.json").exists():
            continue
        try:
            status = get_status(path)
            if group and status.get("group") != group:
                continue
            current = git_snapshot(Path(status["workspace"]))
            baseline = status.get("git_baseline", {})
            changed = status_paths(current.get("status", [])) - status_paths(baseline.get("status", []))
            last_event = status.get("last_event") or {}
            rows.append(
                {
                    "job_id": status.get("job_id"),
                    "group": status.get("group"),
                    "role": status.get("role"),
                    "cli": status.get("cli") or status.get("agent"),
                    "model": status.get("model"),
                    "reasoning_effort": status.get("reasoning_effort"),
                    "route": status.get("route"),
                    "cli_agent": status.get("cli_agent"),
                    "state": status.get("state"),
                    "review_state": status.get("review_state"),
                    "acceptance_state": status.get("acceptance_state"),
                    "plan_id": status.get("plan_id"),
                    "checklist_item": status.get("checklist_item"),
                    "usage_summary": status.get("usage_summary"),
                    "phase": status.get("current_phase"),
                    "elapsed_seconds": elapsed_seconds(status),
                    "changed_files": len(changed),
                    "pending_questions": len(status.get("pending_questions", [])),
                    "last_event_at": last_event.get("at"),
                    "workspace": status.get("workspace"),
                }
            )
        except RunnerError:
            continue
        if len(rows) >= limit:
            break
    return rows


def shortened(value: Any, width: int) -> str:
    text_value = str(value or "-")
    return text_value if len(text_value) <= width else text_value[: width - 1] + "…"


def print_dashboard(rows: list[dict[str, Any]]) -> None:
    headings = ("JOB", "GROUP", "ROLE", "CLI / MODEL / EFFORT", "EXECUTION", "REVIEW", "PHASE", "FILES", "TIME")
    widths = (25, 12, 16, 34, 13, 15, 14, 5, 8)
    print("  ".join(shortened(value, width).ljust(width) for value, width in zip(headings, widths)))
    for row in rows:
        selection = " / ".join(
            str(value) for value in (row["cli"], row["model"], row["reasoning_effort"]) if value
        )
        elapsed = row["elapsed_seconds"]
        elapsed_text = f"{int(elapsed)}s" if elapsed is not None else "-"
        values = (
            row["job_id"],
            row["group"],
            row["role"],
            selection,
            row["state"],
            row["review_state"],
            row["phase"],
            row["changed_files"],
            elapsed_text,
        )
        print("  ".join(shortened(value, width).ljust(width) for value, width in zip(values, widths)))


def command_dashboard(args: argparse.Namespace) -> int:
    try:
        while True:
            rows = dashboard_rows(args.group, args.limit)
            if args.json:
                print(json.dumps(rows, indent=2, sort_keys=True))
            else:
                if args.watch and sys.stdout.isatty():
                    print("\x1b[2J\x1b[H", end="")
                print_dashboard(rows)
            if not args.watch:
                return 0
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 130


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    profiles = subparsers.add_parser("profiles", help="List available agent adapters")
    profiles.add_argument("--config")
    profiles.set_defaults(func=command_profiles)

    routes = subparsers.add_parser("routes", help="List built-in coordinator/executor routing policies")
    routes.set_defaults(func=command_routes)

    validate_task = subparsers.add_parser("validate-task", help="Validate a detailed worker task contract")
    validate_task.add_argument("--task-file", required=True)
    validate_task.set_defaults(func=command_validate_task)

    catalog = subparsers.add_parser("catalog", help="Discover installed CLIs, models, and internal agents")
    catalog.add_argument("--cli", default="all")
    catalog.add_argument("--workspace", default=os.getcwd())
    catalog.add_argument("--query")
    catalog.add_argument("--full", action="store_true")
    catalog.add_argument("--timeout-seconds", type=float, default=15)
    catalog.add_argument("--no-discovery", action="store_true")
    catalog.add_argument("--config")
    catalog.add_argument("--json", action="store_true")
    catalog.set_defaults(func=command_catalog)

    doctor = subparsers.add_parser("doctor", help="Check agent executables")
    doctor.add_argument("--cli", "--agent", dest="cli", default="all")
    doctor.add_argument("--config")
    doctor.set_defaults(func=command_doctor)

    create_plan = subparsers.add_parser("create-plan", help="Create a durable plan and checklist ledger")
    create_plan.add_argument("--plan-file", required=True)
    create_plan.add_argument("--workspace", default=os.getcwd())
    create_plan.add_argument("--title", required=True)
    create_plan.add_argument("--plan-id")
    create_plan.add_argument("--json", action="store_true")
    create_plan.set_defaults(func=command_create_plan)

    plan_status = subparsers.add_parser("plan-status", help="Show durable plan decisions and checklist state")
    plan_status.add_argument("plan_id")
    plan_status.add_argument("--json", action="store_true")
    plan_status.set_defaults(func=command_plan_status)

    launch = subparsers.add_parser("launch", help="Launch a detached agent job")
    launch.add_argument("--cli", "--agent", dest="cli")
    launch.add_argument("--route", choices=tuple(sorted(BUILTIN_ROUTES)))
    launch.add_argument("--coordinator-model")
    launch.add_argument("--workspace", default=os.getcwd())
    launch.add_argument("--task-file", required=True)
    launch.add_argument("--job-id")
    launch.add_argument("--config")
    launch.add_argument("--model")
    launch.add_argument(
        "--reasoning-effort",
        choices=("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"),
    )
    launch.add_argument("--cli-agent")
    launch.add_argument("--group")
    launch.add_argument("--role")
    launch.add_argument("--depends-on", action="append", default=[])
    launch.add_argument("--allow-path", action="append", default=[])
    launch.add_argument("--deny-path", action="append", default=[])
    launch.add_argument("--max-changed-files", type=int)
    launch.add_argument("--max-turns", type=int)
    launch.add_argument("--max-cost-usd", type=float)
    launch.add_argument("--plan-id")
    launch.add_argument("--checklist-item")
    launch.add_argument("--timeout-seconds", type=int, default=3600)
    launch.add_argument("--allow-dirty", action="store_true")
    launch.add_argument("--allow-unstructured-task", action="store_true")
    launch.add_argument("--allow-concurrent-workspace", action="store_true")
    launch.add_argument("--allow-large-context", action="store_true")
    notify = launch.add_mutually_exclusive_group()
    notify.add_argument("--notify", dest="notify", action="store_true")
    notify.add_argument("--no-notify", dest="notify", action="store_false")
    launch.set_defaults(notify=True)
    launch.add_argument("--json", action="store_true")
    launch.set_defaults(func=command_launch)

    status = subparsers.add_parser("status", help="Read durable job status")
    status.add_argument("job_id")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=command_status)

    wait = subparsers.add_parser("wait", help="Wait briefly for a job")
    wait.add_argument("job_id")
    wait.add_argument("--timeout-seconds", type=float, default=45)
    wait.add_argument("--poll-seconds", type=float, default=5)
    wait.add_argument("--json", action="store_true")
    wait.set_defaults(func=command_wait)

    logs = subparsers.add_parser("logs", help="Show the tail of job logs")
    logs.add_argument("job_id")
    logs.add_argument("--stream", choices=("stdout", "stderr", "both"), default="both")
    logs.add_argument("--tail", type=int, default=120)
    logs.set_defaults(func=command_logs)

    event = subparsers.add_parser("event", help="Publish a worker progress event")
    event.add_argument("job_id")
    event.add_argument("--phase", required=True)
    event.add_argument("--message", required=True)
    event.set_defaults(func=command_event)

    ask = subparsers.add_parser("ask", help="Ask Codex a blocking question")
    ask.add_argument("job_id")
    ask.add_argument("--question")
    ask.add_argument("--question-file")
    ask.add_argument("--wait-seconds", type=float, default=1800)
    ask.set_defaults(func=command_ask)

    questions = subparsers.add_parser("questions", help="List worker questions")
    questions.add_argument("job_id")
    questions.add_argument("--all", action="store_true")
    questions.add_argument("--json", action="store_true")
    questions.set_defaults(func=command_questions)

    answer = subparsers.add_parser("answer", help="Answer a pending worker question")
    answer.add_argument("job_id")
    answer.add_argument("question_id")
    answer.add_argument("--text")
    answer.add_argument("--file")
    answer.add_argument("--json", action="store_true")
    answer.set_defaults(func=command_answer)

    review = subparsers.add_parser("record-review", help="Record an independent review verdict and evidence")
    review.add_argument("job_id")
    review.add_argument("--verdict", choices=("accepted", "repair_required", "rejected"), required=True)
    review.add_argument("--reviewer", required=True)
    review.add_argument("--test", action="append", default=[])
    review.add_argument("--notes")
    review.add_argument("--notes-file")
    review.add_argument("--json", action="store_true")
    review.set_defaults(func=command_record_review)

    observe = subparsers.add_parser("observe", help="Capture live process, log, event, and diff state")
    observe.add_argument("job_id")
    observe.add_argument("--tail", type=int, default=40)
    observe.add_argument("--event-limit", type=int, default=20)
    observe.add_argument("--json", action="store_true")
    observe.set_defaults(func=command_observe)

    watch = subparsers.add_parser("watch", help="Stream job state and logs until attention or completion")
    watch.add_argument("job_id")
    watch.add_argument("--interval", type=float, default=2)
    watch.set_defaults(func=command_watch)

    cancel = subparsers.add_parser("cancel", help="Request graceful job cancellation")
    cancel.add_argument("job_id")
    cancel.add_argument("--force", action="store_true")
    cancel.add_argument("--json", action="store_true")
    cancel.set_defaults(func=command_cancel)

    list_parser = subparsers.add_parser("list", help="List known jobs")
    list_parser.add_argument("--json", action="store_true")
    list_parser.set_defaults(func=command_list)

    dashboard = subparsers.add_parser("dashboard", help="Show all worker roles, selections, and live states")
    dashboard.add_argument("--group")
    dashboard.add_argument("--limit", type=int, default=20)
    dashboard.add_argument("--watch", action="store_true")
    dashboard.add_argument("--interval", type=float, default=2)
    dashboard.add_argument("--json", action="store_true")
    dashboard.set_defaults(func=command_dashboard)

    worker = subparsers.add_parser("_worker", help=argparse.SUPPRESS)
    worker.add_argument("--job-dir", required=True)
    worker.set_defaults(func=command_worker)
    return parser


def validate_cli_args(args: argparse.Namespace) -> None:
    if getattr(args, "timeout_seconds", 1) <= 0:
        raise RunnerError("timeout-seconds must be greater than zero")
    if getattr(args, "poll_seconds", 1) < 0.1:
        raise RunnerError("poll-seconds must be at least 0.1")
    if getattr(args, "max_turns", None) is not None and args.max_turns <= 0:
        raise RunnerError("max-turns must be greater than zero")
    if getattr(args, "max_cost_usd", None) is not None and args.max_cost_usd <= 0:
        raise RunnerError("max-cost-usd must be greater than zero")
    if getattr(args, "tail", 1) <= 0:
        raise RunnerError("tail must be greater than zero")
    if getattr(args, "wait_seconds", 1) <= 0:
        raise RunnerError("wait-seconds must be greater than zero")
    if getattr(args, "interval", 1) < 0.2:
        raise RunnerError("interval must be at least 0.2")
    if getattr(args, "event_limit", 1) <= 0:
        raise RunnerError("event-limit must be greater than zero")
    if getattr(args, "limit", 1) <= 0:
        raise RunnerError("limit must be greater than zero")
    if getattr(args, "max_changed_files", None) is not None and args.max_changed_files <= 0:
        raise RunnerError("max-changed-files must be greater than zero")
    for field in ("group", "role", "title", "reviewer"):
        value = getattr(args, field, None)
        if value and (len(value) > 80 or any(ord(character) < 32 for character in value)):
            raise RunnerError(f"{field} must be at most 80 printable characters")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        validate_cli_args(args)
        return args.func(args)
    except RunnerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except subprocess.TimeoutExpired as exc:
        print(f"error: command timed out: {exc.cmd}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
