from __future__ import annotations

import json
import importlib.util
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


RUNNER = Path(__file__).resolve().parents[1] / "scripts" / "cli_agent_job.py"
RUNNER_SPEC = importlib.util.spec_from_file_location(
    "agent_orchestrator_runner", RUNNER
)
assert RUNNER_SPEC and RUNNER_SPEC.loader
runner = importlib.util.module_from_spec(RUNNER_SPEC)
RUNNER_SPEC.loader.exec_module(runner)


class CliAgentJobTests(unittest.TestCase):
    def run_cli(
        self, *args: str, env: dict[str, str], expected: int = 0
    ) -> subprocess.CompletedProcess[str]:
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
        bin_dir.mkdir(exist_ok=True)
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

    def write_plan(self, path: Path) -> None:
        path.write_text(
            "# Goal\nShip the bounded feature with review evidence.\n\n"
            "# Decisions and assumptions\nFollow the existing public contract.\n\n"
            "# Constraints and guardrails\nDo not publish or change credentials.\n\n"
            "# Checklist\n- [ ] Implement and verify the feature.\n\n"
            "# Validation strategy\nRun the focused tests.\n\n"
            "# Completion criteria\nThe review is accepted with test evidence.\n",
            encoding="utf-8",
        )

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
                "--approved-executor",
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
            self.assertEqual(
                json.loads(waited.stdout)["coordinator_model"], "current-codex-task"
            )
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
                "--approved-executor",
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
            result = self.run_cli(
                "request-feedback",
                "--workspace",
                str(workspace),
                "--question",
                "Which scope?",
                "--context",
                "Choose one.",
                "--feedback-id",
                "project-choice",
                "--no-notify",
                "--json",
                env=env,
            )
            record = json.loads(result.stdout)
            self.assertEqual(record["source"], "orchestrator")
            self.assertEqual(record["project_name"], "project")
            path = root / "state" / "feedback" / "project-choice"
            self.assertEqual(path.stat().st_mode & 0o777, 0o700)
            self.assertEqual((path / "feedback.json").stat().st_mode & 0o777, 0o600)
            self.assertEqual(
                len(json.loads(self.run_cli("feedback", "--json", env=env).stdout)), 1
            )
            self.run_cli(
                "answer-feedback",
                "project-choice",
                "--text",
                "Minimal scope.",
                "--json",
                env=env,
            )
            self.run_cli(
                "answer-feedback",
                "project-choice",
                "--text",
                "Again.",
                env=env,
                expected=2,
            )
            self.assertEqual(
                json.loads(self.run_cli("feedback", "--json", env=env).stdout), []
            )
            history = json.loads(
                self.run_cli("feedback", "--all", "--json", env=env).stdout
            )
            self.assertEqual(history[0]["answer"], "Minimal scope.")
            self.assertTrue(history[0]["answered_at"])
            other_env = {**env, "AGENT_ORCHESTRATOR_HOME": str(root / "other-state")}
            self.assertEqual(
                json.loads(
                    self.run_cli("feedback", "--all", "--json", env=other_env).stdout
                ),
                [],
            )
            self.run_cli(
                "request-feedback",
                "--workspace",
                str(workspace),
                "--question",
                "Question",
                "--feedback-id",
                "../bad",
                "--no-notify",
                env=env,
                expected=2,
            )
            self.run_cli(
                "request-feedback",
                "--workspace",
                str(workspace),
                "--question",
                "Question",
                "--checklist-item",
                "item-001",
                "--no-notify",
                env=env,
                expected=2,
            )

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
            result = self.run_cli(
                "profiles", "--config", str(config), env=env, expected=2
            )
            self.assertIn("unknown placeholders", result.stderr)

    def test_builtin_profiles_include_public_cli_agents_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            env = os.environ.copy()
            env["AGENT_ORCHESTRATOR_HOME"] = str(Path(temp) / "state")
            result = self.run_cli("profiles", env=env)
            profiles = json.loads(result.stdout)
            self.assertTrue(
                {
                    "antigravity",
                    "deepseek-harness",
                    "kimi-code",
                    "codex-cli",
                    "claude-code",
                }.issubset(profiles)
            )
            self.assertEqual(profiles["antigravity"]["prompt_transport"], "jsonl-stdin")
            self.assertTrue(profiles["antigravity"]["supports_model"])
            self.assertTrue(profiles["antigravity"]["supports_cli_agent"])
            self.assertEqual(profiles["deepseek-harness"]["maturity"], "preview")
            self.assertEqual(
                profiles["kimi-code"]["executable_candidates"], ["kimi", "kimi-cli"]
            )
            self.assertTrue(profiles["codex-cli"]["supports_model"])
            self.assertTrue(profiles["codex-cli"]["supports_reasoning_effort"])
            self.assertTrue(profiles["claude-code"]["supports_cli_agent"])
            routes = json.loads(self.run_cli("routes", env=env).stdout)
            self.assertEqual(
                routes["quality-first"]["executor"]["recommended_model"], "gpt-5.6-sol"
            )
            self.assertEqual(
                routes["quality-first"]["executor"]["automatic_strategy"],
                "quality-first",
            )
            for route in routes.values():
                self.assertEqual(route["coordinator"]["model"], "current-codex-task")
                self.assertIn("recommended_task_model", route["coordinator"])
            self.assertEqual(
                routes["economy-first"]["executor"]["recommended_model"], "gpt-5.6-luna"
            )
            choices = json.loads(
                self.run_cli(
                    "choices", "--include-unavailable", "--json", env=env
                ).stdout
            )
            harness_names = {item["cli"] for item in choices["harnesses"]}
            self.assertTrue(
                {"codex-cli", "claude-code", "kimi-code", "grok"}.issubset(
                    harness_names
                )
            )
            claude = next(
                item for item in choices["harnesses"] if item["cli"] == "claude-code"
            )
            self.assertEqual(claude["configured_models"], ["sonnet", "opus", "fable"])

            canonical_choices = json.loads(
                self.run_cli("choices", "--json", env=env).stdout
            )
            canonical_names = {item["cli"] for item in canonical_choices["harnesses"]}
            self.assertNotIn("claude", canonical_names)

    def test_antigravity_uses_sandboxed_jsonl_stdin_and_pinned_model(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            task = root / "task.md"
            self.write_structured_task(task)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            executable = bin_dir / "agy"
            executable.write_text(
                "#!/usr/bin/env python3\n"
                "import json,sys\n"
                "payload=json.loads(sys.stdin.readline())\n"
                "print(json.dumps({'event':'result','result':{'status':'SUCCESS','payload':payload,'argv':sys.argv[1:]}}))\n",
                encoding="utf-8",
            )
            executable.chmod(0o755)
            env = os.environ.copy()
            env["AGENT_ORCHESTRATOR_HOME"] = str(root / "state")
            env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
            launched = self.run_cli(
                "launch",
                "--cli",
                "antigravity",
                "--model",
                "gemini-flash-test",
                "--cli-agent",
                "builder",
                "--reasoning-effort",
                "low",
                "--approved-executor",
                "antigravity=gemini-flash-test",
                "--workspace",
                str(workspace),
                "--task-file",
                str(task),
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
            output = json.loads(
                self.run_cli("logs", job_id, "--stream", "stdout", env=env).stdout
            )
            self.assertEqual(output["event"], "result")
            self.assertEqual(output["result"]["payload"]["event"], "user")
            self.assertIn(
                "# Assigned task", output["result"]["payload"]["message"]["content"]
            )
            argv = output["result"]["argv"]
            self.assertIn("--print", argv)
            self.assertIn("--sandbox", argv)
            self.assertEqual(argv[argv.index("--model") + 1], "gemini-flash-test")
            self.assertNotIn("--dangerously-skip-permissions", argv)

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
            notes.write_text(
                "Diff reviewed; behavior and scope match the task.\n", encoding="utf-8"
            )
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
                "--expensive-executor",
                "codex-cli=gpt-6-astra",
                "--json",
                env=env,
            )
            self.assertEqual(json.loads(created_plan.stdout)["counts"], {"pending": 1})
            launched = self.run_cli(
                "launch",
                "--route",
                "quality-first",
                "--cli",
                "codex-cli",
                "--model",
                "gpt-6-astra",
                "--reasoning-effort",
                "high",
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
            self.assertEqual(
                json.loads(reviewed.stdout)["acceptance_state"], "accepted"
            )
            plan_status = self.run_cli(
                "plan-status", "quality-route-test", "--json", env=env
            )
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
                                "reasoning_effort_args": [
                                    "--effort",
                                    "{reasoning_effort}",
                                ],
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
                "--approved-executor",
                "override-worker=chosen-executor",
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
                    self.assertEqual(
                        metadata["coordinator_model"], "chosen-coordinator"
                    )
                    self.assertEqual(metadata["cli"], "override-worker")
                    self.assertEqual(metadata["model"], "chosen-executor")
                    self.assertEqual(metadata["reasoning_effort"], "low")
            logs = self.run_cli("logs", job_id, "--stream", "stdout", env=env)
            self.assertEqual(
                json.loads(logs.stdout),
                ["--model", "chosen-executor", "--effort", "low"],
            )

    def test_executor_pool_exhaustion_and_preapproved_terra_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            task = root / "task.md"
            self.write_structured_task(task)
            plan_file = root / "plan.md"
            plan_file.write_text(
                "# Goal\nShip the bounded feature.\n\n"
                "# Decisions and assumptions\nUse the approved executor pool.\n\n"
                "# Constraints and guardrails\nNever fall back to Astra.\n\n"
                "# Checklist\n- [ ] Implement the feature.\n\n"
                "# Validation strategy\nRun focused tests.\n\n"
                "# Completion criteria\nRecord reviewed evidence.\n",
                encoding="utf-8",
            )
            config = root / "agents.json"
            config.write_text(
                json.dumps(
                    {
                        "agents": {
                            "cheap": {
                                "argv": [sys.executable, "-c", "raise SystemExit(1)"],
                                "prompt_transport": "stdin",
                                "model_args": ["--model", "{model}"],
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["AGENT_ORCHESTRATOR_HOME"] = str(root / "state")
            self.install_fake_codex(root, env)
            self.run_cli(
                "create-plan",
                "--plan-file",
                str(plan_file),
                "--workspace",
                str(workspace),
                "--title",
                "Executor pool test",
                "--plan-id",
                "pool-test",
                "--executor",
                "cheap=cheap-a",
                "--executor",
                "cheap=cheap-b",
                "--terra-fallback-after-seconds",
                "60",
                "--json",
                env=env,
            )

            unapproved = self.run_cli(
                "launch",
                "--cli",
                "cheap",
                "--model",
                "not-approved",
                "--workspace",
                str(workspace),
                "--task-file",
                str(task),
                "--config",
                str(config),
                "--plan-id",
                "pool-test",
                "--checklist-item",
                "item-001",
                "--no-notify",
                env=env,
                expected=2,
            )
            self.assertIn("is not user-approved", unapproved.stderr)

            attempted_job_ids = []
            for model in ("cheap-a", "cheap-b"):
                launched = self.run_cli(
                    "launch",
                    "--cli",
                    "cheap",
                    "--model",
                    model,
                    "--workspace",
                    str(workspace),
                    "--task-file",
                    str(task),
                    "--config",
                    str(config),
                    "--plan-id",
                    "pool-test",
                    "--checklist-item",
                    "item-001",
                    "--no-notify",
                    "--json",
                    env=env,
                )
                job_id = json.loads(launched.stdout)["job_id"]
                attempted_job_ids.append(job_id)
                self.run_cli(
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

            options = self.run_cli(
                "executor-options",
                "pool-test",
                "--checklist-item",
                "item-001",
                "--json",
                env=env,
            )
            self.assertTrue(json.loads(options.stdout)["exhausted"])

            first_result_path = (
                root / "state" / "jobs" / attempted_job_ids[0] / "result.json"
            )
            first_result = json.loads(first_result_path.read_text(encoding="utf-8"))
            first_result_path.write_text(
                json.dumps({**first_result, "state": "succeeded", "exit_code": 0}),
                encoding="utf-8",
            )
            awaiting_review = self.run_cli(
                "executor-options",
                "pool-test",
                "--checklist-item",
                "item-001",
                "--json",
                env=env,
            )
            awaiting_review_data = json.loads(awaiting_review.stdout)
            self.assertFalse(awaiting_review_data["exhausted"])
            self.assertEqual(
                awaiting_review_data["unresolved_attempts"][0]["acceptance_state"],
                "awaiting_review",
            )
            first_result_path.write_text(json.dumps(first_result), encoding="utf-8")

            question = root / "question.md"
            question.write_text(
                "The approved executor pool failed. Do you want to change the pool before the "
                "pre-approved Terra fallback starts?\n",
                encoding="utf-8",
            )
            answered_feedback = self.run_cli(
                "request-feedback",
                "--workspace",
                str(workspace),
                "--question-file",
                str(question),
                "--plan-id",
                "pool-test",
                "--checklist-item",
                "item-001",
                "--feedback-id",
                "answered-feedback",
                "--no-notify",
                env=env,
            )
            self.assertEqual(json.loads(answered_feedback.stdout)["state"], "pending")
            self.run_cli(
                "answer-feedback",
                "answered-feedback",
                "--text",
                "Do not use Terra; wait for me.",
                "--json",
                env=env,
            )
            answered_fallback = self.run_cli(
                "launch",
                "--cli",
                "codex-cli",
                "--model",
                "gpt-5.6-terra",
                "--use-terra-fallback",
                "--feedback-id",
                "answered-feedback",
                "--workspace",
                str(workspace),
                "--task-file",
                str(task),
                "--plan-id",
                "pool-test",
                "--checklist-item",
                "item-001",
                "--no-notify",
                env=env,
                expected=2,
            )
            self.assertIn("follow the user's answer", answered_fallback.stderr)

            feedback = self.run_cli(
                "request-feedback",
                "--workspace",
                str(workspace),
                "--question-file",
                str(question),
                "--plan-id",
                "pool-test",
                "--checklist-item",
                "item-001",
                "--feedback-id",
                "pool-feedback",
                "--no-notify",
                env=env,
            )
            feedback_record = json.loads(feedback.stdout)
            feedback_record["created_at"] = (
                runner.dt.datetime.now(runner.dt.timezone.utc)
                - runner.dt.timedelta(seconds=61)
            ).isoformat(timespec="seconds")
            (
                root / "state" / "feedback" / "pool-feedback" / "feedback.json"
            ).write_text(json.dumps(feedback_record), encoding="utf-8")

            terra = self.run_cli(
                "launch",
                "--cli",
                "codex-cli",
                "--model",
                "gpt-5.6-terra",
                "--use-terra-fallback",
                "--feedback-id",
                "pool-feedback",
                "--workspace",
                str(workspace),
                "--task-file",
                str(task),
                "--plan-id",
                "pool-test",
                "--checklist-item",
                "item-001",
                "--no-notify",
                "--json",
                env=env,
            )
            terra_data = json.loads(terra.stdout)
            self.assertTrue(terra_data["executor_approval"]["terra_fallback"])
            self.assertEqual(terra_data["reasoning_effort"], "high")
            limited = self.run_cli(
                "launch",
                "--cli",
                "cheap",
                "--model",
                "cheap-a",
                "--workspace",
                str(workspace),
                "--task-file",
                str(task),
                "--config",
                str(config),
                "--plan-id",
                "pool-test",
                "--checklist-item",
                "item-001",
                "--no-notify",
                env=env,
                expected=2,
            )
            self.assertIn("3-attempt limit", limited.stderr)
            record = json.loads(
                (
                    root / "state" / "feedback" / "pool-feedback" / "feedback.json"
                ).read_text()
            )
            self.assertEqual(record["state"], "terra_fallback_started")

    def test_astra_requires_separate_expensive_executor_approval(self) -> None:
        with self.assertRaises(runner.RunnerError):
            runner.executor_policy(["codex-cli=gpt-6-astra"], [])
        policy = runner.executor_policy([], ["codex-cli=gpt-6-astra"])
        self.assertTrue(policy["allowed"][0]["expensive_user_approved"])
        self.assertFalse(policy["automatic_frontier_fallback"])

    def test_automatic_strategy_pools_preserve_quality_and_astra_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            plan_file = root / "plan.md"
            self.write_plan(plan_file)
            env = os.environ.copy()
            env["AGENT_ORCHESTRATOR_HOME"] = str(root / "state")
            self.install_fake_codex(root, env)

            quality = self.run_cli(
                "create-plan",
                "--plan-file",
                str(plan_file),
                "--workspace",
                str(workspace),
                "--title",
                "Quality intent",
                "--plan-id",
                "quality-intent",
                "--strategy",
                "quality-first",
                "--risk",
                "high",
                "--json",
                env=env,
            )
            quality_plan = json.loads(quality.stdout)
            quality_models = [
                entry["model"] for entry in quality_plan["executor_policy"]["allowed"]
            ]
            self.assertEqual(quality_models[0], "gpt-5.6-sol")
            self.assertEqual(quality_models[-1], "gpt-5.6-terra")
            self.assertTrue(
                set(quality_models).issubset({"gpt-5.6-sol", "opus", "gpt-5.6-terra"})
            )
            self.assertNotIn("gpt-6-astra", quality_models)
            self.assertEqual(
                quality_plan["executor_policy"]["allowed"][0]["reasoning_effort"],
                "xhigh",
            )
            self.assertEqual(
                quality_plan["goal"]["objective"],
                "Ship the bounded feature with review evidence.",
            )

            maximum = self.run_cli(
                "create-plan",
                "--plan-file",
                str(plan_file),
                "--workspace",
                str(workspace),
                "--title",
                "Maximum intent",
                "--plan-id",
                "maximum-intent",
                "--strategy",
                "maximum-quality",
                "--json",
                env=env,
            )
            maximum_plan = json.loads(maximum.stdout)
            self.assertEqual(
                maximum_plan["executor_policy"]["allowed"][0]["model"], "gpt-6-astra"
            )
            self.assertTrue(
                maximum_plan["executor_policy"]["allowed"][0]["expensive_user_approved"]
            )

    def test_default_cost_plan_inherits_effort_and_exposes_compact_checkpoint_metrics(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            workspace.mkdir()
            task = root / "task.md"
            self.write_structured_task(task)
            plan_file = root / "plan.md"
            self.write_plan(plan_file)
            env = os.environ.copy()
            env["AGENT_ORCHESTRATOR_HOME"] = str(root / "state")
            self.install_fake_codex(root, env)
            created = self.run_cli(
                "create-plan",
                "--plan-file",
                str(plan_file),
                "--workspace",
                str(workspace),
                "--title",
                "Cost intent",
                "--plan-id",
                "cost-intent",
                "--risk",
                "low",
                "--json",
                env=env,
            )
            policy = json.loads(created.stdout)["executor_policy"]
            self.assertEqual(policy["strategy"], "cost-first")
            self.assertEqual(
                [entry["model"] for entry in policy["allowed"]],
                ["gpt-5.6-terra", "gpt-5.6-luna"],
            )

            launched = self.run_cli(
                "launch",
                "--cli",
                "codex-cli",
                "--model",
                "gpt-5.6-terra",
                "--workspace",
                str(workspace),
                "--task-file",
                str(task),
                "--plan-id",
                "cost-intent",
                "--checklist-item",
                "item-001",
                "--no-notify",
                "--json",
                env=env,
            )
            launch_data = json.loads(launched.stdout)
            self.assertEqual(launch_data["reasoning_effort"], "medium")
            self.run_cli(
                "wait",
                launch_data["job_id"],
                "--timeout-seconds",
                "10",
                "--poll-seconds",
                "0.1",
                "--json",
                env=env,
            )
            checkpoint = json.loads(
                self.run_cli("plan-checkpoint", "cost-intent", "--json", env=env).stdout
            )
            self.assertEqual(checkpoint["goal"]["strategy"], "cost-first")
            self.assertEqual(
                checkpoint["active_or_unreviewed_jobs"][0]["acceptance_state"],
                "awaiting_review",
            )
            self.assertNotIn("task_stats", json.dumps(checkpoint))
            metrics = json.loads(
                self.run_cli("metrics", "--since-hours", "1", "--json", env=env).stdout
            )
            self.assertEqual(metrics["jobs"], 1)
            self.assertEqual(metrics["by_executor"][0]["usage"]["input_tokens"], 12)

    def test_ensure_dashboard_reuses_one_detached_server(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            env = os.environ.copy()
            env["AGENT_ORCHESTRATOR_HOME"] = str(Path(temp) / "state")
            first = self.run_cli("ensure-dashboard", "--no-open", "--json", env=env)
            first_data = json.loads(first.stdout)
            try:
                second = self.run_cli(
                    "ensure-dashboard", "--no-open", "--json", env=env
                )
                second_data = json.loads(second.stdout)
                self.assertEqual(first_data["pid"], second_data["pid"])
                self.assertEqual(first_data["url"], second_data["url"])
                self.assertTrue(second_data["reused"])
            finally:
                os.kill(first_data["pid"], signal.SIGTERM)

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
                "--cli",
                "codex-cli",
                "--model",
                "gpt-6-astra",
                "--approved-expensive-executor",
                "codex-cli=gpt-6-astra",
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
                "--approved-executor",
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
            self.assertEqual(
                choices["models"]["configured"], ["cheap-model", "fast-model"]
            )
            self.assertEqual(
                choices["cli_agents"]["configured"], ["builder", "reviewer"]
            )
            launched = self.run_cli(
                "launch",
                "--cli",
                "selectable",
                "--model",
                "cheap-model",
                "--approved-executor",
                "selectable=cheap-model",
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
                "\n\n".join(
                    f"# {heading}\nSpecific details for {heading}."
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
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(workspace),
                    "config",
                    "user.email",
                    "test@example.com",
                ],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(workspace), "config", "user.name", "Test"], check=True
            )
            (workspace / "seed.txt").write_text("seed\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(workspace), "add", "seed.txt"], check=True)
            subprocess.run(
                ["git", "-C", str(workspace), "commit", "-qm", "seed"], check=True
            )
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
                "--approved-executor",
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
            self.assertIn(
                "out-of-scope path changed: outside.txt", status["scope_violations"]
            )

    def test_exact_allowlist_handles_files_in_a_new_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp) / "workspace"
            workspace.mkdir()
            subprocess.run(["git", "init", "-q", str(workspace)], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(workspace),
                    "config",
                    "user.email",
                    "test@example.com",
                ],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(workspace), "config", "user.name", "Test"], check=True
            )
            (workspace / "seed.txt").write_text("seed\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(workspace), "add", "seed.txt"], check=True)
            subprocess.run(
                ["git", "-C", str(workspace), "commit", "-qm", "seed"], check=True
            )
            baseline = runner.git_snapshot(workspace)
            new_dir = workspace / "web"
            new_dir.mkdir()
            (new_dir / "index.html").write_text("dashboard\n", encoding="utf-8")
            self.assertEqual(
                runner.scope_violations(
                    workspace,
                    baseline,
                    {
                        "allowed_paths": ["web/index.html"],
                        "denied_paths": [],
                        "max_changed_files": 1,
                    },
                ),
                [],
            )


if __name__ == "__main__":
    unittest.main()
