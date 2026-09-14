#!/usr/bin/env python3
"""Dependency-free, loopback-only view of durable Agent Orchestrator state."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import subprocess
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import cli_agent_job as jobs

WEB_ROOT = Path(__file__).resolve().parents[1] / "web"
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}
MAX_BODY = 65536
ACTIVE = {"starting", "running", "needs_input", "cancelling"}
FAILURE = {"failed", "lost", "scope_violated", "timed_out"}


def project_root(workspace: str) -> Path:
    """Resolve linked Git worktrees to their shared repository checkout."""
    literal = Path(workspace).resolve()
    if not literal.is_dir():
        return literal
    dot_git = literal / ".git"
    if dot_git.is_dir():
        return literal
    if dot_git.is_file():
        try:
            marker = dot_git.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            marker = ""
        prefix = "gitdir: "
        if marker.startswith(prefix) and "\x00" not in marker:
            candidate = Path(marker[len(prefix) :].strip())
            if not candidate.is_absolute():
                candidate = literal / candidate
            try:
                git_dir = candidate.resolve()
            except OSError:
                git_dir = candidate
            if (
                git_dir.parent.name == "worktrees"
                and git_dir.parent.parent.name == ".git"
            ):
                return git_dir.parent.parent.parent
    try:
        result = subprocess.run(
            ["git", "-C", str(literal), "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return literal
    lines = result.stdout.splitlines()
    if (
        result.returncode != 0
        or len(lines) != 1
        or not lines[0].strip()
        or "\x00" in lines[0]
    ):
        return literal
    common = Path(lines[0].strip())
    if not common.is_absolute():
        common = literal / common
    try:
        common = common.resolve()
    except OSError:
        return literal
    return common.parent if common.is_dir() and common != literal else literal


def project_identity(workspace: str) -> tuple[str, str]:
    root = project_root(workspace)
    pid = "p-" + hashlib.sha256(str(root).encode()).hexdigest()[:24]
    return pid, root.name or "Workspace"


def project_id(workspace: str) -> str:
    return project_identity(workspace)[0]


def activity_key(item: dict) -> tuple:
    return (
        bool(item.get("attention_required")),
        bool(item.get("active")),
        item.get("updated_at") or "",
    )


def feedback_public(record: dict) -> dict:
    public = {
        key: record.get(key)
        for key in (
            "id",
            "state",
            "source",
            "question",
            "context",
            "project_name",
            "plan_id",
            "checklist_item",
            "created_at",
            "answered_at",
            "answer",
            "fallback_job_id",
            "fallback_started_at",
        )
    }
    workspace = record.get("workspace")
    if isinstance(workspace, str) and workspace:
        public["project_name"] = project_identity(workspace)[1]
    return public


class DashboardState:
    """Small observation cache; every mutation still rereads durable state under its record lock."""

    def __init__(self):
        self.lock = threading.RLock()
        self.snapshots: dict = {}

    def changed_files(self, workspace: str, baseline: dict) -> list[str] | None:
        with self.lock:
            canonical = str(Path(workspace).resolve())
            cached = self.snapshots.get(canonical)
            if not cached or time.monotonic() - cached[0] >= 2:
                try:
                    snapshot = jobs.git_snapshot(Path(canonical))
                except (
                    jobs.RunnerError,
                    OSError,
                    TimeoutError,
                    jobs.subprocess.TimeoutExpired,
                ):
                    snapshot = {"is_git": False}
                cached = (time.monotonic(), snapshot)
                self.snapshots[canonical] = cached
            current = cached[1]
        if not current.get("is_git"):
            return None
        return sorted(
            jobs.status_paths(current.get("status", []))
            - jobs.status_paths(baseline.get("status", []))
        )

    def job(self, path: Path) -> tuple[dict, dict]:
        status = jobs.get_status(path)
        workspace = status["workspace"]
        pid, project_name = project_identity(workspace)
        events = jobs.read_events(path, 40)
        progress = [event for event in events if event.get("type") == "worker_progress"]
        latest = events[-1] if events else {}
        files = self.changed_files(workspace, status.get("git_baseline", {}))
        state = status.get("state")
        if state == "succeeded":
            state = status.get("acceptance_state", "awaiting_review")
        questions = [
            {
                **{
                    key: question.get(key)
                    for key in ("id", "state", "question", "created_at")
                },
                "source": "worker",
                "job_id": path.name,
                "project_id": pid,
                "project_name": project_name,
            }
            for question in status.get("pending_questions", [])
        ]
        row = {
            "job_id": path.name,
            "project_id": pid,
            "source": "worker",
            "cli": status.get("cli") or status.get("agent"),
            "model": status.get("model"),
            "cli_agent": status.get("cli_agent"),
            "role": status.get("role"),
            "coordinator_model": status.get("coordinator_model")
            or "current-codex-task",
            "group": status.get("group"),
            "plan_id": status.get("plan_id"),
            "checklist_item": status.get("checklist_item"),
            "state": state,
            "execution_state": status.get("execution_state"),
            "review_state": status.get("review_state"),
            "phase": status.get("current_phase"),
            "elapsed_seconds": jobs.elapsed_seconds(status),
            "created_at": status.get("created_at"),
            "updated_at": latest.get("at")
            or status.get("finished_at")
            or status.get("created_at"),
            "recent_message": progress[-1].get("message") if progress else None,
            "current_work": (
                progress[-1].get("message") if progress else status.get("role")
            ),
            "changed_files": len(files) if files is not None else None,
            "usage": status.get("usage_summary"),
            "pending_questions": questions,
            "active": status.get("execution_state") in ACTIVE,
            "attention_required": bool(questions)
            or state in FAILURE
            or state in {"awaiting_review", "repair_required", "rejected"},
        }
        return row, {
            "status": status,
            "events": events,
            "files": files,
            "project_id": pid,
            "project_name": project_name,
        }

    def overview(self) -> dict:
        projects: dict[str, dict] = {}
        skipped = 0

        def ensure_identity(pid: str, name: str) -> dict:
            return projects.setdefault(
                pid,
                {
                    "id": pid,
                    "name": name,
                    "jobs": [],
                    "pending_feedback": [],
                    "plans": [],
                    "updated_at": "",
                },
            )

        def ensure(workspace: str) -> dict:
            return ensure_identity(*project_identity(workspace))

        for path in sorted(jobs.jobs_dir().glob("*")):
            if not path.is_dir() or not (path / "meta.json").is_file():
                continue
            try:
                jobs.validate_identifier(path.name, "job")
                row, detail = self.job(path)
                ensure_identity(detail["project_id"], detail["project_name"])[
                    "jobs"
                ].append(row)
            except (jobs.RunnerError, OSError, KeyError, ValueError, TypeError):
                skipped += 1
        for record in jobs.read_feedback():
            try:
                project = ensure(record["workspace"])
                project["updated_at"] = max(
                    project["updated_at"],
                    record.get("answered_at") or record["created_at"],
                )
                if record.get("state") == "pending":
                    project["pending_feedback"].append(
                        {**feedback_public(record), "project_id": project["id"]}
                    )
            except (KeyError, ValueError, TypeError):
                skipped += 1
        for path in sorted(jobs.plans_dir().glob("*/plan.json")):
            try:
                plan = jobs.read_json(path)
                project = ensure(plan["workspace"])
                project["plans"].append(
                    {
                        key: plan.get(key)
                        for key in ("plan_id", "title", "counts", "complete")
                    }
                )
                project["updated_at"] = max(
                    project["updated_at"],
                    plan.get("updated_at") or plan.get("created_at") or "",
                )
            except (jobs.RunnerError, KeyError, ValueError, TypeError):
                skipped += 1
        pending = []
        for project in projects.values():
            project["jobs"].sort(key=activity_key, reverse=True)
            rows = project["jobs"]
            project["active"] = sum(bool(row["active"]) for row in rows)
            project["question_count"] = len(project["pending_feedback"]) + sum(
                len(row["pending_questions"]) for row in rows
            )
            # Project-level attention is reserved for questions a user can act on now.
            # Historical failures and unreviewed runs stay visible on their agent rows
            # without turning an old project into a permanent alarm.
            project["attention_required"] = project["question_count"] > 0
            project["attention_count"] = project["question_count"]
            project["review_queue"] = sum(
                row["state"] in {"awaiting_review", "repair_required", "rejected"}
                for row in rows
            )
            project["failed"] = sum(row["state"] in FAILURE for row in rows)
            project["updated_at"] = max(
                [project["updated_at"], *[row["updated_at"] or "" for row in rows]]
            )
            project["completed"] = sum(row["state"] == "accepted" for row in rows)
            pending.extend(project["pending_feedback"])
            for row in rows:
                pending.extend(row["pending_questions"])
        return {
            "projects": sorted(projects.values(), key=activity_key, reverse=True),
            "pending_questions": pending,
            "updated_at": jobs.utc_now(),
            "unreadable_records": skipped,
        }

    def project(self, pid: str) -> dict:
        jobs.validate_identifier(pid, "project")
        project = next(
            (item for item in self.overview()["projects"] if item["id"] == pid), None
        )
        if project is None:
            raise jobs.RunnerError("Unknown project ID")
        project["feedback_history"] = []
        for record in jobs.read_feedback():
            workspace = record.get("workspace")
            if (
                isinstance(workspace, str)
                and workspace
                and project_id(workspace) == pid
                and record.get("state") != "pending"
            ):
                project["feedback_history"].append(feedback_public(record))
        return project

    def verify_job(self, pid: str, job_id: str) -> Path:
        jobs.validate_identifier(pid, "project")
        path = jobs.job_dir(job_id)
        if project_id(jobs.read_json(path / "meta.json")["workspace"]) != pid:
            raise jobs.RunnerError("Unknown job in this project")
        return path

    def detail(self, pid: str, job_id: str) -> dict:
        path = self.verify_job(pid, job_id)
        row, detail = self.job(path)
        events = [
            {
                key: event.get(key)
                for key in (
                    "id",
                    "at",
                    "type",
                    "source",
                    "phase",
                    "message",
                    "question",
                    "state",
                    "verdict",
                )
            }
            for event in detail["events"]
        ]
        return {
            **row,
            "events": events,
            "questions": [
                {
                    key: q.get(key)
                    for key in (
                        "id",
                        "state",
                        "question",
                        "created_at",
                        "answer",
                        "answered_at",
                        "expired_at",
                    )
                }
                for q in jobs.read_questions(path)
            ],
            "files": detail["files"],
            "review": detail["status"].get("review"),
            "logs": {
                "label": "Local / private · last 8 KiB per stream",
                "stdout": local_tail(path / "stdout.log"),
                "stderr": local_tail(path / "stderr.log"),
            },
        }

    def answer(self, parts: list[str], value: str) -> dict:
        if (
            len(parts) == 8
            and parts[:2] == ["api", "projects"]
            and parts[3] == "jobs"
            and parts[5] == "questions"
            and parts[7] == "answer"
        ):
            self.verify_job(parts[2], parts[4])
            return jobs.answer_worker_question(parts[4], parts[6], value)
        if (
            len(parts) == 6
            and parts[:2] == ["api", "projects"]
            and parts[3] == "feedback"
            and parts[5] == "answer"
        ):
            jobs.validate_identifier(parts[2], "project")
            path = jobs.feedback_dir(parts[4])
            if (
                project_id(jobs.read_json(path / "feedback.json")["workspace"])
                != parts[2]
            ):
                raise jobs.RunnerError("Unknown feedback in this project")
            record = jobs.answer_feedback(parts[4], value)
            return feedback_public(record)
        raise jobs.RunnerError("Unknown answer route")


def local_tail(path: Path) -> str:
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            handle.seek(max(0, handle.tell() - 8192))
            return jobs.ANSI_RE.sub(
                "", handle.read(8192).decode("utf-8", errors="replace")
            )
    except OSError:
        return ""


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int = 0):
        self.token = secrets.token_urlsafe(32)
        self.state = DashboardState()
        super().__init__(("127.0.0.1", port), DashboardHandler)
        self.origin = f"http://127.0.0.1:{self.server_port}"
        self.url = f"{self.origin}/#token={self.token}"


class DashboardHandler(BaseHTTPRequestHandler):
    server: DashboardServer
    server_version = "AgentOrchestrator"

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def send_error(self, code, message=None, explain=None):
        self.respond(code, {"error": message or "Unsupported HTTP request"})

    def log_message(self, format, *args):
        pass  # URLs and selected local state never go to access logs.

    def respond(
        self, status: int, body, content_type="application/json; charset=utf-8"
    ):
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
        )
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(data)

    def boundary(self) -> bool:
        hosts = self.headers.get_all("Host", [])
        origins = self.headers.get_all("Origin", [])
        if hosts != [self.server.origin.removeprefix("http://")] or (
            origins and origins != [self.server.origin]
        ):
            self.respond(403, {"error": "Loopback Host / Origin required"})
            return False
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            self.respond(403, {"error": "Cross-site request rejected"})
            return False
        return True

    def authenticated(self) -> bool:
        tokens = self.headers.get_all("X-Orchestrator-Token", [])
        if len(tokens) != 1 or not secrets.compare_digest(
            tokens[0].encode("utf-8"), self.server.token.encode("ascii")
        ):
            self.respond(
                401,
                {
                    "error": "Open the dashboard URL printed by dashboard-web to authenticate"
                },
            )
            return False
        return True

    def do_GET(self):
        if not self.boundary():
            return
        # Exact paths only: no URL decoding, filesystem-derived routes, or wildcard serving.
        if self.path in STATIC:
            name, mime = STATIC[self.path]
            self.respond(200, (WEB_ROOT / name).read_bytes(), mime)
            return
        if not self.path.startswith("/api/"):
            self.respond(404, {"error": "Not found"})
            return
        if not self.authenticated():
            return
        parts = self.path.strip("/").split("/")
        try:
            if self.path == "/api/overview":
                data = self.server.state.overview()
            elif len(parts) == 3 and parts[:2] == ["api", "projects"]:
                data = self.server.state.project(parts[2])
            elif (
                len(parts) == 5
                and parts[:2] == ["api", "projects"]
                and parts[3] == "jobs"
            ):
                data = self.server.state.detail(parts[2], parts[4])
            else:
                self.respond(404, {"error": "Not found"})
                return
            self.respond(200, data)
        except (jobs.RunnerError, KeyError, ValueError, TypeError, OSError):
            self.respond(
                404,
                {
                    "error": "Local record unavailable or ID does not belong to this project"
                },
            )

    def do_POST(self):
        self.close_connection = True
        if not self.boundary() or not self.authenticated():
            return
        if (
            len(self.headers.get_all("Content-Type", [])) != 1
            or self.headers.get_content_type() != "application/json"
        ):
            self.respond(415, {"error": "Content-Type must be application/json"})
            return
        lengths = self.headers.get_all("Content-Length", [])
        if (
            self.headers.get("Transfer-Encoding")
            or len(lengths) != 1
            or not lengths[0].isdigit()
        ):
            self.respond(400, {"error": "A single Content-Length is required"})
            return
        length = int(lengths[0])
        if length > MAX_BODY:
            self.respond(413, {"error": "JSON body exceeds 64 KiB"})
            return
        try:

            def unique_object(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError("Duplicate fields")
                    result[key] = value
                return result

            body = json.loads(
                self.rfile.read(length).decode("utf-8"), object_pairs_hook=unique_object
            )
            if (
                not isinstance(body, dict)
                or set(body) != {"answer"}
                or not isinstance(body["answer"], str)
                or not body["answer"].strip()
            ):
                raise ValueError("Expected only a nonempty answer string")
        except (ValueError, UnicodeError, TimeoutError):
            self.respond(
                400, {"error": "Expected JSON with one nonempty answer string"}
            )
            return
        try:
            result = self.server.state.answer(
                self.path.strip("/").split("/"), body["answer"]
            )
            self.respond(200, result)
        except jobs.RunnerError as exc:
            if "not pending" in str(exc):
                self.respond(
                    409,
                    {
                        "error": "This question is no longer pending. Refresh to see its answer."
                    },
                )
            else:
                self.respond(
                    404,
                    {"error": "Unknown ID or record does not belong to this project"},
                )
        except (OSError, KeyError, ValueError):
            self.respond(
                500,
                {"error": "Could not save the local answer. Refresh before retrying."},
            )


def serve(port=0, open_browser=True) -> int:
    with DashboardServer(port) as server:
        runtime_path = jobs.dashboard_runtime_path()
        runtime = {
            "pid": os.getpid(),
            "origin": server.origin,
            "url": server.url,
            "started_at": jobs.utc_now(),
        }
        jobs.write_json(runtime_path, runtime)
        print(f"Agent Orchestrator dashboard: {server.url}", flush=True)
        if open_browser:
            webbrowser.open(server.url)
        try:
            server.serve_forever(poll_interval=0.25)
        except KeyboardInterrupt:
            return 0
        finally:
            try:
                current = jobs.read_json(runtime_path)
                if current.get("pid") == os.getpid():
                    runtime_path.unlink(missing_ok=True)
            except jobs.RunnerError:
                pass
    return 0
