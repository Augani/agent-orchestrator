# Changelog

## 0.7.0 - 2026-09-14

- Add a sandboxed Cursor CLI adapter with live model discovery and explicit model selection.
- Bundle a Cursor hook that denies native subagent spawning so one orchestrator job remains one
  observable, accountable agent; continue using Grok's native `--no-subagents` control.
- Add durable `auto`, `single`, and `cross-harness` team topology plus a bounded parallel-worker
  limit to every plan.
- Define cross-harness teams as the product boundary: Agent Orchestrator owns delegation, routing,
  questions, usage, retries, review, and escalation across independent CLI subscriptions.
- Document the Fusion-inspired lead/sidekick pattern while avoiding hidden model switches, swarm
  overhead, and unverified subscription or cost claims.

## 0.6.0 - 2026-09-14

- Group linked worktree jobs under their canonical repository in the dashboard, add a selected
  project's inner agent navigation, and reserve project alerts for questions requiring user input.
- Remember an explicit opt-out from Git worktrees and capture the selected workspace mode in every
  new durable plan.
- Add task-type-aware executor recommendations that combine transparent, versioned public
  benchmark/model priors with minimum-sample reviewed local outcomes while preserving user pools
  and the explicit Astra boundary.
- Add automatic intent routing: `cost-first` by default, `quality-first` for explicit
  quality-over-cost requests, and `maximum-quality` only for explicit frontier authorization.
- Let quality-first plans automatically use installed GPT-5.6 Sol and Claude Opus executors while
  continuing to exclude Astra; only maximum-quality can place Astra in an automatic pool.
- Classify plans by low, medium, or high risk and persist each executor's rank, reasoning effort,
  rationale, and authorization source. Plan-bound launches inherit the recorded effort.
- Store the plan's Goal as a durable goal ledger and add `plan-checkpoint` for compact continuation
  state instead of replaying full coordinator history and raw logs.
- Add recent per-executor outcome and provider-usage aggregation with `metrics --since-hours 24`.
- Document evidence from a long-running trace showing coordinator context replay—not bounded
  executor input—was the dominant token amplifier, and require event-driven monitoring.
- Add GPT-5.6 Sol to the built-in Codex CLI model catalog while retaining custom executor pools,
  review gates, scope controls, dashboard feedback, and the no-silent-Astra fallback rule.

## 0.5.0 - 2026-09-14

- Make executor routing fail closed: built-in routes are recommendations only, model-capable CLIs
  require an explicit model, and every launch must match a durable pool or one-off approval.
- Require separate, visible cost approval before Astra, Fable, Opus, or another marked frontier
  model may execute code; planning and review never imply executor approval.
- Add durable plan executor pools, replacement history, and `executor-options` attempt/exhaustion
  reporting so failed or timed-out workers can only move to another approved entry.
- Add optional pre-approved Terra fallback after pool exhaustion, a linked unanswered user-feedback
  grace period, and a 32 KiB focused context limit. Astra is never an automatic fallback.
- Require escalation questions in both the active Codex chat and the dashboard, with user answers
  taking precedence over timeout behavior.
- Add `ensure-dashboard` to automatically start or reuse one private all-project dashboard at the
  beginning of every orchestration session.
- Add a Google Antigravity CLI profile using the official sandboxed headless JSONL protocol, with
  explicit model, effort, and internal-agent selection plus live model/agent discovery.

## 0.4.0 - 2026-09-13

- Add a dependency-free loopback web dashboard for all projects and jobs, with a continuous agent
  table, inspector tabs, two-second refresh, filters, and preserved selections and answer drafts.
- Add durable project orchestrator feedback and JSON `request-feedback`, `feedback`, and
  `answer-feedback` commands. Answer both worker and orchestrator questions through shared locked,
  audited transitions from the CLI or dashboard.
- Protect the local HTTP interface with a per-server capability token, loopback Host/Origin checks,
  strict static routes, response security headers, and bounded validated JSON mutations.
- Default coordinator identity truthfully to `current-codex-task`; route recommendations never
  replace the current task model or an explicit coordinator override.
- Narrow the Codex executor write grant to each job's communication subtree with `--add-dir`,
  retaining `workspace-write`, authoritative records outside that subtree, and legacy question reads.
- Add an explicit post-acceptance cleanup gate for orchestrator-created temporary resources while
  preserving user-owned files and durable audit history by default.

## 0.3.1 - 2026-09-13

- Point the Codex plugin details page's Website action at the public GitHub repository.

## 0.3.0 - 2026-09-13

- Add user-selectable `quality-first` and `economy-first` coordinator/executor routes plus custom
  model-role overrides.
- Add an installed-harness chooser and compact live Codex model discovery; surface configured Claude
  model aliases and each harness's supported controls.
- Add durable plans, decision records, checklist-bound jobs, and resumable plan status.
- Enforce a 64 KiB context-capsule budget for built-in routes with planning guidance for larger
  work.
- Add reasoning-effort controls and machine-readable output for Codex CLI and Claude Code.
- Capture provider-reported token/cost usage when present.
- Require an independent review verdict and test evidence before acceptance or dependency release.

## 0.2.0 - 2026-09-13

- Publish Agent Orchestrator as an installable Codex plugin marketplace.
- Add built-in DeepSeek Harness, Kimi Code, Codex CLI, and Claude Code profiles.
- Add compatible executable fallback selection for Kimi Code.
- Surface adapter maturity, documentation, and installation guidance.
- Include detached execution, structured task contracts, worker questions, live observability,
  scope controls, notifications, and Codex review/repair guidance.
