#!/usr/bin/env python3
"""Detached, auditable job runner for local coding-agent CLIs."""

from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import fcntl
from contextlib import contextmanager
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
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any


MAX_TASK_BYTES = 1_000_000
QUALITY_FIRST_CONTEXT_BYTES = 65_536
TERRA_FALLBACK_CONTEXT_BYTES = 32_768
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
    "channel_dir",
    "prompt_file",
    "prompt_text",
    "model",
    "cli_agent",
    "max_turns",
    "max_cost_usd",
    "reasoning_effort",
}
EXPENSIVE_EXECUTOR_MARKERS = ("astra", "fable", "opus")
DEFAULT_EXECUTOR_LABEL = "<configured-default>"
STRATEGIES = ("cost-first", "quality-first", "maximum-quality")
RISKS = ("low", "medium", "high")
WORKSPACE_MODES = ("worktree", "project")
TASK_TYPES = (
    "general",
    "frontend",
    "backend",
    "debugging",
    "tests",
    "security",
    "migration",
    "performance",
    "documentation",
)
MODEL_EVIDENCE_PATH = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "orchestrate-cli-agents"
    / "references"
    / "model-evidence.json"
)

AUTO_EXECUTOR_CANDIDATES: dict[str, list[dict[str, Any]]] = {
    "cost-first": [
        {
            "cli": "codex-cli",
            "model": "gpt-5.6-terra",
            "effort": {"low": "medium", "medium": "high", "high": "xhigh"},
        },
        {
            "cli": "codex-cli",
            "model": "gpt-5.6-luna",
            "effort": {"low": "high", "medium": "high", "high": "xhigh"},
        },
    ],
    "quality-first": [
        {
            "cli": "codex-cli",
            "model": "gpt-5.6-sol",
            "effort": {"low": "medium", "medium": "high", "high": "xhigh"},
        },
        {
            "cli": "claude-code",
            "model": "opus",
            "effort": {"low": "medium", "medium": "high", "high": "high"},
        },
        {
            "cli": "codex-cli",
            "model": "gpt-5.6-terra",
            "effort": {"low": "high", "medium": "high", "high": "xhigh"},
        },
    ],
    "maximum-quality": [
        {
            "cli": "codex-cli",
            "model": "gpt-6-astra",
            "effort": {"low": "high", "medium": "xhigh", "high": "max"},
        },
        {
            "cli": "codex-cli",
            "model": "gpt-5.6-sol",
            "effort": {"low": "high", "medium": "high", "high": "xhigh"},
        },
        {
            "cli": "claude-code",
            "model": "opus",
            "effort": {"low": "high", "medium": "high", "high": "high"},
        },
    ],
}

BUILTIN_ROUTES: dict[str, dict[str, Any]] = {
    "cost-first": {
        "display_name": "Cost first",
        "description": (
            "Default automatic flow: preserve review gates while preferring balanced, bounded "
            "executors and minimizing total cost per accepted change."
        ),
        "coordinator": {
            "model": "current-codex-task",
            "recommended_task_model": "gpt-5.6-luna",
            "responsibility": "Planning, durable decisions, questions, compact checkpoints, and review.",
        },
        "executor": {
            "automatic_strategy": "cost-first",
            "recommended_cli": "codex-cli",
            "recommended_model": "gpt-5.6-terra",
            "session": "ephemeral",
        },
        "review": {
            "required": True,
            "policy": "Review every diff and rerun relevant tests.",
        },
        "context_budget_bytes": QUALITY_FIRST_CONTEXT_BYTES,
    },
    "quality-first": {
        "display_name": "Quality first",
        "description": (
            "Keep durable orchestration with a cost-efficient coordinator and use a fresh, "
            "high-capability Sol or Opus-class model for each bounded implementation job."
        ),
        "coordinator": {
            "model": "current-codex-task",
            "recommended_task_model": "gpt-5.6-luna",
            "responsibility": "Task decomposition, durable decisions, questions, and progress deltas.",
        },
        "executor": {
            "automatic_strategy": "quality-first",
            "recommended_cli": "codex-cli",
            "recommended_model": "gpt-5.6-sol",
            "recommended_reasoning_effort": "high",
            "cost_warning": "Quality-over-cost authorizes Sol/Opus execution, but not Astra.",
            "session": "ephemeral",
        },
        "review": {
            "required": True,
            "recommended_high_risk_model": "gpt-5.6-sol",
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
            "model": "current-codex-task",
            "recommended_task_model": "gpt-6-astra",
            "responsibility": "Architecture, decomposition, questions, progress, and review.",
        },
        "executor": {
            "requires_explicit_selection": True,
            "recommended_cli": "codex-cli",
            "recommended_model": "gpt-5.6-luna",
            "recommended_reasoning_effort": "high",
            "session": "ephemeral",
        },
        "review": {
            "required": True,
            "recommended_high_risk_model": "gpt-6-astra",
            "requires_explicit_selection": True,
            "policy": "Coordinator reviews the diff and reruns tests before acceptance.",
        },
        "context_budget_bytes": QUALITY_FIRST_CONTEXT_BYTES,
    },
    "maximum-quality": {
        "display_name": "Maximum quality",
        "description": "Explicit frontier-quality flow; may automatically use Astra, Sol, and Opus.",
        "coordinator": {
            "model": "current-codex-task",
            "recommended_task_model": "gpt-6-astra",
            "responsibility": "Architecture, durable planning, questions, compact checkpoints, and review.",
        },
        "executor": {
            "automatic_strategy": "maximum-quality",
            "recommended_cli": "codex-cli",
            "recommended_model": "gpt-6-astra",
            "recommended_reasoning_effort": "xhigh",
            "cost_warning": "This route is explicit authorization for frontier executor cost.",
            "session": "ephemeral",
        },
        "review": {
            "required": True,
            "policy": "Independent diff review and full risk-based validation.",
        },
        "context_budget_bytes": QUALITY_FIRST_CONTEXT_BYTES,
    },
}

BUILTIN_PROFILES: dict[str, dict[str, Any]] = {
    "antigravity": {
        "display_name": "Google Antigravity CLI",
        "description": "Run a pinned Antigravity model and agent in sandboxed headless mode.",
        "maturity": "stable",
        "docs_url": "https://antigravity.google/docs/cli/headless/",
        "install_hint": "Install and authenticate Antigravity CLI, then ensure `agy` is on PATH.",
        "argv": [
            "agy",
            "--print",
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--sandbox",
            "--print-timeout",
            "120m",
        ],
        "executable_candidates": ["agy", "antigravity"],
        "prompt_transport": "jsonl-stdin",
        "model_args": ["--model", "{model}"],
        "cli_agent_args": ["--agent", "{cli_agent}"],
        "reasoning_effort_args": ["--effort", "{reasoning_effort}"],
        "discover_models_argv": ["agy", "models"],
        "discover_cli_agents_argv": ["agy", "agents"],
    },
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
        "models": ["sonnet", "opus", "fable"],
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
        "models": ["sonnet", "opus", "fable"],
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
            "--add-dir",
            "{channel_dir}",
            "--color",
            "never",
            "--ephemeral",
            "--json",
            "-",
        ],
        "prompt_transport": "stdin",
        "model_args": ["--model", "{model}"],
        "models": ["gpt-6-astra", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"],
        "discover_models_argv": ["codex", "debug", "models"],
        "reasoning_effort_args": [
            "--config",
            'model_reasoning_effort="{reasoning_effort}"',
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
    override = os.environ.get("AGENT_ORCHESTRATOR_HOME") or os.environ.get(
        "CLI_AGENT_ORCHESTRATOR_HOME"
    )
    return (
        Path(override).expanduser().resolve()
        if override
        else Path.home() / ".codex" / "agent-orchestrator"
    )


def config_path(value: str | None) -> Path:
    if value:
        return Path(value).expanduser().resolve()
    return Path.home() / ".config" / "agent-orchestrator" / "agents.json"


def preferences_path() -> Path:
    return state_home() / "preferences.json"


def load_preferences() -> dict[str, str]:
    path = preferences_path()
    if not path.is_file():
        return {"workspace_mode": "worktree"}
    value = read_json(path)
    mode = value.get("workspace_mode")
    if mode not in WORKSPACE_MODES:
        raise RunnerError("preferences.json has an invalid workspace_mode")
    return {"workspace_mode": mode}


def save_preferences(preferences: dict[str, str]) -> None:
    path = preferences_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    write_json(path, preferences)
    os.chmod(path, 0o600)


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
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(value, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def add_event(path: Path, event_type: str, **details: Any) -> dict[str, Any]:
    event = {
        "id": f"{time.time_ns()}-{secrets.token_hex(3)}",
        "at": utc_now(),
        "type": event_type,
        **details,
    }
    write_json(path / "events" / f"{event['id']}.json", event)
    return event


def channel_dir(path: Path) -> Path:
    """Legacy jobs keep their original question location; new jobs use a narrow grant."""
    return path / "channel" if (path / "channel").is_dir() else path


def local_record_files(directory: Path) -> list[Path]:
    # Worker-authored records must not redirect observations or answer writes through symlinks.
    if directory.is_symlink() or directory.parent.is_symlink():
        return []
    return [
        item
        for item in directory.glob("*.json")
        if not item.is_symlink() and item.is_file()
    ]


def read_events(path: Path, limit: int = 20) -> list[dict[str, Any]]:
    files = local_record_files(path / "events")
    if channel_dir(path) != path:
        files.extend(local_record_files(channel_dir(path) / "events"))
    events = []
    for event_path in sorted(files, key=lambda item: item.name)[-limit:]:
        try:
            event = read_json(event_path)
            event["source"] = (
                "worker"
                if (
                    event_path.parent.parent != path
                    or event.get("type")
                    in {"worker_progress", "question_asked", "question_expired"}
                )
                else "runner"
            )
            events.append(event)
        except RunnerError:
            continue
    return events


def question_files(path: Path) -> list[Path]:
    files = local_record_files(path / "questions")
    if channel_dir(path) != path:
        files.extend(local_record_files(channel_dir(path) / "questions"))
    return sorted(files, key=lambda item: item.name)


def read_questions(path: Path) -> list[dict[str, Any]]:
    questions = []
    for question_path in question_files(path):
        try:
            question = read_json(question_path)
            if question.get("id") == question_path.stem and JOB_ID_RE.fullmatch(
                question_path.stem
            ):
                questions.append(question)
        except RunnerError:
            continue
    return questions


def pending_questions(path: Path) -> list[dict[str, Any]]:
    return [
        question
        for question in read_questions(path)
        if question.get("state") == "pending"
    ]


@contextmanager
def record_lock(path: Path):
    """Serialize CLI and threaded HTTP transitions, including worker expiry."""
    lock_path = path.with_suffix(".lock")
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def validate_identifier(value: str, label: str) -> str:
    if not isinstance(value, str) or not JOB_ID_RE.fullmatch(value):
        raise RunnerError(f"Invalid {label} ID: {value!r}")
    return value


def validate_answer(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RunnerError("Answer cannot be empty")
    if len(value.encode("utf-8")) > MAX_TASK_BYTES:
        raise RunnerError(f"Answer exceeds {MAX_TASK_BYTES} bytes")
    return value.strip()


def answer_record(
    record_path: Path, value: str, audit_path: Path, event_type: str, **details: Any
) -> dict[str, Any]:
    value = validate_answer(value)
    with record_lock(record_path):
        record = read_json(record_path)
        if record.get("state") != "pending":
            raise RunnerError(
                f"Question is not pending; current state is {record.get('state')}"
            )
        record.update(state="answered", answer=value, answered_at=utc_now())
        write_json(record_path, record)
        add_event(audit_path, event_type, **details)
    return record


def worker_question_path(path: Path, question_id: str) -> Path:
    validate_identifier(question_id, "question")
    matches = [item for item in question_files(path) if item.stem == question_id]
    if len(matches) != 1:
        raise RunnerError(f"Unknown or ambiguous question ID: {question_id}")
    return matches[0]


def answer_worker_question(job_id: str, question_id: str, value: str) -> dict[str, Any]:
    path = job_dir(job_id)
    question_path = worker_question_path(path, question_id)
    if read_json(question_path).get("id") != question_id:
        raise RunnerError("Question ID does not match its record")
    answer_record(
        question_path, value, path, "question_answered", question_id=question_id
    )
    return {"job_id": job_id, "state": "answered", "question_id": question_id}


def feedback_dir(feedback_id: str, must_exist: bool = True) -> Path:
    validate_identifier(feedback_id, "feedback")
    path = state_home() / "feedback" / feedback_id
    if must_exist and not (path / "feedback.json").is_file():
        raise RunnerError(f"Unknown feedback ID: {feedback_id}")
    return path


def read_feedback() -> list[dict[str, Any]]:
    records = []
    for path in sorted((state_home() / "feedback").glob("*/feedback.json")):
        try:
            record = read_json(path)
            if record.get("id") == path.parent.name and JOB_ID_RE.fullmatch(
                path.parent.name
            ):
                records.append(record)
        except RunnerError:
            continue
    return records


def create_feedback(
    workspace: Path,
    question: str,
    context: str | None = None,
    plan_id: str | None = None,
    checklist_item: str | None = None,
    feedback_id: str | None = None,
    notify: bool = True,
) -> dict[str, Any]:
    workspace = workspace.expanduser().resolve()
    if not workspace.is_dir():
        raise RunnerError("Workspace must be an existing directory")
    question = validate_answer(question)
    if context is not None and (
        not isinstance(context, str) or len(context.encode("utf-8")) > MAX_TASK_BYTES
    ):
        raise RunnerError("Context must be text of at most 1000000 bytes")
    if checklist_item and not plan_id:
        raise RunnerError("--checklist-item requires --plan-id")
    if plan_id:
        _, plan = read_plan(plan_id)
        if Path(plan["workspace"]).resolve() != workspace:
            raise RunnerError("Feedback workspace must match the linked plan")
        if checklist_item and not any(
            item.get("id") == checklist_item for item in plan.get("items", [])
        ):
            raise RunnerError("Unknown checklist item")
    selected_id = feedback_id or make_job_id("feedback")
    path = feedback_dir(selected_id, must_exist=False)
    # Private parents even when the configured state home has not been created yet.
    state_home().mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.mkdir(exist_ok=True, mode=0o700)
    try:
        path.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise RunnerError(f"Feedback already exists: {selected_id}") from exc
    record = {
        "id": selected_id,
        "state": "pending",
        "source": "orchestrator",
        "question": question,
        "context": context,
        "workspace": str(workspace),
        "project_name": workspace.name or "Workspace",
        "plan_id": plan_id,
        "checklist_item": checklist_item,
        "created_at": utc_now(),
    }
    write_json(path / "feedback.json", record)
    add_event(path, "feedback_requested", feedback_id=selected_id)
    if notify:
        notify_user("Agent Orchestrator", "The project orchestrator needs your input")
    return record


def answer_feedback(feedback_id: str, value: str) -> dict[str, Any]:
    path = feedback_dir(feedback_id)
    return answer_record(
        path / "feedback.json",
        value,
        path,
        "feedback_answered",
        feedback_id=feedback_id,
    )


def command_request_feedback(args: argparse.Namespace) -> int:
    record = create_feedback(
        Path(args.workspace),
        question_text(args),
        args.context,
        args.plan_id,
        args.checklist_item,
        args.feedback_id,
        args.notify,
    )
    print(json.dumps(record, indent=2, sort_keys=True))
    return 0


def command_feedback(args: argparse.Namespace) -> int:
    records = read_feedback()
    if args.workspace:
        workspace = Path(args.workspace).expanduser().resolve()
        records = [
            record
            for record in records
            if Path(record["workspace"]).resolve() == workspace
        ]
    if not args.all:
        records = [record for record in records if record.get("state") == "pending"]
    print(json.dumps(records, indent=2, sort_keys=True))
    return 0


def command_answer_feedback(args: argparse.Namespace) -> int:
    print(
        json.dumps(
            answer_feedback(args.feedback_id, answer_text(args)),
            indent=2,
            sort_keys=True,
        )
    )
    return 0


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
    path = path / "channel"
    path.mkdir(mode=0o700)
    wrapper = path / "channel.py"
    runner = str(Path(__file__).resolve())
    content = f"""#!/usr/bin/env python3
import os
import sys

RUNNER = {runner!r}
JOB_ID = {job_id!r}
os.environ["AGENT_ORCHESTRATOR_HOME"] = {str(state_home())!r}

if len(sys.argv) < 2 or sys.argv[1] not in {{"ask", "event"}}:
    raise SystemExit("usage: channel.py <ask|event> [arguments]")
os.execv(sys.executable, [sys.executable, RUNNER, sys.argv[1], JOB_ID, *sys.argv[2:]])
"""
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
        set(
            re.findall(r"(?m)(?:^|[`\s])([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.*/-]+)+)", text)
        )
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
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, str) for item in value)
    ):
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
        raise RunnerError(
            f"Profile {name!r} has unsupported fields: {', '.join(unknown_keys)}"
        )
    profile: dict[str, Any] = {
        "argv": validate_string_list(raw.get("argv"), f"{name}.argv"),
        "prompt_transport": raw.get("prompt_transport"),
    }
    if profile["prompt_transport"] not in {"file", "stdin", "jsonl-stdin", "arg"}:
        raise RunnerError(
            f"{name}.prompt_transport must be file, stdin, jsonl-stdin, or arg"
        )
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
    tokens = [
        token
        for key, values in profile.items()
        if key in command_keys
        for token in values
    ]
    placeholders = {
        match for token in tokens for match in PLACEHOLDER_RE.findall(token)
    }
    unknown_placeholders = sorted(placeholders - ALLOWED_PLACEHOLDERS)
    if unknown_placeholders:
        raise RunnerError(
            f"Profile {name!r} has unknown placeholders: {', '.join(unknown_placeholders)}"
        )
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
    profiles = {
        name: validate_profile(name, raw) for name, raw in BUILTIN_PROFILES.items()
    }
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
    args.coordinator_model = args.coordinator_model or "current-codex-task"
    if not args.cli:
        raise RunnerError(
            "Choose --cli explicitly. Routes are recommendations and never select an executor."
        )
    return route


def is_expensive_executor_model(model: str | None) -> bool:
    if not model:
        return False
    normalized = model.casefold()
    return any(marker in normalized for marker in EXPENSIVE_EXECUTOR_MARKERS)


def parse_executor_spec(value: str, expensive_approved: bool = False) -> dict[str, Any]:
    raw = value.strip()
    if not raw:
        raise RunnerError("Executor selection cannot be empty")
    if "=" in raw:
        cli, model = raw.split("=", 1)
        cli = cli.strip()
        model = model.strip()
        if not model:
            raise RunnerError(f"Executor {value!r} has an empty model after '='")
    else:
        cli, model = raw, None
    if not AGENT_NAME_RE.fullmatch(cli):
        raise RunnerError(f"Invalid executor CLI in {value!r}")
    if model is not None and (
        len(model) > 256 or any(ord(char) < 32 for char in model)
    ):
        raise RunnerError(f"Invalid executor model in {value!r}")
    if is_expensive_executor_model(model) and not expensive_approved:
        raise RunnerError(
            f"Executor {cli}={model} is a protected expensive/frontier selection. "
            f"Use --expensive-executor {cli}={model} only after the user explicitly accepts its cost."
        )
    return {
        "cli": cli,
        "model": model,
        "display": (
            f"{cli}={model}" if model is not None else f"{cli}={DEFAULT_EXECUTOR_LABEL}"
        ),
        "expensive_user_approved": bool(expensive_approved),
    }


def executor_policy(
    executor_values: list[str],
    expensive_executor_values: list[str],
    terra_fallback_after_seconds: int | None = None,
) -> dict[str, Any]:
    if (
        terra_fallback_after_seconds is not None
        and not 60 <= terra_fallback_after_seconds <= 86_400
    ):
        raise RunnerError(
            "Terra fallback grace period must be between 60 and 86400 seconds"
        )
    entries = [parse_executor_spec(value) for value in executor_values]
    entries.extend(
        parse_executor_spec(value, expensive_approved=True)
        for value in expensive_executor_values
    )
    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, str | None]] = set()
    for entry in entries:
        key = (entry["cli"], entry["model"])
        if key in seen:
            raise RunnerError(f"Duplicate executor selection: {entry['display']}")
        seen.add(key)
        unique.append(entry)
    if terra_fallback_after_seconds is not None and not unique:
        raise RunnerError(
            "Terra fallback requires at least one primary executor to exhaust first"
        )
    fallback = {
        "enabled": terra_fallback_after_seconds is not None,
        "cli": "codex-cli",
        "model": "gpt-5.6-terra",
        "reasoning_effort": "high",
        "after_seconds": terra_fallback_after_seconds,
        "requires_pending_feedback": True,
        "requires_exhausted_pool": True,
        "max_context_bytes": TERRA_FALLBACK_CONTEXT_BYTES,
        "user_preapproved": terra_fallback_after_seconds is not None,
    }
    return {
        "mode": "allowlist_only",
        "allowed": unique,
        "on_exhausted": (
            "request_user_then_terra_after_grace"
            if terra_fallback_after_seconds is not None
            else "request_user_approval"
        ),
        "automatic_frontier_fallback": False,
        "fallback": fallback,
        "updated_at": utc_now(),
    }


def load_model_evidence() -> dict[str, Any]:
    evidence = read_json(MODEL_EVIDENCE_PATH)
    if evidence.get("schema_version") != 1:
        raise RunnerError("Unsupported model evidence schema")
    if not isinstance(evidence.get("sources"), list) or not isinstance(
        evidence.get("priors"), list
    ):
        raise RunnerError("Model evidence requires sources and priors arrays")
    return evidence


def evidence_prior(model: str | None, evidence: dict[str, Any]) -> dict[str, Any]:
    normalized = (model or DEFAULT_EXECUTOR_LABEL).casefold()
    for prior in evidence["priors"]:
        markers = prior.get("contains", [])
        if isinstance(markers, list) and any(
            isinstance(marker, str) and marker.casefold() in normalized
            for marker in markers
        ):
            return prior
    return {"capability_tier": 1, "strengths": ["general"], "sources": []}


def inferred_task_type(status: dict[str, Any]) -> str:
    explicit = status.get("task_type")
    if explicit in TASK_TYPES:
        return explicit
    text = " ".join(
        str(status.get(key) or "")
        for key in ("role", "checklist_item", "current_phase")
    ).casefold()
    keywords = {
        "security": ("security", "auth", "permission", "credential", "vulnerability"),
        "performance": ("performance", "latency", "optimizer", "benchmark", "profil"),
        "migration": ("migration", "migrate", "upgrade", "port"),
        "debugging": ("debug", "diagnos", "repair", "fix", "regression"),
        "tests": ("test", "coverage", "fixture", "reviewer", "audit"),
        "frontend": ("frontend", "dashboard", "ui", "css", "react", "swiftui"),
        "backend": ("backend", "api", "database", "worker", "server", "runtime"),
        "documentation": ("docs", "documentation", "readme", "article"),
    }
    for task_type, markers in keywords.items():
        if any(marker in text for marker in markers):
            return task_type
    return "general"


def local_executor_outcomes(
    cli: str, model: str | None, task_type: str, since_days: float = 90
) -> dict[str, Any]:
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=since_days)
    matching: list[dict[str, Any]] = []
    overall: list[dict[str, Any]] = []
    for path in jobs_dir().glob("*"):
        if not path.is_dir() or not (path / "meta.json").is_file():
            continue
        try:
            status = get_status(path)
            created = dt.datetime.fromisoformat(status["created_at"])
        except (RunnerError, KeyError, TypeError, ValueError):
            continue
        if created < cutoff or status.get("cli") != cli or status.get("model") != model:
            continue
        overall.append(status)
        if task_type == "general" or inferred_task_type(status) == task_type:
            matching.append(status)

    def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
        reviewed_states = {"accepted", "repair_required", "rejected"}
        failure_states = {"failed", "timed_out", "scope_violated", "lost"}
        reviewed = [
            item for item in records if item.get("acceptance_state") in reviewed_states
        ]
        accepted = sum(item.get("acceptance_state") == "accepted" for item in reviewed)
        failures = sum(
            item.get("execution_state") in failure_states for item in records
        )
        return {
            "attempts": len(records),
            "reviewed": len(reviewed),
            "accepted": accepted,
            "reviewed_acceptance_rate": (
                round(accepted / len(reviewed), 4) if reviewed else None
            ),
            "execution_failure_rate": (
                round(failures / len(records), 4) if records else None
            ),
        }

    return {"task": summarize(matching), "overall": summarize(overall)}


def rank_executor_candidates(
    candidates: list[dict[str, Any]], strategy: str, risk: str, task_type: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    evidence = load_model_evidence()
    ranked: list[dict[str, Any]] = []
    seen: set[tuple[str, str | None]] = set()
    for position, candidate in enumerate(candidates):
        key = (candidate["cli"], candidate.get("model"))
        if key in seen:
            continue
        seen.add(key)
        model = candidate.get("model")
        if "astra" in (model or "").casefold() and strategy != "maximum-quality":
            continue
        if is_expensive_executor_model(model) and strategy == "cost-first":
            continue
        prior = evidence_prior(model, evidence)
        outcomes = local_executor_outcomes(candidate["cli"], model, task_type)
        score = 100.0 - position * 8 + float(prior.get("capability_tier", 1)) * 2
        strengths = prior.get("strengths", [])
        if task_type in strengths:
            score += 4
        local_source = "public_prior_only"
        local = outcomes["task"]
        if local["reviewed"] >= 3:
            score += (local["reviewed_acceptance_rate"] - 0.5) * 30
            local_source = "task_specific_reviewed_history"
        elif outcomes["overall"]["reviewed"] >= 5:
            local = outcomes["overall"]
            score += (local["reviewed_acceptance_rate"] - 0.5) * 15
            local_source = "overall_reviewed_history"
        failure_rate = local.get("execution_failure_rate")
        if failure_rate is not None:
            score -= failure_rate * 12
        entry = dict(candidate)
        entry.update(
            benchmark_sources=prior.get("sources", []),
            capability_tier=prior.get("capability_tier", 1),
            local_evidence=outcomes,
            evidence_basis=local_source,
            routing_score=round(score, 3),
            task_type=task_type,
            risk=risk,
        )
        ranked.append(entry)
    ranked.sort(
        key=lambda item: (-item["routing_score"], item["cli"], item.get("model") or "")
    )
    for index, entry in enumerate(ranked, 1):
        entry["rank"] = index
        entry["selection_reason"] = (
            f"Ranked for {task_type} / {risk} using {entry['evidence_basis']} plus "
            "versioned public capability evidence"
        )
    return ranked, {
        "as_of": evidence.get("as_of"),
        "methodology": evidence.get("methodology"),
        "sources": evidence.get("sources"),
        "minimum_task_specific_reviewed_jobs": 3,
        "minimum_overall_reviewed_jobs": 5,
    }


def automatic_executor_policy(
    strategy: str,
    risk: str,
    profiles: dict[str, dict[str, Any]],
    task_type: str = "general",
) -> dict[str, Any]:
    """Resolve a bounded executor pool from explicit orchestration intent and installed CLIs."""
    if strategy not in STRATEGIES:
        raise RunnerError(f"Unknown strategy: {strategy}")
    if risk not in RISKS:
        raise RunnerError(f"Unknown risk: {risk}")
    if task_type not in TASK_TYPES:
        raise RunnerError(f"Unknown task type: {task_type}")
    authorization_source = {
        "cost-first": "default_cost_first_policy",
        "quality-first": "user_requested_quality_over_cost",
        "maximum-quality": "user_requested_maximum_or_frontier_quality",
    }[strategy]
    candidates: list[dict[str, Any]] = []
    for candidate in AUTO_EXECUTOR_CANDIDATES[strategy]:
        profile = profiles.get(candidate["cli"])
        if not profile or not resolve_profile_executable(profile)[1]:
            continue
        model = candidate["model"]
        if "model_args" not in profile or model not in profile.get("models", []):
            continue
        if "astra" in model.casefold() and strategy != "maximum-quality":
            continue
        candidates.append(
            {
                "cli": candidate["cli"],
                "model": model,
                "display": f"{candidate['cli']}={model}",
                "reasoning_effort": candidate["effort"][risk],
                "auto_selected": True,
                "authorization_source": authorization_source,
                "expensive_user_approved": bool(
                    is_expensive_executor_model(model)
                    and strategy in {"quality-first", "maximum-quality"}
                ),
            }
        )
    allowed, routing_evidence = rank_executor_candidates(
        candidates, strategy, risk, task_type
    )
    if not allowed:
        raise RunnerError(
            f"No installed executor matches the automatic {strategy} policy. "
            "Run choices/catalog and provide an explicit executor pool."
        )
    return {
        "mode": "automatic_strategy_allowlist",
        "strategy": strategy,
        "risk": risk,
        "task_type": task_type,
        "routing_evidence": routing_evidence,
        "authorization_source": authorization_source,
        "allowed": allowed,
        "on_exhausted": "request_user_approval",
        "automatic_frontier_fallback": False,
        "fallback": {"enabled": False},
        "max_attempts_per_item": 3,
        "updated_at": utc_now(),
    }


def executor_selection_matches(
    entry: dict[str, Any], cli: str, model: str | None
) -> bool:
    return entry.get("cli") == cli and entry.get("model") == model


def selected_executor_approval(
    policy: dict[str, Any], cli: str, model: str | None
) -> dict[str, Any]:
    for entry in policy.get("allowed", []):
        if executor_selection_matches(entry, cli, model):
            return entry
    fallback = policy.get("fallback") or {}
    if fallback.get("enabled") and executor_selection_matches(fallback, cli, model):
        return {
            "cli": cli,
            "model": model,
            "display": f"{cli}={model or DEFAULT_EXECUTOR_LABEL}",
            "expensive_user_approved": False,
            "terra_fallback": True,
        }
    raise RunnerError("Resolved executor has no matching approval record")


def validate_executor_selection(
    args: argparse.Namespace,
    profile: dict[str, Any],
    plan: dict[str, Any] | None,
) -> dict[str, Any]:
    if "model_args" in profile and not args.model:
        raise RunnerError(
            f"CLI {args.cli} supports model selection; choose --model explicitly so its configured "
            "default cannot silently select an expensive executor"
        )
    if "model_args" not in profile and args.model:
        raise RunnerError(
            f"Selected CLI {args.cli} does not support explicit model selection"
        )

    if plan is not None:
        if args.approved_executor or args.approved_expensive_executor:
            raise RunnerError(
                "A plan's durable executor pool is authoritative. Use set-executors to change it; "
                "one-off launch flags cannot widen the pool."
            )
        policy = plan.get("executor_policy") or {}
    else:
        policy = executor_policy(
            args.approved_executor, args.approved_expensive_executor
        )

    allowed = policy.get("allowed", [])
    if not allowed:
        target = "plan" if plan is not None else "launch"
        raise RunnerError(
            f"No user-approved executors exist for this {target}. "
            "Set a bounded executor pool before launching; automatic fallback is disabled."
        )
    matching = [
        entry
        for entry in allowed
        if executor_selection_matches(entry, args.cli, args.model)
    ]
    fallback = policy.get("fallback") or {}
    using_terra_fallback = bool(
        getattr(args, "use_terra_fallback", False)
        and fallback.get("enabled")
        and executor_selection_matches(fallback, args.cli, args.model)
    )
    if not matching and not using_terra_fallback:
        approved = ", ".join(entry.get("display", "unknown") for entry in allowed)
        raise RunnerError(
            f"Executor {args.cli}={args.model or DEFAULT_EXECUTOR_LABEL} is not user-approved. "
            f"Allowed executors: {approved}. Update the pool only after asking the user."
        )
    entry = selected_executor_approval(policy, args.cli, args.model)
    if is_expensive_executor_model(args.model) and not entry.get(
        "expensive_user_approved"
    ):
        raise RunnerError(
            f"Executor {args.cli}={args.model} requires explicit expensive-executor approval"
        )
    return policy


def dashboard_runtime_path() -> Path:
    return state_home() / "dashboard.json"


def live_dashboard_runtime() -> dict[str, Any] | None:
    path = dashboard_runtime_path()
    if not path.is_file():
        return None
    try:
        runtime = read_json(path)
        if not pid_alive(runtime.get("pid")):
            return None
        origin = runtime.get("origin")
        if not isinstance(origin, str) or not origin.startswith("http://127.0.0.1:"):
            return None
        with urllib.request.urlopen(origin + "/", timeout=0.5) as response:
            if response.status != 200:
                return None
        return runtime
    except (RunnerError, OSError, ValueError, urllib.error.URLError):
        return None


def replace_placeholders(tokens: list[str], values: dict[str, str]) -> list[str]:
    result: list[str] = []
    for token in tokens:
        expanded = token
        for key, value in values.items():
            expanded = expanded.replace("{" + key + "}", value)
        unresolved = PLACEHOLDER_RE.findall(expanded)
        if unresolved:
            raise RunnerError(
                f"Missing values for placeholders: {', '.join(sorted(set(unresolved)))}"
            )
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
    channel_dir: Path | None = None,
) -> tuple[list[str], str]:
    prompt_text = prompt_file.read_text(encoding="utf-8")
    values = {
        "workspace": str(workspace),
        "channel_dir": str(channel_dir or prompt_file.parent / "channel"),
        "prompt_file": str(prompt_file),
        "prompt_text": prompt_text,
    }
    argv = list(profile["argv"])
    executable, found = resolve_profile_executable(profile)
    argv[0] = found or executable
    controls = (
        (model, "model_args", "model", str(model) if model is not None else ""),
        (
            cli_agent,
            "cli_agent_args",
            "cli_agent",
            str(cli_agent) if cli_agent is not None else "",
        ),
        (
            max_turns,
            "max_turns_args",
            "max_turns",
            str(max_turns) if max_turns is not None else "",
        ),
        (
            max_cost_usd,
            "cost_args",
            "max_cost_usd",
            str(max_cost_usd) if max_cost_usd is not None else "",
        ),
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
            raise RunnerError(
                f"Selected CLI does not support requested control: {value_key}"
            )
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
        [
            "git",
            "-C",
            str(workspace),
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        ],
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


def scope_violations(
    workspace: Path, baseline: dict[str, Any], scope: dict[str, Any]
) -> list[str]:
    if (
        not scope.get("allowed_paths")
        and not scope.get("denied_paths")
        and scope.get("max_changed_files") is None
    ):
        return []
    if not baseline.get("is_git"):
        return []
    current = git_snapshot(workspace)
    changed = status_paths(current.get("status", [])) - status_paths(
        baseline.get("status", [])
    )
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


def named_section_body(text: str, name: str) -> str:
    matches = list(re.finditer(r"(?m)^#{1,6}\s+(.+?)\s*$", text))
    for index, match in enumerate(matches):
        if match.group(1).strip().casefold() != name.casefold():
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        return text[match.end() : end].strip()
    return ""


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
    goal = plan.get("goal")
    if isinstance(goal, dict):
        goal["status"] = "complete" if plan["complete"] else "active"
        goal["updated_at"] = plan["updated_at"]
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
    selected_plan_id = (
        args.plan_id
        or f"plan-{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"
    )
    path = plan_dir(selected_plan_id, must_exist=False)
    if path.exists():
        raise RunnerError(f"Plan already exists: {selected_plan_id}")
    path.mkdir(parents=True, mode=0o700)
    plan_copy = path / "plan.md"
    shutil.copyfile(source, plan_copy)
    os.chmod(plan_copy, 0o600)
    if args.executor or args.expensive_executor:
        policy = executor_policy(
            args.executor, args.expensive_executor, args.terra_fallback_after_seconds
        )
        policy.update(
            strategy=args.strategy,
            risk=args.risk,
            authorization_source="explicit_executor_allowlist",
            max_attempts_per_item=3,
        )
    else:
        if args.terra_fallback_after_seconds is not None:
            raise RunnerError(
                "Automatic executor selection does not use a separate Terra fallback"
            )
        policy = automatic_executor_policy(
            args.strategy,
            args.risk,
            load_profiles(config_path(args.config)),
            args.task_type,
        )
    preferences = load_preferences()
    plan = {
        "plan_id": selected_plan_id,
        "title": args.title,
        "workspace": str(workspace),
        "created_at": utc_now(),
        "plan_file": str(plan_copy),
        "plan_sha256": hashlib.sha256(plan_copy.read_bytes()).hexdigest(),
        "workspace_mode": preferences["workspace_mode"],
        "goal": {
            "objective": named_section_body(text, "Goal"),
            "status": "active",
            "strategy": args.strategy,
            "risk": args.risk,
            "task_type": args.task_type,
            "created_at": utc_now(),
        },
        "executor_policy": policy,
        "items": items,
    }
    save_plan(path, plan)
    output = read_json(path / "plan.json")
    print(
        json.dumps(output, indent=2, sort_keys=True) if args.json else selected_plan_id
    )
    return 0


def command_preferences(args: argparse.Namespace) -> int:
    preferences = load_preferences()
    if args.workspace_mode:
        preferences["workspace_mode"] = args.workspace_mode
        save_preferences(preferences)
    if args.json:
        print(json.dumps(preferences, indent=2, sort_keys=True))
    else:
        print(f"workspace_mode: {preferences['workspace_mode']}")
    return 0


def command_plan_status(args: argparse.Namespace) -> int:
    _, plan = read_plan(args.plan_id)
    if args.json:
        print(json.dumps(plan, indent=2, sort_keys=True))
    else:
        print(f"plan_id: {plan['plan_id']}")
        print(f"title: {plan['title']}")
        print(f"complete: {plan.get('complete', False)}")
        policy = plan.get("executor_policy", {})
        approved = policy.get("allowed", [])
        print("executor_policy: allowlist_only")
        print(
            "approved_executors: "
            + (", ".join(item["display"] for item in approved) or "none")
        )
        print(
            f"automatic_frontier_fallback: {policy.get('automatic_frontier_fallback', False)}"
        )
        for item in plan.get("items", []):
            print(f"{item['id']}\t{item['state']}\t{item['title']}")
    return 0


USAGE_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
    "total_cost_usd",
    "cost_usd",
    "duration_ms",
)


def add_usage_totals(target: dict[str, float], usage: Any) -> bool:
    if not isinstance(usage, dict):
        return False
    found = False
    for key in USAGE_FIELDS:
        value = usage.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            target[key] = target.get(key, 0) + value
            found = True
    return found


def compact_job_status(status: dict[str, Any]) -> dict[str, Any]:
    return {
        key: status.get(key)
        for key in (
            "job_id",
            "checklist_item",
            "cli",
            "model",
            "reasoning_effort",
            "execution_state",
            "acceptance_state",
            "current_phase",
            "attention_required",
            "created_at",
            "finished_at",
        )
    }


def command_plan_checkpoint(args: argparse.Namespace) -> int:
    _, plan = read_plan(args.plan_id)
    jobs: list[dict[str, Any]] = []
    usage: dict[str, float] = {}
    for item in plan.get("items", []):
        for job_id in item.get("job_ids", []):
            try:
                status = get_status(job_dir(job_id))
            except RunnerError:
                continue
            jobs.append(compact_job_status(status))
            add_usage_totals(usage, status.get("usage_summary"))
    pending = [
        {
            "id": record.get("id"),
            "checklist_item": record.get("checklist_item"),
            "question": record.get("question"),
            "created_at": record.get("created_at"),
        }
        for record in read_feedback()
        if record.get("plan_id") == args.plan_id and record.get("state") == "pending"
    ]
    unfinished = [item for item in plan.get("items", []) if item.get("state") != "done"]
    output = {
        "plan_id": args.plan_id,
        "title": plan.get("title"),
        "workspace": plan.get("workspace"),
        "goal": plan.get("goal"),
        "complete": plan.get("complete", False),
        "counts": plan.get("counts", {}),
        "next_item": unfinished[0] if unfinished else None,
        "completed_items": [
            {"id": item.get("id"), "title": item.get("title")}
            for item in plan.get("items", [])
            if item.get("state") == "done"
        ],
        "active_or_unreviewed_jobs": [
            job
            for job in jobs
            if job.get("execution_state") in {"starting", "running"}
            or job.get("acceptance_state") == "awaiting_review"
            or job.get("attention_required")
        ],
        "pending_feedback": pending,
        "usage_summary": usage or None,
        "continuation_contract": (
            "Resume from this checkpoint and the named plan item; do not replay raw logs or the "
            "full coordinator transcript unless the checkpoint identifies missing evidence."
        ),
    }
    print(
        json.dumps(output, indent=2, sort_keys=True)
        if args.json
        else json.dumps(output, sort_keys=True)
    )
    return 0


def command_metrics(args: argparse.Namespace) -> int:
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=args.since_hours)
    workspace = Path(args.workspace).expanduser().resolve() if args.workspace else None
    groups: dict[tuple[str, str | None], dict[str, Any]] = {}
    total_usage: dict[str, float] = {}
    total_jobs = 0
    for path in jobs_dir().glob("*"):
        if not path.is_dir() or not (path / "meta.json").is_file():
            continue
        try:
            status = get_status(path)
            created = dt.datetime.fromisoformat(status["created_at"])
        except (RunnerError, KeyError, TypeError, ValueError):
            continue
        if created < cutoff:
            continue
        if workspace and Path(status.get("workspace", "")).resolve() != workspace:
            continue
        total_jobs += 1
        key = (
            status.get("cli") or status.get("agent") or "unknown",
            status.get("model"),
        )
        group = groups.setdefault(
            key,
            {
                "cli": key[0],
                "model": key[1],
                "jobs": 0,
                "execution_states": {},
                "acceptance_states": {},
                "task_types": {},
                "provider_usage_jobs": 0,
                "usage": {},
            },
        )
        group["jobs"] += 1
        observed_task_type = inferred_task_type(status)
        group["task_types"][observed_task_type] = (
            group["task_types"].get(observed_task_type, 0) + 1
        )
        for field, bucket in (
            ("execution_state", "execution_states"),
            ("acceptance_state", "acceptance_states"),
        ):
            value = status.get(field) or "unknown"
            group[bucket][value] = group[bucket].get(value, 0) + 1
        if add_usage_totals(group["usage"], status.get("usage_summary")):
            group["provider_usage_jobs"] += 1
            add_usage_totals(total_usage, status.get("usage_summary"))
    rows = []
    for group in groups.values():
        reviewed = sum(
            group["acceptance_states"].get(state, 0)
            for state in ("accepted", "repair_required", "rejected")
        )
        group["reviewed_acceptance_rate"] = (
            round(group["acceptance_states"].get("accepted", 0) / reviewed, 4)
            if reviewed
            else None
        )
        usage = group["usage"]
        if "input_tokens" in usage:
            usage["uncached_input_tokens"] = max(
                0, usage["input_tokens"] - usage.get("cached_input_tokens", 0)
            )
        rows.append(group)
    rows.sort(key=lambda row: (-row["jobs"], row["cli"], row.get("model") or ""))
    output = {
        "since_hours": args.since_hours,
        "workspace": str(workspace) if workspace else None,
        "jobs": total_jobs,
        "usage": total_usage or None,
        "by_executor": rows,
        "interpretation": (
            "Compare attempts, accepted outcomes, repair rates, and provider-reported usage; "
            "do not choose a route from token price alone."
        ),
    }
    print(
        json.dumps(output, indent=2, sort_keys=True)
        if args.json
        else json.dumps(output, sort_keys=True)
    )
    return 0


def command_recommend_executors(args: argparse.Namespace) -> int:
    profiles = load_profiles(config_path(args.config))
    candidates: list[dict[str, Any]] = []
    excluded: list[dict[str, str]] = []
    requested = [(value, False) for value in args.candidate] + [
        (value, True) for value in args.expensive_candidate
    ]
    if requested:
        for value, separately_approved in requested:
            raw_model = value.split("=", 1)[1].strip() if "=" in value else None
            normalized = (raw_model or "").casefold()
            if "astra" in normalized and args.strategy != "maximum-quality":
                raise RunnerError(
                    "Astra can be recommended for execution only with --strategy maximum-quality"
                )
            intent_approved = args.strategy in {"quality-first", "maximum-quality"}
            entry = parse_executor_spec(
                value, expensive_approved=separately_approved or intent_approved
            )
            profile = profiles.get(entry["cli"])
            if not profile:
                excluded.append(
                    {"candidate": entry["display"], "reason": "unknown_cli"}
                )
                continue
            if not resolve_profile_executable(profile)[1]:
                excluded.append(
                    {"candidate": entry["display"], "reason": "cli_not_installed"}
                )
                continue
            entry.update(
                reasoning_effort={"low": "medium", "medium": "high", "high": "xhigh"}[
                    args.risk
                ],
                auto_selected=True,
                authorization_source=(
                    "user_requested_maximum_or_frontier_quality"
                    if args.strategy == "maximum-quality"
                    else (
                        "user_requested_quality_over_cost"
                        if args.strategy == "quality-first"
                        else "default_cost_first_policy"
                    )
                ),
            )
            candidates.append(entry)
    else:
        for candidate in AUTO_EXECUTOR_CANDIDATES[args.strategy]:
            profile = profiles.get(candidate["cli"])
            if not profile or not resolve_profile_executable(profile)[1]:
                continue
            candidates.append(
                {
                    "cli": candidate["cli"],
                    "model": candidate["model"],
                    "display": f"{candidate['cli']}={candidate['model']}",
                    "reasoning_effort": candidate["effort"][args.risk],
                    "auto_selected": True,
                }
            )
    ranked, evidence = rank_executor_candidates(
        candidates, args.strategy, args.risk, args.task_type
    )
    if not ranked:
        raise RunnerError(
            "No installed, policy-eligible candidates remain. Run choices/catalog or provide "
            "a user-approved candidate list."
        )
    output = {
        "strategy": args.strategy,
        "risk": args.risk,
        "task_type": args.task_type,
        "recommended": ranked,
        "excluded": excluded,
        "evidence": evidence,
        "guardrail": (
            "Recommendations never widen the user's executor pool. Confirm discovered exact model "
            "IDs, lock the chosen entries into the plan, and never select Astra without explicit "
            "maximum-quality authorization."
        ),
    }
    print(
        json.dumps(output, indent=2, sort_keys=True)
        if args.json
        else "\n".join(entry["display"] for entry in ranked)
    )
    return 0


def command_set_executors(args: argparse.Namespace) -> int:
    path, plan = read_plan(args.plan_id)
    policy = executor_policy(
        args.executor, args.expensive_executor, args.terra_fallback_after_seconds
    )
    if not policy["allowed"]:
        raise RunnerError("Provide at least one --executor or --expensive-executor")
    previous = plan.get("executor_policy")
    if previous:
        plan.setdefault("executor_policy_history", []).append(previous)
    plan["executor_policy"] = policy
    save_plan(path, plan)
    output = {
        "plan_id": args.plan_id,
        "executor_policy": policy,
        "warning": (
            "Only this pool may execute plan items. If every entry fails or is unavailable, "
            "ask the user in chat and the dashboard before replacing the pool or using a "
            "pre-approved Terra fallback."
        ),
    }
    print(
        json.dumps(output, indent=2, sort_keys=True)
        if args.json
        else ", ".join(entry["display"] for entry in policy["allowed"])
    )
    return 0


def executor_options_for_item(
    plan: dict[str, Any], item: dict[str, Any]
) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    attempts_by_key: dict[tuple[str, str | None], list[dict[str, Any]]] = {}
    for job_id in item.get("job_ids", []):
        try:
            status = get_status(job_dir(job_id))
        except RunnerError:
            continue
        key = (status.get("cli"), status.get("model"))
        attempt = {
            "job_id": job_id,
            "cli": status.get("cli"),
            "model": status.get("model"),
            "execution_state": status.get("execution_state"),
            "acceptance_state": status.get("acceptance_state"),
        }
        attempts.append(attempt)
        attempts_by_key.setdefault(key, []).append(attempt)
    policy = plan.get("executor_policy") or {}
    allowed = policy.get("allowed", [])
    untried = [
        entry
        for entry in allowed
        if (entry.get("cli"), entry.get("model")) not in attempts_by_key
    ]
    terminal_execution_failures = {
        "failed",
        "timed_out",
        "cancelled",
        "scope_violated",
        "lost",
    }
    terminal_review_failures = {"repair_required", "rejected"}

    def is_terminal_failure(attempt: dict[str, Any]) -> bool:
        return attempt.get("execution_state") in terminal_execution_failures or (
            attempt.get("execution_state") == "succeeded"
            and attempt.get("acceptance_state") in terminal_review_failures
        )

    unresolved = [attempt for attempt in attempts if not is_terminal_failure(attempt)]
    exhausted_entries = []
    for entry in allowed:
        key = (entry.get("cli"), entry.get("model"))
        entry_attempts = attempts_by_key.get(key, [])
        if entry_attempts and all(
            is_terminal_failure(attempt) for attempt in entry_attempts
        ):
            exhausted_entries.append(entry)
    return {
        "plan_id": plan["plan_id"],
        "checklist_item": item["id"],
        "allowed": allowed,
        "attempts": attempts,
        "untried": untried,
        "unresolved_attempts": unresolved,
        "exhausted_entries": exhausted_entries,
        "exhausted": bool(allowed) and len(exhausted_entries) == len(allowed),
        "on_exhausted": policy.get("on_exhausted", "request_user_approval"),
        "automatic_frontier_fallback": False,
    }


def command_executor_options(args: argparse.Namespace) -> int:
    _, plan = read_plan(args.plan_id)
    matching = [
        item for item in plan.get("items", []) if item.get("id") == args.checklist_item
    ]
    if not matching:
        raise RunnerError(
            f"Unknown checklist item {args.checklist_item!r} in plan {args.plan_id}"
        )
    output = executor_options_for_item(plan, matching[0])
    print(
        json.dumps(output, indent=2, sort_keys=True)
        if args.json
        else json.dumps(output, sort_keys=True)
    )
    return 0


def validate_terra_fallback(
    args: argparse.Namespace,
    plan: dict[str, Any] | None,
    item: dict[str, Any] | None,
    workspace: Path,
    task_stats: dict[str, Any],
) -> dict[str, Any] | None:
    if not args.use_terra_fallback:
        return None
    if plan is None or item is None:
        raise RunnerError("Terra fallback requires a durable plan and checklist item")
    policy = plan.get("executor_policy") or {}
    fallback = policy.get("fallback") or {}
    if not fallback.get("enabled") or not fallback.get("user_preapproved"):
        raise RunnerError(
            "Terra fallback was not disclosed and approved when the executor pool was set"
        )
    if not executor_selection_matches(fallback, args.cli, args.model):
        raise RunnerError(
            f"Terra fallback must use {fallback.get('cli')}={fallback.get('model')}"
        )
    options = executor_options_for_item(plan, item)
    if options["untried"]:
        remaining = ", ".join(entry["display"] for entry in options["untried"])
        raise RunnerError(
            f"Approved executor pool is not exhausted; try these entries before Terra: {remaining}"
        )
    if options["unresolved_attempts"]:
        job_ids = ", ".join(
            attempt["job_id"] for attempt in options["unresolved_attempts"]
        )
        raise RunnerError(
            "Approved executor pool is not exhausted; these jobs are still active, awaiting "
            f"review, or accepted: {job_ids}"
        )
    if not options["exhausted"]:
        raise RunnerError("Approved executor pool is not exhausted")
    if not args.feedback_id:
        raise RunnerError(
            "Terra fallback requires --feedback-id for the unanswered chat/dashboard escalation"
        )
    path = feedback_dir(args.feedback_id)
    record = read_json(path / "feedback.json")
    if record.get("state") != "pending":
        raise RunnerError(
            f"Feedback {args.feedback_id} is {record.get('state')}; follow the user's answer instead"
        )
    if (
        record.get("plan_id") != plan["plan_id"]
        or record.get("checklist_item") != item["id"]
        or Path(record.get("workspace", "")).resolve() != workspace
    ):
        raise RunnerError(
            "Fallback feedback does not belong to this plan item and workspace"
        )
    try:
        created = dt.datetime.fromisoformat(record["created_at"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RunnerError("Fallback feedback has an invalid creation time") from exc
    age = (dt.datetime.now(dt.timezone.utc) - created).total_seconds()
    grace = fallback["after_seconds"]
    if age < grace:
        raise RunnerError(
            f"User feedback grace period has not elapsed; wait {int(grace - age) + 1} more seconds"
        )
    if task_stats["bytes"] > fallback["max_context_bytes"]:
        raise RunnerError(
            f"Terra fallback packet is {task_stats['bytes']} bytes; split it below "
            f"{fallback['max_context_bytes']} bytes to keep the fallback narrowly focused"
        )
    return record


def claim_feedback_for_fallback(feedback_id: str, job_id: str) -> None:
    path = feedback_dir(feedback_id)
    record_path = path / "feedback.json"
    with record_lock(record_path):
        record = read_json(record_path)
        if record.get("state") != "pending":
            raise RunnerError(
                f"Feedback {feedback_id} is no longer pending; follow the user's answer"
            )
        record.update(
            state="terra_fallback_started",
            fallback_job_id=job_id,
            fallback_started_at=utc_now(),
        )
        write_json(record_path, record)
        add_event(
            path, "terra_fallback_started", feedback_id=feedback_id, job_id=job_id
        )


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
    validate_identifier(job_id, "job")
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
                            if (
                                isinstance(child, (int, float))
                                and not isinstance(child, bool)
                                and (
                                    "token" in normalized
                                    or normalized
                                    in {"total_cost_usd", "cost_usd", "duration_ms"}
                                )
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
        state = (
            "cancelling"
            if (path / "cancel-requested").exists() and worker_alive
            else "running" if worker_alive else "lost"
        )
        status = {**meta, "state": state}
    review_path = path / "review.json"
    review = read_json(review_path) if review_path.exists() else None
    status["review"] = review
    if status["state"] == "succeeded":
        status["review_state"] = review.get("verdict") if review else "required"
        status["acceptance_state"] = (
            review.get("verdict") if review else "awaiting_review"
        )
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
            "executable_candidates": profile.get(
                "executable_candidates", [profile["argv"][0]]
            ),
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
            "executable_candidates": profile.get(
                "executable_candidates", [profile["argv"][0]]
            ),
            "path": found,
            "available": bool(found),
            "install_hint": profile.get("install_hint"),
            "docs_url": profile.get("docs_url"),
        }
        if found:
            version = subprocess.run(
                [found, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
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


def compact_model_discovery(
    output: str, query: str | None
) -> list[dict[str, Any]] | None:
    try:
        value = json.loads(output)
    except json.JSONDecodeError:
        return None
    models = value.get("models") if isinstance(value, dict) else value
    if not isinstance(models, list) or not all(
        isinstance(model, dict) for model in models
    ):
        return None
    choices: list[dict[str, Any]] = []
    for model in models:
        if model.get("visibility") not in (None, "list"):
            continue
        model_id = model.get("slug") or model.get("id") or model.get("model")
        if not isinstance(model_id, str):
            continue
        supported_efforts = model.get("supported_reasoning_levels", [])
        efforts = [
            item.get("effort")
            for item in supported_efforts
            if isinstance(item, dict) and isinstance(item.get("effort"), str)
        ]
        item = {
            "id": model_id,
            "display_name": model.get("display_name"),
            "description": model.get("description"),
            "default_reasoning_effort": model.get("default_reasoning_level"),
            "reasoning_efforts": efforts,
        }
        if (
            query
            and query.casefold() not in json.dumps(item, sort_keys=True).casefold()
        ):
            continue
        choices.append(item)
    return choices


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
        result.update(
            {"discovery": "unavailable", "error": f"Executable not found: {command[0]}"}
        )
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
    structured_models = (
        compact_model_discovery(output, query)
        if key == "discover_models_argv"
        else None
    )
    if structured_models is not None:
        result.update(
            {
                "discovery": "succeeded" if completed.returncode == 0 else "failed",
                "command": command,
                "exit_code": completed.returncode,
                "choices": structured_models if full else structured_models[:120],
            }
        )
        if len(structured_models) > 120 and not full:
            result["compact"] = True
            result["hint"] = (
                "Use --query <text> for exact matches or --full for every model."
            )
        return result
    if query:
        lowered = query.casefold()
        output = "\n".join(
            line for line in output.splitlines() if lowered in line.casefold()
        )
    elif not full and len(output.splitlines()) > 120:
        compact_lines = [
            line
            for line in output.splitlines()
            if line
            and (
                not line.startswith((" ", "\t")) or line.lstrip().startswith("aliases:")
            )
        ]
        output = "\n".join(compact_lines[:120])
        result["compact"] = True
        result["hint"] = (
            "Use --query <text> for exact matches or --full for unabridged output."
        )
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
            "executable_candidates": profile.get(
                "executable_candidates", [profile["argv"][0]]
            ),
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
                profile,
                "discover_models_argv",
                workspace,
                args.query,
                args.timeout_seconds,
                args.full,
            )
            item["cli_agents"] = discovery_result(
                profile,
                "discover_cli_agents_argv",
                workspace,
                args.query,
                args.timeout_seconds,
                args.full,
            )
        else:
            item["models"] = {
                "configured": profile.get("models", []),
                "discovery": "skipped",
            }
            item["cli_agents"] = {
                "configured": profile.get("cli_agents", []),
                "discovery": "skipped",
            }
        catalog[name] = item
    print(json.dumps(catalog, indent=2, sort_keys=True))
    return 0


def command_choices(args: argparse.Namespace) -> int:
    profiles = load_profiles(config_path(args.config))
    harnesses: list[dict[str, Any]] = []
    for name, profile in sorted(profiles.items()):
        if profile.get("maturity") == "legacy" and not args.include_unavailable:
            continue
        executable, found = resolve_profile_executable(profile)
        if not found and not args.include_unavailable:
            continue
        harnesses.append(
            {
                "cli": name,
                "display_name": profile.get("display_name", name),
                "available": bool(found),
                "executable": executable,
                "maturity": profile.get("maturity", "stable"),
                "configured_models": profile.get("models", []),
                "configured_cli_agents": profile.get("cli_agents", []),
                "supports": {
                    "model": "model_args" in profile,
                    "cli_agent": "cli_agent_args" in profile,
                    "reasoning_effort": "reasoning_effort_args" in profile,
                    "max_turns": "max_turns_args" in profile,
                    "max_cost_usd": "cost_args" in profile,
                },
                "install_hint": profile.get("install_hint") if not found else None,
            }
        )
    output = {
        "routes": BUILTIN_ROUTES,
        "harnesses": harnesses,
        "selection": {
            "default_strategy": "cost-first",
            "automatic_plan_routing": True,
            "quality_first_authorizes": ["gpt-5.6-sol", "opus"],
            "quality_first_excludes": ["gpt-6-astra"],
            "maximum_quality_required_for_automatic_astra": True,
            "executor_allowlist_required": True,
            "automatic_frontier_fallback": False,
            "custom_fields": [
                "coordinator_model",
                "cli",
                "model",
                "cli_agent",
                "reasoning_effort",
            ],
            "next_step": (
                "Create a plan with the inferred strategy/risk for automatic installed-model "
                "routing, or use catalog and an explicit pool when the user names executors."
            ),
        },
    }
    if args.json:
        print(json.dumps(output, indent=2, sort_keys=True))
        return 0
    print("ROUTES")
    for name, route in sorted(BUILTIN_ROUTES.items()):
        executor = route["executor"]
        print(
            f"{name}\t{route['coordinator']['model']} -> "
            f"recommended {executor['recommended_cli']} / {executor['recommended_model']} "
            "(plan strategy or explicit pool required)"
        )
    print("\nAVAILABLE HARNESSES")
    for item in harnesses:
        controls = (
            ",".join(key for key, supported in item["supports"].items() if supported)
            or "defaults"
        )
        availability = "installed" if item["available"] else "unavailable"
        print(f"{item['cli']}\t{availability}\t{item['maturity']}\t{controls}")
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
        if status.get("workspace") == str(workspace) and status.get(
            "execution_state"
        ) in {
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
    if (
        route
        and task_stats["bytes"] > route["context_budget_bytes"]
        and not args.allow_large_context
    ):
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
    plan: dict[str, Any] | None = None
    plan_item: dict[str, Any] | None = None
    if args.plan_id:
        _, plan = read_plan(args.plan_id)
        matching = [
            item
            for item in plan.get("items", [])
            if item.get("id") == args.checklist_item
        ]
        if not matching:
            raise RunnerError(
                f"Unknown checklist item {args.checklist_item!r} in plan {args.plan_id}"
            )
        if matching[0].get("state") == "done":
            raise RunnerError(f"Checklist item {args.checklist_item} is already done")
        plan_item = matching[0]
        attempt_limit = (plan.get("executor_policy") or {}).get("max_attempts_per_item")
        if (
            isinstance(attempt_limit, int)
            and len(plan_item.get("job_ids", [])) >= attempt_limit
        ):
            raise RunnerError(
                f"Checklist item {args.checklist_item} reached its {attempt_limit}-attempt limit; "
                "ask the user before changing the plan or executor policy"
            )
        if args.reasoning_effort is None and not args.use_terra_fallback:
            approved = [
                entry
                for entry in (plan.get("executor_policy") or {}).get("allowed", [])
                if executor_selection_matches(entry, args.cli, args.model)
            ]
            if approved:
                args.reasoning_effort = approved[0].get("reasoning_effort")
        if args.use_terra_fallback and args.reasoning_effort is None:
            args.reasoning_effort = (
                plan.get("executor_policy", {}).get("fallback") or {}
            ).get("reasoning_effort")
    applied_executor_policy = validate_executor_selection(args, profile, plan)
    fallback_feedback = validate_terra_fallback(
        args, plan, plan_item, workspace, task_stats
    )
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
        raise RunnerError(
            "Workspace has uncommitted changes; use a clean worktree or pass --allow-dirty after recording the baseline"
        )
    selected_job_id = args.job_id or make_job_id(args.cli)
    path = job_dir(selected_job_id, must_exist=False)
    if path.exists():
        raise RunnerError(f"Job already exists: {selected_job_id}")
    if fallback_feedback:
        claim_feedback_for_fallback(fallback_feedback["id"], selected_job_id)
    state_home().mkdir(parents=True, exist_ok=True, mode=0o700)
    jobs_dir().mkdir(exist_ok=True, mode=0o700)
    path.mkdir(mode=0o700)
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
        channel_path.parent,
    )
    redacted_command = [
        "<prompt_text>" if token == task_copy.read_text(encoding="utf-8") else token
        for token in command
    ]
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
        "task_type": (
            (plan.get("goal") or {}).get("task_type") if plan is not None else "general"
        ),
        "cli_agent": args.cli_agent,
        "max_turns": args.max_turns,
        "max_cost_usd": args.max_cost_usd,
        "executor_policy": applied_executor_policy,
        "terra_fallback_feedback_id": (
            fallback_feedback.get("id") if fallback_feedback else None
        ),
        "task_sha256": task_sha256,
        "task_stats": task_stats,
        "context_budget_bytes": route.get("context_budget_bytes") if route else None,
        "review_required": (
            route.get("review", {}).get("required", True) if route else True
        ),
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
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "_worker",
            "--job-dir",
            str(path),
        ],
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
        "executor_approval": selected_executor_approval(
            applied_executor_policy, args.cli, args.model
        ),
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
            channel_dir(path),
        )
        with stdout_path.open("wb") as stdout_handle, stderr_path.open(
            "wb"
        ) as stderr_handle:
            os.chmod(stdout_path, 0o600)
            os.chmod(stderr_path, 0o600)
            stdin_path: Path | None = None
            if transport == "stdin":
                stdin_handle = prompt_file.open("rb")
            elif transport == "jsonl-stdin":
                stdin_path = path / "stdin.jsonl"
                stdin_path.write_text(
                    json.dumps(
                        {
                            "event": "user",
                            "message": {
                                "content": prompt_file.read_text(encoding="utf-8")
                            },
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )
                os.chmod(stdin_path, 0o600)
                stdin_handle = stdin_path.open("rb")
            else:
                stdin_handle = subprocess.DEVNULL
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
                meta.update(
                    {
                        "state": "running",
                        "started_at": started_at,
                        "child_pid": child.pid,
                    }
                )
                write_json(meta_path, meta)
                add_event(path, "agent_started", child_pid=child.pid)
                deadline = time.monotonic() + meta["timeout_seconds"]
                while child.poll() is None and time.monotonic() < deadline:
                    write_json(
                        path / "heartbeat.json",
                        {
                            "at": utc_now(),
                            "worker_pid": os.getpid(),
                            "child_pid": child.pid,
                        },
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
                    add_event(
                        path, "timeout_reached", timeout_seconds=meta["timeout_seconds"]
                    )
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
            stderr_handle.write(
                f"runner error: {type(exc).__name__}: {exc}\n".encode(
                    "utf-8", errors="replace"
                )
            )
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
        notify_user(
            "Agent Orchestrator", f"Job {meta['job_id']} finished: {state}{suffix}"
        )
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
    event = add_event(
        channel_dir(path), "worker_progress", phase=args.phase, message=args.message
    )
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
    question_path = channel_dir(path) / "questions" / f"{question_id}.json"
    question = {
        "id": question_id,
        "state": "pending",
        "question": value,
        "created_at": utc_now(),
    }
    write_json(question_path, question)
    add_event(
        channel_dir(path), "question_asked", question_id=question_id, question=value
    )
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
            print(
                "Question was cancelled before an answer was provided.", file=sys.stderr
            )
            return 2
        time.sleep(min(1, max(0.1, deadline - time.monotonic())))
    with record_lock(question_path):
        current = read_json(question_path)
        if current.get("state") == "answered":
            print(current["answer"])
            return 0
        if current.get("state") == "pending":
            current.update({"state": "expired", "expired_at": utc_now()})
            write_json(question_path, current)
            add_event(channel_dir(path), "question_expired", question_id=question_id)
    print(f"Timed out waiting for answer to {question_id}.", file=sys.stderr)
    return 2


def command_questions(args: argparse.Namespace) -> int:
    path = job_dir(args.job_id)
    questions = [
        question
        for question in read_questions(path)
        if args.all or question.get("state") == "pending"
    ]
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
    print_value(
        answer_worker_question(args.job_id, args.question_id, answer_text(args)),
        args.json,
    )
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
        raise RunnerError(
            "An accepted review requires at least one independently run --test result"
        )
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
        elapsed = (
            (dt.datetime.now(dt.timezone.utc) - created).total_seconds()
            if created
            else None
        )
    except ValueError:
        elapsed = None
    observation = {
        **status,
        "elapsed_seconds": round(elapsed, 3) if elapsed is not None else None,
        "logs": {
            "stdout_bytes": (
                (path / "stdout.log").stat().st_size
                if (path / "stdout.log").exists()
                else 0
            ),
            "stderr_bytes": (
                (path / "stderr.log").stat().st_size
                if (path / "stderr.log").exists()
                else 0
            ),
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
                print(
                    f"[{utc_now()}] state={status['state']} attention_required={status['attention_required']}",
                    flush=True,
                )
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
                    print(
                        f"[{stream}] {data.decode('utf-8', errors='replace')}",
                        end="",
                        flush=True,
                    )
            if status["state"] == "needs_input":
                for question in status["pending_questions"]:
                    print(
                        f"[question {question['id']}] {question['question']}",
                        flush=True,
                    )
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
        question_path = worker_question_path(path, question["id"])
        with record_lock(question_path):
            current = read_json(question_path)
            if current.get("state") == "pending":
                current.update(state="cancelled", cancelled_at=utc_now())
                write_json(question_path, current)
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
                    for key in (
                        "job_id",
                        "cli",
                        "model",
                        "cli_agent",
                        "state",
                        "workspace",
                    )
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
        end = (
            dt.datetime.fromisoformat(end_value)
            if end_value
            else dt.datetime.now(dt.timezone.utc)
        )
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
            changed = status_paths(current.get("status", [])) - status_paths(
                baseline.get("status", [])
            )
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
    headings = (
        "JOB",
        "GROUP",
        "ROLE",
        "CLI / MODEL / EFFORT",
        "EXECUTION",
        "REVIEW",
        "PHASE",
        "FILES",
        "TIME",
    )
    widths = (25, 12, 16, 34, 13, 15, 14, 5, 8)
    print(
        "  ".join(
            shortened(value, width).ljust(width)
            for value, width in zip(headings, widths)
        )
    )
    for row in rows:
        selection = " / ".join(
            str(value)
            for value in (row["cli"], row["model"], row["reasoning_effort"])
            if value
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
        print(
            "  ".join(
                shortened(value, width).ljust(width)
                for value, width in zip(values, widths)
            )
        )


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


def command_dashboard_web(args: argparse.Namespace) -> int:
    from dashboard_server import serve

    return serve(port=args.port, open_browser=not args.no_open)


def command_ensure_dashboard(args: argparse.Namespace) -> int:
    state_home().mkdir(parents=True, exist_ok=True, mode=0o700)
    runtime: dict[str, Any] | None = None
    reused = False
    start_lock = state_home() / "dashboard-start"
    with record_lock(start_lock):
        runtime = live_dashboard_runtime()
        if runtime:
            reused = True
        else:
            log_path = state_home() / "dashboard.log"
            log = log_path.open("ab", buffering=0)
            subprocess.Popen(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "dashboard-web",
                    "--no-open",
                    "--port",
                    str(args.port),
                ],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                start_new_session=True,
                close_fds=True,
            )
            log.close()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                time.sleep(0.1)
                runtime = live_dashboard_runtime()
                if runtime:
                    break
    if not runtime:
        raise RunnerError(
            f"Dashboard did not become ready; inspect {state_home() / 'dashboard.log'}"
        )
    if not args.no_open:
        webbrowser.open(runtime["url"])
    output = {
        **runtime,
        "reused": reused,
        "state_file": str(dashboard_runtime_path()),
    }
    print(json.dumps(output, indent=2, sort_keys=True) if args.json else runtime["url"])
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    profiles = subparsers.add_parser("profiles", help="List available agent adapters")
    profiles.add_argument("--config")
    profiles.set_defaults(func=command_profiles)

    routes = subparsers.add_parser(
        "routes", help="List built-in coordinator/executor routing policies"
    )
    routes.set_defaults(func=command_routes)

    validate_task = subparsers.add_parser(
        "validate-task", help="Validate a detailed worker task contract"
    )
    validate_task.add_argument("--task-file", required=True)
    validate_task.set_defaults(func=command_validate_task)

    catalog = subparsers.add_parser(
        "catalog", help="Discover installed CLIs, models, and internal agents"
    )
    catalog.add_argument("--cli", default="all")
    catalog.add_argument("--workspace", default=os.getcwd())
    catalog.add_argument("--query")
    catalog.add_argument("--full", action="store_true")
    catalog.add_argument("--timeout-seconds", type=float, default=15)
    catalog.add_argument("--no-discovery", action="store_true")
    catalog.add_argument("--config")
    catalog.add_argument("--json", action="store_true")
    catalog.set_defaults(func=command_catalog)

    choices = subparsers.add_parser(
        "choices", help="Show routing flows and installed CLI harnesses"
    )
    choices.add_argument("--include-unavailable", action="store_true")
    choices.add_argument("--config")
    choices.add_argument("--json", action="store_true")
    choices.set_defaults(func=command_choices)

    doctor = subparsers.add_parser("doctor", help="Check agent executables")
    doctor.add_argument("--cli", "--agent", dest="cli", default="all")
    doctor.add_argument("--config")
    doctor.set_defaults(func=command_doctor)

    preferences = subparsers.add_parser(
        "preferences", help="Read or save persistent orchestration preferences"
    )
    preferences.add_argument("--workspace-mode", choices=WORKSPACE_MODES)
    preferences.add_argument("--json", action="store_true")
    preferences.set_defaults(func=command_preferences)

    create_plan = subparsers.add_parser(
        "create-plan", help="Create a durable plan and checklist ledger"
    )
    create_plan.add_argument("--plan-file", required=True)
    create_plan.add_argument("--workspace", default=os.getcwd())
    create_plan.add_argument("--title", required=True)
    create_plan.add_argument("--plan-id")
    create_plan.add_argument(
        "--strategy",
        choices=STRATEGIES,
        default="cost-first",
        help=(
            "Automatic routing intent when no executor pool is supplied. quality-first may use "
            "Sol/Opus; only maximum-quality may automatically use Astra."
        ),
    )
    create_plan.add_argument("--risk", choices=RISKS, default="medium")
    create_plan.add_argument("--task-type", choices=TASK_TYPES, default="general")
    create_plan.add_argument("--config")
    create_plan.add_argument(
        "--executor",
        action="append",
        default=[],
        metavar="CLI=MODEL",
        help="Add a user-approved executor; omit =MODEL only for a knowingly approved CLI default",
    )
    create_plan.add_argument(
        "--expensive-executor",
        action="append",
        default=[],
        metavar="CLI=MODEL",
        help="Add an expensive/frontier executor after explicit user cost approval",
    )
    create_plan.add_argument(
        "--terra-fallback-after-seconds",
        type=int,
        metavar="SECONDS",
        help=(
            "Pre-approve Terra only after the executor pool is exhausted and linked user "
            "feedback remains unanswered for this grace period"
        ),
    )
    create_plan.add_argument("--json", action="store_true")
    create_plan.set_defaults(func=command_create_plan)

    plan_status = subparsers.add_parser(
        "plan-status", help="Show durable plan decisions and checklist state"
    )
    plan_status.add_argument("plan_id")
    plan_status.add_argument("--json", action="store_true")
    plan_status.set_defaults(func=command_plan_status)

    checkpoint = subparsers.add_parser(
        "plan-checkpoint",
        help="Return compact durable state for a coordinator continuation",
    )
    checkpoint.add_argument("plan_id")
    checkpoint.add_argument("--json", action="store_true")
    checkpoint.set_defaults(func=command_plan_checkpoint)

    metrics = subparsers.add_parser(
        "metrics", help="Aggregate recent executor outcomes and provider-reported usage"
    )
    metrics.add_argument("--since-hours", type=float, default=24)
    metrics.add_argument("--workspace")
    metrics.add_argument("--json", action="store_true")
    metrics.set_defaults(func=command_metrics)

    recommend = subparsers.add_parser(
        "recommend-executors",
        help="Rank installed executor choices using public priors and reviewed local outcomes",
    )
    recommend.add_argument("--strategy", choices=STRATEGIES, default="cost-first")
    recommend.add_argument("--risk", choices=RISKS, default="medium")
    recommend.add_argument("--task-type", choices=TASK_TYPES, default="general")
    recommend.add_argument(
        "--candidate", action="append", default=[], metavar="CLI=MODEL"
    )
    recommend.add_argument(
        "--expensive-candidate", action="append", default=[], metavar="CLI=MODEL"
    )
    recommend.add_argument("--config")
    recommend.add_argument("--json", action="store_true")
    recommend.set_defaults(func=command_recommend_executors)

    set_executors = subparsers.add_parser(
        "set-executors", help="Replace a plan's user-approved executor allowlist"
    )
    set_executors.add_argument("plan_id")
    set_executors.add_argument(
        "--executor", action="append", default=[], metavar="CLI=MODEL"
    )
    set_executors.add_argument(
        "--expensive-executor", action="append", default=[], metavar="CLI=MODEL"
    )
    set_executors.add_argument(
        "--terra-fallback-after-seconds", type=int, metavar="SECONDS"
    )
    set_executors.add_argument("--json", action="store_true")
    set_executors.set_defaults(func=command_set_executors)

    executor_options = subparsers.add_parser(
        "executor-options",
        help="Show attempted and untried executors for one plan item",
    )
    executor_options.add_argument("plan_id")
    executor_options.add_argument("--checklist-item", required=True)
    executor_options.add_argument("--json", action="store_true")
    executor_options.set_defaults(func=command_executor_options)

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
        "--approved-executor",
        action="append",
        default=[],
        metavar="CLI=MODEL",
        help="One-off user-approved executor allowlist entry for a non-plan launch",
    )
    launch.add_argument(
        "--approved-expensive-executor",
        action="append",
        default=[],
        metavar="CLI=MODEL",
        help="One-off expensive/frontier entry after explicit user cost approval",
    )
    launch.add_argument(
        "--use-terra-fallback",
        action="store_true",
        help="Use a plan's pre-approved Terra fallback after pool exhaustion and feedback timeout",
    )
    launch.add_argument(
        "--feedback-id",
        help="Pending linked feedback record proving the user had a chance to respond",
    )
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

    review = subparsers.add_parser(
        "record-review", help="Record an independent review verdict and evidence"
    )
    review.add_argument("job_id")
    review.add_argument(
        "--verdict", choices=("accepted", "repair_required", "rejected"), required=True
    )
    review.add_argument("--reviewer", required=True)
    review.add_argument("--test", action="append", default=[])
    review.add_argument("--notes")
    review.add_argument("--notes-file")
    review.add_argument("--json", action="store_true")
    review.set_defaults(func=command_record_review)

    observe = subparsers.add_parser(
        "observe", help="Capture live process, log, event, and diff state"
    )
    observe.add_argument("job_id")
    observe.add_argument("--tail", type=int, default=40)
    observe.add_argument("--event-limit", type=int, default=20)
    observe.add_argument("--json", action="store_true")
    observe.set_defaults(func=command_observe)

    watch = subparsers.add_parser(
        "watch", help="Stream job state and logs until attention or completion"
    )
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

    dashboard = subparsers.add_parser(
        "dashboard", help="Show all worker roles, selections, and live states"
    )
    dashboard.add_argument("--group")
    dashboard.add_argument("--limit", type=int, default=20)
    dashboard.add_argument("--watch", action="store_true")
    dashboard.add_argument("--interval", type=float, default=2)
    dashboard.add_argument("--json", action="store_true")
    dashboard.set_defaults(func=command_dashboard)

    web = subparsers.add_parser(
        "dashboard-web", help="Open the private all-project web dashboard"
    )
    web.add_argument("--port", type=int, default=0)
    web.add_argument("--no-open", action="store_true")
    web.set_defaults(func=command_dashboard_web)

    ensure_web = subparsers.add_parser(
        "ensure-dashboard",
        help="Start or reuse the private dashboard for an orchestration session",
    )
    ensure_web.add_argument("--port", type=int, default=0)
    ensure_web.add_argument("--no-open", action="store_true")
    ensure_web.add_argument("--json", action="store_true")
    ensure_web.set_defaults(func=command_ensure_dashboard)

    request = subparsers.add_parser(
        "request-feedback", help="Request durable project orchestrator feedback"
    )
    request.add_argument("--workspace", default=os.getcwd())
    request.add_argument("--question")
    request.add_argument("--question-file")
    request.add_argument("--context")
    request.add_argument("--plan-id")
    request.add_argument("--checklist-item")
    request.add_argument("--feedback-id")
    request.add_argument("--no-notify", dest="notify", action="store_false")
    request.add_argument("--json", action="store_true")
    request.set_defaults(func=command_request_feedback, notify=True)

    feedback = subparsers.add_parser("feedback", help="List project feedback as JSON")
    feedback.add_argument("--workspace")
    feedback.add_argument("--all", action="store_true")
    feedback.add_argument("--json", action="store_true")
    feedback.set_defaults(func=command_feedback)

    feedback_answer = subparsers.add_parser(
        "answer-feedback", help="Answer pending orchestrator feedback"
    )
    feedback_answer.add_argument("feedback_id")
    feedback_answer.add_argument("--text")
    feedback_answer.add_argument("--file")
    feedback_answer.add_argument("--json", action="store_true")
    feedback_answer.set_defaults(func=command_answer_feedback)

    worker = subparsers.add_parser("_worker", help=argparse.SUPPRESS)
    worker.add_argument("--job-dir", required=True)
    worker.set_defaults(func=command_worker)
    return parser


def validate_cli_args(args: argparse.Namespace) -> None:
    if not 0 <= getattr(args, "port", 0) <= 65535:
        raise RunnerError("port must be between 0 and 65535")
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
    if (
        getattr(args, "max_changed_files", None) is not None
        and args.max_changed_files <= 0
    ):
        raise RunnerError("max-changed-files must be greater than zero")
    if getattr(args, "since_hours", 1) <= 0:
        raise RunnerError("since-hours must be greater than zero")
    for field in ("group", "role", "title", "reviewer"):
        value = getattr(args, field, None)
        if value and (
            len(value) > 80 or any(ord(character) < 32 for character in value)
        ):
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
