from __future__ import annotations

import json
import importlib.util
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


RUNNER = Path(__file__).resolve().parents[1] / "scripts" / "cli_agent_job.py"
RUNNER_SPEC = importlib.util.spec_from_file_location("agent_orchestrator_runner", RUNNER)
assert RUNNER_SPEC and RUNNER_SPEC.loader
runner = importlib.util.module_from_spec(RUNNER_SPEC)
RUNNER_SPEC.loader.exec_module(runner)


class CliAgentJobTests(unittest.TestCase):
    def run_cli(self, *args: str, env: dict[str, str], expected: int = 0) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, str(RUNNER), *args],
            capture_output=True,
            text=True,
            env=env,
            timeout=20,
            check=False,
        )
        self.assertEqual(result.returncode, expected, result.stderr or result.stdout)
        return result

    def write_structured_task(self, path: Path, extra: str = "") -> None:
        path.write_text(
            "\n\n".join(
                f"# {heading}\nSpecific details for {heading}. {extra}"
                for heading in (
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
            ),
            encoding="utf-8",
        )

    def install_fake_codex(self, root: Path, env: dict[str, str]) -> None:
        bin_dir = root / "bin"
        bin_dir.mkdir()
        executable = bin_dir / "codex"
        executable.write_text(
            "#!/usr/bin/env python3\n"
            "import json,sys\n"
            "sys.stdin.read()\n"
            "print(json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 12, 'cached_input_tokens': 4, 'output_tokens': 3}, 'total_cost_usd': 0.42}))\n",
            encoding="utf-8",
        )
        executable.chmod(0o755)
        env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")

    def test_custom_file_adapter_runs_as_detached_job(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            task = root / "task.md"
            task.write_text("implement the bounded change\n", encoding="utf-8")
            config = root / "agents.json"
            config.write_text(
                json.dumps(
                    {
                        "agents": {
                            "fake": {
                                "argv": [
                                    sys.executable,
                                    "-c",
                                    "import pathlib,sys; print(pathlib.Path(sys.argv[1]).read_text())",
                                    "{prompt_file}",
                                ],
                                "prompt_transport": "file",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["AGENT_ORCHESTRATOR_HOME"] = str(root / "state")
            launched = self.run_cli(
                "launch",
                "--cli",
                "fake",
                "--workspace",
                str(workspace),
                "--task-file",
                str(task),
                "--config",
                str(config),
                "--allow-unstructured-task",
                "--no-notify",
                "--json",
                env=env,
            )
            job_id = json.loads(launched.stdout)["job_id"]
            waited = self.run_cli(
                "wait",
                job_id,
                "--timeout-seconds",
                "10",
                "--poll-seconds",
                "0.1",
                "--json",
                env=env,
            )
            self.assertEqual(json.loads(waited.stdout)["state"], "succeeded")
            self.assertEqual(json.loads(waited.stdout)["coordinator_model"], "current-codex-task")
            logs = self.run_cli("logs", job_id, "--stream", "stdout", env=env)
            self.assertIn("implement the bounded change", logs.stdout)

    def test_question_channel_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            task = root / "task.md"
            task.write_text("wait for clarification\n", encoding="utf-8")
            helper = root / "asker.py"
            helper.write_text(
                "import pathlib,subprocess,sys\n"
                "prompt = pathlib.Path(sys.argv[1]).read_text()\n"
                "channel = prompt.split('python3 ', 1)[1].split(' event', 1)[0]\n"
                "result = subprocess.run([sys.executable, channel, 'ask', '--question', 'Which mode?', '--wait-seconds', '10'], capture_output=True, text=True)\n"
                "print(result.stdout.strip())\n"
                "raise SystemExit(result.returncode)\n",
                encoding="utf-8",
            )
            config = root / "agents.json"
            config.write_text(
                json.dumps(
                    {
                        "agents": {
                            "asker": {
                                "argv": [sys.executable, str(helper), "{prompt_file}"],
                                "prompt_transport": "file",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["AGENT_ORCHESTRATOR_HOME"] = str(root / "state")
            launched = self.run_cli(
                "launch",
                "--cli",
                "asker",
                "--workspace",
                str(workspace),
                "--task-file",
                str(task),
                "--config",
                str(config),
                "--allow-unstructured-task",
                "--no-notify",
                "--json",
                env=env,
            )
            job_id = json.loads(launched.stdout)["job_id"]
            question = None
            for _ in range(30):
                pending = self.run_cli("questions", job_id, "--json", env=env)
                values = json.loads(pending.stdout)
                if values:
                    question = values[0]
                    break
                time.sleep(0.1)
            self.assertIsNotNone(question)
            self.run_cli(
                "answer",
                job_id,
                question["id"],
                "--text",
                "Use strict mode.",
                env=env,
            )
            self.run_cli(
                "wait",
                job_id,
                "--timeout-seconds",
                "10",
                "--poll-seconds",
                "0.1",
                "--json",
                env=env,
            )
            logs = self.run_cli("logs", job_id, "--stream", "stdout", env=env)
            self.assertIn("Use strict mode.", logs.stdout)

    def test_project_feedback_cli_lifecycle_permissions_and_isolation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "project"
            workspace.mkdir()
            env = {**os.environ, "AGENT_ORCHESTRATOR_HOME": str(root / "state")}
            result = self.run_cli("request-feedback", "--workspace", str(workspace), "--question", "Which scope?", "--context", "Choose one.", "--feedback-id", "project-choice", "--no-notify", "--json", env=env)
            record = json.loads(result.stdout)
            self.assertEqual(record["source"], "orchestrator")
            self.assertEqual(record["project_name"], "project")
            path = root / "state" / "feedback" / "project-choice"
            self.assertEqual(path.stat().st_mode & 0o777, 0o700)
            self.assertEqual((path / "feedback.json").stat().st_mode & 0o777, 0o600)
            self.assertEqual(len(json.loads(self.run_cli("feedback", "--json", env=env).stdout)), 1)
            self.run_cli("answer-feedback", "project-choice", "--text", "Minimal scope.", "--json", env=env)
            self.run_cli("answer-feedback", "project-choice", "--text", "Again.", env=env, expected=2)
            self.assertEqual(json.loads(self.run_cli("feedback", "--json", env=env).stdout), [])
            history = json.loads(self.run_cli("feedback", "--all", "--json", env=env).stdout)
            self.assertEqual(history[0]["answer"], "Minimal scope.")
            self.assertTrue(history[0]["answered_at"])
            other_env = {**env, "AGENT_ORCHESTRATOR_HOME": str(root / "other-state")}
            self.assertEqual(json.loads(self.run_cli("feedback", "--all", "--json", env=other_env).stdout), [])
            self.run_cli("request-feedback", "--workspace", str(workspace), "--question", "Question", "--feedback-id", "../bad", "--no-notify", env=env, expected=2)
            self.run_cli("request-feedback", "--workspace", str(workspace), "--question", "Question", "--checklist-item", "item-001", "--no-notify", env=env, expected=2)

    def test_unknown_placeholder_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "agents.json"
            config.write_text(
                json.dumps(
                    {
                        "agents": {
                            "bad": {
                                "argv": ["echo", "{secret}"],
                                "prompt_transport": "stdin",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["AGENT_ORCHESTRATOR_HOME"] = str(root / "state")
            result = self.run_cli("profiles", "--config", str(config), env=env, expected=2)
            self.assertIn("unknown placeholders", result.stderr)

    def test_builtin_profiles_include_public_cli_agents_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            env = os.environ.copy()
            env["AGENT_ORCHESTRATOR_HOME"] = str(Path(temp) / "state")
            result = self.run_cli("profiles", env=env)
            profiles = json.loads(result.stdout)
            self.assertTrue(
                {"deepseek-harness", "kimi-code", "codex-cli", "claude-code"}.issubset(profiles)
            )
            self.assertEqual(profiles["deepseek-harness"]["maturity"], "preview")
            self.assertEqual(profiles["kimi-code"]["executable_candidates"], ["kimi", "kimi-cli"])
            self.assertTrue(profiles["codex-cli"]["supports_model"])
            self.assertTrue(profiles["codex-cli"]["supports_reasoning_effort"])
            self.assertTrue(profiles["claude-code"]["supports_cli_agent"])
            routes = json.loads(self.run_cli("routes", env=env).stdout)
            self.assertEqual(routes["quality-first"]["executor"]["model"], "gpt-6-astra")
            for route in routes.values():
                self.assertEqual(route["coordinator"]["model"], "current-codex-task")
                self.assertIn("recommended_task_model", route["coordinator"])
            self.assertEqual(routes["economy-first"]["executor"]["model"], "gpt-5.6-luna")
            choices = json.loads(self.run_cli("choices", "--include-unavailable", "--json", env=env).stdout)
            harness_names = {item["cli"] for item in choices["harnesses"]}
            self.assertTrue({"codex-cli", "claude-code", "kimi-code", "grok"}.issubset(harness_names))
            claude = next(item for item in choices["harnesses"] if item["cli"] == "claude-code")
            self.assertEqual(claude["configured_models"], ["sonnet", "opus", "fable"])

            canonical_choices = json.loads(self.run_cli("choices", "--json", env=env).stdout)
            canonical_names = {item["cli"] for item in canonical_choices["harnesses"]}
            self.assertNotIn("claude", canonical_names)

    def test_structured_model_discovery_is_compacted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            helper = root / "models.py"
            helper.write_text(
                "import json\n"
                "print(json.dumps({'models': [{'slug': 'smart-model', 'display_name': 'Smart', "
                "'description': 'For hard work', 'default_reasoning_level': 'medium', "
                "'supported_reasoning_levels': [{'effort': 'low'}, {'effort': 'high'}], "
                "'private_payload': 'must-not-leak'}]}))\n",
                encoding="utf-8",
            )
            config = root / "agents.json"
            config.write_text(
                json.dumps(
                    {
                        "agents": {
                            "discoverable": {
                                "argv": [sys.executable, "-c", "pass", "{prompt_file}"],
                                "prompt_transport": "file",
                                "discover_models_argv": [sys.executable, str(helper)],
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["AGENT_ORCHESTRATOR_HOME"] = str(root / "state")
            result = self.run_cli(
                "catalog",
                "--cli",
                "discoverable",
                "--workspace",
                str(root),
                "--config",
                str(config),
                env=env,
            )
            models = json.loads(result.stdout)["discoverable"]["models"]
            self.assertEqual(models["choices"][0]["id"], "smart-model")
            self.assertEqual(models["choices"][0]["reasoning_efforts"], ["low", "high"])
            self.assertNotIn("private_payload", json.dumps(models))

    def test_quality_route_plan_usage_and_review_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            task = root / "task.md"
            self.write_structured_task(task)
            plan_file = root / "plan.md"
            plan_file.write_text(
                "# Goal\nShip the bounded feature.\n\n"
                "# Decisions and assumptions\nUse the public runner contract.\n\n"
                "# Constraints and guardrails\nDo not publish or change credentials.\n\n"
                "# Checklist\n- [ ] Implement and verify the feature.\n\n"
                "# Validation strategy\nRun the focused unit tests.\n\n"
                "# Completion criteria\nThe review is accepted with test evidence.\n",
                encoding="utf-8",
            )
            notes = root / "review.md"
            notes.write_text("Diff reviewed; behavior and scope match the task.\n", encoding="utf-8")
            env = os.environ.copy()
            env["AGENT_ORCHESTRATOR_HOME"] = str(root / "state")
            self.install_fake_codex(root, env)
            created_plan = self.run_cli(
                "create-plan",
                "--plan-file",
                str(plan_file),
                "--workspace",
                str(workspace),
                "--title",
                "Quality route test",
                "--plan-id",
                "quality-route-test",
                "--json",
                env=env,
            )
            self.assertEqual(json.loads(created_plan.stdout)["counts"], {"pending": 1})
            launched = self.run_cli(
                "launch",
                "--route",
                "quality-first",
                "--workspace",
                str(workspace),
                "--task-file",
                str(task),
                "--plan-id",
                "quality-route-test",
                "--checklist-item",
                "item-001",
                "--no-notify",
                "--json",
                env=env,
            )
            launch_data = json.loads(launched.stdout)
            self.assertEqual(launch_data["cli"], "codex-cli")
            self.assertEqual(launch_data["model"], "gpt-6-astra")
            self.assertEqual(launch_data["coordinator_model"], "current-codex-task")
            self.assertEqual(launch_data["reasoning_effort"], "high")
            job_id = launch_data["job_id"]
            job_path = (root / "state" / "jobs" / job_id).resolve()
            metadata = json.loads((job_path / "meta.json").read_text())
            command = metadata["command"]
            grant = command[command.index("--add-dir") + 1]
            self.assertEqual(grant, str(job_path / "channel"))
            self.assertNotIn(str(job_path), command)
            self.assertNotIn(str(root / "state"), command)
            self.assertEqual(command[command.index("--sandbox") + 1], "workspace-write")
            self.assertNotIn("danger-full-access", command)
            self.assertTrue((job_path / "channel" / "channel.py").is_file())
            self.assertFalse((job_path / "channel" / "meta.json").exists())
            waited = self.run_cli(
                "wait",
                job_id,
                "--timeout-seconds",
                "10",
                "--poll-seconds",
                "0.1",
                "--json",
                env=env,
            )
            status = json.loads(waited.stdout)
            self.assertEqual(status["review_state"], "required")
            self.assertEqual(status["acceptance_state"], "awaiting_review")
            self.assertEqual(status["usage_summary"]["input_tokens"], 12)
            self.assertEqual(status["usage_summary"]["total_cost_usd"], 0.42)
            self.run_cli(
                "record-review",
                job_id,
                "--verdict",
                "accepted",
                "--reviewer",
                "gpt-6-astra",
                "--test",
                "python3 -m unittest: passed",
                "--notes-file",
                str(notes),
                "--json",
                env=env,
            )
            reviewed = self.run_cli("status", job_id, "--json", env=env)
            self.assertEqual(json.loads(reviewed.stdout)["acceptance_state"], "accepted")
            plan_status = self.run_cli("plan-status", "quality-route-test", "--json", env=env)
            plan = json.loads(plan_status.stdout)
            self.assertTrue(plan["complete"])
            self.assertEqual(plan["items"][0]["state"], "done")

    def test_quality_route_preserves_explicit_selections(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            task = root / "task.md"
            self.write_structured_task(task)
            config = root / "agents.json"
            config.write_text(
                json.dumps(
                    {
                        "agents": {
                            "override-worker": {
                                "argv": [
                                    sys.executable,
                                    "-c",
                                    "import json,pathlib,sys; pathlib.Path(sys.argv[1]).read_text(); print(json.dumps(sys.argv[2:]))",
                                    "{prompt_file}",
                                ],
                                "prompt_transport": "file",
                                "model_args": ["--model", "{model}"],
                                "reasoning_effort_args": ["--effort", "{reasoning_effort}"],
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["AGENT_ORCHESTRATOR_HOME"] = str(root / "state")
            # Keep a regression to the route's default CLI local, too.
            self.install_fake_codex(root, env)
            launched = self.run_cli(
                "launch",
                "--route",
                "quality-first",
                "--cli",
                "override-worker",
                "--model",
                "chosen-executor",
                "--reasoning-effort",
                "low",
                "--coordinator-model",
                "chosen-coordinator",
                "--workspace",
                str(workspace),
                "--task-file",
                str(task),
                "--config",
                str(config),
                "--no-notify",
                "--json",
                env=env,
            )
            launch_data = json.loads(launched.stdout)
            job_id = launch_data["job_id"]
            waited = self.run_cli(
                "wait",
                job_id,
                "--timeout-seconds",
                "10",
                "--poll-seconds",
                "0.1",
                "--json",
                env=env,
            )
            status = json.loads(waited.stdout)
            self.assertEqual(status["state"], "succeeded")
            for source, metadata in (("launch", launch_data), ("status", status)):
                with self.subTest(source=source):
                    self.assertEqual(metadata["route"], "quality-first")
                    self.assertEqual(metadata["coordinator_model"], "chosen-coordinator")
                    self.assertEqual(metadata["cli"], "override-worker")
                    self.assertEqual(metadata["model"], "chosen-executor")
                    self.assertEqual(metadata["reasoning_effort"], "low")
            logs = self.run_cli("logs", job_id, "--stream", "stdout", env=env)
            self.assertEqual(
                json.loads(logs.stdout),
                ["--model", "chosen-executor", "--effort", "low"],
            )

    def test_quality_route_rejects_oversized_context_capsule(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            task = root / "task.md"
            self.write_structured_task(task, "x" * 8_000)
            env = os.environ.copy()
            env["AGENT_ORCHESTRATOR_HOME"] = str(root / "state")
            self.install_fake_codex(root, env)
            result = self.run_cli(
                "launch",
                "--route",
                "quality-first",
                "--workspace",
                str(workspace),
                "--task-file",
                str(task),
                "--no-notify",
                "--json",
                env=env,
                expected=2,
            )
            self.assertIn("Create a durable plan", result.stderr)

    def test_executable_candidate_fallback_is_used(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            task = root / "task.md"
            task.write_text("use the compatible executable\n", encoding="utf-8")
            config = root / "agents.json"
            config.write_text(
                json.dumps(
                    {
                        "agents": {
                            "fallback": {
                                "argv": [
                                    "definitely-not-installed-agent",
                                    "-c",
                                    "import sys; print(sys.stdin.read())",
                                ],
                                "executable_candidates": [
                                    "definitely-not-installed-agent",
                                    sys.executable,
                                ],
                                "prompt_transport": "stdin",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["AGENT_ORCHESTRATOR_HOME"] = str(root / "state")
            launched = self.run_cli(
                "launch",
                "--cli",
                "fallback",
                "--workspace",
                str(workspace),
                "--task-file",
                str(task),
                "--config",
                str(config),
                "--allow-unstructured-task",
                "--no-notify",
                "--json",
                env=env,
            )
            job_id = json.loads(launched.stdout)["job_id"]
            self.run_cli(
                "wait",
                job_id,
                "--timeout-seconds",
                "10",
                "--poll-seconds",
                "0.1",
                "--json",
                env=env,
            )
            logs = self.run_cli("logs", job_id, "--stream", "stdout", env=env)
            self.assertIn("use the compatible executable", logs.stdout)

    def test_cli_model_and_internal_agent_are_independent(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            task = root / "task.md"
            task.write_text("make the change\n", encoding="utf-8")
            config = root / "agents.json"
            config.write_text(
                json.dumps(
                    {
                        "agents": {
                            "selectable": {
                                "argv": [
                                    sys.executable,
                                    "-c",
                                    "import sys; print('ARGS=' + '|'.join(sys.argv[1:]))",
                                    "{prompt_file}",
                                ],
                                "prompt_transport": "file",
                                "model_args": ["--model", "{model}"],
                                "cli_agent_args": ["--persona", "{cli_agent}"],
                                "models": ["cheap-model", "fast-model"],
                                "cli_agents": ["builder", "reviewer"],
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["AGENT_ORCHESTRATOR_HOME"] = str(root / "state")
            catalog = self.run_cli(
                "catalog",
                "--cli",
                "selectable",
                "--config",
                str(config),
                "--no-discovery",
                env=env,
            )
            choices = json.loads(catalog.stdout)["selectable"]
            self.assertEqual(choices["models"]["configured"], ["cheap-model", "fast-model"])
            self.assertEqual(choices["cli_agents"]["configured"], ["builder", "reviewer"])
            launched = self.run_cli(
                "launch",
                "--cli",
                "selectable",
                "--model",
                "cheap-model",
                "--cli-agent",
                "builder",
                "--workspace",
                str(workspace),
                "--task-file",
                str(task),
                "--config",
                str(config),
                "--allow-unstructured-task",
                "--no-notify",
                "--json",
                env=env,
            )
            job_id = json.loads(launched.stdout)["job_id"]
            self.run_cli(
                "wait",
                job_id,
                "--timeout-seconds",
                "10",
                "--poll-seconds",
                "0.1",
                "--json",
                env=env,
            )
            logs = self.run_cli("logs", job_id, "--stream", "stdout", env=env)
            self.assertIn("--model|cheap-model|--persona|builder", logs.stdout)

    def test_task_contract_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            valid = root / "valid.md"
            valid.write_text(
                "\n\n".join(f"# {heading}\nSpecific details for {heading}." for heading in (
                    "Objective",
                    "Why",
                    "Scope",
                    "Files to inspect",
                    "Implementation guidance",
                    "Constraints",
                    "Acceptance criteria",
                    "Validation",
                    "Final report",
                )),
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["AGENT_ORCHESTRATOR_HOME"] = str(root / "state")
            result = self.run_cli("validate-task", "--task-file", str(valid), env=env)
            self.assertTrue(json.loads(result.stdout)["valid"])

    def test_scope_drift_stops_worker(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            subprocess.run(["git", "init", "-q", str(workspace)], check=True)
            subprocess.run(["git", "-C", str(workspace), "config", "user.email", "test@example.com"], check=True)
            subprocess.run(["git", "-C", str(workspace), "config", "user.name", "Test"], check=True)
            (workspace / "seed.txt").write_text("seed\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(workspace), "add", "seed.txt"], check=True)
            subprocess.run(["git", "-C", str(workspace), "commit", "-qm", "seed"], check=True)
            task = root / "task.md"
            task.write_text("stay in scope\n", encoding="utf-8")
            helper = root / "drift.py"
            helper.write_text(
                "from pathlib import Path\nimport time\nPath('outside.txt').write_text('drift')\ntime.sleep(10)\n",
                encoding="utf-8",
            )
            config = root / "agents.json"
            config.write_text(
                json.dumps(
                    {
                        "agents": {
                            "drifter": {
                                "argv": [sys.executable, str(helper), "{prompt_file}"],
                                "prompt_transport": "file",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["AGENT_ORCHESTRATOR_HOME"] = str(root / "state")
            launched = self.run_cli(
                "launch",
                "--cli",
                "drifter",
                "--workspace",
                str(workspace),
                "--task-file",
                str(task),
                "--config",
                str(config),
                "--allow-path",
                "allowed/**",
                "--allow-unstructured-task",
                "--no-notify",
                "--json",
                env=env,
            )
            job_id = json.loads(launched.stdout)["job_id"]
            waited = self.run_cli(
                "wait",
                job_id,
                "--timeout-seconds",
                "10",
                "--poll-seconds",
                "0.1",
                "--json",
                env=env,
                expected=1,
            )
            status = json.loads(waited.stdout)
            self.assertEqual(status["state"], "scope_violated")
            self.assertIn("out-of-scope path changed: outside.txt", status["scope_violations"])

    def test_exact_allowlist_handles_files_in_a_new_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp) / "workspace"
            workspace.mkdir()
            subprocess.run(["git", "init", "-q", str(workspace)], check=True)
            subprocess.run(["git", "-C", str(workspace), "config", "user.email", "test@example.com"], check=True)
            subprocess.run(["git", "-C", str(workspace), "config", "user.name", "Test"], check=True)
            (workspace / "seed.txt").write_text("seed\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(workspace), "add", "seed.txt"], check=True)
            subprocess.run(["git", "-C", str(workspace), "commit", "-qm", "seed"], check=True)
            baseline = runner.git_snapshot(workspace)
            new_dir = workspace / "web"
            new_dir.mkdir()
            (new_dir / "index.html").write_text("dashboard\n", encoding="utf-8")
            self.assertEqual(
                runner.scope_violations(
                    workspace,
                    baseline,
                    {"allowed_paths": ["web/index.html"], "denied_paths": [], "max_changed_files": 1},
                ),
                [],
            )


if __name__ == "__main__":
    unittest.main()
