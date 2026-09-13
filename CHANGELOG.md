# Changelog

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
