# Changelog

## 0.3.0 - 2026-09-13

- Add user-selectable `quality-first` and `economy-first` coordinator/executor routes plus custom
  model-role overrides.
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
