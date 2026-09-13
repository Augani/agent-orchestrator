# Contributing

Thank you for improving Agent Orchestrator.

## Before opening a pull request

1. Keep each change bounded. Avoid unrelated cleanup.
2. For a new built-in CLI, use its official non-interactive documentation and choose the least
   permissive mode that can edit a workspace.
3. Never add permission-bypass flags, credentials, private task packets, runtime logs, or local
   machine paths.
4. Prefer stdin or a private prompt file. If a CLI only supports a prompt argument, document the
   process-list exposure and mark the adapter preview until its behavior is well tested.
5. Add tests for adapter selection, failure behavior, and any runner state transition you change.
6. Update the skill and adapter reference when behavior or operator guidance changes.

Run the checks documented in the README. A worker reporting success is not evidence by itself:
inspect the code and rerun tests independently.

## Adapter checklist

- Official executable name and compatible aliases are correct.
- Headless invocation exits when the task finishes.
- Working-directory behavior is explicit.
- Model and internal-agent selectors are separate and only advertised when supported.
- Authentication remains in the provider CLI.
- No shell interpolation is introduced.
- Prompt exposure, maturity, install hint, and documentation URL are accurate.
- Failure returns a non-zero exit code that the runner can record.
